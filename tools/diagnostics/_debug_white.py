# -*- coding: utf-8 -*-
"""验证 flatten_white 修复：白色背景 + 彩色物体 → 白色背景格应为 0"""
import numpy as np
from PIL import Image
from palette import EXHIBITION_PALETTE
from pixelate import pixelate

# 造一张图：白底 + 红色圆 + 蓝色方块（背景 245~255 模拟缩放后的"近似白"）
size = 192
img = Image.new("RGB", (size, size), (250, 250, 250))
px = img.load()
for y in range(size):
    for x in range(size):
        # 红色圆
        if (x - 100) ** 2 + (y - 60) ** 2 < 30 ** 2:
            px[x, y] = (220, 40, 40)
        # 蓝色方块
        if 40 <= x <= 80 and 120 <= y <= 160:
            px[x, y] = (40, 80, 220)
        # 中心白色纯白区
        if 120 <= x <= 150 and 120 <= y <= 150:
            px[x, y] = (255, 255, 255)

img.save("_tmp_white.png")
mat = pixelate("_tmp_white.png", dither="none", flatten_white=True)

# 统计
flat = mat.flatten()
counts = np.bincount(flat, minlength=len(EXHIBITION_PALETTE))
print("颜色分布（top10）:")
for idx in np.argsort(-counts)[:10]:
    if counts[idx] > 0:
        code, rgb, name = EXHIBITION_PALETTE[idx]
        print(f"  idx={idx:2d} {code} {rgb} {name} x{counts[idx]}")

# 白色背景格（应该映射为 0）
# 原图中 (200, 200) 是背景 (250,250,250)
# 原图中 (60, 40) 是红圆
# 原图中 (200, 60) 是背景
white_cells = 0
nonwhite_cells = 0
for y in range(24):
    for x in range(24):
        # 原图 192x192 → 每格 8x8，中心像素
        ox = x * 8 + 4
        oy = y * 8 + 4
        orig = img.getpixel((ox, oy))
        idx = int(mat[y, x])
        if orig[0] > 240 and orig[1] > 240 and orig[2] > 240:
            white_cells += 1
            if idx != 0:
                print(f"  背景格未归0! ({x},{y}) 原色={orig} idx={idx} {EXHIBITION_PALETTE[idx][0]}")
        else:
            if idx == 0:
                nonwhite_cells += 1

print(f"\n白色背景格数={white_cells} 其中被正确归0的比例="
      f"{(white_cells - white_cells + (white_cells - sum(1 for y in range(24) for x in range(24) if img.getpixel((x*8+4,y*8+4))[0]>240 and mat[y,x]!=0)))/max(1,white_cells):.0%}")
import os
os.remove("_tmp_white.png")
