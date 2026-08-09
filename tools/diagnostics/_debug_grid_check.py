# -*- coding: utf-8 -*-
"""验证色板网格：按假设的原点+间距采样每格颜色，看是否匹配 X01~X38 行优先"""
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


# 测试不同的 top 候选，看哪个与 X01-X04 匹配最好
# 假设 palette_left=1236, col_gap=87, row_gap=88
# X01..X04 应位于第0行 (row=0): 采样各列
left, gx, gy = 1236, 87, 88
for top0 in [300, 340, 350, 355, 360, 380, 400, 420, 430, 440, 443, 444, 450]:
    row0_ok = 0
    total_d = 0
    row0_rgb = []
    for col in range(4):
        x = int(left + col * gx)
        y = int(top0)
        if 0 <= x < w and 0 <= y < h:
            patch = img[y - 8:y + 9, x - 8:x + 9]
            rgb = patch.reshape(-1, 3).mean(axis=0)
            idx, d = match(rgb)
            # X01..X04 = idx 0..3
            if idx <= 3:
                row0_ok += 1
            total_d += d
            row0_rgb.append((idx, d, tuple(int(v) for v in rgb)))
    # 第1行应该 X05..X08 (idx 4..7)
    row1_ok = 0
    for col in range(4):
        x = int(left + col * gx)
        y = int(top0 + gy)
        if 0 <= x < w and 0 <= y < h:
            patch = img[y - 8:y + 9, x - 8:x + 9]
            rgb = patch.reshape(-1, 3).mean(axis=0)
            idx, d = match(rgb)
            if 4 <= idx <= 7:
                row1_ok += 1
    print(f"top0={top0}: row0匹配X01-04={row0_ok}/4  row1匹配X05-08={row1_ok}/4  行0采样={row0_rgb}")

print("\n=== 详细检查 top0=355 与 top0=443 ===")
for top0 in [355, 443]:
    print(f"\n-- top0={top0} --")
    for r in range(5):
        line = []
        for col in range(4):
            x = int(left + col * gx)
            y = int(top0 + r * gy)
            if 0 <= x < w and 0 <= y < h:
                patch = img[y - 8:y + 9, x - 8:x + 9]
                rgb = patch.reshape(-1, 3).mean(axis=0)
                idx, d = match(rgb)
                code = EXHIBITION_PALETTE[idx][0]
                line.append(f"{code}(d={d:.0f},{tuple(int(v) for v in rgb)})")
            else:
                line.append("out")
        print(f"  r{r}: " + " | ".join(line))
