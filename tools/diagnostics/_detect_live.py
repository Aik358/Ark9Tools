# -*- coding: utf-8 -*-
"""在当前实时截图上运行正式检测算法，对比配置校准"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from PIL import Image
import cv2

from calibration import (detect_canvas_from_screenshot,
                         detect_palette_from_screenshot, Calibration)

a = np.asarray(Image.open("live_capture.png").convert("RGB"))
img = a[:, :, ::-1].copy()   # RGB -> BGR

cfg = json.load(open("pixel_painter_config.json", encoding="utf-8"))
cal = cfg.get("calibration", {})
print("配置 calibration:", json.dumps(cal, ensure_ascii=False))

# 1) 正式画布检测
cal_c = detect_canvas_from_screenshot(img)
print("\n[正式检测] 画布:", None if cal_c is None else (
    "origin=%s cell=%dx%d" % (cal_c.canvas_origin, cal_c.canvas_cell_w, cal_c.canvas_cell_h)))

# 2) 正式色板检测
cal_p = detect_palette_from_screenshot(img, canvas=cal_c)
print("[正式检测] 色板:", None if cal_p is None else (
    "left=%d top=%d col_gap=%d row_gap=%d rows=%d" % (
        cal_p.palette_left, cal_p.palette_top,
        cal_p.palette_col_gap, cal_p.palette_row_gap, cal_p.palette_visible_rows)))

# 3) 直接用 Canny+Hough 检测全部等间距水平/垂直线的可能网格（低阈值，宽松）
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
for (lo, hi, ml, th) in [(30, 120, 80, 30), (20, 100, 60, 25), (15, 80, 50, 20)]:
    edges = cv2.Canny(gray, lo, hi)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, threshold=th,
                            minLineLength=ml, maxLineGap=4)
    if lines is None:
        print("  Canny(%d,%d) 无线" % (lo, hi))
        continue
    h_l, v_l = [], []
    for x1, y1, x2, y2 in lines[:, 0]:
        if abs(y1-y2) < 3:
            h_l.append(int((y1+y2)/2))
        elif abs(x1-x2) < 3:
            v_l.append(int((x1+x2)/2))
    h_l, v_l = sorted(set(h_l)), sorted(set(v_l))
    print("  Canny(%d,%d) 横线数=%d 纵线数=%d" % (lo, hi, len(h_l), len(v_l)))
    print("    横:", h_l[:40])
    print("    纵:", v_l[:40])
