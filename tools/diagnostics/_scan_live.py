# -*- coding: utf-8 -*-
"""深入扫描 live_capture.png：找画布区域（周期性网格 / 彩色像素画区域）"""
import numpy as np
from PIL import Image

a = np.asarray(Image.open("live_capture.png").convert("RGB")).astype(np.float32)
h, w = a.shape[:2]
g = a.mean(axis=2)

# 1) 彩色像素分布：像素画区域会有大量非灰彩色
sat = (a.max(axis=2) - a.min(axis=2))
color_mask = sat > 40
print("彩色像素占比: %.2f%%" % (100.0 * color_mask.mean()))
# 彩色像素按区域统计
ys, xs = np.nonzero(color_mask)
if len(xs):
    print("  彩色区域 bbox: x[%d..%d] y[%d..%d]" % (xs.min(), xs.max(), ys.min(), ys.max()))
    # 划分 8x8 网格统计密度
    n = 8
    dens = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            x0, x1 = w * j // n, w * (j + 1) // n
            y0, y1 = h * i // n, h * (i + 1) // n
            sub = color_mask[y0:y1, x0:x1]
            dens[i, j] = sub.mean()
    print("  彩色密度(8x8 网格,每格0-100%):")
    for i in range(n):
        print("    " + " ".join("%4.1f" % (100 * dens[i, j]) for j in range(n)))

# 2) 周期性检测：对每行/列做自相关找网格周期
def find_period(proj):
    """对投影序列做自相关，找显著周期"""
    n = len(proj)
    p = proj - proj.mean()
    # 只分析中间区域
    rng = slice(n // 4, 3 * n // 4)
    p = p[rng]
    m = len(p)
    best, bestv = None, 0
    for lag in range(8, min(120, m // 2)):
        v = np.corrcoef(p[:-lag], p[lag:])[0, 1]
        if np.isfinite(v) and v > bestv:
            bestv, best = v, lag
    return best, bestv

cp = find_period(g.mean(axis=0))  # 列方向周期
rp = find_period(g.mean(axis=1))  # 行方向周期
print("列方向最强周期(像素):", cp, "  行方向最强周期:", rp)

# 3) 每个采样块的平均亮度 -> 找亮/暗区域分布
print("平均亮度(8x8 网格):")
blk = np.zeros((8, 8))
for i in range(8):
    for j in range(8):
        x0, x1 = w * j // 8, w * (j + 1) // 8
        y0, y1 = h * i // 8, h * (i + 1) // 8
        blk[i, j] = g[y0:y1, x0:x1].mean()
    print("    " + " ".join("%5.0f" % blk[i, j] for j in range(8)))

# 4) 四角与中心采样
print("角/中心采样:")
for (x, y) in [(w // 2, h // 2), (w // 4, h // 4), (3 * w // 4, 3 * h // 4), (100, 100), (w - 100, h - 100)]:
    print("  (%d,%d)=%s" % (x, y, a[y, x].astype(int).tolist()))
