from PIL import Image
from pixelate import pixelate
from palette import EXHIBITION_PALETTE, WHITE_PALETTE_INDEX

path = "_verify_palette_input.png"
img = Image.new("RGB", (64, 64), (255, 255, 255))
p = img.load()
for y in range(16, 48):
    for x in range(8, 24):
        p[x, y] = (0, 0, 0)
for y in range(16, 48):
    for x in range(40, 56):
        p[x, y] = (0, 200, 180)
img.save(path)
mat = pixelate(path, dither="none", flatten_white=True)
print("palette_size", len(EXHIBITION_PALETTE))
print("white_index", WHITE_PALETTE_INDEX)
print("matrix_min_max", int(mat.min()), int(mat.max()))
print("white_cells", int((mat == WHITE_PALETTE_INDEX).sum()))
print("black_cells", int((mat == 0).sum()))
print("nonwhite_items", sum(1 for v in mat.flat if int(v) != WHITE_PALETTE_INDEX))
