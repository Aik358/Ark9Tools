# -*- coding: utf-8 -*-
"""验证：大画布边框 + 等间距网格结构检测（抗 HDR 偏色，分辨率无关）"""
import numpy as np
from PIL import Image


def load(p):
    return np.asarray(Image.open(p).convert("RGB"))


def edge_projection(gray):
    """Sobel 边缘 + 行/列投影。返回 (row_proj, col_proj)，值 = 该行/列边缘像素数"""
    g = gray.astype(np.float32)
    gy = np.abs(np.diff(g, axis=0))  # 垂直梯度 → 横向边缘（横线）
    gx = np.abs(np.diff(g, axis=1))  # 水平梯度 → 纵向边缘（竖线）
    # 阈值化边缘
    ey = (gy > 25).sum(axis=1)  # 每行横向边缘像素数
    ex = (gx > 25).sum(axis=0)  # 每列纵向边缘像素数
    # 平滑
    k = np.ones(5) / 5
    ey = np.convolve(ey, k, mode="same")
    ex = np.convolve(ex, k, mode="same")
    return ey, ex


def find_strong_lines(proj, min_val, sep=20):
    """找投影中显著峰值（大边框线位置），peak 排序"""
    n = len(proj)
    peaks = []
    for i in range(1, n - 1):
        if proj[i] >= proj[i - 1] and proj[i] >= proj[i + 1] and proj[i] > min_val:
            peaks.append((i, float(proj[i])))
    peaks.sort(key=lambda t: -t[1])
    # 去重（相邻 sep 内只留最强）
    out = []
    for i, v in peaks:
        if all(abs(i - o[0]) > sep for o in out):
            out.append((i, v))
    return out


def main():
    for name in ("calibration_capture.png", "live_capture.png"):
        print("=" * 66)
        print(f"### {name}")
        img = load(name)
        h, w = img.shape[:2]
        gray = img.mean(axis=2).astype(np.float32)
        ey, ex = edge_projection(gray)

        # ---- 画布边框候选：整图最强横/竖线 ----
        hy = find_strong_lines(ey, min_val=ex.max() * 0.25, sep=15)
        vx = find_strong_lines(ex, min_val=ey.max() * 0.25, sep=15)
        print(f"最强横线(y): {[(i, int(v)) for i, v in hy[:10]]}")
        print(f"最强竖线(x): {[(i, int(v)) for i, v in vx[:10]]}")

        # ---- 色板区域检测（右侧规则方块）----
        # 用"相邻色差"找方块边界：右半屏
        g = img.astype(np.float32)
        right = g[:, w // 2:]
        dx = np.abs(np.diff(right, axis=1)).sum(axis=2).mean(axis=0)
        dy = np.abs(np.diff(right, axis=0)).sum(axis=2).mean(axis=1)
        k3 = np.ones(3) / 3
        dx = np.convolve(dx, k3, mode="same")
        dy = np.convolve(dy, k3, mode="same")
        px = find_strong_lines(dx, min_val=dx.max() * 0.3, sep=10)
        py = find_strong_lines(dy, min_val=dy.max() * 0.3, sep=10)
        print(f"右半屏色差竖边界(全局x): {[(int(w / 2 + i), int(v)) for i, v in px[:8]]}")
        print(f"右半屏色差横边界(全局y): {[(int(i), int(v)) for i, v in py[:8]]}")
        print()


if __name__ == "__main__":
    main()
