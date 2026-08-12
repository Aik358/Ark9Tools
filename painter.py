# -*- coding: utf-8 -*-
"""绘画控制器：在游戏中按色板-涂色顺序涂完 24×24 矩阵

算法：
1. 提取所有非白色格：[(x, y, color_idx), ...]
2. 按颜色分组，按使用频次从高到低排序（最少切换色板次数）
3. 维护当前激活色 color_idx，每格：
   - 若 color_idx 不同：点色板中对应格子
   - 点画布中 (x,y) 格子
4. 可选校验：涂色后立即截图该格，确认颜色已变更
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Callable
import time
import ctypes
import ctypes.wintypes as wt

import numpy as np

from win32_input import WindowInput, find_window, WHEEL_DELTA
from win32_capture import ScreenCapturer
from hdrcapture_adapter import AutoSdrWindowCapture
from calibration import Calibration, detect_palette_from_screenshot, sample_color_at
from palette import (GAME_PALETTE, GAME_PALETTE_DATA, PALETTE_COLS,
                     PALETTE_ROWS, color_dist)


user32 = ctypes.WinDLL("user32", use_last_error=True)
ClientToScreen = user32.ClientToScreen

_GetAsyncKeyState = user32.GetAsyncKeyState
_GetAsyncKeyState.argtypes = [ctypes.c_int]
_GetAsyncKeyState.restype = ctypes.c_short
VK_ESCAPE = 0x1B


def _esc_pressed() -> bool:
    """系统级查询 ESC 是否被按下（不依赖窗口焦点，绘画期间随时可急停）"""
    return bool(_GetAsyncKeyState(VK_ESCAPE) & 0x8000)


def _client_origin_screen(hwnd: int) -> Tuple[int, int]:
    """获取窗口客户区原点的屏幕坐标（用于屏幕坐标→客户区坐标换算）"""
    pt = wt.POINT(0, 0)
    if ClientToScreen(hwnd, ctypes.byref(pt)):
        return pt.x, pt.y
    return 0, 0


@dataclass(frozen=True)
class PaletteSlot:
    """一次预扫描确认的游戏物理色板槽位。"""
    rgb: Tuple[int, int, int]
    row: int
    col: int


class PaintingProgress:
    def __init__(self):
        self.total = 0
        self.done = 0
        self.failed: List[Tuple[int, int, int]] = []   # (x, y, color_idx)
        self.cur_color_idx: Optional[int] = None
        self.completed: List[Tuple[int, int, int]] = []
        self.cur_page: int = 0
        self.cur_scroll_top_row: int = 0   # 色板当前滚动位置（可见第一行行号）
        self.start_time = 0.0
        self.status_msg: str = ""          # 停止/异常原因（供 UI 展示）


class Painter:
    def __init__(self, hwnd: int, cal: Calibration,
                 window_region: Tuple[int, int, int, int],
                 progress_cb: Optional[Callable[[PaintingProgress], None]] = None,
                 verify: bool = False,
                 input_mode: str = "sendinput",
                 stroke_mode: bool = False):
        """input_mode: "sendinput"(默认,Unity) | "windowpos" | "plain" """
        self.hwnd = hwnd
        self.input = WindowInput(hwnd)
        self.cal = cal
        self.window_region = window_region
        # 画布验证继续使用稳定 DXGI；网格/色板识别使用独立的标准 SDR 源。
        # SDR 源固定输出 8-bit BGR，HDR/非 HDR 不再走不同颜色逻辑。
        self.capturer = ScreenCapturer(window_region, prefer_winrt=False)
        try:
            self.sdr_capturer = AutoSdrWindowCapture(hwnd)
        except Exception as exc:
            self.sdr_capturer = None
            self._sdr_capture_error = str(exc)
        self.progress = PaintingProgress()
        self.progress_cb = progress_cb
        self.verify_enabled = verify
        self.input_mode = input_mode
        self.stroke_mode = stroke_mode
        self._stop = False
        # 绘画前校色表：物理色板索引 -> 已确认的物理色板槽位。
        self._palette_targets: Dict[int, PaletteSlot] = {}
        self._client_origin = _client_origin_screen(hwnd)

    def stop(self):
        self._stop = True

    def _close_capturers(self):
        """统一释放 DXGI 与 SDR/WGC 捕获资源。"""
        for capturer in (getattr(self, "capturer", None), getattr(self, "sdr_capturer", None)):
            if capturer is not None:
                try:
                    capturer.close()
                except Exception:
                    pass
        self.sdr_capturer = None

    def _ensure_foreground(self, timeout: float = 5.0) -> bool:
        """确保游戏窗口保持前台（窗口化下 SendInput 只发给前台窗口）。

        失焦时每 0.1s 轮询等待恢复；超时或收到急停则返回 False。
        绘画期间工具窗口已最小化让位，正常情况下游戏会一直保持前台。
        """
        if self.input.is_foreground():
            return True
        deadline = time.time() + timeout
        while not self.input.is_foreground():
            if self._stop or _esc_pressed():
                return False
            if time.time() > deadline:
                return False
            time.sleep(0.1)
        return True

    def _emit(self):
        if self.progress_cb:
            self.progress_cb(self.progress)

    def _screen_to_client(self, sx: int, sy: int) -> Tuple[int, int]:
        """屏幕坐标 → 窗口客户区坐标（sendmsg 使用客户区坐标）"""
        cx0, cy0 = self._client_origin
        return sx - cx0, sy - cy0

    def _click_screen(self, sx: int, sy: int):
        """点击客户区坐标 (sx, sy)。

        校准坐标基于客户区（相对窗口客户区左上角）。
        - sendinput: click_sendinput 内部 ClientToScreen 转屏幕坐标（Unity 硬件输入）
        - windowpos: 移动窗口使目标对准光标后 SendMessage
        - plain: 直接 SendMessage（客户区坐标）
        """
        if self.input_mode == "sendinput":
            ok = self.input.click_sendinput(sx, sy)
        elif self.input_mode == "windowpos":
            ok = self.input.click_with_windowpos(sx, sy)
        else:
            ok = self.input.send_click(sx, sy)
        if not ok:
            raise RuntimeError("鼠标未能移动到目标坐标，已阻止点击")

    def _desktop_palette_frame(self) -> np.ndarray:
        """读取统一 SDR 8-bit RGB 的当前游戏帧用于网格/色板识别。"""
        if self.sdr_capturer is None:
            raise RuntimeError(getattr(self, "_sdr_capture_error", "SDR 捕获源不可用"))
        bgr = self.sdr_capturer.grab()
        if bgr is None:
            raise RuntimeError("SDR 窗口捕获未返回有效帧")
        return np.ascontiguousarray(bgr[:, :, ::-1])

    def _refresh_palette_geometry_safe(self) -> bool:
        """尝试用当前帧增强几何校准，失败时保留 remap 后的保存校准。

        HDR、低饱和色块、遮挡或部分滚动视图都可能让轮廓检测不完整；
        这类失败不能阻断绘画，也不能覆盖已经通过 ref_w/ref_h remap 的旧校准。
        """
        try:
            detected = detect_palette_from_screenshot(self._desktop_palette_frame())
        except Exception:
            return False
        if detected is None or detected.palette_visible_rows < 4:
            return False
        # 新检测结果必须与保存校准在同一比例尺度附近；明显跳变视为 HDR/误检。
        checks = (
            abs(detected.palette_left - self.cal.palette_left) <= max(32, self.cal.palette_col_gap * 0.6),
            abs(detected.palette_grid_top - self.cal.palette_grid_top) <= max(36, self.cal.palette_row_gap * 0.6),
            abs(detected.palette_col_gap - self.cal.palette_col_gap) <= max(18, self.cal.palette_col_gap * 0.25),
            abs(detected.palette_row_gap - self.cal.palette_row_gap) <= max(18, self.cal.palette_row_gap * 0.25),
        )
        if not all(checks):
            return False
        self.cal.palette_left = detected.palette_left
        self.cal.palette_top = detected.palette_top
        self.cal.palette_grid_top = detected.palette_grid_top
        self.cal.palette_col_gap = detected.palette_col_gap
        self.cal.palette_row_gap = detected.palette_row_gap
        self.cal.palette_visible_rows = min(6, max(4, detected.palette_visible_rows))
        return True

    def _read_visible_palette(self) -> List[Tuple[Tuple[int, int, int], Tuple[int, int]]]:
        """返回当前视图的全部物理色块：(实测 RGB, 客户区中心坐标)。"""
        img = self._desktop_palette_frame()
        h, w = img.shape[:2]
        cells: List[Tuple[Tuple[int, int, int], Tuple[int, int]]] = []
        for visual_row in range(self.cal.palette_visible_rows):
            y = self.cal.palette_grid_top + visual_row * self.cal.palette_row_gap
            if y < 6 or y >= h - 6:
                continue
            for col in range(PALETTE_COLS):
                x = self.cal.palette_left + col * self.cal.palette_col_gap
                if x < 6 or x >= w - 6:
                    continue
                patch = img[y - 5:y + 6, x - 5:x + 6]
                mean_rgb = patch.reshape(-1, 3).mean(axis=0)
                rgb = (int(mean_rgb[0]), int(mean_rgb[1]), int(mean_rgb[2]))
                cells.append((rgb, (x, y)))
        if not cells:
            raise RuntimeError("未读取到可见色板格")
        return cells

    def _visible_slot(self, slot: PaletteSlot) -> Optional[Tuple[int, int]]:
        """目标物理槽位在当前视图可见时，返回它的客户区中心坐标。"""
        visual_row = slot.row - self.progress.cur_scroll_top_row
        if not 0 <= visual_row < self.cal.palette_visible_rows:
            return None
        return (self.cal.palette_left + slot.col * self.cal.palette_col_gap,
                self.cal.palette_grid_top + visual_row * self.cal.palette_row_gap)

    def _slot_matches(self, slot: PaletteSlot, pos: Tuple[int, int]) -> bool:
        """确认缓存槽位当前仍是预扫描时的同一个颜色。"""
        img = self._desktop_palette_frame()
        x, y = pos
        if y < 6 or y >= img.shape[0] - 6 or x < 6 or x >= img.shape[1] - 6:
            return False
        patch = img[y - 5:y + 6, x - 5:x + 6]
        mean_rgb = patch.reshape(-1, 3).mean(axis=0)
        rgb = (int(mean_rgb[0]), int(mean_rgb[1]), int(mean_rgb[2]))
        return color_dist(rgb[0], rgb[1], rgb[2], slot.rgb[0], slot.rgb[1], slot.rgb[2]) <= 4000

    def _drag_palette(self, toward_later_rows: bool) -> bool:
        """按住色板内容区连续拖动一个视窗高度。"""
        x = self.cal.palette_left + self.cal.palette_col_gap * 2
        y1 = self.cal.palette_grid_top + self.cal.palette_row_gap * 4
        distance = self.cal.palette_row_gap * 4
        y2 = y1 - distance if toward_later_rows else y1 + distance
        before = self._desktop_palette_frame()
        if self.input_mode == "windowpos":
            ok = self.input.drag_with_windowpos(x, y1, x, y2, steps=24)
        elif self.input_mode == "sendinput":
            ok = self.input.drag_sendinput(x, y1, x, y2, steps=24)
        else:
            ok = self.input.send_drag(x, y1, x, y2, steps=24)
        if not ok:
            return False
        time.sleep(0.20)
        after = self._desktop_palette_frame()
        # 只比较色板区域，确认拖动实际生效；无变化时绝不继续按错误状态绘画。
        px0 = max(0, self.cal.palette_left - self.cal.palette_col_gap // 2)
        px1 = min(after.shape[1], self.cal.palette_left + self.cal.palette_col_gap * 4)
        py0 = max(0, self.cal.palette_grid_top - self.cal.palette_row_gap // 2)
        py1 = min(after.shape[0], self.cal.palette_grid_top + self.cal.palette_row_gap * 6)
        delta = np.abs(before[py0:py1, px0:px1].astype(np.int16)
                       - after[py0:py1, px0:px1].astype(np.int16)).sum()
        return delta > 5000

    def _reset_palette_to_top(self):
        """连续向下拖动直到内容不再变化，回到色板顶部稳定状态。

        色板本来已在顶部时，画面没有变化是正常的边界状态，不应当误报失败。
        后续 `_calibrate_palette()` 会用实际色块扫描验证校准是否可信。
        """
        for _ in range(3):
            if not self._drag_palette(toward_later_rows=False):
                break
        self.progress.cur_scroll_top_row = 0

    _PALETTE_MATCH_LIMIT = 18000

    def _calibrate_palette(self, required_colors: set[int]):
        """预扫描物理色板，建立物理索引到行列的稳定映射。"""
        self._reset_palette_to_top()
        physical = [PaletteSlot(rgb=(rgb[0], rgb[1], rgb[2]),
                                row=index // PALETTE_COLS, col=index % PALETTE_COLS)
                    for index, rgb in enumerate(GAME_PALETTE)]
        if len(physical) != PALETTE_ROWS * PALETTE_COLS:
            raise RuntimeError("游戏色板定义不完整")

        observed: Dict[Tuple[int, int], Tuple[int, int, int]] = {}
        top_row = 0
        for page_index in range(2):
            if page_index == 1:
                if not self._drag_palette(toward_later_rows=True):
                    raise RuntimeError("色板无法滑到底部，不能建立可靠索引")
                top_row = PALETTE_ROWS - self.cal.palette_visible_rows
                self.progress.cur_scroll_top_row = top_row
            for index, (rgb, _) in enumerate(self._read_visible_palette()):
                row = top_row + index // PALETTE_COLS
                col = index % PALETTE_COLS
                if row < PALETTE_ROWS:
                    observed[(row, col)] = rgb

        targets: Dict[int, PaletteSlot] = {}
        for physical_idx in required_colors:
            slot = physical[physical_idx]
            observed_rgb = observed.get((slot.row, slot.col), slot.rgb)
            if color_dist(*observed_rgb, *slot.rgb) > self._PALETTE_MATCH_LIMIT:
                raise RuntimeError(f"物理色板槽位校验失败：第 {slot.row + 1} 行第 {slot.col + 1} 列")
            targets[physical_idx] = PaletteSlot(observed_rgb, slot.row, slot.col)

        self._palette_targets = targets
        self._reset_palette_to_top()

    def _select_color(self, color_idx: int):
        """优先命中预扫描索引；槽位校验失败才重扫或滚动。"""
        slot = self._palette_targets.get(color_idx)
        if slot is None:
            raise RuntimeError(f"未校准颜色 {GAME_PALETTE_DATA[color_idx][0]}")

        for _ in range(2):
            pos = self._visible_slot(slot)
            if pos is not None and self._slot_matches(slot, pos):
                self._click_screen(*pos)
                return

            # 缓存页错误时先回顶部；顶部仍不匹配才向后查找目标行。
            if self.progress.cur_scroll_top_row:
                self._reset_palette_to_top()
                continue
            if slot.row >= self.cal.palette_visible_rows:
                if not self._drag_palette(toward_later_rows=True):
                    break
                self.progress.cur_scroll_top_row = PALETTE_ROWS - self.cal.palette_visible_rows
                continue
            # 目标本应在当前顶部页却校验失败：只重扫该页一次后停止。
            break
        raise RuntimeError(f"当前色板无法校验缓存色 {GAME_PALETTE_DATA[color_idx][0]}")

    def _paint_one(self, x: int, y: int, color_idx: int) -> bool:
        """涂一格。若需切换颜色先点色板，再点画布格子。"""
        if color_idx != self.progress.cur_color_idx:
            self._select_color(color_idx)
            self.progress.cur_color_idx = color_idx
        cx, cy = self.cal.canvas_cell_center(x, y)
        self._click_screen(cx, cy)
        return True

    def _verify(self, x: int, y: int, expected_color_idx: int) -> bool:
        """涂色后校验：截图该格中心，匹配预期颜色"""
        img = self.capturer.grab()
        if img is None:
            return True
        cx, cy = self.cal.canvas_cell_center(x, y)
        # 区域截图（DXGI）：region 起点 = 客户区左上角，
        # 帧内坐标 = 客户区坐标 = 校准坐标（直接相等）
        rx, ry = cx, cy
        if rx < 0 or ry < 0 or rx >= img.shape[1] or ry >= img.shape[0]:
            return True
        rgb = sample_color_at(img, rx, ry, half=4)
        expected = self._palette_targets.get(expected_color_idx)
        return (expected is not None and
                color_dist(rgb[0], rgb[1], rgb[2], expected.rgb[0], expected.rgb[1], expected.rgb[2]) <= 18000)

    def _paint_stroke(self, cells: List[Tuple[int, int, int]]) -> bool:
        """沿同色、严格相邻的单行或单列格心执行一次连续笔画。"""
        if len(cells) < 2:
            return self._paint_one(*cells[0])
        color_idx = cells[0][2]
        if color_idx != self.progress.cur_color_idx:
            self._select_color(color_idx)
            self.progress.cur_color_idx = color_idx
        x1, y1, _ = cells[0]
        x2, y2, _ = cells[-1]
        p1 = self.cal.canvas_cell_center(x1, y1)
        p2 = self.cal.canvas_cell_center(x2, y2)
        steps = max(8, len(cells) * 3)
        if self.input_mode == "windowpos":
            return self.input.drag_with_windowpos(p1[0], p1[1], p2[0], p2[1], steps=steps)
        if self.input_mode == "sendinput":
            return self.input.drag_sendinput(p1[0], p1[1], p2[0], p2[1], steps=steps)
        return self.input.send_drag(p1[0], p1[1], p2[0], p2[1], steps=steps)

    @staticmethod
    def _stroke_groups(ordered: List[Tuple[int, int, int]]) -> List[List[Tuple[int, int, int]]]:
        """将相邻的同色行/列计划合并为笔画，其余保留为单格。"""
        groups: List[List[Tuple[int, int, int]]] = []
        current: List[Tuple[int, int, int]] = []
        for cell in ordered:
            if not current:
                current = [cell]
                continue
            px, py, pc = current[-1]
            x, y, c = cell
            if c == pc and ((y == py and x == px + 1) or (x == px and y == py + 1)):
                current.append(cell)
            else:
                groups.append(current)
                current = [cell]
        if current:
            groups.append(current)
        return groups

    def paint(self, plan: List[Tuple[int, int, int]]):
        """按 plan 涂色

        plan: [(x, y, color_idx), ...]
        """
        # 按颜色分组，按使用频次从高到低排
        groups = {}
        for x, y, c in plan:
            groups.setdefault(c, []).append((x, y, c))
        sorted_colors = sorted(groups.keys(), key=lambda k: -len(groups[k]))
        ordered = []
        for c in sorted_colors:
            ordered.extend(groups[c])

        self.progress.total = len(ordered)
        self.progress.done = 0
        self.progress.failed.clear()
        self.progress.completed.clear()
        self.progress.cur_color_idx = None
        self.progress.cur_scroll_top_row = 0
        self.progress.start_time = time.time()
        # WindowPos 使用稳定的后台窗口消息路径，不模拟官方 C++ 的伪最小化。
        # 目标游戏需保持可见且不要真实最小化，色板校色依赖当前桌面画面。
        # SendInput 仍只支持前台真实光标输入。
        if self.input_mode == "sendinput" and not self._ensure_foreground(1.5):
            self._stop = True
            self.progress.status_msg = "游戏窗口未保持前台，未发送绘制输入"
            self._close_capturers()
            self._emit()
            return self.progress
        # 先只操作色板进行实测校色。任何所需颜色缺失或拖动无效时，
        # 在点击画布前停止，绝不带着猜测的颜色映射开始绘画。
        try:
            # 先保留保存校准 + remap 作为可靠基准，再尝试当前帧几何增强。
            # HDR/飞跃 HDR 或低饱和漏检时，增强失败只回退，不阻断任务。
            enhanced = self._refresh_palette_geometry_safe()
            self.progress.status_msg = (
                "使用当前帧增强校准，正在校准色板颜色…"
                if enhanced else
                "使用比例适配校准，正在校准色板颜色…"
            )
            self._emit()
            self._calibrate_palette(set(groups))
            self.progress.status_msg = "色板校准完成，开始绘画"
            self._emit()
        except Exception as e:
            self._stop = True
            self.progress.status_msg = f"色板颜色校准失败：{e}"
            self._close_capturers()
            self._emit()
            return self.progress

        operations = self._stroke_groups(ordered) if self.stroke_mode else [[cell] for cell in ordered]
        for cells in operations:
            x, y, c = cells[0]
            # 停止条件：程序内停止标志 或 用户按 ESC 急停（系统级检测，游戏在前台也有效）
            if self._stop or _esc_pressed():
                self._stop = True
                if not self.progress.status_msg:
                    self.progress.status_msg = "已按 ESC 急停"
                break
            if self.input_mode == "sendinput" and not self._ensure_foreground(5.0):
                self._stop = True
                if not self.progress.status_msg:
                    self.progress.status_msg = "游戏窗口长时间未在前台，绘画已自动停止"
                break
            try:
                ok = self._paint_stroke(cells) if len(cells) > 1 else self._paint_one(x, y, c)
            except Exception as e:
                self._stop = True
                self.progress.status_msg = f"第 {self.progress.done + 1} 格输入失败：{e}"
                self.progress.failed.extend(cells)
                self._emit()
                break
            if not ok:
                self._stop = True
                self.progress.status_msg = f"第 {self.progress.done + 1} 格未完成，已停止"
                self.progress.failed.extend(cells)
                self._emit()
                break
            for x, y, c in cells:
                if self.verify_enabled and not self._verify(x, y, c):
                    self.progress.failed.append((x, y, c))
                self.progress.done += 1
                self.progress.completed.append((x, y, c))
            self._emit()
            time.sleep(0.08 if len(cells) == 1 else 0.12)

        self._close_capturers()
        return self.progress
