# -*- coding: utf-8 -*-
"""实时连接游戏窗口截图，分析色板真实排列（干净无遮罩）"""
import ctypes
import ctypes.wintypes as wt
import numpy as np
from palette import EXHIBITION_PALETTE

from win32_capture import ScreenCapturer
from win32_input import IsWindow

hwnd = 3612468  # 配置中的窗口句柄
print(f"hwnd={hwnd} alive={bool(IsWindow(hwnd))}")

if not IsWindow(hwnd):
    print("窗口已失效，尝试重新查找...")
    from win32_input import find_window
    hwnd = find_window("方舟") or find_window("明日")
    print("找到 hwnd:", hwnd)
    if not hwnd:
        raise SystemExit(0)

# 客户区
rc = wt.RECT()
ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rc))
pt = wt.POINT(0, 0)
ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))
region = (pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom)
print(f"客户区 {rc.right}x{rc.bottom} 屏幕region={region}")

cap = ScreenCapturer(region)
img = cap.grab()
cap.close()
if img is None:
    raise SystemExit("截图失败")

h, w = img.shape[:2]
print(f"截图 {w}x{h}")

# 保存一份供人工查看
import os
from PIL import Image
Image.fromarray(img[:, :, ::-1]).save("_live_clean.png")
print("已保存 _live_clean.png")


def match(rgb):
    r, g, b = rgb
    best, best_d = -1, float("inf")
    for i, (_, prgb, _) in enumerate(EXHIBITION_PALETTE):
        dr, dg, db = r - prgb[0], g - prgb[1], b - prgb[2]
        d = 2 * dr * dr + 4 * dg * dg + 3 * db * db
        if d < best_d:
            best_d, best = d, i
    return best, best_d


# 全图扫描高饱和色块（连通域）
zone = img
zh, zw = zone.shape[:2]
sat = (zone.max(axis=2) - zone.min(axis=2)) > 40
sat &= zone.mean(axis=2) > 50

visited = np.zeros((zh, zw), dtype=bool)
blobs = []
from collections import deque
for y in range(zh):
    for x in range(zw):
        if sat[y, x] and not visited[y, x]:
            q = deque([(y, x)])
            visited[y, x] = True
            px, py = [], []
            while q:
                yy, xx = q.popleft()
                px.append(xx); py.append(yy)
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    ny, nx = yy + dy, xx + dx
                    if 0 <= ny < zh and 0 <= nx < zw and sat[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        q.append((ny, nx))
            if len(px) > 500:
                cx = sum(px) // len(px)
                cy = sum(py) // len(py)
                patch = img[cy - 10:cy + 11, cx - 10:cx + 11]
                rgb = patch.reshape(-1, 3).mean(axis=0)
                idx, d = match(rgb)
                code = EXHIBITION_PALETTE[idx][0]
                blobs.append((cx, cy, idx, code, d, tuple(int(v) for v in rgb)))

blobs.sort(key=lambda b: (b[1], b[0]))
print(f"检测到 {len(blobs)} 个色块 (x, y, idx, code, d, 采样RGB):")
for cx, cy, idx, code, d, s in blobs:
    print(f"  x={cx} y={cy}  idx={idx:2d} {code} d={d:.0f} 采样RGB={s}")

print("\n行优先排列:")
ys = sorted(set(b[1] // 40 for b in blobs))
for yk in ys:
    rowblobs = [b for b in blobs if b[1] // 40 == yk]
    rowblobs.sort(key=lambda b: b[0])
    ymid = rowblobs[0][1]
    codes = " ".join(f"{b[2]:2d}:{b[3]}" for b in rowblobs)
    print(f"  y~{ymid}: {codes}")
