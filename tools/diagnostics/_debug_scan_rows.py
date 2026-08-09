# -*- coding: utf-8 -*-
"""全面扫描色板区域：逐行找色块行中心，逐列采样颜色"""
import numpy as np
from PIL import Image
from palette import EXHIBITION_PALETTE

path = "_live_clean.png"
img = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
h, w = img.shape[:2]
print(f"截图 {path} {w}x{h}")


def match(rgb):
    r, g, b = rgb
    best, best_d = -1, float("inf")
    for i, (_, prgb, _) in enumerate(EXHIBITION_PALETTE):
        dr, dg, db = r - prgb[0], g - prgb[1], b - prgb[2]
        d = 2 * dr * dr + 4 * dg * dg + 3 * db * db
        if d < best_d:
            best_d, best = d, i
    return best, best_d


# 逐行统计"彩色像素数"（非白非黑）
zone = img[300:, 1100:]
zh, zw = zone.shape[:2]
sat = (zone.max(axis=2) - zone.min(axis=2)) > 30
sat &= zone.mean(axis=2) > 40

row_count = sat.sum(axis=1)
print("每行彩色像素数（>100 的行）:")
runs = []
i = 0
while i < zh:
    if row_count[i] > 100:
        j = i
        while j < zh and row_count[j] > 100:
            j += 1
        runs.append((i + 300, j + 300, int(row_count[i:j].max())))
        i = j
    else:
        i += 1
for r0, r1, v in runs:
    mid = (r0 + r1) // 2
    print(f"  y=[{r0}..{r1}] 中心={mid} 峰值={v}")

# 对每个色块行，采样所有列的颜色
print("\n各色块行采样:")
left_candidates = [1236, 1324, 1411, 1499]
for r0, r1, v in runs:
    if r1 - r0 < 30:
        continue
    mid = (r0 + r1) // 2
    line = []
    for x in left_candidates:
        if 0 <= x < w and 0 <= mid < h:
            patch = img[mid - 6:mid + 7, x - 6:x + 7]
            rgb = patch.reshape(-1, 3).mean(axis=0)
            idx, d = match(rgb)
            code = EXHIBITION_PALETTE[idx][0]
            line.append(f"{code}({tuple(int(v) for v in rgb)},d={d:.0f})")
        else:
            line.append("out")
    print(f"  y={mid}: " + " | ".join(line))
