# -*- coding: utf-8 -*-
"""验证 pixelate 解码是否存在反色/通道错乱：用已知纯色图测试"""
import numpy as np
from PIL import Image
from palette import EXHIBITION_PALETTE
from pixelate import pixelate

# 已知色 → 预期索引（依据 EXHIBITION_PALETTE 最近色）
tests = [
    ("白", (255, 255, 255), 0),     # X01
    ("黑", (0, 0, 0), 1),            # X02
    ("纯蓝", (0, 0, 255), None),     # 期望蓝系
    ("纯黄", (255, 255, 0), None),   # 期望黄系
    ("纯绿", (0, 255, 0), None),     # 期望绿系
    ("纯红", (255, 0, 0), None),     # 期望红系
]

for name, rgb, exp_idx in tests:
    img = Image.new("RGB", (96, 96), rgb)
    img.save("_tmp_test.png")
    mat = pixelate("_tmp_test.png", dither="none", flatten_white=False)
    # 统计所有格索引
    cnt = {}
    for v in mat.flatten():
        cnt[int(v)] = cnt.get(int(v), 0) + 1
    top = sorted(cnt.items(), key=lambda t: -t[1])[:3]
    desc = []
    for idx, c in top:
        code, crgb, cname = EXHIBITION_PALETTE[idx]
        desc.append(f"idx{idx}={code}{crgb}{cname}x{c}")
    print(f"{name}{rgb} -> {'反色?' if top and EXHIBITION_PALETTE[top[0][0]][1][0] < 40 and (255-rgb[0]) > 200 else ''} {'/'.join(desc)}")

import os
os.remove("_tmp_test.png")
