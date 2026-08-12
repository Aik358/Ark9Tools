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
from PySide6.QtCore import (Qt, Signal, QUrl, QRect, QPoint, QEvent, QTimer,
                            QObject, QAbstractNativeEventFilter,
                            QPropertyAnimation, QEasingCurve, QAbstractAnimation,
                            QParallelAnimationGroup, QVariantAnimation)
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import (QImage, QPixmap, QColor, QShortcut, QKeySequence, QFont,
                           QIcon, QDragEnterEvent, QDropEvent, QPainter, QPen)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QLineEdit, QSlider, QCheckBox,
    QGroupBox, QProgressBar, QTextEdit, QComboBox, QMessageBox,
    QFrame, QGridLayout, QSpinBox, QFormLayout, QStackedWidget, QSizePolicy,
    QScrollArea, QScrollBar, QInputDialog,
)

# ===========================================================================
# 动效工具
# ===========================================================================
# 禁止对 QWidget 使用 QGraphicsEffect：它会创建离屏绘制设备，和自定义
# paintEvent 同时发生时会触发 QPainter "only be painted by one painter"。
_ANIM_ACTIVE = True


def _keep_anim(anim):
    """让 Qt 托管动画生命周期，避免 Python 回收后动画中断。"""
    anim.setParent(QApplication.instance())
    anim.start(QAbstractAnimation.DeleteWhenStopped)


def _smooth_progress(bar: QProgressBar, value: int, duration: int = 180):
    """仅动画数值属性，不参与 QWidget 绘制。"""
    current = bar.value()
    if value == current:
        return
    anim = QPropertyAnimation(bar, b"value", bar)
    anim.setDuration(duration)
    anim.setStartValue(current)
    anim.setEndValue(value)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    _keep_anim(anim)


def _dialog_anchor(dialog: QDialog) -> QPoint:
    """优先从触发控件中心开合，未指定时回退到父窗口中心。"""
    source = getattr(dialog, "_animation_source", None)
    if isinstance(source, QWidget) and source.isVisible():
        return source.mapToGlobal(source.rect().center())
    parent = dialog.parentWidget()
    if parent is not None:
        return parent.mapToGlobal(parent.rect().center())
    return dialog.frameGeometry().center()


def _collapsed_geometry(dialog: QDialog, anchor: QPoint) -> QRect:
    geo = dialog.geometry()
    width = max(36, geo.width() // 8)
    height = max(28, geo.height() // 8)
    return QRect(anchor.x() - width // 2, anchor.y() - height // 2, width, height)


def slide_switch_stacked(stack: QStackedWidget, index: int, duration: int = 180,
                         direction: int = 1):
    """稳定切页：布局只负责页面位置，避免对栈页做位置/图形效果动画。"""
    stack.setCurrentIndex(index)


def pop_in_dialog(dialog: QDialog, duration: int = 180):
    """从触发位置平滑展开，无回弹、无 QWidget 图形效果。"""
    if not _ANIM_ACTIVE:
        dialog.show()
        dialog.raise_()
        return
    final_geo = dialog.geometry()
    start_geo = _collapsed_geometry(dialog, _dialog_anchor(dialog))
    dialog.setGeometry(start_geo)
    dialog.setWindowOpacity(0.0)
    dialog.show()
    dialog.raise_()
    group = QParallelAnimationGroup(dialog)
    geo_anim = QPropertyAnimation(dialog, b"geometry", group)
    geo_anim.setDuration(duration)
    geo_anim.setStartValue(start_geo)
    geo_anim.setEndValue(final_geo)
    geo_anim.setEasingCurve(QEasingCurve.OutCubic)
    opacity = QPropertyAnimation(dialog, b"windowOpacity", group)
    opacity.setDuration(duration)
    opacity.setStartValue(0.0)
    opacity.setEndValue(1.0)
    opacity.setEasingCurve(QEasingCurve.OutCubic)
    group.addAnimation(geo_anim)
    group.addAnimation(opacity)
    _keep_anim(group)


def pop_out_dialog(dialog: QDialog, duration: int = 150, on_finished=None):
    """沿同一触发锚点收回，关闭后才真正 reject。"""
    if not _ANIM_ACTIVE:
        if on_finished:
            on_finished()
        return
    group = QParallelAnimationGroup(dialog)
    geo_anim = QPropertyAnimation(dialog, b"geometry", group)
    geo_anim.setDuration(duration)
    geo_anim.setStartValue(dialog.geometry())
    geo_anim.setEndValue(_collapsed_geometry(dialog, _dialog_anchor(dialog)))
    geo_anim.setEasingCurve(QEasingCurve.InCubic)
    opacity = QPropertyAnimation(dialog, b"windowOpacity", group)
    opacity.setDuration(duration)
    opacity.setStartValue(dialog.windowOpacity())
    opacity.setEndValue(0.0)
    opacity.setEasingCurve(QEasingCurve.InCubic)
    group.addAnimation(geo_anim)
    group.addAnimation(opacity)
    if on_finished:
        group.finished.connect(on_finished)
    _keep_anim(group)


class DialogAnimMixin:
    """为对话框提供按来源开合的无回弹转场。"""

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_anim_shown", False):
            self._anim_shown = True
            QTimer.singleShot(0, lambda: pop_in_dialog(self))

    def closeEvent(self, event):
        if getattr(self, "_anim_closing", False) or not _ANIM_ACTIVE:
            super().closeEvent(event)
            return
        event.ignore()
        self._anim_closing = True

        def finish_close():
            self._anim_closing = False
            self._anim_shown = False
            self.reject()

        pop_out_dialog(self, on_finished=finish_close)


def attach_hover_raise(widget: QWidget, lift: int = 0, shadow: bool = False):
    """保留统一悬停状态入口，不再移动控件或安装图形效果。"""
    return

from palette import GAME_PALETTE_DATA, GAME_WHITE_INDEX, rgb_to_hex
from pixelate import (pixelate, detect_24x24_box, parse_blueprint_region,
                      save_visual_preview, count_color_usage, paint_order,
                      parse_collection_page_detailed)
from calibration import Calibration, load_config, save_config
from painter import Painter, PaintingProgress
from history_store import PixelHistoryStore
from calibration_dialog import VisualCalibrationDialog
from privilege import check_game_privilege, is_elevated, relaunch_as_admin
from win32_input import (RegisterHotKey, UnregisterHotKey, WM_HOTKEY,
                         MOD_NOREPEAT, VK_F8, HOTKEY_PAINT_STOP, WindowInput)

APP_NAME = "Ark9Tools"


def resource_path(*parts: str) -> str:
    """返回开发环境或 PyInstaller 打包环境中的资源绝对路径。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    direct = os.path.join(base, *parts)
    internal = os.path.join(base, "_internal", *parts)
    return internal if os.path.exists(internal) else direct


def app_icon() -> QIcon:
    """加载应用 Logo（兼容 PyInstaller 打包后的资源目录）。"""
    for name in ("app.ico", "logo.png"):
        p = resource_path("assets", name)
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
QPushButton[btnType="primary"]:hover { background: #1d8a84; border-color: #38a89f; }
QPushButton[btnType="primary"]:pressed { background: #0f5c59; }
QPushButton[btnType="success"] { background: #256b55; color: #ffffff; border: 1px solid #2f8267; }
QPushButton[btnType="success"]:hover { background: #2b7b61; border-color: #3f9a7c; }
QPushButton[btnType="success"]:pressed { background: #1d5444; }
QPushButton[btnType="danger"] { background: #963f47; color: #ffffff; border: 1px solid #b5515a; }
QPushButton[btnType="danger"]:hover { background: #a94952; border-color: #cf6a73; }
QPushButton[btnType="danger"]:pressed { background: #7c3339; }
QPushButton[navModule="true"] { text-align: left; padding: 10px 12px; color: #9fb0bf; background: transparent; border: 1px solid transparent; }
QPushButton[navModule="true"]:hover { background: #14202b; color: #d9e5ee; border-color: #263a49; }
QPushButton[moduleState="active"] { background: #12383d; color: #dcfff8; border-color: #2c918b; }
QPushButton[dashboardCard="true"] { text-align: left; background: #121c26; border: 1px solid #263a49; border-radius: 8px; padding: 15px 16px; }
QPushButton[dashboardCard="true"]:hover { background: #162634; border-color: #3c7978; }
QPushButton[dashboardCard="true"]:pressed { background: #0f1922; }
QPushButton[hovered="true"] { border-color: #3c7978; }
QFrame[hovered="true"] { border-color: #3c7978; }

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
    """把 24×24 索引矩阵放大成预览图（numpy 批量，避免逐像素慢）。"""
    h, w = idx_mat.shape
    H, W = h * cell, w * cell
    # 色板索引 → RGB 查表，一次性生成整幅 RGB 数组
    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            color = GAME_PALETTE_DATA[int(idx_mat[y, x])][1]
            rgb[y * cell:(y + 1) * cell, x * cell:(x + 1) * cell] = color
    img = QImage(rgb.data, W, H, 3 * W, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(img)


class LoadingOverlay(QDialog):
    """耗时任务期间的非交互加载层。

    使用色键抠图后的透明 PNG 帧序列 + QTimer 轮换，实现：
    - 真正透明背景（无黑块）；
    - 无限循环（帧序列循环播放）；
    - 窗口无边框、始终置顶、可点击穿透；
    - 默认延迟 300ms 才真正显示：耗时短的任务不会闪现，
      耗时长的任务由调用方持续显示，stop() 时立即消失。
    """

    FRAME_INTERVAL_MS = 40  # 25fps，17 帧约 0.68s 一个循环

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setModal(False)
        self.setFixedSize(300, 300)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)
        self._message = QLabel("正在处理…", self)
        self._message.setAlignment(Qt.AlignCenter)
        self._message.setStyleSheet("color:#eaf4ff;font-weight:650;background:transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(4)
        layout.addStretch(1)
        layout.addWidget(self._label)
        layout.addWidget(self._message)
        layout.addStretch(1)

        # 加载透明帧序列（色键抠图后的 loading_frames/*.png）
        self._frames = []
        frame_dir = resource_path("assets", "loading_frames")
        try:
            names = sorted(n for n in os.listdir(frame_dir)
                           if n.startswith("frame_") and n.endswith(".png"))
            for n in names:
                pm = QPixmap(os.path.join(frame_dir, n))
                if not pm.isNull():
                    self._frames.append(pm)
        except OSError:
            self._frames = []
        self._frame_index = 0
        if self._frames:
            self._label.setPixmap(self._frames[0])

        # 帧轮换定时器：天然无限循环
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._next_frame)
        # 延迟显示定时器：短任务不闪现
        self._delay_timer = QTimer(self)
        self._delay_timer.setSingleShot(True)
        self._delay_timer.timeout.connect(self._do_show)

    def _next_frame(self):
        if not self._frames:
            return
        self._frame_index = (self._frame_index + 1) % len(self._frames)
        self._label.setPixmap(self._frames[self._frame_index])

    def start(self, message: str, delay_ms: int = 300):
        """准备显示；delay_ms 内若 stop() 则不显示，避免短任务闪烁。"""
        self._message.setText(message)
        self._delay_timer.start(max(0, delay_ms))

    def _do_show(self):
        parent = self.parentWidget()
        if parent is not None:
            center = parent.mapToGlobal(parent.rect().center())
        elif self.screen() is not None:
            center = self.screen().availableGeometry().center()
        else:
            center = None
        if center is not None:
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        self.show()
        self.raise_()
        self.activateWindow()
        self._anim_timer.start(self.FRAME_INTERVAL_MS)

    def stop(self):
        self._delay_timer.stop()
        self._anim_timer.stop()
        self.hide()


class ImageCropCanvas(QWidget):
    """固定1:1裁剪框：四角/四边调整，框内移动，滚轮缩放预览。"""
    selection_changed = Signal(tuple)

    def __init__(self, image_path: str, selection: tuple[int, int, int, int], parent=None):
        super().__init__(parent)
        self.image = QImage(image_path)
        self.selection = self._square_selection(selection)
        self.zoom = 1.0
        self._drag_start = None
        self._origin = None
        self._mode = None
        self.setMinimumSize(620, 460)
        self.setMouseTracking(True)

    def _square_selection(self, selection):
        width, height = self.image.width(), self.image.height()
        l, t, r, b = map(int, selection)
        side = max(24, min(width, height, r - l, b - t))
        cx, cy = (l + r) // 2, (t + b) // 2
        l = max(0, min(width - side, cx - side // 2))
        t = max(0, min(height - side, cy - side // 2))
        return l, t, l + side, t + side

    def _display_rect(self):
        if self.image.isNull():
            return QRect()
        fit = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
        width = max(1, round(fit.width() * self.zoom))
        height = max(1, round(fit.height() * self.zoom))
        return QRect((self.width() - width) // 2, (self.height() - height) // 2, width, height)

    def _to_image(self, point: QPoint):
        rect = self._display_rect()
        x = (point.x() - rect.x()) * self.image.width() / max(1, rect.width())
        y = (point.y() - rect.y()) * self.image.height() / max(1, rect.height())
        return QPoint(max(0, min(round(x), self.image.width())), max(0, min(round(y), self.image.height())))

    def _selection_rect(self):
        rect = self._display_rect()
        l, t, r, b = self.selection
        return QRect(rect.x() + round(l * rect.width() / self.image.width()),
                     rect.y() + round(t * rect.height() / self.image.height()),
                     max(1, round((r - l) * rect.width() / self.image.width())),
                     max(1, round((b - t) * rect.height() / self.image.height())))

    def _handle_at(self, point: QPoint):
        selected = self._selection_rect()
        threshold = 12
        # 框内（且不靠近四边/四角）按下 → 移动整个选区；
        # 只有命中角点/边中点附近才进入“调整形状”。先判断移动区，
        # 避免角点命中优先导致无法拖动整体。
        inner = QRect(selected.adjusted(threshold, threshold, -threshold, -threshold))
        if inner.contains(point):
            return "move"
        points = {
            "tl": selected.topLeft(), "tm": QPoint(selected.center().x(), selected.top()),
            "tr": selected.topRight(), "ml": QPoint(selected.left(), selected.center().y()),
            "mr": QPoint(selected.right(), selected.center().y()), "bl": selected.bottomLeft(),
            "bm": QPoint(selected.center().x(), selected.bottom()), "br": selected.bottomRight(),
        }
        for name, handle in points.items():
            if abs(point.x() - handle.x()) <= threshold and abs(point.y() - handle.y()) <= threshold:
                return name
        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#0a1118"))
            rect = self._display_rect()
            if rect.isEmpty():
                return
            painter.drawImage(rect, self.image)
            selected = self._selection_rect()
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.setPen(Qt.NoPen)
            painter.drawRect(self.rect())
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.drawImage(selected, self.image, QRect(
                self.selection[0], self.selection[1], self.selection[2] - self.selection[0],
                self.selection[3] - self.selection[1]))
            painter.setPen(QPen(QColor("#54d0c0"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(selected)
            painter.setPen(QPen(QColor(84, 208, 192, 150), 1))
            for n in range(1, 24):
                x = selected.left() + n * selected.width() / 24
                y = selected.top() + n * selected.height() / 24
                painter.drawLine(round(x), selected.top(), round(x), selected.bottom())
                painter.drawLine(selected.left(), round(y), selected.right(), round(y))
            painter.setBrush(QColor("#54d0c0"))
            painter.setPen(Qt.NoPen)
            for point in (selected.topLeft(), QPoint(selected.center().x(), selected.top()), selected.topRight(),
                          QPoint(selected.left(), selected.center().y()), QPoint(selected.right(), selected.center().y()),
                          selected.bottomLeft(), QPoint(selected.center().x(), selected.bottom()), selected.bottomRight()):
                painter.drawEllipse(point, 5, 5)
        finally:
            painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._drag_start = self._to_image(event.position().toPoint())
        self._origin = self.selection
        self._mode = self._handle_at(event.position().toPoint())

    def mouseMoveEvent(self, event):
        if self._drag_start is None or self._mode is None:
            return
        current = self._to_image(event.position().toPoint())
        dx, dy = current.x() - self._drag_start.x(), current.y() - self._drag_start.y()
        l, t, r, b = self._origin
        side = r - l
        if self._mode == "move":
            # 移动整个选区：四个边一起平移，保持尺寸不变。
            # 只改 l,t 而保留 r,b 会把正方形拖成矩形，是“中间拖动改形状”的元凶。
            l = max(0, min(self.image.width() - side, l + dx))
            t = max(0, min(self.image.height() - side, t + dy))
            r = l + side
            b = t + side
        else:
            if self._mode in ("tl", "ml", "bl"):
                l = max(0, min(r - 24, l + dx))
            if self._mode in ("tr", "mr", "br"):
                r = max(l + 24, min(self.image.width(), r + dx))
            if self._mode in ("tl", "tm", "tr"):
                t = max(0, min(b - 24, t + dy))
            if self._mode in ("bl", "bm", "br"):
                b = max(t + 24, min(self.image.height(), b + dy))
            # 以调整后的最大边为正方形边长，锚定对角/边中心。
            side = max(24, min(self.image.width(), self.image.height(), r - l, b - t))
            if self._mode in ("tl", "ml", "bl"):
                l, r = r - side, r
            else:
                r = l + side
            if self._mode in ("tl", "tm", "tr"):
                t, b = b - side, b
            else:
                b = t + side
            l, t = max(0, l), max(0, t)
            r, b = min(self.image.width(), l + side), min(self.image.height(), t + side)
        self.selection = (int(l), int(t), int(r), int(b))
        self.selection_changed.emit(self.selection)
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        self._origin = None
        self._mode = None

    def wheelEvent(self, event):
        self.zoom = max(0.5, min(3.0, self.zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        self.update()

class ImageActionDialog(DialogAnimMixin, QDialog):
    """拖入/选择图片后的处理方式选择浮窗。

    显示检测到的图片预览与文件名，并提供可用的处理选项。当前只提供
    “绘制像素画”，其余选项在布局中预留位置，后续可按需追加按钮。
    """

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.setWindowTitle("检测到图片")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.resize(480, 430)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        title = QLabel("检测到图片，请选择处理方式")
        title.setStyleSheet("font-size:18px;font-weight:800;")
        layout.addWidget(title)
        self.preview = QLabel("无法预览该图片")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(250)
        self.preview.setStyleSheet("background:#f3f4f5;border:1px solid #c8cdd1;")
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            self.preview.setPixmap(
                pixmap.scaled(440, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(self.preview, 1)
        self.file_label = QLabel(os.path.basename(image_path))
        self.file_label.setAlignment(Qt.AlignCenter)
        self.file_label.setStyleSheet("color:#4d565e;font-weight:600;")
        self.file_label.setToolTip(image_path)
        layout.addWidget(self.file_label)
        # 选项区：当前只放“绘制像素画”，其余选项预留在此布局中追加。
        self.actions = QVBoxLayout()
        self.actions.setSpacing(8)
        self.btn_paint = QPushButton("绘制像素画")
        _set_type(self.btn_paint, "primary")
        self.btn_paint.setMinimumHeight(40)
        self.btn_paint.setToolTip("按普通图片转换为 24×24 像素画，可精修后进入绘画")
        self.btn_paint.clicked.connect(self.accept)
        self.actions.addWidget(self.btn_paint)
        layout.addLayout(self.actions)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)
        self._animated_once = False


class ImageEditorDialog(DialogAnimMixin, QDialog):
    """置顶模态图片编辑浮层：裁剪、滤镜与24×24区域确认在此统一完成。"""
    def __init__(self, image_path: str, initial_params: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图片编辑与24×24画布识别")
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setModal(True)
        self.resize(940, 700)
        self.image_path = image_path
        image = QImage(image_path)
        side = min(image.width(), image.height())
        self.selection = ((image.width() - side) // 2, (image.height() - side) // 2,
                          (image.width() + side) // 2, (image.height() + side) // 2)
        self.editor_result = None
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("调整图片后再转换")
        title.setProperty("class", "moduleTitle")
        header.addWidget(title)
        self.detect_state = QLabel("正在检测24×24画布…")
        self.detect_state.setProperty("class", "subtitle")
        header.addWidget(self.detect_state, 1)
        detect = QPushButton("重新检测24×24画布")
        detect.clicked.connect(self._detect)
        header.addWidget(detect)
        layout.addLayout(header)
        self.canvas = ImageCropCanvas(image_path, self.selection)
        self.canvas.selection_changed.connect(self._on_selection_changed)
        layout.addWidget(self.canvas, 1)
        tools = QHBoxLayout()
        self.crop_label = QLabel()
        self.crop_label.setProperty("class", "subtitle")
        tools.addWidget(self.crop_label, 1)
        for label, key in (("对比度", "contrast"), ("亮度", "brightness"), ("饱和度", "saturation")):
            tools.addWidget(QLabel(label))
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 200)
            slider.setValue(initial_params[key])
            slider.setFixedWidth(105)
            setattr(self, f"{key}_slider", slider)
            tools.addWidget(slider)
        layout.addLayout(tools)
        actions = QHBoxLayout()
        reset = QPushButton("恢复全图")
        reset.clicked.connect(self._reset_selection)
        actions.addWidget(reset)
        self.direct_btn = QPushButton("按当前24×24框直接读取")
        self.direct_btn.setEnabled(True)
        self.direct_btn.setToolTip("自动检测只是建议；你也可以移动默认正方形框后直接读取")
        _set_type(self.direct_btn, "success")
        self.direct_btn.clicked.connect(self._accept_direct)
        actions.addWidget(self.direct_btn)
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        convert = QPushButton("按普通图片转换")
        _set_type(convert, "primary")
        convert.clicked.connect(self._accept_convert)
        actions.addWidget(convert)
        layout.addLayout(actions)
        self._on_selection_changed(self.selection)
        self._detect()
        self._animated_once = False

    def _on_selection_changed(self, selection):
        self.selection = selection
        l, t, r, b = selection
        self.crop_label.setText(f"当前选区：{r - l} × {b - t} 像素；在图中拖动可移动或调整选区")

    def _reset_selection(self):
        image = self.canvas.image
        side = min(image.width(), image.height())
        self.selection = ((image.width() - side) // 2, (image.height() - side) // 2,
                          (image.width() + side) // 2, (image.height() + side) // 2)
        self.canvas.selection = self.selection
        self.canvas.update()
        self._on_selection_changed(self.selection)

    def _detect(self):
        try:
            box = detect_24x24_box(self.image_path)
        except Exception:
            box = None
        if box is None:
            self.direct_btn.setEnabled(True)
            self.detect_state.setText("未自动锁定网格；当前为默认正方形框，可移动四边角后直接读取")
            return
        self.selection = box
        self.canvas.selection = box
        self.canvas.update()
        self._on_selection_changed(box)
        self.direct_btn.setEnabled(True)
        self.detect_state.setText("检测到候选24×24区域：请确认或拖动调整边框后直接读取")

    def _params(self):
        return {
            "crop_box": self.selection,
            "contrast": self.contrast_slider.value(),
            "brightness": self.brightness_slider.value(),
            "saturation": self.saturation_slider.value(),
        }

    def _accept_direct(self):
        try:
            self.editor_result = ("direct", parse_blueprint_region(self.image_path, self.selection), self._params())
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "读取失败", str(exc))

    def _accept_convert(self):
        self.editor_result = ("convert", None, self._params())
        self.accept()


# ===========================================================================
# 步骤 ① 图片像素化
# ===========================================================================
class _ThumbnailLoader(QObject):
    """异步缩略图加载器：工作线程解码 PNG，主线程接收 QImage。

    只加载已入队的（= 已创建的）卡片，控制并发数，避免一次性解码几百张图。
    """
    loaded = Signal(str, object)   # (item_id, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._active = 0
        self._max_workers = 4

    def enqueue(self, item_id: str, preview_path: str):
        with self._lock:
            self._queue.append((item_id, preview_path))
        self._pump()

    def _pump(self):
        with self._lock:
            if self._active >= self._max_workers or not self._queue:
                return
            item_id, path = self._queue.pop(0)
            self._active += 1
        threading.Thread(target=self._work, args=(item_id, path), daemon=True).start()

    def _work(self, item_id: str, path: str):
        try:
            img = QImage(path)
            if not img.isNull():
                img = img.scaled(176, 176, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.loaded.emit(item_id, img)
        except Exception:
            pass
        finally:
            with self._lock:
                self._active -= 1
            self._pump()


class LibraryCard(QFrame):
    """历史库/收藏库共用卡片：勾选多选 + 改名 + 预览图点击直接绘画 + 使用/删除。"""
    selection_toggled = Signal(str, bool)
    paint_requested = Signal(str)
    use_requested = Signal(str)
    deleted = Signal(str)
    renamed = Signal(str, str)   # (item_id, new_name)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item
        self._selected = False
        self.setObjectName("libraryCard")
        self.setFixedSize(205, 292)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        self.check = QCheckBox()
        self.check.setToolTip("勾选后可批量分享/导出或批量删除")
        self.check.toggled.connect(self._on_check_toggled)
        head.addWidget(self.check)
        head.addStretch()
        rename_btn = QPushButton("改名")
        rename_btn.setFixedWidth(52)
        rename_btn.setStyleSheet("padding:2px 6px;font-size:11px;")
        rename_btn.setToolTip("自定义这条像素画的名称")
        rename_btn.clicked.connect(lambda: self.renamed.emit(item["id"], item.get("name", "")))
        head.addWidget(rename_btn)
        layout.addLayout(head)
        self.preview = QLabel("加载中…")
        self.preview.setFixedSize(180, 180)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:#eef1f2;border:1px solid #c8cdd1;")
        self.preview.setToolTip("点击图片：直接进入第③步开始绘画")
        self.preview.mousePressEvent = lambda event: self.paint_requested.emit(item["id"])
        layout.addWidget(self.preview, 0, Qt.AlignCenter)
        self.name_label = QLabel(item.get("name", "未命名"))
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setMaximumWidth(184)
        self.name_label.setToolTip(item.get("name", ""))
        self.name_label.setStyleSheet("color:#222;font-weight:700;font-size:13px;")
        layout.addWidget(self.name_label)
        date = QLabel(f"{item.get('source', '图片转换')} · {item.get('created_at', '')[:10]}")
        date.setAlignment(Qt.AlignCenter)
        date.setStyleSheet("color:#72777b;font-size:11px;")
        layout.addWidget(date)
        row = QHBoxLayout()
        use = QPushButton("使用")
        use.setToolTip("加载到第①步像素画（不自动跳转）")
        use.clicked.connect(lambda: self.use_requested.emit(item["id"]))
        row.addWidget(use)
        remove = QPushButton("删除")
        remove.setProperty("btnType", "danger")
        remove.clicked.connect(lambda: self.deleted.emit(item["id"]))
        row.addWidget(remove)
        layout.addLayout(row)
        self._apply_selected_style()

    def set_preview(self, pixmap: QPixmap):
        if pixmap and not pixmap.isNull():
            self.preview.setPixmap(
                pixmap.scaled(176, 176, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.preview.setText("")

    def set_name(self, name: str):
        self.name_label.setText(name)
        self.name_label.setToolTip(name)
        self.item["name"] = name

    def set_selected(self, selected: bool):
        if self._selected == selected:
            return
        self._selected = selected
        self.check.blockSignals(True)
        self.check.setChecked(selected)
        self.check.blockSignals(False)
        self._apply_selected_style()

    def _on_check_toggled(self, checked: bool):
        self._selected = checked
        self._apply_selected_style()
        self.selection_toggled.emit(self.item["id"], checked)

    def _apply_selected_style(self):
        if self._selected:
            self.setStyleSheet(
                "#libraryCard{border:2px solid #1e7c77;border-radius:8px;background:#f3fbfa;}")
        else:
            self.setStyleSheet(
                "#libraryCard{border:1px solid #d8dde2;border-radius:8px;background:#ffffff;}")


class DragScrollArea(QScrollArea):
    """模拟游戏收藏页的按住左键拖动滚动。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_y = None
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_y = event.position().toPoint().y()
                return True
            if event.type() == QEvent.MouseMove and self._drag_y is not None:
                y = event.position().toPoint().y()
                bar = self.verticalScrollBar()
                bar.setValue(bar.value() - y + self._drag_y)
                self._drag_y = y
                return True
            if event.type() == QEvent.MouseButtonRelease:
                self._drag_y = None
                return True
        return super().eventFilter(watched, event)


class LibraryDialog(DialogAnimMixin, QDialog):
    """历史库 / 收藏库共用浮窗。

    - 元数据只读 JSON，卡片按页懒加载、缩略图异步解码，打开立即显示
      标题/数量/状态，不阻塞 Qt 主线程。
    - 多选（勾选框/选中边框）、全选、批量分享/导出、批量删除，
      批量操作只针对当前库（store）。
    """
    matrix_selected = Signal(object)          # “使用”：加载到第①步，不自动跳转
    paint_matrix_selected = Signal(object)    # 点击预览图：直接进入第③步绘画

    def __init__(self, store: PixelHistoryStore, parent=None, title: str = "像素画库",
                 page_size: int = 48):
        super().__init__(parent)
        self.store = store
        self.title = title
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.resize(930, 720)
        self._items: list[dict] = []
        self._filtered_items: list[dict] = []
        self._broken = 0
        self._selected: set[str] = set()
        self._page_size = max(24, page_size)
        self._visible_count = 0
        self._search_query = ""
        self._cards_by_id: dict[str, LibraryCard] = {}
        self._thumb = _ThumbnailLoader(self)
        self._thumb.loaded.connect(self._on_thumb_loaded)
        self._build_ui()
        self.reload()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel(f"▦  {self.title}")
        title.setStyleSheet("font-size:20px;font-weight:800;color:#20262b;")
        header.addWidget(title)
        header.addStretch()
        self.count = QLabel()
        self.count.setStyleSheet("font-size:14px;font-weight:700;color:#4d565e;")
        header.addWidget(self.count)
        layout.addLayout(header)

        # 便捷搜索：按名称/来源/日期实时过滤
        search_row = QHBoxLayout()
        search_label = QLabel("🔍")
        search_row.addWidget(search_label)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("搜索名称 / 来源 / 日期…（回车或输入即过滤）")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.search_box, 1)
        self.search_count = QLabel("")
        self.search_count.setStyleSheet("color:#1e7c77;font-weight:700;")
        search_row.addWidget(self.search_count)
        clear_search = QPushButton("清空")
        clear_search.setFixedWidth(64)
        clear_search.clicked.connect(self.search_box.clear)
        search_row.addWidget(clear_search)
        layout.addLayout(search_row)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_select_all = QPushButton("全选")
        self.btn_select_all.clicked.connect(self._select_all)
        bar.addWidget(self.btn_select_all)
        self.btn_clear = QPushButton("取消全选")
        self.btn_clear.clicked.connect(self._clear_selection)
        bar.addWidget(self.btn_clear)
        self.sel_count = QLabel("已选 0")
        self.sel_count.setStyleSheet("color:#1e7c77;font-weight:700;")
        bar.addWidget(self.sel_count)
        bar.addStretch()
        self.btn_share = QPushButton("批量分享/导出")
        _set_type(self.btn_share, "primary")
        self.btn_share.setToolTip("导出选中项的 PNG 预览、矩阵 .npy、元数据 JSON 并打包 ZIP")
        self.btn_share.clicked.connect(self._batch_share)
        bar.addWidget(self.btn_share)
        self.btn_batch_delete = QPushButton("批量删除")
        _set_type(self.btn_batch_delete, "danger")
        self.btn_batch_delete.clicked.connect(self._batch_delete)
        bar.addWidget(self.btn_batch_delete)
        refresh_btn = QPushButton("刷新")
        refresh_btn.setToolTip("重新扫描当前库，显示最新导入结果")
        refresh_btn.clicked.connect(self.reload)
        bar.addWidget(refresh_btn)
        layout.addLayout(bar)

        self.scroll_area = DragScrollArea()
        self.cards_host = QWidget()
        self.cards = QGridLayout(self.cards_host)
        self.cards.setContentsMargins(20, 12, 20, 30)
        self.cards.setHorizontalSpacing(18)
        self.cards.setVerticalSpacing(20)
        self.scroll_area.setWidget(self.cards_host)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        layout.addWidget(self.scroll_area, 1)
        self.status = QLabel("加载中…")
        self.status.setStyleSheet("color:#70777d;")
        layout.addWidget(self.status)

    # ---------- 懒加载分页 ----------
    def reload(self):
        """重新扫描当前库（只读元数据），清空并重建首屏（应用当前搜索）。"""
        self._items, self._broken = self.store.scan_meta()
        self._selected.clear()
        self._rebuild_grid()

    def _apply_filter(self) -> list[dict]:
        """按当前搜索词过滤（名称/来源/日期）。"""
        query = self._search_query.strip().lower()
        if not query:
            return list(self._items)
        return [item for item in self._items
                if query in str(item.get("name", "")).lower()
                or query in str(item.get("source", "")).lower()
                or query in str(item.get("created_at", ""))]

    def _rebuild_grid(self):
        """重建卡片网格（搜索过滤/删除后共用），滚动条回到顶部。"""
        self._filtered_items = self._apply_filter()
        self._visible_count = 0
        self._cards_by_id.clear()
        while self.cards.count():
            item = self.cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        bar = self.scroll_area.verticalScrollBar()
        if bar is not None:
            bar.setValue(0)
        self._load_more()
        self._update_counts()
        self.status.setText("就绪" if self._filtered_items or not self._broken
                            else f"已跳过 {self._broken} 条损坏记录")

    def _on_search_changed(self, text: str):
        self._search_query = text or ""
        self._rebuild_grid()

    def _load_more(self):
        total = len(self._filtered_items)
        if self._visible_count >= total:
            self._update_counts()
            return
        guard = 0
        while self._visible_count < total and guard < 4:
            end = min(total, self._visible_count + self._page_size)
            for index in range(self._visible_count, end):
                item = self._filtered_items[index]
                card = LibraryCard(item)
                attach_hover_raise(card, lift=3, shadow=True)
                card.selection_toggled.connect(self._on_selection_toggled)
                card.paint_requested.connect(self._on_paint)
                card.use_requested.connect(self._on_use)
                card.deleted.connect(self._on_delete)
                card.renamed.connect(self._on_rename)
                if item["id"] in self._selected:
                    card.set_selected(True)
                self.cards.addWidget(card, index // 4, index % 4)
                self._cards_by_id[item["id"]] = card
                self._thumb.enqueue(item["id"], item.get("preview_path", ""))
            self._visible_count = end
            guard += 1
            if self._visible_count >= total:
                break
            if self.cards_host.sizeHint().height() >= self.scroll_area.viewport().height():
                break
        self._update_counts()

    def _on_scrolled(self, value: int):
        bar = self.scroll_area.verticalScrollBar()
        if bar.maximum() <= 0 or value >= bar.maximum() - 140:
            self._load_more()

    def _on_thumb_loaded(self, item_id: str, image: QImage):
        card = self._cards_by_id.get(item_id)
        if card is not None:
            card.set_preview(QPixmap.fromImage(image))

    def _update_counts(self):
        total = len(self._items)
        shown = len(self._filtered_items)
        broken = self._broken
        text = f"记录 {total} · 显示 {self._visible_count}"
        if broken:
            text += f" · 损坏 {broken}"
        self.count.setText(text)
        if self._search_query.strip():
            self.search_count.setText(f"匹配 {shown} 条")
        else:
            self.search_count.setText("")
        self.sel_count.setText(f"已选 {len(self._selected)}")

    # ---------- 改名 ----------
    def _on_rename(self, item_id: str, old_name: str):
        new_name, ok = QInputDialog.getText(self, "自定义名称",
                                            f"请输入这条像素画的新名称：",
                                            text=old_name)
        if not ok or not new_name or not new_name.strip():
            return
        new_name = new_name.strip()[:48]
        result = self.store.rename(item_id, new_name)
        if result is None:
            QMessageBox.warning(self, "改名失败", "无法更新记录，可能已被删除。")
            return
        card = self._cards_by_id.get(item_id)
        if card is not None:
            card.set_name(result)
        for item in self._items:
            if item["id"] == item_id:
                item["name"] = result
                break
        # 若正在搜索，新名称可能不再匹配 → 重建网格
        if self._search_query.strip():
            self._rebuild_grid()

    # ---------- 多选 ----------
    def _on_selection_toggled(self, item_id: str, checked: bool):
        if checked:
            self._selected.add(item_id)
        else:
            self._selected.discard(item_id)
        self._update_counts()

    def _select_all(self):
        self._selected = {item["id"] for item in self._filtered_items}
        for card in self._cards_by_id.values():
            card.set_selected(True)
        self._update_counts()

    def _clear_selection(self):
        self._selected.clear()
        for card in self._cards_by_id.values():
            card.set_selected(False)
        self._update_counts()

    # ---------- 单卡操作 ----------
    def _on_use(self, item_id: str):
        """“使用”：只加载矩阵到第①步，不自动跳转。"""
        try:
            self.matrix_selected.emit(self.store.load(item_id))
            self.hide()
        except Exception as exc:
            QMessageBox.warning(self, "加载失败", str(exc))

    def _on_paint(self, item_id: str):
        """点击预览图：加载矩阵并直接进入第③步绘画。"""
        try:
            self.paint_matrix_selected.emit(self.store.load(item_id))
            self.reject()
            top = self.window()
            if hasattr(top, "_open_pixel_step"):
                top._open_pixel_step(2)
        except Exception as exc:
            QMessageBox.warning(self, "开始绘画失败", str(exc))

    def _on_delete(self, item_id: str):
        ret = QMessageBox.question(
            self, "确认删除", "确定删除这条像素画吗？\n删除后不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self.store.delete(item_id)
        self.reload()

    # ---------- 批量操作（只针对当前库） ----------
    def _batch_delete(self):
        ids = sorted(self._selected)
        if not ids:
            QMessageBox.information(self, "提示", "请先勾选要删除的像素画")
            return
        ret = QMessageBox.question(
            self, "确认批量删除",
            f"确定从当前库中删除选中的 {len(ids)} 条像素画吗？\n删除后不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        removed = self.store.delete_many(ids)
        self.reload()
        QMessageBox.information(self, "已删除", f"已删除 {removed} 条记录")

    def _batch_share(self):
        ids = sorted(self._selected)
        if not ids:
            QMessageBox.information(self, "提示", "请先勾选要分享/导出的像素画")
            return
        dest = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dest:
            return
        result = self.store.export_share(ids, dest)
        lines = [f"导出成功 {result['count']} 张", f"目录：{result['dir']}"]
        if result.get("zip"):
            lines.append(f"分享包：{result['zip']}")
        if result.get("failed"):
            lines.append(f"失败：{result['failed']} 张")
        QMessageBox.information(self, "导出完成", "\n".join(lines))


class CollectionImportProgressDialog(QDialog):
    """自动导入期间的紧凑进度浮窗，不显示截图预览以避免遮挡收藏列表。"""
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("画像收藏导入进度")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setFixedSize(390, 178)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel("画像收藏自动导入")
        title.setStyleSheet("font-size:16px;font-weight:800;")
        layout.addWidget(title)
        self.phase = QLabel("正在准备…")
        self.phase.setStyleSheet("color:#54d0c0;font-weight:700;")
        self.phase.setWordWrap(True)
        layout.addWidget(self.phase)
        # 确定模式进度条：避免活动模式（range 0,0）在 Windows 上周期性闪烁。
        self.progress = QProgressBar()
        self.progress.setRange(0, CollectionImportDialog.MAX_PAGES)
        self.progress.setFormat("%v / %m 页")
        layout.addWidget(self.progress)
        self.metrics = QLabel("扫描 0 页 · 识别 0 · 新增 0 · 重复 0")
        self.metrics.setStyleSheet("color:#a6b4c2;")
        layout.addWidget(self.metrics)
        layout.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        self.stop_btn = QPushButton("停止")
        _set_type(self.stop_btn, "danger")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        row.addWidget(self.stop_btn)
        layout.addLayout(row)

    def update_state(self, phase: str, stats: dict):
        self.phase.setText(phase)
        self.progress.setValue(min(stats.get("pages", 0), CollectionImportDialog.MAX_PAGES))
        self.metrics.setText(
            f"扫描 {stats['pages']} 页 · 识别 {stats['recognized']} · "
            f"新增 {stats['new']} · 重复 {stats['duplicates']}")

    def finish(self, message: str, stats: dict):
        self.progress.setValue(min(stats.get("pages", 1), CollectionImportDialog.MAX_PAGES))
        self.update_state(message, stats)
        self.stop_btn.setText("关闭")
        try:
            self.stop_btn.clicked.disconnect()
        except Exception:
            pass
        self.stop_btn.clicked.connect(self.close)


class CollectionImportDialog(QDialog):
    """从游戏画像收藏页自动导入：真实左键拖动滚动 + 非阻塞 QTimer 状态机。

    滚动完全复用步骤③已验证的 `WindowInput.drag_sendinput`（激活前台 →
    隐藏浮窗 → 真实按住拖动 → 截图校验页面变化 → 重试），不使用任何
    WM_MOUSEWHEEL / 伪滚动消息。每步间隔处理 Qt 事件，停止立即响应。
    """
    imported = Signal(int)

    # 收藏最多约 30 张，按每页约 6~8 张算，5 页内即可扫完；上限给足余量即可，
    # 避免滚动失败时无意义扫到几十页导致识别数虚高。
    MAX_PAGES = 12

    def __init__(self, store: PixelHistoryStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("导入游戏画像收藏")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.resize(900, 650)
        self._frame = None
        self._importing = False
        self._seen_pages: list[np.ndarray] = []
        self._scan_phase = None
        self._top_drags = 0
        self._top_prev_fp = None
        self._last_parsed_fp = None
        self._consecutive_dup_pages = 0
        # 本会话已新增矩阵：用于跨页近似去重（滚动偏移导致的采样差异也合并）。
        self._session_matrices: list[np.ndarray] = []
        self._progress_dialog: Optional[CollectionImportProgressDialog] = None
        self._stats = {"pages": 0, "recognized": 0, "new": 0,
                       "duplicates": 0, "low_conf": 0}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        title = QLabel("▦  导入游戏画像收藏")
        title.setStyleSheet("font-size:22px;font-weight:800;")
        layout.addWidget(title)
        layout.addWidget(QLabel("请先打开游戏的“画像收藏”页面；自动导入会先回到顶部，再逐页向下扫描并识别卡片中的24×24像素画。"))
        row = QHBoxLayout()
        capture = QPushButton("截取当前收藏页")
        capture.clicked.connect(self._capture)
        row.addWidget(capture)
        parse = QPushButton("解析当前页并保存")
        parse.clicked.connect(lambda: self._parse())
        row.addWidget(parse)
        self.auto_import_btn = QPushButton("从顶部开始自动导入")
        _set_type(self.auto_import_btn, "primary")
        self.auto_import_btn.clicked.connect(self._auto_import)
        row.addWidget(self.auto_import_btn)
        self.stop_import_btn = QPushButton("停止")
        self.stop_import_btn.setEnabled(False)
        self.stop_import_btn.clicked.connect(self._stop_import)
        row.addWidget(self.stop_import_btn)
        row.addStretch()
        layout.addLayout(row)
        self.preview = QLabel("尚未截取收藏页")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(430)
        self.preview.setStyleSheet("background:#eef1f2;color:#626b70;border:1px dashed #97a1a6;")
        layout.addWidget(self.preview, 1)
        self.status = QLabel("支持重复截取不同滚动位置的页面，已导入内容会按矩阵指纹去重。")
        layout.addWidget(self.status)

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    def _set_import_status(self, message: str):
        """同时更新主导入窗和独立进度浮窗。"""
        self.status.setText(message)
        if self._progress_dialog is not None:
            self._progress_dialog.update_state(message, self._stats)

    def _show_progress_dialog(self):
        if self._progress_dialog is None:
            self._progress_dialog = CollectionImportProgressDialog(self.window())
            self._progress_dialog.stop_requested.connect(self._stop_import)
        self._progress_dialog.update_state("正在准备游戏窗口…", self._stats)
        self._progress_dialog.show()
        self._progress_dialog.raise_()

    def _hide_progress_for_capture(self) -> bool:
        """截图前用透明度隐藏进度窗（不重映射窗口，避免白闪）。"""
        if self._progress_dialog is not None and self._progress_dialog.isVisible():
            self._progress_dialog.setWindowOpacity(0.0)
            QApplication.processEvents()
            time.sleep(0.05)
            return True
        return False

    def _restore_progress_after_capture(self, was_visible: bool):
        if was_visible and self._importing and self._progress_dialog is not None:
            self._progress_dialog.setWindowOpacity(1.0)
            self._progress_dialog.raise_()

    def _hwnd(self):
        owner = self.parent()
        return getattr(getattr(owner, "ctx", None), "hwnd", None) or getattr(owner, "hwnd", None)

    def _capture(self, quiet: bool = False) -> bool:
        """截图（隐藏浮窗避免遮挡游戏收藏页），成功返回 True。

        ScreenCapturer 返回 BGR 帧；这里统一转为 RGB 存储到 `self._frame`，
        预览、页面指纹与卡片解析全部使用 RGB，杜绝通道错位导致的偏色。
        """
        try:
            from win32_capture import ScreenCapturer
            hwnd = self._hwnd()
            if not hwnd:
                raise RuntimeError("请先点击“连接游戏窗口”选择目标窗口")
            was_visible = self.isVisible()
            if was_visible:
                self.hide()
                QApplication.processEvents()
                time.sleep(0.15)
            progress_was_visible = self._hide_progress_for_capture()
            try:
                cap = ScreenCapturer(_client_region_from_hwnd(int(hwnd)))
                raw = cap.grab()
                cap.close()
            finally:
                # 自动导入期间主导入窗保持隐藏；进度窗只在截图瞬间隐藏并立即恢复。
                if was_visible and not self._importing:
                    self.show()
                    self.raise_()
                self._restore_progress_after_capture(progress_was_visible)
            if raw is None:
                raise RuntimeError("截图失败")
            self._frame = np.ascontiguousarray(raw[:, :, :3][:, :, ::-1])
            image = QImage(self._frame.data, self._frame.shape[1], self._frame.shape[0],
                           self._frame.shape[1] * 3, QImage.Format_RGB888).copy()
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(
                self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            if not quiet:
                self.status.setText("已截取当前收藏页；可手动解析或从顶部开始自动导入。")
            return True
        except Exception as exc:
            if not quiet:
                QMessageBox.warning(self, "截取失败", str(exc))
            self.status.setText(f"截取失败：{exc}")
            return False

    def _page_fp(self) -> np.ndarray | None:
        """返回量化降采样页面指纹，抑制游戏动画/抗锯齿的微小帧差。"""
        if self._frame is None:
            return None
        h, w = self._frame.shape[:2]
        # 排除顶部导航与两侧固定背景，聚焦收藏列表主体。
        x0, x1 = round(w * 0.10), round(w * 0.90)
        y0, y1 = round(h * 0.20), round(h * 0.85)
        region = self._frame[y0:y1:32, x0:x1:32, :3]
        return np.ascontiguousarray(region // 24)

    @staticmethod
    def _same_page(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> bool:
        """允许少量动态像素差：量化指纹平均差很小即视为同一页面。"""
        if a is None or b is None or a.shape != b.shape:
            return False
        return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))) < 0.50

    def _drag_collection(self, toward_top: bool) -> bool:
        """复用步骤③默认的 WindowPos（MAA PC 推荐）真实按住左键拖动收藏列表。

        与步骤③ `_drag_palette` 完全一致：临时移动窗口使拖动起点对准真实
        光标，再发送 MOVE→LBUTTONDOWN→MOVE(MK_LBUTTON)→LBUTTONUP 完整拖动
        序列，每个拖动点重新对齐窗口，后台执行，不依赖前台焦点。
        （SendInput 在部分 PC 无法移动真实光标，不能用于收藏滚动。）

        拖动起点取列表内容区中央（x 居中，y 位于列表上/下半部），方向：
        - toward_top=True ：从下方往上拖 → 页面内容向下移动 → 回到顶部
        - toward_top=False：从上方往下拖 → 页面内容向上移动 → 滚动到下一页
        拖动前先隐藏浮窗，避免置顶浮窗遮住游戏收藏列表。
        """
        from win32_input import WindowInput
        hwnd = self._hwnd()
        if not hwnd:
            self.status.setText("拖动失败：未连接游戏窗口")
            return False
        left, top, right, bottom = _client_region_from_hwnd(int(hwnd))
        width, height = right - left, bottom - top
        if width < 120 or height < 120:
            self.status.setText("拖动失败：窗口客户区过小")
            return False
        x = width // 2
        # 单次拖动距离：约 62% 视口高度。终点 clamp 范围放到 0.05~0.95，
        # 起点靠边缘，终点在另一端，确保实际拖动距离≈distance 不被吞掉。
        # 之前 clamp 0.12~0.88 且起点在中间，导致 62% 被截成 172~259px，
        # 而卡片行高约 360px —— 一拖不到下一行，是“滑不动”的根因。
        distance = max(200, round(height * 0.62))
        if toward_top:
            # 向下拖动（内容向上移 → 回到顶部）：起点偏上，终点偏下
            y1 = round(height * 0.18)
            y2 = max(round(height * 0.05), min(round(height * 0.95), y1 + distance))
        else:
            # 向上拖动（内容向下移 → 滚到下一页）：起点偏下，终点偏上
            y1 = round(height * 0.82)
            y2 = max(round(height * 0.05), min(round(height * 0.95), y1 - distance))
        driver = WindowInput(int(hwnd))
        # 自动导入期间浮窗已全程隐藏；若仍可见（异常/手动场景）先隐藏，
        # 避免置顶浮窗遮住收藏列表。这里不恢复显示，避免拖动闪烁。
        if self.isVisible():
            self.hide()
            QApplication.processEvents()
            time.sleep(0.12)
        # 与步骤③默认输入模式保持一致：WindowPos（MAA PC 推荐）。
        # 通过临时移动窗口使拖动起点对准真实光标 + SendMessage 完整拖动
        # 序列后台执行，PC 端已验证可行；SendInput 在部分 PC 无法移动
        # 真实光标，不能作为收藏滚动的实现。
        if not driver.activate_foreground(force=True):
            self.status.setText("提示：未能抢到游戏窗口前台，尝试后台拖动…")
        # 到终点后继续按住 0.5s 再释放，先让 Unity 完全消化拖动手势并停住，
        # 抑制列表惯性；随后由 _wait_page_stable 做页面静止确认。
        return driver.drag_with_windowpos(
            x, y1, x, y2, hold_ms=60, steps=20, release_delay_ms=500)

    def _wait_page_stable(self, tries: int = 5, settle_ms: float = 0.5) -> bool:
        """等待页面彻底静止：连续两次截图指纹一致才通过。

        拖动终点按住 0.5s 释放后，Unity 列表仍可能有惯性滚动；这里以 0.5s
        间隔连续截图，直到相邻两帧一致（最多 tries 次），确保截图时画面
        完全静止，卡片不再错位。
        """
        prev = None
        for _ in range(tries):
            if not self._importing:
                return False
            if not self._capture(quiet=True):
                return False
            fp = self._page_fp()
            if self._same_page(prev, fp):
                return True
            prev = fp
            time.sleep(settle_ms)
        return True

    def _drag_with_retry(self, toward_top: bool) -> tuple[bool, str]:
        """拖动 + 重试：失败时重试并检查窗口有效性，返回 (是否成功, 原因)。"""
        reason = ""
        for attempt in range(1, 4):
            if not self._importing:
                return False, "已停止"
            if self._drag_collection(toward_top):
                return True, ""
            reason = f"第 {attempt} 次拖动返回失败"
            try:
                from win32_input import WindowInput, IsWindow
                hwnd = self._hwnd()
                if hwnd and not IsWindow(int(hwnd)):
                    reason += "（游戏窗口已失效）"
                elif hwnd and not WindowInput(int(hwnd)).is_foreground():
                    reason += "（游戏窗口不在前台）"
            except Exception:
                pass
            time.sleep(0.12)
        return False, reason

    # ------------------------------------------------------------------
    # 解析与保存
    # ------------------------------------------------------------------
    def _parse(self, update_status: bool = True) -> dict:
        """解析当前帧并保存到当前库，返回本页统计。"""
        if self._frame is None and not self._capture(quiet=not update_status):
            return {"recognized": 0, "new": 0, "duplicates": 0, "low_conf": 0}
        try:
            # self._frame 已在 _capture 统一为 RGB，显式 bgr=False 防止通道误判。
            results = parse_collection_page_detailed(self._frame, bgr=False)
            new_count = 0
            dup_count = 0
            low_count = 0
            for r in results:
                mat = np.asarray(r.matrix, dtype=np.int16)
                # 跨页近似去重：同一像素画在滚动重叠时采样可能有少量偏移，
                # 精确指纹不相等。与本会话已新增矩阵比较，≥85% 格相同即合并
                # （滚动边缘同一张卡可能有背景/边框差异），判定重复时不再入库。
                fuzzy_dup = any(
                    seen.shape == mat.shape and float(np.mean(seen == mat)) >= 0.85
                    for seen in self._session_matrices)
                if fuzzy_dup:
                    dup_count += 1
                else:
                    result = self.store.save_unique(mat, name=r.name, source="游戏画像收藏")
                    if result["saved"]:
                        self._session_matrices.append(mat)
                        new_count += 1
                    else:
                        dup_count += 1
                if r.low_confidence:
                    low_count += 1
            stats = {"recognized": len(results), "new": new_count,
                     "duplicates": dup_count, "low_conf": low_count}
            if update_status:
                self.status.setText(
                    f"本页识别 {stats['recognized']} 张，新增 {new_count}，"
                    f"重复 {dup_count}，低置信 {low_count}。")
            self.imported.emit(new_count)
            return stats
        except Exception as exc:
            if update_status:
                QMessageBox.warning(self, "解析失败", str(exc))
            self.status.setText(f"解析失败：{exc}")
            return {"recognized": 0, "new": 0, "duplicates": 0, "low_conf": 0}

    # ------------------------------------------------------------------
    # 自动导入状态机（QTimer 非阻塞）
    # ------------------------------------------------------------------
    def _restore_dialog(self):
        """自动导入结束：恢复浮窗显示并置前。"""
        if not self.isVisible():
            self.show()
            self.raise_()
            self.activateWindow()
            QApplication.processEvents()

    def _stop_import(self):
        if not self._importing:
            return
        top = self.window()
        if isinstance(top, MainWindow):
            top._loading_overlay.stop()
        self._importing = False
        self._scan_phase = None
        self.auto_import_btn.setEnabled(True)
        self.stop_import_btn.setEnabled(False)
        self._restore_dialog()
        self._show_stats("自动导入已停止")

    def _show_stats(self, reason: str):
        s = self._stats
        message = (f"{reason}。扫描 {s['pages']} 页，识别 {s['recognized']} 张，"
                   f"新增 {s['new']}，重复 {s['duplicates']}，低置信 {s['low_conf']}。")
        self.status.setText(message)
        if self._progress_dialog is not None:
            self._progress_dialog.finish(message, s)

    def _finish_auto_import(self, reason: str):
        if not self._importing:
            return
        top = self.window()
        if isinstance(top, MainWindow):
            top._loading_overlay.stop()
        self._importing = False
        self._scan_phase = None
        self.auto_import_btn.setEnabled(True)
        self.stop_import_btn.setEnabled(False)
        self._restore_dialog()
        self._show_stats(reason)
        self.imported.emit(self._stats["new"])

    def _schedule_scan_step(self, delay_ms: int = 0):
        if self._importing:
            QTimer.singleShot(delay_ms, self._scan_step)

    def _scan_step(self):
        if not self._importing:
            return
        try:
            # ---- 开始：主导入窗隐藏，独立进度窗持续展示状态 ----
            if self._scan_phase == "start":
                self._set_import_status("正在准备游戏窗口…")
                try:
                    from win32_input import WindowInput
                    hwnd = self._hwnd()
                    if not hwnd:
                        self._finish_auto_import("未连接游戏窗口")
                        return
                    # 尝试激活提升成功率；失败不阻断（WindowPos 可后台执行）。
                    WindowInput(int(hwnd)).activate_foreground(force=True)
                except Exception as exc:
                    self._finish_auto_import(f"窗口准备失败：{exc}")
                    return
                if self.isVisible():
                    self.hide()
                    QApplication.processEvents()
                    time.sleep(0.15)
                self._show_progress_dialog()
                self._scan_phase = "to_top"
                self._top_drags = 0
                self._top_prev_fp = None
                self._consecutive_dup_pages = 0
                self._schedule_scan_step(200)
                return

            # ---- 回到顶部：页面首次不再变化即已到顶，立即进入扫描 ----
            if self._scan_phase == "to_top":
                if self._top_prev_fp is None:
                    self._set_import_status("正在读取当前收藏页位置…")
                    if not self._wait_page_stable():
                        self._finish_auto_import("定位顶部时截图失败")
                        return
                    self._top_prev_fp = self._page_fp()
                self._set_import_status(f"正在向上拖动定位顶部（第 {self._top_drags + 1} 次）…")
                ok, reason = self._drag_with_retry(toward_top=True)
                if not self._importing:
                    return
                if not ok:
                    self._finish_auto_import(f"回顶部拖动失败：{reason}")
                    return
                if not self._wait_page_stable():
                    self._finish_auto_import("回顶部后截图失败")
                    return
                fp = self._page_fp()
                self._top_drags += 1
                if self._same_page(fp, self._top_prev_fp):
                    # 页面第一次稳定不变就是顶部；不再机械拖固定次数。
                    self._set_import_status("已定位到收藏页顶部，开始扫描")
                    self._scan_phase = "capture"
                    self._schedule_scan_step(200)
                    return
                self._top_prev_fp = fp
                self._schedule_scan_step(220)
                return

            # ---- 截取并解析当前页（先等页面停稳） ----
            if self._scan_phase == "capture":
                if self._stats["pages"] >= self.MAX_PAGES:
                    self._finish_auto_import("已达到页数上限")
                    return
                self._set_import_status(f"正在等待页面停稳并解析第 {self._stats['pages'] + 1} 页…")
                if not self._wait_page_stable():
                    self._finish_auto_import("截图失败")
                    return
                fp = self._page_fp()
                if any(self._same_page(fp, seen) for seen in self._seen_pages):
                    # 页面指纹重复：停止滚动；但本页卡片先解析入库。
                    self._last_parsed_fp = fp
                    page_stats = self._parse(update_status=False)
                    self._merge_stats(page_stats)
                    self._finish_auto_import("已到达底部（检测到重复页面）")
                    return
                self._seen_pages.append(fp)
                page_stats = self._parse(update_status=False)
                self._merge_stats(page_stats)
                self._last_parsed_fp = fp
                # 连续重复页停止：本页识别到卡片但全部重复（新增 0），
                # 说明已滚到列表末尾附近，连续 2 页即停止，不再无意义翻页。
                if page_stats["recognized"] > 0 and page_stats["new"] == 0:
                    self._consecutive_dup_pages += 1
                else:
                    self._consecutive_dup_pages = 0
                self._set_import_status(
                    f"第 {self._stats['pages']} 页完成：识别 {page_stats['recognized']}，"
                    f"新增 {page_stats['new']}，重复 {page_stats['duplicates']}，"
                    f"低置信 {page_stats['low_conf']}；累计新增 {self._stats['new']}")
                if self._consecutive_dup_pages >= 2:
                    self._finish_auto_import("已到达底部（连续重复页）")
                    return
                self._scan_phase = "scroll"
                self._schedule_scan_step(300)
                return

            # ---- 真实拖动到下一页（等惯性停稳） ----
            if self._scan_phase == "scroll":
                self._set_import_status("正在按住拖动到下一页（终点停顿后释放）…")
                ok, reason = self._drag_with_retry(toward_top=False)
                if not self._importing:
                    return
                if not ok:
                    self._finish_auto_import(f"下滑失败：{reason}")
                    return
                if not self._wait_page_stable():
                    self._finish_auto_import("下滑后截图失败")
                    return
                new_fp = self._page_fp()
                if self._same_page(new_fp, self._last_parsed_fp):
                    self._set_import_status("拖动后页面未变化，再试一次…")
                    ok2, reason2 = self._drag_with_retry(toward_top=False)
                    if not self._importing:
                        return
                    if not ok2:
                        self._finish_auto_import(f"下滑失败：{reason2}")
                        return
                    if not self._wait_page_stable():
                        self._finish_auto_import("下滑后截图失败")
                        return
                    if self._same_page(self._page_fp(), self._last_parsed_fp):
                        self._finish_auto_import("已到达底部（页面未变化）")
                        return
                self._scan_phase = "capture"
                self._schedule_scan_step(350)
                return
        except Exception as exc:
            self._set_import_status(f"自动导入失败：{exc}")
            self._finish_auto_import("自动导入失败")

    def _merge_stats(self, page_stats: dict):
        for key in ("recognized", "new", "duplicates", "low_conf"):
            self._stats[key] += page_stats.get(key, 0)
        self._stats["pages"] += 1

    def _auto_import(self):
        if self._importing:
            return
        if not self._hwnd():
            QMessageBox.warning(self, "提示", "请先点击“连接游戏窗口”选择目标窗口")
            return
        self._importing = True
        self._seen_pages.clear()
        self._session_matrices.clear()
        self._scan_phase = "start"
        self._top_drags = 0
        self._top_prev_fp = None
        self._last_parsed_fp = None
        self._consecutive_dup_pages = 0
        self._stats = {"pages": 0, "recognized": 0, "new": 0,
                       "duplicates": 0, "low_conf": 0}
        self.auto_import_btn.setEnabled(False)
        self.stop_import_btn.setEnabled(True)
        self.status.setText("自动导入已开始，正在准备游戏窗口…")
        self._schedule_scan_step(50)


class GameWindowPickerDialog(DialogAnimMixin, QDialog):
    """工作区级游戏窗口选择器，步骤①收藏导入和步骤②校准共用。"""
    window_selected = Signal(int, str)

    def __init__(self, current_hwnd=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("连接游戏窗口")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.resize(620, 190)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择要捕获的游戏窗口。连接后，收藏导入、校准和绘画都会共用它。"))
        row = QHBoxLayout()
        self.combo = QComboBox()
        row.addWidget(self.combo, 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self._refresh)
        row.addWidget(refresh)
        auto = QPushButton("自动查找明日方舟")
        _set_type(auto, "primary")
        auto.clicked.connect(self._auto_find)
        row.addWidget(auto)
        layout.addLayout(row)
        self.status = QLabel("尚未连接")
        self.status.setProperty("class", "subtitle")
        layout.addWidget(self.status)
        connect = QPushButton("连接此窗口")
        _set_type(connect, "success")
        connect.clicked.connect(self._connect)
        layout.addWidget(connect)
        self.current_hwnd = current_hwnd
        self.windows = []
        self._animated_once = False
        self._refresh()

    def _refresh(self):
        try:
            from win32_input import enum_windows
            import ctypes
            pid = ctypes.windll.kernel32.GetCurrentProcessId()
            self.windows = [w for w in enum_windows(min_title_len=1) if w["pid"] != pid]
        except Exception as exc:
            self.windows = []
            self.status.setText(f"窗口枚举失败：{exc}")
        self.combo.clear()
        for win in self.windows:
            title = win["title"].replace("\\n", " ").strip()
            self.combo.addItem(f"{title}  [{win.get('process_name', '')}]", win["hwnd"])
        if self.current_hwnd:
            index = self.combo.findData(int(self.current_hwnd))
            if index >= 0:
                self.combo.setCurrentIndex(index)

    def _auto_find(self):
        for index, win in enumerate(self.windows):
            title = win["title"].lower()
            if win.get("class_name") == "UnityWndClass" or "arknights" in title or "明日方舟" in title:
                self.combo.setCurrentIndex(index)
                return
        self.status.setText("没有找到 Unity/明日方舟窗口，请手动选择")

    def _connect(self):
        hwnd = self.combo.currentData()
        if hwnd is None:
            self.status.setText("请先选择一个窗口")
            return
        title = self.combo.currentText().split("  [")[0]
        self.window_selected.emit(int(hwnd), title)
        self.accept()


class StepPixelate(QWidget):
    """选择图片 → 像素化 → 生成 24×24 矩阵（存内存供后续步骤使用）"""
    convert_done_signal = Signal(object)
    convert_error_signal = Signal(str)

    def __init__(self, app_ctx):
        super().__init__()
        self.setAcceptDrops(True)
        self.ctx = app_ctx
        self.idx_mat: Optional[np.ndarray] = None
        self.image_path: Optional[str] = None
        self.crop_box: Optional[tuple[int, int, int, int]] = None
        self.history_store = PixelHistoryStore()
        self.collection_store = PixelHistoryStore(
            os.path.join(os.path.dirname(__file__), "pixel_collection"))
        # 首次启动兼容旧数据：把历史库里的“游戏画像收藏/收藏库”记录迁移到收藏库，
        # 普通历史记录保持不动；迁移是目录级移动，不重新加载矩阵。
        try:
            self.history_store.migrate_source_to("游戏画像收藏", self.collection_store)
            self.history_store.migrate_source_to("收藏库", self.collection_store)
        except Exception:
            pass
        # 回填旧记录缺失的指纹（一次性，之后去重只读 JSON 元数据）
        try:
            self.history_store.ensure_fingerprints()
            self.collection_store.ensure_fingerprints()
        except Exception:
            pass
        # 清理收藏库跨页重复记录（保留最早一条）
        try:
            self.collection_store.deduplicate()
        except Exception:
            pass
        self.history_dialog = None
        self.collection_library_dialog = None
        self.collection_dialog = None
        self._drop_overlay = QLabel(self)
        self._drop_overlay.setAlignment(Qt.AlignCenter)
        self._drop_overlay.setWordWrap(True)
        self._drop_overlay.setText("将图片拖入此处\n转换为像素画")
        self._drop_overlay.setStyleSheet(
            "background:rgba(7, 18, 24, 248);color:#dffff9;border:2px dashed #54d0c0;"
            "border-radius:12px;font-size:24px;font-weight:700;")
        self._drop_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._drop_overlay.hide()
        self._init_ui()
        self._install_drag_filters()
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
        self.path_edit.setPlaceholderText("点击「浏览…」或用系统对话框选择 PNG/JPG（推荐 1:1 比例）")
        fb.addWidget(self.path_edit, 1)
        btn = QPushButton("浏览…")
        btn.setToolTip("打开系统文件选择器，可直接预览图片缩略图")
        btn.clicked.connect(self.on_browse)
        fb.addWidget(btn)
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

        # 拟合与输出选项
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("拟合："))
        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["丰富色彩", "色块拟合"])
        self.fit_combo.setCurrentIndex(1)
        self.fit_combo.setToolTip("丰富色彩使用超采样保留平滑过渡；色块拟合使用格心采样保留高光和轮廓")
        self.fit_combo.currentIndexChanged.connect(self._update_fit_controls)
        opt_row.addWidget(self.fit_combo)
        opt_row.addWidget(QLabel("取景："))
        self.crop_combo = QComboBox()
        self.crop_combo.addItems(["裁切铺满", "完整压缩"])
        self.crop_combo.setToolTip("裁切铺满保持图片比例并居中裁切；完整压缩会拉伸整张图片")
        opt_row.addWidget(self.crop_combo)
        self.flatten_white = QCheckBox("跳过白色背景")
        self.flatten_white.setChecked(True)
        self.flatten_white.setToolTip("白色格保留在预览中，但绘制时不点击")
        opt_row.addWidget(self.flatten_white)
        self.k_spin = QSpinBox()
        self.k_spin.setValue(40)
        opt_row.addWidget(QLabel("抖动："))
        self.dither_combo = QComboBox()
        self.dither_combo.addItems(["Floyd-Steinberg", "Atkinson", "无抖动"])
        self.dither_combo.setCurrentIndex(2)
        opt_row.addWidget(self.dither_combo)
        opt_row.addStretch()
        layout.addLayout(opt_row)
        self._update_fit_controls()

        # 转换按钮
        self.btn_convert = QPushButton("转换像素图")
        _set_type(self.btn_convert, "primary")
        self.btn_convert.setMinimumHeight(44)
        self.btn_convert.clicked.connect(self.on_convert)
        layout.addWidget(self.btn_convert)

        # 中部：预览 + 使用统计
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        preview_frame = QFrame()
        preview_frame.setMinimumSize(330, 360)
        preview_frame.setStyleSheet(
            "background:#1a1d24;border:1px solid #2e323d;border-radius:12px;")
        pv = QVBoxLayout(preview_frame)
        pv.addWidget(QLabel("像素预览"))
        self.preview_label = QLabel()
        self.preview_label.setMinimumSize(300, 300)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setText("（尚未转换）")
        self.preview_label.setStyleSheet("color:#5a6270;font-size:14px;")
        pv.addWidget(self.preview_label)
        self.drop_hint = QLabel("把 PNG/JPG 拖进本页，或点击「浏览…」用系统对话框选择（带缩略图预览）")
        self.drop_hint.setProperty("class", "subtitle")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        pv.addWidget(self.drop_hint)
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
        self.btn_history = QPushButton("历史库")
        self.btn_history.clicked.connect(self.on_open_history)
        save_row.addWidget(self.btn_history)
        self.btn_collection = QPushButton("导入画像收藏")
        self.btn_collection.clicked.connect(self.on_open_collection)
        save_row.addWidget(self.btn_collection)
        self.btn_collection_library = QPushButton("收藏库")
        self.btn_collection_library.clicked.connect(self.on_open_collection_library)
        save_row.addWidget(self.btn_collection_library)
        save_row.addStretch()
        layout.addLayout(save_row)


    def _update_fit_controls(self):
        self.k_spin.setEnabled(False)
        self.dither_combo.setEnabled(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._drop_overlay.setGeometry(self.rect())
        if self.idx_mat is not None:
            self._refresh_preview()

    def _install_drag_filters(self):
        for widget in self.findChildren(QWidget):
            widget.setAcceptDrops(True)
            widget.installEventFilter(self)
        self.installEventFilter(self)

    def _drag_path(self, event):
        if not event.mimeData().hasUrls():
            return None
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        if len(paths) != 1:
            return None
        if os.path.splitext(paths[0])[1].lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
            return None
        return paths[0]

    def _forward_drop_to_window(self, path: str) -> bool:
        """把拖放转发到主窗口统一入口（带锁），避免双路径重复打开编辑器。"""
        top = self.window()
        if hasattr(top, "_accept_external_drop"):
            return bool(top._accept_external_drop(path))
        return False

    def eventFilter(self, watched, event):
        if event.type() == QEvent.DragEnter:
            path = self._drag_path(event)
            if path:
                self._drop_overlay.setGeometry(self.rect())
                self._drop_overlay.show()
                self._drop_overlay.raise_()
                event.acceptProposedAction()
                return True
        elif event.type() == QEvent.DragMove:
            path = self._drag_path(event)
            if path:
                event.acceptProposedAction()
                return True
        elif event.type() == QEvent.Drop:
            path = self._drag_path(event)
            self._drop_overlay.hide()
            if path and self._forward_drop_to_window(path):
                event.acceptProposedAction()
                return True
        elif event.type() == QEvent.DragLeave:
            self._drop_overlay.hide()
        return super().eventFilter(watched, event)

    def on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self._open_image_editor(path)

    def dragEnterEvent(self, event: QDragEnterEvent):
        path = self._drag_path(event)
        if path:
            self._drop_overlay.setGeometry(self.rect())
            self._drop_overlay.show()
            self._drop_overlay.raise_()
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._drop_overlay.hide()
        event.accept()

    def dropEvent(self, event: QDropEvent):
        self._drop_overlay.hide()
        path = self._drag_path(event)
        if path and self._forward_drop_to_window(path):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _open_image_editor(self, path: str):
        if not os.path.isfile(path):
            return
        dialog = ImageEditorDialog(path, {
            "contrast": self.con_slider.value(),
            "brightness": self.bri_slider.value(),
            "saturation": self.sat_slider.value(),
        }, self.window())
        if dialog.exec() != QDialog.Accepted or dialog.editor_result is None:
            return
        mode, matrix, params = dialog.editor_result
        self.path_edit.setText(path)
        self.image_path = path
        self.crop_box = params["crop_box"]
        self.con_slider.setValue(params["contrast"])
        self.bri_slider.setValue(params["brightness"])
        self.sat_slider.setValue(params["saturation"])
        if mode == "direct":
            self._on_convert_done(matrix, source="蓝图/游戏截图", source_path=path)
            self.drop_hint.setText("已按确认选区读取24×24网格，可精修、导出或直接绘画")
        else:
            self.drop_hint.setText("已应用裁剪与参数，正在按普通图片转换")
            self.on_convert()

    def on_convert(self):
        if not self.image_path or not os.path.exists(self.image_path):
            QMessageBox.warning(self, "提示", "请先选择有效图片")
            return
        try:
            params = {
                "image_path": self.image_path,
                "contrast": self.con_slider.value() / 100.0,
                "brightness": self.bri_slider.value() / 100.0,
                "saturation": self.sat_slider.value() / 100.0,
                "color_count": self.k_spin.value(),
                "dither": ["fs", "atkinson", "none"][self.dither_combo.currentIndex()],
                "flatten_white": self.flatten_white.isChecked(),
                "crop_mode": ["cover", "stretch"][self.crop_combo.currentIndex()],
                "fit_mode": ["rich", "blocks"][self.fit_combo.currentIndex()],
                "crop_box": self.crop_box,
            }
            # 固定本次参数后再进入后台线程，避免线程读取已变化的控件。
            self.btn_convert.setEnabled(False)
            self.btn_convert.setText("转换中…")
            window = self.window()
            if isinstance(window, MainWindow):
                window._loading_overlay.start("正在转换像素图…")
            QApplication.processEvents()

            def do_convert():
                try:
                    mat = pixelate(**params)
                    self.convert_done_signal.emit(mat)
                except Exception as e:
                    self.convert_error_signal.emit(str(e))

            t = threading.Thread(target=do_convert, daemon=True)
            t.start()
        except Exception as e:
            self.btn_convert.setEnabled(True)
            self.btn_convert.setText("转换像素图")
            QMessageBox.critical(self, "错误", f"转换失败：{e}")

    def _on_convert_done(self, mat: np.ndarray, source: str = "图片转换", source_path: str = ""):
        """转换完成（主线程执行 UI 更新）并自动写入历史库。"""
        window = self.window()
        if isinstance(window, MainWindow):
            window._loading_overlay.stop()
        self.idx_mat = mat
        try:
            self.history_store.save_unique(mat, name=os.path.splitext(os.path.basename(source_path))[0] or "未命名",
                                           source=source, source_path=source_path)
        except Exception:
            pass
        self.ctx.idx_mat = mat   # 内存共享给后续步骤
        self._refresh_preview()
        self._refresh_usage()
        self.btn_preview.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("转换像素图")
        QMessageBox.information(self, "转换成功",
                                f"已生成 24×24 像素矩阵，"
                                f"非白格 {np.count_nonzero(mat != GAME_WHITE_INDEX)} 个")

    def _on_convert_error(self, err: str):
        window = self.window()
        if isinstance(window, MainWindow):
            window._loading_overlay.stop()
        self.btn_convert.setEnabled(True)
        self.btn_convert.setText("转换像素图")
        QMessageBox.critical(self, "错误", f"转换失败：{err}")

    def _refresh_preview(self):
        if self.idx_mat is None:
            return
        available = self.preview_label.contentsRect().size()
        cell = max(8, min(available.width(), available.height()) // 24)
        pix = _make_preview_pixmap(self.idx_mat, cell)
        self.preview_label.setPixmap(
            pix.scaled(available, Qt.KeepAspectRatio, Qt.FastTransformation))

    def _refresh_usage(self):
        if self.idx_mat is None:
            return
        usage = count_color_usage(self.idx_mat)
        lines = [f"非白格数：{sum(usage.values())} / 576\n"]
        lines.append("色号  RGB        名称    用量")
        for idx, cnt in usage.items():
            code, rgb, name = GAME_PALETTE_DATA[idx]
            lines.append(f"{code}  {rgb_to_hex(rgb)}  {name}  {cnt}")
        self.usage_text.setPlainText("\n".join(lines))

    def _apply_matrix(self, matrix: np.ndarray, hint: str):
        """把矩阵加载到当前工作区（内存共享给第②③步），不写历史库。"""
        mat = np.asarray(matrix, dtype=np.int16)
        self.idx_mat = mat
        self.ctx.idx_mat = mat
        self._refresh_preview()
        self._refresh_usage()
        self.btn_preview.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.drop_hint.setText(hint)

    def on_open_history(self):
        if self.history_dialog is None:
            self.history_dialog = LibraryDialog(self.history_store, self.window(),
                                                title="像素画历史库")
            self.history_dialog.matrix_selected.connect(self._load_matrix_step1)
            self.history_dialog.paint_matrix_selected.connect(self._load_matrix_and_paint)
        else:
            self.history_dialog.reload()
        self.history_dialog._animation_source = self.btn_history
        self.history_dialog.show()
        self.history_dialog.activateWindow()

    def on_open_collection(self):
        if self.collection_dialog is None:
            self.collection_dialog = CollectionImportDialog(self.collection_store, self.window())
            self.collection_dialog.imported.connect(self._on_collection_imported)
        self.collection_dialog._animation_source = self.btn_collection
        pop_in_dialog(self.collection_dialog, duration=200)
        self.collection_dialog.activateWindow()

    def on_open_collection_library(self):
        if self.collection_library_dialog is None:
            self.collection_library_dialog = LibraryDialog(
                self.collection_store, self.window(), title="游戏画像收藏库")
            self.collection_library_dialog.matrix_selected.connect(self._load_matrix_step1)
            self.collection_library_dialog.paint_matrix_selected.connect(self._load_matrix_and_paint)
        else:
            # 每次打开都重新扫描，确保显示最新导入结果。
            self.collection_library_dialog.reload()
        self.collection_library_dialog._animation_source = self.btn_collection_library
        self.collection_library_dialog.show()
        self.collection_library_dialog.activateWindow()

    def _load_matrix_step1(self, matrix):
        """“使用”：只加载到第①步，不自动跳转，也不重新写入历史库。"""
        self._apply_matrix(matrix, "已从库中加载到像素画步骤，可精修、导出或直接进入第③步")

    def _load_matrix_and_paint(self, matrix):
        """点击卡片预览图：加载矩阵并直接进入第③步开始绘画。"""
        self._apply_matrix(matrix, "已从库中加载，正在进入第③步…")
        top = self.window()
        if hasattr(top, "_open_pixel_step"):
            QTimer.singleShot(30, lambda: top._open_pixel_step(2))

    def _on_collection_imported(self, count):
        # 导入完成后强制刷新收藏库浮窗（无论是否可见）。
        if self.collection_library_dialog is not None:
            self.collection_library_dialog.reload()
        self.drop_hint.setText(f"画像收藏已导入 {count} 张新矩阵，可打开收藏库查看")

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


def _client_region_from_hwnd(hwnd: int) -> tuple[int, int, int, int]:
    """返回目标窗口客户区的屏幕坐标，供收藏捕获和校准共用。"""
    import ctypes
    import ctypes.wintypes as wt
    rect = wt.RECT()
    if not ctypes.windll.user32.GetClientRect(int(hwnd), ctypes.byref(rect)):
        raise RuntimeError("无法读取目标窗口客户区")
    point = wt.POINT(0, 0)
    if not ctypes.windll.user32.ClientToScreen(int(hwnd), ctypes.byref(point)):
        raise RuntimeError("无法转换目标窗口坐标")
    return point.x, point.y, point.x + rect.right, point.y + rect.bottom


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

        # 自动校准优先；复杂参数与标点校准仅在失败时作为高级选项展开。
        auto_box = QGroupBox("2. 自动校准（推荐）")
        auto_layout = QVBoxLayout(auto_box)
        auto_layout.addWidget(QLabel("选择游戏窗口后点击一次即可检测画布与色板。检测失败时会引导你使用可视化校准。"))
        btn_row = QHBoxLayout()
        self.btn_auto_calibrate = QPushButton("自动检测画布/色板")
        _set_type(self.btn_auto_calibrate, "primary")
        self.btn_auto_calibrate.setMinimumHeight(42)
        self.btn_auto_calibrate.clicked.connect(self.on_auto_detect)
        btn_row.addWidget(self.btn_auto_calibrate, 3)
        btn_preset = QPushButton("使用默认校准")
        btn_preset.clicked.connect(self.on_apply_preset)
        btn_row.addWidget(btn_preset, 1)
        auto_layout.addLayout(btn_row)
        layout.addWidget(auto_box)

        self.btn_cal_dialog = QPushButton("打开可视化校准（高级）")
        self.btn_cal_dialog.clicked.connect(self.on_open_cal_dialog)

        # 手动参数
        manual_box = QGroupBox("高级：手动坐标参数")
        manual_box.setCheckable(True)
        manual_box.setChecked(False)
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
        advanced_row = QHBoxLayout()
        advanced_row.addWidget(self.btn_cal_dialog)
        advanced_row.addStretch()
        layout.addLayout(advanced_row)

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
            reply = QMessageBox.question(
                self, "自动检测失败",
                f"自动检测未完成：{e}\n\n是否现在打开可视化校准？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                self.on_open_cal_dialog()

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
class PaintProgressGrid(QWidget):
    """监控浮窗内的24×24绘制状态预览。"""
    def __init__(self, idx_mat: np.ndarray, parent=None):
        super().__init__(parent)
        self.idx_mat = idx_mat
        self.completed = set()
        self.failed = set()
        self.setFixedSize(216, 216)

    def update_state(self, progress: PaintingProgress):
        self.completed = {(x, y) for x, y, _ in progress.completed}
        self.failed = {(x, y) for x, y, _ in progress.failed}
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#0a1118"))
            cell = self.width() / 24
            for y in range(24):
                for x in range(24):
                    color = QColor(*GAME_PALETTE_DATA[int(self.idx_mat[y, x])][1])
                    if (x, y) not in self.completed:
                        color = color.darker(260)
                    painter.fillRect(round(x * cell), round(y * cell), round(cell) + 1, round(cell) + 1, color)
                    if (x, y) in self.failed:
                        painter.setPen(QPen(QColor("#ff5f63"), 2))
                        painter.drawRect(round(x * cell), round(y * cell), round(cell), round(cell))
            painter.setPen(QPen(QColor("#2b3e4d"), 1))
            for n in range(25):
                painter.drawLine(round(n * cell), 0, round(n * cell), self.height())
                painter.drawLine(0, round(n * cell), self.width(), round(n * cell))
        finally:
            painter.end()


class TaskMonitorDialog(DialogAnimMixin, QDialog):
    """独立于游戏窗口的绘画进度监控，不修改目标窗口状态。"""
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ark9Tools · 绘画监控")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setFixedSize(360, 470)
        self.grid: Optional[PaintProgressGrid] = None
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
        self.grid_host = QVBoxLayout()
        layout.addLayout(self.grid_host)
        layout.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        self.stop_btn = QPushButton("停止任务")
        _set_type(self.stop_btn, "danger")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        row.addWidget(self.stop_btn)
        layout.addLayout(row)
        self._animated_once = False

    def set_matrix(self, idx_mat: np.ndarray):
        if self.grid is not None:
            self.grid.deleteLater()
        self.grid = PaintProgressGrid(idx_mat, self)
        self.grid_host.addWidget(self.grid, 0, Qt.AlignCenter)

    def update_progress(self, progress: PaintingProgress):
        if self.grid is not None:
            self.grid.update_state(progress)
        total = max(1, progress.total)
        _smooth_progress(self.progress, round(progress.done * 100 / total))
        elapsed = time.time() - progress.start_time if progress.start_time else 0
        rate = progress.done / elapsed if elapsed > 0 else 0
        eta = (progress.total - progress.done) / rate if rate > 0 else 0
        color = GAME_PALETTE_DATA[progress.cur_color_idx][0] if progress.cur_color_idx is not None else "-"
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
        self.paint_thread: Optional[threading.Thread] = None
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
        self.strategy_combo.addItems(["按颜色频次（推荐）", "行扫描", "列扫描", "行滑动绘制", "列滑动绘制", "螺旋"])
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
            "行滑动绘制": "row_stroke",
            "列滑动绘制": "column_stroke",
            "螺旋": "spiral",
        }[self.strategy_combo.currentText()]

    def _log(self, msg: str):
        self.status_text.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def on_start(self):
        if self.paint_thread is not None and self.paint_thread.is_alive():
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
            QMessageBox.warning(self, "提示", "请先点击窗口右上角的‘连接游戏窗口’")
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
        self.monitor.set_matrix(self.idx_mat)
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
                                       input_mode=input_mode,
                                       stroke_mode=self._strategy().endswith("_stroke"))
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

        self.paint_thread = threading.Thread(target=run, daemon=True)
        self.paint_thread.start()

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
        _smooth_progress(self.progress_bar, p.done)
        if self.monitor and self.monitor.isVisible():
            self.monitor.update_progress(p)
        elapsed = time.time() - p.start_time if p.start_time else 0
        rate = p.done / elapsed if elapsed > 0 else 0
        cur = GAME_PALETTE_DATA[p.cur_color_idx][0] if p.cur_color_idx is not None else "-"
        eta = (p.total - p.done) / rate if rate > 0 else 0
        self._log(f"[{p.done}/{p.total}] 当前色 {cur} | "
                  f"用时 {elapsed:.0f}s | {rate:.2f} 格/s | 剩余约 {eta:.0f}s")


# ===========================================================================
# 主窗口（向导式）
# ===========================================================================
class _DropNativeFilter(QAbstractNativeEventFilter):
    """app 级原生消息过滤器：统一捕获 WM_DROPFILES（拖到任意控件上都生效）。

    管理员权限下 Qt 的 OLE 拖放被 UIPI 拦截，靠 RevokeDragDrop 强制回退到
    WM_DROPFILES 通道；此过滤器挂在 QApplication 上，无论消息发给主窗口
    还是子控件，都能先拿到 HDROP 并取出文件路径。
    """

    def __init__(self, on_drop):
        super().__init__()
        self._on_drop = on_drop
        self._wm_registered = False
        self._installed = False

    def register(self):
        """对主窗口 + 全部子控件：放行跨权限消息、注册 WM_DROPFILES、
        移除 Qt 的 OLE 拖放注册。窗口显示后所有句柄才有效。"""
        if self._wm_registered:
            return
        try:
            import ctypes.wintypes as wt
            MSGFLT_ALLOW = 1
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            ole32 = ctypes.WinDLL("ole32", use_last_error=True)
            ChangeWindowMessageFilterEx = user32.ChangeWindowMessageFilterEx
            ChangeWindowMessageFilterEx.argtypes = [wt.HWND, wt.UINT, wt.UINT,
                                                    ctypes.c_void_p]
            ChangeWindowMessageFilterEx.restype = wt.BOOL
            allow_msgs = (0x0233, 0x004A, 0x0049)  # WM_DROPFILES/WM_COPYDATA/WM_COPYGLOBALDATA
            win = self._on_drop.__self__
            for w in (win, *win.findChildren(QWidget)):
                try:
                    hwnd = int(w.winId())
                    if not hwnd:
                        continue
                    shell32.DragAcceptFiles(hwnd, True)
                    for msg in allow_msgs:
                        ChangeWindowMessageFilterEx(hwnd, msg, MSGFLT_ALLOW, None)
                    ole32.RevokeDragDrop(hwnd)
                except Exception:
                    continue
            self._wm_registered = True
        except Exception:
            pass

    def nativeEventFilter(self, eventType, message):
        try:
            # 注：PySide6 中 eventType 为 bytes（如 b"windows_generic_MSG"），
            # 不在此做强类型判断；Windows 平台消息一律是指向 MSG 的指针。
            # 注意：不能用 `if message:` 判断——PySide6 传入的 VoidPtr
            # 其 size 参数恒为 0，布尔值恒为 False，必须用 is not None。
            if message is not None:
                import ctypes.wintypes as wt
                ptr = int(message)
                msg_id = ctypes.c_uint.from_address(ptr + 8).value
                # WM_DROPFILES 的 HDROP 在 wParam（MSG 偏移 16），lParam 恒为 0
                wparam = ctypes.c_size_t.from_address(ptr + 16).value
                if msg_id == 0x0233 and wparam:
                    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
                    h_drop = ctypes.c_void_p(wparam)
                    DragQueryFileW = shell32.DragQueryFileW
                    DragQueryFileW.argtypes = [ctypes.c_void_p, wt.UINT,
                                               ctypes.c_wchar_p, wt.UINT]
                    DragQueryFileW.restype = wt.UINT
                    DragFinish = shell32.DragFinish
                    DragFinish.argtypes = [ctypes.c_void_p]
                    count = DragQueryFileW(h_drop, 0xFFFFFFFF, None, 0)
                    path = ""
                    for i in range(count):
                        buf = ctypes.create_unicode_buffer(2048)
                        DragQueryFileW(h_drop, i, buf, 2048)
                        if os.path.isfile(buf.value):
                            path = buf.value
                            break
                    DragFinish(h_drop)
                    if path:
                        self._on_drop(path)
                        return True
        except Exception:
            pass
        return False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setWindowTitle("Ark9Tools - 游戏辅助工具中心")
        # 初始窗口大小：按用户截图 1280×970，所有面板一次装下。
        # 用户仍可自由拖动；最小高度保证全部内容仍可见，
        # 最小宽度不限以便用户在窄屏缩小宽度查看。
        self.resize(1280, 970)
        self.setMinimumHeight(760)
        self.setMinimumWidth(720)
        self.setStyleSheet(STYLE)
        self._drop_open_pending = False
        self._drop_filter = _DropNativeFilter(self._accept_external_drop)
        self._global_drop_overlay = QLabel(self)
        self._global_drop_overlay.setAlignment(Qt.AlignCenter)
        self._global_drop_overlay.setWordWrap(True)
        self._global_drop_overlay.setText("将图片拖入此处\n转换为像素画")
        self._global_drop_overlay.setStyleSheet(
            "background:rgba(7, 18, 24, 248);color:#dffff9;border:3px dashed #54d0c0;"
            "border-radius:12px;font-size:28px;font-weight:700;")
        self._global_drop_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._global_drop_overlay.hide()
        self._loading_overlay = LoadingOverlay(self)

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
        self.connect_game_btn = QPushButton("连接游戏窗口")
        self.connect_game_btn.clicked.connect(self.on_open_game_picker)
        head.addWidget(self.connect_game_btn)
        head.addSpacing(14)
        self.session_status = QLabel("● 就绪")
        self.session_status.setProperty("class", "statusGood")
        head.addWidget(self.session_status)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(10)
        sidebar = QFrame()
        sidebar.setProperty("class", "sidebar")
        sidebar.setFixedWidth(150)
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
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, target=index: self._set_module(target))
            attach_hover_raise(btn, lift=0, shadow=False)
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
        drop_hint = QFrame()
        drop_hint.setStyleSheet(
            "QFrame{background:#0e1a1d;border:1px dashed #2c918b;border-radius:8px;}"
            "QLabel{color:#7fe0c4;}")
        drop_layout = QVBoxLayout(drop_hint)
        drop_layout.setContentsMargins(10, 9, 10, 9)
        drop_title = QLabel("拖放图片到此")
        drop_title.setStyleSheet("font-size:12px;font-weight:800;")
        drop_layout.addWidget(drop_title)
        drop_desc = QLabel("把 PNG/JPG 拖到\n窗口任意位置\n即可打开")
        drop_desc.setProperty("class", "subtitle")
        drop_desc.setWordWrap(True)
        drop_layout.addWidget(drop_desc)
        side.addWidget(drop_hint)
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
        self._install_application_drag_filter()
        # 关键：Qt 拖放事件只发给鼠标下方设置了 acceptDrops 的控件。
        # 若子控件（按钮/标签/堆栈页面）不接受，拖动经过时系统直接显示禁止光标，
        # app 级过滤器也拦截不到。这里递归让全部子控件接受拖放。
        for widget in self.findChildren(QWidget):
            widget.setAcceptDrops(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._global_drop_overlay.setGeometry(self.rect())

    def showEvent(self, event):
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None:
            if not self._drop_filter._installed:
                app.installNativeEventFilter(self._drop_filter)
                self._drop_filter._installed = True
            # 首帧后控件/句柄全部就绪，此时注册最可靠
            QTimer.singleShot(150, self._drop_filter.register)

    def _show_global_drop_overlay(self):
        self._global_drop_overlay.setGeometry(self.rect())
        self._global_drop_overlay.show()
        self._global_drop_overlay.raise_()

    def _hide_global_drop_overlay(self):
        self._global_drop_overlay.hide()

    def _install_application_drag_filter(self):
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _external_drag_path(self, event):
        if not hasattr(event, "mimeData") or not event.mimeData().hasUrls():
            return None
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        if len(paths) != 1:
            return None
        path = paths[0]
        if not os.path.isfile(path):
            return None
        if os.path.splitext(path)[1].lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
            return None
        return path

    def _accept_external_drop(self, path):
        if self._drop_open_pending:
            return False
        self._drop_open_pending = True
        self._show_global_drop_overlay()
        QApplication.processEvents()
        # 保留一帧以上的遮罩反馈，随后自动进入像素画流程。
        QTimer.singleShot(180, lambda: self._finish_external_drop(path))
        return True

    def _finish_external_drop(self, path):
        self._hide_global_drop_overlay()
        try:
            # 拖入图片：先弹“检测到图片”选择浮窗，用户确认后进入像素画流程。
            # 不自动跳流程，避免拖放误触/过于绝对。
            dialog = ImageActionDialog(path, self)
            if dialog.exec() != QDialog.Accepted:
                return True
            # 用户选择“绘制像素画”：自动切到 MAA-PixelPainter 第①步并打开编辑。
            # 拖放只触发像素画流程，不影响其他模块的任何状态。
            if self.current_module != 1 or self.current_step != 0:
                self._open_pixel_step(0)
            self.step_pixelate._open_image_editor(path)
        finally:
            self._drop_open_pending = False
        return True

    def dragEnterEvent(self, event):
        path = self._external_drag_path(event)
        if path:
            self._show_global_drop_overlay()
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        path = self._external_drag_path(event)
        if path:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._hide_global_drop_overlay()
        event.accept()

    def dropEvent(self, event):
        path = self._external_drag_path(event)
        self._hide_global_drop_overlay()
        if path and self._accept_external_drop(path):
            event.acceptProposedAction()
            return
        event.ignore()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.DragEnter, QEvent.DragMove):
            path = self._external_drag_path(event)
            if path:
                self._show_global_drop_overlay()
                event.acceptProposedAction()
                return True
        elif event.type() == QEvent.Drop:
            path = self._external_drag_path(event)
            self._hide_global_drop_overlay()
            if path and self._accept_external_drop(path):
                event.acceptProposedAction()
                return True
        elif event.type() == QEvent.DragLeave:
            self._hide_global_drop_overlay()
        return super().eventFilter(watched, event)

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

    def on_open_game_picker(self):
        dialog = GameWindowPickerDialog(getattr(self.ctx, "hwnd", None), self)
        dialog.window_selected.connect(self._on_game_window_selected)
        dialog.exec()

    def _on_game_window_selected(self, hwnd: int, title: str):
        self.ctx.hwnd = int(hwnd)
        self.step_calibrate.hwnd = int(hwnd)
        self.step_calibrate.hwnd_label.setText(f"已连接窗口：{title}（0x{hwnd:X}）")
        self.step_calibrate.hwnd_label.setProperty("accent", "true")
        self.step_calibrate.hwnd_label.style().unpolish(self.step_calibrate.hwnd_label)
        self.step_calibrate.hwnd_label.style().polish(self.step_calibrate.hwnd_label)
        cfg = load_config()
        cfg["window_hwnd"] = int(hwnd)
        cfg["window_region"] = list(_client_region_from_hwnd(int(hwnd)))
        save_config(cfg)
        self._update_nav()

    def _set_module(self, target: int):
        prev = self.current_module
        # 模块切换：新页从右侧滑入 + 淡入（方向随切换方向）
        direction = 1 if target >= prev else -1
        slide_switch_stacked(self.module_stack, target, duration=200,
                             direction=direction)
        self.current_module = target
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
                QMessageBox.information(self, "请先连接游戏", "请先点击窗口右上角的‘连接游戏窗口’。")
                return
            self.step_paint._load_from_ctx()
        prev_step = self.current_step
        direction = 1 if target >= prev_step else -1
        slide_switch_stacked(self.stack, target, duration=200, direction=direction)
        self.current_step = target
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
                QMessageBox.warning(self, "提示", "请先点击窗口右上角的‘连接游戏窗口’")
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
    # 使用系统原生文件对话框（Windows 资源管理器风格，可直接预览图片缩略图）。
    # 之前的 Qt 自定义对话框无法预览图片，且拖入图片也不如原生对话框可靠。
    app = QApplication(sys.argv)
    app.setApplicationName("Ark9Tools")
    font = QFont("Segoe UI")
    font.setPointSize(10)
    app.setFont(font)
    app.setWindowIcon(app_icon())  # 窗口/任务栏 Logo
    startup = LoadingOverlay()
    startup.start("正在初始化 Ark9Tools…", delay_ms=0)
    app.processEvents()
    win = MainWindow()
    win.setWindowIcon(app_icon())
    win.show()
    # 主窗口显示后把加载动画重新拉回顶层，并延迟关闭，
    # 确保用户能完整看到初始化动画而非一闪而过。
    startup._do_show()
    startup.raise_()
    startup.activateWindow()
    QTimer.singleShot(1200, startup.stop)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
