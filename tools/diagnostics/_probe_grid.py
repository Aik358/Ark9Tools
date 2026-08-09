# -*- coding: utf-8 -*-
"""精细分析校准截图：投影曲线形态 + 细线检测"""
import numpy as np
from PIL import Image

img = np.asarray(Image.open("calibration_capture.png").convert("RGB"))
h, w = img.shape[:2]
print(f"尺寸 {w}x{h}")

gray = img.mean(axis=2).astype(np.float32)

# 按行/列求"与均值的差的绝对值"并累计 → 任何横向/纵向变化都会凸起
col_dev = np.abs(gray - gray.mean()).sum(axis=0)   # 每列偏离均值总量
row_dev = np.abs(gray - gray.mean()).sum(axis=1)   # 每行偏离均值总量

# 平滑
k = np.ones(3) / 3
col_dev = np.convolve(col_dev, k, mode="same")
row_dev = np.convolve(row_dev, k, mode="same")

def find_valleys_or_peaks(arr, mode="peak"):
    """找局部极值，返回 (idx, value) 列表，强度排序"""
    n = len(arr)
    out = []
    for i in range(2, n - 2):
        win = arr[i - 2:i + 3]
        if mode == "peak" and arr[i] == win.max() and arr[i] > arr[i - 1] and arr[i] >= arr[i + 1]:
            out.append((i, float(arr[i])))
        if mode == "valley" and arr[i] == win.min() and arr[i] < arr[i - 1] and arr[i] <= arr[i + 1]:
            out.append((i, float(arr[i])))
    out.sort(key=lambda t: -t[1])
    return out

print("\n--- 列方向(竖线) top40 ---")
for i, v in find_valleys_or_peaks(col_dev, "peak")[:40]:
    print(f"  x={i} v={v:.0f}")

print("\n--- 行方向(横线) top40 ---")
for i, v in find_valleys_or_peaks(row_dev, "peak")[:40]:
    print(f"  y={i} v={v:.0f}")

# 检查是否存在等间距 29 的序列（画布 24 格 → 29px 格子）
print("\n--- 检查等间距(29px 候选) ---")
cols = sorted([i for i, v in find_valleys_or_peaks(col_dev, "peak")])
rows = sorted([i for i, v in find_valleys_or_peaks(row_dev, "peak")])
for arr, nm in ((cols, "竖线"), (rows, "横线")):
    if len(arr) > 10:
        diffs = np.diff(arr)
        print(f"{nm}: 前30个间距 = {diffs[:30].tolist()}")

# 画布边框：找"偏离均值"特别强的连续段
print("\n--- 列偏差 top10 及其邻域 ---")
for i, v in find_valleys_or_peaks(col_dev, "peak")[:10]:
    seg = col_dev[max(0,i-3):i+4]
    print(f"  x={i} v={v:.0f} 邻域=[{', '.join(f'{x:.0f}' for x in seg)}]")
