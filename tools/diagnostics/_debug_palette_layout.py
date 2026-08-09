# -*- coding: utf-8 -*-
"""分析截图：用 EXHIBITION_PALETTE 精确匹配色板区域色块，推断实际排列"""
import numpy as np
from PIL import Image
from palette import EXHIBITION_PALETTE

img = np.asarray(Image.open("live_capture.png").convert("RGB")).astype(np.float32)
h, w = img.shape[:2]
print(f"截图 {w}x{h}")

# 校准值
left, top = 1236, 355
gx, gy = 87, 88


def match(rgb):
    r, g, b = rgb
    best, best_d = -1, float("inf")
    for i, (_, prgb, _) in enumerate(EXHIBITION_PALETTE):
        dr, dg, db = r - prgb[0], g - prgb[1], b - prgb[2]
        d = 2 * dr * dr + 4 * dg * dg + 3 * db * db
        if d < best_d:
            best_d, best = d, i
    return best, best_d


# 采样色板网格区域（多行多列，超出截图范围跳过）
print("按校准网格采样 (x=left+c*gx, y=top+r*gy):")
for r in range(0, 8):
    row = []
    for c in range(4):
        x = int(left + c * gx)
        y = int(top + r * gy)
        if 0 <= x < w and 0 <= y < h:
            patch = img[y - 6:y + 7, x - 6:x + 7]
            rgb = patch.reshape(-1, 3).mean(axis=0)
            idx, d = match(rgb)
            code = EXHIBITION_PALETTE[idx][0]
            crgb = tuple(int(v) for v in EXHIBITION_PALETTE[idx][1])
            row.append(f"({code},{d:.0f})")
        else:
            row.append("  out")
    print(f"  r{r}: " + "  ".join(row))

# 不依赖校准：全图扫描高饱和色块，聚类出实际网格
print("\n全图色块扫描:")
hsv = np.zeros((h, w), dtype=np.uint8)
import colorsys
for y in range(h):
    for x in range(w):
        r, g, b = img[y, x] / 255.0
        hh, ss, vv = colorsys.rgb_to_hsv(r, g, b)
        if ss > 0.3 and vv > 0.25:
            hsv[y, x] = 1

# 找连通区中心（简化：按行/列投影聚类）
ys_proj = hsv.sum(axis=1)
xs_proj = hsv.sum(axis=0)


def cluster_peaks(proj, min_v=3):
    """找峰值并聚类为块中心"""
    peaks = []
    i = 0
    n = len(proj)
    while i < n:
        if proj[i] >= min_v:
            j = i
            while j < n and proj[j] >= min_v:
                j += 1
            mid = (i + j) // 2
            peaks.append((mid, int(proj[i:j].max())))
            i = j
        else:
            i += 1
    # 合并相邻峰值（同一行色块可能分裂）
    out = []
    for p in peaks:
        if out and p[0] - out[-1][0] < 6:
            continue
        out.append(p)
    return out


rows = cluster_peaks(ys_proj)
cols = cluster_peaks(xs_proj)
print(f"  检测到行中心: {[p[0] for p in rows]}")
print(f"  检测到列中心: {[p[0] for p in cols]}")

# 对每个 (row_y, col_x) 中心采样匹配
print("\n实际色块匹配:")
for ry, rv in rows:
    line = []
    for cx, cv in cols:
        y, x = ry, cx
        patch = img[y - 6:y + 7, x - 6:x + 7]
        rgb = patch.reshape(-1, 3).mean(axis=0)
        idx, d = match(rgb)
        code = EXHIBITION_PALETTE[idx][0]
        crgb = tuple(int(v) for v in EXHIBITION_PALETTE[idx][1])
        line.append(f"{code}")
    print(f"  y={ry}: {' '.join(line)}")
