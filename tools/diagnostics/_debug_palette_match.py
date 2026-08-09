# -*- coding: utf-8 -*-
"""实时截图色板每个色块 → 提取真实 RGB → 与 EXHIBITION_PALETTE 比对 → 找错位"""
import ctypes
import ctypes.wintypes as wt
import numpy as np
from PIL import Image

from win32_capture import ScreenCapturer
from win32_input import IsWindow
from palette import EXHIBITION_PALETTE, color_dist

hwnd = 3612468
if not IsWindow(hwnd):
    raise SystemExit("窗口失效")

# 截图
rc = wt.RECT()
ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rc))
pt0 = wt.POINT(0, 0)
ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt0))
region = (pt0.x, pt0.y, pt0.x + rc.right, pt0.y + rc.bottom)
cap = ScreenCapturer(region)
img = cap.grab()
cap.close()
if img is None:
    raise SystemExit("截图失败")
h, w = img.shape[:2]
print(f"截图 {w}x{h}")

# 按你的截图：色板顶部是"当前选中"大色块行，下面 5 行网格
# 经验值：网格起始约 y=443，行距 88，列距 87
# 但顶部"当前选中"行也会跟着滚动 — 直接扫描色板区域找 5 行

# 简化：先按假设 grid_top=443、col_gap=87、row_gap=88 采样所有可见行
left = 1236
gx, gy = 87, 88

# 扫描"网格首行"可能在多个 Y 候选上
print("\n按候选 grid_top 采样网格各行(每行4列):")
for grid_top in [443, 444, 450, 355, 530, 620, 707, 795]:
    print(f"\n-- grid_top={grid_top} --")
    for r in range(6):
        y = grid_top + r * gy
        if not (0 <= y < h):
            continue
        line = []
        for c in range(4):
            x = left + c * gx
            if not (0 <= x < w):
                continue
            patch = img[y - 5:y + 6, x - 5:x + 6]
            rgb = patch.reshape(-1, 3).mean(axis=0)
            # 找 EXHIBITION_PALETTE 最近色
            best, best_d = -1, float("inf")
            for i, (_, prgb, _) in enumerate(EXHIBITION_PALETTE):
                d = color_dist(int(rgb[0]), int(rgb[1]), int(rgb[2]),
                               prgb[0], prgb[1], prgb[2])
                if d < best_d:
                    best_d, best = d, i
            code = EXHIBITION_PALETTE[best][0]
            crgb = EXHIBITION_PALETTE[best][1]
            line.append(f"{code}actual={tuple(int(v) for v in rgb)}expected={crgb}d={int(best_d)}")
        print(f"  y={y}: " + " | ".join(line))