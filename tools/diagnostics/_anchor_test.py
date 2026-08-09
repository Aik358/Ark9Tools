# -*- coding: utf-8 -*-
"""结构锚点检测原型验证：
- 画布：左半屏内找等间距网格线序列（25 条线，间距=格宽），排除顶部导航区
- 色板：右半屏内找规则方块网格（色差边界）
- 全部输出相对坐标（分辨率无关）
"""
import numpy as np
from PIL import Image


def load(p):
    return np.asarray(Image.open(p).convert("RGB"))


def dedup(arr, tol=3):
    arr = sorted(arr)
    if not arr:
        return []
    out = [arr[0]]
    for v in arr[1:]:
        if v - out[-1] > tol:
            out.append(v)
    return out


def find_grid(arr, need=15, tol_ratio=0.12):
    """拟合等间距网格。返回 (start, end, gap, count) 或 None"""
    if len(arr) < need:
        return None
    gaps = [arr[i + 1] - arr[i] for i in range(len(arr) - 1)]
    from collections import Counter
    cnt = Counter()
    for g in gaps:
        if g > 2:
            cnt[round(g)] += 1
    if not cnt:
        return None
    best_gap = cnt.most_common(1)[0][0]
    tol = max(3, best_gap * tol_ratio)

    # 最长连续匹配段
    best_start, best_len, best_total = 0, 0, 0
    cur_start, cur_len, cur_total = 0, 0, 0
    for i in range(len(arr) - 1):
        g = arr[i + 1] - arr[i]
        if abs(g - best_gap) <= tol:
            if cur_len == 0:
                cur_start = i
            cur_len += 1
            cur_total += g
        else:
            if cur_len > best_len:
                best_len, best_start, best_total = cur_len, cur_start, cur_total
            cur_len = cur_total = 0
    if cur_len > best_len:
        best_len, best_start, best_total = cur_len, cur_start, cur_total
    if best_len < need - 1:
        return None
    avg = best_total / best_len
    return arr[best_start], arr[best_start + best_len], avg, best_len + 1


def detect_canvas(img):
    """画布检测：左半屏 + 等间距网格（相对坐标）"""
    h, w = img.shape[:2]
    gray = img.mean(axis=2).astype(np.float32)

    # 四象限先验：画布在窗口左部
    zone_x = max(0, int(w * 0.05))
    zone_xe = int(w * 0.55)
    zone_y = int(h * 0.12)  # 排除顶部导航
    sub = gray[zone_y:, zone_x:zone_xe]
    sh, sw = sub.shape

    # 竖线：x 方向梯度 → 列投影（排除顶部后全行累计）
    gx = np.abs(np.diff(sub, axis=1)).sum(axis=0)
    # 横线：y 方向梯度 → 行投影
    gy = np.abs(np.diff(sub, axis=0)).sum(axis=1)

    k3 = np.ones(3) / 3
    gx = np.convolve(gx, k3, mode="same")
    gy = np.convolve(gy, k3, mode="same")

    def peaks(arr):
        n = len(arr)
        ps = []
        for i in range(1, n - 1):
            if arr[i] >= arr[i - 1] and arr[i] >= arr[i + 1] and arr[i] > arr.max() * 0.15:
                ps.append(i)
        return ps

    # 竖向候选（竖线位置，相对子图）
    vx = dedup(peaks(gx))
    hy = dedup(peaks(gy))

    gv = find_grid(vx, need=15)
    gh = find_grid(hy, need=15)
    if gv is None or gh is None:
        return None, len(vx), len(hy), gv, gh
    v_start, v_end, v_gap, v_n = gv
    h_start, h_end, h_gap, h_n = gh
    # 格宽格高应接近（方形格）
    if not (0.75 < v_gap / h_gap < 1.33):
        return None, len(vx), len(hy), gv, gh

    # 绝对坐标（还原 zone 偏移）
    left = v_start + zone_x
    right = v_end + zone_x
    top = h_start + zone_y
    bottom = h_end + zone_y
    return {
        "origin": (left, top),
        "cell": (round(v_gap), round(h_gap)),
        "n_lines": (v_n, h_n),
        "right": right, "bottom": bottom,
        # 相对坐标
        "rel_left": left / w, "rel_top": top / h,
        "rel_right": right / w, "rel_bottom": bottom / h,
    }, len(vx), len(hy), gv, gh


def detect_palette(img):
    """色板检测：右半屏 + 色差边界等间距网格"""
    h, w = img.shape[:2]
    g = img.astype(np.float32)
    zone_x = int(w * 0.5)
    zone_y = int(h * 0.12)
    sub = g[zone_y:, zone_x:]
    sh, sw = sub.shape

    # 相邻色差（方块边界）
    dx = np.abs(np.diff(sub, axis=1)).sum(axis=2).mean(axis=0)
    dy = np.abs(np.diff(sub, axis=0)).sum(axis=2).mean(axis=1)
    k3 = np.ones(3) / 3
    dx = np.convolve(dx, k3, mode="same")
    dy = np.convolve(dy, k3, mode="same")

    def peaks(arr):
        n = len(arr)
        ps = []
        for i in range(1, n - 1):
            if arr[i] >= arr[i - 1] and arr[i] >= arr[i + 1] and arr[i] > arr.max() * 0.2:
                ps.append(i)
        return ps

    vx = dedup(peaks(dx), tol=6)
    hy = dedup(peaks(dy), tol=6)

    gv = find_grid(vx, need=4)
    gh = find_grid(hy, need=3)
    if gv is None or gh is None:
        return None
    v_start, v_end, v_gap, v_n = gv
    h_start, h_end, h_gap, h_n = gh
    if not (40 < v_gap < 150 and 40 < h_gap < 150):
        return None
    if not (0.7 < v_gap / h_gap < 1.4):
        return None
    return {
        "origin": (v_start + zone_x, h_start + zone_y),
        "cell": (round(v_gap), round(h_gap)),
        "n": (v_n, h_n),
        "rel_left": (v_start + zone_x) / w, "rel_top": (h_start + zone_y) / h,
    }


def main():
    for name in ("calibration_capture.png", "live_capture.png"):
        print("=" * 64)
        print(f"### {name}")
        img = load(name)
        h, w = img.shape[:2]
        print(f"尺寸 {w}x{h}")

        cv = detect_canvas(img)
        if cv[0]:
            c = cv[0]
            print(f"画布 OK: origin={c['origin']} cell={c['cell']} nlines={c['n_lines']}")
            print(f"  相对: left={c['rel_left']:.3f} top={c['rel_top']:.3f} "
                  f"right={c['rel_right']:.3f} bottom={c['rel_bottom']:.3f}")
        else:
            nv, nh, gv, gh = cv[1], cv[2], cv[3], cv[4]
            print(f"画布 未检测到 (竖线峰={nv}, 横线峰={nh})")
            print(f"  竖线网格: {gv}")
            print(f"  横线网格: {gh}")

        p = detect_palette(img)
        if p:
            print(f"色板 OK: origin={p['origin']} cell={p['cell']} n={p['n']}")
            print(f"  相对: left={p['rel_left']:.3f} top={p['rel_top']:.3f}")
        else:
            print("色板 未检测到")
        print()


if __name__ == "__main__":
    main()
