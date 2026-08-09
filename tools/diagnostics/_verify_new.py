# -*- coding: utf-8 -*-
"""验证改造后的检测：边框结构锚点 + ref 尺寸"""
import numpy as np
from PIL import Image
from calibration import detect_canvas_from_screenshot, detect_palette_from_screenshot


def load(p):
    return np.asarray(Image.open(p).convert("RGB"))


def main():
    for name in ("calibration_capture.png", "live_capture.png"):
        print("=" * 60)
        print(f"### {name}")
        img = load(name)
        h, w = img.shape[:2]
        print(f"尺寸 {w}x{h}")

        cal = detect_canvas_from_screenshot(img)
        if cal is not None:
            ox, oy = cal.canvas_origin
            print(f"画布 OK: origin=({ox},{oy}) cell={cal.canvas_cell_w}x{cal.canvas_cell_h} "
                  f"ref=({cal.ref_w},{cal.ref_h})")
            # 相对坐标
            print(f"  相对: left={ox/w:.3f} top={oy/h:.3f} "
                  f"right={(ox+cal.canvas_cell_w*23)/w:.3f} bottom={(oy+cal.canvas_cell_h*23)/h:.3f}")
        else:
            print("画布: 未检测到")

        pal = detect_palette_from_screenshot(img)
        if pal is not None:
            print(f"色板 OK: left={pal.palette_left} top={pal.palette_top} "
                  f"gaps=({pal.palette_col_gap},{pal.palette_row_gap}) rows={pal.palette_visible_rows}")
            print(f"  相对: left={pal.palette_left/w:.3f} top={pal.palette_top/h:.3f}")
        else:
            print("色板: 未检测到")

        # remap 测试：假设换到 1280x720
        if cal is not None and pal is not None:
            from calibration import Calibration
            merged = Calibration()
            merged.from_dict({**cal.to_dict(), **pal.to_dict()})
            # 修正：pal 的 canvas 字段覆盖了 cal 的，手动恢复
            merged.canvas_origin = cal.canvas_origin
            merged.canvas_cell_w = cal.canvas_cell_w
            merged.canvas_cell_h = cal.canvas_cell_h
            m = merged.remap(1280, 720)
            print(f"  remap→1280x720: 画布origin={m.canvas_origin} cell={m.canvas_cell_w} "
                  f"色板left={m.palette_left} top={m.palette_top}")
        print()


if __name__ == "__main__":
    main()
