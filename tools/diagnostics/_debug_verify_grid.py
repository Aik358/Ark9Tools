# -*- coding: utf-8 -*-
"""验证当前校准（grid_top=444, left=1236, gaps=88）下色板网格采样是否对准色块中心"""
import ctypes
import ctypes.wintypes as wt
import numpy as np

from win32_capture import ScreenCapturer
from win32_input import IsWindow
from calibration import sample_color_at
from palette import EXHIBITION_PALETTE, find_nearest_idx, color_dist

hwnd = 3612468
if not IsWindow(hwnd):
    raise SystemExit("窗口失效")

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

left, gx, gy, top = 1236, 88, 88, 444
print("\n网格采样（每格: 最近色(d=感知距离) 实际RGB）:")
for r in range(6):
    y = top + r * gy
    line = []
    for c in range(4):
        x = left + c * gx
        if not (0 <= x < w and 0 <= y < h):
            line.append("out")
            continue
        rgb = sample_color_at(img, x, y, half=5)
        idx = find_nearest_idx(rgb)
        code = EXHIBITION_PALETTE[idx][0]
        prgb = EXHIBITION_PALETTE[idx][1]
        d = color_dist(*[int(v) for v in rgb], *prgb)
        line.append(f"{code}(d={d:.0f},{tuple(int(v) for v in rgb)})")
    print(f"  row{r} y={y}: " + " | ".join(line))

# 顶部"当前选中"大色块（y=355 附近）
print("\n顶部大色块区扫描（y 从 320 到 440，每 10px 采样 x=1236）:")
for y in range(320, 450, 10):
    rgb = sample_color_at(img, 1236, y, half=5)
    idx = find_nearest_idx(rgb)
    code = EXHIBITION_PALETTE[idx][0]
    print(f"  y={y}: {code} {tuple(int(v) for v in rgb)}")
