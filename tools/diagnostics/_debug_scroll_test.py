# -*- coding: utf-8 -*-
"""交互诊断：滚动游戏色板，观察可见行变化，确定排列与每档滚动行数"""
import ctypes
import ctypes.wintypes as wt
import time
import numpy as np
from PIL import Image
from palette import EXHIBITION_PALETTE

from win32_capture import ScreenCapturer
from win32_input import IsWindow, WindowInput, WHEEL_DELTA

hwnd = 3612468
print(f"hwnd={hwnd} alive={bool(IsWindow(hwnd))}")
if not IsWindow(hwnd):
    raise SystemExit("窗口失效")

# 激活窗口（确保 SendInput 有效）
win = WindowInput(hwnd)
win.activate_foreground(force=True, aggressive=True)
time.sleep(0.3)
print(f"前台={win.is_foreground()}")


def grab():
    rc = wt.RECT()
    ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rc))
    pt = wt.POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))
    region = (pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom)
    cap = ScreenCapturer(region)
    img = cap.grab()
    cap.close()
    return img


def match(rgb):
    r, g, b = rgb
    best, best_d = -1, float("inf")
    for i, (_, prgb, _) in enumerate(EXHIBITION_PALETTE):
        dr, dg, db = r - prgb[0], g - prgb[1], b - prgb[2]
        d = 2 * dr * dr + 4 * dg * dg + 3 * db * db
        if d < best_d:
            best_d, best = d, i
    return best, best_d


def scan_palette(img):
    """扫描色板区域可见行，返回 [(y_center, [(col_code, rgb), ...]), ...]"""
    if img is None:
        return []
    h, w = img.shape[:2]
    zone = img[300:, 1100:]
    zh, zw = zone.shape[:2]
    sat = (zone.max(axis=2) - zone.min(axis=2)) > 30
    sat &= zone.mean(axis=2) > 40
    row_count = sat.sum(axis=1)
    runs = []
    i = 0
    while i < zh:
        if row_count[i] > 80:
            j = i
            while j < zh and row_count[j] > 80:
                j += 1
            if j - i >= 30:
                runs.append((i + 300, j + 300))
            i = j
        else:
            i += 1
    out = []
    for r0, r1 in runs:
        mid = (r0 + r1) // 2
        line = []
        for x in [1236, 1324, 1411, 1499]:
            if 0 <= x < w and 0 <= mid < h:
                patch = img[mid - 5:mid + 6, x - 5:x + 6]
                rgb = patch.reshape(-1, 3).mean(axis=0)
                idx, d = match(rgb)
                line.append((EXHIBITION_PALETTE[idx][0], tuple(int(v) for v in rgb), float(d)))
        out.append((mid, line))
    return out


print("\n=== 当前色板状态 ===")
img = grab()
if img is not None:
    Image.fromarray(img[:, :, ::-1]).save("_s1.png")
for y, line in scan_palette(img):
    print(f"  y={y}: " + " | ".join(f"{c}({rgb},d={d:.0f})" for c, rgb, d in line))

# 向上滚轮 12 次（delta=+120），尽量到顶
print("\n=== 向上滚动 12 次后 ===")
for _ in range(12):
    win.send_wheel(1236, 620, WHEEL_DELTA)
    time.sleep(0.12)
img = grab()
if img is not None:
    Image.fromarray(img[:, :, ::-1]).save("_s2.png")
for y, line in scan_palette(img):
    print(f"  y={y}: " + " | ".join(f"{c}({rgb},d={d:.0f})" for c, rgb, d in line))

# 再向下滚 3 次，观察每档滚动行数
print("\n=== 向下滚动 3 次（每次-120）===")
img = grab()
if img is not None:
    for i in range(3):
        win.send_wheel(1236, 620, -WHEEL_DELTA)
        time.sleep(0.12)
        img = grab()
        print(f"  第{i+1}次:")
        for y, line in scan_palette(img):
            print(f"    y={y}: " + " | ".join(f"{c}" for c, rgb, d in line))
