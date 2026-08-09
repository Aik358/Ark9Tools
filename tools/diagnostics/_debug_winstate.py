# -*- coding: utf-8 -*-
"""实时诊断：当前游戏窗口客户区尺寸 vs 配置校准，并检测当前画布网格实际 cell 尺寸"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from win32_capture import ScreenCapturer  # noqa

user32 = ctypes.windll.user32


def get_window_info(hwnd):
    rc = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    crc = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(crc))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    return {
        "title": title.value,
        "window_rect": (rc.left, rc.top, rc.right, rc.bottom),
        "client_size": (crc.right, crc.bottom),
        "client_origin_screen": (pt.x, pt.y),
    }


def detect_grid(gray, axis):
    if axis == 0:
        proj = np.abs(np.diff(gray.astype(np.int16), axis=0)).mean(axis=1)
    else:
        proj = np.abs(np.diff(gray.astype(np.int16), axis=1)).mean(axis=0)
    from scipy.ndimage import uniform_filter1d
    sm = uniform_filter1d(proj, 3)
    thr = max(sm.mean() + 2.5 * sm.std(), 1.0)
    peaks = []
    i = 1
    while i < len(sm) - 1:
        if sm[i] > thr and sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1]:
            j = i
            while j + 1 < len(sm) and sm[j + 1] > thr * 0.6 and sm[j + 1] >= sm[j]:
                j += 1
            peaks.append(int((i + j) / 2))
            i = j + 1
        else:
            i += 1
    return peaks


def find_span(arr):
    if len(arr) < 3:
        return None
    gaps = np.diff(arr)
    from collections import Counter
    cnt = Counter()
    for g in gaps:
        if g > 2:
            for b in (round(g - 2), round(g), round(g + 2)):
                cnt[b] += 1
    if not cnt:
        return None
    best_gap = cnt.most_common(1)[0][0]
    tol = max(3, best_gap // 5)
    best_start, best_len = 0, 0
    cur_start, cur_len = 0, 0
    for i in range(len(arr) - 1):
        g = arr[i + 1] - arr[i]
        if abs(g - best_gap) <= tol:
            if cur_len == 0:
                cur_start = i
            cur_len += 1
        else:
            if cur_len > best_len:
                best_len, best_start = cur_len, cur_start
            cur_len = 0
    if cur_len > best_len:
        best_len, best_start = cur_len, cur_start
    return arr[best_start], arr[best_start + best_len], best_gap, best_len + 1


def main():
    cfg = json.load(open(os.path.join(BASE, "pixel_painter_config.json"), encoding="utf-8"))
    hwnd = cfg.get("window_hwnd")
    region = cfg.get("window_region")
    cal = cfg.get("calibration", {})

    print("配置 window_region:", region, "宽=%d 高=%d" % (region[2] - region[0], region[3] - region[1]))
    print("配置 canvas_origin:", cal.get("canvas_origin"), "cell:", cal.get("canvas_cell_w"), "x", cal.get("canvas_cell_h"))

    if not hwnd or not user32.IsWindow(hwnd):
        print("!! 窗口无效或不存在: hwnd=%s" % hwnd)
        print("请先运行主程序并选择游戏窗口。")
        return

    info = get_window_info(hwnd)
    print("当前窗口: %s" % info["title"])
    print("  窗口矩形(屏幕):", info["window_rect"])
    print("  客户区尺寸:", info["client_size"])
    print("  客户区原点(屏幕):", info["client_origin_screen"])

    # 与配置对比
    cur_w, cur_h = info["client_size"]
    cfg_w, cfg_h = region[2] - region[0], region[3] - region[1]
    print()
    if (cur_w, cur_h) == (cfg_w, cfg_h):
        print(">>> 客户区尺寸与配置一致 ✓")
    else:
        print(">>> !! 客户区尺寸与配置不一致: 当前 %dx%d vs 配置 %dx%d" % (cur_w, cur_h, cfg_w, cfg_h))
        print("    若窗口被拉伸/缩放，Unity 画布会等比变化，点击会集中在左上角或偏移！")

    # 用实时客户区 region 截图检测当前画布网格（配置 region 可能已失效）
    cur_region = (info["client_origin_screen"][0], info["client_origin_screen"][1],
                  info["client_origin_screen"][0] + cur_w, info["client_origin_screen"][1] + cur_h)
    print("\n实时客户区 region:", cur_region)
    try:
        cap = ScreenCapturer(tuple(cur_region))
        img = cap.grab()
        cap.close()
    except Exception as e:
        print("截图失败:", e)
        return
    if img is None:
        print("截图失败: None")
        return
    h, w = img.shape[:2]
    print("当前截图尺寸: %dx%d (实时客户区)" % (w, h))
    gray = img[:, :, 1] if img.ndim == 3 else img
    xs = detect_grid(gray, axis=1)
    ys = detect_grid(gray, axis=0)
    sx = find_span(xs)
    sy = find_span(ys)
    print("纵向线:", xs[:30])
    print("横向线:", ys[:30])
    if sx:
        print("纵向等间距段: 起线=%d 末线=%d 间距=%d 线数=%d" % sx)
        print("  -> 实际画布 cell_w ≈ %d (配置 %d)" % (sx[2], cal.get("canvas_cell_w")))
    if sy:
        print("横向等间距段: 起线=%d 末线=%d 间距=%d 线数=%d" % sy)
        print("  -> 实际画布 cell_h ≈ %d (配置 %d)" % (sy[2], cal.get("canvas_cell_h")))
    if sx and sy:
        print()
        print(">>> 若实际 cell 明显大于配置 cell，说明窗口被放大了，需要重新校准！")


if __name__ == "__main__":
    main()
