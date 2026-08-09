# -*- coding: utf-8 -*-
"""分析校准截图：检测真实画布网格线与配置对比，定位"图集中在左上象限"问题"""
import json
import os
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(BASE, "calibration_capture.png")


def detect_grid_lines(gray, axis):
    """检测 axis=0 时横向线的 y 坐标，axis=1 时纵向线的 x 坐标"""
    if axis == 0:  # 横向线：行投影，每行求垂直方向的边缘强度
        proj = np.abs(np.diff(gray.astype(np.int16), axis=0)).mean(axis=1)
    else:          # 纵向线：列投影
        proj = np.abs(np.diff(gray.astype(np.int16), axis=1)).mean(axis=0)
    # 平滑 + 峰值检测
    from scipy.ndimage import uniform_filter1d
    sm = uniform_filter1d(proj, 3)
    thr = max(sm.mean() + 2.5 * sm.std(), 1.0)
    peaks = []
    i = 1
    while i < len(sm) - 1:
        if sm[i] > thr and sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1]:
            # 合并相邻峰
            j = i
            while j + 1 < len(sm) and sm[j + 1] > thr * 0.6 and sm[j + 1] >= sm[j]:
                j += 1
            peaks.append(int((i + j) / 2))
            i = j + 1
        else:
            i += 1
    return peaks


def main():
    try:
        import cv2
    except ImportError:
        print("需要 opencv-python")
        return
    img = cv2.imread(IMG, cv2.IMREAD_COLOR)
    if img is None:
        print("无法读取截图:", IMG)
        return
    h, w = img.shape[:2]
    print("截图尺寸: %dx%d" % (w, h))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    xs = detect_grid_lines(gray, axis=1)
    ys = detect_grid_lines(gray, axis=0)
    print("检测到纵向线 %d 条: %s" % (len(xs), xs[:40]))
    print("检测到横向线 %d 条: %s" % (len(ys), ys[:40]))

    # 找等间距段
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
        return best_start, best_start + best_len, best_gap

    sx = find_span(xs)
    sy = find_span(ys)
    if sx:
        s, e, gap = sx
        print("纵向等间距段: 起点线 x=%d, 终点线 x=%d, 间距=%d, 线数=%d" % (xs[s], xs[e], gap, e - s + 1))
    if sy:
        s, e, gap = sy
        print("横向等间距段: 起点线 y=%d, 终点线 y=%d, 间距=%d, 线数=%d" % (ys[s], ys[e], gap, e - s + 1))

    # 配置对比
    cfg = json.load(open(os.path.join(BASE, "pixel_painter_config.json"), encoding="utf-8"))
    cal = cfg["calibration"]
    ox, oy = cal["canvas_origin"]
    cw, ch = cal["canvas_cell_w"], cal["canvas_cell_h"]
    print("\n配置校准: origin=(%d,%d) cell=%dx%d" % (ox, oy, cw, ch))
    print("配置画布范围: x=[%d,%d] y=[%d,%d] (帧内/客户区坐标)" % (ox, ox + 24 * cw, oy, oy + 24 * ch))


if __name__ == "__main__":
    main()
