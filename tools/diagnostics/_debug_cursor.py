# -*- coding: utf-8 -*-
"""诊断 SetCursorPos 是否生效 + 窗口位置"""
import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
hwnd = 3612468

# 窗口矩形
rc = wt.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rc))
print(f"窗口矩形: left={rc.left} top={rc.top} right={rc.right} bottom={rc.bottom}")
print(f"整窗尺寸: {rc.right-rc.left}x{rc.bottom-rc.top}")

# 客户区
crc = wt.RECT()
user32.GetClientRect(hwnd, ctypes.byref(crc))
pt0 = wt.POINT(0, 0)
user32.ClientToScreen(hwnd, ctypes.byref(pt0))
print(f"客户区尺寸: {crc.right}x{crc.bottom} 原点屏幕坐标: ({pt0.x},{pt0.y})")

# 客户区 (1236,620) -> 屏幕坐标
pt = wt.POINT(1236, 620)
r = user32.ClientToScreen(hwnd, ctypes.byref(pt))
print(f"ClientToScreen(1236,620) -> ({pt.x},{pt.y}) 返回={r}")

# SetCursorPos 测试
SetCursorPos = user32.SetCursorPos
SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
SetCursorPos.restype = wt.BOOL
ok = SetCursorPos(pt.x, pt.y)
err = ctypes.get_last_error()
time.sleep(0.2)
cur = wt.POINT()
user32.GetCursorPos(ctypes.byref(cur))
print(f"SetCursorPos({pt.x},{pt.y}) 返回={ok} last_error={err} 当前光标=({cur.x},{cur.y})")

# 前台窗口
fg = user32.GetForegroundWindow()
print(f"前台窗口=0x{fg:X} 目标=0x{hwnd:X} 相同={fg == hwnd}")
