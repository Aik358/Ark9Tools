# -*- coding: utf-8 -*-
"""WGC 截图模块（Python 实现）

优先使用 dxcam 的 winrt 后端（Windows.Graphics.Capture）：
- WGC 会自动把 HDR 显示器内容色调映射到 SDR，避免截图过亮/发白
- 若 winrt 不可用则降级 dxgi，再降级 mss

HDR 说明：当系统开启 HDR 时，DXGI 桌面复制拿到的可能是 HDR 浮点帧，
导致数值溢出发白。WGC (winrt) 后端使用 B8G8R8A8UIntNormalized 格式，
由系统自动完成 HDR→SDR 转换。这是官方推荐做法。
"""
import ctypes
from typing import Optional, Tuple
import numpy as np

# 在导入任何 GUI/捕获库之前设置 DPI 感知，避免 Qt 后续设置失败
try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        _shcore = ctypes.WinDLL("shcore", use_last_error=True)
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        _shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

try:
    import dxcam
    _HAS_DXCAM = True
except Exception:
    _HAS_DXCAM = False

try:
    import mss
    _HAS_MSS = True
except Exception:
    _HAS_MSS = False


def _detect_hdr_frame(frame: np.ndarray) -> bool:
    """从一帧截图中检测是否疑似 HDR 数据。

    判断依据：
    - 浮点 dtype（dxgi 后端 HDR 帧常为 float16/float32）
    - 或值明显超出 8bit 范围（>260 或 [0,1] 浮点）
    普通 uint8 SDR 帧（即使很亮）不会被误判。
    """
    if frame is None or frame.size == 0:
        return False
    arr = np.asarray(frame)
    if arr.dtype.kind == "f":      # 浮点纹理
        if arr.max() > 1.0:
            return True            # 线性 HDR 值
        # [0,1] 浮点也可能是 dxgi 转出的归一化帧，保守视为非 HDR
        return False
    if arr.max() > 260.0:          # 明显超出 8bit 范围
        return True
    return False


def _sdr_correct(frame: np.ndarray) -> np.ndarray:
    """HDR→SDR 后处理校正（兜底方案）。

    当 WGC 不可用、降级到 DXGI 后端时，HDR 帧可能亮度偏高。
    做法：浮点线性值 → 归一化 → 反 sRGB gamma → 8bit。
    """
    arr = np.asarray(frame, dtype=np.float32)
    if arr.max() <= 1.5:
        # 已是 [0,1] 浮点：直接反 gamma
        arr = np.clip(arr, 0.0, 1.0)
        arr = np.power(arr, 1.0 / 2.2)
        return np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    # 线性 HDR：归一化到 [0,1]（用 99 分位避免单点极值）
    p99 = float(np.percentile(arr, 99))
    if p99 <= 1:
        p99 = float(arr.max())
    arr = np.clip(arr, 0, p99) / p99
    arr = np.power(arr, 1.0 / 2.2)
    return np.clip(arr * 255.0, 0, 255).astype(np.uint8)


class ScreenCapturer:
    """窗口区域截图（DXGI 桌面复制优先，无黄框；winrt 仅作 HDR 兜底）"""

    def __init__(self, window_region: Tuple[int, int, int, int],
                 prefer_winrt: bool = False):
        """
        Args:
            window_region: (left, top, right, bottom) 窗口客户区屏幕坐标
            prefer_winrt: 是否优先用 WGC (winrt) 后端。

            注意：winrt (WGC) 后端截图时会显示一个"捕获黄框"（系统级边框），
            干扰用户。因此默认用 DXGI 桌面复制后端（无边框）。
            仅当屏幕是 HDR 且需要色彩校正时，才用 winrt 兜底。
        """
        self.region = window_region
        self.prefer_winrt = prefer_winrt
        self._dxcam_inst = None
        self._used_winrt = False
        self._hdr_correct_enabled = True

        if _HAS_DXCAM:
            backends = []
            if prefer_winrt:
                backends.append("winrt")
            backends.append("dxgi")
            for backend in backends:
                try:
                    self._dxcam_inst = dxcam.create(
                        region=window_region,
                        output_color="BGR",
                        max_buffer_len=2,
                        backend=backend,
                    )
                    self._used_winrt = (backend == "winrt")
                    break
                except Exception:
                    self._dxcam_inst = None
                    continue

    def grab(self) -> Optional[np.ndarray]:
        """截取窗口当前画面（BGR uint8）。失败返回 None。

        若使用 DXGI 后端且检测到 HDR 过曝帧，自动做 SDR 校正。
        """
        if self._dxcam_inst is not None:
            try:
                frame = self._dxcam_inst.grab()
                if frame is not None:
                    frame = np.asarray(frame)
                    if (not self._used_winrt
                            and self._hdr_correct_enabled
                            and _detect_hdr_frame(frame)):
                        frame = _sdr_correct(frame)
                    return frame
            except Exception:
                pass

        if _HAS_MSS:
            try:
                with mss.mss() as sct:
                    l, t, r, b = self.region
                    mon = {"left": l, "top": t, "width": r - l, "height": b - t}
                    img = np.asarray(sct.grab(mon))  # BGRA
                    bgr = img[:, :, :3]
                    if self._hdr_correct_enabled and _detect_hdr_frame(bgr):
                        bgr = _sdr_correct(bgr)
                    return bgr
            except Exception:
                return None
        return None

    def close(self):
        if self._dxcam_inst is not None:
            try:
                self._dxcam_inst.release()
            except Exception:
                pass
            self._dxcam_inst = None


class WindowCapturer:
    """窗口捕获器（默认区域截图，稳定无闪退）。

    注意：WGC 窗口捕获（WindowGrabber）的帧回调线程与 COM 对象生命周期纠缠，
    反复创建/释放会触发进程级崩溃（try/except 捕获不到）。因此 use_wgc=False
    时直接走 ScreenCapturer（DXGI 区域截图，无黄框、HDR 自动校正）。
    仅在确需 WGC 特性（如被遮挡截图）时显式开启。
    """

    def __init__(self, hwnd: int,
                 fallback_region: Optional[Tuple[int, int, int, int]] = None,
                 use_wgc: bool = False):
        self.hwnd = hwnd
        self.fallback_region = fallback_region
        self._grabber = None
        self._fallback = None
        self._thread = None
        self._stop = False

        # 默认禁用 WGC：WGC 帧回调线程与 COM 对象生命周期纠缠，
        # 反复创建/释放会触发"访问已释放对象"的进程级崩溃（try/except 捕获不到）。
        # 统一走 DXGI 区域截图（ScreenCapturer），稳定且无黄框。
        if use_wgc:
            try:
                from window_capture import WindowGrabber
                self._grabber = WindowGrabber(hwnd)
            except Exception:
                self._grabber = None
        if self._grabber is None and fallback_region is not None:
            self._fallback = ScreenCapturer(fallback_region)

    @property
    def size(self):
        if self._grabber is not None:
            try:
                return self._grabber.size
            except Exception:
                return None
        return None

    def grab(self) -> Optional[np.ndarray]:
        """截取窗口内容（BGR uint8）。"""
        if self._grabber is not None:
            try:
                frame = self._grabber.grab()
                if frame is not None:
                    return frame
            except Exception:
                pass
        if self._fallback is not None:
            try:
                return self._fallback.grab()
            except Exception:
                return None
        return None

    def close(self):
        if self._grabber is not None:
            try:
                self._grabber.release()
            except Exception:
                pass
            self._grabber = None
        if self._fallback is not None:
            try:
                self._fallback.close()
            except Exception:
                pass
            self._fallback = None
