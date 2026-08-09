"""Recommended HDR-aware SDR window capture backend.

Uses LDNKS094/hdrcapture in ``mode='auto'``. On HDR displays the library applies
its GPU tone mapping and returns BGRA8 SDR; on SDR displays it returns BGRA8
without a separate color path. Ark9Tools converts this canonical output to BGR8
for existing OpenCV/recognition code.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np


class HdrCaptureError(RuntimeError):
    pass


class AutoSdrWindowCapture:
    """Thread-confined reusable hwnd capture pipeline with safe reinitialization."""

    def __init__(self, hwnd: int, *, max_failures: int = 3):
        try:
            import hdrcapture
        except ImportError as exc:
            raise HdrCaptureError("未安装 hdrcapture；请使用 Ark9Tools 的 Python 3.13 虚拟环境") from exc
        self._api = hdrcapture
        self.hwnd = int(hwnd)
        self.max_failures = max_failures
        self._capture = None
        self._thread_id: Optional[int] = None
        self._size: Optional[tuple[int, int]] = None
        self._failures = 0
        self.last_error = ""

    def _ensure_pipeline(self) -> None:
        current = threading.get_ident()
        if self._capture is not None and self._thread_id == current:
            return
        self.close()
        try:
            self._capture = self._api.capture.window(hwnd=self.hwnd, mode="auto", headless=True)
            self._thread_id = current
        except Exception as exc:
            self._capture = None
            self._thread_id = None
            self.last_error = f"hdrcapture 初始化失败: {exc}"
            raise HdrCaptureError(self.last_error) from exc

    def grab(self) -> Optional[np.ndarray]:
        try:
            self._ensure_pipeline()
            # capture() waits for a new frame; this avoids stale frames after
            # palette dragging or a display HDR state switch.
            frame = self._capture.capture()
            arr = frame.ndarray()
            if arr is None or arr.ndim != 3 or arr.shape[2] < 3:
                raise HdrCaptureError("hdrcapture 返回无效帧")
            if arr.dtype != np.uint8:
                raise HdrCaptureError(f"期望 SDR bgra8，实际为 {arr.dtype}/{frame.format}")
            size = (int(arr.shape[1]), int(arr.shape[0]))
            if self._size is not None and size != self._size:
                # Window resize/HDR mode changes invalidate the pipeline.
                self._size = size
                self._recreate()
                frame = self._capture.capture()
                arr = frame.ndarray()
                if arr is None or arr.dtype != np.uint8:
                    raise HdrCaptureError("窗口尺寸变化后未获得 SDR 帧")
            else:
                self._size = size
            self._failures = 0
            # hdrcapture auto output order is BGRA.
            return np.ascontiguousarray(arr[:, :, :3])
        except Exception as exc:
            self._failures += 1
            self.last_error = str(exc)
            if self._failures >= self.max_failures:
                self.close()
            return None

    def _recreate(self) -> None:
        self.close()
        self._ensure_pipeline()

    def close(self) -> None:
        capture, self._capture = self._capture, None
        self._thread_id = None
        if capture is not None:
            try:
                capture.close()
            except Exception:
                pass
