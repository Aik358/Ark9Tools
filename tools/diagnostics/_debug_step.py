# -*- coding: utf-8 -*-
"""精确测：滚 1 档后色板网格首行颜色变化 → 确定每档滚动行数"""
import ctypes
import ctypes.wintypes as wt
import time
import numpy as np

from win32_capture import ScreenCapturer
from win32_input import IsWindow, WindowInput, WHEEL_DELTA
from calibration import sample_color_at
from palette import EXHIBITION_PALETTE, find_nearest_idx

hwnd = 3612468
print(f"hwnd={hwnd} alive={bool(IsWindow(hwnd))}")
if not IsWindow(hwnd):
    raise SystemExit("窗口失效")

win = WindowInput(hwnd)
print(f"前台={win.is_foreground()}")


def grab():
    rc = wt.RECT()
    ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rc))
    pt0 = wt.POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt0))
    region = (pt0.x, pt0.y, pt0.x + rc.right, pt0.y + rc.bottom)
    cap = ScreenCapturer(region)
    img = cap.grab()
    cap.close()
    return img


def read_grid_row0(img):
    """读网格第 0 行（grid_top=443）的 4 列颜色，返回识别到的 row 集合"""
    if img is None:
        return None
    h, w = img.shape[:2]
    grid_top = 443
    sample_y = grid_top
    if not (0 <= sample_y < h):
        return None
    idxs = []
    for col in range(4):
        cx = 1236 + col * 87
        if not (0 <= cx < w):
            continue
        rgb = sample_color_at(img, cx, sample_y, half=4)
        idx = find_nearest_idx(rgb)
        if idx is not None and idx % 4 == col:
            idxs.append(idx)
    if len(idxs) >= 2:
        rows = {i // 4 for i in idxs}
        if len(rows) == 1:
            return rows.pop()
    return None


print("\n=== 当前（顶部）网格首行 ===")
img = grab()
r0 = read_grid_row0(img)
print(f"grid_top_row = {r0}")

# 向下滚，每滚 1 次读一次
print("\n=== 向下滚动（每档 -120）===")
for i in range(6):
    win.send_wheel(1236, 620, -WHEEL_DELTA)
    time.sleep(0.2)
    img = grab()
    r = read_grid_row0(img)
    print(f"  滚{i+1}次后 grid_top_row={r}")

# 向上滚回
print("\n=== 向上滚动（每档 +120）===")
for i in range(6):
    win.send_wheel(1236, 620, WHEEL_DELTA)
    time.sleep(0.2)
    img = grab()
    r = read_grid_row0(img)
    print(f"  滚{i+1}次后 grid_top_row={r}")
