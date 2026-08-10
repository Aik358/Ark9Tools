# -*- coding: utf-8 -*-
"""像素化模块：图片 → 24×24 像素矩阵

**完全照搬《巡展像素_v2_官方38色_滤镜.html》的 confirmCrop 算法**：

- Step 1: 超采样块平均（SAMPLE=8，192×192，每格取 8×8 块平均）
- Step 2+3: K-means++ 初始化 + 感知加权迭代（12轮，默认 K=32 色）
- Step 4: 抖动（Floyd-Steinberg / Atkinson / none）映射到 K 色
- Step 5: 输出 RGB 颜色 → 适配到游戏官方 38 色（EXHIBITION_DATA）

感知色差：colorDistRGB = 2*dr² + 4*dg² + 3*db²（绿色权重最高）
"""
from typing import List, Optional
import random
import numpy as np
from PIL import Image

from palette import EXHIBITION_PALETTE, WHITE_PALETTE_INDEX

GRID_SIZE = 24
SAMPLE = 8
DEFAULT_K = 32   # 默认 K-means 聚类数（HTML 默认值）


# ===========================================================================
# 感知色差（与 HTML colorDistRGB 完全一致）
# ===========================================================================
def color_dist_rgb(r1: int, g1: int, b1: int,
                   r2: int, g2: int, b2: int) -> float:
    dr = r1 - r2
    dg = g1 - g2
    db = b1 - b2
    return 2 * dr * dr + 4 * dg * dg + 3 * db * db


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
def _block_mean(hi_arr: np.ndarray) -> np.ndarray:
    """从 192×192 超采样画布计算 24×24 每格平均色（RGB float）。

    HTML: rawR/G/B[idx] = 8×8 子块均值（仅统计 alpha>128 的像素）。
    """
    h, w = hi_arr.shape[:2]
    grid_h, grid_w = h // SAMPLE, w // SAMPLE
    raw = np.zeros((grid_h, grid_w, 3), dtype=np.float32)
    for gy in range(grid_h):
        for gx in range(grid_w):
            block = hi_arr[gy * SAMPLE:(gy + 1) * SAMPLE,
                           gx * SAMPLE:(gx + 1) * SAMPLE].astype(np.float32)
            raw[gy, gx] = block.mean(axis=(0, 1))
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

    # K-means++ 初始化
    centers = []
    first = sample_idx[random.randint(0, len(sample_idx) - 1)]
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
        threshold = random.random() * dist_sum
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
    """抖动映射到调色板，返回 24×24 的调色板索引矩阵。"""
    grid_h, grid_w = raw.shape[:2]
    total = grid_h * grid_w
    raw_flat = raw.reshape(-1, 3)
    err = np.zeros((total, 3), dtype=np.float32)
    out_idx = np.zeros(total, dtype=int)

    for y in range(grid_h):
        for x in range(grid_w):
            idx = y * grid_w + x
            r = min(255, max(0, raw_flat[idx][0] + err[idx][0]))
            g = min(255, max(0, raw_flat[idx][1] + err[idx][1]))
            b = min(255, max(0, raw_flat[idx][2] + err[idx][2]))
            pi = _nearest_color_idx(r, g, b, palette)
            out_idx[idx] = pi
            pr, pg, pb = palette[pi]
            er = r - pr
            eg = g - pg
            eb = b - pb

            if mode == "fs":
                # Floyd-Steinberg
                if x + 1 < grid_w:
                    ri = y * grid_w + (x + 1)
                    err[ri][0] += er * 7 / 16
                    err[ri][1] += eg * 7 / 16
                    err[ri][2] += eb * 7 / 16
                if y + 1 < grid_h:
                    if x - 1 >= 0:
                        bl = (y + 1) * grid_w + (x - 1)
                        err[bl][0] += er * 3 / 16
                        err[bl][1] += eg * 3 / 16
                        err[bl][2] += eb * 3 / 16
                    bd = (y + 1) * grid_w + x
                    err[bd][0] += er * 5 / 16
                    err[bd][1] += eg * 5 / 16
                    err[bd][2] += eb * 5 / 16
                    if x + 1 < grid_w:
                        br = (y + 1) * grid_w + (x + 1)
                        err[br][0] += er * 1 / 16
                        err[br][1] += eg * 1 / 16
                        err[br][2] += eb * 1 / 16
            elif mode == "atkinson":
                er, eg, eb = er / 8, eg / 8, eb / 8
                if x + 1 < grid_w:
                    err[y * grid_w + (x + 1)] += (er, eg, eb)
                if x + 2 < grid_w:
                    err[y * grid_w + (x + 2)] += (er, eg, eb)
                if y + 1 < grid_h:
                    if x - 1 >= 0:
                        err[(y + 1) * grid_w + (x - 1)] += (er, eg, eb)
                    err[(y + 1) * grid_w + x] += (er, eg, eb)
                    if x + 1 < grid_w:
                        err[(y + 1) * grid_w + (x + 1)] += (er, eg, eb)
                if y + 2 < grid_h:
                    err[(y + 2) * grid_w + x] += (er, eg, eb)
            else:
                pass  # none 模式：无抖动

    return out_idx.reshape(grid_h, grid_w)


# ===========================================================================
# Step 5: 调色板索引 → 游戏 38 色
# ===========================================================================
def _palette_to_38(palette: np.ndarray) -> np.ndarray:
    """把 K-means 生成的调色板（RGB）映射到游戏官方 38 色。

    每个聚类中心 → 感知距离最近的一个官方色。返回长度为 K 的色板索引数组。
    """
    idx_map = np.zeros(len(palette), dtype=int)
    for i, c in enumerate(palette):
        best, best_d = 0, float("inf")
        for j, (_, prgb, _) in enumerate(EXHIBITION_PALETTE):
            d = color_dist_rgb(c[0], c[1], c[2], prgb[0], prgb[1], prgb[2])
            if d < best_d:
                best_d, best = d, j
        idx_map[i] = best
    return idx_map


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
             crop_box: Optional[tuple[int, int, int, int]] = None,
             transparent_mode: str = "blank") -> np.ndarray:
    """将图片转换为 24×24 × 38色索引矩阵（完全照搬 HTML confirmCrop 算法）。

    Args:
        image_path: 输入图片路径
        saturation: 饱和度（100%=1.0）
        brightness: 亮度（100%=1.0）
        contrast: 对比度（100%=1.0）
        color_count: K-means 聚类数（HTML 默认 32）
        dither: 'fs' | 'atkinson' | 'none'（HTML 默认 fs）
        flatten_white: 纯白格（RGB=255）是否作为空（不涂）
        crop_box: 可选裁剪区域 (left, top, right, bottom)，坐标基于原图像素
        transparent_mode: 透明区域处理方式，'blank' 留白，'black' 转黑色

    Returns:
        np.ndarray: shape=(24,24), dtype=int, 值为 0~37 的官方色板索引
    """
    if transparent_mode not in {"blank", "black"}:
        raise ValueError("transparent_mode 必须是 'blank' 或 'black'")
    source = Image.open(image_path).convert("RGBA")
    if crop_box is not None:
        left, top, right, bottom = crop_box
        width, height = source.size
        left = max(0, min(int(left), width - 1))
        top = max(0, min(int(top), height - 1))
        right = max(left + 1, min(int(right), width))
        bottom = max(top + 1, min(int(bottom), height))
        source = source.crop((left, top, right, bottom))

    alpha = source.getchannel("A")
    transparency_mask = np.asarray(alpha.resize(
        (GRID_SIZE, GRID_SIZE), Image.Resampling.BOX), dtype=np.uint8) < 128
    if transparent_mode == "black":
        background = Image.new("RGBA", source.size, (0, 0, 0, 255))
        background.alpha_composite(source)
        img = background.convert("RGB")
    else:
        img = Image.new("RGB", source.size, (255, 255, 255))
        img.paste(source.convert("RGB"), mask=alpha)

    # Step 1: 超采样到 192×192（白底），应用滤镜
    hi_res = GRID_SIZE * SAMPLE
    hi_img = img.resize((hi_res, hi_res), Image.Resampling.LANCZOS)
    hi_arr = np.asarray(hi_img, dtype=np.uint8)
    hi_arr = _apply_css_filter(hi_arr, contrast, brightness, saturation)

    # 超采样块平均 → 24×24×3（float）
    raw = _block_mean(hi_arr)

    # Step 2+3: K-means 聚类（感知距离）
    palette = _kmeans_palette(raw, color_count, max_iter=12)

    # Step 4: 抖动映射到 K 色调色板
    k_idx = _dither_map(raw, palette, dither)

    # Step 5: 调色板 → 官方 38 色
    map_38 = _palette_to_38(palette)
    idx_mat = map_38[k_idx]

    # 纯白格映射为 X01(0)（跳过不涂）
    if flatten_white or transparent_mode == "blank":
        if transparent_mode == "blank":
            idx_mat[transparency_mask] = WHITE_PALETTE_INDEX
        # 近似白识别：图片缩放（LANCZOS）+ 滤镜后，白色背景的 K-means 中心
        # 通常是 240~255 的浅灰白，并非精确 255。若只判定"恰好 255"，
        # 绝大多数白色背景会漏检 → 被映射为 X15 奶白等浅色 → 绘画时被涂色。
        # 这里放宽到 RGB 均 > 240。
        white_mask = ((palette[:, 0] > 240)
                      & (palette[:, 1] > 240)
                      & (palette[:, 2] > 240))
        white_k = np.where(white_mask)[0]
        if len(white_k) > 0:
            mask = np.isin(k_idx, white_k)
            idx_mat[mask] = WHITE_PALETTE_INDEX
        else:
            # 兜底：极端情况下（如整体偏暗），选"最接近纯白"的中心作为背景
            dists = (255 - palette).sum(axis=1)
            wk = int(np.argmin(dists))
            idx_mat[k_idx == wk] = WHITE_PALETTE_INDEX

    return idx_mat


# ===========================================================================
# 工具函数
# ===========================================================================
def save_visual_preview(idx_mat: np.ndarray, out_path: str, cell: int = 24):
    h, w = idx_mat.shape
    img = Image.new("RGB", (w * cell, h * cell), (255, 255, 255))
    for y in range(h):
        for x in range(w):
            rgb = EXHIBITION_PALETTE[idx_mat[y, x]][1]
            for dy in range(cell):
                for dx in range(cell):
                    img.putpixel((x * cell + dx, y * cell + dy), rgb)
    img.save(out_path)


def count_color_usage(idx_mat: np.ndarray) -> dict:
    flat = idx_mat.flatten()
    counts = np.bincount(flat, minlength=len(EXHIBITION_PALETTE))
    order = np.argsort(-counts)
    return {int(idx): int(counts[idx]) for idx in order if counts[idx] > 0}


def paint_order(idx_mat: np.ndarray, strategy: str = "row") -> List[tuple]:
    """生成绘画顺序（同前）"""
    items = [(x, y, int(idx_mat[y, x]))
             for y in range(GRID_SIZE)
             for x in range(GRID_SIZE)]
    items = [it for it in items if it[2] != WHITE_PALETTE_INDEX]

    if strategy == "row":
        return items
    if strategy == "column":
        out = []
        for x in range(GRID_SIZE):
            for y in range(GRID_SIZE):
                if idx_mat[y, x] != WHITE_PALETTE_INDEX:
                    out.append((x, y, int(idx_mat[y, x])))
        return out
    if strategy == "spiral":
        out = []
        seen = [[False] * GRID_SIZE for _ in range(GRID_SIZE)]
        x0, y0, x1, y1 = 0, 0, GRID_SIZE - 1, GRID_SIZE - 1
        while x0 <= x1 and y0 <= y1:
            for x in range(x0, x1 + 1):
                if not seen[y0][x] and idx_mat[y0, x] != 0:
                    out.append((x, y0, int(idx_mat[y0, x])))
                    seen[y0][x] = True
            for y in range(y0 + 1, y1 + 1):
                if not seen[y][x1] and idx_mat[y][x1] != 0:
                    out.append((x1, y, int(idx_mat[y][x1])))
                    seen[y][x1] = True
            for x in range(x1 - 1, x0 - 1, -1):
                if not seen[y1][x] and idx_mat[y1][x] != 0:
                    out.append((x, y1, int(idx_mat[y1][x])))
                    seen[y1][x] = True
            for y in range(y1 - 1, y0, -1):
                if not seen[y][x0] and idx_mat[y][x0] != 0:
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
