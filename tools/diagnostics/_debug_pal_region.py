# -*- coding: utf-8 -*-
"""聚焦色板区域(x1100+, y300+)：输出所有色块边界框与采样颜色"""
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


# 色板区域
zone = img[300:, 1100:]
zh, zw = zone.shape[:2]
sat = (zone.max(axis=2) - zone.min(axis=2)) > 30
sat &= zone.mean(axis=2) > 40   # 排除纯黑背景

visited = np.zeros((zh, zw), dtype=bool)
blobs = []
from collections import deque
for y in range(zh):
    for x in range(zw):
        if sat[y, x] and not visited[y, x]:
            q = deque([(y, x)])
            visited[y, x] = True
            xs, ys = [], []
            while q:
                yy, xx = q.popleft()
                xs.append(xx); ys.append(yy)
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    ny, nx = yy + dy, xx + dx
                    if 0 <= ny < zh and 0 <= nx < zw and sat[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        q.append((ny, nx))
            if len(xs) > 300:
                x0, x1 = min(xs), max(xs)
                y0, y1 = min(ys), max(ys)
                cx = (x0 + x1) // 2 + 1100
                cy = (y0 + y1) // 2 + 300
                # 用中心 -3..3 小区域采样（避开边框）
                patch = img[cy - 3:cy + 4, cx - 3:cx + 4]
                rgb = patch.reshape(-1, 3).mean(axis=0)
                idx, d = match(rgb)
                code = EXHIBITION_PALETTE[idx][0]
                blobs.append((cx, cy, x1 - x0, y1 - y0, idx, code, d,
                              tuple(int(v) for v in rgb)))

blobs.sort(key=lambda b: (b[1], b[0]))
print(f"色板区域检测到 {len(blobs)} 个色块 (中心x, 中心y, 宽, 高, idx, code, d, RGB):")
for cx, cy, bw, bh, idx, code, d, s in blobs:
    print(f"  x={cx} y={cy} 尺寸={bw}x{bh}  idx={idx:2d} {code} d={d:6.0f} 采样RGB={s}")

print("\n按行排列:")
ys = sorted(set(b[1] // 50 for b in blobs))
for yk in ys:
    rowblobs = [b for b in blobs if b[1] // 50 == yk]
    rowblobs.sort(key=lambda b: b[0])
    ymid = rowblobs[0][1]
    codes = " ".join(f"{b[2]}x{b[3]}@{b[4]:2d}:{b[5]}" for b in rowblobs)
    print(f"  y~{ymid}: {codes}")
