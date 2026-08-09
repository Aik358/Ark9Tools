# -*- coding: utf-8 -*-
"""可视化校准工具

- 截图游戏窗口并显示
- 用 OpenCV 自动检测画布/色板（可选）
- 手动模式：在图片上依次点击标点
  画布：(0,0)格 → (23,23)格
  色板：第0行0列 → 第1行0列 → 第0行1列（可选）
- 实时显示已标点与计算结果，可保存
"""
import os
from typing import Optional, Tuple
import numpy as np
from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QMessageBox,
)

from calibration import (
    Calibration, GRID_SIZE, PALETTE_COLS,
    detect_canvas_from_screenshot, detect_palette_from_screenshot,
    load_config, save_config,
)
from palette import EXHIBITION_PALETTE


class ImageLabel(QLabel):
    """可点击、可显示的图片区"""
    clicked = Signal(QPoint)          # 相对图片像素坐标（已做缩放换算）

    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 280)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#15151c;border:1px solid #2a2a35;"
                           "border-radius:10px;")
        self._pixmap: Optional[QPixmap] = None
        self._scale = 1.0
        self._offset = QPoint(0, 0)
        self.points: list = []           # [(x, y, label, color)]

    def set_frame(self, qimg: QImage):
        self._pixmap = QPixmap.fromImage(qimg)
        self._fit()

    def _fit(self):
        if not self._pixmap:
            return
        scaled = self._pixmap.scaled(self.size(), Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation)
        self._scale = scaled.width() / self._pixmap.width()
        ox = (self.width() - scaled.width()) // 2
        oy = (self.height() - scaled.height()) // 2
        self._offset = QPoint(ox, oy)
        self.setPixmap(scaled)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _img_to_widget(self, px: int, py: int) -> QPoint:
        return QPoint(int(px * self._scale) + self._offset.x(),
                      int(py * self._scale) + self._offset.y())

    def _widget_to_img(self, wx: int, wy: int) -> QPoint:
        return QPoint(int((wx - self._offset.x()) / self._scale),
                      int((wy - self._offset.y()) / self._scale))

    def mousePressEvent(self, e):
        if not self._pixmap:
            return
        img_pt = self._widget_to_img(int(e.position().x()), int(e.position().y()))
        if 0 <= img_pt.x() < self._pixmap.width() and 0 <= img_pt.y() < self._pixmap.height():
            self.clicked.emit(img_pt)

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._pixmap or not self.points:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for idx, (x, y, _label, color) in enumerate(self.points):
            wpt = self._img_to_widget(x, y)
            p.setPen(QPen(QColor(255, 255, 255, 230), 2))
            c = 10
            p.drawLine(wpt.x() - c, wpt.y(), wpt.x() + c, wpt.y())
            p.drawLine(wpt.x(), wpt.y() - c, wpt.x(), wpt.y() + c)
            p.setBrush(QColor(*color, 210))
            p.setPen(QPen(QColor(255, 255, 255), 1))
            radius = 13
            p.drawEllipse(wpt.x() - radius, wpt.y() - radius, radius * 2, radius * 2)
            p.setPen(QColor(255, 255, 255))
            f = QFont("Microsoft YaHei")
            f.setPointSize(9)
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.drawText(wpt.x() - radius, wpt.y() - radius, radius * 2, radius * 2,
                       Qt.AlignCenter, str(idx + 1))
        p.end()


class VisualCalibrationDialog(QWidget):
    """可视化校准对话框（独立窗口）"""
    cal_saved = Signal(object)   # 保存后发出，携带 Calibration 对象

    def __init__(self, region: Tuple[int, int, int, int],
                 hwnd: Optional[int] = None):
        super().__init__()
        self.setWindowTitle("可视化校准 - 点击标点")
        self.resize(640, 520)   # 较小默认，避免盖住游戏窗口（可自行缩放）
        self.region = region
        self.hwnd = hwnd   # 优先用 WGC 窗口捕获（HDR 正常、不被遮挡）
        self.frame: Optional[np.ndarray] = None

        self.canvas_points = []   # 依次存 (0,0) 和 (23,23)
        self.palette_points = []  # 依次存 p00, p10, p01
        self.cur_step = "canvas1"  # canvas1 -> canvas2 -> palette1 -> palette2 -> palette3

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.hint_label = QLabel("点击「截取画面」获取游戏窗口截图")
        self.hint_label.setStyleSheet("color:#ffd020;font-weight:600;font-size:14px;")
        top.addWidget(self.hint_label, 1)
        btn_capture = QPushButton("截取画面")
        btn_capture.setObjectName("primary")
        btn_capture.clicked.connect(self.on_capture)
        top.addWidget(btn_capture)
        btn_auto = QPushButton("自动检测")
        btn_auto.setObjectName("primary")
        btn_auto.clicked.connect(self.on_auto_detect)
        top.addWidget(btn_auto)
        btn_undo = QPushButton("撤销")
        btn_undo.clicked.connect(self.on_undo)
        top.addWidget(btn_undo)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self.on_clear)
        top.addWidget(btn_clear)
        btn_save = QPushButton("保存校准")
        btn_save.setObjectName("success")
        btn_save.clicked.connect(self.on_save)
        top.addWidget(btn_save)
        layout.addLayout(top)

        self.img_label = ImageLabel()
        self.img_label.clicked.connect(self.on_img_click)
        layout.addWidget(self.img_label, 1)

        # 兼容性提醒：分辨率 / 窗口尺寸 / 模拟器无关，HDR 偏色不影响
        compat_tip = QLabel(
            "✔ 自动检测基于画面结构锚点（画布大边框、网格、四象限），"
            "不依赖颜色——HDR 校色偏色也能识别；\n"
            "✔ 校准坐标按窗口客户区比例保存，自动适配不同分辨率、"
            "窗口大小与模拟器")
        compat_tip.setStyleSheet(
            "color:#7fe08a;background:#12301a;border:1px solid #1f4d2a;"
            "border-radius:6px;padding:6px 10px;font-size:12px;")
        compat_tip.setWordWrap(True)
        layout.addWidget(compat_tip)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(160)
        self.result_text.setStyleSheet("background:#101016;color:#9fe8c8;"
                                       "border:1px solid #2a2a35;border-radius:8px;"
                                       "font-family:Consolas;font-size:12px;")
        layout.addWidget(self.result_text)

    # ---------------- 截图 ----------------
    def on_capture(self):
        try:
            from win32_capture import ScreenCapturer
            # 区域截图（DXGI）：稳定无黄框。WGC 帧回调线程易导致闪退，默认不用
            cap = ScreenCapturer(self.region)
            self.frame = cap.grab()
            cap.close()
            if self.frame is None:
                QMessageBox.warning(self, "提示", "截图失败：请检查窗口区域设置")
                return
            h, w = self.frame.shape[:2]
            rgb = np.ascontiguousarray(self.frame[:, :, ::-1])
            qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()
            self.img_label.set_frame(qimg)
            # 同步保存一份到工作区（方便调试或分析算法）
            try:
                save_dir = os.path.dirname(os.path.abspath(__file__))
                from PIL import Image as _PI
                _PI.fromarray(rgb).save(os.path.join(save_dir, "calibration_capture.png"))
            except Exception:
                pass
            self._reset_points()
            self._update_hint()
            self._update_result()
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def _reset_points(self):
        self.canvas_points.clear()
        self.palette_points.clear()
        self.img_label.points.clear()
        self.cur_step = "canvas1"
        self._update_hint()

    def _update_hint(self):
        hint = {
            "canvas1": "① 点击画布 (0,0) 格子中心（左上角第一格）",
            "canvas2": "② 点击画布 (23,23) 格子中心（右下角最后一格）",
            "palette1": "③ 点击色板 第0行第0列 色块中心（X01 纯白）",
            "palette2": "④ 点击色板 第1行第0列 色块中心（第0列第2格）",
            "palette3": "⑤ 点击色板 第0行第1列 色块中心（第1行第2格）",
            "done": "✅ 校准完成，点击「保存校准」",
        }.get(self.cur_step, "")
        self.hint_label.setText(hint)

    # ---------------- 标点 ----------------
    def on_img_click(self, pt: QPoint):
        if self.frame is None:
            return
        x, y = pt.x(), pt.y()
        if self.cur_step == "canvas1":
            self.canvas_points.append((x, y)); self.cur_step = "canvas2"
        elif self.cur_step == "canvas2":
            self.canvas_points.append((x, y)); self.cur_step = "palette1"
        elif self.cur_step == "palette1":
            self.palette_points.append((x, y)); self.cur_step = "palette2"
        elif self.cur_step == "palette2":
            self.palette_points.append((x, y)); self.cur_step = "palette3"
        elif self.cur_step == "palette3":
            self.palette_points.append((x, y)); self.cur_step = "done"
        self._refresh_points()
        self._update_hint()
        self._update_result()

    def on_undo(self):
        if self.cur_step == "done":
            if self.palette_points:
                self.palette_points.pop()
            elif self.canvas_points:
                self.canvas_points.pop()
            self.cur_step = "palette3" if self.palette_points else "canvas2"
        elif self.cur_step == "palette3":
            self.palette_points.pop(); self.cur_step = "palette2"
        elif self.cur_step == "palette2":
            self.palette_points.pop(); self.cur_step = "palette1"
        elif self.cur_step == "palette1":
            self.canvas_points.pop(); self.cur_step = "canvas2"
        elif self.cur_step == "canvas2":
            self.canvas_points.pop(); self.cur_step = "canvas1"
        self._refresh_points()
        self._update_hint()
        self._update_result()

    def on_clear(self):
        self._reset_points()
        self._update_result()

    def _refresh_points(self):
        pts = []
        colors = [(255, 80, 80), (255, 140, 40), (40, 200, 120),
                  (40, 160, 255), (220, 90, 220)]
        label = 1
        for (x, y) in self.canvas_points:
            pts.append((x, y, f"画布{label}", colors[min(label - 1, 4)]))
            label += 1
        for (x, y) in self.palette_points:
            pts.append((x, y, f"色板{label}", colors[min(label - 1, 4)]))
            label += 1
        self.img_label.points = pts
        self.img_label.update()

    # ---------------- 自动检测 ----------------
    def on_auto_detect(self):
        if self.frame is None:
            QMessageBox.warning(self, "提示", "请先截取画面")
            return
        try:
            # 检查 OpenCV
            try:
                import cv2  # noqa
            except ImportError:
                QMessageBox.warning(
                    self, "提示",
                    "未安装 opencv-python，自动检测不可用。\n\n"
                    "请运行 run.bat 自动安装，或在终端执行：\n"
                    "  pip install opencv-python -i https://pypi.tuna.tsinghua.edu.cn/simple\n\n"
                    "您也可以直接手动点击标点，不影响使用。")
                return

            cal_c = detect_canvas_from_screenshot(self.frame)
            cal_p = detect_palette_from_screenshot(self.frame, canvas=cal_c)
            msgs = []
            if cal_c is not None:
                self.canvas_points = [
                    (cal_c.canvas_origin[0] + cal_c.canvas_cell_w // 2,
                     cal_c.canvas_origin[1] + cal_c.canvas_cell_h // 2),
                    (cal_c.canvas_origin[0] + 23 * cal_c.canvas_cell_w + cal_c.canvas_cell_w // 2,
                     cal_c.canvas_origin[1] + 23 * cal_c.canvas_cell_h + cal_c.canvas_cell_h // 2),
                ]
                msgs.append("画布自动检测成功")
                self.cur_step = "palette1"
            if cal_p is not None:
                self.palette_points = [
                    (cal_p.palette_left + cal_p.palette_cell_w // 2,
                     cal_p.palette_top + cal_p.palette_cell_h // 2),
                    (cal_p.palette_left + cal_p.palette_cell_w // 2,
                     cal_p.palette_top + cal_p.palette_cell_h + cal_p.palette_cell_h // 2),
                    (cal_p.palette_left + cal_p.palette_cell_w + cal_p.palette_cell_w // 2,
                     cal_p.palette_top + cal_p.palette_cell_h // 2),
                ]
                msgs.append("色板自动检测成功")
                self.cur_step = "done"
            if not msgs:
                QMessageBox.warning(
                    self, "提示",
                    "自动检测失败。可能原因：\n"
                    "① 截图区域未覆盖游戏窗口\n"
                    "② 游戏界面样式与预期不同\n\n"
                    "建议：手动点击标点（更可靠）。")
                return
            self._refresh_points()
            self._update_hint()
            self._update_result()
            QMessageBox.information(self, "自动检测", "\n".join(msgs))
        except Exception as e:
            QMessageBox.warning(self, "自动检测失败", str(e))

    # ---------------- 结果与保存 ----------------
    def build_calibration(self) -> Optional[Calibration]:
        if len(self.canvas_points) < 2:
            return None
        cal = Calibration()
        cal.set_canvas_from_corners(self.canvas_points[0], self.canvas_points[1])
        if len(self.palette_points) >= 2:
            p01 = self.palette_points[2] if len(self.palette_points) >= 3 else None
            cal.set_palette_from_points(self.palette_points[0], self.palette_points[1],
                                        p01, scroll_top_row=0)
        # 记录参考尺寸（当前客户区），供分辨率/窗口/模拟器自适应换算
        cal.ref_w = max(1, self.region[2] - self.region[0])
        cal.ref_h = max(1, self.region[3] - self.region[1])
        return cal

    def _update_result(self):
        cal = self.build_calibration()
        if cal is None:
            self.result_text.setPlainText("（等待标点……）")
            return
        lines = []
        if cal.is_canvas_valid():
            lines.append(f"画布 (0,0) 左上角: ({cal.canvas_origin[0]}, {cal.canvas_origin[1]})")
            lines.append(f"画布格子: {cal.canvas_cell_w} x {cal.canvas_cell_h} px")
            c00 = cal.canvas_cell_center(0, 0)
            c23 = cal.canvas_cell_center(23, 23)
            lines.append(f" (0,0)格中心=({c00[0]},{c00[1]})  (23,23)格中心=({c23[0]},{c23[1]})")
        if cal.is_palette_valid():
            lines.append(f"色板起点: ({cal.palette_left}, {cal.palette_top})")
            lines.append(f"色块: {cal.palette_cell_w} x {cal.palette_cell_h} px")
            lines.append(f"可见行数: {cal.palette_visible_rows} 首行={cal.palette_scroll_top_row}")
        self.result_text.setPlainText("\n".join(lines))

    def on_save(self):
        cal = self.build_calibration()
        if cal is None or not cal.is_canvas_valid():
            QMessageBox.warning(self, "提示", "请至少完成画布标点（①②）")
            return
        cfg = load_config()
        cfg["window_region"] = list(self.region)
        cfg["calibration"] = cal.to_dict()
        save_config(cfg)
        self.cal_saved.emit(cal)
        QMessageBox.information(self, "已保存",
                                "校准已保存到 pixel_painter_config.json")
        self.close()


def run_calibration_dialog(region: Tuple[int, int, int, int]):
    """独立运行校准对话框（用于 main.py 外部调用）"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    dlg = VisualCalibrationDialog(region)
    dlg.show()
    app.exec()
