# -*- coding: utf-8 -*-
"""综合诊断：① send_wheel 返回值/光标位置 ② 滚动前后截图像素差 ③ HDR 校色对比"""
import ctypes
import ctypes.wintypes as wt
import time
import numpy as np
from PIL import Image

from win32_capture import ScreenCapturer
from win32_input import IsWindow, WindowInput, WHEEL_DELTA

hwnd = 3612468
print(f"hwnd={hwnd} alive={bool(IsWindow(hwnd))}")
if not IsWindow(hwnd):
    raise SystemExit("窗口失效")

win = WindowInput(hwnd)
print(f"激活前前台={win.is_foreground()}")
win.activate_foreground(force=True, aggressive=True)
time.sleep(0.3)
print(f"激活后前台={win.is_foreground()}")

# 光标当前位置
pt = wt.POINT()
ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
print(f"当前光标屏幕坐标=({pt.x},{pt.y})")

# 客户区
rc = wt.RECT()
ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rc))
pt0 = wt.POINT(0, 0)
ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt0))
region = (pt0.x, pt0.y, pt0.x + rc.right, pt0.y + rc.bottom)
print(f"客户区 {rc.right}x{rc.bottom} region={region}")

# 截图（HDR 校正开启默认）
cap = ScreenCapturer(region)
img1 = cap.grab()
cap.close()
Image.fromarray(img1[:, :, ::-1]).save("_diag_a.png")

# send_wheel 测试
ret = win.send_wheel(1236, 620, -WHEEL_DELTA)
print(f"\nsend_wheel(1236,620,-120) 返回={ret}")
time.sleep(0.5)
pt = wt.POINT()
ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
print(f"send_wheel 后光标=({pt.x},{pt.y})")

# 再截一张对比
cap = ScreenCapturer(region)
img2 = cap.grab()
cap.close()
Image.fromarray(img2[:, :, ::-1]).save("_diag_b.png")
diff = np.abs(img1.astype(int) - img2.astype(int)).sum()
print(f"滚动前后截图总像素差={diff}")

# 禁用 HDR 校正再截一张
cap2 = ScreenCapturer(region)
cap2._hdr_correct_enabled = False
img3 = cap2.grab()
cap2.close()
if img3 is not None:
    Image.fromarray(img3[:, :, ::-1]).save("_diag_c.png")
    # 比较色板区域颜色
    a = img1[500:700, 1230:1500]
    c = img3[500:700, 1230:1500]
    print(f"色板区域 校正前均值={a.reshape(-1,3).mean(axis=0).round(1).tolist()} "
          f"校正后={c.reshape(-1,3).mean(axis=0).round(1).tolist()}")
