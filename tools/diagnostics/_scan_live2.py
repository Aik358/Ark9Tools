# -*- coding: utf-8 -*-
"""在 live_capture 上检测：左侧画布网格（宽松阈值）+ 右侧色板结构"""
import numpy as np
from PIL import Image
import cv2

a = np.asarray(Image.open("live_capture.png").convert("RGB"))
h, w = a.shape[:2]
img = a[:, :, ::-1].copy()  # BGR
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# 左侧区域 (0..1000 x 0..900) 找网格
left = gray[:, :1000]
# 水平/垂直边缘响应（Sobel）
gx = np.abs(cv2.Sobel(left, cv2.CV_32F, 1, 0, ksize=3))
gy = np.abs(cv2.Sobel(left, cv2.CV_32F, 0, 1, ksize=3))
print("左侧区域 Sobel 响应: 垂直边均值=%.2f 水平边均值=%.2f" % (gx.mean(), gy.mean()))

# 找列方向弱周期（自相关）
def autocorr_period(sig):
    s = sig - sig.mean()
    n = len(s)
    out = []
    for lag in range(10, 150):
        if lag >= n:
            break
        c = np.corrcoef(s[:-lag], s[lag:])[0, 1]
        if np.isfinite(c):
            out.append((lag, c))
    out.sort(key=lambda t: -t[1])
    return out[:3]

col_proj = left.mean(axis=0)
row_proj = left.mean(axis=1)
print("左侧列投影周期候选:", autocorr_period(col_proj))
print("左侧行投影周期候选:", autocorr_period(row_proj))

# 右侧区域 (1000..1600) 检测色板网格：找彩色方块
right = a[:, 1000:]
sat = (right.max(axis=2) - right.min(axis=2))
print("\n右侧区域: 彩色像素占比 %.2f%%" % (100 * sat.mean()))
# 色块检测：连通区域
mask = (sat > 60).astype(np.uint8) * 255
mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
boxes = []
for c in cnts:
    x, y, ww, hh = cv2.boundingRect(c)
    if ww >= 20 and hh >= 20:
        boxes.append((x + 1000, y, ww, hh))
print("检测到彩色块数:", len(boxes))
boxes.sort(key=lambda b: (b[1], b[0]))
for b in boxes[:30]:
    print("  色块 x=%d y=%d w=%d h=%d" % b)

# 整图按 4x4 网格亮度
print("\n亮度(4x4):")
blk = np.zeros((4, 4))
for i in range(4):
    row = []
    for j in range(4):
        x0, x1 = w * j // 4, w * (j + 1) // 4
        y0, y1 = h * i // 4, h * (i + 1) // 4
        v = gray[y0:y1, x0:x1].mean()
        blk[i, j] = v
        row.append("%5.0f" % v)
    print("   " + " ".join(row))

# 检测色板左边界：右侧区域按列扫描，找第一条彩色列
col_sat = sat.mean(axis=1)
first_color_col = None
for i, v in enumerate(col_sat):
    if v > 0.05:
        first_color_col = 1000 + i
        break
print("\n右侧第一条彩色列 x=", first_color_col)
