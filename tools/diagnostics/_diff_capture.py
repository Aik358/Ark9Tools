# -*- coding: utf-8 -*-
"""对比旧校准截图与当前实时画面，检测网格/界面差异"""
import numpy as np
from PIL import Image


def load(p):
    im = Image.open(p).convert("RGB")
    return np.asarray(im)


for name in ["calibration_capture.png", "live_capture.png"]:
    a = load(name)
    h, w = a.shape[:2]
    print("=== %s: %dx%d ===" % (name, w, h))
    g = a.mean(axis=2).astype(np.float32)
    rdiff = np.abs(np.diff(g, axis=0)).mean(axis=1)
    cdiff = np.abs(np.diff(g, axis=1)).mean(axis=0)
    import heapq
    topy = heapq.nlargest(20, range(len(rdiff)), key=lambda i: rdiff[i])
    topx = heapq.nlargest(20, range(len(cdiff)), key=lambda i: cdiff[i])
    print("  行边界top20(y):", sorted(topy))
    print("  列边界top20(x):", sorted(topx))
    print("  全图平均色:", a.reshape(-1, 3).mean(axis=0).round(1).tolist())
    # 统计独特颜色数（近似）
    q = (a // 16).reshape(-1, 3)
    uniq = len(np.unique(q, axis=0))
    print("  近似独特色块数(量化/16):", uniq)
    for (x, y) in [(0, 0), (500, 400), (800, 450), (1000, 500), (300, 200)]:
        print("  px(%d,%d)=" % (x, y), a[y, x].tolist())
    print()
