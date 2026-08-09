# -*- coding: utf-8 -*-
"""色板区域网格原始RGB采样：不匹配调色板，直接看游戏实际颜色排列"""
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


# 网格：从配置假设 left=1236 top=355 gx=87 gy=88
# 但先尝试不同 top 起点，打印原始RGB
left, gx, gy = 1236, 87, 88

print("\n=== top0=355 网格原始RGB ===")
for r in range(7):
    y = 355 + r * gy
    line = []
    for c in range(4):
        x = left + c * gx
        if 0 <= x < w and 0 <= y < h:
            patch = img[y - 8:y + 9, x - 8:x + 9]
            rgb = patch.reshape(-1, 3).mean(axis=0)
            idx, d = match(rgb)
            code = EXHIBITION_PALETTE[idx][0]
            line.append(f"{code}rgb={tuple(int(v) for v in rgb)}")
        else:
            line.append("out")
    print(f"  y={y}: " + " | ".join(line))

print("\n=== top0=443 网格原始RGB ===")
for r in range(6):
    y = 443 + r * gy
    line = []
    for c in range(4):
        x = left + c * gx
        if 0 <= x < w and 0 <= y < h:
            patch = img[y - 8:y + 9, x - 8:x + 9]
            rgb = patch.reshape(-1, 3).mean(axis=0)
            idx, d = match(rgb)
            code = EXHIBITION_PALETTE[idx][0]
            line.append(f"{code}rgb={tuple(int(v) for v in rgb)}")
        else:
            line.append("out")
    print(f"  y={y}: " + " | ".join(line))
