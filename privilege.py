# -*- coding: utf-8 -*-
"""权限检测与提权重启（解决 UIPI 输入注入被过滤的问题）。

背景：明日方舟 PC 端默认以管理员权限运行。Windows UIPI 安全机制规定，
低完整性/非提权进程不能向高完整性窗口注入 SendInput 输入、也不能抢前台。
这会导致自动绘画"点击无效、窗口无法激活"。本模块提供：
- check_game_privilege(hwnd)：判断游戏是否需要管理员权限
- is_elevated()：当前进程是否已提权
- relaunch_as_admin()：以管理员权限重启本程序
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
from typing import Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20
ERROR_ACCESS_DENIED = 5


def is_elevated() -> bool:
    """当前进程是否以管理员（提权）身份运行"""
    try:
        class TOKEN_ELEVATION(ctypes.Structure):
            _fields_ = [("TokenIsElevated", wt.DWORD)]
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                 os.getpid())
        if not h:
            return False
        try:
            t = wt.HANDLE()
            if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(t)):
                return False
            try:
                te = TOKEN_ELEVATION()
                size = wt.DWORD()
                if not advapi32.GetTokenInformation(t, TokenElevation,
                                                    ctypes.byref(te),
                                                    ctypes.sizeof(te),
                                                    ctypes.byref(size)):
                    return False
                return bool(te.TokenIsElevated)
            finally:
                kernel32.CloseHandle(t)
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return False


def _process_token_elevation(pid: int):
    """查询进程是否提权。返回 (is_elevated|None, err_msg)。
    ACCESS_DENIED 说明目标权限高于当前进程（通常即管理员运行）。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None, f"OpenProcess 失败 err={ctypes.get_last_error()}"
    try:
        t = wt.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(t)):
            err = ctypes.get_last_error()
            kernel32.CloseHandle(h)
            return None, f"OpenProcessToken 失败 err={err}"
        kernel32.CloseHandle(h)
        try:
            class TOKEN_ELEVATION(ctypes.Structure):
                _fields_ = [("TokenIsElevated", wt.DWORD)]
            te = TOKEN_ELEVATION()
            size = wt.DWORD()
            if not advapi32.GetTokenInformation(t, TokenElevation,
                                                ctypes.byref(te),
                                                ctypes.sizeof(te),
                                                ctypes.byref(size)):
                return None, f"GetTokenInformation 失败 err={ctypes.get_last_error()}"
            return bool(te.TokenIsElevated), ""
        finally:
            kernel32.CloseHandle(t)
    except Exception as e:
        return None, f"异常: {e}"


def check_game_privilege(hwnd: int) -> Optional[bool]:
    """判断游戏窗口是否以管理员权限运行。

    Returns:
        True  : 游戏需要管理员权限才能被注入输入
        False : 游戏为普通权限，无需提权
        None  : 无法判断（窗口无效或查询失败）
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    elev, err = _process_token_elevation(pid.value)
    if elev is not None:
        return elev
    # ACCESS_DENIED：目标进程权限高于当前进程（管理员运行的特征）
    if err and "err=5" in err:
        return True
    return None


def relaunch_as_admin() -> bool:
    """以管理员权限重新启动本程序（UAC 提权），返回是否成功拉起。"""
    try:
        if getattr(sys, "frozen", False):
            # 打包成 exe：直接以 exe 路径运行
            target = sys.executable
            params = subprocess.list2cmdline(sys.argv[1:])
        else:
            target = sys.executable
            args = [os.path.abspath(sys.argv[0])] + sys.argv[1:]
            params = subprocess.list2cmdline(args)
        ShellExecuteW = ctypes.WinDLL("shell32").ShellExecuteW
        ShellExecuteW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                  ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
        ShellExecuteW.restype = ctypes.c_int
        ret = ShellExecuteW(None, "runas", target, params, None, 1)  # SW_SHOWNORMAL
        return ret > 32
    except Exception:
        return False
