"""Opt-in experimental window capture lifecycle.

Wraps the existing HWND capture backend with the lifecycle guarantees used by
MaaFramework's FramePoolWithPseudoMinimizeScreencap: validate frames, detect
size changes, enforce timeouts, cache the last valid frame, and tear down the
pseudo-minimize helper on any failure.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

from experimental_backend import ExperimentalPseudoMinimize


class ExperimentalCaptureSession:
    def __init__(self, hwnd: int, capturer, *, frame_timeout: float = 2.0, max_failures: int = 3):
        self.hwnd = int(hwnd)
        self.capturer = capturer
        self.frame_timeout = frame_timeout
        self.max_failures = max_failures
        self.helper = ExperimentalPseudoMinimize(hwnd)
        self.last_frame: Optional[np.ndarray] = None
        self.last_size: Optional[tuple[int, int]] = None
        self.failure_count = 0
        self.last_error = ""
        self._lock = threading.RLock()
        self._closed = False

    def start(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            if not self.helper.start():
                self.last_error = self.helper.last_error
                return False
            return True

    def grab(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._closed:
                return None
            if not self.helper.ensure_not_minimized():
                self._fail("伪最小化状态无法恢复")
                return self.last_frame.copy() if self.last_frame is not None else None
        started = time.monotonic()
        try:
            frame = self.capturer.grab()
        except Exception as exc:
            self._fail(f"捕获异常: {exc}")
            return self.last_frame.copy() if self.last_frame is not None else None
        elapsed = time.monotonic() - started
        if frame is None:
            self._fail("捕获后端未返回帧")
            return self.last_frame.copy() if self.last_frame is not None else None
        if elapsed > self.frame_timeout:
            self._fail(f"帧超时: {elapsed:.2f}s")
            return self.last_frame.copy() if self.last_frame is not None else None
        if not isinstance(frame, np.ndarray) or frame.ndim < 2 or frame.size == 0:
            self._fail("捕获帧为空或格式无效")
            return self.last_frame.copy() if self.last_frame is not None else None
        size = (int(frame.shape[1]), int(frame.shape[0]))
        if self.last_size is not None and size != self.last_size:
            # FramePool 在窗口尺寸变化时需要重建资源；Python 后端通过重建 capturer
            # 交给上层实现，当前会先丢弃缓存，避免按旧尺寸使用帧。
            self.last_frame = None
        self.last_size = size
        self.failure_count = 0
        self.last_frame = frame.copy()
        return frame

    def _fail(self, message: str) -> None:
        self.failure_count += 1
        self.last_error = message
        if self.failure_count >= self.max_failures:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.capturer.close()
            finally:
                self.helper.stop()

    def __enter__(self):
        if not self.start():
            raise RuntimeError(self.last_error or "实验捕获会话启动失败")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
