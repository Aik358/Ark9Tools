# -*- coding: utf-8 -*-
"""实测 SendMessageW(WM_MOUSEWHEEL) 滚动是否生效"""
import ctypes
import ctypes.wintypes as wt
import time
import numpy as np

from win32_capture import ScreenCapturer
from win32_input import IsWindow, WindowInput, WHEEL_DELTA

hwnd = 3612468
print(f"hwnd={hwnd} alive={bool(IsWindow(hwnd))}")
if not IsWindow(hwnd):
    raise SystemExit("窗口失效")

win = WindowInput(hwnd)
print(f"前台={win.is_foreground()}")

# 截图A
rc = wt.RECT()
ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rc))
pt0 = wt.POINT(0, 0)
ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt0))
region = (pt0.x, pt0.y, pt0.x + rc.right, pt0.y + rc.bottom)
cap = ScreenCapturer(region)
img1 = cap.grab()
cap.close()

# 向下滚 5 次（-120），每次截图对比
for i in range(5):
    win.send_wheel(1236, 620, -WHEEL_DELTA)
    time.sleep(0.2)
    cap = ScreenCapturer(region)
    img2 = cap.grab()
    cap.close()
    diff = int(np.abs(img1.astype(int) - img2.astype(int)).sum())
    # 色板区域差异
    pal1 = img1[400:850, 1200:1550].astype(int)
    pal2 = img2[400:850, 1200:1550].astype(int)
    paldiff = int(np.abs(pal1 - pal2).sum())
    print(f"  第{i+1}次滚动: 全图像素差={diff} 色板区域差={paldiff}")
    img1 = img2

# 向上滚 3 次，再对比
img2 = None
for i in range(3):
    win.send_wheel(1236, 620, WHEEL_DELTA)
    time.sleep(0.2)
    cap = ScreenCapturer(region)
    img2 = cap.grab()
    cap.close()
    pal1 = img1[400:850, 1200:1550].astype(int)
    pal2 = img2[400:850, 1200:1550].astype(int)
    paldiff = int(np.abs(pal1 - pal2).sum())
    print(f"  向上第{i+1}次滚动: 色板区域差={paldiff}")
    img1 = img2
