# -*- coding: utf-8 -*-
"""Win32 消息输入模块（sendmsg 鼠标/键盘）

完整对应 MAA MessageInput：
- 鼠标：SendMessageW(WM_LBUTTONDOWN/UP/WM_MOUSEMOVE) → target window
- 键盘：SendMessageW(WM_KEYDOWN/UP/CHAR)
- lParam: 客户区坐标编码（MAKELPARAM）
"""
import ctypes
import ctypes.wintypes as wt
import time
from typing import Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_ACTIVATE = 0x0006
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WA_ACTIVE = 1

MK_LBUTTON = 0x0001
WHEEL_DELTA = 120

# SendInput 相关常量（硬件级输入，Unity 游戏需要）
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_KEYUP = 0x0002

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# 前台激活 / 全局热键相关
VK_MENU = 0x12
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
VK_F8 = 0x77
HOTKEY_PAINT_STOP = 0xB001   # 绘画急停全局热键 ID（F8）

# SendMessageW / PostMessageW 签名
LRESULT = ctypes.c_int64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
SendMessageW = user32.SendMessageW
SendMessageW.restype = LRESULT
SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

PostMessageW = user32.PostMessageW
PostMessageW.restype = wt.BOOL
PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

IsWindow = user32.IsWindow
IsWindow.argtypes = [wt.HWND]

GetCursorPos = user32.GetCursorPos
GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
GetCursorPos.restype = wt.BOOL

GetWindowRect = user32.GetWindowRect
GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
GetWindowRect.restype = wt.BOOL

SetWindowPos = user32.SetWindowPos
SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, wt.UINT]
SetWindowPos.restype = wt.BOOL

ClientToScreen = user32.ClientToScreen
ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
ClientToScreen.restype = wt.BOOL


# SetWindowPos 标志（MAA 实现使用的组合）
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_ASYNCWINDOWPOS = 0x4000
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
SW_SHOW = 5
SW_RESTORE = 9
SW_MINIMIZE = 6
SWP_NOOWNERZORDER = 0x0200

# 前台激活相关 API（MAA ForegroundUtils 风格）
GetForegroundWindow = user32.GetForegroundWindow
GetForegroundWindow.argtypes = []
GetForegroundWindow.restype = wt.HWND

SetForegroundWindow = user32.SetForegroundWindow
SetForegroundWindow.argtypes = [wt.HWND]
SetForegroundWindow.restype = wt.BOOL

IsIconic = user32.IsIconic
IsIconic.argtypes = [wt.HWND]
IsIconic.restype = wt.BOOL

ShowWindow = user32.ShowWindow
ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
ShowWindow.restype = wt.BOOL

GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
GetWindowThreadProcessId.restype = wt.DWORD

AttachThreadInput = user32.AttachThreadInput
AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
AttachThreadInput.restype = wt.BOOL

BringWindowToTop = user32.BringWindowToTop
BringWindowToTop.argtypes = [wt.HWND]
BringWindowToTop.restype = wt.BOOL

SetActiveWindow = user32.SetActiveWindow
SetActiveWindow.argtypes = [wt.HWND]
SetActiveWindow.restype = wt.HWND

SetFocus = user32.SetFocus
SetFocus.argtypes = [wt.HWND]
SetFocus.restype = wt.HWND

# SwitchToThisWindow 是 Windows 为用户触发的窗口切换保留的兼容 API；
# 对部分 Unity 窗口比单独调用 SetForegroundWindow 更可靠。
SwitchToThisWindow = user32.SwitchToThisWindow
SwitchToThisWindow.argtypes = [wt.HWND, wt.BOOL]
SwitchToThisWindow.restype = None

# 全局热键（不依赖窗口焦点，游戏在前台也能急停）
RegisterHotKey = user32.RegisterHotKey
RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
RegisterHotKey.restype = wt.BOOL

UnregisterHotKey = user32.UnregisterHotKey
UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
UnregisterHotKey.restype = wt.BOOL


def _simulate_alt_press():
    """模拟一次 Alt 键按下+抬起。

    Windows 前台锁定（ForegroundLockTimeout）会拒绝非前台进程的
    SetForegroundWindow；模拟键盘输入向系统表明"用户正在操作"，
    从而解除锁定，让后台线程能真正抢到前台。
    """
    try:
        keybd_event = user32.keybd_event
        keybd_event(VK_MENU, 0, 0, 0)
        time.sleep(0.01)
        keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        pass


def _attach_thread_set_foreground(hwnd: int):
    """AttachThreadInput 技巧：把当前线程附加到前台线程再 SetForegroundWindow。

    前台锁定时 SetForegroundWindow 通常直接失败；与前台线程共享输入队列后
    该调用被允许，是 MAA 官方同样采用的破解手段。
    """
    try:
        cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        fg = GetForegroundWindow()
        if not fg:
            SetForegroundWindow(hwnd)
            return
        fg_tid = GetWindowThreadProcessId(fg, None)
        if cur_tid == fg_tid:
            SetForegroundWindow(hwnd)
            return
        AttachThreadInput(cur_tid, fg_tid, True)
        try:
            SetForegroundWindow(hwnd)
            BringWindowToTop(hwnd)
        finally:
            AttachThreadInput(cur_tid, fg_tid, False)
    except Exception:
        SetForegroundWindow(hwnd)


def activate_window(hwnd: int, aggressive: bool = False) -> bool:
    """将目标窗口置前并获取输入焦点（MAA 风格多策略激活）。

    SetForegroundWindow 的返回值受 Windows 前台锁限制，不能单次判定。
    这里将当前线程同时附加到前台窗口和目标窗口的输入队列，再执行恢复、
    临时置顶、活动窗口、焦点、前台切换，并轮询结果。
    """
    if not hwnd or not IsWindow(hwnd):
        return False
    if GetForegroundWindow() == hwnd:
        PostMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
        return True
    cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    fg = GetForegroundWindow()
    fg_tid = GetWindowThreadProcessId(fg, None) if fg else 0
    target_tid = GetWindowThreadProcessId(hwnd, None)
    attached = []
    try:
        if IsIconic(hwnd):
            ShowWindow(hwnd, SW_RESTORE)
        ShowWindow(hwnd, SW_SHOW)
        if aggressive:
            _simulate_alt_press()
        # 与前台和目标窗口共享输入队列，使 SetFocus/SetActiveWindow 可跨线程生效。
        for tid in (fg_tid, target_tid):
            if tid and tid != cur_tid and tid not in attached:
                if AttachThreadInput(cur_tid, tid, True):
                    attached.append(tid)
        for _ in range(3):
            SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            BringWindowToTop(hwnd)
            SwitchToThisWindow(hwnd, True)
            SetForegroundWindow(hwnd)
            SetActiveWindow(hwnd)
            SetFocus(hwnd)
            PostMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
            SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE)
            for _ in range(5):
                if GetForegroundWindow() == hwnd:
                    return True
                time.sleep(0.05)
            if aggressive:
                _simulate_alt_press()
        return GetForegroundWindow() == hwnd
    except Exception:
        return False
    finally:
        for tid in reversed(attached):
            AttachThreadInput(cur_tid, tid, False)


def MAKELPARAM(x: int, y: int) -> int:
    """组合 x,y 客户区坐标到 lParam（低 16 位 = x, 高 16 位 = y）"""
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def MAKEWPARAM(lo: int, hi: int) -> int:
    """组合 lo,hi 到 wParam（低 16 位 = lo, 高 16 位 = hi）"""
    return ((hi & 0xFFFF) << 16) | (lo & 0xFFFF)


def _err():
    return ctypes.get_last_error()


def enum_windows(min_title_len: int = 1) -> list:
    """枚举所有可见顶层窗口（对齐官方 MAA 的窗口选择方式）

    Returns:
        List[dict]: [{hwnd, title, class_name, pid, process_name}]
    """
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    GetClassNameW = user32.GetClassNameW
    IsWindowVisible = user32.IsWindowVisible
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId

    # 进程名（用于提示，不打开进程）
    query_full = True
    if query_full:
        try:
            import psutil
            _has_psutil = True
        except ImportError:
            _has_psutil = False
    else:
        _has_psutil = False

    out = []

    def cb(hwnd, lParam):
        if not IsWindowVisible(hwnd):
            return True
        nLen = GetWindowTextLengthW(hwnd)
        if nLen < min_title_len:
            return True
        buf = ctypes.create_unicode_buffer(nLen + 1)
        GetWindowTextW(hwnd, buf, nLen + 1)
        title = buf.value

        cls_buf = ctypes.create_unicode_buffer(256)
        GetClassNameW(hwnd, cls_buf, 256)
        cls = cls_buf.value

        pid = wt.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = ""
        if _has_psutil:
            try:
                proc = psutil.Process(pid.value).name()
            except Exception:
                proc = ""

        out.append({
            "hwnd": hwnd,
            "title": title,
            "class_name": cls,
            "pid": pid.value,
            "process_name": proc,
        })
        return True

    EnumWindows(EnumWindowsProc(cb), 0)
    return out


def find_window(title_substring: str, timeout: float = 5.0) -> Optional[int]:
    """根据窗口标题子串查找顶层窗口句柄（EnumWindows）"""
    for win in enum_windows(min_title_len=1):
        if title_substring.lower() in win["title"].lower():
            return win["hwnd"]
    return None


class WindowInput:
    """对指定窗口发送 sendmsg 鼠标/键盘消息

    支持两种鼠标模式：
    - plain: 直接 SendMessage，坐标为客户区坐标
    - windowpos: 移动窗口使目标客户区坐标对准真实光标，再发消息（MAA with_window_pos）
    """

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self._window_pos_saved = False
        self._saved_rect = None
        self._last_activate = 0.0   # 前台激活冷却时间戳

    def is_alive(self) -> bool:
        return bool(IsWindow(self.hwnd))

    # ---------- windowpos 支持（对齐 MAA MessageInput） ----------
    def get_cursor_pos(self):
        """当前光标屏幕坐标 (x, y)"""
        pt = wt.POINT()
        if GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
        return 0, 0

    def get_window_rect(self):
        """窗口整窗矩形 (left, top, right, bottom)"""
        rc = wt.RECT()
        if GetWindowRect(self.hwnd, ctypes.byref(rc)):
            return rc.left, rc.top, rc.right, rc.bottom
        return None

    def client_to_screen(self, x: int, y: int):
        """客户区坐标 → 屏幕坐标"""
        pt = wt.POINT(x, y)
        if ClientToScreen(self.hwnd, ctypes.byref(pt)):
            return pt.x, pt.y
        return x, y

    def move_window_to_align_cursor(self, x: int, y: int) -> bool:
        """移动窗口，使客户区 (x,y) 点对准当前真实光标。

        对应 MAA `move_window_to_align_cursor`：
        - 获取光标屏幕坐标 cursor_pos
        - 获取客户区原点屏幕坐标 client_origin
        - 获取窗口整窗矩形 rect
        - 边框偏移 border = client_origin - rect.left/top
        - new_left = cursor.x - x - border_x, new_top = cursor.y - y - border_y
        - SetWindowPos（不改变大小/Z序/激活，异步）
        """
        if not self.hwnd or not IsWindow(self.hwnd):
            return False
        cursor_pos = self.get_cursor_pos()
        if cursor_pos == (0, 0):
            return False

        client_origin = self.client_to_screen(0, 0)
        rect = self.get_window_rect()
        if rect is None:
            return False
        left, top, _, _ = rect

        border_x = client_origin[0] - left
        border_y = client_origin[1] - top

        new_left = cursor_pos[0] - x - border_x
        new_top = cursor_pos[1] - y - border_y

        # 必须同步完成移动后再投递鼠标消息；异步移动会让消息仍按旧窗口位置
        # 解释，表现为点击/滑动无效。
        return bool(SetWindowPos(
            self.hwnd, None, new_left, new_top, 0, 0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))

    def save_window_pos(self):
        """保存窗口当前位置（供操作后恢复）。"""
        rect = self.get_window_rect()
        if rect is not None:
            self._saved_rect = rect
            self._saved_iconic = bool(IsIconic(self.hwnd))
            self._window_pos_saved = True

    def restore_window_pos(self):
        """恢复窗口到保存的位置"""
        if not self._window_pos_saved or self._saved_rect is None:
            return
        left, top, _, _ = self._saved_rect
        SetWindowPos(self.hwnd, None, left, top, 0, 0,
                     SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        self._window_pos_saved = False

    def _restore_after_operation(self):
        """单次 WindowPos 操作结束后恢复窗口原位置。"""
        self.restore_window_pos()

    def click_with_windowpos(self, x: int, y: int) -> bool:
        """windowpos 模式点击客户区 (x,y)：
        1. 保存窗口位置
        2. 移动窗口使 (x,y) 对准光标
        3. 发送 WM_LBUTTONDOWN/UP（坐标仍为客户区坐标）
        4. 恢复窗口位置
        """
        self.save_window_pos()
        if not self.move_window_to_align_cursor(x, y):
            self.restore_window_pos()
            return False
        time.sleep(0.01)
        lParam = MAKELPARAM(x, y)
        SendMessageW(self.hwnd, WM_MOUSEMOVE, 0, lParam)
        time.sleep(0.01)
        SendMessageW(self.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lParam)
        time.sleep(0.05)
        SendMessageW(self.hwnd, WM_LBUTTONUP, 0, lParam)
        time.sleep(0.01)
        self._restore_after_operation()
        return True

    def send_mouse_move(self, x: int, y: int) -> bool:
        return bool(SendMessageW(self.hwnd, WM_MOUSEMOVE, 0, MAKELPARAM(x, y)))

    def send_click(self, x: int, y: int) -> bool:
        """单击：先 WM_MOUSEMOVE，再 WM_LBUTTONDOWN/UP（间隔 50ms，与 MAA 一致）"""
        lParam = MAKELPARAM(x, y)
        SendMessageW(self.hwnd, WM_MOUSEMOVE, 0, lParam)
        time.sleep(0.01)
        SendMessageW(self.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lParam)
        time.sleep(0.05)
        SendMessageW(self.hwnd, WM_LBUTTONUP, 0, lParam)
        return True

    def send_wheel(self, x: int, y: int, delta: int) -> bool:
        """向目标窗口发送滚轮消息（plain 模式）。"""
        pt = wt.POINT(x, y)
        ClientToScreen(self.hwnd, ctypes.byref(pt))
        lParam = MAKELPARAM(pt.x, pt.y)
        wParam = MAKEWPARAM(0, delta & 0xFFFF)
        result = SendMessageW(self.hwnd, WM_MOUSEWHEEL, wParam, lParam)
        time.sleep(0.05)
        return True

    def send_wheel_with_windowpos(self, x: int, y: int, delta: int) -> bool:
        """MAA SendMessageWithWindowPos 版本的滚轮。

        不尝试移动真实鼠标。临时移动目标窗口，使客户区 (x,y) 对准当前
        光标位置，再用光标屏幕坐标构造 WM_MOUSEWHEEL 的 lParam，最后恢复
        窗口原位置。这与 MAA 的 PC Win32 控制单元一致。
        """
        cursor_x, cursor_y = self.get_cursor_pos()
        if cursor_x == 0 and cursor_y == 0:
            return False
        self.save_window_pos()
        if not self.move_window_to_align_cursor(x, y):
            self._restore_after_operation()
            return False
        try:
            lParam = MAKELPARAM(cursor_x, cursor_y)
            wParam = MAKEWPARAM(0, delta & 0xFFFF)
            SendMessageW(self.hwnd, WM_MOUSEMOVE, 0, MAKELPARAM(x, y))
            SendMessageW(self.hwnd, WM_MOUSEWHEEL, wParam, lParam)
            time.sleep(0.08)
            return True
        finally:
            self._restore_after_operation()

    def send_drag(self, x1: int, y1: int, x2: int, y2: int,
                  hold_ms: int = 30, steps: int = 5) -> bool:
        """直接消息拖动（plain 模式）。"""
        p1 = MAKELPARAM(x1, y1)
        SendMessageW(self.hwnd, WM_MOUSEMOVE, 0, p1)
        time.sleep(0.01)
        SendMessageW(self.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, p1)
        time.sleep(hold_ms / 1000.0)
        for i in range(1, steps + 1):
            t = i / steps
            cx = int(x1 + (x2 - x1) * t)
            cy = int(y1 + (y2 - y1) * t)
            SendMessageW(self.hwnd, WM_MOUSEMOVE, MK_LBUTTON, MAKELPARAM(cx, cy))
            time.sleep(0.01)
        p2 = MAKELPARAM(x2, y2)
        SendMessageW(self.hwnd, WM_LBUTTONUP, 0, p2)
        return True

    def drag_with_windowpos(self, x1: int, y1: int, x2: int, y2: int,
                             hold_ms: int = 50, steps: int = 16) -> bool:
        """MAA WindowPos 方式执行色板滑块拖动。

        临时移动窗口让滑块起点对准真实光标，再发送完整的
        MOVE -> LBUTTONDOWN -> MOVE(with MK_LBUTTON) -> LBUTTONUP 序列。
        全程保持窗口位置不变，直到抬起后才恢复，避免拖动中坐标系跳变。
        """
        self.save_window_pos()
        if not self.move_window_to_align_cursor(x1, y1):
            self._restore_after_operation()
            return False
        try:
            p1 = MAKELPARAM(x1, y1)
            SendMessageW(self.hwnd, WM_MOUSEMOVE, 0, p1)
            time.sleep(0.02)
            SendMessageW(self.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, p1)
            time.sleep(hold_ms / 1000.0)
            for i in range(1, steps + 1):
                t = i / steps
                cx = round(x1 + (x2 - x1) * t)
                cy = round(y1 + (y2 - y1) * t)
                # MAA WithWindowPos 的关键：每一个拖动点都重新对齐窗口，
                # 保证真实鼠标持续处于当前客户区点位；只对齐起点会退化为点击。
                if not self.move_window_to_align_cursor(cx, cy):
                    SendMessageW(self.hwnd, WM_LBUTTONUP, 0, MAKELPARAM(cx, cy))
                    return False
                SendMessageW(self.hwnd, WM_MOUSEMOVE, MK_LBUTTON, MAKELPARAM(cx, cy))
                time.sleep(0.018)
            p2 = MAKELPARAM(x2, y2)
            SendMessageW(self.hwnd, WM_LBUTTONUP, 0, p2)
            time.sleep(0.10)
            return True
        finally:
            self._restore_after_operation()

    def send_key(self, vk: int, hold: float = 0.05):
        """按键（按下 → hold → 抬起）"""
        SendMessageW(self.hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
        lParam = 1 | ((ctypes.c_uint(vk).value & 0xFF) << 16)
        SendMessageW(self.hwnd, WM_KEYDOWN, vk, lParam)
        time.sleep(hold)
        SendMessageW(self.hwnd, WM_KEYUP, vk, lParam | (1 << 30) | (1 << 31))

    # =====================================================================
    # SendInput 硬件级输入（Unity 游戏必须用这个，SendMessage 无效）
    # =====================================================================
    def _make_input(self, input_type: int, **fields) -> ctypes.Structure:
        """构造 SendInput 的 INPUT 结构"""
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
        class DUMMY(ctypes.Structure):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]
        class INPUT(ctypes.Union):
            _fields_ = [("type", ctypes.c_ulong), ("_input", DUMMY)]

        inp = INPUT()
        inp.type = input_type
        for k, v in fields.items():
            if input_type == INPUT_MOUSE:
                setattr(inp._input.mi, k, v)
            else:
                setattr(inp._input.ki, k, v)
        return inp

    def _send_input(self, *inputs) -> bool:
        """调用 SendInput，返回是否成功"""
        SendInput = user32.SendInput
        SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
        SendInput.restype = ctypes.c_uint
        arr = (ctypes.c_void_p * len(inputs))()
        for i, inp in enumerate(inputs):
            arr[i] = ctypes.cast(ctypes.pointer(inp), ctypes.c_void_p)
        # SendInput 需要 INPUT 数组，用更标准的方式
        return self._send_input_raw(inputs)

    def _send_input_raw(self, inputs) -> bool:
        SendInput = user32.SendInput
        INPUT_SIZE = 40  # 64 位下 INPUT 结构大小
        # 用字节缓冲区构造 INPUT 数组
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
        class INPUTUNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]
        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("u", INPUTUNION)]

        arr = (INPUT * len(inputs))()
        for i, inp in enumerate(inputs):
            arr[i].type = inp.type
            if inp.type == INPUT_MOUSE:
                arr[i].u.mi.dx = inp._input.mi.dx
                arr[i].u.mi.dy = inp._input.mi.dy
                arr[i].u.mi.mouseData = inp._input.mi.mouseData
                arr[i].u.mi.dwFlags = inp._input.mi.dwFlags
                arr[i].u.mi.time = 0
            else:
                arr[i].u.ki.wVk = inp._input.ki.wVk
                arr[i].u.ki.wScan = inp._input.ki.wScan
                arr[i].u.ki.dwFlags = inp._input.ki.dwFlags
        try:
            SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
            SendInput.restype = ctypes.c_uint
            n = SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
            return n == len(inputs)
        except Exception:
            return False

    def activate_foreground(self, force: bool = False,
                            aggressive: bool = False) -> bool:
        """确保窗口在前台（带冷却，避免频繁 SetForegroundWindow 被系统抑制）。

        Unity 只在窗口激活/前台时接收鼠标输入。窗口已在前台时零开销返回
        True；非前台时每 0.4s 才尝试抢一次前台。aggressive=True 时额外
        模拟 Alt 键 + AttachThreadInput，用于绘画开始时的"一次性强激活"。
        """
        now = time.time()
        if force or aggressive or (now - self._last_activate >= 0.4):
            self._last_activate = now
            if GetForegroundWindow() != self.hwnd:
                return activate_window(self.hwnd, aggressive=aggressive)
        return GetForegroundWindow() == self.hwnd

    def is_foreground(self) -> bool:
        """当前前台窗口是否就是目标窗口（SendInput 只发给前台窗口）"""
        return bool(self.hwnd) and GetForegroundWindow() == self.hwnd

    def _set_cursor_client_pos(self, client_x: int, client_y: int) -> bool:
        """用 SendInput 绝对坐标把真实光标移到目标客户区坐标。

        当前多显示器/UIPI 环境下 SetCursorPos 会返回失败，导致后续点击、
        拖动都在旧鼠标位置发生。使用 MOUSEEVENTF_ABSOLUTE|VIRTUALDESK
        覆盖整个虚拟桌面，和 Unity 所需的硬件输入链路保持一致。
        """
        pt = wt.POINT(client_x, client_y)
        if not ClientToScreen(self.hwnd, ctypes.byref(pt)):
            return False
        GetSystemMetrics = user32.GetSystemMetrics
        GetSystemMetrics.argtypes = [ctypes.c_int]
        GetSystemMetrics.restype = ctypes.c_int
        left = GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if width <= 1 or height <= 1:
            return False
        # SendInput 绝对坐标使用 [0, 65535] 映射整个虚拟桌面。
        dx = round((pt.x - left) * 65535 / (width - 1))
        dy = round((pt.y - top) * 65535 / (height - 1))
        move = self._make_input(
            INPUT_MOUSE, dx=max(0, min(65535, dx)), dy=max(0, min(65535, dy)),
            mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        )
        if not self._send_input_raw([move]):
            return False
        # SendInput 的返回值只说明事件已进入系统队列，不能保证实际光标已移动。
        # 未移动时绝不能继续发送 LEFTDOWN/LEFTUP，否则会在旧位置反复误点。
        time.sleep(0.01)
        actual = wt.POINT()
        if not user32.GetCursorPos(ctypes.byref(actual)):
            return False
        return abs(actual.x - pt.x) <= 2 and abs(actual.y - pt.y) <= 2

    def drag_sendinput(self, x1: int, y1: int, x2: int, y2: int,
                       hold_ms: int = 30, steps: int = 12) -> bool:
        """用硬件 SendInput 执行按住拖动（Unity 色板滚动专用）。

        先将真实光标定位到起点，再发送 LEFTDOWN；拖动过程中以真实光标位置
        逐步移动，最后发送 LEFTUP。不能用 WM_MOUSEMOVE/LBUTTON 消息模拟，
        Unity 不会把它识别为可拖拽的色板手势。
        """
        if not self._set_cursor_client_pos(x1, y1):
            return False
        time.sleep(0.02)
        down = self._make_input(INPUT_MOUSE, dx=0, dy=0, mouseData=0,
                                dwFlags=MOUSEEVENTF_LEFTDOWN)
        if not self._send_input_raw([down]):
            return False
        time.sleep(hold_ms / 1000.0)
        for i in range(1, steps + 1):
            t = i / steps
            x = round(x1 + (x2 - x1) * t)
            y = round(y1 + (y2 - y1) * t)
            if not self._set_cursor_client_pos(x, y):
                break
            time.sleep(0.015)
        up = self._make_input(INPUT_MOUSE, dx=0, dy=0, mouseData=0,
                              dwFlags=MOUSEEVENTF_LEFTUP)
        ok = self._send_input_raw([up])
        time.sleep(0.12)
        return ok

    def click_sendinput(self, client_x: int, client_y: int,
                        absolute: bool = False) -> bool:
        """用 SendInput 硬件级点击。

        做法（多显示器/DPI 精确）：
        1. SetCursorPos 直接设置光标到目标屏幕坐标（精确，不依赖归一化）
        2. SendInput 发 LEFT_DOWN / LEFT_UP 触发硬件级点击事件

        Unity 游戏必须用硬件输入才能识别。

        注意：这里不再每次点击前抢前台——绘画期间由主流程保证游戏窗口
        持续前台（工具窗口已最小化让位），避免反复 SetForegroundWindow
        导致焦点在窗口间乱跳、UI 无法操作。
        """
        # 1. 用 SendInput 绝对坐标精确定位真实光标。
        if absolute:
            # absolute 参数保留兼容：调用方传屏幕坐标时换算为客户区坐标。
            pt = wt.POINT(client_x, client_y)
            user32.ScreenToClient(self.hwnd, ctypes.byref(pt))
            client_x, client_y = pt.x, pt.y
        if not self._set_cursor_client_pos(client_x, client_y):
            return False
        time.sleep(0.01)

        # 2. SendInput 硬件级按下/抬起（无 MOVE，光标已在目标位置）
        down = self._make_input(INPUT_MOUSE, dx=0, dy=0, mouseData=0,
                                dwFlags=MOUSEEVENTF_LEFTDOWN)
        up = self._make_input(INPUT_MOUSE, dx=0, dy=0, mouseData=0,
                              dwFlags=MOUSEEVENTF_LEFTUP)
        self._send_input_raw([down])
        time.sleep(0.03)
        self._send_input_raw([up])
        time.sleep(0.01)
        return True

    def activate(self):
        SendMessageW(self.hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
        time.sleep(0.01)