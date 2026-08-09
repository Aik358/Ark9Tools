"""Ark9Tools experimental MAA-style backend.

This module is intentionally opt-in. The stable backend remains the default.
It mirrors the safe lifecycle of MaaFramework's PseudoMinimizeHelper:
- snapshot original extended style and layered alpha
- add WS_EX_LAYERED | WS_EX_TRANSPARENT
- restore with SW_SHOWNOACTIVATE
- monitor the target window and revert when it becomes foreground
- always restore state on stop/failure

Full FramePool/D3D11 staging and low-level mouse-hook tracking require a native
Windows build toolchain. The official C++ sources are kept under
``tools/official_maa/MaaFramework`` as the native implementation reference.
"""
from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass
from typing import Optional

import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_ALPHA = 0x00000002
SW_SHOWNOACTIVATE = 4
SW_MINIMIZE = 6

GetWindowLongPtrW = user32.GetWindowLongPtrW
GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
GetWindowLongPtrW.restype = ctypes.c_ssize_t
SetWindowLongPtrW = user32.SetWindowLongPtrW
SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
SetWindowLongPtrW.restype = ctypes.c_ssize_t
GetLayeredWindowAttributes = user32.GetLayeredWindowAttributes
GetLayeredWindowAttributes.argtypes = [wt.HWND, ctypes.POINTER(wt.COLORREF), ctypes.POINTER(wt.BYTE), ctypes.POINTER(wt.DWORD)]
GetLayeredWindowAttributes.restype = wt.BOOL
SetLayeredWindowAttributes = user32.SetLayeredWindowAttributes
SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF, wt.BYTE, wt.DWORD]
SetLayeredWindowAttributes.restype = wt.BOOL
ShowWindow = user32.ShowWindow
ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
ShowWindow.restype = wt.BOOL
IsWindow = user32.IsWindow
IsWindow.argtypes = [wt.HWND]
IsWindow.restype = wt.BOOL
IsIconic = user32.IsIconic
IsIconic.argtypes = [wt.HWND]
IsIconic.restype = wt.BOOL
GetForegroundWindow = user32.GetForegroundWindow
GetForegroundWindow.restype = wt.HWND


@dataclass
class ExperimentalState:
    original_ex_style: int
    had_layered_style: bool
    original_alpha: int
    pseudo_minimized: bool = False


class ExperimentalPseudoMinimize:
    """Safe, opt-in Python equivalent of PseudoMinimizeHelper."""

    def __init__(self, hwnd: int, poll_seconds: float = 0.1):
        self.hwnd = int(hwnd)
        self.poll_seconds = poll_seconds
        self._state: Optional[ExperimentalState] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self.last_error = ""

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return True
            if not IsWindow(self.hwnd):
                self.last_error = "目标窗口无效"
                return False
            ex_style = int(GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE))
            if not ex_style and ctypes.get_last_error():
                self.last_error = f"读取窗口扩展样式失败: {ctypes.get_last_error()}"
                return False
            had_layered = bool(ex_style & WS_EX_LAYERED)
            alpha = 255
            if had_layered:
                key = wt.COLORREF(0)
                flags = wt.DWORD(0)
                value = wt.BYTE(255)
                if GetLayeredWindowAttributes(self.hwnd, ctypes.byref(key), ctypes.byref(value), ctypes.byref(flags)):
                    alpha = int(value.value)
            self._state = ExperimentalState(ex_style, had_layered, alpha)
            self._running = True
            self._thread = threading.Thread(target=self._monitor, name="Ark9Tools-PseudoMinimize", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        with self._lock:
            if self._state and self._state.pseudo_minimized:
                self.revert()
            self._state = None
            self._thread = None

    def ensure_not_minimized(self) -> bool:
        with self._lock:
            if not self._running or not self._state or not IsWindow(self.hwnd):
                return False
            if IsIconic(self.hwnd):
                return self.apply()
            return True

    def apply(self) -> bool:
        with self._lock:
            if not self._state or not IsWindow(self.hwnd):
                return False
            current = int(GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE))
            if not SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, current | WS_EX_LAYERED | WS_EX_TRANSPARENT):
                self.last_error = f"设置窗口扩展样式失败: {ctypes.get_last_error()}"
                return False
            if not SetLayeredWindowAttributes(self.hwnd, 0, 0, LWA_ALPHA):
                SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, self._state.original_ex_style)
                self.last_error = f"设置窗口透明度失败: {ctypes.get_last_error()}"
                return False
            ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self._state.pseudo_minimized = True
            return True

    def revert(self) -> bool:
        with self._lock:
            if not self._state or not IsWindow(self.hwnd):
                return False
            if not SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, self._state.original_ex_style):
                self.last_error = f"恢复窗口扩展样式失败: {ctypes.get_last_error()}"
                return False
            if self._state.had_layered_style:
                SetLayeredWindowAttributes(self.hwnd, 0, self._state.original_alpha, LWA_ALPHA)
            self._state.pseudo_minimized = False
            return True

    def _monitor(self) -> None:
        while self._running:
            if not IsWindow(self.hwnd):
                self.last_error = "目标窗口已失效"
                self._running = False
                return
            with self._lock:
                if self._state and self._state.pseudo_minimized and GetForegroundWindow() == self.hwnd:
                    self.revert()
            time.sleep(self.poll_seconds)

    def __enter__(self):
        if not self.start():
            raise RuntimeError(self.last_error or "实验伪最小化启动失败")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
