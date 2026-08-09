# -*- coding: utf-8 -*-
"""校准模块：画布与色板坐标计算 + OpenCV 自动检测"""
from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple

import numpy as np

from palette import EXHIBITION_PALETTE

GRID_SIZE = 24
PALETTE_COLS = 4
PALETTE_TOTAL_ROWS = (len(EXHIBITION_PALETTE) + PALETTE_COLS - 1) // PALETTE_COLS  # 10


# ===========================================================================
# 界面预设（已知游戏界面的默认校准参考值）
# 用于：用户没有运行过校准、或自动检测失败时的兜底
# 单位：客户区坐标（相对窗口客户区左上角）
# ===========================================================================
INTERFACE_PRESETS = {
    # 默认预设：基于明日方舟夏日嘉年华像素画编辑器
    # 画布：白色 24×24 网格，每格约 20×20px
    # 色板：右侧 4 列，每块 38×38px，起点约 (590, 220)
    "arknights_summer": {
        "canvas_origin": (371, 182),   # (0,0) 格左上角（从真实截图精确分析）
        "canvas_cell_w": 29,
        "canvas_cell_h": 29,
        "palette_left": 1200,          # 第 0 列中心 X
        "palette_top": 438,            # 可见第 0 行中心 Y（滚动基准）
        "palette_cell_w": 75,
        "palette_cell_h": 75,
        "palette_col_gap": 88,         # 列间距
        "palette_row_gap": 88,         # 行间距
        "palette_visible_rows": 6,     # 每屏可见约 6 行
    },
}


def apply_preset(name: str) -> Calibration:
    """加载界面预设，返回默认校准。"""
    p = INTERFACE_PRESETS.get(name)
    if p is None:
        raise ValueError(f"未知预设: {name}")
    cal = Calibration()
    cal.canvas_origin = tuple(p["canvas_origin"])
    cal.canvas_cell_w = p["canvas_cell_w"]
    cal.canvas_cell_h = p["canvas_cell_h"]
    cal.palette_left = p["palette_left"]
    cal.palette_top = p["palette_top"]
    # palette_top 本身即"网格第 0 行"中心 Y（色板网格的滚动基准）
    cal.palette_grid_top = p.get("palette_grid_top", p["palette_top"])
    cal.palette_col_gap = p.get("palette_col_gap", 88)
    cal.palette_row_gap = p.get("palette_row_gap", 88)
    cal.palette_cell_w = p.get("palette_cell_w", 75)
    cal.palette_cell_h = p.get("palette_cell_h", 75)
    cal.palette_visible_rows = p.get("palette_visible_rows", 6)
    return cal


def list_presets() -> List[str]:
    """列出所有可用预设名称"""
    return list(INTERFACE_PRESETS.keys())


class Calibration:
    """所有坐标均为客户区相对坐标（相对窗口客户区左上角，像素）。

    为兼容不同屏幕分辨率 / 游戏窗口尺寸 / 模拟器，校准结果会记录
    校准时的客户区参考尺寸 ref_w/ref_h；运行时用 remap() 按当前
    客户区尺寸等比例换算，即可做到分辨率无关。
    """

    def __init__(self):
        # ---- 画布 ----
        self.canvas_origin: Tuple[int, int] = (0, 0)   # (0,0) 格左上角
        self.canvas_cell_w: int = 0
        self.canvas_cell_h: int = 0

        # ---- 色板（基于网格：中心点 + 行列间距）----
        # 色板实际布局（实测）：
        #   顶部：1 个"当前选中"大色块（带高亮边框），中心 ≈ palette_top
        #   下方：N 行 × 4 列 小色块网格（X01~X38 行优先排列）
        #   palette_grid_top = 网格第 0 行的中心 Y（在"当前选中"大色块之下）
        self.palette_left: int = 0       # 第 0 列色块中心 X（与"当前选中"列对齐）
        self.palette_top: int = 0        # "当前选中"大色块中心 Y（保留兼容旧配置）
        self.palette_grid_top: int = 0   # 网格第 0 行中心 Y（用于回读与点击计算）
        self.palette_col_gap: int = 88   # 列间距
        self.palette_row_gap: int = 88   # 行间距
        self.palette_cell_w: int = 75    # 色块尺寸（用于命中判断）
        self.palette_cell_h: int = 75
        self.palette_visible_rows: int = 6   # 每屏可见网格行数
        self.palette_scroll_top_row: int = 0  # 当前可见第一行对应的色板行号

        # ---- 参考尺寸（校准时的客户区宽高，用于分辨率无关换算）----
        self.ref_w: int = 0
        self.ref_h: int = 0

    # ---------- 画布 ----------
    def canvas_cell_center(self, x: int, y: int) -> Tuple[int, int]:
        ox, oy = self.canvas_origin
        return (ox + x * self.canvas_cell_w + self.canvas_cell_w // 2,
                oy + y * self.canvas_cell_h + self.canvas_cell_h // 2)

    def set_canvas_from_corners(self, p0: Tuple[int, int], p23: Tuple[int, int]):
        """由 (0,0) 格中心与 (23,23) 格中心推算画布参数"""
        self.canvas_cell_w = max(1, round((p23[0] - p0[0]) / (GRID_SIZE - 1)))
        self.canvas_cell_h = max(1, round((p23[1] - p0[1]) / (GRID_SIZE - 1)))
        self.canvas_origin = (p0[0] - self.canvas_cell_w // 2,
                              p0[1] - self.canvas_cell_h // 2)

    # ---------- 色板 ----------
    def palette_cell_center(self, color_idx: int,
                            scroll_top_row: Optional[int] = None) -> Tuple[int, int]:
        """色板中 color_idx (0~37) 的色块中心。

        使用网格坐标：palette_grid_top 是"网格第 0 行"中心 Y；
        滚动后 grid_top_row 变化，每行 Y = grid_top + (row - top) * row_gap。

        Args:
            color_idx: 颜色索引 0~37（X01=0 ... X38=37）
            scroll_top_row: 当前可见网格首行对应的色板行号；None 用当前记录值
        """
        top = self.palette_scroll_top_row if scroll_top_row is None else scroll_top_row
        row = color_idx // PALETTE_COLS
        col = color_idx % PALETTE_COLS
        visual_row = row - top
        cx = self.palette_left + col * self.palette_col_gap
        cy = self.palette_grid_top + visual_row * self.palette_row_gap
        return cx, cy

    def set_palette_from_points(self, p00: Tuple[int, int],
                                p10: Tuple[int, int],
                                p01: Optional[Tuple[int, int]],
                                scroll_top_row: int = 0) -> Tuple[int, int]:
        """由色块标点推算色板参数（p00/p10/p01 均为色块中心坐标）。

        p00: 第0行第0列色块中心（视为"当前选中"行）
        p10: 第1行第0列色块中心（推算行间距）
        p01: 第0行第1列色块中心（推算列间距）
        scroll_top_row: 当前可见第一行对应色板行号

        Returns: (col_gap, row_gap)
        """
        self.palette_row_gap = max(1, round(p10[1] - p00[1]))
        self.palette_left = p00[0]
        self.palette_top = p00[1]
        # p00 即"网格第 0 行"色块中心（色板网格滚动基准）
        self.palette_grid_top = p00[1]
        self.palette_scroll_top_row = scroll_top_row
        if p01 is not None:
            self.palette_col_gap = max(1, round(p01[0] - p00[0]))
        return self.palette_col_gap, self.palette_row_gap

    # ---------- 序列化 ----------
    def to_dict(self) -> dict:
        return {
            "canvas_origin": list(self.canvas_origin),
            "canvas_cell_w": self.canvas_cell_w,
            "canvas_cell_h": self.canvas_cell_h,
            "palette_left": self.palette_left,
            "palette_top": self.palette_top,
            "palette_grid_top": self.palette_grid_top,
            "palette_col_gap": self.palette_col_gap,
            "palette_row_gap": self.palette_row_gap,
            "palette_cell_w": self.palette_cell_w,
            "palette_cell_h": self.palette_cell_h,
            "palette_visible_rows": self.palette_visible_rows,
            "palette_scroll_top_row": self.palette_scroll_top_row,
            "ref_w": self.ref_w,
            "ref_h": self.ref_h,
        }

    def from_dict(self, d: dict):
        self.canvas_origin = tuple(d["canvas_origin"])
        self.canvas_cell_w = d["canvas_cell_w"]
        self.canvas_cell_h = d["canvas_cell_h"]
        self.palette_left = d.get("palette_left", 0)
        self.palette_top = d.get("palette_top", 0)
        self.palette_col_gap = d.get("palette_col_gap", 88)
        self.palette_row_gap = d.get("palette_row_gap", 88)
        self.palette_cell_w = d.get("palette_cell_w", 75)
        self.palette_cell_h = d.get("palette_cell_h", 75)
        self.palette_visible_rows = d.get("palette_visible_rows", 6)
        self.palette_scroll_top_row = d.get("palette_scroll_top_row", 0)
        self.ref_w = d.get("ref_w", 0)
        self.ref_h = d.get("ref_h", 0)
        # 兼容旧配置：palette_top 即"网格第 0 行"中心 Y（色板网格滚动基准），
        # palette_grid_top 缺省等于 palette_top。若配置里已显式写了正确值则沿用。
        self.palette_grid_top = d.get("palette_grid_top", self.palette_top)

    # ---------- 分辨率无关换算 ----------
    def remap(self, client_w: int, client_h: int) -> "Calibration":
        """按当前客户区尺寸换算坐标，实现分辨率 / 窗口大小 / 模拟器自适应。

        原理：校准坐标以"校准时的客户区"为参照系（ref_w/ref_h）。
        若当前客户区尺寸与参照系不同，将所有坐标按比例缩放即可。
        缩放后画布仍是 24×24 格（网格数不变），色板行列数也不变，
        只是像素间距按比例变化——这正是窗口整体缩放 / 模拟器的行为。

        Args:
            client_w, client_h: 当前窗口客户区宽高（像素）

        Returns: 换算后的新 Calibration（不改动自身）
        """
        cal = Calibration()
        cal.from_dict(self.to_dict())
        if self.ref_w <= 0 or self.ref_h <= 0:
            # 旧配置无参考尺寸：无法换算，原样返回（并记录当前尺寸）
            cal.ref_w, cal.ref_h = client_w, client_h
            return cal
        sx = client_w / self.ref_w
        sy = client_h / self.ref_h

        ox, oy = self.canvas_origin
        cal.canvas_origin = (round(ox * sx), round(oy * sy))
        cal.canvas_cell_w = max(1, round(self.canvas_cell_w * sx))
        cal.canvas_cell_h = max(1, round(self.canvas_cell_h * sy))

        cal.palette_left = round(self.palette_left * sx)
        cal.palette_top = round(self.palette_top * sy)
        cal.palette_grid_top = round(self.palette_grid_top * sy)
        cal.palette_col_gap = max(1, round(self.palette_col_gap * sx))
        cal.palette_row_gap = max(1, round(self.palette_row_gap * sy))
        cal.palette_cell_w = max(1, round(self.palette_cell_w * sx))
        cal.palette_cell_h = max(1, round(self.palette_cell_h * sy))

        cal.ref_w, cal.ref_h = client_w, client_h
        return cal

    def is_canvas_valid(self) -> bool:
        return self.canvas_cell_w > 0 and self.canvas_cell_h > 0

    def is_palette_valid(self) -> bool:
        return self.palette_cell_w > 0 and self.palette_cell_h > 0


# ===========================================================================
# OpenCV 自动检测
# ===========================================================================
def _detect_canvas_by_border(gray: np.ndarray,
                             palette_box: Optional[Tuple[int, int, int, int]] = None
                             ) -> Optional[Calibration]:
    """大矩形边框检测：画布外框是连续的长水平线 + 长垂直线构成的矩形。

    这是最可靠的"结构锚点"——完全基于几何（长直线 run-length），
    不依赖任何颜色，HDR 校色把画面整体提亮/偏色也不影响。
    画布是 24×24 正方形网格，其外边框满足：
      - 四条边都是足够长的连续直线（占窗口较大比例）
      - 矩形宽高比 ≈ 1（方形）
      - 边在窗口内侧（不会贴到客户区边缘）

    Args:
        gray: 灰度图
        palette_box: 色板包围盒 (x0,y0,x1,y1)；画布应在色板左侧，作为先验。

    Returns: Calibration（仅画布参数）或 None
    """
    try:
        import cv2
    except ImportError:
        return None

    h, w = gray.shape[:2]
    if h < 200 or w < 200:
        return None

    edges = cv2.Canny(gray, 30, 110, apertureSize=3)

    # ---- 1. 每行 / 每列找最长连续边缘 run（即长直线）----
    min_run = int(min(w, h) * 0.25)          # 边至少占窗口 25%
    if palette_box is not None:
        max_x = max(120, palette_box[0] - 20)  # 画布只可能在色板左侧
    else:
        max_x = w

    # 水平线候选：每行最长 run
    h_best = []  # (y, x_start, x_end, len)
    for y in range(h):
        row = edges[y, :max_x]
        x = 0
        while x < max_x:
            if row[x]:
                x2 = x
                while x2 < max_x and row[x2]:
                    x2 += 1
                ln = x2 - x
                if ln >= min_run:
                    h_best.append((y, x, x2 - 1, ln))
                x = x2
            else:
                x += 1
    # 垂直线候选：每列最长 run
    v_best = []  # (x, y_start, y_end, len)
    for x in range(max_x):
        col = edges[:h, x]
        y = 0
        while y < h:
            if col[y]:
                y2 = y
                while y2 < h and col[y2]:
                    y2 += 1
                ln = y2 - y
                if ln >= min_run:
                    v_best.append((x, y, y2 - 1, ln))
                y = y2
            else:
                y += 1

    if len(h_best) < 2 or len(v_best) < 2:
        return None

    # ---- 2. 聚类相邻行/列（线条有 2~3px 宽度）----
    def cluster(lines, axis, tol=3):
        """axis=0: y 聚类; axis=1: x 聚类。返回每簇中"覆盖最长"的一条"""
        if axis == 0:
            lines = sorted(lines, key=lambda t: t[0])
            key = lambda t: t[0]
        else:
            lines = sorted(lines, key=lambda t: t[0])
            key = lambda t: t[0]
        groups = []
        cur = [lines[0]]
        for ln in lines[1:]:
            if key(ln) - key(cur[-1]) <= tol:
                cur.append(ln)
            else:
                groups.append(cur)
                cur = [ln]
        groups.append(cur)
        # 每簇取最长 run
        return [max(g, key=lambda t: t[3]) for g in groups]

    h_lines = cluster(h_best, 0)  # (y, xs, xe, len)
    v_lines = cluster(v_best, 1)  # (x, ys, ye, len)

    # ---- 3. 组合矩形：两条横线 + 两条竖线，四边须贯通覆盖整个矩形 ----
    best = None   # (score, x0, y0, x1, y1, cell)
    for i in range(len(h_lines)):
        y0, hxs0, hxe0, _ = h_lines[i]
        for j in range(i + 1, len(h_lines)):
            y1_, hxs1, hxe1, _ = h_lines[j]
            y0, y1_ = min(y0, y1_), max(y0, y1_)
            bh_ = y1_ - y0
            if bh_ < min_run:
                continue
            # 两条横线都应贯通 [x0..x1]（先暂用它们的公共覆盖区间）
            lx = max(hxs0, hxs1)
            rx = min(hxe0, hxe1)
            if rx - lx < min_run:
                continue
            # 每条横线自身长度也应接近公共区间
            for a in range(len(v_lines)):
                x0, vys0, vye0, _ = v_lines[a]
                for b in range(a + 1, len(v_lines)):
                    x1_, vys1, vye1, _ = v_lines[b]
                    x0, x1_ = min(x0, x1_), max(x0, x1_)
                    bw_ = x1_ - x0
                    if bw_ < min_run:
                        continue
                    # ---- 四边贯通校验：每条边都应覆盖矩形全宽/全高 ----
                    # 横线 0/1：须从左到右覆盖 [x0, x1]（误差 ≤ 12px）
                    tol = 12
                    if not (hxs0 - tol <= x0 and hxe0 + tol >= x1_):
                        continue
                    if not (hxs1 - tol <= x0 and hxe1 + tol >= x1_):
                        continue
                    # 竖线 0/1：须从上到下覆盖 [y0, y1_]
                    if not (vys0 - tol <= y0 and vye0 + tol >= y1_):
                        continue
                    if not (vys1 - tol <= y0 and vye1 + tol >= y1_):
                        continue
                    # 方形画布：宽高比接近 1
                    if max(bw_, bh_) / min(bw_, bh_) > 1.15:
                        continue
                    # 尺寸合理：画布是画面主体，边长至少占窗口短边 30%
                    if bw_ < min(w, h) * 0.30 or bh_ < min(w, h) * 0.30:
                        continue
                    # 边在窗口内侧（不贴边）
                    if x0 < 5 or y0 < 5 or x1_ > w - 5 or y1_ > h - 5:
                        continue
                    # 四象限：画布中心应位于窗口中部偏左（避免误检 UI 小矩形）
                    cx = (x0 + x1_) / 2
                    cy = (y0 + y1_) / 2
                    if not (w * 0.25 <= cx <= w * 0.72):
                        continue
                    if not (h * 0.20 <= cy <= h * 0.80):
                        continue
                    # 可整除 24 格（画布网格数固定）
                    cell = bw_ / GRID_SIZE
                    if cell < 8 or cell > 60:
                        continue
                    # 分数：越接近正方形、面积越大越好
                    score = (bw_ + bh_) / (1 + abs(bw_ - bh_) / max(1, min(bw_, bh_)))
                    if best is None or score > best[0]:
                        best = (score, x0, y0, x1_, y1_, cell)

    if best is None:
        return None
    _, x0, y0, x1_, y1_, cell = best
    cal = Calibration()
    cal.canvas_origin = (x0, y0)
    cal.canvas_cell_w = round(cell)
    cal.canvas_cell_h = round(cell)
    return cal


def detect_canvas_from_screenshot(img: np.ndarray) -> Optional[Calibration]:
    """自动检测画布（多策略，优先几何结构定位，网格线检测兜底）。

    策略：
    0. 画布大矩形边框（结构锚点，纯几何，HDR 校色 / 白色画布依然可靠）
    1. 圆形标记 / "10"字准线 → 画布中心 → 中心扫描定边界（分辨率无关，
       不依赖颜色，HDR 校色后依然可靠）
    2. Canny + Hough 等间距网格线拟合（网格线对比度足够时的高精度方案）

    Returns: 可用的 Calibration（仅画布参数）或 None
    """
    try:
        import cv2
    except ImportError:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]

    # 色板先验（供策略 0/2 使用：画布在色板左侧）
    pal_box = _find_palette_box(img)

    # 策略 0（最强锚点）：大矩形边框（长直线 run-length，纯几何）
    cal = _detect_canvas_by_border(gray, pal_box)
    if cal is not None:
        cal.ref_w, cal.ref_h = w, h
        return cal

    # 策略 1：圆形标记 / 十字准线 → 中心扫描（几何结构定位）
    cal = _detect_canvas_by_markers(gray)
    if cal is not None:
        cal.ref_w, cal.ref_h = w, h
        return cal

    # 画布检测区域：若检测到色板，只搜色板左侧；否则全图
    if pal_box is not None:
        left_zone = gray[:, : max(100, pal_box[0] - 20)]
    else:
        left_zone = gray

    # ================= 主策略：网格线检测（分辨率无关，不依赖颜色） =================
    # 画布是 24×24 等间距网格，通过 Canny + Hough 检测网格线，
    # 用 find_grid 拟合等间距序列。这是最可靠、与 HDR/颜色无关的方式。
    # 画布在窗口左侧，色板在右侧，用色板先验排除右侧干扰。

    def find_grid(arr):
        """在坐标数组中拟合等间距网格（容忍干扰线与轻微间距波动）。

        策略：
        1. 间隙直方图（±2px 宽容）找众数间距 gap
        2. 从每个起点出发，累积间隙匹配（相邻间隙与 gap 接近即累计），
           找最长的"准连续"网格段
        3. 返回 (起点, 终点, 平均间距, 线数)
        """
        if len(arr) < 3:
            return None
        gaps = [arr[i + 1] - arr[i] for i in range(len(arr) - 1)]
        from collections import Counter
        cnt = Counter()
        for g in gaps:
            if g > 2:
                for base in (round(g - 2), round(g), round(g + 2)):
                    cnt[base] += 1
        if not cnt:
            return None
        best_gap = cnt.most_common(1)[0][0]
        tol = max(3, best_gap // 5)

        # 用宽容匹配累加，找最长段
        best_start, best_len, best_total = 0, 0, 0
        cur_start, cur_len, cur_total = 0, 0, 0
        for i in range(len(arr) - 1):
            g = arr[i + 1] - arr[i]
            if abs(g - best_gap) <= tol:
                if cur_len == 0:
                    cur_start = i
                cur_len += 1
                cur_total += g
            else:
                if cur_len > best_len:
                    best_len, best_start, best_total = cur_len, cur_start, cur_total
                cur_len, cur_total = 0, 0
        if cur_len > best_len:
            best_len, best_start, best_total = cur_len, cur_start, cur_total

        if best_len < 4:
            return None
        end = best_start + best_len
        avg_gap = best_total / best_len
        return arr[best_start], arr[end], avg_gap, best_len + 1

    def dedup(arr, tol=3):
        """合并相距 <= tol 的坐标（网格线 1px 宽会被 Hough 检出为相邻两条）"""
        arr = sorted(arr)
        if not arr:
            return []
        out = [arr[0]]
        for v in arr[1:]:
            if v - out[-1] > tol:
                out.append(v)
        return out

    def try_detect(canny_lo, canny_hi, min_len_ratio, hough_thresh):
        edges = cv2.Canny(left_zone, canny_lo, canny_hi, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=hough_thresh,
                                minLineLength=max(20, int(w * min_len_ratio)),
                                maxLineGap=4)
        if lines is None:
            return None
        lines = np.asarray(lines, dtype=np.float64)
        if lines.ndim == 3:
            lines = lines[:, 0, :]
        elif lines.ndim != 2:
            return None

        h_lines, v_lines = [], []
        for x1, y1, x2, y2 in lines:
            if abs(y1 - y2) < 3:
                h_lines.append(int((y1 + y2) / 2))
            elif abs(x1 - x2) < 3:
                v_lines.append(int((x1 + x2) / 2))
        h_lines = dedup(h_lines)
        v_lines = dedup(v_lines)
        if len(h_lines) < 5 or len(v_lines) < 5:
            return None

        h_span = find_grid(h_lines)
        v_span = find_grid(v_lines)
        if h_span is None or v_span is None:
            return None

        top, bottom, cell_h, n_h = h_span
        left, right, cell_w, n_v = v_span
        if cell_h <= 1 or cell_w <= 1:
            return None

        cell_wf = (right - left) / max(1, n_v - 1)
        cell_hf = (bottom - top) / max(1, n_h - 1)
        mid_v = (left + right) / 2
        mid_h = (top + bottom) / 2
        origin_line_x = mid_v - 12 * cell_wf
        origin_line_y = mid_h - 12 * cell_hf

        cal = Calibration()
        cal.canvas_cell_w = round(cell_wf)
        cal.canvas_cell_h = round(cell_hf)
        cal.canvas_origin = (round(origin_line_x), round(origin_line_y))
        ox, oy = cal.canvas_origin
        if ox < 0 or oy < 0 or ox + 24 * cal.canvas_cell_w > w \
                or oy + 24 * cal.canvas_cell_h > h:
            return None
        return cal

    # 多组参数尝试：从宽松到严格
    attempts = [
        (30, 120, 1 / 14, 40),    # 宽松：弱边缘
        (40, 140, 1 / 12, 50),
        (60, 180, 1 / 16, 40),    # 强边缘 + 更长线
        (20, 100, 1 / 14, 30),    # 非常宽松
    ]
    for args in attempts:
        cal = try_detect(*args)
        if cal is not None:
            cal.ref_w, cal.ref_h = w, h
            return cal
    return None


def _find_canvas_by_crosshair(gray: np.ndarray,
                              cx: float = 0.0, cy: float = 0.0) -> Optional[Calibration]:
    """通过贯穿画布的"10"字准线直接确定画布边界（几何定位，不依赖颜色）。

    原理：
    - 画布中央有贯穿整个画布的深色十字准线，其交叉点即画布中心
    - 准线颜色不固定（HDR 校色后可能偏移），从中心点像素自适应获取
    - 准线横线左右端点 ≈ 画布左右边界（实测差 2px）
    - 准线竖线上下端点 ≈ 画布上下边界（实测差 2~3px）

    Args:
        gray: 灰度图
        cx, cy: 画布中心（准线交叉点）；传 0,0 时自动检测

    完全不依赖颜色数值，只依赖"贯穿结构"与线段端点。
    """
    try:
        import cv2
    except ImportError:
        return None
    h, w = gray.shape[:2]
    if h < 300 or w < 300:
        return None

    # 未指定中心时：用左右圆标记或网格中点估计
    if cx <= 0 or cy <= 0:
        center = _detect_center_from_markers(gray)
        if center is None:
            return None
        cx, cy = center

    cx, cy = int(round(cx)), int(round(cy))
    if not (0 < cx < w and 0 < cy < h):
        return None

    # 准线色 = 中心点像素颜色（中心在准线交叉点上，自适应 HDR 偏移）
    base = int(gray[cy, cx])
    tol = 25
    mask = np.abs(gray.astype(np.int16) - base) < tol

    # 水平：沿准线横线从中心向左右延伸（单行）
    row = mask[cy]
    left = cx
    while left > 0 and row[left - 1]:
        left -= 1
    right = cx
    while right < w - 1 and row[right + 1]:
        right += 1

    # 垂直：沿准线竖线从中心向上下延伸（5 列投票，避免顶部 UI 误连）
    cols = [cx + d for d in (-2, -1, 0, 1, 2) if 0 <= cx + d < w]
    if len(cols) < 3:
        return None
    col_mask = mask[:, cols]
    top = cy
    while top > 0 and col_mask[top - 1].mean() >= 0.6:
        top -= 1
    bottom = cy
    while bottom < h - 1 and col_mask[bottom + 1].mean() >= 0.6:
        bottom += 1

    bw = right - left
    bh = bottom - top
    # 校验：画布应足够大、近似正方形、格子尺寸合理
    if not (150 < bw < w * 0.95) or not (150 < bh < h * 0.95):
        return None
    cell_w = bw / 24.0
    cell_h = bh / 24.0
    if abs(cell_w - cell_h) > max(cell_w, cell_h) * 0.3:
        return None
    if not (8 < cell_w < 80):
        return None

    cal = Calibration()
    cal.canvas_origin = (left, top)
    cal.canvas_cell_w = round(cell_w)
    cal.canvas_cell_h = round(cell_h)
    return cal


def _detect_center_from_markers(gray: np.ndarray) -> Optional[Tuple[float, float]]:
    """检测左右圆标记（或近似网格中心）返回画布中心。

    优先：左右两个水平配对的圆标记，中点即画布中心。
    兜底：在图中部搜索"两个相距较远的相似圆"或网格线密集区域。
    """
    try:
        import cv2
    except ImportError:
        return None
    h, w = gray.shape[:2]
    try:
        blurred = cv2.GaussianBlur(gray, (9, 9), 0)
        circles = cv2.HoughCircles(
            blurred, cv2.HOUGH_GRADIENT, dp=1, minDist=40,
            param1=100, param2=25,
            minRadius=4, maxRadius=int(min(w, h) * 0.15))
    except Exception:
        circles = None

    if circles is not None:
        circles = np.asarray(circles[0], dtype=np.int32)
        best_pair = None
        best_mid = None
        for i in range(len(circles)):
            for j in range(i + 1, len(circles)):
                cx1, cy1, r1 = (int(circles[i][0]), int(circles[i][1]),
                                int(circles[i][2]))
                cx2, cy2, r2 = (int(circles[j][0]), int(circles[j][1]),
                                int(circles[j][2]))
                if abs(cy1 - cy2) < max(12, max(r1, r2) * 3):
                    dist = abs(cx2 - cx1)
                    if 200 < dist < w * 0.7:
                        if abs(r1 - r2) < max(r1, r2) * 0.9:
                            mid = ((cx1 + cx2) / 2.0, (cy1 + cy2) / 2.0)
                            mid_y = (cy1 + cy2) / 2.0
                            score = dist if mid_y > 150 else dist * 0.5
                            if best_mid is None or score > best_pair:
                                best_pair = score
                                best_mid = mid
        if best_mid is not None:
            return best_mid
    return None


def _detect_canvas_by_markers(gray: np.ndarray) -> Optional[Calibration]:
    """通过画布上的几何标记定位画布（适配不同缩放/分辨率）。

    思路：
    1. 主方法：检测贯穿画布的"10"字准线端点 → 直接得画布边界
       （完全几何定位，不依赖颜色）
    2. 辅助方法：左右圆标记 → 画布中心 → 中心扫描定边界
       （准线不清晰时使用）

    此方法用相对特征定位，不依赖绝对坐标/颜色，能适应不同分辨率/缩放。
    """
    try:
        import cv2
    except ImportError:
        return None

    h, w = gray.shape[:2]

    # 先检测中心：左右圆标记（准线交叉点与圆标记中点一致）
    center = _detect_center_from_markers(gray)

    # 方法一：十字准线端点直接定边界（最可靠，中心已知时直接延伸）
    if center is not None:
        canvas = _find_canvas_by_crosshair(gray, cx=center[0], cy=center[1])
    else:
        canvas = _find_canvas_by_crosshair(gray)
    if canvas is not None:
        return canvas

    # 方法二：左右圆标记 → 中心扫描（准线不清晰时兜底）
    if center is not None:
        mid_x, mid_y = center
        canvas = _find_canvas_by_center_scan(gray, mid_x, mid_y)
        if canvas is not None:
            return canvas
    return None


def _find_canvas_by_center_scan(gray: np.ndarray, cx: float, cy: float) -> Optional[Calibration]:
    """从画布中心向四周扫描，找到画布边界（多线投票，不依赖绝对颜色）。

    思路：
    - 画布中心区域存在"10"字准线（贯穿画布的深色十字，仅 1~3px 宽）。
      若用单行扫描，从中心出发会立刻撞上十字线导致误判边界。
    - 改用 7 条平行线投票：某列/某行上只有少数平行线偏离底色时视为干扰
      （十字线、零星涂色格子），只有当大部分平行线都偏离底色（真正离开画布）
      才判定为边界。
    - 底色 base 取中心区域众数（自适应 HDR 校色后的实际值，不依赖固定颜色）。

    Args:
        gray: 灰度图
        cx, cy: 画布中心坐标
    """
    try:
        import cv2
    except ImportError:
        return None

    h, w = gray.shape[:2]
    cx, cy = int(cx), int(cy)

    # 画布底色：取中心周围较大区域的众数灰度（自适应）。
    ph, pw = 60, 60
    x0, y0 = max(0, cx - pw), max(0, cy - ph)
    x1, y1 = min(w, cx + pw), min(h, cy + ph)
    patch = gray[y0:y1, x0:x1].flatten()
    if len(patch) == 0:
        return None
    from collections import Counter
    cnt = Counter(int(v) for v in patch if 60 < v < 200)
    if not cnt:
        return None
    base = cnt.most_common(1)[0][0]

    # 多行/多列投票扫描：positions 为 (n_lines, n_points) 灰度矩阵
    # 返回"进入深色背景"的偏移（从中心计）。思路：
    # 画布外是纯黑背景（HDR 校色后仍显著深于画布），这是最可靠的特征。
    # 画布内即使有已涂色的浅色/彩色格子，也不会多行同时变成深色。
    # 从中心向外，找第一条"多数行同时深色、且连续足够长"的段起点 = 边界。
    def scan_axis(positions: np.ndarray, dark: int = 80,
                  vote_ratio: float = 0.7, min_run: int = 15) -> int:
        n = positions.shape[1]
        dark_mask = positions < dark                    # 每点是否深色
        ratio = dark_mask.mean(axis=0)                  # 每条扫描线的深色比例
        run = 0
        for i, r in enumerate(ratio):
            if r > vote_ratio:
                run += 1
                if run >= min_run:
                    return i - run + 1
            else:
                run = 0
        return n - 1

    # 向右 / 向左：多条横线扫描（跳过中心十字准线的竖线，宽容已涂色行）
    off_rows = [cy + d for d in (-6, -4, -2, 0, 2, 4, 6) if 0 <= cy + d < h]
    if len(off_rows) < 3:
        return None
    rows_arr = np.asarray(off_rows, dtype=np.int32)
    right_pos = gray[rows_arr[:, None], np.arange(cx, w)[None, :]]
    left_pos = gray[rows_arr[:, None], np.arange(cx, -1, -1)[None, :]]
    right_off = scan_axis(right_pos)
    left_off = scan_axis(left_pos)
    right_x = cx + right_off
    left_x = cx - left_off

    # 向上 / 向下：多条竖线扫描
    off_cols = [cx + d for d in (-6, -4, -2, 0, 2, 4, 6) if 0 <= cx + d < w]
    if len(off_cols) < 3:
        return None
    cols_arr = np.asarray(off_cols, dtype=np.int32)
    bottom_pos = gray[np.arange(cy, h)[None, :], cols_arr[:, None]]
    top_pos = gray[np.arange(cy, -1, -1)[None, :], cols_arr[:, None]]
    bottom_off = scan_axis(bottom_pos)
    top_off = scan_axis(top_pos)
    bottom_y = cy + bottom_off
    top_y = cy - top_off

    bw_ = right_x - left_x
    bh_ = bottom_y - top_y
    if bw_ < 200 or bh_ < 200:
        return None
    cell_w = bw_ / 24.0
    cell_h = bh_ / 24.0
    if abs(cell_w - cell_h) > max(cell_w, cell_h) * 0.3:
        return None
    if not (8 < cell_w < 80):
        return None

    # 中心一致性校验：画布是正方形、中心即准线交叉点，
    # 四条边到中心的距离应几乎相等。若差异过大，说明扫描吞进了
    # 画布外同色 UI（如左侧 153 浅灰面板），拒绝该结果而非返回错误边界。
    ds = [cx - left_x, right_x - cx, cy - top_y, bottom_y - cy]
    if min(ds) <= 0:
        return None
    med = float(np.median(ds))
    if med < 60 or max(ds) / min(ds) > 1.25:
        return None

    cal = Calibration()
    cal.canvas_origin = (left_x, top_y)
    cal.canvas_cell_w = round(cell_w)
    cal.canvas_cell_h = round(cell_h)
    return cal


def _find_palette_box(img: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """用高饱和色块网格检测定位色板包围盒 (x0, y0, x1, y1)。

    用于画布检测的先验：画布应在色板左侧。
    排除顶部导航区（y<150），要求检测到多个色块。
    """
    try:
        import cv2
    except ImportError:
        return None

    h, w = img.shape[:2]
    # 在右半屏 + 排除顶部导航区（y>150）搜索色板
    zone = img[150:, w // 2:]
    hsv = cv2.cvtColor(zone, cv2.COLOR_RGB2HSV)
    sat = ((hsv[:, :, 1] > 50) & (hsv[:, :, 2] > 40)).astype(np.uint8) * 255
    sat = cv2.morphologyEx(sat, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(sat, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    hits = []
    for c in cnts:
        x, y, bw_, bh_ = cv2.boundingRect(c)
        if 40 < bw_ < 100 and 40 < bh_ < 100 and cv2.contourArea(c) > 1500:
            hits.append((x + w // 2, y + 150))

    if len(hits) < 3:
        return None
    xs = [h[0] for h in hits]
    ys = [h[1] for h in hits]
    return (min(xs), min(ys), max(xs), max(ys))


def detect_palette_from_screenshot(img: np.ndarray,
                                   canvas: Optional[Calibration] = None,
                                   expected_cell: int = 75,
                                   area_tol: float = 0.6) -> Optional[Calibration]:
    """自动检测色板布局。

    思路：38 色都有精确 RGB，用 inRange 精确匹配每个颜色 → 取连通域质心，
    然后按 X 聚类列、按 Y 聚类行，得到列宽/行高/起点。

    Args:
        img: BGR 截图
        canvas: 画布检测结果（用其右侧边界排除画布区域）
        expected_cell: 期望色块尺寸（像素），用于面积过滤
        area_tol: 面积容忍比例（实际面积 >= expected_cell²*area_tol 才算命中）

    Returns: 可用的 Calibration（仅色板参数）或 None
    """
    try:
        import cv2
    except ImportError:
        return None

    h, w = img.shape[:2]
    # 用色板包围盒先验限定搜索区域（排除顶部导航区 y<150）
    pal_box = _find_palette_box(img)
    if pal_box is not None:
        zone_x0 = max(0, pal_box[0] - 30)
        zone_y0 = max(0, pal_box[1] - 30)
    elif canvas is not None and canvas.is_canvas_valid():
        zone_x0 = min(w - 1, canvas.canvas_origin[0]
                      + 24 * canvas.canvas_cell_w + 60)
        zone_y0 = 0
    else:
        zone_x0 = int(w * 0.5)
        zone_y0 = 150   # 排除顶部导航区
    if zone_x0 >= w:
        return None
    right_zone = img[zone_y0:, zone_x0:]

    # 用"高饱和色块"检测（不依赖官方颜色精确匹配，更鲁棒）
    # 色块是 75×75 方形彩色块，饱和度较高
    hsv = cv2.cvtColor(right_zone, cv2.COLOR_RGB2HSV)
    sat = ((hsv[:, :, 1] > 50) & (hsv[:, :, 2] > 40)).astype(np.uint8) * 255
    sat = cv2.morphologyEx(sat, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(sat, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    hits = []   # (cx, cy)
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        if 40 < bw < 100 and 40 < bh < 100:   # 色块约 75px
            a = cv2.contourArea(c)
            if a > 1500:
                M = cv2.moments(c)
                if M["m00"] > 0:
                    cx = zone_x0 + M["m10"] / M["m00"]
                    cy = zone_y0 + M["m01"] / M["m00"]
                    hits.append((cx, cy))

    if len(hits) < 6:
        return None

    # ---- 过滤"当前选中"大色块 ----
    # 色板顶部第 0 列常有"当前选中"高亮大色块（尺寸/面积明显大于其它色块），
    # 它的中心 Y 可能与小色块不同，会污染 palette_top。这里把面积显著大于
    # 中位数且位于最左列的色块过滤掉。
    try:
        cnts2, _ = cv2.findContours(sat, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        rect_areas = []
        for c in cnts2:
            x, y, bw, bh = cv2.boundingRect(c)
            if 40 < bw < 100 and 40 < bh < 100:
                rect_areas.append((cv2.boundingRect(c), cv2.contourArea(c)))
        if rect_areas:
            areas_only = [a[1] for a in rect_areas]
            med_area = float(np.median(areas_only))
            # 找出面积 > 1.25 倍中位数且 X 在最左（X 最小）的 bbox → 当前选中大色块
            big_left = [a[0] for a in rect_areas
                        if a[1] > med_area * 1.25 and a[0][0] == min(r[0][0] for r in rect_areas)]
            if big_left:
                bx, by, bw, bh = big_left[0]
                big_cx = bx + bw / 2
                big_cy = by + bh / 2
                # 把所有落在该大色块中心 ±40px 范围内的 hits 都过滤掉
                hits = [(cx, cy) for (cx, cy) in hits
                        if abs(cx - big_cx) > 40 or abs(cy - big_cy) > 40]
                if len(hits) < 6:
                    return None
    except Exception:
        pass

    xs = sorted(h[0] for h in hits)
    cols = [xs[0]]
    d = np.diff(xs)
    threshold = max(10, float(np.median(d)) * 1.5) if len(d) else 10
    for v in xs[1:]:
        if v - cols[-1] > threshold:
            cols.append(v)
    if len(cols) < 2:
        return None
    col_gap = round(float(np.median(np.diff(cols))))

    ys = sorted(h[1] for h in hits)
    rows = [ys[0]]
    d = np.diff(ys)
    threshold = max(10, float(np.median(d)) * 1.5) if len(d) else 10
    for v in ys[1:]:
        if v - rows[-1] > threshold:
            rows.append(v)
    row_gap = round(float(np.median(np.diff(rows)))) if len(rows) > 1 else None
    if row_gap is None or row_gap <= 1:
        return None

    cal = Calibration()
    cal.palette_col_gap = col_gap
    cal.palette_row_gap = row_gap
    cal.palette_cell_w = 75
    cal.palette_cell_h = 75
    # 第 0 列中心 = 最左色块中心的 x；第 0 行中心 = 最上色块中心 y
    # 注意：最上方可能有一个"当前选中色"大色块(88x88)，需过滤
    # 用聚类后每列/每行的最小中心
    cal.palette_left = int(cols[0])
    cal.palette_top = int(rows[0])
    cal.palette_grid_top = int(rows[0])   # palette_top 即网格第 0 行中心
    cal.palette_visible_rows = len(rows)
    cal.ref_w, cal.ref_h = w, h
    return cal


# ===========================================================================
# 像素采样
# ===========================================================================
def sample_color_at(img: np.ndarray, cx: int, cy: int, half: int = 5) -> tuple:
    """在截图 (cx, cy) 周围采样平均 RGB（BGR 输入）"""
    h, w = img.shape[:2]
    x1 = max(0, cx - half)
    x2 = min(w, cx + half)
    y1 = max(0, cy - half)
    y2 = min(h, cy + half)
    patch = img[y1:y2, x1:x2]
    b, g, r = patch.mean(axis=(0, 1))
    return int(r), int(g), int(b)


# ===========================================================================
# 配置读写
# ===========================================================================
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "pixel_painter_config.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(d: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
