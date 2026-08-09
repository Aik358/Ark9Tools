# -*- coding: utf-8 -*-
"""探测长水平/垂直线段（画布大边框），基于边缘图 run-length"""
import numpy as np
from PIL import Image


def load(p):
    return np.asarray(Image.open(p).convert("RGB"))


def edge_map(gray, thresh=30):
    g = gray.astype(np.float32)
    gy = np.abs(np.diff(g, axis=0))  # 横向边缘
    gx = np.abs(np.diff(g, axis=1))  # 纵向边缘
    ey = np.pad((gy > thresh).astype(np.uint8), ((1, 0), (0, 0)))
    ex = np.pad((gx > thresh).astype(np.uint8), ((0, 0), (1, 0)))
    return ey, ex


def horizontal_runs(ey, min_run=150):
    """找横向边缘长 run 的行。返回 [(y, x_start, x_end, len)]"""
    h, w = ey.shape
    runs = []
    for y in range(h):
        row = ey[y]
        x = 0
        while x < w:
            if row[x]:
                x2 = x
                while x2 < w and row[x2]:
                    x2 += 1
                if x2 - x >= min_run:
                    runs.append((y, x, x2 - 1, x2 - x))
                x = x2
            else:
                x += 1
    return runs


def vertical_runs(ex, min_run=150):
    """找纵向边缘长 run 的列"""
    h, w = ex.shape
    runs = []
    for x in range(w):
        col = ex[:, x]
        y = 0
        while y < h:
            if col[y]:
                y2 = y
                while y2 < h and col[y2]:
                    y2 += 1
                if y2 - y >= min_run:
                    runs.append((x, y, y2 - 1, y2 - y))
                y = y2
            else:
                y += 1
    return runs


def cluster_by_pos(items, tol=6):
    """按位置聚类（容差 tol），返回均值列表"""
    items = sorted(items)
    out = []
    cur = [items[0]]
    for v in items[1:]:
        if v - np.mean(cur) <= tol:
            cur.append(v)
        else:
            out.append(int(round(np.mean(cur))))
            cur = [v]
    out.append(int(round(np.mean(cur))))
    return out


def main():
    for name in ("calibration_capture.png", "live_capture.png"):
        print("=" * 66)
        print(f"### {name}")
        img = load(name)
        h, w = img.shape[:2]
        gray = img.mean(axis=2).astype(np.float32)
        ey, ex = edge_map(gray, thresh=30)
        hr = horizontal_runs(ey, min_run=w // 5)
        vr = vertical_runs(ex, min_run=h // 5)
        # 统计每行/列的最长 run
        row_best = {}
        for y, xs, xe, ln in hr:
            if y not in row_best or ln > row_best[y][0]:
                row_best[y] = (ln, xs, xe)
        col_best = {}
        for x, ys, ye, ln in vr:
            if x not in col_best or ln > col_best[x][0]:
                col_best[x] = (ln, ys, ye)

        # 找出显著的行（long run > w*0.4）
        strong_rows = [(y, v[0], v[1], v[2]) for y, v in sorted(row_best.items()) if v[0] > w * 0.4]
        strong_cols = [(x, v[0], v[1], v[2]) for x, v in sorted(col_best.items()) if v[0] > h * 0.4]
        print(f"强水平长线({len(strong_rows)}):")
        for y, ln, xs, xe in strong_rows[:30]:
            print(f"  y={y} len={ln} x=[{xs},{xe}]")
        print(f"强垂直长线({len(strong_cols)}):")
        for x, ln, ys, ye in strong_cols[:30]:
            print(f"  x={x} len={ln} y=[{ys},{ye}]")
        print()


if __name__ == "__main__":
    main()
