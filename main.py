# -*- coding: utf-8 -*-
"""Ark9Tools - 主程序入口（集成化游戏工具工作台）

三步向导：
  ① 选择图片并像素化 → ② 校准游戏界面 → ③ 自动绘画

特性：
- 自动查找并捕获游戏窗口（WGC 窗口捕获，无需游戏在前台）
- 像素数据自动传递（内存共享，无需重新导入，同时保留导入）
- 可视化校准（自动检测 + 手动标点）
- sendmsg + windowpos 鼠标输入
"""
import ctypes
import os
import sys
import threading
import time
from typing import Optional

# 尽早设置 DPI 感知（Per-Monitor V2），避免 Qt 创建时 DPI 警告
try:
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except Exception:
    try:
        _sh = ctypes.WinDLL("shcore", use_last_error=True)
        _sh.SetProcessDpiAwareness(2)
    except Exception:
        pass

import numpy as np
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QImage, QPixmap, QColor, QShortcut, QKeySequence, QIcon, QPainter, QPen
from PIL import Image
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QLineEdit, QSlider, QCheckBox,
    QGroupBox, QProgressBar, QTextEdit, QComboBox, QMessageBox,
    QFrame, QGridLayout, QSpinBox, QFormLayout, QStackedWidget,
)

from palette import EXHIBITION_PALETTE, WHITE_PALETTE_INDEX, rgb_to_hex
from pixelate import pixelate, save_visual_preview, count_color_usage, paint_order
from calibration import Calibration, load_config, save_config
from painter import Painter, PaintingProgress
from calibration_dialog import VisualCalibrationDialog
from privilege import check_game_privilege, is_elevated, relaunch_as_admin
from win32_input import (RegisterHotKey, UnregisterHotKey, WM_HOTKEY,
                         MOD_NOREPEAT, VK_F8, HOTKEY_PAINT_STOP, WindowInput)

APP_NAME = "Ark9Tools"


def app_icon() -> QIcon:
    """加载应用 Logo（兼容 PyInstaller 打包后的资源目录）。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for name in ("app.ico", "logo.png"):
        p = os.path.join(base, "assets", name)
        if os.path.exists(p):
            return QIcon(p)
    return QIcon()


def _privilege_ok(hwnd) -> bool:
    """SendInput 模式下校验 UIPI 权限。

    游戏以管理员运行时，普通权限工具无法向其注入输入/抢前台，
    表现为"点击无效、绘画自动停止"。检测到权限不匹配时：
    - 工具未提权 → 引导用户以管理员权限重启
    - 工具已提权 → 可正常注入，放行
    返回 True 表示可以继续，False 表示已处理（提示/重启）并中止。
    """
    need_admin = check_game_privilege(hwnd)
    if need_admin is None:
        # 无法判断，放行（不阻断用户）
        return True
    if not need_admin:
        return True
    if is_elevated():
        return True
    ret = QMessageBox.question(
        None, "需要管理员权限",
        "检测到游戏窗口以【管理员权限】运行。\n\n"
        "Windows 安全机制（UIPI）会阻止普通权限程序向管理员窗口\n"
        "注入鼠标输入，这是自动绘画「点击无效/绘画自动停止」的根因。\n\n"
        "是否立即以管理员权限重启本工具？",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
    if ret == QMessageBox.Yes:
        if relaunch_as_admin():
            QMessageBox.information(
                None, "已重启",
                "本工具正在以管理员权限重新启动。\n"
                "启动后请重新点击「开始绘画」。")
            return False
        QMessageBox.critical(
            None, "提权失败",
            "无法以管理员权限启动（可能已被系统拦截）。\n"
            "请手动关闭本工具后，右键图标选择「以管理员身份运行」。")
        return False
    # 权限不匹配时绝不放行：UIPI 会拦截全部硬件点击/拖动，继续只会
    # 造成色板位置与内部状态错位，表现为颜色错误。
    QMessageBox.warning(
        None, "已取消绘画",
        "未以管理员权限运行，无法向游戏注入点击或拖动。\n"
        "本次绘画已取消。请在弹窗中选择“是”自动提权重启后再开始。")
    return False

# ===========================================================================
# 全局主题（现代深色 + 明日方舟青色点缀）
# ===========================================================================
STYLE = """
QMainWindow, QDialog { background: #0b0f14; }
QWidget {
    color: #d9e2ec;
    font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei";
    font-size: 13px;
}
QLabel { color: #d9e2ec; }
QLabel[muted="true"] { color: #78879a; }
QLabel[accent="true"] { color: #54d0c0; }
QLabel[class="title"] { font-size: 23px; font-weight: 650; color: #f5f8fb; }
QLabel[class="subtitle"] { color: #7f8d9e; font-size: 12px; }
QLabel[class="eyebrow"] { color: #54d0c0; font-size: 10px; font-weight: 700; }
QLabel[class="brand"] { color: #f5f8fb; font-size: 20px; font-weight: 700; }
QLabel[class="moduleTitle"] { color: #f1f6fa; font-size: 14px; font-weight: 650; }
QLabel[class="statusGood"] { color: #71d9ad; font-size: 12px; font-weight: 700; }

QFrame[class="sidebar"] { background: #0e141b; border: 1px solid #1e2a35; border-radius: 8px; }
QFrame[class="header"] { background: #0e141b; border: 1px solid #1e2a35; border-radius: 8px; }
QFrame[class="statusCard"] { background: #101820; border: 1px solid #243746; border-radius: 7px; }
QFrame[class="contentShell"] { background: #0e141b; border: 1px solid #1e2a35; border-radius: 8px; }
QFrame[class="moduleCard"] { background: #121c26; border: 1px solid #263a49; border-radius: 8px; }

QPushButton {
    background: #182431; color: #dce5ef; border: 1px solid #2b3e4d;
    border-radius: 6px; padding: 7px 13px; font-weight: 600;
}
QPushButton:hover { background: #203040; border-color: #3c6670; }
QPushButton:pressed { background: #111a24; }
QPushButton:disabled { background: #121a23; color: #5e7081; border-color: #1e2a35; }
QPushButton[btnType="primary"] { background: #16736f; color: #ffffff; border: 1px solid #248d87; }
QPushButton[btnType="primary"]:hover { background: #1d8a84; }
QPushButton[btnType="success"] { background: #256b55; color: #ffffff; border: 1px solid #2f8267; }
QPushButton[btnType="success"]:hover { background: #2b7b61; }
QPushButton[btnType="danger"] { background: #963f47; color: #ffffff; border: 1px solid #b5515a; }
QPushButton[btnType="danger"]:hover { background: #a94952; }
QPushButton[navModule="true"] { text-align: left; padding: 10px 12px; color: #9fb0bf; background: transparent; border: 1px solid transparent; }
QPushButton[navModule="true"]:hover { background: #14202b; color: #d9e5ee; border-color: #263a49; }
QPushButton[moduleState="active"] { background: #12383d; color: #dcfff8; border-color: #2c918b; }
QPushButton[dashboardCard="true"] { text-align: left; background: #121c26; border: 1px solid #263a49; border-radius: 8px; padding: 15px 16px; }
QPushButton[dashboardCard="true"]:hover { background: #162634; border-color: #3c7978; }

QLineEdit, QSpinBox, QComboBox {
    background: #101923; border: 1px solid #2a3d4b; border-radius: 6px;
    padding: 6px 9px; color: #e1eaf2; min-height: 18px;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #54d0c0; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #121c26; border: 1px solid #2a3d4b; selection-background-color: #16736f; color: #e1eaf2; }

QSlider::groove:horizontal { height: 4px; background: #263845; border-radius: 2px; }
QSlider::handle:horizontal { background: #54d0c0; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QSlider::sub-page:horizontal { background: #1e7c77; border-radius: 2px; }
QCheckBox { spacing: 8px; color: #bfcbd8; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #405363; background: #101923; }
QCheckBox::indicator:checked { background: #1e7c77; border-color: #54d0c0; }

QGroupBox { border: 1px solid #243341; border-radius: 8px; margin-top: 13px; padding-top: 8px; background: #111a23; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #6ad3c5; font-weight: 650; }
QProgressBar { background: #101923; border: 1px solid #2a3d4b; border-radius: 6px; text-align: center; color: #f2f6fa; font-weight: 650; height: 21px; }
QProgressBar::chunk { background: #1e7c77; border-radius: 5px; }
QTextEdit { background: #0c1219; border: 1px solid #243341; border-radius: 6px; color: #b6c5d2; font-family: Consolas, "Cascadia Mono"; font-size: 12px; padding: 7px; }
"""


def _set_type(btn: QPushButton, t: str):
    btn.setProperty("btnType", t)
    btn.style().unpolish(btn)
    btn.style().polish(btn)


def _make_preview_pixmap(idx_mat: np.ndarray, cell: int = 18) -> QPixmap:
    h, w = idx_mat.shape
    img = QImage(w * cell, h * cell, QImage.Format_RGB32)
    for y in range(h):
        for x in range(w):
            rgb = EXHIBITION_PALETTE[idx_mat[y, x]][1]
            color = QColor(*rgb)
            for dy in range(cell):
                for dx in range(cell):
                    img.setPixelColor(x * cell + dx, y * cell + dy, color)
    return QPixmap.fromImage(img)


class CropDialog(QDialog):
    """以原图像素坐标指定裁剪区域，并提供实时预览。"""

    def __init__(self, image_path: str, crop_box=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("裁剪图片")
        self.setMinimumSize(680, 540)
        self._image = Image.open(image_path).convert("RGB")
        width, height = self._image.size
        if crop_box is None:
            crop_box = (0, 0, width, height)
        self.crop_box = crop_box

        layout = QVBoxLayout(self)
        form = QGridLayout()
        self.x_spin = QSpinBox(); self.x_spin.setRange(0, width - 1); self.x_spin.setValue(crop_box[0])
        self.y_spin = QSpinBox(); self.y_spin.setRange(0, height - 1); self.y_spin.setValue(crop_box[1])
        self.w_spin = QSpinBox(); self.w_spin.setRange(1, width); self.w_spin.setValue(crop_box[2] - crop_box[0])
        self.h_spin = QSpinBox(); self.h_spin.setRange(1, height); self.h_spin.setValue(crop_box[3] - crop_box[1])
        for row, (text, control) in enumerate((("左侧 X", self.x_spin), ("顶部 Y", self.y_spin), ("宽度", self.w_spin), ("高度", self.h_spin))):
            form.addWidget(QLabel(text), row // 2, (row % 2) * 2)
            form.addWidget(control, row // 2, (row % 2) * 2 + 1)
        layout.addLayout(form)
        self.preview = QLabel()
        self.preview.setMinimumSize(560, 380)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:#101923;border:1px solid #2a3d4b;border-radius:6px;")
        layout.addWidget(self.preview, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        reset = QPushButton("恢复全图")
        reset.clicked.connect(lambda: self._set_full_image())
        actions.addWidget(reset)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        confirm = QPushButton("应用裁剪")
        _set_type(confirm, "primary")
        confirm.clicked.connect(self._accept_crop)
        actions.addWidget(confirm)
        layout.addLayout(actions)
        for control in (self.x_spin, self.y_spin, self.w_spin, self.h_spin):
            control.valueChanged.connect(self._refresh_preview)
        self._refresh_preview()

    def _set_full_image(self):
        width, height = self._image.size
        self.x_spin.setValue(0); self.y_spin.setValue(0)
        self.w_spin.setValue(width); self.h_spin.setValue(height)

    def _box(self):
        width, height = self._image.size
        left = min(self.x_spin.value(), width - 1)
        top = min(self.y_spin.value(), height - 1)
        right = min(width, left + self.w_spin.value())
        bottom = min(height, top + self.h_spin.value())
        return left, top, max(left + 1, right), max(top + 1, bottom)

    def _refresh_preview(self):
        crop = self._image.crop(self._box())
        qimg = QImage(crop.tobytes(), crop.width, crop.height, crop.width * 3, QImage.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(qimg).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _accept_crop(self):
        self.crop_box = self._box()
        self.accept()


class PixelGridEditor(QWidget):
    changed = Signal()

    def __init__(self, idx_mat: np.ndarray, selected_color: QComboBox, parent=None):
        super().__init__(parent)
        self.idx_mat = idx_mat.copy()
        self.selected_color = selected_color
        self.setMinimumSize(480, 480)

    def _cell_geometry(self):
        side = min(self.width(), self.height())
        cell = max(1, side // 24)
        return cell, (self.width() - cell * 24) // 2, (self.height() - cell * 24) // 2

    def paintEvent(self, event):
        painter = QPainter(self)
        cell, ox, oy = self._cell_geometry()
        for y in range(24):
            for x in range(24):
                painter.fillRect(ox + x * cell, oy + y * cell, cell, cell, QColor(*EXHIBITION_PALETTE[int(self.idx_mat[y, x])][1]))
        painter.setPen(QPen(QColor("#344655"), 1))
        for n in range(25):
            painter.drawLine(ox + n * cell, oy, ox + n * cell, oy + cell * 24)
            painter.drawLine(ox, oy + n * cell, ox + cell * 24, oy + n * cell)

    def mousePressEvent(self, event):
        cell, ox, oy = self._cell_geometry()
        x = (int(event.position().x()) - ox) // cell
        y = (int(event.position().y()) - oy) // cell
        if 0 <= x < 24 and 0 <= y < 24:
            self.idx_mat[y, x] = self.selected_color.currentData()
            self.update()
            self.changed.emit()


class PixelEditorDialog(QDialog):
    def __init__(self, idx_mat: np.ndarray, parent=None):
        super().__init__(parent)
        self.setWindowTitle("精修像素图")
        self.setMinimumSize(690, 650)
        self.result_mat = idx_mat.copy()
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("绘制颜色："))
        self.color_combo = QComboBox()
        for index, (code, rgb, name) in enumerate(EXHIBITION_PALETTE):
            self.color_combo.addItem(f"{code}  {name}  {rgb_to_hex(rgb)}", index)
        top.addWidget(self.color_combo, 1)
        clear = QPushButton("设为留白")
        clear.clicked.connect(lambda: self.color_combo.setCurrentIndex(WHITE_PALETTE_INDEX))
        top.addWidget(clear)
        layout.addLayout(top)
        self.grid = PixelGridEditor(idx_mat, self.color_combo)
        layout.addWidget(self.grid, 1)
        self.summary = QLabel()
        self.summary.setProperty("class", "subtitle")
        layout.addWidget(self.summary)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        confirm = QPushButton("应用精修")
        _set_type(confirm, "primary")
        confirm.clicked.connect(self._accept_edit)
        actions.addWidget(confirm)
        layout.addLayout(actions)
        self.grid.changed.connect(self._refresh_summary)
        self._refresh_summary()

    def _refresh_summary(self):
        used = count_color_usage(self.grid.idx_mat)
        self.summary.setText(f"非白格：{np.count_nonzero(self.grid.idx_mat != WHITE_PALETTE_INDEX)} / 576  ·  使用 {len(used)} 种色")

    def _accept_edit(self):
        self.result_mat = self.grid.idx_mat.copy()
        self.accept()


# ===========================================================================
# 步骤 ① 图片像素化
# ===========================================================================
class StepPixelate(QWidget):
    """选择图片 → 像素化 → 生成 24×24 矩阵（存内存供后续步骤使用）"""
    convert_done_signal = Signal(object)
    convert_error_signal = Signal(str)

    def __init__(self, app_ctx):
        super().__init__()
        self.ctx = app_ctx
        self.idx_mat: Optional[np.ndarray] = None
        self.image_path: Optional[str] = None
        self.crop_box: Optional[tuple[int, int, int, int]] = None
        self._init_ui()
        self.convert_done_signal.connect(self._on_convert_done)
        self.convert_error_signal.connect(self._on_convert_error)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("① 选择图片并转换为像素图")
        title.setProperty("class", "title")
        layout.addWidget(title)
        sub = QLabel("导入任意图片，自动转换为游戏画布用的 24×24 × 38色 像素矩阵")
        sub.setProperty("class", "subtitle")
        layout.addWidget(sub)

        # 图片选择
        file_box = QGroupBox("1. 选择图片")
        fb = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("点击「浏览…」选择图片（推荐 1:1 比例）")
        fb.addWidget(self.path_edit, 1)
        btn = QPushButton("浏览…")
        btn.clicked.connect(self.on_browse)
        fb.addWidget(btn)
        self.btn_crop = QPushButton("裁剪图片")
        self.btn_crop.setEnabled(False)
        self.btn_crop.clicked.connect(self.on_crop)
        fb.addWidget(self.btn_crop)
        file_box.setLayout(fb)
        layout.addWidget(file_box)

        # 调节参数（对应 HTML 插件 cropContrast / cropBrightness / cropSaturation）
        param_box = QGroupBox("2. 画面调节（对应插件滤镜，默认 100%）")
        pl = QHBoxLayout()
        pl.setSpacing(24)

        con_col = QVBoxLayout()
        con_head = QHBoxLayout()
        con_head.addWidget(QLabel("对比度"))
        self.con_val = QLabel("100%")
        self.con_val.setProperty("accent", "true")
        con_head.addWidget(self.con_val)
        con_head.addStretch()
        con_col.addLayout(con_head)
        self.con_slider = QSlider(Qt.Horizontal)
        self.con_slider.setRange(0, 200); self.con_slider.setValue(100)
        self.con_slider.valueChanged.connect(lambda v: self.con_val.setText(f"{v}%"))
        con_col.addWidget(self.con_slider)
        pl.addLayout(con_col)

        bri_col = QVBoxLayout()
        bri_head = QHBoxLayout()
        bri_head.addWidget(QLabel("亮度"))
        self.bri_val = QLabel("100%")
        self.bri_val.setProperty("accent", "true")
        bri_head.addWidget(self.bri_val)
        bri_head.addStretch()
        bri_col.addLayout(bri_head)
        self.bri_slider = QSlider(Qt.Horizontal)
        self.bri_slider.setRange(0, 200); self.bri_slider.setValue(100)
        self.bri_slider.valueChanged.connect(lambda v: self.bri_val.setText(f"{v}%"))
        bri_col.addWidget(self.bri_slider)
        pl.addLayout(bri_col)

        sat_col = QVBoxLayout()
        sat_head = QHBoxLayout()
        sat_head.addWidget(QLabel("饱和度"))
        self.sat_val = QLabel("100%")
        self.sat_val.setProperty("accent", "true")
        sat_head.addWidget(self.sat_val)
        sat_head.addStretch()
        sat_col.addLayout(sat_head)
        self.sat_slider = QSlider(Qt.Horizontal)
        self.sat_slider.setRange(0, 200); self.sat_slider.setValue(100)
        self.sat_slider.valueChanged.connect(lambda v: self.sat_val.setText(f"{v}%"))
        sat_col.addWidget(self.sat_slider)
        pl.addLayout(sat_col)
        param_box.setLayout(pl)
        layout.addWidget(param_box)

        # 选项（对应 HTML 抖动开关）
        opt_row = QHBoxLayout()
        self.flatten_white = QCheckBox("白色背景映射为 X01（跳过不涂）")
        self.flatten_white.setChecked(True)
        opt_row.addWidget(self.flatten_white)
        opt_row.addWidget(QLabel("透明背景："))
        self.transparent_combo = QComboBox()
        self.transparent_combo.addItem("留白（不绘制）", "blank")
        self.transparent_combo.addItem("转成黑色", "black")
        self.transparent_combo.setToolTip("仅对 PNG 等带透明通道的图片生效")
        opt_row.addWidget(self.transparent_combo)
        opt_row.addWidget(QLabel("取色数 K："))
        self.k_spin = QSpinBox()
        self.k_spin.setRange(4, 64)
        self.k_spin.setValue(32)
        self.k_spin.setToolTip("K-means 聚类数（对应插件 cropColorCount，默认 32）")
        opt_row.addWidget(self.k_spin)
        opt_row.addWidget(QLabel("抖动："))
        self.dither_combo = QComboBox()
        self.dither_combo.addItems(["Floyd-Steinberg", "Atkinson", "无抖动"])
        self.dither_combo.setCurrentIndex(0)
        opt_row.addWidget(self.dither_combo)
        opt_row.addStretch()
        layout.addLayout(opt_row)

        # 转换按钮
        self.btn_convert = QPushButton("转换像素图")
        _set_type(self.btn_convert, "primary")
        self.btn_convert.setMinimumHeight(44)
        self.btn_convert.clicked.connect(self.on_convert)
        layout.addWidget(self.btn_convert)

        # 中部：预览 + 使用统计
        mid = QHBoxLayout()
        preview_frame = QFrame()
        preview_frame.setStyleSheet(
            "background:#1a1d24;border:1px solid #2e323d;border-radius:12px;")
        pv = QVBoxLayout(preview_frame)
        pv.addWidget(QLabel("像素预览"))
        self.preview_label = QLabel()
        self.preview_label.setMinimumSize(280, 280)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setText("（尚未转换）")
        self.preview_label.setStyleSheet("color:#5a6270;font-size:14px;")
        pv.addWidget(self.preview_label)
        mid.addWidget(preview_frame, 3)

        usage_frame = QFrame()
        usage_frame.setStyleSheet(
            "background:#1a1d24;border:1px solid #2e323d;border-radius:12px;")
        uv = QVBoxLayout(usage_frame)
        uv.addWidget(QLabel("颜色使用统计"))
        self.usage_text = QTextEdit()
        self.usage_text.setMaximumHeight(260)
        uv.addWidget(self.usage_text)
        mid.addWidget(usage_frame, 2)
        layout.addLayout(mid, 1)

        # 保存按钮
        save_row = QHBoxLayout()
        self.btn_preview = QPushButton("保存预览 PNG")
        self.btn_preview.setEnabled(False)
        self.btn_preview.clicked.connect(self.on_save_preview)
        save_row.addWidget(self.btn_preview)
        self.btn_export = QPushButton("导出矩阵 .npy")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.on_export_npy)
        save_row.addWidget(self.btn_export)
        self.btn_edit = QPushButton("精修像素图")
        self.btn_edit.setEnabled(False)
        self.btn_edit.clicked.connect(self.on_edit_pixels)
        save_row.addWidget(self.btn_edit)
        save_row.addStretch()
        layout.addLayout(save_row)



    def on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self.path_edit.setText(path)
            self.image_path = path
            self.crop_box = None
            self.btn_crop.setEnabled(True)

    def on_crop(self):
        if not self.image_path or not os.path.exists(self.image_path):
            return
        try:
            dialog = CropDialog(self.image_path, self.crop_box, self)
            if dialog.exec() == QDialog.Accepted:
                self.crop_box = dialog.crop_box
                left, top, right, bottom = self.crop_box
                self.btn_crop.setText(f"裁剪：{right - left}×{bottom - top}")
        except Exception as exc:
            QMessageBox.critical(self, "裁剪失败", str(exc))

    def on_convert(self):
        if not self.image_path or not os.path.exists(self.image_path):
            QMessageBox.warning(self, "提示", "请先选择有效图片")
            return
        try:
            # AI 主体提取可能较慢（首次需加载模型），放子线程避免卡 UI
            self.btn_convert.setEnabled(False)
            self.btn_convert.setText("转换中…")
            QApplication.processEvents()
            image_path = self.image_path
            params = {
                "contrast": self.con_slider.value() / 100.0,
                "brightness": self.bri_slider.value() / 100.0,
                "saturation": self.sat_slider.value() / 100.0,
                "color_count": self.k_spin.value(),
                "dither": ["fs", "atkinson", "none"][self.dither_combo.currentIndex()],
                "flatten_white": self.flatten_white.isChecked(),
                "crop_box": self.crop_box,
                "transparent_mode": self.transparent_combo.currentData(),
            }

            def do_convert():
                try:
                    mat = pixelate(image_path, **params)
                    self.convert_done_signal.emit(mat)
                except Exception as e:
                    self.convert_error_signal.emit(str(e))

            t = threading.Thread(target=do_convert, daemon=True)
            t.start()
        except Exception as e:
            self.btn_convert.setEnabled(True)
            self.btn_convert.setText("转换像素图")
            QMessageBox.critical(self, "错误", f"转换失败：{e}")

    def _on_convert_done(self, mat: np.ndarray):
        """转换完成（主线程执行 UI 更新）"""
        self.idx_mat = mat
        self.ctx.idx_mat = mat   # 内存共享给后续步骤
        self._refresh_preview()
        self._refresh_usage()
        self.btn_preview.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.btn_edit.setEnabled(True)
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("转换像素图")
        QMessageBox.information(self, "转换成功",
                                f"已生成 24×24 像素矩阵，"
                                f"非白格 {np.count_nonzero(mat != WHITE_PALETTE_INDEX)} 个")

    def _on_convert_error(self, err: str):
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("转换像素图")
        QMessageBox.critical(self, "错误", f"转换失败：{err}")

    def _refresh_preview(self):
        if self.idx_mat is None:
            return
        pix = _make_preview_pixmap(self.idx_mat, 14)
        self.preview_label.setPixmap(
            pix.scaled(self.preview_label.size(),
                       Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _refresh_usage(self):
        if self.idx_mat is None:
            return
        usage = count_color_usage(self.idx_mat)
        lines = [f"非白格数：{sum(usage.values())} / 576\n"]
        lines.append("色号  RGB        名称    用量")
        for idx, cnt in usage.items():
            code, rgb, name = EXHIBITION_PALETTE[idx]
            lines.append(f"{code}  {rgb_to_hex(rgb)}  {name}  {cnt}")
        self.usage_text.setPlainText("\n".join(lines))

    def on_edit_pixels(self):
        if self.idx_mat is None:
            return
        dialog = PixelEditorDialog(self.idx_mat, self)
        if dialog.exec() == QDialog.Accepted:
            self.idx_mat = dialog.result_mat
            self.ctx.idx_mat = self.idx_mat
            self._refresh_preview()
            self._refresh_usage()
            QMessageBox.information(
                self, "精修已应用",
                f"最终矩阵已更新：非白格 {np.count_nonzero(self.idx_mat != WHITE_PALETTE_INDEX)} 个。\n"
                "后续进入自动绘画时将使用此精修结果。")

    def on_save_preview(self):
        if self.idx_mat is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存预览", "pixel_preview.png",
                                              "PNG (*.png)")
        if path:
            save_visual_preview(self.idx_mat, path)
            QMessageBox.information(self, "保存", f"已保存 {path}")

    def on_export_npy(self):
        if self.idx_mat is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出矩阵", "pixel_map.npy",
                                              "NumPy (*.npy)")
        if path:
            np.save(path, self.idx_mat)
            QMessageBox.information(self, "导出", f"已保存 {path}")


# ===========================================================================
# 步骤 ② 游戏界面校准
# ===========================================================================
class StepCalibrate(QWidget):
    """自动查找游戏窗口 + 画布/色板校准"""

    def __init__(self, app_ctx):
        super().__init__()
        self.ctx = app_ctx
        self.hwnd: Optional[int] = None
        self._init_ui()
        self._load_from_config()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("② 校准游戏界面")
        title.setProperty("class", "title")
        layout.addWidget(title)
        sub = QLabel("自动定位游戏窗口、画布与色板。建议点击「自动检测」。")
        sub.setProperty("class", "subtitle")
        layout.addWidget(sub)
        # 重要提示：WGC 截取要求窗口可见（不能被遮挡、不能最小化）
        tip = QLabel("⚠ 请确保游戏窗口未被其他窗口遮挡、且未最小化，否则截图可能失败")
        tip.setStyleSheet(
            "color:#ffd020;background:#3a3410;border:1px solid #5a4c10;"
            "border-radius:6px;padding:6px 10px;font-size:12px;")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        # 兼容性提醒：分辨率 / 窗口尺寸 / 模拟器无关，HDR 偏色不影响
        compat_tip = QLabel(
            "✔ 本工具按画面结构锚点（画布大边框、网格、四象限）定位，"
            "不依赖颜色值——开启 HDR 校色导致偏色也不影响识别\n"
            "✔ 校准坐标按「窗口客户区比例」保存，自动适配不同分辨率、"
            "窗口大小与模拟器（缩放后画布仍是 24×24 格）")
        compat_tip.setStyleSheet(
            "color:#7fe08a;background:#12301a;border:1px solid #1f4d2a;"
            "border-radius:6px;padding:6px 10px;font-size:12px;")
        compat_tip.setWordWrap(True)
        layout.addWidget(compat_tip)

        # 游戏窗口
        win_box = QGroupBox("1. 游戏窗口（自动查找，无需切到前台）")
        wb = QVBoxLayout()
        row = QHBoxLayout()
        btn_auto_find = QPushButton("自动查找游戏窗口")
        _set_type(btn_auto_find, "primary")
        btn_auto_find.clicked.connect(self.on_auto_find)
        row.addWidget(btn_auto_find)
        btn_refresh = QPushButton("刷新列表")
        btn_refresh.clicked.connect(self.on_refresh_windows)
        row.addWidget(btn_refresh)
        wb.addLayout(row)
        self.window_combo = QComboBox()
        self.window_combo.setMinimumHeight(34)
        self.window_combo.currentIndexChanged.connect(self.on_window_selected)
        wb.addWidget(self.window_combo)
        win_box.setLayout(wb)
        layout.addWidget(win_box)
        self.hwnd_label = QLabel("窗口：未选择")
        self.hwnd_label.setProperty("muted", "true")
        layout.addWidget(self.hwnd_label)

        # 校准按钮
        btn_row = QHBoxLayout()
        self.btn_cal_dialog = QPushButton("可视化校准（截图标点）")
        _set_type(self.btn_cal_dialog, "primary")
        self.btn_cal_dialog.setMinimumHeight(40)
        self.btn_cal_dialog.clicked.connect(self.on_open_cal_dialog)
        btn_row.addWidget(self.btn_cal_dialog, 3)
        btn_auto = QPushButton("自动检测画布/色板")
        btn_auto.clicked.connect(self.on_auto_detect)
        btn_row.addWidget(btn_auto, 2)
        btn_preset = QPushButton("使用默认校准")
        btn_preset.clicked.connect(self.on_apply_preset)
        btn_row.addWidget(btn_preset, 1)
        layout.addLayout(btn_row)

        # 手动参数
        manual_box = QGroupBox("2. 坐标参数（自动检测后自动填入）")
        ml = QGridLayout()
        ml.setHorizontalSpacing(18)
        ml.setVerticalSpacing(8)

        ml.addWidget(QLabel("画布(0,0) X:"), 0, 0)
        self.c_ox = QSpinBox(); self.c_ox.setRange(0, 99999)
        ml.addWidget(self.c_ox, 0, 1)
        ml.addWidget(QLabel("画布(0,0) Y:"), 0, 2)
        self.c_oy = QSpinBox(); self.c_oy.setRange(0, 99999)
        ml.addWidget(self.c_oy, 0, 3)

        ml.addWidget(QLabel("格子宽:"), 1, 0)
        self.c_w = QSpinBox(); self.c_w.setRange(1, 9999); self.c_w.setValue(21)
        ml.addWidget(self.c_w, 1, 1)
        ml.addWidget(QLabel("格子高:"), 1, 2)
        self.c_h = QSpinBox(); self.c_h.setRange(1, 9999); self.c_h.setValue(23)
        ml.addWidget(self.c_h, 1, 3)

        ml.addWidget(QLabel("色板列0中心X:"), 2, 0)
        self.p_x = QSpinBox(); self.p_x.setRange(0, 99999)
        ml.addWidget(self.p_x, 2, 1)
        ml.addWidget(QLabel("色板行0中心Y:"), 2, 2)
        self.p_y = QSpinBox(); self.p_y.setRange(0, 99999)
        ml.addWidget(self.p_y, 2, 3)

        ml.addWidget(QLabel("列间距:"), 3, 0)
        self.p_gx = QSpinBox(); self.p_gx.setRange(1, 9999); self.p_gx.setValue(88)
        ml.addWidget(self.p_gx, 3, 1)
        ml.addWidget(QLabel("行间距:"), 3, 2)
        self.p_gy = QSpinBox(); self.p_gy.setRange(1, 9999); self.p_gy.setValue(88)
        ml.addWidget(self.p_gy, 3, 3)

        manual_box.setLayout(ml)
        layout.addWidget(manual_box)

        btn_save = QPushButton("保存校准")
        _set_type(btn_save, "success")
        btn_save.clicked.connect(self.on_save_cal)
        layout.addWidget(btn_save)

        layout.addStretch(1)
        self._windows = []
        self.on_refresh_windows()

    # ---------- 窗口 ----------
    def on_auto_find(self):
        """自动查找游戏窗口（明日方舟/Unity 窗口，排除自身）"""
        import ctypes
        from win32_input import enum_windows
        # 排除自身进程的窗口（本工具标题含"明日方舟"，会误匹配自己）
        self_pid = ctypes.windll.kernel32.GetCurrentProcessId()
        wins = [w for w in enum_windows(min_title_len=1) if w["pid"] != self_pid]

        # 优先：UnityWndClass 类名 + 标题含游戏名
        target = None
        for w in wins:
            if w["class_name"] == "UnityWndClass":
                t = w["title"]
                if t and ("方舟" in t or "明日" in t or "arknights" in t.lower()):
                    target = w
                    break
        # 其次：任何 UnityWndClass 窗口
        if target is None:
            for w in wins:
                if w["class_name"] == "UnityWndClass":
                    target = w
                    break
        # 再次：标题含游戏名
        if target is None:
            for w in wins:
                t = w["title"]
                if t and ("方舟" in t or "明日" in t or "arknights" in t.lower()):
                    target = w
                    break
        if target is None:
            QMessageBox.warning(self, "提示", "未找到游戏窗口，请确认游戏已打开")
            return
        self._windows = wins
        self._fill_combo()
        for i in range(self.window_combo.count()):
            if self.window_combo.itemData(i) == target["hwnd"]:
                self.window_combo.setCurrentIndex(i)
                break
        QMessageBox.information(
            self, "成功",
            f"已找到游戏窗口：{target['title']}\n句柄 0x{target['hwnd']:X}")

    def on_refresh_windows(self):
        try:
            from win32_input import enum_windows
            self._windows = enum_windows(min_title_len=1)
        except Exception:
            self._windows = []
        self._fill_combo()

    def _fill_combo(self):
        self.window_combo.blockSignals(True)
        self.window_combo.clear()
        self.window_combo.addItem("— 请选择游戏窗口 —", None)
        for win in self._windows:
            title = win["title"].replace("\n", " ").strip()
            proc = win.get("process_name", "")
            self.window_combo.addItem(f"{title}  [{proc}]", win["hwnd"])
        self.window_combo.blockSignals(False)
        self.hwnd_label.setText(f"共 {len(self._windows)} 个可见窗口，请选择")

    def on_window_selected(self, index: int):
        hwnd = self.window_combo.itemData(index)
        if hwnd is None:
            self.hwnd = None
            self.hwnd_label.setText("窗口：未选择")
            self.hwnd_label.setProperty("muted", "true")
            return
        self.hwnd = int(hwnd)
        self.ctx.hwnd = self.hwnd
        title = self.window_combo.itemText(index).split("  [")[0]
        self.hwnd_label.setText(f"已选择窗口：{title}（0x{hwnd:X}）")
        self.hwnd_label.setProperty("accent", "true")
        self.hwnd_label.style().unpolish(self.hwnd_label)
        self.hwnd_label.style().polish(self.hwnd_label)

    # ---------- 校准 ----------
    def on_open_cal_dialog(self):
        region = self._region()
        hwnd = getattr(self, "hwnd", None) or getattr(self.ctx, "hwnd", None)
        self.dlg = VisualCalibrationDialog(region, hwnd=hwnd)
        self.dlg.cal_saved.connect(self._apply_cal)
        self.dlg.show()

    def _region(self):
        # 优先用窗口客户区屏幕矩形（精确匹配 WGC 坐标）
        if getattr(self, "hwnd", None):
            import ctypes
            import ctypes.wintypes as wt
            rc = wt.RECT()
            ctypes.windll.user32.GetClientRect(self.hwnd, ctypes.byref(rc))
            pt = wt.POINT(0, 0)
            ctypes.windll.user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
            return (pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom)
        cfg = load_config()
        r = cfg.get("window_region")
        if r:
            return tuple(r)
        return (0, 0, 1920, 1080)

    def _apply_cal(self, cal: Calibration):
        self.c_ox.setValue(cal.canvas_origin[0])
        self.c_oy.setValue(cal.canvas_origin[1])
        self.c_w.setValue(cal.canvas_cell_w)
        self.c_h.setValue(cal.canvas_cell_h)
        self.p_x.setValue(cal.palette_left)
        self.p_y.setValue(cal.palette_top)
        self.p_gx.setValue(cal.palette_col_gap)
        self.p_gy.setValue(cal.palette_row_gap)

    def on_apply_preset(self):
        """使用内置界面预设（明日方舟夏日嘉年华默认校准）"""
        from calibration import apply_preset
        try:
            cal = apply_preset("arknights_summer")
        except Exception as e:
            QMessageBox.warning(self, "提示", f"加载预设失败：{e}")
            return
        self._apply_cal(cal)
        QMessageBox.information(
            self, "已加载",
            "已加载默认校准（明日方舟夏日嘉年华）。\n\n"
            "点「保存校准」即可使用。\n"
            "若实际界面与默认值不符，可用「自动检测」或「可视化校准」调整。")

    def on_auto_detect(self):
        try:
            try:
                import cv2  # noqa
            except ImportError:
                QMessageBox.warning(
                    self, "提示",
                    "未安装 opencv-python，自动检测不可用。\n\n"
                    "请在终端执行：\n"
                    "  pip install opencv-python -i https://pypi.tuna.tsinghua.edu.cn/simple\n\n"
                    "或点击「可视化校准」手动标点。")
                return
            if self.hwnd is None:
                QMessageBox.warning(self, "提示", "请先选择游戏窗口")
                return

            # 校准使用 DXGI 区域捕获，避免 WGC 系统级黄色捕获边框。
            from win32_capture import ScreenCapturer
            from calibration import (detect_canvas_from_screenshot,
                                     detect_palette_from_screenshot)
            import ctypes
            import ctypes.wintypes as wt
            rc = wt.RECT()
            ctypes.windll.user32.GetClientRect(self.hwnd, ctypes.byref(rc))
            pt = wt.POINT(0, 0)
            ctypes.windll.user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
            region = (pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom)
            cap = ScreenCapturer(region, prefer_winrt=False)
            img = cap.grab()
            cap.close()
            if img is None:
                QMessageBox.warning(self, "提示", "截图失败，请确认窗口有效")
                return
            cal_c = detect_canvas_from_screenshot(img)
            cal_p = detect_palette_from_screenshot(img, canvas=cal_c)
            msgs = []
            if cal_c is not None:
                self.c_ox.setValue(cal_c.canvas_origin[0])
                self.c_oy.setValue(cal_c.canvas_origin[1])
                self.c_w.setValue(cal_c.canvas_cell_w)
                self.c_h.setValue(cal_c.canvas_cell_h)
                msgs.append("画布检测成功")
            else:
                # 画布边框不可见（HDR 偏色漂白等）：若已有校准，
                # 按当前客户区比例重映射旧画布参数兜底，避免直接丢失
                old = load_config().get("calibration", {})
                if old.get("canvas_cell_w") and old.get("canvas_origin"):
                    old_cal = Calibration()
                    old_cal.from_dict(old)
                    old_cal = old_cal.remap(rc.right, rc.bottom)
                    self.c_ox.setValue(old_cal.canvas_origin[0])
                    self.c_oy.setValue(old_cal.canvas_origin[1])
                    self.c_w.setValue(old_cal.canvas_cell_w)
                    self.c_h.setValue(old_cal.canvas_cell_h)
                    msgs.append("画布沿用旧校准(按比例适配)")
            if cal_p is not None:
                self.p_x.setValue(cal_p.palette_left)
                self.p_y.setValue(cal_p.palette_top)
                self.p_gx.setValue(cal_p.palette_col_gap)
                self.p_gy.setValue(cal_p.palette_row_gap)
                msgs.append("色板检测成功")
            if not msgs:
                # 自动检测失败：引导用户用可视化校准（最可靠）
                reply = QMessageBox.question(
                    self, "自动检测失败",
                    "未找到画布/色板。\n\n"
                    "建议用「可视化校准」手动标点（截图后在图上点 5 个位置），\n"
                    "最可靠。\n\n"
                    "是否打开可视化校准？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes)
                if reply == QMessageBox.Yes:
                    self.on_open_cal_dialog()
                return
            QMessageBox.information(self, "自动检测", "\n".join(msgs) + "，已填入参数")
        except Exception as e:
            QMessageBox.warning(self, "自动检测失败", str(e))

    def _cal_from_ui(self) -> Calibration:
        cal = Calibration()
        cal.canvas_origin = (self.c_ox.value(), self.c_oy.value())
        cal.canvas_cell_w = self.c_w.value()
        cal.canvas_cell_h = self.c_h.value()
        cal.palette_left = self.p_x.value()
        cal.palette_top = self.p_y.value()
        cal.palette_grid_top = self.p_y.value()   # 色板"网格第 0 行"中心 Y
        cal.palette_col_gap = self.p_gx.value()
        cal.palette_row_gap = self.p_gy.value()
        cal.palette_cell_w = 75
        cal.palette_cell_h = 75
        # 记录当前客户区尺寸作为换算基准（分辨率/窗口/模拟器自适应）
        r = self._region()
        cal.ref_w = max(1, r[2] - r[0])
        cal.ref_h = max(1, r[3] - r[1])
        return cal

    def on_save_cal(self):
        cfg = load_config()
        cfg["window_hwnd"] = self.hwnd if self.hwnd else None
        cfg["window_region"] = list(self._region())
        cfg["calibration"] = self._cal_from_ui().to_dict()
        save_config(cfg)
        QMessageBox.information(self, "保存", "校准已保存")

    def _load_from_config(self):
        cfg = load_config()
        cal = cfg.get("calibration", {})
        if cal:
            # 旧校准基于"当时的客户区尺寸"；若当前窗口尺寸不同，
            # 按比例重映射到当前客户区（分辨率/窗口/模拟器自适应）
            from calibration import Calibration as _Cal
            cal_obj = _Cal()
            try:
                cal_obj.from_dict(cal)
            except Exception:
                cal_obj = None
            if cal_obj is not None:
                r = self._region()
                cw = max(1, r[2] - r[0])
                ch = max(1, r[3] - r[1])
                cal_obj = cal_obj.remap(cw, ch)
                cal = cal_obj.to_dict()
            self.c_ox.setValue(cal["canvas_origin"][0])
            self.c_oy.setValue(cal["canvas_origin"][1])
            self.c_w.setValue(cal.get("canvas_cell_w", 21))
            self.c_h.setValue(cal.get("canvas_cell_h", 23))
            self.p_x.setValue(cal.get("palette_left", 0))
            self.p_y.setValue(cal.get("palette_top", 0))
            self.p_gx.setValue(cal.get("palette_col_gap", 88))
            self.p_gy.setValue(cal.get("palette_row_gap", 88))


# ===========================================================================
# 独立任务监控窗口
# ===========================================================================
class TaskMonitorDialog(QDialog):
    """独立于游戏窗口的绘画进度监控，不修改目标窗口状态。"""
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ark9Tools · 绘画监控")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setFixedSize(320, 210)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        eyebrow = QLabel("PIXELPAINTER / RUNNING")
        eyebrow.setProperty("class", "eyebrow")
        layout.addWidget(eyebrow)
        self.state = QLabel("正在准备任务")
        self.state.setProperty("class", "moduleTitle")
        layout.addWidget(self.state)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.metrics = QLabel("0 / 0 格\n当前色 -  ·  0.00 格/s")
        self.metrics.setProperty("class", "subtitle")
        layout.addWidget(self.metrics)
        layout.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        self.stop_btn = QPushButton("停止任务")
        _set_type(self.stop_btn, "danger")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        row.addWidget(self.stop_btn)
        layout.addLayout(row)

    def update_progress(self, progress: PaintingProgress):
        total = max(1, progress.total)
        self.progress.setValue(round(progress.done * 100 / total))
        elapsed = time.time() - progress.start_time if progress.start_time else 0
        rate = progress.done / elapsed if elapsed > 0 else 0
        eta = (progress.total - progress.done) / rate if rate > 0 else 0
        color = EXHIBITION_PALETTE[progress.cur_color_idx][0] if progress.cur_color_idx is not None else "-"
        self.state.setText(progress.status_msg or "正在绘画")
        self.metrics.setText(
            f"{progress.done} / {progress.total} 格  ·  剩余约 {eta:.0f}s\n"
            f"当前色 {color}  ·  {rate:.2f} 格/s")

    def finish(self, message: str):
        self.state.setText(message)
        self.stop_btn.setText("关闭")
        try:
            self.stop_btn.clicked.disconnect()
        except Exception:
            pass
        self.stop_btn.clicked.connect(self.close)


# ===========================================================================
# 步骤 ③ 自动绘画
# ===========================================================================
class StepPaint(QWidget):
    progress_signal = Signal(object)
    done_signal = Signal()
    error_signal = Signal(str)

    def __init__(self, app_ctx):
        super().__init__()
        self.ctx = app_ctx
        self.idx_mat: Optional[np.ndarray] = None
        self.painter: Optional[Painter] = None
        self.thread: Optional[threading.Thread] = None
        self._hotkey_registered = False
        self._hotkey_hwnd = 0
        self._stop_requested = False   # 空窗期急停：线程就绪前按下的停止指令
        self._last_status_msg = ""
        self.monitor: Optional[TaskMonitorDialog] = None
        self._init_ui()
        # ESC 急停快捷键（工具窗口获得焦点时同样可停）
        self.esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.esc_shortcut.setContext(Qt.ApplicationShortcut)
        self.esc_shortcut.activated.connect(self.on_stop)
        self.progress_signal.connect(self._on_progress)
        self.done_signal.connect(self._on_done)
        self.error_signal.connect(self._on_paint_error)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("③ 自动绘画")
        title.setProperty("class", "title")
        layout.addWidget(title)
        sub = QLabel("确认像素矩阵后，一键开始自动涂色（DXGI 截图 + SendInput 硬件输入）")
        sub.setProperty("class", "subtitle")
        layout.addWidget(sub)

        # 像素矩阵来源
        src_box = QGroupBox("1. 像素矩阵")
        sb = QHBoxLayout()
        self.src_label = QLabel("（等待从第①步传递）")
        self.src_label.setProperty("muted", "true")
        sb.addWidget(self.src_label, 1)
        btn_load = QPushButton("导入 .npy")
        btn_load.clicked.connect(self.on_load_npy)
        sb.addWidget(btn_load)
        src_box.setLayout(sb)
        layout.addWidget(src_box)

        # 绘画设置
        opt_box = QGroupBox("2. 绘画设置")
        ob = QHBoxLayout()
        ob.addWidget(QLabel("绘画顺序："))
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems(["按颜色频次（推荐）", "行扫描", "列扫描", "螺旋"])
        ob.addWidget(self.strategy_combo, 1)
        ob.addSpacing(18)
        ob.addWidget(QLabel("输入模式："))
        self.input_mode_combo = QComboBox()
        self.input_mode_combo.addItems([
            "SendInput 硬件级",
            "WindowPos（MAA PC 推荐）",
            "纯消息",
            "实验：MAA 伪最小化",
        ])
        # 对齐本机 D:\maa 的 PC 配置：SendMessageWithWindowPos。
        # 当前桌面会话中 SendInput 无法移动真实光标，会造成无输入的假完成。
        self.input_mode_combo.setCurrentIndex(1)
        ob.addWidget(self.input_mode_combo, 1)
        ob.addSpacing(18)
        self.verify_check = QCheckBox("涂色后校验")
        ob.addWidget(self.verify_check)
        opt_box.setLayout(ob)
        layout.addWidget(opt_box)

        # 进度
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("%v / %m 格")
        layout.addWidget(self.progress_bar)

        self.status_text = QTextEdit()
        self.status_text.setMaximumHeight(220)
        layout.addWidget(self.status_text, 1)

        # 急停提示（醒目，防止误以为无法中途停止）
        esc_hint = QLabel("⚠ 急停：绘画中按 F8 或 ESC 立即停止（F8 为系统级热键，游戏窗口内同样有效）")
        esc_hint.setStyleSheet("color:#ffb54d;font-weight:600;")
        layout.addWidget(esc_hint)

        ctrl = QHBoxLayout()
        self.btn_start = QPushButton("开始绘画")
        _set_type(self.btn_start, "success")
        self.btn_start.setMinimumHeight(44)
        self.btn_start.clicked.connect(self.on_start)
        ctrl.addWidget(self.btn_start, 2)
        self.btn_stop = QPushButton("停止")
        _set_type(self.btn_stop, "danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setMinimumHeight(44)
        self.btn_stop.clicked.connect(self.on_stop)
        ctrl.addWidget(self.btn_stop, 1)
        layout.addLayout(ctrl)

    def _load_from_ctx(self):
        """从内存共享读取第①步的像素矩阵（数据自动传递）"""
        mat = getattr(self.ctx, "idx_mat", None)
        if mat is not None:
            self.idx_mat = mat
            usage = count_color_usage(mat)
            self.src_label.setText(
                f"已从第①步自动获取：{sum(usage.values())} 格非白像素 "
                f"（{len(usage)} 种颜色）")
            self.src_label.setProperty("accent", "true")
            self.src_label.style().unpolish(self.src_label)
            self.src_label.style().polish(self.src_label)

    def on_load_npy(self):
        """保留手动导入方式"""
        path, _ = QFileDialog.getOpenFileName(self, "加载矩阵", "", "NumPy (*.npy)")
        if not path:
            return
        try:
            self.idx_mat = np.load(path)
            if self.idx_mat.shape != (24, 24):
                QMessageBox.warning(self, "提示", "矩阵尺寸必须为 24×24")
                self.idx_mat = None
                return
            self.src_label.setText(f"已导入：{os.path.basename(path)}")
            self.src_label.setProperty("accent", "true")
            self.src_label.style().unpolish(self.src_label)
            self.src_label.style().polish(self.src_label)
            self._log(f"已导入矩阵 {path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def _strategy(self) -> str:
        return {
            "按颜色频次（推荐）": "usage_first",
            "行扫描": "row",
            "列扫描": "column",
            "螺旋": "spiral",
        }[self.strategy_combo.currentText()]

    def _log(self, msg: str):
        self.status_text.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def on_start(self):
        if self.thread is not None and self.thread.is_alive():
            QMessageBox.warning(self, "提示", "绘画正在运行中，请先停止或按 ESC 急停")
            return
        if self.idx_mat is None:
            QMessageBox.warning(self, "提示", "请先在①页转换像素图，或在此导入 .npy")
            return
        cfg = load_config()
        hwnd = getattr(self.ctx, "hwnd", None)
        if not hwnd:
            hwnd = cfg.get("window_hwnd")
        if not hwnd:
            QMessageBox.warning(self, "提示", "请先在②页选择游戏窗口")
            return
        cal_data = cfg.get("calibration")
        if not cal_data:
            QMessageBox.warning(self, "提示", "请先在②页完成校准")
            return

        hwnd = int(hwnd)
        cal = Calibration()
        cal.from_dict(cal_data)
        # 分辨率/窗口大小/模拟器自适应：按当前客户区尺寸重映射校准坐标。
        # 旧校准基于"当时客户区尺寸"，若与现在不同（换分辨率/改窗口/模拟器），
        # 等比例缩放后画布仍是 24×24 格、色板行列数不变。
        r = self._region()
        cal = cal.remap(max(1, r[2] - r[0]), max(1, r[3] - r[1]))

        plan = paint_order(self.idx_mat, strategy=self._strategy())
        if not plan:
            QMessageBox.warning(self, "提示", "矩阵全为白色，无需绘画")
            return

        selected_mode = self.input_mode_combo.currentIndex()
        if selected_mode == 3:
            self._log("实验模式：检查原生 FramePool/D3D11 组件…")
            try:
                import native_maa_backend  # noqa: F401
            except Exception:
                self._log("实验模式不可用：未找到原生 MaaFramework 组件，自动回退 WindowPos。")
                input_mode = "windowpos"
            else:
                input_mode = "experimental"
        else:
            input_mode = ["sendinput", "windowpos", "plain"][selected_mode]

        # 所有模式都需要同等级权限：WindowPos 需要移动目标窗口，
        # SendInput 需要注入硬件事件；权限不一致都会产生假完成。
        if not _privilege_ok(hwnd):
            return

        # 仅 SendInput 依赖系统前台焦点。WindowPos/纯消息与 MAA PC
        # 控制方式一致：通过目标窗口消息和窗口位置换算操作，可后台执行。
        if input_mode == "sendinput":
            game_input = WindowInput(hwnd)
            try:
                activated = game_input.activate_foreground(force=True, aggressive=True)
            except Exception as e:
                activated = False
                self._log(f"游戏窗口激活异常：{e}")
            if not activated or not game_input.is_foreground():
                self._log("绘画未启动：SendInput 模式需要游戏窗口处于前台。")
                QMessageBox.warning(
                    self, "请先前置游戏窗口",
                    "SendInput 模式需要游戏窗口处于前台。\n\n"
                    "请先点击游戏窗口，或切换为“WindowPos（MAA PC 推荐）”以使用后台控制。")
                return
        else:
            self._log("WindowPos/消息模式：允许游戏窗口在后台运行。")

        self.progress_bar.setMaximum(len(plan))
        self.progress_bar.setValue(0)
        self._log(f"开始绘画：{len(plan)} 格，策略「{self.strategy_combo.currentText()}」"
                 f"，输入「{self.input_mode_combo.currentText()}」")
        self._log(f"窗口句柄: 0x{hwnd:X} | 运行时输入模式: {input_mode}")

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._stop_requested = False
        self._register_hotkey()
        self._log("任务监控已启动；按 F8 / ESC 或监控窗中的停止按钮急停")
        self.monitor = TaskMonitorDialog(self.window())
        self.monitor.stop_requested.connect(self.on_stop)
        self.monitor.show()
        self.monitor.raise_()

        # 关键：Painter（含 dxcam 截图）必须在子线程内创建和使用，
        # 捕获器/设备与线程亲和，跨线程创建会崩溃。
        def run():
            try:
                # 赋给 self.painter，使"停止/急停"能真正触达绘画循环。
                self.painter = Painter(hwnd, cal, self._region(),
                                       progress_cb=lambda p: self.progress_signal.emit(p),
                                       verify=self.verify_check.isChecked(),
                                       input_mode=input_mode)
                # 启动空窗期内用户已急停（此时 painter 尚未就绪）
                if self._stop_requested:
                    self.painter.stop()
                self.painter.paint(plan)
                # paint() 内部已负责关闭捕获器
            except Exception:
                import traceback
                traceback.print_exc()
                # 把错误带回主线程展示，避免"点了没反应"又看不见原因
                self.error_signal.emit(traceback.format_exc())
            finally:
                self.done_signal.emit()   # 通过信号回到主线程更新 UI

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def _region(self):
        # 优先用窗口客户区屏幕矩形（精确匹配 WGC 坐标）
        hwnd = getattr(self, "hwnd", None) or load_config().get("window_hwnd")
        if hwnd:
            import ctypes
            import ctypes.wintypes as wt
            rc = wt.RECT()
            ctypes.windll.user32.GetClientRect(int(hwnd), ctypes.byref(rc))
            pt = wt.POINT(0, 0)
            ctypes.windll.user32.ClientToScreen(int(hwnd), ctypes.byref(pt))
            return (pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom)
        cfg = load_config()
        r = cfg.get("window_region")
        if r:
            return tuple(r)
        return (0, 0, 1920, 1080)

    def _on_done(self):
        """绘画结束（主线程执行 UI 更新）"""
        last_status = getattr(self.painter.progress, "status_msg", "") if self.painter else ""
        done = getattr(self.painter.progress, "done", 0) if self.painter else 0
        total = getattr(self.painter.progress, "total", 0) if self.painter else 0
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.painter = None
        self._unregister_hotkey()
        if last_status.startswith("色板校准失败") or last_status.startswith("第 "):
            message = f"任务已中止：{last_status}"
            self._log(f"绘画未开始/已中止：{last_status}")
        elif done < total:
            message = f"任务已中止：{done}/{total} 格"
            self._log(message)
        else:
            message = "绘画完成"
            self._log(message)
        if self.monitor:
            self.monitor.finish(message)

    def _on_paint_error(self, trace: str):
        """绘画线程异常（主线程弹窗展示，避免静默失败）"""
        self._log("绘画异常：" + trace.splitlines()[-1] if trace else "未知错误")
        QMessageBox.critical(self, "绘画出错",
                             "绘画过程中发生错误：\n\n" + trace[-800:] +
                             "\n\n请查看日志或稍后重试。")

    def on_stop(self):
        """停止/急停入口（按钮 + ESC 快捷键 + 全局热键 F8 共用）"""
        self._stop_requested = True
        if self.painter:
            self.painter.stop()
            self._log("急停指令已发送，正在停止当前格子……")
        else:
            self._log("急停已记录（绘画线程尚未就绪，启动后将立即停止）")

    def _register_hotkey(self):
        """注册系统级全局热键 F8（不依赖窗口焦点，游戏在前台也能急停）"""
        if self._hotkey_registered:
            return
        try:
            self._hotkey_hwnd = int(self.winId())
            if RegisterHotKey(self._hotkey_hwnd, HOTKEY_PAINT_STOP,
                              MOD_NOREPEAT, VK_F8):
                self._hotkey_registered = True
                self._log("已注册全局急停热键 F8（游戏窗口内同样有效）")
            else:
                self._log("F8 热键注册失败（可能被其他程序占用），仍可用 ESC 急停")
        except Exception as e:
            self._log("热键注册异常：" + str(e))

    def _unregister_hotkey(self):
        if self._hotkey_registered and self._hotkey_hwnd:
            try:
                UnregisterHotKey(self._hotkey_hwnd, HOTKEY_PAINT_STOP)
            except Exception:
                pass
        self._hotkey_registered = False
        self._hotkey_hwnd = 0

    def _restore_tool_window(self):
        """绘画结束：恢复工具窗口并聚焦，方便查看结果与日志"""
        top = self.window()
        if top.isMinimized():
            top.showNormal()
        top.activateWindow()
        top.raise_()

    def shutdown(self):
        """窗口销毁前清理：注销全局热键，避免残留占用"""
        self._unregister_hotkey()

    def nativeEvent(self, eventType, message):
        """拦截系统级 WM_HOTKEY（F8 急停；绘画期间窗口已最小化也能触发）"""
        try:
            if self._hotkey_registered:
                # PySide6: message 是指向 MSG 结构的指针（int）
                ptr = int(message)
                if ptr:
                    msg_id = ctypes.c_uint.from_address(ptr + 8).value
                    wparam = ctypes.c_size_t.from_address(ptr + 16).value
                    if msg_id == WM_HOTKEY and wparam == HOTKEY_PAINT_STOP:
                        self.on_stop()
                        return (True, 0)
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def _on_progress(self, p: PaintingProgress):
        # 停止/异常原因透传（如 ESC 急停、游戏窗口失焦超时）
        if getattr(p, "status_msg", "") and p.status_msg != self._last_status_msg:
            self._last_status_msg = p.status_msg
            self._log("▶ " + p.status_msg)
        self.progress_bar.setValue(p.done)
        if self.monitor and self.monitor.isVisible():
            self.monitor.update_progress(p)
        elapsed = time.time() - p.start_time if p.start_time else 0
        rate = p.done / elapsed if elapsed > 0 else 0
        cur = EXHIBITION_PALETTE[p.cur_color_idx][0] if p.cur_color_idx is not None else "-"
        eta = (p.total - p.done) / rate if rate > 0 else 0
        self._log(f"[{p.done}/{p.total}] 当前色 {cur} | "
                  f"用时 {elapsed:.0f}s | {rate:.2f} 格/s | 剩余约 {eta:.0f}s")


# ===========================================================================
# 主窗口（向导式）
# ===========================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ark9Tools - 游戏辅助工具中心")
        self.resize(1220, 860)
        self.setMinimumSize(1080, 760)
        self.setStyleSheet(STYLE)

        # 共享上下文（当前模块与后续工具模块共用）
        class Ctx:
            idx_mat = None
            hwnd = None
        self.ctx = Ctx()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header = QFrame()
        header.setProperty("class", "header")
        head = QHBoxLayout(header)
        head.setContentsMargins(18, 12, 18, 12)
        brand_col = QVBoxLayout()
        eyebrow = QLabel("PIXEL WORKSPACE  /  TOOLKIT")
        eyebrow.setProperty("class", "eyebrow")
        brand_col.addWidget(eyebrow)
        brand = QLabel("Ark9Tools")
        brand.setProperty("class", "brand")
        brand_col.addWidget(brand)
        head.addLayout(brand_col)
        head.addStretch()
        self.game_status = QLabel("游戏窗口：等待连接")
        self.game_status.setProperty("muted", "true")
        head.addWidget(self.game_status)
        head.addSpacing(14)
        self.session_status = QLabel("● 就绪")
        self.session_status.setProperty("class", "statusGood")
        head.addWidget(self.session_status)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(10)
        sidebar = QFrame()
        sidebar.setProperty("class", "sidebar")
        sidebar.setFixedWidth(174)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 14, 10, 14)
        side.setSpacing(5)
        nav_title = QLabel("工作区")
        nav_title.setProperty("class", "eyebrow")
        side.addWidget(nav_title)
        self.module_buttons = []
        for index, name in enumerate(("工具中心", "MAA-PixelPainter", "连接中心", "截图诊断")):
            btn = QPushButton(name)
            btn.setProperty("navModule", "true")
            btn.clicked.connect(lambda checked=False, target=index: self._set_module(target))
            self.module_buttons.append(btn)
            side.addWidget(btn)
        side.addSpacing(16)
        section = QLabel("当前模块")
        section.setProperty("class", "eyebrow")
        side.addWidget(section)
        self.module_hint = QLabel("工具中心\n选择一个功能模块开始工作。")
        self.module_hint.setProperty("class", "subtitle")
        self.module_hint.setWordWrap(True)
        side.addWidget(self.module_hint)
        side.addStretch()
        safety = QFrame()
        safety.setProperty("class", "statusCard")
        safety_layout = QVBoxLayout(safety)
        safety_layout.setContentsMargins(10, 9, 10, 9)
        safety_label = QLabel("运行安全")
        safety_label.setProperty("class", "eyebrow")
        safety_layout.addWidget(safety_label)
        safety_text = QLabel("F8 / ESC\n全局急停")
        safety_text.setProperty("class", "subtitle")
        safety_layout.addWidget(safety_text)
        side.addWidget(safety)
        body.addWidget(sidebar)

        self.module_stack = QStackedWidget()
        self.module_stack.addWidget(self._build_dashboard())

        painter_shell = QFrame()
        painter_shell.setProperty("class", "contentShell")
        content = QVBoxLayout(painter_shell)
        content.setContentsMargins(6, 4, 6, 8)
        content.setSpacing(8)
        self.stack = QStackedWidget()
        self.step_pixelate = StepPixelate(self.ctx)
        self.step_calibrate = StepCalibrate(self.ctx)
        self.step_paint = StepPaint(self.ctx)
        self.stack.addWidget(self.step_pixelate)
        self.stack.addWidget(self.step_calibrate)
        self.stack.addWidget(self.step_paint)
        content.addWidget(self.stack, 1)
        workflow = QHBoxLayout()
        workflow.setContentsMargins(12, 0, 12, 0)
        self.btn_prev = QPushButton("← 上一步")
        self.btn_prev.clicked.connect(self.on_prev)
        workflow.addWidget(self.btn_prev)
        workflow.addStretch()
        self.step_caption = QLabel("1 / 3  ·  图像处理")
        self.step_caption.setProperty("muted", "true")
        workflow.addWidget(self.step_caption)
        workflow.addStretch()
        self.btn_next = QPushButton("继续 →")
        _set_type(self.btn_next, "primary")
        self.btn_next.setMinimumWidth(132)
        self.btn_next.clicked.connect(self.on_next)
        workflow.addWidget(self.btn_next)
        content.addLayout(workflow)
        self.module_stack.addWidget(painter_shell)
        self.module_stack.addWidget(self._build_connection_center())
        self.module_stack.addWidget(self._build_diagnostics_center())
        body.addWidget(self.module_stack, 1)
        root.addLayout(body, 1)

        self.current_step = 0
        self.current_module = 0
        self._set_module(0)

    def _build_dashboard(self) -> QWidget:
        page = QFrame()
        page.setProperty("class", "contentShell")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        eyebrow = QLabel("ARK9TOOLS / TOOL CENTER")
        eyebrow.setProperty("class", "eyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("把游戏画面识别、校准和执行，集中在一个工作台里")
        title.setProperty("class", "title")
        layout.addWidget(title)
        sub = QLabel("基于图像识别与 Win32 控制，统一管理连接状态、校准基准和自动化任务。")
        sub.setProperty("class", "subtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        pixel = QPushButton("MAA-PixelPainter\n将图片转换为像素矩阵，校准色板并自动绘画")
        pixel.setProperty("dashboardCard", "true")
        pixel.setMinimumHeight(108)
        pixel.clicked.connect(lambda: self._set_module(1))
        grid.addWidget(pixel, 0, 0)
        connection = QPushButton("连接中心\n发现游戏窗口，检查权限和当前校准状态")
        connection.setProperty("dashboardCard", "true")
        connection.setMinimumHeight(108)
        connection.clicked.connect(lambda: self._set_module(2))
        grid.addWidget(connection, 0, 1)
        diagnostics = QPushButton("截图诊断\n查看当前捕获策略和色板检测状态，不发送输入")
        diagnostics.setProperty("dashboardCard", "true")
        diagnostics.setMinimumHeight(108)
        diagnostics.clicked.connect(lambda: self._set_module(3))
        grid.addWidget(diagnostics, 1, 0)
        upcoming = QPushButton("任务编排\n将常用流程组织成可重复执行的任务")
        upcoming.setProperty("dashboardCard", "true")
        upcoming.setEnabled(False)
        upcoming.setMinimumHeight(108)
        grid.addWidget(upcoming, 1, 1)
        layout.addLayout(grid)
        layout.addStretch()
        return page

    def _build_connection_center(self) -> QWidget:
        page = QFrame()
        page.setProperty("class", "contentShell")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        title = QLabel("连接中心")
        title.setProperty("class", "title")
        layout.addWidget(title)
        sub = QLabel("管理当前游戏窗口与校准基准。连接成功后，所有自动化模块共享该窗口上下文。")
        sub.setProperty("class", "subtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        card = QFrame()
        card.setProperty("class", "statusCard")
        form = QVBoxLayout(card)
        form.setContentsMargins(16, 14, 16, 14)
        self.connection_summary = QLabel("尚未连接游戏窗口")
        self.connection_summary.setProperty("class", "moduleTitle")
        form.addWidget(self.connection_summary)
        detail = QLabel("进入 PixelPainter 的“游戏校准”步骤可选择窗口、自动检测画布与色板。")
        detail.setProperty("class", "subtitle")
        detail.setWordWrap(True)
        form.addWidget(detail)
        layout.addWidget(card)
        go = QPushButton("打开 PixelPainter 校准")
        _set_type(go, "primary")
        go.clicked.connect(lambda: self._open_pixel_step(1))
        layout.addWidget(go, alignment=Qt.AlignLeft)
        layout.addStretch()
        return page

    def _build_diagnostics_center(self) -> QWidget:
        page = QFrame()
        page.setProperty("class", "contentShell")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        title = QLabel("截图诊断")
        title.setProperty("class", "title")
        layout.addWidget(title)
        sub = QLabel("只读检查当前游戏连接、截图后端与色板校准。此工作区不会向游戏发送点击或拖动。")
        sub.setProperty("class", "subtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        self.diagnostics_summary = QTextEdit()
        self.diagnostics_summary.setReadOnly(True)
        self.diagnostics_summary.setMinimumHeight(240)
        layout.addWidget(self.diagnostics_summary, 1)
        refresh = QPushButton("刷新当前状态")
        _set_type(refresh, "primary")
        refresh.clicked.connect(self._refresh_diagnostics)
        layout.addWidget(refresh, alignment=Qt.AlignLeft)
        return page

    def _set_module(self, target: int):
        self.current_module = target
        self.module_stack.setCurrentIndex(target)
        names = ["工具中心", "MAA-PixelPainter", "连接中心", "截图诊断"]
        hints = [
            "工具中心\n选择一个功能模块开始工作。",
            "MAA-PixelPainter\n像素化、校准与自动绘画。",
            "连接中心\n管理目标窗口与共享校准。",
            "截图诊断\n安全检查捕获与色板状态。",
        ]
        self.module_hint.setText(hints[target])
        for index, button in enumerate(self.module_buttons):
            button.setProperty("moduleState", "active" if index == target else "idle")
            button.style().unpolish(button)
            button.style().polish(button)
        if target == 2:
            hwnd = getattr(self.ctx, "hwnd", None)
            self.connection_summary.setText(
                f"已连接窗口：0x{hwnd:X}" if hwnd else "尚未连接游戏窗口")
        if target == 3:
            self._refresh_diagnostics()
        self._update_nav()

    def _open_pixel_step(self, step: int):
        self._set_module(1)
        self._go_to_step(step)

    def _refresh_diagnostics(self):
        cfg = load_config()
        hwnd = getattr(self.ctx, "hwnd", None) or cfg.get("window_hwnd")
        lines = ["Ark9Tools / 只读诊断", ""]
        if hwnd:
            lines.append(f"目标窗口: 0x{int(hwnd):X}")
            lines.append(f"权限状态: {'管理员' if is_elevated() else '普通用户'}")
        else:
            lines.append("目标窗口: 未连接")
        cal = cfg.get("calibration", {})
        lines.append(f"色板基准: x={cal.get('palette_left', '-')}  y={cal.get('palette_grid_top', '-')}")
        lines.append(f"色板间距: {cal.get('palette_col_gap', '-')} × {cal.get('palette_row_gap', '-')}")
        lines.append("")
        lines.append("诊断中心只展示状态；实际校准请进入 MAA-PixelPainter 模块。")
        self.diagnostics_summary.setPlainText("\n".join(lines))

    def _update_nav(self):
        names = ["图像处理", "游戏校准", "执行绘画"]
        self.btn_prev.setEnabled(self.current_step > 0)
        self.btn_next.setText("完成工作流" if self.current_step == 2 else "继续 →")
        self.step_caption.setText(f"{self.current_step + 1} / 3  ·  {names[self.current_step]}")
        hwnd = getattr(self.ctx, "hwnd", None)
        if hwnd:
            self.game_status.setText(f"游戏窗口：0x{hwnd:X} 已连接")
            self.game_status.setProperty("accent", "true")
        else:
            self.game_status.setText("游戏窗口：等待连接")
            self.game_status.setProperty("muted", "true")

    def _go_to_step(self, target: int):
        if target > 0 and getattr(self.ctx, "idx_mat", None) is None:
            QMessageBox.information(self, "请先完成图像处理", "请先生成 24×24 像素矩阵，再进入后续模块。")
            return
        if target > 1:
            cfg = load_config()
            if not getattr(self.ctx, "hwnd", None) and not cfg.get("window_hwnd"):
                QMessageBox.information(self, "请先连接游戏", "请先在“游戏校准”中选择目标窗口。")
                return
            self.step_paint._load_from_ctx()
        self.current_step = target
        self.stack.setCurrentIndex(target)
        self._update_nav()

    def on_next(self):
        if self.current_step == 0:
            if getattr(self.ctx, "idx_mat", None) is None:
                QMessageBox.warning(self, "提示", "请先在①页转换像素图")
                return
            self.step_paint._load_from_ctx()
        elif self.current_step == 1:
            cfg = load_config()
            if not getattr(self.ctx, "hwnd", None) and not cfg.get("window_hwnd"):
                QMessageBox.warning(self, "提示", "请先在②页选择游戏窗口")
                return
        if self.current_step < 2:
            self._go_to_step(self.current_step + 1)
        else:
            self._set_module(0)

    def on_prev(self):
        if self.current_step > 0:
            self._go_to_step(self.current_step - 1)

    def closeEvent(self, event):
        # 窗口关闭前注销全局热键，避免 F8 被占用残留
        self.step_paint.shutdown()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Ark9Tools")
    app.setWindowIcon(app_icon())  # 窗口/任务栏 Logo
    win = MainWindow()
    win.setWindowIcon(app_icon())
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
