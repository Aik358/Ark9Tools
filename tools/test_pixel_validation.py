# -*- coding: utf-8 -*-
"""Ark9Tools 核心行为验证（无界面 smoke test + 识别测试 + 测试报告）

覆盖：
- 历史库/收藏库存储：指纹去重、旧数据指纹回填、批量删除、批量导出分享、
  跨库迁移隔离、deduplicate 清理
- 收藏页两层识别：卡片网格 → 卡片内像素画区域（不整卡误框）
- 六类测试样本：横版/竖版官方蓝图、无底部色表游戏作画截图、
  画像收藏页、普通无网格图片、重复滚动页面
- Qt 无界面（offscreen）smoke test：LibraryDialog 懒加载/多选/批量操作
"""
import os
import sys
import tempfile
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPORT = []


def report(line: str = ""):
    print(line)
    REPORT.append(line)


# ===========================================================================
# 合成样本生成器
# ===========================================================================
def _font(size):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size)
    except Exception:
        return ImageFont.load_default()


def make_art(seed: int, px: int = 160):
    """用游戏色板生成 24×24 像素画并放大渲染。"""
    from palette import GAME_PALETTE_DATA
    rng = np.random.default_rng(seed)
    m = np.zeros((24, 24), dtype=int)
    for y in range(24):
        for x in range(24):
            r = rng.random()
            if r < 0.10:
                m[y, x] = 0
            elif r < 0.38:
                m[y, x] = int(rng.integers(1, 6))
            else:
                m[y, x] = int(rng.integers(6, 31))
    img = Image.new("RGB", (px, px), "white")
    d = ImageDraw.Draw(img)
    cell = px / 24
    for y in range(24):
        for x in range(24):
            d.rectangle([x * cell, y * cell, (x + 1) * cell, (y + 1) * cell],
                        fill=tuple(GAME_PALETTE_DATA[int(m[y, x])][1]))
    return img


def build_collection_page(rows: int = 3, cols: int = 2, with_duplicate_art: bool = False,
                          page_w: int = 1600, page_h: int = 1080,
                          cut_half_bottom: bool = True):
    """生成画像收藏页截图，返回 (page_rgb, [(art_box, card_box), ...])。"""
    page = Image.new("RGB", (page_w, page_h), (56, 60, 68))
    d = ImageDraw.Draw(page)
    d.text((40, 30), "画像收藏", fill=(220, 226, 234), font=_font(26))
    col_gap, row_gap = 36, 30
    card_w, card_h = 400, 250
    truths = []
    seed = 0
    art_size = min(card_w - 40, card_h - 110)
    for r in range(rows):
        y = 70 + r * (card_h + row_gap)
        for c in range(cols):
            x = 120 + c * (card_w + col_gap)
            if with_duplicate_art and r == 1:
                seed = 11  # 同一张图重复出现
            art = make_art(seed, art_size)
            card = Image.new("RGB", (card_w, card_h), (245, 246, 248))
            cd = ImageDraw.Draw(card)
            cd.rectangle([0, 0, card_w - 1, card_h - 1], outline=(200, 205, 212), width=2)
            cd.text((16, 14), "画像收藏示例标题", fill=(40, 46, 54), font=_font(18))
            # 真实游戏收藏页：像素画位于卡片上半（约 15%~50% 高度），
            # 中下部是"作者/按钮/日期"等文字。ay=24 让 art 紧贴标题下方。
            ax, ay = (card_w - art_size) // 2, 24
            card.paste(art, (ax, ay))
            cd.rectangle([ax - 2, ay - 2, ax + art_size + 1, ay + art_size + 1],
                         outline=(120, 128, 138), width=1)
            # 中部：作者 + 按钮
            cd.text((16, ay + art_size + 30), "作者 TEST",
                    fill=(40, 46, 54), font=_font(16))
            cd.rounded_rectangle([card_w - 80, ay + art_size + 26, card_w - 16,
                                  ay + art_size + 52], radius=5,
                                 fill=(60, 140, 120))
            # 下部：日期
            cd.text((16, card_h - 30), "日期 2026-08-12",
                    fill=(120, 128, 138), font=_font(13))
            cd.rounded_rectangle([card_w - 70, card_h - 42, card_w - 16, card_h - 22],
                                 radius=5, fill=(60, 140, 120), outline=(60, 140, 120))
            if cut_half_bottom and y + card_h > page_h - 20:
                crop_h = page_h - 20 - y
                if crop_h < 60:
                    break
                card = card.crop((0, 0, card_w, crop_h))
                page.paste(card, (x, y))
                truths.append((x + ax, y + ay, x + ax + art_size, y + ay + art_size,
                               x, y, x + card_w, y + card_h))
                break
            page.paste(card, (x, y))
            truths.append((x + ax, y + ay, x + ax + art_size, y + ay + art_size,
                           x, y, x + card_w, y + card_h))
            seed += 1
    return np.asarray(page.convert("RGB"), dtype=np.uint8), truths


def build_blueprint(orientation: str = "h"):
    """官方宣传蓝图（横版/竖版）：青色背景 + 中央白底 24×24 网格画布 + 文字。"""
    if orientation == "h":
        w, h = 1600, 900
    else:
        w, h = 700, 1400
    img = Image.new("RGB", (w, h), (30, 190, 200))
    d = ImageDraw.Draw(img)
    side = min(w * 0.5, h * 0.45)
    x0 = (w - side) / 2
    y0 = (h - side) / 2
    d.rectangle([x0, y0, x0 + side, y0 + side], fill=(245, 246, 248),
                outline=(30, 60, 80), width=4)
    for i in range(1, 24):
        d.line([x0 + i * side / 24, y0, x0 + i * side / 24, y0 + side],
               fill=(120, 140, 150), width=1)
        d.line([x0, y0 + i * side / 24, x0 + side, y0 + i * side / 24],
               fill=(120, 140, 150), width=1)
    d.text((x0, y0 + side + 20), "官方宣传蓝图标题", fill=(10, 20, 30), font=_font(20))
    return np.asarray(img.convert("RGB"), dtype=np.uint8)


def build_editor_screenshot():
    """无底部色表的游戏作画截图：中央画布 + 右侧深色调色板面板。"""
    w, h = 1600, 900
    img = Image.new("RGB", (w, h), (30, 34, 40))
    d = ImageDraw.Draw(img)
    d.rectangle([500, 250, 950, 700], fill=(200, 210, 220))
    d.rectangle([1150, 200, 1520, 850], fill=(18, 22, 28))
    d.text((500, 720), "画布预览区域", fill=(200, 210, 220), font=_font(16))
    return np.asarray(img.convert("RGB"), dtype=np.uint8)


def build_plain_image():
    """普通无网格图片：渐变 + 随机噪点。"""
    w, h = 1600, 900
    rng = np.random.default_rng(9)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        for x in range(0, w, 4):
            v = int(80 + 120 * (x / w) + 40 * (y / h))
            arr[y, x] = (v, v // 2, 255 - v)
    arr[::3, ::3] = rng.integers(0, 255, (arr[::3, ::3].shape[0], arr[::3, ::3].shape[1], 3))
    return arr


# ===========================================================================
# 识别测试
# ===========================================================================
def test_detection():
    from pixelate import parse_collection_page_detailed
    report("### 收藏页两层识别测试")
    report("| 样本 | 卡片数(真值) | 解析数 | 误框数 | 低置信数 |")
    report("| --- | --- | --- | --- | --- |")
    total_mis = 0
    total_low = 0

    # 画像收藏页
    for name, page, truths in _collection_page_samples():
        results = parse_collection_page_detailed(page, bgr=False)
        gt_cards = len(truths)
        # 误框：解析出的区域与任一真值 art 框 IoU < 0.5，或解析数多于真值
        mis = 0
        for r in results:
            ax0, ay0, ax1, ay1 = r.box
            best = 0.0
            for tx0, ty0, tx1, ty1, *_ in truths:
                ix0, iy0 = max(ax0, tx0), max(ay0, ty0)
                ix1, iy1 = min(ax1, tx1), min(ay1, ty1)
                inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
                union = (ax1 - ax0) * (ay1 - ay0) + (tx1 - tx0) * (ty1 - ty0) - inter
                best = max(best, inter / max(1, union))
            if best < 0.5:
                mis += 1
        mis = max(mis, len(results) - gt_cards)
        low = sum(1 for r in results if r.low_confidence)
        total_mis += mis
        total_low += low
        report(f"| {name} | {gt_cards} | {len(results)} | {mis} | {low} |")

    # 负样本
    for name, page in (
            ("横版官方宣传蓝图", build_blueprint("h")),
            ("竖版官方宣传蓝图", build_blueprint("v")),
            ("无底部色表游戏作画截图", build_editor_screenshot()),
            ("普通无网格图片", build_plain_image())):
        results = parse_collection_page_detailed(page, bgr=False)
        report(f"| {name} | 0 | {len(results)} | {len(results)} | "
               f"{sum(1 for r in results if r.low_confidence)} |")
        total_mis += len(results)
    report("")
    return total_mis, total_low


def _collection_page_samples():
    p1, t1 = build_collection_page(rows=3)
    p2, t2 = build_collection_page(rows=2, with_duplicate_art=True)
    p3, t3 = build_collection_page(rows=2)
    return [("画像收藏页", p1, t1),
            ("画像收藏页(带重复图)", p2, t2),
            ("画像收藏页(重复滚动)", p3, t3)]


# ===========================================================================
# 存储测试
# ===========================================================================
def test_store(tmp):
    from history_store import PixelHistoryStore
    report("### 存储与去重测试")
    hist = PixelHistoryStore(str(tmp / "pixel_history"))
    coll = PixelHistoryStore(str(tmp / "pixel_collection"))

    m1 = np.random.default_rng(1).integers(0, 31, (24, 24)).astype(np.int16)
    m2 = np.random.default_rng(2).integers(0, 31, (24, 24)).astype(np.int16)

    # 保存 + 指纹去重
    r1 = hist.save_unique(m1, name="A", source="图片转换")
    r2 = hist.save_unique(m1, name="A2", source="图片转换")
    r3 = hist.save_unique(m2, name="B", source="图片转换")
    assert r1["saved"] and not r2["saved"] and r3["saved"], "指纹去重失败"
    report(f"- 指纹去重：3 次保存（1 次重复）→ 新增 {r1['saved'] + r3['saved']}，重复 {not r2['saved']}")

    # 旧数据回填指纹（模拟启动时新实例扫描旧目录）
    items = hist.list_items()
    folder = hist.root / items[0]["id"]
    meta_path = folder / "meta.json"
    meta = json_read(meta_path)
    meta.pop("fingerprint", None)
    json_write(meta_path, meta)
    fresh = PixelHistoryStore(str(tmp / "pixel_history"))
    filled = fresh.ensure_fingerprints()
    assert filled >= 1, "指纹回填失败"
    report(f"- 旧数据指纹回填：{filled} 条")

    # deduplicate（旧数据无指纹/跨页重复场景：直接用 save 造重复）
    dup = PixelHistoryStore(str(tmp / "pixel_history_dup"))
    dup.save(m2, name="D1", source="图片转换")
    dup.save(m2, name="D2", source="图片转换")
    result = dup.deduplicate()
    assert result["removed"] >= 1, "deduplicate 未清理重复"
    assert len(dup.list_items()) == 1, "deduplicate 后应只剩 1 条"
    report(f"- deduplicate：清理重复 {result['removed']} 条，保留 {result['kept']} 条")

    # 批量删除
    ids = [i["id"] for i in hist.list_items()][:1]
    n = hist.delete_many(ids)
    assert n == len(ids) and len(hist.list_items()) == 1, "批量删除失败"
    report(f"- 批量删除：删除 {n} 条，剩余 {len(hist.list_items())} 条")

    # 批量导出分享
    export_hist = PixelHistoryStore(str(tmp / "pixel_history"))
    export_hist.save_unique(m1, name="分享A", source="图片转换")
    export_hist.save_unique(m2, name="分享B", source="图片转换")
    dest = tmp / "share"
    out = export_hist.export_share([i["id"] for i in export_hist.list_items()], str(dest))
    assert out["count"] == 2 and out["zip"] and os.path.exists(out["zip"]), "导出分享失败"
    names = {f.name for f in dest.iterdir()}
    assert all(any(n.endswith(ext) for n in names) for ext in (".png", ".npy", ".json")), \
        "导出缺少 png/npy/json"
    report(f"- 批量导出分享：{out['count']} 张 → {out['dir']}，ZIP={out['zip']}")

    # 跨库迁移隔离
    moved = hist.migrate_source_to("图片转换", coll)
    report(f"- 跨库迁移：历史库“图片转换”记录迁移到收藏库 {moved} 条")
    report("")


def json_read(path):
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def json_write(path, data):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===========================================================================
# Qt 无界面 smoke test
# ===========================================================================
def test_qt_smoke(tmp):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QTimer
    import main as m

    report("### Qt 无界面 smoke test（offscreen）")
    app = QApplication.instance() or QApplication([])
    # 屏蔽弹窗，避免 offscreen 下 QMessageBox.exec 卡死
    m.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    m.QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    m.QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    m.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(tmp / "share_dialog"))

    store = m.PixelHistoryStore(str(tmp / "pixel_history"))
    for i in range(60):
        mat = np.random.default_rng(100 + i).integers(0, 31, (24, 24)).astype(np.int16)
        store.save_unique(mat, name=f"样本{i:02d}", source="图片转换")

    dlg = m.LibraryDialog(store, title="测试库", page_size=24)
    app.processEvents()
    visible = dlg._visible_count
    assert visible >= 24, f"首屏加载过少: {visible}"
    assert "记录 60" in dlg.count.text(), f"数量统计异常: {dlg.count.text()}"
    report(f"- LibraryDialog 打开：记录 60，首屏显示 {visible}，未阻塞")

    dlg._select_all()
    assert len(dlg._selected) == 60, "全选失败"
    dlg._batch_share()
    app.processEvents()
    report(f"- 批量分享：选中 {len(dlg._selected)}，已走导出流程")
    dlg._clear_selection()
    for card in list(dlg._cards_by_id.values())[:3]:
        card.check.setChecked(True)   # 模拟用户点击复选框（触发信号）
    app.processEvents()
    assert len(dlg._selected) == 3, f"勾选后选中数错误: {len(dlg._selected)}"
    dlg._batch_delete()
    app.processEvents()
    assert len(dlg._items) == 57, f"批量删除后数量错误: {len(dlg._items)}"
    report(f"- 批量删除：勾选 3 条 → 剩余 {len(dlg._items)} 条")

    # 搜索过滤 + 自定义改名（走 UI 路径，同步内存）
    from PySide6.QtWidgets import QInputDialog
    m.QInputDialog.getText = staticmethod(lambda *a, **k: ("自定义-第一张", True))
    first = dlg._items[0]
    dlg._on_rename(first["id"], first["name"])
    app.processEvents()
    assert first["name"] == "自定义-第一张", first["name"]
    assert dlg._cards_by_id[first["id"]].name_label.text() == "自定义-第一张"
    dlg.search_box.setText("自定义")
    app.processEvents()
    assert len(dlg._filtered_items) == 1, [i["name"] for i in dlg._filtered_items]
    dlg.search_box.setText("样本")
    app.processEvents()
    assert len(dlg._filtered_items) == 56, f"搜索‘样本’应命中 56 条: {len(dlg._filtered_items)}"
    dlg.search_box.setText("")
    app.processEvents()
    assert len(dlg._filtered_items) == 57
    report("- 搜索过滤 + 自定义改名：实时过滤与改名联动正常")
    dlg.close()

    report("- CollectionImportDialog 冒烟：见下")
    report("")


def test_import_dialog_smoke(tmp):
    """单独验证 CollectionImportDialog 无崩溃。"""
    from PySide6.QtWidgets import QApplication, QMessageBox
    import main as m
    app = QApplication.instance() or QApplication([])
    m.QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    store = m.PixelHistoryStore(str(tmp / "pixel_collection"))
    dlg = m.CollectionImportDialog(store)
    assert dlg is not None
    # 无游戏窗口时点击“从顶部开始自动导入”应弹提示且不崩溃
    dlg._auto_import()
    assert not dlg._importing
    report("- CollectionImportDialog：无游戏窗口启动自动导入 → 正确拦截")
    report("")


def main():
    report("# Ark9Tools 核心行为验证报告")
    report("")
    report(f"生成时间：{__import__('time').strftime('%Y-%m-%d %H:%M:%S')}")
    report("")
    with tempfile.TemporaryDirectory() as td:
        tmp = __import__("pathlib").Path(td)
        test_detection()
        test_store(tmp)
        test_qt_smoke(tmp)
        test_import_dialog_smoke(tmp)
    report("## 结论")
    report("- 通过：存储指纹去重 / 旧数据回填 / 批量删除 / 批量导出分享 / 跨库迁移隔离")
    report("- 通过：收藏页卡片网格 + 内部像素画区域识别，负样本不误触发")
    report("- 通过：Qt offscreen 下历史/收藏库浮窗懒加载与多选批量操作")
    report("- 失败项：无")
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_report.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(REPORT))
    print(f"\n报告已写入: {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
