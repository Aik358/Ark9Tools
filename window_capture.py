# -*- coding: utf-8 -*-
"""WGC 指定窗口捕获（对齐 MAA FramePoolScreencap 架构）

特性（与 MAA 官方一致）：
- 直接捕获窗口内容，不需要窗口在前台；被遮挡也能捕获（DWM 合成管线）
- 通过 winrt interop `create_for_window(hwnd)` 创建捕获项
- HDR 屏幕自动由系统完成 HDR→SDR 色调映射（B8G8R8A8UIntNormalized）

实现参考：
- MAA FramePoolScreencap.cpp（帧池 + staging + CopyResource + Map）
- dxcam WinRTDuplicator / StageSurface（python-winrt 具体调用方式）
"""
import ctypes
import time
from typing import Optional, Tuple
import numpy as np

try:
    import dxcam
    import winrt.windows.graphics.capture as wgc
    import winrt.windows.graphics.capture.interop as wgci
    import winrt.windows.graphics.directx as wgd
    import winrt.windows.graphics.directx.direct3d11.interop as wgd3d
    from dxcam.core.device import Device
    from dxcam._libs.dxgi import IDXGIDevice, IDXGISurface, DXGI_MAPPED_RECT
    from dxcam._libs.d3d11 import (
        D3D11_CPU_ACCESS_READ,
        D3D11_TEXTURE2D_DESC,
        D3D11_USAGE_STAGING,
        ID3D11Texture2D,
        ID3D11Resource,
    )
    _HAS_WGC = True
except Exception:
    _HAS_WGC = False


class WindowGrabber:
    """基于 WGC 的指定窗口捕获器"""

    def __init__(self, hwnd: int):
        if not _HAS_WGC:
            raise RuntimeError("WGC (winrt) 后端不可用，请安装 dxcam[winrt]")
        self.hwnd = hwnd
        self._device = None
        self._winrt_device = None
        self._capture_item = None
        self._frame_pool = None
        self._session = None
        self._staging = None        # ctypes.POINTER(ID3D11Texture2D)
        self._staging_interface = None  # comtypes IDXGISurface
        self._staging_desc = None
        self._size = (0, 0)
        self._start()

    def _start(self):
        # 1. D3D11 设备（默认适配器）
        adapters = dxcam.enum_dxgi_adapters()
        if not adapters:
            raise RuntimeError("未找到 DXGI 适配器")
        self._device = Device(adapter=adapters[0])
        self._device_context = self._device.im_context

        # 2. IDXGIDevice → WinRT IDirect3DDevice
        dxgi_device = self._device.device.QueryInterface(IDXGIDevice)
        ptr = ctypes.cast(dxgi_device, ctypes.c_void_p).value
        self._winrt_device = wgd3d.create_direct3d11_device_from_dxgi_device(ptr)

        # 3. hwnd → GraphicsCaptureItem（核心：捕获窗口而非屏幕）
        self._capture_item = wgci.create_for_window(self.hwnd)
        item_size = self._capture_item.size

        # 4. 帧池 + 会话（BufferCount=2，同 dxcam）
        self._frame_pool = wgc.Direct3D11CaptureFramePool.create_free_threaded(
            self._winrt_device,
            wgd.DirectXPixelFormat.B8_G8_R8_A8_UINT_NORMALIZED,
            2,
            item_size,
        )
        self._session = self._frame_pool.create_capture_session(self._capture_item)
        try:
            self._session.is_border_required = False
        except Exception:
            pass
        try:
            self._session.is_cursor_capture_enabled = False
        except Exception:
            pass

        # 5. 帧到达事件（保持回调强引用）
        from threading import Event
        self._frame_event = Event()
        self._frame_handler = self._on_frame_arrived
        try:
            self._frame_token = self._frame_pool.add_frame_arrived(
                self._frame_handler)
        except Exception:
            self._frame_token = None

        self._session.start_capture()
        self._size = (int(item_size.width), int(item_size.height))

    def _on_frame_arrived(self, _sender, _args):
        self._frame_event.set()

    @property
    def size(self) -> Tuple[int, int]:
        return self._size

    def _drain_to_latest_frame(self):
        """排空帧池，返回最新一帧（或 None）"""
        latest = None
        while True:
            try:
                frame = self._frame_pool.try_get_next_frame()
            except Exception:
                return None
            if frame is None:
                break
            if latest is not None:
                try:
                    latest.close()
                except Exception:
                    pass
            latest = frame
        return latest

    def _wait_frame(self):
        """等待帧到达事件（最多 0.5s）"""
        if self._frame_event is not None:
            if self._frame_event.wait(timeout=0.5):
                self._frame_event.clear()
                return True
        return False

    def grab(self) -> Optional[np.ndarray]:
        """取一帧（BGR uint8）。失败返回 None。"""
        try:
            # 1. 等帧到达（首帧稍慢）
            got = self._wait_frame()
            if not got:
                # 没有事件就轮询取一次
                frame = self._drain_to_latest_frame()
                if frame is None:
                    # 再给一次机会：多等 300ms
                    time.sleep(0.3)
                    frame = self._drain_to_latest_frame()
            else:
                frame = self._drain_to_latest_frame()
            if frame is None:
                return None

            # 2. 帧 → DXGI 表面 → D3D11 纹理（comtypes 对象）
            surface_ptr = wgd3d.get_dxgi_surface_from_object(frame.surface)
            if not surface_ptr:
                try:
                    frame.close()
                except Exception:
                    pass
                return None
            surface = ctypes.cast(surface_ptr, ctypes.POINTER(IDXGISurface))
            texture = surface.QueryInterface(ID3D11Texture2D)  # comtypes 对象

            # 3. staging 纹理（CPU 可读）
            desc = D3D11_TEXTURE2D_DESC()
            texture.GetDesc(ctypes.byref(desc))
            key = (int(desc.Width), int(desc.Height))
            if self._staging is None or self._staging_desc != key:
                self._staging_desc = key
                self._staging = ctypes.POINTER(ID3D11Texture2D)()
                sd = D3D11_TEXTURE2D_DESC()
                sd.Width = desc.Width
                sd.Height = desc.Height
                sd.Format = desc.Format
                sd.MipLevels = 1
                sd.ArraySize = 1
                sd.SampleDesc.Count = 1
                sd.SampleDesc.Quality = 0
                sd.Usage = D3D11_USAGE_STAGING
                sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ
                sd.MiscFlags = 0
                sd.BindFlags = 0
                hr = self._device.device.CreateTexture2D(
                    ctypes.byref(sd), None, ctypes.byref(self._staging))
                if hr != 0:
                    try:
                        frame.close()
                    except Exception:
                        pass
                    return None
                self._staging_interface = self._staging.QueryInterface(IDXGISurface)

            # 4. GPU 纹理 → staging
            staging_res = ctypes.cast(self._staging, ctypes.POINTER(ID3D11Resource))
            tex_res = ctypes.cast(texture, ctypes.POINTER(ID3D11Resource))
            self._device_context.CopyResource(staging_res, tex_res)

            # 5. Map 读取像素
            rect = DXGI_MAPPED_RECT()
            hr = self._staging_interface.Map(ctypes.byref(rect), 1)  # DXGI_MAP_READ
            if hr != 0:
                try:
                    frame.close()
                except Exception:
                    pass
                return None
            try:
                w = int(desc.Width)
                h = int(desc.Height)
                row_pitch = int(rect.Pitch)
                buf = ctypes.string_at(rect.pBits, row_pitch * h)
                arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, row_pitch // 4, 4)
                bgr = arr[:, :w, :3].copy()
            finally:
                try:
                    self._staging_interface.Unmap()
                except Exception:
                    pass

            try:
                frame.close()
            except Exception:
                pass
            self._size = (w, h)

            # HDR 校正：当窗口在 HDR 显示器上，WGC 可能返回过曝的 HDR 值。
            # 检测并做 HDR→SDR 校正，避免画面过亮/全白。
            bgr = self._hdr_correct(bgr)
            return bgr
        except Exception:
            return None

    def _hdr_correct(self, bgr: np.ndarray) -> np.ndarray:
        """检测 HDR 过曝帧并校正到正常 SDR 亮度。

        判断依据：截图整体过亮（均值偏高）+ 动态范围大（HDR 特征）。
        若判断为 HDR，用 99 分位归一化 + 反 gamma 压缩到 SDR。
        """
        if bgr is None or bgr.size == 0:
            return bgr
        arr = bgr.astype(np.float32)
        mean_val = float(arr.mean())
        white_ratio = float((arr > 245).mean())
        # HDR 过曝特征：整体均值偏高（正常游戏画面约 100-130，HDR 会更高）
        if mean_val > 145 or white_ratio > 0.08:
            # HDR 线性数据：按均值压缩到 SDR 基准（target_mean≈115）
            # 线性缩放，保持对比度但降低整体亮度
            target_mean = 115.0
            scale = target_mean / max(mean_val, 1.0)
            arr = arr * scale
            return np.clip(arr, 0, 255).astype(np.uint8)
        return bgr

    def release(self):
        for obj in (self._session, self._frame_pool, self._capture_item):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        self._session = None
        self._frame_pool = None
        self._capture_item = None
        if self._staging is not None:
            try:
                self._staging.Release()
            except Exception:
                pass
            self._staging = None
            self._staging_interface = None
