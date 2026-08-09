# -*- coding: utf-8 -*-
"""纯几何结构检测：不依赖颜色（抗 HDR 偏色），基于相对比例（兼容任意分辨率）

检测内容：
1. 网格线：Sobel 梯度投影 → 等间距峰值序列（画布 24 格）
2. 画布边框：Canny + 长水平/垂直线段（大矩形）
3. 相对位置：画布/色板在窗口中的相对比例 + 四象限
"""
import numpy as np
from PIL import Image


def load(p):
    im = Image.open(p).convert("RGB")
    return np.asarray(im)


def gradient_peaks(gray, axis=0, blur=1):
    """投影梯度强度找网格线候选。
    axis=0: 垂直方向梯度 → 竖线(列边界)在 x 投影; axis=1: 横线在 y 投影
    返回峰值位置列表（经过局部极大过滤）
    """
    g = gray.astype(np.float32)
    if axis == 0:
        grad = np.abs(np.diff(g, axis=1)).sum(axis=0)   # 每列竖向变化总量 → 竖线
    else:
        grad = np.abs(np.diff(g, axis=0)).sum(axis=1)   # 每行横向变化总量 → 横线
    # 平滑
    if blur > 1:
        k = np.ones(blur) / blur
        grad = np.convolve(grad, k, mode="same")
    # 局部极大值
    n = len(grad)
    peaks = []
    for i in range(1, n - 1):
        if grad[i] > grad[i - 1] and grad[i] >= grad[i + 1]:
            peaks.append((i, float(grad[i])))
    # 按强度排序
    peaks.sort(key=lambda t: -t[1])
    return grad, peaks


def find_uniform_grid(peaks, max_lines, tol=0.12):
    """在峰值中寻找等间距序列（网格）。
    返回 (start, spacing, indices) 或 None。spacing 即格宽/格高。
    不依赖颜色，只依赖等间距结构。
    """
    n = len(peaks)
    if n < max_lines:
        return None
    best = None
    # 取前若干强峰值（最多 max_lines 个），检查是否等间距
    strong = [p[0] for p in peaks[:max_lines * 3]]
    strong.sort()
    for i in range(len(strong)):
        for j in range(i + 1, len(strong)):
            spacing = strong[j] - strong[i]
            if spacing < 5:
                continue
            # 检查从 strong[i] 开始，连续 spacing 步进的 k 个点是否存在
            k = max_lines
            hits = []
            for m in range(k):
                target = strong[i] + m * spacing
                # 在 strong 里找接近 target 的点
                idx = np.searchsorted(strong, target)
                found = None
                for ii in (idx - 1, idx, idx + 1):
                    if 0 <= ii < len(strong) and abs(strong[ii] - target) <= max(2, spacing * tol):
                        found = strong[ii]
                        break
                if found is None:
                    break
                hits.append(found)
            if len(hits) >= max_lines - 1:
                start = hits[0]
                # 用回归精化 spacing（用连续点）
                arr = np.asarray(hits, dtype=np.float64)
                idxs = np.arange(len(arr))
                if len(arr) >= 2:
                    sp = (arr[-1] - arr[0]) / (len(arr) - 1)
                else:
                    sp = spacing
                score = (len(hits), -np.std(arr - (arr[0] + idxs * sp)))
                if best is None or score > best[0]:
                    best = (score, start, sp, hits)
    if best is None:
        return None
    _, start, sp, hits = best
    return start, sp, hits


def detect_canvas_structural(img):
    """结构法画布检测：找 24 格等间距网格 + 边框，返回相对比例坐标"""
    h, w = img.shape[:2]
    gray = img.mean(axis=2)

    # ---- 1. 网格线检测（等间距） ----
    gcol, peaks_x = gradient_peaks(gray, axis=0)   # 竖线位置
    grow, peaks_y = gradient_peaks(gray, axis=1)   # 横线位置

    res_x = find_uniform_grid(peaks_x, max_lines=25)
    res_y = find_uniform_grid(peaks_y, max_lines=25)

    # 画布需要 24+1 条线（边界+内线），若不足 25 试试 20
    canvas = None
    for mx in (25, 20, 15):
        for my in (25, 20, 15):
            rx = find_uniform_grid(peaks_x, max_lines=mx)
            ry = find_uniform_grid(peaks_y, max_lines=my)
            if rx is None or ry is None:
                continue
            sx, spx, hx = rx
            sy, spy, hy = ry
            if not (spx > 3 and spy > 3):
                continue
            # 校验格子数：画布约 24×24 → 间距应相近（方形网格）
            if 0.6 < spx / spy < 1.8:
                canvas = (sx, sy, spx, spy, len(hx), len(hy))
                break
        if canvas:
            break

    # ---- 2. 画布边框（大矩形）：用 Canny+Hough 太重，改用行/列投影的"边缘带" ----
    # 网格本身自带边框（最外侧线），因此从网格结果即可推边框
    info = {
        "w": w, "h": h,
        "peaks_x_top": [p[0] for p in peaks_x[:30]],
        "peaks_y_top": [p[0] for p in peaks_y[:30]],
        "canvas": None,
    }
    if canvas:
        sx, sy, spx, spy, nx, ny = canvas
        # 画布范围（外边框）
        right = sx + (nx - 1) * spx
        bottom = sy + (ny - 1) * spy
        info["canvas"] = {
            "origin": (sx, sy),           # 画布(0,0)格左上角 = 网格左上角
            "right": right, "bottom": bottom,
            "cell_w": spx, "cell_h": spy,
            "grid_nx": nx, "grid_ny": ny,
            # 相对比例（分辨率无关）
            "rel_left": sx / w, "rel_top": sy / h,
            "rel_right": right / w, "rel_bottom": bottom / h,
            "rel_cx": (sx + right) / 2 / w, "rel_cy": (sy + bottom) / 2 / h,
        }
    return info


def detect_palette_structural(img, canvas=None):
    """结构法色板检测：找规则排列的彩色方块网格（75×75 色块）。
    不依赖精确颜色，用"相邻像素色差 + 方块边界"。
    返回相对比例坐标。
    """
    h, w = img.shape[:2]
    g = img.astype(np.float32)
    # 色差 = 相邻像素 RGB 距离（方块边界处大）
    diff_x = np.abs(np.diff(g, axis=1)).sum(axis=2).mean(axis=0)   # 竖边界在 x
    diff_y = np.abs(np.diff(g, axis=0)).sum(axis=2).mean(axis=1)   # 横边界在 y
    # 平滑
    kx = np.ones(3) / 3
    diff_x = np.convolve(diff_x, kx, mode="same")
    diff_y = np.convolve(diff_y, kx, mode="same")

    def peaks_of(arr):
        n = len(arr)
        ps = []
        for i in range(1, n - 1):
            if arr[i] > arr[i - 1] and arr[i] >= arr[i + 1]:
                ps.append((i, float(arr[i])))
        ps.sort(key=lambda t: -t[1])
        return ps

    px = peaks_of(diff_x)
    py = peaks_of(diff_y)

    # 色板方块是较大间距的网格（约75px），且列数较少（4列）
    best = None
    for mx in (5, 4, 6):
        for my in (4, 3, 5, 6):
            rx = find_uniform_grid(px, max_lines=mx)
            ry = find_uniform_grid(py, max_lines=my)
            if rx is None or ry is None:
                continue
            sx, spx, hx = rx
            sy, spy, hy = ry
            if not (spx > 30 and spy > 30):
                continue
            if 0.7 < spx / spy < 1.5:
                score = (len(hx) + len(hy), -abs(spx - spy))
                if best is None or score > best[0]:
                    best = (score, sx, sy, spx, spy, len(hx), len(hy))
    if best is None:
        return None
    _, sx, sy, spx, spy, nx, ny = best
    return {
        "origin": (sx, sy),
        "cell_w": spx, "cell_h": spy,
        "grid_nx": nx, "grid_ny": ny,
        "rel_left": sx / w, "rel_top": sy / h,
        "rel_right": (sx + (nx - 1) * spx) / w,
        "rel_bottom": (sy + (ny - 1) * spy) / h,
    }


def main():
    for name in ("calibration_capture.png", "live_capture.png"):
        print("=" * 60)
        print(f"### {name}")
        img = load(name)
        h, w = img.shape[:2]
        print(f"尺寸 {w}x{h}  平均色 {img.mean():.0f}")
        ci = detect_canvas_structural(img)
        if ci["canvas"]:
            c = ci["canvas"]
            print("  画布网格 OK:")
            print(f"    origin=({c['origin'][0]},{c['origin'][1]})  cell={c['cell_w']}x{c['cell_h']}  grid={c['grid_nx']}x{c['grid_ny']}")
            print(f"    相对位置: left={c['rel_left']:.3f} top={c['rel_top']:.3f} "
                  f"right={c['rel_right']:.3f} bottom={c['rel_bottom']:.3f} 中心=({c['rel_cx']:.3f},{c['rel_cy']:.3f})")
        else:
            print("  画布网格: 未检测到")
            print(f"    最强竖线峰值: {ci['peaks_x_top'][:15]}")
            print(f"    最强横线峰值: {ci['peaks_y_top'][:15]}")
        pal = detect_palette_structural(img)
        if pal:
            print(f"  色板网格 OK: origin=({pal['origin'][0]},{pal['origin'][1]}) "
                  f"cell={pal['cell_w']}x{pal['cell_h']} grid={pal['grid_nx']}x{pal['grid_ny']}")
            print(f"    相对位置: left={pal['rel_left']:.3f} top={pal['rel_top']:.3f} "
                  f"right={pal['rel_right']:.3f} bottom={pal['rel_bottom']:.3f}")
        else:
            print("  色板网格: 未检测到")
        print()


if __name__ == "__main__":
    main()
