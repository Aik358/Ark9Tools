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
from typing import Dict, List, Optional, Tuple, Callable
import time
import ctypes
import ctypes.wintypes as wt

import numpy as np
from PIL import ImageGrab

from win32_input import WindowInput, find_window, WHEEL_DELTA
from win32_capture import ScreenCapturer
from hdrcapture_adapter import AutoSdrWindowCapture
from calibration import Calibration, detect_palette_from_screenshot, sample_color_at
from palette import EXHIBITION_PALETTE, color_dist, find_nearest_idx


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


class PaintingProgress:
    def __init__(self):
        self.total = 0
        self.done = 0
        self.failed: List[Tuple[int, int, int]] = []   # (x, y, color_idx)
        self.cur_color_idx: Optional[int] = None
        self.cur_page: int = 0
        self.cur_scroll_top_row: int = 0   # 色板当前滚动位置（可见第一行行号）
        self.start_time = 0.0
        self.status_msg: str = ""          # 停止/异常原因（供 UI 展示）


class Painter:
    def __init__(self, hwnd: int, cal: Calibration,
                 window_region: Tuple[int, int, int, int],
                 progress_cb: Optional[Callable[[PaintingProgress], None]] = None,
                 verify: bool = False,
                 input_mode: str = "sendinput"):
        """input_mode: "sendinput"(默认,Unity) | "windowpos" | "plain" """
        self.hwnd = hwnd
        self.input = WindowInput(hwnd)
        self.cal = cal
        self.window_region = window_region
        # 推荐：hdrcapture auto 统一输出 BGRA8 SDR（HDR/Auto HDR 与 SDR 共用）。
        # 原 DXGI 仅在该管线初始化或连续取帧失败时作为兼容回退。
        self.capturer = ScreenCapturer(window_region, prefer_winrt=False)
        try:
            self.sdr_capturer = AutoSdrWindowCapture(hwnd)
            self.sdr_capturer._ensure_pipeline()
            self.sdr_capture_source = "hdrcapture-auto"
        except Exception as exc:
            self.sdr_capturer = None
            self.sdr_capture_source = "dxgi-fallback"
            self._sdr_capture_error = str(exc)
        self.progress = PaintingProgress()
        self.progress_cb = progress_cb
        self.verify_enabled = verify
        self.input_mode = input_mode
        self._stop = False
        # 绘画前校色表：逻辑 X 索引 -> 实测游戏物理色块 RGB。
        # 逻辑色存在重复/近重复，允许多个逻辑色映射到同一个游戏色块。
        self._palette_targets: Dict[int, Tuple[int, int, int]] = {}
        self._client_origin = _client_origin_screen(hwnd)

    def stop(self):
        self._stop = True

    def _close_capturers(self):
        try:
            self.capturer.close()
        except Exception:
            pass
        if self.sdr_capturer is not None:
            try:
                self.sdr_capturer.close()
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

    def _sdr_rgb_frame(self) -> np.ndarray:
        """返回统一 SDR RGB 帧，供网格、色板与画布验证共用。"""
        if self.sdr_capturer is not None:
            bgr = self.sdr_capturer.grab()
            if bgr is not None:
                return np.ascontiguousarray(bgr[:, :, ::-1])
            # hdrcapture 连续失败后释放管线，后续用稳定 DXGI 兼容回退。
            if self.sdr_capturer.last_error:
                self.sdr_capture_source = "dxgi-fallback"
                self.sdr_capturer.close()
                self.sdr_capturer = None
        bgr = self.capturer.grab()
        if bgr is None:
            raise RuntimeError("HDR SDR 捕获与 DXGI 回退均未返回有效窗口帧")
        return np.ascontiguousarray(bgr[:, :, ::-1])

    def _desktop_palette_frame(self) -> np.ndarray:
        """兼容旧调用：返回同一份 SDR RGB 帧。"""
        return self._sdr_rgb_frame()

    def _refresh_palette_geometry(self):
        """从当前桌面画面检测色板网格，刷新仅本次绘画使用的坐标。"""
        detected = detect_palette_from_screenshot(self._desktop_palette_frame())
        if detected is None or detected.palette_visible_rows < 4:
            raise RuntimeError("未能从当前画面检测到色板网格")
        self.cal.palette_left = detected.palette_left
        self.cal.palette_top = detected.palette_top
        self.cal.palette_grid_top = detected.palette_grid_top
        self.cal.palette_col_gap = detected.palette_col_gap
        self.cal.palette_row_gap = detected.palette_row_gap
        self.cal.palette_visible_rows = detected.palette_visible_rows

    def _read_visible_palette(self) -> List[Tuple[Tuple[int, int, int], Tuple[int, int]]]:
        """返回当前视图的全部物理色块：(实测 RGB, 客户区中心坐标)。"""
        img = self._desktop_palette_frame()
        h, w = img.shape[:2]
        cells: List[Tuple[Tuple[int, int, int], Tuple[int, int]]] = []
        for visual_row in range(self.cal.palette_visible_rows):
            y = self.cal.palette_grid_top + visual_row * self.cal.palette_row_gap
            if y < 6 or y >= h - 6:
                continue
            for col in range(4):
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

    def _drag_palette(self, toward_later_rows: bool) -> bool:
        """按住色板内容区连续拖动一个视窗高度。"""
        x = self.cal.palette_left + self.cal.palette_col_gap * 2
        y1 = self.cal.palette_grid_top + self.cal.palette_row_gap * 4
        distance = self.cal.palette_row_gap * 4
        y2 = y1 - distance if toward_later_rows else y1 + distance
        before = self._desktop_palette_frame()
        if self.input_mode == "windowpos":
            ok = self.input.drag_with_windowpos(x, y1, x, y2, steps=24)
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
        """扫描全部色板并建立一对一的逻辑色到物理色块校色表。"""
        self._reset_palette_to_top()
        physical: List[Tuple[int, int, int]] = []
        for pass_index in range(5):
            for rgb, _ in self._read_visible_palette():
                # 相同色块会在相邻视图重复出现，只保留一次。
                if not any(color_dist(*rgb, *old) < 300 for old in physical):
                    physical.append(rgb)
            if pass_index < 4 and not self._drag_palette(toward_later_rows=True):
                break
        if len(physical) < 38:
            raise RuntimeError(f"色板校准只读取到 {len(physical)} 个物理色块")

        # 每个逻辑色独立选取最近的实测游戏色块。
        # X01~X38 中包含重复/近重复颜色，不应强制一个物理格只能分配一次，
        # 否则浅灰、肤色、浅蓝会被挤到完全无关的棕/紫格。
        targets: Dict[int, Tuple[int, int, int]] = {}
        for logical_idx, (_, logical_rgb, _) in enumerate(EXHIBITION_PALETTE):
            _, target = min(
                (color_dist(*logical_rgb, *physical_rgb), physical_rgb)
                for physical_rgb in physical
            )
            targets[logical_idx] = target

        worst = max(
            color_dist(*EXHIBITION_PALETTE[i][1], *targets[i])
            for i in required_colors
        )
        if worst > self._PALETTE_MATCH_LIMIT:
            raise RuntimeError(f"色板校准色差过大：{worst}")
        self._palette_targets = targets
        self._reset_palette_to_top()

    def _select_color(self, color_idx: int):
        """仅点击已校色的物理色块，找不到时连续拖动并重新读取。"""
        if color_idx not in self._palette_targets:
            raise RuntimeError(f"未校准颜色 {EXHIBITION_PALETTE[color_idx][0]}")
        target = self._palette_targets[color_idx]
        for search_round in range(2):
            for _ in range(5):
                visible = self._read_visible_palette()
                dist, pos = min(
                    (color_dist(*rgb, *target), pos) for rgb, pos in visible
                )
                if dist <= 4000:
                    self._click_screen(*pos)
                    return
                if not self._drag_palette(toward_later_rows=True):
                    break
            self._reset_palette_to_top()
        raise RuntimeError(f"当前色板找不到校准色 {EXHIBITION_PALETTE[color_idx][0]}")

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
        try:
            img = self._sdr_rgb_frame()
        except RuntimeError:
            return True
        cx, cy = self.cal.canvas_cell_center(x, y)
        # 区域截图（DXGI）：region 起点 = 客户区左上角，
        # 帧内坐标 = 客户区坐标 = 校准坐标（直接相等）
        rx, ry = cx, cy
        if rx < 0 or ry < 0 or rx >= img.shape[1] or ry >= img.shape[0]:
            return True
        rgb = sample_color_at(img, rx, ry, half=4)
        return find_nearest_idx(rgb) == expected_color_idx

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
            self.progress.status_msg = "正在检测色板网格…"
            self._emit()
            self._refresh_palette_geometry()
            self.progress.status_msg = "正在校准色板颜色…"
            self._emit()
            self._calibrate_palette(set(groups))
            self.progress.status_msg = "色板校准完成，开始绘画"
            self._emit()
        except Exception as e:
            self._stop = True
            self.progress.status_msg = f"色板校准失败：{e}"
            self._close_capturers()
            self._emit()
            return self.progress

        for (x, y, c) in ordered:
            # 停止条件：程序内停止标志 或 用户按 ESC 急停（系统级检测，游戏在前台也有效）
            if self._stop or _esc_pressed():
                self._stop = True
                if not self.progress.status_msg:
                    self.progress.status_msg = "已按 ESC 急停"
                break
            # 窗口化适配：SendInput 只发给前台窗口，游戏必须保持前台点击才有效。
            # 失焦（如用户切走）则暂停等待恢复；超时自动停止，避免误点其他窗口。
            if self.input_mode == "sendinput" and not self._ensure_foreground(5.0):
                self._stop = True
                if not self.progress.status_msg:
                    self.progress.status_msg = "游戏窗口长时间未在前台，绘画已自动停止"
                break
            try:
                ok = self._paint_one(x, y, c)
            except Exception as e:
                self._stop = True
                self.progress.status_msg = f"第 {self.progress.done + 1} 格输入失败：{e}"
                self.progress.failed.append((x, y, c))
                self._emit()
                break
            if not ok:
                self._stop = True
                self.progress.status_msg = f"第 {self.progress.done + 1} 格未完成，已停止"
                self.progress.failed.append((x, y, c))
                self._emit()
                break
            if self.verify_enabled and not self._verify(x, y, c):
                self.progress.failed.append((x, y, c))
            self.progress.done += 1
            self._emit()
            # 每格间隔：给游戏足够响应时间（避免点太快游戏反应不过来）
            time.sleep(0.08)

        self._close_capturers()
        return self.progress
