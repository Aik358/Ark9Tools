# -*- coding: utf-8 -*-
"""像素化模块：图片 → 24×24 像素矩阵

**完全照搬《巡展像素_v2_官方38色_滤镜.html》的 confirmCrop 算法**：

- Step 1: 超采样块平均（SAMPLE=8，192×192，每格取 8×8 块平均）
- Step 2+3: K-means++ 初始化 + 感知加权迭代（12轮，默认 K=32 色）
- Step 4: 抖动（Floyd-Steinberg / Atkinson / none）映射到 K 色
- Step 5: 输出 RGB 颜色 → 适配到游戏官方 38 色（EXHIBITION_DATA）

感知色差：colorDistRGB = 2*dr² + 4*dg² + 3*db²（绿色权重最高）
"""
from dataclasses import dataclass
from typing import List, Optional
import numpy as np
from PIL import Image

from palette import GAME_PALETTE, GAME_WHITE_INDEX

GRID_SIZE = 24
SAMPLE = 8
DEFAULT_K = 32   # 默认 K-means 聚类数（HTML 默认值）


# ===========================================================================
# 感知色差（与 HTML colorDistRGB 完全一致）
# ===========================================================================
def color_dist_rgb(r1: int, g1: int, b1: int,
                   r2: int, g2: int, b2: int) -> float:
    """CompuPhase 加权 RGB 色差，与公开 PixelPaintHelper 保持一致。"""
    rmean = (r1 + r2) / 2.0
    dr = r1 - r2
    dg = g1 - g2
    db = b1 - b2
    return ((2.0 + rmean / 256.0) * dr * dr
            + 4.0 * dg * dg
            + (2.0 + (255.0 - rmean) / 256.0) * db * db)


# ===========================================================================
# 滤镜（对应 HTML cropContrast / cropBrightness / cropSaturation）
# ===========================================================================
def _apply_css_filter(arr: np.ndarray, contrast: float = 1.0,
                      brightness: float = 1.0, saturation: float = 1.0) -> np.ndarray:
    """对超采样画布应用 CSS 滤镜（与 HTML hiCtx.filter 一致）。

    contrast(c) = (v-128)*c+128, brightness(b) = v*b, saturate(s) = HSV 饱和度缩放
    """
    arr = arr.astype(np.float32)
    if contrast != 1.0:
        arr = (arr - 128.0) * contrast + 128.0
    if brightness != 1.0:
        arr = arr * brightness
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    if saturation != 1.0:
        img = Image.fromarray(arr, "RGB")
        hsv = img.convert("HSV")
        h, s, v = hsv.split()
        s = s.point(lambda p: max(0, min(255, int(p * saturation))))
        arr = np.asarray(Image.merge("HSV", (h, s, v)).convert("RGB"))
    return arr


# ===========================================================================
# Step 1: 超采样块平均
# ===========================================================================
def _sample_bilinear_grid(img: Image.Image, crop_mode: str) -> np.ndarray:
    """公开色块拟合链：24×24 格心双线性采样，透明像素与白底合成。"""
    rgba = np.asarray(img.convert("RGBA"), dtype=np.float32)
    height, width = rgba.shape[:2]
    if crop_mode == "cover":
        side = min(width, height)
        left = (width - side) / 2.0
        top = (height - side) / 2.0
        map_w = map_h = float(side)
    elif crop_mode == "stretch":
        left, top, map_w, map_h = 0.0, 0.0, float(width), float(height)
    else:
        raise ValueError(f"unknown crop mode: {crop_mode}")

    out = np.empty((GRID_SIZE, GRID_SIZE, 3), dtype=np.float32)
    for gy in range(GRID_SIZE):
        sy = top + ((gy + 0.5) / GRID_SIZE) * map_h
        y0 = int(np.floor(sy))
        fy = sy - y0
        for gx in range(GRID_SIZE):
            sx = left + ((gx + 0.5) / GRID_SIZE) * map_w
            x0 = int(np.floor(sx))
            fx = sx - x0
            samples = []
            for yy, wy in ((y0, 1.0 - fy), (y0 + 1, fy)):
                for xx, wx in ((x0, 1.0 - fx), (x0 + 1, fx)):
                    if 0 <= xx < width and 0 <= yy < height:
                        rgb = rgba[yy, xx, :3]
                        alpha = rgba[yy, xx, 3] / 255.0
                        samples.append((rgb * alpha + 255.0 * (1.0 - alpha), wx * wy))
                    else:
                        samples.append((np.array((255.0, 255.0, 255.0)), wx * wy))
            out[gy, gx] = sum(rgb * weight for rgb, weight in samples)
    return out


def _trim_empty_border(img: Image.Image) -> Image.Image:
    """裁除 alpha<16 或近白像素构成的空白边框。"""
    rgba = np.asarray(img.convert("RGBA"), dtype=np.uint8)
    content = ((rgba[:, :, 3] >= 16)
               & ((rgba[:, :, 0] < 250) | (rgba[:, :, 1] < 250) | (rgba[:, :, 2] < 250)))
    ys, xs = np.where(content)
    if len(xs) == 0:
        return img
    return img.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))


def _apply_pixelpaint_filters(arr: np.ndarray, contrast: float,
                              brightness: float, saturation: float) -> np.ndarray:
    """公开色块拟合链：亮度→对比度→亮度权重饱和度。"""
    out = np.clip(arr.astype(np.float32) * brightness, 0, 255)
    out = np.clip((out - 127.5) * contrast + 127.5, 0, 255)
    luma = (0.2126 * out[:, :, 0] + 0.7152 * out[:, :, 1] + 0.0722 * out[:, :, 2])
    out = luma[:, :, None] + (out - luma[:, :, None]) * saturation
    return np.clip(out, 0, 255)


def _block_mean(hi_arr: np.ndarray, alpha: Optional[np.ndarray] = None) -> np.ndarray:
    """从 192×192 超采样画布计算 24×24 每格平均色。

    带透明通道时只统计 alpha > 128 的样本；全透明格视为白色，
    防止 PNG 透明边缘中残留的 RGB 污染轮廓和背景。
    """
    h, w = hi_arr.shape[:2]
    grid_h, grid_w = h // SAMPLE, w // SAMPLE
    raw = np.zeros((grid_h, grid_w, 3), dtype=np.float32)
    for gy in range(grid_h):
        for gx in range(grid_w):
            block = hi_arr[gy * SAMPLE:(gy + 1) * SAMPLE,
                           gx * SAMPLE:(gx + 1) * SAMPLE].astype(np.float32)
            if alpha is None:
                raw[gy, gx] = block.mean(axis=(0, 1))
                continue
            mask = alpha[gy * SAMPLE:(gy + 1) * SAMPLE,
                         gx * SAMPLE:(gx + 1) * SAMPLE] > 128
            raw[gy, gx] = block[mask].mean(axis=0) if np.any(mask) else (255, 255, 255)
    return raw


# ===========================================================================
# Step 2+3: K-means++ 聚类
# ===========================================================================
def _kmeans_palette(raw: np.ndarray, k: int, max_iter: int = 12) -> np.ndarray:
    """K-means++ 初始化 + 感知加权迭代，返回 K×3 的调色板（BGR/RGB 顺序注意）。

    与 HTML 完全一致：
    - 采样池：非白像素（r<250 || g<250 || b<250）
    - K-means++ 概率初始化
    - 强制锚定黑白
    - 12 轮感知距离迭代
    """
    grid_h, grid_w = raw.shape[:2]
    total = grid_h * grid_w
    # 展平
    raw_flat = raw.reshape(-1, 3)   # (576, 3)

    # 采样池（非白）
    nonwhite = (raw_flat[:, 0] < 250) | (raw_flat[:, 1] < 250) | (raw_flat[:, 2] < 250)
    sample_idx = np.where(nonwhite)[0]
    if len(sample_idx) == 0:
        sample_idx = np.arange(total)
    k = min(k, len(sample_idx))

    # K-means++ 初始化：由原始网格内容派生种子，重复转换完全一致。
    seed = int(np.rint(raw_flat).astype(np.uint64).sum() % np.uint64(2 ** 32 - 1))
    rng = np.random.default_rng(seed)
    centers = []
    first = sample_idx[int(rng.integers(len(sample_idx)))]
    centers.append(raw_flat[first].copy())

    for kk in range(1, k):
        # 每个样本到最近中心的距离
        best_d = np.full(total, np.inf, dtype=np.float64)
        for c in centers:
            d = (2 * (raw_flat[:, 0] - c[0]) ** 2 +
                 4 * (raw_flat[:, 1] - c[1]) ** 2 +
                 3 * (raw_flat[:, 2] - c[2]) ** 2)
            best_d = np.minimum(best_d, d)
        dist_sum = best_d[sample_idx].sum()
        threshold = float(rng.random() * dist_sum)
        accum = 0.0
        picked = sample_idx[0]
        for i in sample_idx:
            accum += best_d[i]
            if accum >= threshold:
                picked = i
                break
        centers.append(raw_flat[picked].copy())

    centers = np.array(centers)   # (K,3)

    # 强制锚定黑白
    best_white = 1
    best_black = 1
    for i in range(1, len(centers)):
        if color_dist_rgb(centers[i][0], centers[i][1], centers[i][2], 255, 255, 255) < \
           color_dist_rgb(centers[best_white][0], centers[best_white][1], centers[best_white][2], 255, 255, 255):
            best_white = i
        if color_dist_rgb(centers[i][0], centers[i][1], centers[i][2], 0, 0, 0) < \
           color_dist_rgb(centers[best_black][0], centers[best_black][1], centers[best_black][2], 0, 0, 0):
            best_black = i
    centers[best_white] = [255, 255, 255]
    centers[best_black] = [0, 0, 0]

    # 12 轮迭代
    for _ in range(max_iter):
        # 分配
        best_k = np.zeros(total, dtype=int)
        best_d = np.full(total, np.inf)
        for kk in range(k):
            d = (2 * (raw_flat[:, 0] - centers[kk][0]) ** 2 +
                 4 * (raw_flat[:, 1] - centers[kk][1]) ** 2 +
                 3 * (raw_flat[:, 2] - centers[kk][2]) ** 2)
            better = d < best_d
            best_d[better] = d[better]
            best_k[better] = kk
        # 更新中心
        for kk in range(k):
            mask = best_k == kk
            if mask.sum() > 0:
                centers[kk] = raw_flat[mask].mean(axis=0).round()

    return centers


# ===========================================================================
# Step 4: 抖动映射
# ===========================================================================
def _nearest_color_idx(r, g, b, palette) -> int:
    """感知距离最近色索引"""
    best, best_d = 0, float("inf")
    for i, c in enumerate(palette):
        d = color_dist_rgb(r, g, b, c[0], c[1], c[2])
        if d < best_d:
            best_d, best = d, i
    return best


def _dither_map(raw: np.ndarray, palette: np.ndarray, mode: str = "fs") -> np.ndarray:
    """直接量化或按公开规则扩散误差；每次写入邻格后立即截断。"""
    grid_h, grid_w = raw.shape[:2]
    work = raw.astype(np.float32).copy()
    out_idx = np.zeros((grid_h, grid_w), dtype=int)

    def add_error(x: int, y: int, error: np.ndarray, weight: float):
        if 0 <= x < grid_w and 0 <= y < grid_h:
            work[y, x] = np.clip(work[y, x] + error * weight, 0, 255)

    for y in range(grid_h):
        for x in range(grid_w):
            old = np.clip(work[y, x], 0, 255)
            pi = _nearest_color_idx(old[0], old[1], old[2], palette)
            out_idx[y, x] = pi
            error = old - palette[pi]
            if mode == "fs":
                add_error(x + 1, y, error, 7 / 16)
                add_error(x - 1, y + 1, error, 3 / 16)
                add_error(x, y + 1, error, 5 / 16)
                add_error(x + 1, y + 1, error, 1 / 16)
            elif mode == "atkinson":
                for dx, dy in ((1, 0), (2, 0), (-1, 1), (0, 1), (1, 1), (0, 2)):
                    add_error(x + dx, y + dy, error, 1 / 8)
            elif mode != "none":
                raise ValueError(f"unknown dither mode: {mode}")

    return out_idx


# ===========================================================================
# Step 5: 聚类色索引 → 游戏实际 40 色
# ===========================================================================
def _palette_to_game(palette: np.ndarray) -> np.ndarray:
    """将每个聚类中心直接映射到游戏可点击的物理40色。"""
    idx_map = np.zeros(len(palette), dtype=int)
    for i, c in enumerate(palette):
        idx_map[i] = _nearest_color_idx(c[0], c[1], c[2], GAME_PALETTE)
    return idx_map


def _balanced_raw(raw: np.ndarray) -> np.ndarray:
    """轻量校正量化前的整体暖偏，保留图片自身的色彩关系。

    只在明亮、低饱和的近中性色中估计白平衡；没有可靠中性色时不处理，
    因此不会把刻意的暖色插画强行调冷。
    """
    flat = raw.reshape(-1, 3)
    spread = flat.max(axis=1) - flat.min(axis=1)
    neutral = flat[(flat.mean(axis=1) > 150) & (spread < 42)]
    if len(neutral) < 8:
        return raw
    reference = neutral.mean(axis=0)
    target = float(reference.mean())
    gain = np.clip(target / np.maximum(reference, 1.0), 0.94, 1.06)
    return np.clip(raw * gain, 0, 255)


def _raw_to_game(raw: np.ndarray, neutral_balance: bool = True) -> np.ndarray:
    """丰富色彩模式：逐格独立映射到游戏物理40色。"""
    source = _balanced_raw(raw) if neutral_balance else raw
    out = np.empty(source.shape[:2], dtype=int)
    for y in range(source.shape[0]):
        for x in range(source.shape[1]):
            r, g, b = source[y, x]
            out[y, x] = _nearest_color_idx(int(r), int(g), int(b), GAME_PALETTE)
    return out


def _blueprint_canvas_box(rgb: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """定位官方蓝图的青色方形画布边框，返回内部画布区域。"""
    mask = ((rgb[:, :, 0] < 120)
            & (rgb[:, :, 1] > 150)
            & (rgb[:, :, 2] > 150)
            & (rgb[:, :, 1] > rgb[:, :, 0] * 1.35))
    try:
        import cv2
        contours, _ = cv2.findContours(
            mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        height, width = mask.shape
        candidates = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w < 180 or h < 180 or w * h < width * height * 0.025:
                continue
            ratio = w / max(1.0, h)
            if not 0.72 <= ratio <= 1.28:
                continue
            candidates.append((w * h, x, y, w, h))
        if candidates:
            _, x, y, w, h = max(candidates)
            inset_x = max(2, round(w * 0.008))
            inset_y = max(2, round(h * 0.008))
            return x + inset_x, y + inset_y, x + w - inset_x, y + h - inset_y
    except ImportError:
        pass

    # 无 OpenCV 时使用高密度青色行列的最大近方形区域作为降级识别。
    row_score = mask.mean(axis=1)
    col_score = mask.mean(axis=0)
    rows = np.where(row_score > 0.45)[0]
    cols = np.where(col_score > 0.45)[0]
    if len(rows) and len(cols):
        x0, x1, y0, y1 = int(cols.min()), int(cols.max()), int(rows.min()), int(rows.max())
        if min(x1 - x0, y1 - y0) >= 180:
            return x0 + 3, y0 + 3, x1 - 3, y1 - 3
    return None


def _regular_grid_canvas_box(rgb: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """从截图中寻找连续等间距的24×24网格，不依赖官方边框和色表。"""
    try:
        import cv2
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 40, 130)
        height, width = edges.shape

        def clusters(values):
            if len(values) == 0:
                return []
            groups = [[int(values[0])]]
            for value in values[1:]:
                if int(value) - groups[-1][-1] <= 3:
                    groups[-1].append(int(value))
                else:
                    groups.append([int(value)])
            return [float(np.median(group)) for group in groups]

        def find_run(values):
            if len(values) < 25:
                return None
            for start in range(len(values) - 24):
                run = np.asarray(values[start:start + 25], dtype=float)
                gaps = np.diff(run)
                step = float(np.median(gaps))
                if step < 5 or step > min(width, height) / 8:
                    continue
                if float(np.std(gaps) / step) <= 0.12:
                    return run[0], run[-1]
            return None

        # 规则网格线在对应整列/整行有高边缘响应，比短线霍夫变换稳定。
        vertical = clusters(np.where(edges.sum(axis=0) >= height * 255 * 0.22)[0])
        horizontal = clusters(np.where(edges.sum(axis=1) >= width * 255 * 0.22)[0])
        x_run, y_run = find_run(vertical), find_run(horizontal)
        if x_run is None or y_run is None:
            return None
        left, right = x_run
        top, bottom = y_run
        ratio = (right - left) / max(1.0, bottom - top)
        if min(right - left, bottom - top) < 180 or not 0.72 <= ratio <= 1.28:
            return None
        return round(left + 1), round(top + 1), round(right - 1), round(bottom - 1)
    except ImportError:
        return None


def _official_blueprint_box(rgb: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """用官方分享页的青色直线边框定位画布，避免把整页青色背景当成画布。"""
    try:
        import cv2
        mask = (((rgb[:, :, 0] < 120) & (rgb[:, :, 1] > 150) & (rgb[:, :, 2] > 150)
                 & (rgb[:, :, 1] > rgb[:, :, 0] * 1.35)).astype(np.uint8) * 255)
        lines = cv2.HoughLinesP(cv2.Canny(mask, 40, 130), 1, np.pi / 180,
                                threshold=55, minLineLength=max(100, min(rgb.shape[:2]) // 7), maxLineGap=18)
        if lines is None:
            return None
        vertical, horizontal = [], []
        for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
            if abs(x2 - x1) <= 5 and abs(y2 - y1) >= 100:
                vertical.append((round((x1 + x2) / 2), min(y1, y2), max(y1, y2)))
            if abs(y2 - y1) <= 5 and abs(x2 - x1) >= 100:
                horizontal.append((round((y1 + y2) / 2), min(x1, x2), max(x1, x2)))
        best = None
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        edge_map = cv2.Canny(gray, 35, 110)

        def grid_score(left, top, right, bottom):
            """真画布的24等分位置会持续出现网格边缘，装饰外框不会。"""
            crop = edge_map[top:bottom + 1, left:right + 1]
            if crop.shape[0] < 120 or crop.shape[1] < 120:
                return 0.0
            baseline = float(crop.mean()) + 1.0
            x_hits, y_hits = [], []
            for n in range(1, 24):
                x = round(n * (crop.shape[1] - 1) / 24)
                y = round(n * (crop.shape[0] - 1) / 24)
                x_hits.append(float(crop[:, max(0, x - 1):x + 2].mean()) / baseline)
                y_hits.append(float(crop[max(0, y - 1):y + 2, :].mean()) / baseline)
            return float(np.mean(sorted(x_hits)[-16:]) + np.mean(sorted(y_hits)[-16:]))

        for lx, ly1, ly2 in vertical:
            for rx, ry1, ry2 in vertical:
                side = abs(rx - lx)
                if not 140 <= side <= min(rgb.shape[:2]) * 0.92:
                    continue
                for ty, tx1, tx2 in horizontal:
                    for by, bx1, bx2 in horizontal:
                        height = abs(by - ty)
                        if not 0.82 <= side / max(1, height) <= 1.18:
                            continue
                        left, right, top, bottom = min(lx, rx), max(lx, rx), min(ty, by), max(ty, by)
                        # 四条边必须覆盖对应边长的大部分，避免标题条/页面边框干扰。
                        if (min(ly2, ry2) - max(ly1, ry1) < side * 0.65
                                or min(tx2, bx2) - max(tx1, bx1) < height * 0.65):
                            continue
                        score = grid_score(left, top, right, bottom)
                        if best is None or score > best[0]:
                            best = (score, left, top, right, bottom)
        if best is not None:
            _, left, top, right, bottom = best
            inset = max(2, round((right - left) * 0.008))
            return left + inset, top + inset, right - inset, bottom - inset
    except ImportError:
        pass
    return None


def _editor_screenshot_box(rgb: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """识别游戏编辑器的中央近方形画布；要求右侧存在深色调色板面板作为锚点。"""
    height, width = rgb.shape[:2]
    if width < 700 or width / max(1, height) < 1.25:
        return None
    # 真实编辑器右侧调色板为深色竖面板，排除普通横向宣传图。
    panel = rgb[int(height * 0.12):int(height * 0.94), int(width * 0.70):int(width * 0.99)]
    if panel.size == 0 or (panel.mean(axis=2) < 80).mean() < 0.20:
        return None
    try:
        import cv2
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 35, 120)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=70,
                                minLineLength=max(180, width // 5), maxLineGap=12)
        if lines is None:
            return None
        vertical, horizontal = [], []
        for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
            if abs(x2 - x1) <= 6 and abs(y2 - y1) >= height * 0.28:
                vertical.append(round((x1 + x2) / 2))
            elif abs(y2 - y1) <= 6 and abs(x2 - x1) >= width * 0.28:
                horizontal.append(round((y1 + y2) / 2))
        best = None
        for left in vertical:
            for right in vertical:
                side = abs(right - left)
                if not width * 0.30 <= side <= width * 0.55:
                    continue
                for top in horizontal:
                    for bottom in horizontal:
                        h = abs(bottom - top)
                        if not 0.85 <= side / max(1, h) <= 1.15:
                            continue
                        x, y = min(left, right), min(top, bottom)
                        # 中央画布应位于左侧导航与右侧调色板之间。
                        if not width * 0.18 <= x <= width * 0.32 or x + side > width * 0.72:
                            continue
                        score = side
                        if best is None or score > best[0]:
                            best = (score, x, y, x + side, y + h)
        return None if best is None else tuple(best[1:])
    except ImportError:
        return None


def detect_24x24_box(image_path: str) -> Optional[tuple[int, int, int, int]]:
    """返回保守的24×24候选区域；未通过严格锚点校验时交给用户手动框选。"""
    rgb = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.uint8)
    candidates = (_official_blueprint_box(rgb), _editor_screenshot_box(rgb), _regular_grid_canvas_box(rgb))
    for box in candidates:
        if box is None:
            continue
        left, top, right, bottom = box
        width, height = rgb.shape[1], rgb.shape[0]
        # 永不接受接近整张宣传页的候选，避免青色背景误框。
        if (right - left) * (bottom - top) < width * height * 0.72:
            return box
    return None


def parse_blueprint_region(image_path: str,
                           box: tuple[int, int, int, int]) -> np.ndarray:
    """从用户确认的区域读取24×24游戏色板矩阵。"""
    rgb = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.uint8)
    left, top, right, bottom = map(int, box)
    left, top = max(0, left), max(0, top)
    right, bottom = min(rgb.shape[1], right), min(rgb.shape[0], bottom)
    if right - left < 24 or bottom - top < 24:
        raise ValueError("选区过小，无法读取24×24网格")
    samples = np.empty((GRID_SIZE, GRID_SIZE, 3), dtype=np.float32)
    for y in range(GRID_SIZE):
        cy = top + (y + 0.5) * (bottom - top) / GRID_SIZE
        for x in range(GRID_SIZE):
            cx = left + (x + 0.5) * (right - left) / GRID_SIZE
            radius_x = max(1, round((right - left) / GRID_SIZE * 0.22))
            radius_y = max(1, round((bottom - top) / GRID_SIZE * 0.22))
            x0, x1 = max(0, round(cx) - radius_x), min(rgb.shape[1], round(cx) + radius_x + 1)
            y0, y1 = max(0, round(cy) - radius_y), min(rgb.shape[0], round(cy) + radius_y + 1)
            samples[y, x] = np.median(rgb[y0:y1, x0:x1].reshape(-1, 3), axis=0)
    return _raw_to_game(samples, neutral_balance=False)


def _matrix_from_rgb_region(rgb: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """从内存 RGB 图像区域按格心采样到游戏色板矩阵。"""
    left, top, right, bottom = map(int, box)
    region = rgb[top:bottom, left:right]
    if region.shape[0] < 24 or region.shape[1] < 24:
        raise ValueError("收藏卡片图区域过小")
    samples = np.empty((GRID_SIZE, GRID_SIZE, 3), dtype=np.float32)
    for y in range(GRID_SIZE):
        y0 = round(y * region.shape[0] / GRID_SIZE)
        y1 = round((y + 1) * region.shape[0] / GRID_SIZE)
        for x in range(GRID_SIZE):
            x0 = round(x * region.shape[1] / GRID_SIZE)
            x1 = round((x + 1) * region.shape[1] / GRID_SIZE)
            cell = region[y0:y1, x0:x1]
            samples[y, x] = np.median(cell.reshape(-1, 3), axis=0)
    return _raw_to_game(samples, neutral_balance=False)


# ===========================================================================
# 游戏画像收藏页识别（两层检测）
#
# 第一层：检测收藏卡片网格（卡片是页面上的浅色/白色带边框矩形）。
# 第二层：在每张卡片内部定位真正的方形像素画区域，排除标题、日期、
#         作者、按钮等文字/控件区域。
# 对每个候选区域评分后取卡片内得分最高者，而不是把整张卡片当像素画。
# ===========================================================================
@dataclass
class CollectionCardResult:
    """一张收藏卡片的解析结果。"""
    matrix: np.ndarray
    name: str
    confidence: float          # 0~1，区域评分
    low_confidence: bool       # 置信度过低，需要用户确认
    box: tuple[int, int, int, int]   # 像素画区域（rgb 坐标）


def _to_rgb(frame: np.ndarray, bgr: Optional[bool] = None) -> Optional[np.ndarray]:
    """统一输入为 RGB uint8。bgr=None 时用通道均值启发式判断。"""
    image = np.asarray(frame)
    if image.ndim != 3 or image.shape[2] < 3:
        return None
    data = image[:, :, :3]
    if bgr is True:
        return data[:, :, ::-1].copy()
    if bgr is False:
        return data.copy()
    # 启发式：BGR 截图中蓝/红通道均值差异通常明显
    if abs(float(data[:, :, 0].mean()) - float(data[:, :, 2].mean())) > 8:
        return data[:, :, ::-1].copy()
    return data.copy()


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / max(1.0, union)


def _cluster_card_grid(boxes: list, image_w: int, image_h: int) -> list[tuple]:
    """把候选卡片框聚类成网格，返回 (x0, y0, x1, y1) 卡片列表。

    - 尺寸一致性：剔除明显偏离中位尺寸的噪声块
    - 行聚类：y 中心接近的框归为同一行，允许顶部/底部半卡
    """
    if not boxes:
        return []
    sides = [min(b[2], b[3]) for b in boxes]
    med = float(np.median(sides)) if sides else 0
    if med < 1:
        return []
    boxes = [b for b in boxes if 0.45 <= min(b[2], b[3]) / med <= 2.2]
    if not boxes:
        return []
    boxes.sort(key=lambda b: b[1] + b[3] / 2)
    rows: list[list] = []
    current = [boxes[0]]
    cur_cy = boxes[0][1] + boxes[0][3] / 2
    for b in boxes[1:]:
        cy = b[1] + b[3] / 2
        if abs(cy - cur_cy) <= max(med * 0.55, 40):
            current.append(b)
            cur_cy = (cur_cy * (len(current) - 1) + cy) / len(current)
        else:
            rows.append(current)
            current = [b]
            cur_cy = cy
    rows.append(current)
    cards = []
    for row in rows:
        row.sort(key=lambda b: b[0])
        row_med_w = float(np.median([b[2] for b in row]))
        row_med_h = float(np.median([b[3] for b in row]))
        # 行内 top 校正：同一行卡片的顶边应一致（允许顶部/底部半卡）。
        # 若某张卡顶部明显更高，说明被上方标题/文字污染，校正到行中位顶边。
        row_med_top = float(np.median([b[1] for b in row]))
        for b in row:
            if abs(b[2] - row_med_w) > row_med_w * 0.55 or abs(b[3] - row_med_h) > row_med_h * 0.55:
                continue
            x0, y0 = b[0], b[1]
            x1, y1 = b[0] + b[2], b[1] + b[3]
            if y0 < row_med_top - max(12, row_med_h * 0.10):
                y0 = round(row_med_top)
            cards.append((x0, y0, x1, y1))
    # 半卡过滤：滚动停止位置常停在卡片中间，导致顶部/底部"半张卡片"
    # 被识别成新卡片。每页至少会露出一张完整卡，用它作为标准卡高参考；
    # 高度明显不足（<78%）的视为半卡丢弃，避免双页扫时同一张卡被截成
    # 两个半张重复入库。
    if cards:
        heights = sorted((c[3] - c[1]) for c in cards)
        # 用 75 分位作为“完整卡高”参考：多数卡完整时不会受单张半卡干扰。
        ref_h = float(np.percentile(heights, 75))
        if ref_h > 30:
            full_min = ref_h * 0.78
            cards = [c for c in cards if (c[3] - c[1]) >= full_min]
    # 限制数量，避免把整页噪声当卡片
    return cards[:40]


def _trim_light_strips(light: np.ndarray, x: int, y: int, cw: int, ch: int,
                       min_side: int):
    """裁掉与卡片连通的文字/噪声条带（顶/底/左/右）。

    标题、按钮或页面栏位是“浅色像素占比明显低于卡片本体”的行/列，
    会与卡片框连通。返回裁切后的 (x, y, cw, ch) 或 None（尺寸不足）。
    """
    block = light[y:y + ch, x:x + cw]
    if ch < 8 or cw < 8:
        return (x, y, cw, ch) if ch >= min_side and cw >= min_side else None
    row_ratio = block.mean(axis=1) / 255.0
    col_ratio = block.mean(axis=0) / 255.0
    r_mid = row_ratio[int(len(row_ratio) * 0.3):int(len(row_ratio) * 0.8)]
    c_mid = col_ratio[int(len(col_ratio) * 0.3):int(len(col_ratio) * 0.8)]
    r_ratio = float(np.median(r_mid)) if len(r_mid) else 1.0
    c_ratio = float(np.median(c_mid)) if len(c_mid) else 1.0
    if r_ratio > 0.3:
        top_cut = 0
        while top_cut < ch * 0.28 and row_ratio[top_cut] < r_ratio * 0.45:
            top_cut += 1
        bottom_cut = 0
        while bottom_cut < ch * 0.28 and row_ratio[ch - 1 - bottom_cut] < r_ratio * 0.45:
            bottom_cut += 1
        y += top_cut
        ch -= top_cut + bottom_cut
    if c_ratio > 0.3:
        left_cut = 0
        while left_cut < cw * 0.28 and col_ratio[left_cut] < c_ratio * 0.45:
            left_cut += 1
        right_cut = 0
        while right_cut < cw * 0.28 and col_ratio[cw - 1 - right_cut] < c_ratio * 0.45:
            right_cut += 1
        x += left_cut
        cw -= left_cut + right_cut
    if ch < min_side or cw < min_side:
        return None
    return x, y, cw, ch


def _detect_card_grid(rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """第一层：检测收藏卡片网格。

    卡片是页面上的浅色/白色带边框矩形；用「浅色连通块 + 边缘闭运算轮廓」
    两种信号取并集，统一裁掉文字/噪声条带，再做 NMS 与网格聚类。
    绝不只依赖单个最大轮廓。
    """
    height, width = rgb.shape[:2]
    if width < 120 or height < 120:
        return []
    try:
        import cv2
    except ImportError:
        return []
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    min_side = max(60, round(min(width, height) * 0.13))
    max_area = width * height * 0.55
    boxes: list = []

    # 信号 A：浅色卡片背景连通块
    # 游戏窗口背景本身就是浅色（白色/浅灰渐变），阈值 185 会把整张图
    # 合成一个超大连通块，无法分离出单张卡片。改用更严的 240，
    # 让真正接近白色的卡片本体与背景拉开差距。
    light = (gray > 240).astype(np.uint8) * 255
    ksize = max(9, round(min(width, height) * 0.025))
    light = cv2.morphologyEx(light, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (ksize, ksize)))
    num, _, stats, _ = cv2.connectedComponentsWithStats(light, connectivity=8)
    for i in range(1, num):
        x, y, cw, ch, area = stats[i]
        if cw < min_side or ch < min_side:
            continue
        if cw * ch > max_area or area < cw * ch * 0.4:
            continue
        ratio = cw / max(1, ch)
        if not 0.5 <= ratio <= 2.6 or y < height * 0.03:
            continue
        trimmed = _trim_light_strips(light, x, y, cw, ch, min_side)
        if trimmed is None:
            continue
        x, y, cw, ch = trimmed
        area = cw * ch
        boxes.append([int(x), int(y), int(cw), int(ch), int(area)])

    # 信号 B：边缘闭运算轮廓（边框清晰的卡片），同样裁掉文字条带
    edges = cv2.Canny(gray, 40, 120)
    ksize_b = max(7, round(min(width, height) * 0.02))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize_b, ksize_b)))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw < min_side or ch < min_side or cw * ch > max_area:
            continue
        ratio = cw / max(1, ch)
        if not 0.5 <= ratio <= 2.6 or y < height * 0.03:
            continue
        area = float(cv2.contourArea(c))
        if area < cw * ch * 0.35:
            continue
        trimmed = _trim_light_strips(light, x, y, cw, ch, min_side)
        if trimmed is None:
            continue
        x, y, cw, ch = trimmed
        boxes.append([int(x), int(y), int(cw), int(ch), int(cw * ch)])

    # NMS：重叠保留面积大者
    boxes.sort(key=lambda b: b[4], reverse=True)
    kept: list = []
    for b in boxes:
        if any(_iou(b, k) > 0.5 for k in kept):
            continue
        kept.append(b)
    return _cluster_card_grid(kept, width, height)


def _snap_square(box: tuple, cw: int, ch: int) -> tuple[int, int, int, int]:
    """以中心扩成正方形并夹在 (0,0)-(cw,ch) 内。"""
    x0, y0, x1, y1 = map(int, box)
    side = max(x1 - x0, y1 - y0, 24)
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    nx0 = max(0, min(cw - side, round(cx - side / 2)))
    ny0 = max(0, min(ch - side, round(cy - side / 2)))
    return nx0, ny0, nx0 + side, ny0 + side


def _grid_box_scoped(crop: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """在卡片局部寻找 24 等分规则网格，返回裁剪坐标。"""
    try:
        import cv2
    except ImportError:
        return None
    h, w = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 40, 130)

    def clusters(values):
        if len(values) == 0:
            return []
        groups = [[int(values[0])]]
        for value in values[1:]:
            if int(value) - groups[-1][-1] <= 3:
                groups[-1].append(int(value))
            else:
                groups.append([int(value)])
        return [float(np.median(g)) for g in groups]

    def find_run(values):
        if len(values) < 25:
            return None
        for start in range(len(values) - 24):
            run = np.asarray(values[start:start + 25], dtype=float)
            gaps = np.diff(run)
            step = float(np.median(gaps))
            if step < 4 or step > max(40, min(w, h) / 6):
                continue
            if float(np.std(gaps) / step) <= 0.15:
                return run[0], run[-1]
        return None

    vertical = clusters(np.where(edges.sum(axis=0) >= h * 255 * 0.16)[0])
    horizontal = clusters(np.where(edges.sum(axis=1) >= w * 255 * 0.16)[0])
    x_run, y_run = find_run(vertical), find_run(horizontal)
    if x_run is None or y_run is None:
        return None
    left, right = x_run
    top, bottom = y_run
    if min(right - left, bottom - top) < max(28, min(w, h) * 0.3):
        return None
    ratio = (right - left) / max(1.0, bottom - top)
    if not 0.7 <= ratio <= 1.3:
        return None
    return round(left), round(top), round(right), round(bottom)


def _square_border_boxes(crop: np.ndarray, cw: int, ch: int) -> list[tuple]:
    """在卡片局部找带强边框的近方形区域。"""
    try:
        import cv2
    except ImportError:
        return []
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    edge = cv2.Canny(gray, 35, 120)
    contours, _ = cv2.findContours(edge, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    min_side = max(32, round(min(cw, ch) * 0.2))
    max_side = min(cw, ch)
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < min_side or h < min_side or max(w, h) > max_side:
            continue
        if not 0.75 <= w / max(1, h) <= 1.3:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        if len(approx) not in (4, 5, 6, 8):
            continue
        out.append(_snap_square((x, y, x + w, y + h), cw, ch))
    return out


def _content_bounding_box(crop: np.ndarray, cw: int, ch: int) -> Optional[tuple]:
    """内容包围盒：像素画颜色与卡片背景显著不同，取差异像素的外接方形。"""
    margin = max(3, round(min(cw, ch) * 0.04))
    corners = np.concatenate([
        crop[:margin].reshape(-1, 3), crop[-margin:].reshape(-1, 3),
        crop[:, :margin].reshape(-1, 3), crop[:, -margin:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    diff = np.abs(crop.astype(np.int16) - bg.astype(np.int16)).sum(axis=2)
    mask = diff > 130
    ys, xs = np.where(mask)
    if len(xs) < 36:
        return None
    box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    return _snap_square(box, cw, ch)


def _middle_squares(cw: int, ch: int) -> list[tuple]:
    """兜底候选：卡片中上部不同比例/位置的方形。

    游戏中像素画位于卡片上半部；cy_ratio 改为 0.30~0.46（原 0.48~0.64
    会落在"按钮/日期"等文字条带，导致误把整张卡片当像素画）。
    """
    out = []
    for scale in (0.50, 0.62, 0.74):
        for cy_ratio in (0.30, 0.38, 0.46):
            side = round(min(cw, ch) * scale)
            cx = cw // 2
            cy = round(ch * cy_ratio)
            x0 = max(0, min(cw - side, cx - side // 2))
            y0 = max(0, min(ch - side, cy - side // 2))
            out.append((x0, y0, x0 + side, y0 + side))
    return out


def _score_art_region(rgb: np.ndarray, box: tuple[int, int, int, int],
                      card: tuple[int, int, int, int]) -> float:
    """对候选像素画区域评分（0~1）。

    依据：24 等分网格线一致性、格内主导色规律（低方差）、相邻同色块结构、
    边框强度、区域在卡片中的位置（避开标题/按钮）、面积占比。
    """
    try:
        import cv2
    except ImportError:
        return 0.5
    x0, y0, x1, y1 = map(int, box)
    if x1 - x0 < 28 or y1 - y0 < 28:
        return 0.0
    crop = rgb[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    edge = cv2.Canny(gray, 30, 110)

    # ---- 边框强度 ----
    border = (float(edge[0, :].mean()) + float(edge[-1, :].mean())
              + float(edge[:, 0].mean()) + float(edge[:, -1].mean())) / 4.0
    border_score = min(1.0, border / 90.0)

    # ---- 24 等分网格线一致性 ----
    line_hits: list[float] = []
    line_miss: list[float] = []
    for n in range(1, 24):
        gx = round(n * (w - 1) / 24)
        gy = round(n * (h - 1) / 24)
        line_hits.append(float(edge[:, max(0, gx - 1):gx + 2].mean())
                         + float(edge[max(0, gy - 1):gy + 2, :].mean()))
        ox = round((n - 0.5) * (w - 1) / 24)
        oy = round((n - 0.5) * (h - 1) / 24)
        line_miss.append(float(edge[:, max(0, ox - 1):ox + 2].mean())
                         + float(edge[max(0, oy - 1):oy + 2, :].mean()))
    grid_score = min(1.0, max(0.0, (float(np.mean(line_hits))
                                    - float(np.mean(line_miss))) / 2.0))

    # ---- 24×24 格内主导色规律（低方差 = 色块）----
    stds: list[float] = []
    for gy in range(24):
        cy0 = round(gy * h / 24)
        cy1 = round((gy + 1) * h / 24)
        for gx in range(24):
            cx0 = round(gx * w / 24)
            cx1 = round((gx + 1) * w / 24)
            cell = crop[cy0:cy1, cx0:cx1].reshape(-1, 3)
            if cell.size == 0:
                continue
            stds.append(float(cell.std(axis=0).mean()))
    cell_score = 1.0 - min(1.0, float(np.mean(stds)) / 70.0) if stds else 0.0

    # ---- 色块结构：24×24 中相邻同色占比（随机噪声几乎为 0）----
    colors = np.empty((24, 24, 3), dtype=np.float32)
    for gy in range(24):
        cy0 = round(gy * h / 24)
        cy1 = round((gy + 1) * h / 24)
        for gx in range(24):
            cx0 = round(gx * w / 24)
            cx1 = round((gx + 1) * w / 24)
            cell = crop[cy0:cy1, cx0:cx1].reshape(-1, 3)
            colors[gy, gx] = cell.mean(axis=0) if cell.size else (255, 255, 255)
    quant = np.round(colors / 16).astype(np.int16)
    same = 0
    total = 0
    for gy in range(24):
        for gx in range(23):
            total += 1
            if np.abs(quant[gy, gx] - quant[gy, gx + 1]).sum() < 3:
                same += 1
    for gx in range(24):
        for gy in range(23):
            total += 1
            if np.abs(quant[gy, gx] - quant[gy + 1, gx]).sum() < 3:
                same += 1
    block_score = max(0.0, min(1.0, (same / max(1, total) - 0.08) / 0.28))

    # ---- 位置：卡片中像素画通常在上半区（上方 25%~55%）。"中部偏下 0.55"
    # 会选到标题/按钮/日期条带，误把整张卡片当像素画（用户实测）。
    # 改成"中部偏上 0.40"，并允许 0.22~0.62 的合理区间。
    card_h = max(1, card[3] - card[1])
    cy_ratio = ((y0 + y1) / 2 - card[1]) / card_h
    pos_score = 1.0 - min(1.0, abs(cy_ratio - 0.40) / 0.22)

    # ---- 边缘文字惩罚：游戏卡片上下常有"作者/按钮/日期"浅色条带，
    # 候选若触及卡片上/下 8% 边带（浅色纯文字行）则减分。
    edge_band = max(2, round(card_h * 0.08))
    top_band = rgb[card[1]:card[1] + edge_band, x0:x1]
    bot_band = rgb[card[3] - edge_band:card[3], x0:x1]
    try:
        top_mid = float(np.median(top_band.reshape(-1, 3), axis=0).std())
        bot_mid = float(np.median(bot_band.reshape(-1, 3), axis=0).std())
        # 候选紧贴上边 → 上边色与边框一致 → 高分；紧贴下边同理。
        near_top = (y0 - card[1]) < edge_band // 2
        near_bot = (card[3] - y1) < edge_band // 2
        # 候选若"进入"了文字条带（甚至超出卡片像素画正常范围）→ 大幅减分
        touching_text = (y0 < card[1] + edge_band // 2) or (y1 > card[3] - edge_band // 2)
        text_penalty = 0.30 if touching_text else 0.0
    except Exception:
        text_penalty = 0.0

    # ---- 面积占比：不能接近整张截图尺寸（占满整张卡片=误框）----
    card_area = max(1, (card[2] - card[0]) * (card[3] - card[1]))
    area_ratio = (x1 - x0) * (y1 - y0) / card_area
    # 占满 ≥88% 整张卡片 → 必然是把标题/按钮一起框进来，大幅减分
    size_score = 1.0 if area_ratio > 1.05 else min(1.0, area_ratio / 0.95)
    if area_ratio >= 0.88:
        text_penalty = max(text_penalty, 0.35)

    return (grid_score * 0.28 + cell_score * 0.22 + block_score * 0.18
            + border_score * 0.10 + pos_score * 0.14 + size_score * 0.08
            - text_penalty)


def _best_art_region(rgb: np.ndarray,
                     card: tuple[int, int, int, int]) -> Optional[tuple[tuple, float]]:
    """第二层：在单张卡片内找得分最高的像素画候选区域。

    返回 (区域完整坐标, 得分)；没有任何候选时返回 None。
    """
    x0, y0, x1, y1 = map(int, card)
    cw, ch = x1 - x0, y1 - y0
    if cw < 60 or ch < 60:
        return None
    crop = rgb[y0:y1, x0:x1]
    candidates: list[tuple] = []
    box = _grid_box_scoped(crop)
    if box is not None:
        candidates.append(box)
    candidates.extend(_square_border_boxes(crop, cw, ch))
    content = _content_bounding_box(crop, cw, ch)
    if content is not None:
        candidates.append(content)
    candidates.extend(_middle_squares(cw, ch))

    best = None
    best_score = -1.0
    for bx0, by0, bx1, by1 in candidates:
        full_box = (x0 + bx0, y0 + by0, x0 + bx1, y0 + by1)
        score = _score_art_region(rgb, full_box, card)
        if score > best_score:
            best, best_score = full_box, score
    return (best, best_score) if best is not None else None


def parse_collection_page_detailed(frame: np.ndarray, bgr: Optional[bool] = None,
                                   min_confidence: float = 0.34,
                                   max_cards: int = 32) -> list[CollectionCardResult]:
    """解析游戏画像收藏页截图，返回卡片内像素画解析结果（带置信度）。

    第一层检测卡片网格，第二层在每张卡片内部找得分最高的方形像素画区域，
    不用整个近方形卡片当像素画。同页按 sha256 指纹去重。
    """
    rgb = _to_rgb(frame, bgr)
    if rgb is None:
        return []
    height, width = rgb.shape[:2]
    # 官方蓝图护栏：官方宣传分享页是整页大范围青色背景 + 中央白底画布，
    # 与收藏卡片页（深色/浅灰背景）明显不同。青色占比高时直接判定为蓝图。
    try:
        import cv2
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.int16)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        cyan = ((hue > 70) & (hue < 110) & (sat > 90) & (val > 80))
        if float(cyan.mean()) > 0.15:
            return []
    except Exception:
        pass
    cards = _detect_card_grid(rgb)
    # 页面级护栏：只有一张且占据页面很大面积时，多半是编辑器画布，
    # 不是收藏卡片网格，不应触发收藏解析。
    if len(cards) == 1:
        x0, y0, x1, y1 = cards[0]
        card_area_ratio = (x1 - x0) * (y1 - y0) / max(1, width * height)
        if card_area_ratio > 0.12:
            cards = []
    results: list[CollectionCardResult] = []
    seen_fp: set[str] = set()
    for card in cards:
        art = _best_art_region(rgb, card)
        if art is None:
            continue
        box, score = art
        # 卡片级护栏：像素画区域几乎占满整张卡片（≥88%）说明这是蓝图/画布
        # 整体，而不是卡片内部真正的像素画，跳过避免误框。
        card_area = max(1, (card[2] - card[0]) * (card[3] - card[1]))
        art_area = (box[2] - box[0]) * (box[3] - box[1])
        if art_area > card_area * 0.88:
            continue
        try:
            matrix = _matrix_from_rgb_region(rgb, box)
        except ValueError:
            continue
        if matrix.size == 0:
            continue
        import hashlib
        fp = hashlib.sha256(matrix.tobytes()).hexdigest()
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        low = float(score) < min_confidence
        results.append(CollectionCardResult(
            matrix=matrix, name=f"画像收藏_{len(results) + 1:02d}",
            confidence=round(float(score), 3), low_confidence=low, box=box))
        if len(results) >= max_cards:
            break
    return results


def parse_collection_page(frame: np.ndarray) -> list[tuple[np.ndarray, str]]:
    """兼容旧调用：只返回 (矩阵, 名称) 列表。"""
    return [(r.matrix, r.name) for r in parse_collection_page_detailed(frame)]


def parse_official_blueprint(image_path: str) -> Optional[np.ndarray]:
    """兼容旧调用：自动检测后直接读取。新界面应使用用户确认的选区。"""
    box = detect_24x24_box(image_path)
    return parse_blueprint_region(image_path, box) if box is not None else None


# ===========================================================================
# 主入口
# ===========================================================================
def pixelate(image_path: str,
             saturation: float = 1.0,
             brightness: float = 1.0,
             contrast: float = 1.0,
             color_count: int = DEFAULT_K,
             dither: str = "fs",
             flatten_white: bool = True,
             crop_mode: str = "cover",
             fit_mode: str = "blocks",
             crop_box: Optional[tuple[int, int, int, int]] = None) -> np.ndarray:
    """将图片转换为 24×24 × 38色索引矩阵（完全照搬 HTML confirmCrop 算法）。

    Args:
        image_path: 输入图片路径
        saturation: 饱和度（100%=1.0）
        brightness: 亮度（100%=1.0）
        contrast: 对比度（100%=1.0）
        color_count: K-means 聚类数（HTML 默认 32）
        dither: 'fs' | 'atkinson' | 'none'（HTML 默认 fs）
        flatten_white: 纯白格（RGB=255）是否作为空（不涂）
        crop_mode: 'cover'（裁切铺满）或 'stretch'（完整压缩）
        fit_mode: 'rich'（丰富色彩）或 'blocks'（色块拟合）
        crop_box: 编辑浮层确认的原图裁剪区域 (left, top, right, bottom)

    Returns:
        np.ndarray: shape=(24,24), dtype=int, 值为 0~39 的物理色板索引
    """
    img = Image.open(image_path).convert("RGBA")
    if crop_box is not None:
        left, top, right, bottom = map(int, crop_box)
        left = max(0, min(left, img.width - 1))
        top = max(0, min(top, img.height - 1))
        right = max(left + 1, min(right, img.width))
        bottom = max(top + 1, min(bottom, img.height))
        img = img.crop((left, top, right, bottom))

    if fit_mode == "blocks":
        # 色块拟合使用公开 PixelPaintHelper 的格心采样处理链。
        raw = _apply_pixelpaint_filters(
            _sample_bilinear_grid(_trim_empty_border(img), crop_mode),
            contrast, brightness, saturation)
    elif fit_mode == "rich":
        if crop_mode == "cover":
            width, height = img.size
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            img = img.crop((left, top, left + side, top + side))
        elif crop_mode != "stretch":
            raise ValueError(f"unknown crop mode: {crop_mode}")

        # 丰富色彩保留超采样块平均，获得更平滑的局部颜色过渡。
        hi_res = GRID_SIZE * SAMPLE
        rgba = np.asarray(img, dtype=np.float32)
        alpha_src = rgba[:, :, 3] / 255.0
        premultiplied = rgba[:, :, :3] * alpha_src[:, :, None]
        pm_img = Image.fromarray(np.clip(premultiplied, 0, 255).astype(np.uint8), "RGB")
        hi_pm = np.asarray(pm_img.resize((hi_res, hi_res), Image.Resampling.LANCZOS), dtype=np.float32)
        hi_alpha = np.asarray(img.getchannel("A").resize((hi_res, hi_res), Image.Resampling.LANCZOS), dtype=np.uint8)
        alpha_norm = hi_alpha.astype(np.float32) / 255.0
        hi_rgb = np.full_like(hi_pm, 255.0)
        visible = alpha_norm > 1e-3
        hi_rgb[visible] = hi_pm[visible] / alpha_norm[visible, None]
        hi_arr = _apply_css_filter(np.clip(hi_rgb, 0, 255).astype(np.uint8),
                                   contrast, brightness, saturation)
        raw = _block_mean(hi_arr, hi_alpha)
    else:
        raise ValueError(f"unknown fit mode: {fit_mode}")

    if fit_mode == "rich":
        # 丰富色彩不使用K-means，但仍支持三种误差扩散模型。
        white_source = _balanced_raw(raw)
        idx_mat = _dither_map(white_source, np.asarray(GAME_PALETTE, dtype=np.float32), dither)
        if flatten_white:
            idx_mat[(white_source[:, :, 0] > 240)
                    & (white_source[:, :, 1] > 240)
                    & (white_source[:, :, 2] > 240)] = GAME_WHITE_INDEX
    elif fit_mode == "blocks":
        # 直接量化固定40色，保留格心高光和细轮廓，不经过K-means收敛。
        idx_mat = _dither_map(raw, np.asarray(GAME_PALETTE, dtype=np.float32), dither)
        if flatten_white:
            idx_mat[(raw[:, :, 0] > 240)
                    & (raw[:, :, 1] > 240)
                    & (raw[:, :, 2] > 240)] = GAME_WHITE_INDEX

    return idx_mat


# ===========================================================================
# 工具函数
# ===========================================================================
def save_visual_preview(idx_mat: np.ndarray, out_path: str, cell: int = 24):
    h, w = idx_mat.shape
    img = Image.new("RGB", (w * cell, h * cell), (255, 255, 255))
    for y in range(h):
        for x in range(w):
            rgb = GAME_PALETTE[idx_mat[y, x]]
            for dy in range(cell):
                for dx in range(cell):
                    img.putpixel((x * cell + dx, y * cell + dy), rgb)
    img.save(out_path)


def count_color_usage(idx_mat: np.ndarray) -> dict:
    flat = idx_mat.flatten()
    counts = np.bincount(flat, minlength=len(GAME_PALETTE))
    order = np.argsort(-counts)
    return {int(idx): int(counts[idx]) for idx in order if counts[idx] > 0}


def paint_order(idx_mat: np.ndarray, strategy: str = "row") -> List[tuple]:
    """生成绘画顺序（同前）"""
    items = [(x, y, int(idx_mat[y, x]))
             for y in range(GRID_SIZE)
             for x in range(GRID_SIZE)]
    items = [it for it in items if it[2] != GAME_WHITE_INDEX]

    if strategy == "row":
        return items
    if strategy in ("row_stroke", "column_stroke"):
        out = []
        if strategy == "row_stroke":
            for y in range(GRID_SIZE):
                for x in range(GRID_SIZE):
                    if idx_mat[y, x] != GAME_WHITE_INDEX:
                        out.append((x, y, int(idx_mat[y, x])))
        else:
            for x in range(GRID_SIZE):
                for y in range(GRID_SIZE):
                    if idx_mat[y, x] != GAME_WHITE_INDEX:
                        out.append((x, y, int(idx_mat[y, x])))
        return out
    if strategy == "column":
        out = []
        for x in range(GRID_SIZE):
            for y in range(GRID_SIZE):
                if idx_mat[y, x] != GAME_WHITE_INDEX:
                    out.append((x, y, int(idx_mat[y, x])))
        return out
    if strategy == "spiral":
        out = []
        seen = [[False] * GRID_SIZE for _ in range(GRID_SIZE)]
        x0, y0, x1, y1 = 0, 0, GRID_SIZE - 1, GRID_SIZE - 1
        while x0 <= x1 and y0 <= y1:
            for x in range(x0, x1 + 1):
                if not seen[y0][x] and idx_mat[y0, x] != GAME_WHITE_INDEX:
                    out.append((x, y0, int(idx_mat[y0, x])))
                    seen[y0][x] = True
            for y in range(y0 + 1, y1 + 1):
                if not seen[y][x1] and idx_mat[y][x1] != GAME_WHITE_INDEX:
                    out.append((x1, y, int(idx_mat[y][x1])))
                    seen[y][x1] = True
            for x in range(x1 - 1, x0 - 1, -1):
                if not seen[y1][x] and idx_mat[y1][x] != GAME_WHITE_INDEX:
                    out.append((x, y1, int(idx_mat[y1][x])))
                    seen[y1][x] = True
            for y in range(y1 - 1, y0, -1):
                if not seen[y][x0] and idx_mat[y][x0] != GAME_WHITE_INDEX:
                    out.append((x0, y, int(idx_mat[y][x0])))
                    seen[y][x0] = True
            x0 += 1; y0 += 1; x1 -= 1; y1 -= 1
        return out
    if strategy == "usage_first":
        groups = {}
        for x, y, c in items:
            groups.setdefault(c, []).append((x, y, c))
        return [item for c in sorted(groups.keys(),
                                     key=lambda k: -len(groups[k]))
                for item in groups[c]]
    raise ValueError(f"unknown strategy: {strategy}")
