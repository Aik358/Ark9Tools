# -*- coding: utf-8 -*-
"""精确分析色板区域（x>1100）：打印每个色块采样RGB，重建实际排列"""
import numpy as np
from PIL import Image
from palette import EXHIBITION_PALETTE

import sys
path = sys.argv[1] if len(sys.argv) > 1 else "_live_clean.png"
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


# 只扫描色板区域
zone = img[300:, 1100:]
zh, zw = zone.shape[:2]
sat = (zone.max(axis=2) - zone.min(axis=2)) > 40
sat &= zone.mean(axis=2) > 50

visited = np.zeros((zh, zw), dtype=bool)
blobs = []
from collections import deque
for y in range(zh):
    for x in range(zw):
        if sat[y, x] and not visited[y, x]:
            q = deque([(y, x)])
            visited[y, x] = True
            px, py = [], []
            while q:
                yy, xx = q.popleft()
                px.append(xx); py.append(yy)
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    ny, nx = yy + dy, xx + dx
                    if 0 <= ny < zh and 0 <= nx < zw and sat[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        q.append((ny, nx))
            if len(px) > 400:
                cx = sum(px) // len(px) + 1100
                cy = sum(py) // len(py) + 300
                patch = img[cy - 10:cy + 11, cx - 10:cx + 11]
                rgb = patch.reshape(-1, 3).mean(axis=0)
                idx, d = match(rgb)
                code = EXHIBITION_PALETTE[idx][0]
                blobs.append((cx, cy, idx, code, d, tuple(int(v) for v in rgb)))

blobs.sort(key=lambda b: (b[1], b[0]))
print(f"色板区域检测到 {len(blobs)} 个色块 (x, y, idx, code, d, 采样RGB):")
for cx, cy, idx, code, d, s in blobs:
    print(f"  x={cx} y={cy}  idx={idx:2d} {code} d={d:6.0f} 采样RGB={s}")

print("\n行优先排列:")
ys = sorted(set(b[1] // 40 for b in blobs))
for yk in ys:
    rowblobs = [b for b in blobs if b[1] // 40 == yk]
    rowblobs.sort(key=lambda b: b[0])
    ymid = rowblobs[0][1]
    codes = " ".join(f"{b[2]:2d}:{b[3]}" for b in rowblobs)
    print(f"  y~{ymid}: {codes}")

# 列 x 分布
print("\n列 x 分布:", sorted(set(b[0] // 50 for b in blobs)))
