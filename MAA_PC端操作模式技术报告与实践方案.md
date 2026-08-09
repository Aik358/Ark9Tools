# MAA PC 端操作模式技术报告与实践方案

> 基于 MAA（MaaAssistantArknights / MaaFramework）PC 控制单元源码的深度剖析，
> 围绕 **WGC 截图**、**sendmsg-windowpos 鼠标输入**、**sendmsg 键盘输入** 三大核心技术点，
> 为自研游戏辅助工具提供原理说明、架构设计与可落地的实现方案。

- 分析对象版本：MaaWin32ControlUnit v5.9.2（对应 `MAA.deps.json` 中 MAA 6.16.5，连接配置 `Config: PC`）
- 证据来源：本地发布版 DLL 导出符号、`debug/asst.log` TRC 级日志、`config/gui.json` 配置，
  以及 MAA / MaaFramework 开源仓库同源实现源码（`FramePoolScreencap.cpp`、`MessageInput.cpp`、`InputUtils.h`、`PseudoMinimizeHelper.cpp`、`Win32ControlUnitMgr` 等）。

---

## 实践修订（Ark9Tools，2026-08-09）

本节记录 Ark9Tools 在 Unity 游戏像素画面板上的实际验证结果。它优先于本文中仅用于说明原理的简化代码示例。

### 已验证的控制组合

| 层面 | Ark9Tools 当前策略 | 实测结论 |
|---|---|---|
| 进程权限 | 启动脚本通过 UAC 请求管理员权限 | 游戏以管理员权限运行时，低完整性工具会收到 `ERROR_ACCESS_DENIED (5)`，既不能移动游戏窗口，也不能可靠注入输入。权限必须同级。 |
| 窗口前台 | 开始任务前尝试自动前置；失败时不创建绘画任务，要求用户先手动点击游戏窗口 | 避免前几笔在游戏未获得焦点前丢失，或把输入发给工具窗口。 |
| 截图 | WGC 优先；色板校准使用桌面 `ImageGrab` 的当前帧 | WGC/DXGI 可能拿到旧帧或受 HDR 色调映射影响；颜色校准必须使用与当前可见面板一致的实时画面。 |
| 鼠标点击 | `SendMessageWithWindowPos` 风格窗口消息路径 | 不依赖全局鼠标光标成功移动；目标窗口临时对齐后发送同步鼠标消息，操作结束恢复窗口位置。 |
| 色板滑动 | 按住色板内容区连续上下拖动 | 色板是可拖拽内容面板，不应建模成固定的滚轮逐行列表，也不应假设只有两个页面。 |
| 色板选色 | 拖动后重读实际色块 RGB；只点击当前画面已确认的色块 | 避免内部滚动行号与游戏真实视图不同步时选择黄色、橙色等错误颜色。 |

### 色板与颜色校准的强制流程

Ark9Tools 的 PixelPainter 在点击画布前必须完成下列步骤：

1. 从当前客户区桌面截图检测色板网格，刷新 `palette_left`、首行 Y、行列间距与可见行数。
2. 将色板内容拖回顶部边界，再连续向上拖动采样全部可见物理色块。
3. 将逻辑色表 `X01~X38` 与实测 RGB 逐色匹配；逻辑色允许共享同一个游戏物理色块，因为原始色表存在近重复色。
4. 每次选择颜色前，在当前截图中重新定位该已校准物理 RGB；找不到目标、拖动无变化或色差超过阈值时立即停止，不允许带着猜测继续绘制。

这套闭环比“逻辑颜色索引除以 4 后直接点色板”更慢，但能避免 Unity 面板滚动、HDR 显示和浅色近似匹配带来的系统性错色。

### 模块化应用架构

Ark9Tools 是宿主应用，PixelPainter 是其中一个模块，而不是整个应用：

```text
工具中心
├─ MAA-PixelPainter：图像处理 → 游戏校准 → 执行绘画
├─ 连接中心：共享目标窗口与校准上下文
├─ 截图诊断：只读检查截图、权限与色板参数
└─ 任务编排：后续扩展模块接口
```

各模块共享窗口句柄和校准上下文，但任务执行、日志和安全检查由模块自行管理。PixelPainter 完成三步工作流后返回工具中心，不关闭宿主应用。

### 操作建议

1. 用 `run.bat` 启动 Ark9Tools，并在 UAC 提示中确认管理员权限。
2. 先点击游戏窗口，使其进入前台，再进入 `MAA-PixelPainter` 的“游戏校准”。
3. 完成窗口、画布与色板校准后再点击“开始绘画”。
4. 绘画前的“正在检测色板网格 / 正在校准色板颜色”属于正常安全阶段；失败时应排查权限、窗口前台、色板是否完整可见，而不是继续强行绘制。

---

## 目录

1. [技术原理剖析](#一技术原理剖析)
   - 1.1 WGC（Windows Graphics Capture）截图 API 原理
   - 1.2 sendmsg-windowpos 鼠标消息机制
   - 1.3 sendmsg 键盘消息注入机制
2. [实现架构设计](#二实现架构设计)
3. [核心代码实现方案](#三核心代码实现方案)
4. [稳定性与兼容性方案](#四稳定性与兼容性方案)
5. [参考资料](#五参考资料)

---

## 一、技术原理剖析

### 1.1 WGC（Windows Graphics Capture）截图 API 原理

#### 1.1.1 什么是 WGC

WGC（Windows.Graphics.Capture）是微软从 **Windows 10 1803（17134）** 开始引入的现代屏幕捕获 API。与传统的 `BitBlt`（GDI 截屏）、`PrintWindow` 相比，WGC 直接与系统的图形合成引擎 **DWM（Desktop Window Manager）** 协作，从**最终合成后的画面**中截取指定窗口或屏幕，因此具备以下核心优势：

| 特性 | 说明 |
|---|---|
| 窗口遮挡无关 | 即使目标窗口被其他窗口完全遮挡，仍能捕获其真实内容 |
| 兼容独占全屏 | 支持部分独占全屏 / 无边框全屏游戏（视驱动而定） |
| 高效率 | 通过 GPU 纹理复制（`CopyResource`），不经过 GDI 内存往返 |
| 后台可用 | 窗口不必是前台窗口即可捕获（只要不是最小化） |

#### 1.1.2 核心对象模型

WGC 的截图流程由 4 个核心对象协作完成：

```
┌────────────────────────────────────────────────────────────┐
│                    WGC 对象协作模型                          │
│                                                            │
│  GraphicsCaptureItem     ← 捕获目标（窗口 / 显示器）         │
│        │                                                    │
│        ▼                                                    │
│  Direct3D11CaptureFramePool  ← 帧缓冲池（GPU 纹理池）       │
│        │                                                    │
│        ▼                                                    │
│  GraphicsCaptureSession  ← 捕获会话（StartCapture 启动）     │
│        │                                                    │
│        ▼                                                    │
│  Direct3D11CaptureFrame  ← 单帧数据（frame.Surface()）      │
└────────────────────────────────────────────────────────────┘
```

| 对象 | 作用 | 关键成员/方法 |
|---|---|---|
| `GraphicsCaptureItem` | 表示一个可捕获的窗口或显示器 | `Size`（捕获尺寸）、`Created` 事件 |
| `Direct3D11CaptureFramePool` | 管理 GPU 帧缓冲，持续接收合成帧 | `Create()`、`CreateCaptureSession()`、`TryGetNextFrame()`、`Close()` |
| `GraphicsCaptureSession` | 控制捕获过程的启停与行为 | `StartCapture()`、`IsBorderRequired`、`IsCursorCaptureEnabled`、`IncludeSecondaryWindows` |
| `Direct3D11CaptureFrame` | 单个捕获帧 | `Surface()`（返回 `IDirect3DSurface`） |

#### 1.1.3 关键 Windows API 与互操作接口

WGC 是 WinRT API，要在纯 Win32/C++ 程序中使用，需要借助以下互操作层：

**（1）创建设备与交换链**

```cpp
// D3D11 设备与交换链（FramePoolScreencap 的 init 第一步）
DXGI_SWAP_CHAIN_DESC desc = {};
desc.BufferCount = 1;
desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
desc.OutputWindow = hwnd;
desc.SampleDesc.Count = 1;
desc.Windowed = TRUE;

D3D11CreateDeviceAndSwapChain(
    nullptr,                 // pAdapter：nullptr 表示使用默认适配器
    D3D_DRIVER_TYPE_HARDWARE,
    nullptr,
    0,
    nullptr, 0,
    D3D11_SDK_VERSION,
    &desc,                   // pSwapChainDesc
    swapChain.put(),         // IDXGISwapChain
    device.put(),            // ID3D11Device
    nullptr,
    context.put());          // ID3D11DeviceContext
```

**（2）通过 HWND 创建捕获项（核心互操作调用）**

`GraphicsCaptureItem` 不能直接从 HWND 构造，必须通过 `IGraphicsCaptureItemInterop` 接口：

```cpp
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.capture.h>

auto activation_factory =
    winrt::get_activation_factory<winrt::Windows::Graphics::Capture::GraphicsCaptureItem>();
auto interop_factory = activation_factory.try_as<IGraphicsCaptureItemInterop>();

HRESULT hr = interop_factory->CreateForWindow(
    hwnd,                                                // 目标窗口句柄
    winrt::guid_of<winrt::Windows::Graphics::Capture::GraphicsCaptureItem>(),  // riid
    winrt::put_abi(cap_item));                           // 输出 GraphicsCaptureItem*
```

**（3）DXGI 设备 → WinRT IDirect3DDevice**

`Direct3D11CaptureFramePool::Create` 需要一个 WinRT 的 `IDirect3DDevice`，需要将 `ID3D11Device` 通过 `IDXGIDevice` 包装：

```cpp
#include <windows.graphics.directx.direct3d11.interop.h>

auto dxgi_device = d3d_device.try_as<IDXGIDevice>();
winrt::com_ptr<IInspectable> inspectable = nullptr;
CreateDirect3D11DeviceFromDXGIDevice(dxgi_device.get(), inspectable.put());

auto d3d_device_interop = inspectable.try_as<
    winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice>();
```

**（4）创建帧池与会话**

```cpp
cap_frame_pool_ = winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool::Create(
    d3d_device_interop,                                        // WinRT D3D 设备
    winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized, // BGRA
    1,                                                         // BufferCount
    cap_item_.Size());                                         // 捕获尺寸

cap_session_ = cap_frame_pool_.CreateCaptureSession(cap_item_);
cap_session_.StartCapture();                                   // 开始持续捕获
```

> **BufferCount 为何是 1**：MAA 采用"按需取帧"策略——每次 `screencap()` 时先清空池中残留帧，再 `TryGetNextFrame()` 等待新帧，1 个缓冲足够且内存占用最小。

**（5）从帧中提取 CPU 可读像素**

```cpp
// 帧 → IDirect3DSurface → ID3D11Texture2D
auto surface = frame.Surface();
auto access = surface.try_as<IDirect3DDxgiInterfaceAccess>();
winrt::com_ptr<ID3D11Texture2D> texture = nullptr;
access->GetInterface(winrt::guid_of<ID3D11Texture2D>(), texture.put_void());

// GPU 纹理 → CPU 可读的 Staging 纹理
d3d_context->CopyResource(readable_texture.get(), texture.get());

// Map 获得 CPU 访问指针
D3D11_MAPPED_SUBRESOURCE mapped = {};
d3d_context->Map(readable_texture.get(), 0, D3D11_MAP_READ, 0, &mapped);
// mapped.pData 指向 BGRA 像素数据，RowPitch 为每行字节数
d3d_context->Unmap(readable_texture.get(), 0);
```

**（6）DWM 实际边框计算（坐标对齐的关键）**

WGC 捕获的是**整窗（含边框与阴影）**的画面，而图像识别与点击用的是**客户区坐标**，因此必须算出两者偏移：

```cpp
RECT client_rect = {};
GetClientRect(hwnd, &client_rect);            // 客户区大小
POINT client_top_left = { 0, 0 };
ClientToScreen(hwnd, &client_top_left);       // 客户区左上角屏幕坐标

RECT frame_rect = {};
// DWM 实际可视边框（排除不可见的阴影区域）
DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, &frame_rect, sizeof(frame_rect));

int border_left = client_top_left.x - frame_rect.left;  // 捕获画面中左侧边框宽度
int border_top  = client_top_left.y - frame_rect.top;   // 捕获画面中顶部边框宽度
```

**（7）可选会话行为开关（兼容性增强）**

```cpp
// 关闭捕获时高亮边框（Win10 2004+），需先请求 Borderless 权限
GraphicsCaptureAccess::RequestAccessAsync(GraphicsCaptureAccessKind::Borderless);
cap_session_.IsBorderRequired(false);

// 关闭捕获画面中的鼠标指针
cap_session_.IsCursorCaptureEnabled(false);

// 包含从属窗口（弹窗、工具提示等）
cap_session_.IncludeSecondaryWindows(true);
```

#### 1.1.4 WGC 帧获取的完整生命周期

MAA `FramePoolScreencap::screencap()` 的完整流程：

```
screencap()
 ├─ 校验 hwnd_
 ├─ 惰性 init()：创建 D3D 设备 → CreateForWindow → FramePool::Create → StartCapture
 ├─ check_and_handle_size_changed()：窗口尺寸变化则 uninit + init 重建
 ├─ 清空池中残留旧帧（TryGetNextFrame 循环 + Close）
 ├─ 2 秒超时循环：每 2ms TryGetNextFrame() 等待新帧
 │     └─ 超时 → 返回上次成功截图的缓存 cached_image_.clone()
 ├─ frame.Surface() → ID3D11Texture2D → CopyResource → Map
 ├─ 用 DWM 边框裁剪出客户区 ROI
 └─ bgra_to_bgr 转换（BGRA → BGR，匹配 OpenCV 的 CV_8UC3）
```

> **性能要点**：MAA 日志显示单帧 `screencap` 耗时约 **6~10ms**（D:\MAA\debug\asst.log，`Win32Controller::screencap | leave, 7 ms`），即约 100~160 FPS 的截图吞吐，完全满足图像识别循环需求。

---

### 1.2 sendmsg-windowpos 鼠标消息机制

#### 1.2.1 消息驱动的输入模型

MAA 的 PC 输入方案不注入硬件级事件（不走 `SendInput` / `mouse_event`），而是**直接向目标窗口过程投递窗口消息**。核心 API 是 `SendMessageW` / `PostMessageW`：

| API | 行为 | 适用 |
|---|---|---|
| `PostMessageW` | 异步，消息进目标线程消息队列后立即返回 | 不关心处理结果 |
| `SendMessageW` | 同步，直接调用目标窗口过程，等待其返回 | 保证时序、可靠性高 |

MAA 默认使用 **`SendMessageW`**（`config_.mode == Mode::SendMessage`），保证每个消息都被目标窗口过程实际处理后，才发送下一个消息，从而保证操作时序。

#### 1.2.2 鼠标消息结构体与参数含义

Win32 鼠标消息的 `wParam` / `lParam` 含义：

| 消息 | ID | wParam | lParam |
|---|---|---|---|
| `WM_MOUSEMOVE` | 0x0200 | 按键状态（`MK_LBUTTON` 等） | 客户区坐标 `MAKELPARAM(x, y)` |
| `WM_LBUTTONDOWN` | 0x0201 | `MK_LBUTTON` | 客户区坐标 |
| `WM_LBUTTONUP` | 0x0202 | `0` | 客户区坐标 |
| `WM_RBUTTONDOWN` | 0x0204 | `MK_RBUTTON` | 客户区坐标 |
| `WM_RBUTTONUP` | 0x0205 | `0` | 客户区坐标 |
| `WM_MBUTTONDOWN` | 0x0207 | `MK_MBUTTON` | 客户区坐标 |
| `WM_MBUTTONUP` | 0x0208 | `0` | 客户区坐标 |
| `WM_XBUTTONDOWN` | 0x020B | `MAKEWPARAM(MK_XBUTTON1, XBUTTON1)` | 客户区坐标 |
| `WM_XBUTTONUP` | 0x020C | `MAKEWPARAM(0, XBUTTON1)` | 客户区坐标 |
| `WM_MOUSEWHEEL` | 0x020A | `MAKEWPARAM(按键状态, delta)` | **屏幕坐标**（注意不是客户区） |

**`lParam` 坐标编码**：

```cpp
// 低 16 位 = X，高 16 位 = Y（客户区坐标）
LPARAM lParam = MAKELPARAM(x, y);
// 等价于：((LPARAM)y << 16) | (x & 0xFFFF)
```

**contact → 鼠标消息映射**（MAA `InputUtils.h` 的 `contact_to_mouse_down_message`）：

```cpp
case 0: msg = WM_LBUTTONDOWN; wParam = MK_LBUTTON; break;
case 1: msg = WM_RBUTTONDOWN; wParam = MK_RBUTTON; break;
case 2: msg = WM_MBUTTONDOWN; wParam = MK_MBUTTON; break;
case 3: msg = WM_XBUTTONDOWN; wParam = MAKEWPARAM(MK_XBUTTON1, XBUTTON1); break;
case 4: msg = WM_XBUTTONDOWN; wParam = MAKEWPARAM(MK_XBUTTON2, XBUTTON2); break;
```

> 映射前会调用 `GetMappedContact`：当系统开启了"交换左右键"（`GetSystemMetrics(SM_SWAPBUTTON)` 非 0）时，自动对调 contact 0/1，保证语义正确。

#### 1.2.3 坐标转换：make_mouse_lparam

传入的 `(x, y)` 始终是**主窗口客户区坐标**，若实际接收消息的目标窗口不是 `hwnd_`（例如焦点窗口是弹窗），需要转换：

```cpp
LPARAM make_mouse_lparam(HWND target, int x, int y) {
    if (target == hwnd_) {
        return MAKELPARAM(x, y);            // 直接使用客户区坐标
    }
    POINT pt = { x, y };
    ClientToScreen(hwnd_, &pt);             // 客户区 → 屏幕
    ScreenToClient(target, &pt);            // 屏幕 → 目标窗口客户区
    return MAKELPARAM(pt.x, pt.y);
}
```

#### 1.2.4 两种鼠标位置策略：with_cursor_pos vs with_window_pos

MAA 的 `prepare_mouse_position()` 是两种策略的分派点，也是 **"sendmsg-windowpos"** 命名来源：

```cpp
bool prepare_mouse_position(int x, int y) {
    if (config_.with_window_pos && window_pos_invalid_movement_.load()) {
        return false; // 上一次移动被判定非法，拒绝本次输入
    }
    if (config_.with_cursor_pos) {
        // 策略 A：移动真实光标到目标客户区对应的屏幕坐标
        POINT screen_pos = client_to_screen(x, y);
        SetCursorPos(screen_pos.x, screen_pos.y);
        return true;
    }
    if (config_.with_window_pos) {
        // 策略 B：光标不动，反向移动窗口使目标点对准光标
        start_window_tracking(x, y);
        return move_window_to_align_cursor(x, y);
    }
    return true; // 纯消息模式：不移动光标也不移动窗口
}
```

**两种策略对比**：

| 维度 | with_cursor_pos（移动光标） | with_window_pos（移动窗口） |
|---|---|---|
| 真实光标 | 被移动到目标点 | **保持不动** |
| 窗口位置 | 不动 | 反向移动，使客户区目标点对准光标 |
| 适用场景 | 普通窗口程序 | **锁定/捕获鼠标的 FPS、3D 游戏**（光标由游戏驱动，移动光标会导致坐标漂移） |
| 实现复杂度 | 低 | 高（需后台跟踪线程、进程挂起、窗口位置守护） |
| 副作用 | 扰动用户真实鼠标 | 窗口发生位移（结束后恢复） |

#### 1.2.5 with_window_pos 的核心算法：锚点反向移动窗口

核心思路：**保持光标不动，把窗口移动过去**，让窗口客户区的 `(x, y)` 点正好落在当前光标屏幕坐标上。

```cpp
bool move_window_to_align_cursor(int x, int y) {
    POINT cursor_pos;
    GetCursorPos(&cursor_pos);                      // 当前光标屏幕坐标

    POINT client_origin = { 0, 0 };
    ClientToScreen(hwnd_, &client_origin);          // 客户区原点（左上角）屏幕坐标

    RECT current_rect = {};
    GetWindowRect(hwnd_, &current_rect);            // 整窗（含边框）坐标

    // 锚点算法：客户区起点与整窗起点之间的差值（边框宽度）
    // 用差值而非直接换算，避免异步调用延迟导致的累积"拉扯"误差
    int border_x = client_origin.x - current_rect.left;
    int border_y = client_origin.y - current_rect.top;

    // 新窗口左上角 = 光标 - 目标客户区坐标 - 边框偏移
    int new_left = cursor_pos.x - x - border_x;
    int new_top  = cursor_pos.y - y - border_y;

    if (!is_window_move_allowed(new_left, new_top, current_rect, "align cursor")) {
        return false;
    }

    SetWindowPos(hwnd_, nullptr, new_left, new_top, 0, 0,
                 SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS);
    return true;
}
```

**窗口移动标志位**：

| 标志 | 含义 |
|---|---|
| `SWP_NOSIZE` | 不改变窗口大小 |
| `SWP_NOZORDER` | 不改变 Z 序（不抢顶层） |
| `SWP_NOACTIVATE` | 不激活窗口（保持后台状态） |
| `SWP_ASYNCWINDOWPOS` | 异步移动，不等待同步结果，避免卡住调用线程 |

#### 1.2.6 后台跟踪线程（with_window_pos 的增量位移处理）

对于锁定鼠标的游戏，用户（或游戏自身）会产生 Raw Input 鼠标位移。MAA 启动一个**后台跟踪线程**，监听真实鼠标位移，并将其换算为窗口移动量，保证"光标 → 客户区坐标"的对齐关系持续成立：

```
后台跟踪线程每帧：
 1. 获取累积的鼠标位移 dx/dy（pending_mouse_x_ / pending_mouse_y_ 原子变量）
 2. 计算新光标期望位置 mx = cursor.x + dx, my = cursor.y + dy
 3. 限制在虚拟屏幕范围（SM_XVIRTUALSCREEN 等）
 4. 计算新窗口位置 new_left = mx - tracking_x - border_x
 5. 挂起目标进程（suspend_target_process）避免应用观察到中间态
 6. SetWindowPos 移动窗口
 7. 恢复目标进程，SetCursorPos(mx, my) 释放累积位移
```

> **关键细节**：移动窗口前会 `suspend_target_process()` 挂起目标进程，移动完成后再 `resume_target_process()`，防止游戏在窗口移动的中间状态下做射线检测等计算导致抖动。

#### 1.2.7 窗口激活与输入保护

```cpp
// 发送 WM_ACTIVATE 让窗口"认为自己激活"（不实际抢占前台）
SendMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0);

// 可选的输入屏蔽：低级鼠标钩子，抑制用户真实鼠标干扰自动化
// check_and_block_input() / unblock_input()
```

---

### 1.3 sendmsg 键盘消息注入机制

#### 1.3.1 按键消息与 lParam 结构

MAA 的键盘输入同样通过 `SendMessageW` 注入 `WM_KEYDOWN` / `WM_KEYUP` / `WM_CHAR` 消息：

```cpp
bool key_down(int key) {
    HWND target = send_activate();                       // 先激活目标窗口
    LPARAM lParam = make_keydown_lparam(key);            // 构造按键 lParam
    return send_or_post_w(target, WM_KEYDOWN, (WPARAM)key, lParam);
}

bool key_up(int key) {
    HWND target = send_activate();
    LPARAM lParam = make_keyup_lparam(key);
    return send_or_post_w(target, WM_KEYUP, (WPARAM)key, lParam);
}
```

`wParam` 是**虚拟键码**（`VK_ESCAPE = 0x1B`、`VK_RETURN = 0x0D` 等）。

**`lParam` 位域结构**（`WM_KEYDOWN` / `WM_KEYUP` 消息）：

```
31 30      29     28        16    15        0
│  │       │       │          │     │
│  │       │       │          │     └─ 重复计数（repeat count）
│  │       │       │          └──────── 扫描码（OEM scan code）
│  │       │       └─────────────────── bit28：区分左右 Ctrl/Alt（扩展键标志）
│  │       └─────────────────────────── bit29：保留
│  └─────────────────────────────────── bit30：先前按键状态（0=按下前抬起，1=按下前已按下）
└────────────────────────────────────── bit31：转换状态（0=按下，1=抬起）
```

**MAA 的构造函数**（`InputUtils.h`）：

```cpp
// WM_KEYDOWN 的 lParam
LPARAM make_keydown_lparam(int key) {
    UINT sc = MapVirtualKeyW((UINT)key, MAPVK_VK_TO_VSC); // 虚拟键码 → 扫描码
    return 1 | ((LPARAM)sc << 16);        // 重复计数 1 + 扫描码
}

// WM_KEYUP 的 lParam（额外置位先前状态与转换状态位）
LPARAM make_keyup_lparam(int key) {
    UINT sc = MapVirtualKeyW((UINT)key, MAPVK_VK_TO_VSC);
    return 1 | ((LPARAM)sc << 16) | (1 << 30) | (1 << 31);
}
```

| API | 用途 |
|---|---|
| `MapVirtualKeyW(key, MAPVK_VK_TO_VSC)` | 将虚拟键码转换为扫描码（OEM 扫描码） |

#### 1.3.2 文本输入：WM_CHAR 逐字符注入

```cpp
bool input_text(const std::string& text) {
    HWND target = send_activate();
    for (auto ch : to_u16(text)) {                 // UTF-8 → UTF-16
        send_or_post_w(target, WM_CHAR, (WPARAM)ch, 0);
        std::this_thread::sleep_for(50ms);          // 每字符间隔，保证时序
    }
    return true;
}
```

#### 1.3.3 特性位协商：Down/Up 替代 Click

MAA 控制单元通过 `get_features()` 向上层声明能力：

```cpp
constexpr uint64_t UseMouseDownAndUpInsteadOfClick   = 1ULL << 1;
constexpr uint64_t UseKeyboardDownAndUpInsteadOfClick = 1ULL << 2;
```

上层 `Win32Controller` 收到该特性后，`click(p)` 不再调用 `unit_click`，而是：

```cpp
bool click(const Point& p) {
    unit_touch_down(0, p.x, p.y, 0);   // 按下
    sleep(50ms);                        // 保持 50ms（与 Minitouch 默认 ClickDelay 对齐）
    unit_touch_up(0);                   // 抬起
    sleep(50ms);                        // 为下一次点击留出间隔
    return true;
}
```

同理 `press_esc()` 走 `key_down(VK_ESCAPE)` + sleep(50ms) + `key_up(VK_ESCAPE)`。

---

## 二、实现架构设计

### 2.1 总体分层架构（对齐 MAA）

```
┌─────────────────────────────────────────────────────────────────┐
│                    辅助工具上层（任务调度 / 图像识别）              │
│   识别循环：screencap() → 模板匹配/OCR → 决策 → click/swipe/input  │
└─────────────────────────────────────────────────────────────────┘
                          │  ControllerAPI 抽象接口
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Controller 层（如 MAA Win32Controller）        │
│   attach(hwnd, screencap_method, mouse_method, keyboard_method)  │
│   screencap() / click() / swipe() / input() / press_esc()        │
│   └─ 特性位协商（Down/Up 替代 Click）                             │
└─────────────────────────────────────────────────────────────────┘
                          │  MaaFwControlUnitAPI（虚接口，ABI 兼容）
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                   ControlUnit 管理器（Win32ControlUnitMgr）      │
│   构造参数：hwnd + 截图方式 + 鼠标方式 + 键盘方式                  │
│   connect()：初始化截图模块 + 创建输入模块                        │
│   screencap() / touch_down() / key_down() ... 分发给内部模块      │
├──────────────┬─────────────────────┬────────────────────────────┤
│  Screencap   │  MouseInput         │  KeyboardInput              │
│  FramePool   │  MessageInput       │  MessageInput（同一实现）     │
│  (WGC)       │  with_window_pos    │  WM_KEYDOWN/UP/CHAR         │
│              │  WM_MOUSEMOVE/...   │                             │
└──────────────┴─────────────────────┴────────────────────────────┘
```

### 2.2 模块职责

| 模块 | 职责 | 关键接口 |
|---|---|---|
| **截图模块**（ScreencapBase） | 提供一帧 `cv::Mat` 图像 | `std::optional<cv::Mat> screencap()` |
| **鼠标模块**（InputBase） | 点击、滑动、触摸、滚动 | `click / swipe / touch_down / touch_move / touch_up / scroll` |
| **键盘模块**（InputBase） | 按键、文本输入 | `click_key / input_text / key_down / key_up` |
| **管理器**（Win32ControlUnitMgr） | 生命周期、模块分发、特性位声明 | `connect / screencap / ... / get_features` |

> 鼠标与键盘共用 `MessageInput` 实现，仅 `Config`（mode、with_cursor_pos、with_window_pos）不同。管理器在 `connect()` 时若鼠标/键盘方式相同则复用同一实例。

### 2.3 数据流转

```
【识别循环】

 识别引擎                          Controller                     ControlUnit
    │                                 │                              │
    │ 1. screencap()                  │ 2. unit_screencap()          │ 3. FramePool WGC
    │ ◄───────────────────────────────│◄─────────────────────────────│    取帧 → cv::Mat
    │ 4. 识别出目标按钮坐标 (x,y)       │                              │
    │ 5. click(x, y)                  │ 6. touch_down(0,x,y)         │ 7. send_activate()
    │                                │                              │    prepare_mouse_position()
    │                                │                              │    WM_MOUSEMOVE + WM_LBUTTONDOWN
    │                                │ 8. sleep(50ms)               │
    │                                │ 9. touch_up(0)               │ 10. WM_LBUTTONUP
    │                                │ 11. sleep(50ms)              │    restore 光标/窗口位置
    └────────────────────────────────┴──────────────────────────────┘
```

### 2.4 配置模型（对齐 MAA Win32Extra）

```jsonc
// config/gui.json 中 Win32Extra 的实际取值（本机）
"Win32Extra": {
    "ScreencapMethod": "FramePool",            // WGC 帧池截图
    "MouseMethod": "SendMessageWithWindowPos", // sendmsg + 移动窗口对齐光标
    "KeyboardMethod": "SendMessage"            // sendmsg 键盘
}
```

| 配置项 | 枚举值（MAA 支持全集） | 含义 |
|---|---|---|
| ScreencapMethod | `GDI` / `FramePool` / `DXGI_DesktopDup` / `DXGI_DesktopDup_Window` / `PrintWindow` / `ScreenDC` | 截图方式；`FramePool` 即 WGC |
| MouseMethod / KeyboardMethod | `None` / `SendMessage` / `PostMessage` / `SendMessageWithCursorPos` / `SendMessageWithWindowPos` / `Seize` / `LegacyEvent` / `Interception` / `PostThreadMessage` | 输入注入方式 |

---

## 三、核心代码实现方案

以下按模块给出可落地的核心代码。语言采用 **C++17 + C++/WinRT**（与 MAA 同技术栈）。

### 3.1 WGC 截图模块（FramePoolScreencap）

```cpp
// FramePoolScreencap.h
#pragma once
#include <optional>
#include <winrt/Windows.Graphics.Capture.h>
#include <windows.graphics.capture.interop.h>
#include <d3d11.h>
#include <opencv2/opencv.hpp>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>

namespace myassist {

class FramePoolScreencap {
public:
    explicit FramePoolScreencap(HWND hwnd) : hwnd_(hwnd) {}
    ~FramePoolScreencap() { uninit(); }

    std::optional<cv::Mat> screencap();
    void uninit();

private:
    bool init();
    bool init_texture(winrt::com_ptr<ID3D11Texture2D> raw_texture);
    bool check_and_handle_size_changed();

    void try_disable_border();
    void try_disable_cursor();
    void try_include_secondary_windows();

    HWND hwnd_ = nullptr;
    winrt::com_ptr<ID3D11Device> d3d_device_;
    winrt::com_ptr<ID3D11DeviceContext> d3d_context_;
    winrt::com_ptr<IDXGISwapChain> dxgi_swap_chain_;

    winrt::Windows::Graphics::Capture::GraphicsCaptureItem cap_item_ = nullptr;
    winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool cap_frame_pool_ = nullptr;
    winrt::Windows::Graphics::Capture::GraphicsCaptureSession cap_session_ = nullptr;

    winrt::com_ptr<ID3D11Texture2D> readable_texture_;
    D3D11_TEXTURE2D_DESC texture_desc_ = {};
    cv::Mat cached_image_;
    std::pair<int, int> last_capture_size_ = { 0, 0 };
};

} // namespace myassist
```

```cpp
// FramePoolScreencap.cpp —— 核心实现
#include "FramePoolScreencap.h"
#include <dwmapi.h>
#include <windows.graphics.capture.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <chrono>
#include <thread>

using namespace winrt;
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;

namespace myassist {

bool FramePoolScreencap::init()
{
    if (!hwnd_ || !IsWindow(hwnd_)) return false;

    // 1. 创建 D3D11 设备
    DXGI_SWAP_CHAIN_DESC desc = {};
    desc.BufferCount = 1;
    desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.OutputWindow = hwnd_;
    desc.SampleDesc.Count = 1;
    desc.Windowed = TRUE;

    HRESULT hr = D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE,
        nullptr, 0, nullptr, 0, D3D11_SDK_VERSION, &desc,
        dxgi_swap_chain_.put(), d3d_device_.put(), nullptr, d3d_context_.put());
    if (FAILED(hr)) return false;

    // 2. HWND → GraphicsCaptureItem
    auto activation_factory = get_activation_factory<GraphicsCaptureItem>();
    auto interop_factory = activation_factory.try_as<IGraphicsCaptureItemInterop>();
    if (!interop_factory) return false;

    hr = interop_factory->CreateForWindow(hwnd_,
        guid_of<GraphicsCaptureItem>(), put_abi(cap_item_));
    if (FAILED(hr) || !cap_item_) return false;

    auto item_size = cap_item_.Size();
    if (item_size.Width <= 0 || item_size.Height <= 0) return false;

    // 3. DXGI 设备 → WinRT IDirect3DDevice
    auto dxgi_device = d3d_device_.try_as<IDXGIDevice>();
    com_ptr<IInspectable> inspectable = nullptr;
    hr = CreateDirect3D11DeviceFromDXGIDevice(dxgi_device.get(), inspectable.put());
    if (FAILED(hr)) return false;

    auto d3d_device_interop = inspectable.try_as<
        Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice>();

    // 4. 创建帧池与会话
    cap_frame_pool_ = Direct3D11CaptureFramePool::Create(
        d3d_device_interop,
        DirectXPixelFormat::B8G8R8A8UIntNormalized,  // BGRA
        1,                                            // BufferCount
        cap_item_.Size());
    cap_session_ = cap_frame_pool_.CreateCaptureSession(cap_item_);

    // 5. 可选行为优化
    try_disable_border();
    try_disable_cursor();
    try_include_secondary_windows();

    cap_session_.StartCapture();
    last_capture_size_ = { item_size.Width, item_size.Height };
    return true;
}

std::optional<cv::Mat> FramePoolScreencap::screencap()
{
    if (!hwnd_) return std::nullopt;

    // 惰性初始化
    if (!cap_frame_pool_) {
        if (!init()) { uninit(); return std::nullopt; }
    }

    // 窗口尺寸变化 → 重建
    if (!check_and_handle_size_changed()) return std::nullopt;

    Direct3D11CaptureFrame frame = nullptr;
    try {
        // 清空残留旧帧，确保取到最新帧
        while (auto old_frame = cap_frame_pool_.TryGetNextFrame()) {
            old_frame.Close();
        }
        // 2 秒超时等待新帧
        auto start = std::chrono::steady_clock::now();
        while (std::chrono::steady_clock::now() - start < std::chrono::seconds(2)) {
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
            frame = cap_frame_pool_.TryGetNextFrame();
            if (frame) break;
        }
    }
    catch (const winrt::hresult_error&) {
        uninit();
        return std::nullopt;
    }

    if (!frame) {
        // 超时降级：返回缓存帧
        return cached_image_.empty() ? std::nullopt
                                     : std::optional<cv::Mat>(cached_image_.clone());
    }

    // 提取 D3D 纹理
    auto surface = frame.Surface();
    auto access = surface.try_as<IDirect3DDxgiInterfaceAccess>();
    if (!access) return std::nullopt;

    com_ptr<ID3D11Texture2D> texture = nullptr;
    HRESULT hr = access->GetInterface(guid_of<ID3D11Texture2D>(), texture.put_void());
    if (FAILED(hr)) return std::nullopt;

    if (!readable_texture_ && !init_texture(texture)) return std::nullopt;

    // GPU → CPU 可读纹理
    d3d_context_->CopyResource(readable_texture_.get(), texture.get());

    D3D11_MAPPED_SUBRESOURCE mapped = {};
    hr = d3d_context_->Map(readable_texture_.get(), 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(hr)) return std::nullopt;
    auto unmap = [&] { d3d_context_->Unmap(readable_texture_.get(), 0); };

    cv::Mat raw(texture_desc_.Height, texture_desc_.Width, CV_8UC4,
                mapped.pData, (int)mapped.RowPitch);

    // 用 DWM 边框裁剪客户区
    RECT client_rect = {};
    GetClientRect(hwnd_, &client_rect);
    POINT client_top_left = { 0, 0 };
    ClientToScreen(hwnd_, &client_top_left);

    RECT frame_rect = {};
    if (FAILED(DwmGetWindowAttribute(hwnd_, DWMWA_EXTENDED_FRAME_BOUNDS,
                                     &frame_rect, sizeof(frame_rect)))) {
        GetWindowRect(hwnd_, &frame_rect);
    }

    int border_left = std::max(0, client_top_left.x - frame_rect.left);
    int border_top  = std::max(0, client_top_left.y - frame_rect.top);
    int client_w    = std::min<int>(client_rect.right - client_rect.left, raw.cols);
    int client_h    = std::min<int>(client_rect.bottom - client_rect.top, raw.rows);
    border_left     = std::min(border_left, raw.cols - client_w);
    border_top      = std::min(border_top, raw.rows - client_h);

    cv::Mat result;
    cv::cvtColor(raw(cv::Rect(border_left, border_top, client_w, client_h)), result,
                 cv::COLOR_BGRA2BGR);

    unmap();
    cached_image_ = result.clone();
    return result;
}

bool FramePoolScreencap::init_texture(winrt::com_ptr<ID3D11Texture2D> raw_texture)
{
    raw_texture->GetDesc(&texture_desc_);
    texture_desc_.BindFlags = 0;
    texture_desc_.MiscFlags = 0;
    texture_desc_.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    texture_desc_.Usage = D3D11_USAGE_STAGING;   // CPU 可读
    return SUCCEEDED(d3d_device_->CreateTexture2D(&texture_desc_, nullptr, readable_texture_.put()));
}

bool FramePoolScreencap::check_and_handle_size_changed()
{
    if (!cap_item_ || !IsWindow(hwnd_)) return false;
    auto size = cap_item_.Size();
    if (size.Width == last_capture_size_.first &&
        size.Height == last_capture_size_.second) {
        return true;
    }
    // 尺寸变化：完全重建帧池
    uninit();
    return init();
}

void FramePoolScreencap::uninit()
{
    if (cap_session_) { try { cap_session_.Close(); } catch (...) {} cap_session_ = nullptr; }
    if (cap_frame_pool_) { try { cap_frame_pool_.Close(); } catch (...) {} cap_frame_pool_ = nullptr; }
    readable_texture_ = nullptr;
    cap_item_ = nullptr;
    texture_desc_ = {};
    last_capture_size_ = {};
}

void FramePoolScreencap::try_disable_border()
{
    using namespace winrt::Windows::Foundation::Metadata;
    if (!ApiInformation::IsPropertyPresent(L"Windows.Graphics.Capture.GraphicsCaptureSession",
                                           L"IsBorderRequired")) return;
    try {
        auto op = GraphicsCaptureAccess::RequestAccessAsync(GraphicsCaptureAccessKind::Borderless);
        if (op.wait_for(std::chrono::seconds(5)) == Windows::Foundation::AsyncStatus::Completed &&
            op.GetResults() == Windows::Security::Authorization::AppCapabilityAccess::AppCapabilityAccessStatus::Allowed) {
            cap_session_.IsBorderRequired(false);
        }
    } catch (...) {}
}

void FramePoolScreencap::try_disable_cursor()
{
    using namespace winrt::Windows::Foundation::Metadata;
    if (!ApiInformation::IsPropertyPresent(L"Windows.Graphics.Capture.GraphicsCaptureSession",
                                           L"IsCursorCaptureEnabled")) return;
    try { cap_session_.IsCursorCaptureEnabled(false); } catch (...) {}
}

void FramePoolScreencap::try_include_secondary_windows()
{
    using namespace winrt::Windows::Foundation::Metadata;
    if (!ApiInformation::IsPropertyPresent(L"Windows.Graphics.Capture.GraphicsCaptureSession",
                                           L"IncludeSecondaryWindows")) return;
    try { cap_session_.IncludeSecondaryWindows(true); } catch (...) {}
}

} // namespace myassist
```

**错误处理要点**：
- 所有 WinRT 调用包裹在 `try/catch(hresult_error)` 中，捕获后 `uninit()` 并返回 `nullopt`，下次调用自动重建。
- `TryGetNextFrame` 超时（2s）返回缓存帧，避免识别循环因偶发丢帧中断。
- `Map/Unmap` 用 RAII 保证成对调用。

**性能优化要点**：
- 使用 Staging 纹理 + `CopyResource`，绕开 GDI 内存拷贝。
- `BufferCount=1` + 取帧前清空，减少内存占用与延迟。
- 窗口尺寸不变时复用帧池，不重复创建。
- 所有截图结果缓存，超时降级不中断流程。

---

### 3.2 鼠标输入模块（sendmsg-windowpos）

```cpp
// MessageInput.h
#pragma once
#include <windows.h>
#include <atomic>
#include <thread>
#include <opencv2/opencv.hpp>

namespace myassist {

class MessageInput {
public:
    struct Config {
        enum class Mode { SendMessage, PostMessage };
        Mode mode = Mode::SendMessage;
        bool with_cursor_pos = false;    // 移动真实光标
        bool with_window_pos = false;    // 移动窗口对齐光标（sendmsg-windowpos）
        bool block_input = false;        // 屏蔽真实鼠标
    };

    explicit MessageInput(HWND hwnd, Config cfg) : hwnd_(hwnd), config_(cfg) {}

    bool touch_down(int contact, int x, int y, int pressure);
    bool touch_move(int contact, int x, int y, int pressure);
    bool touch_up(int contact);
    bool scroll(int dx, int dy);

    void inactive();

private:
    bool send_or_post_w(HWND target, UINT message, WPARAM w, LPARAM l);
    LPARAM make_mouse_lparam(HWND target, int x, int y);
    POINT client_to_screen(int x, int y);
    bool prepare_mouse_position(int x, int y);
    bool move_window_to_align_cursor(int x, int y);
    HWND send_activate();
    void check_and_block_input();
    void save_pos();
    void restore_pos();
    void start_window_tracking(int x, int y);

    HWND hwnd_ = nullptr;
    Config config_;
    POINT saved_cursor_pos_ = {};
    RECT saved_window_rect_ = {};
    bool pos_saved_ = false;

    std::atomic<int> pending_mouse_x_ { 0 }, pending_mouse_y_ { 0 };
    std::atomic<bool> window_pos_invalid_movement_ { false };
};

} // namespace myassist
```

```cpp
// MessageInput.cpp —— 核心实现
#include "MessageInput.h"
#include <map>

namespace myassist {

static int GetMappedContact(int c) {
    if (!GetSystemMetrics(SM_SWAPBUTTON)) return c;   // 未交换左右键
    if (c == 0) return 1;
    if (c == 1) return 0;
    return c;
}

struct MouseMsg { UINT message = 0; WPARAM w_param = 0; };

static MouseMsg contact_to_mouse_down_message(int contact) {
    switch (GetMappedContact(contact)) {
    case 0: return { WM_LBUTTONDOWN, MK_LBUTTON };
    case 1: return { WM_RBUTTONDOWN, MK_RBUTTON };
    case 2: return { WM_MBUTTONDOWN, MK_MBUTTON };
    case 3: return { WM_XBUTTONDOWN, MAKEWPARAM(MK_XBUTTON1, XBUTTON1) };
    case 4: return { WM_XBUTTONDOWN, MAKEWPARAM(MK_XBUTTON2, XBUTTON2) };
    default: return { 0, 0 };
    }
}
static MouseMsg contact_to_mouse_move_message(int contact) {
    switch (GetMappedContact(contact)) {
    case 0: return { WM_MOUSEMOVE, MK_LBUTTON };
    case 1: return { WM_MOUSEMOVE, MK_RBUTTON };
    case 2: return { WM_MOUSEMOVE, MK_MBUTTON };
    case 3: return { WM_MOUSEMOVE, MK_XBUTTON1 };
    case 4: return { WM_MOUSEMOVE, MK_XBUTTON2 };
    default: return { 0, 0 };
    }
}
static MouseMsg contact_to_mouse_up_message(int contact) {
    switch (GetMappedContact(contact)) {
    case 0: return { WM_LBUTTONUP, 0 };
    case 1: return { WM_RBUTTONUP, 0 };
    case 2: return { WM_MBUTTONUP, 0 };
    case 3: return { WM_XBUTTONUP, MAKEWPARAM(0, XBUTTON1) };
    case 4: return { WM_XBUTTONUP, MAKEWPARAM(0, XBUTTON2) };
    default: return { 0, 0 };
    }
}

bool MessageInput::send_or_post_w(HWND target, UINT message, WPARAM w, LPARAM l)
{
    if (!target || !IsWindow(target)) return false;
    if (config_.mode == Config::Mode::PostMessage) {
        return PostMessageW(target, message, w, l);
    }
    SendMessageW(target, message, w, l);   // 同步发送，等窗口过程处理完
    return true;
}

LPARAM MessageInput::make_mouse_lparam(HWND target, int x, int y)
{
    if (target == hwnd_) return MAKELPARAM(x, y);
    POINT pt = { x, y };
    ClientToScreen(hwnd_, &pt);
    ScreenToClient(target, &pt);
    return MAKELPARAM(pt.x, pt.y);
}

POINT MessageInput::client_to_screen(int x, int y)
{
    POINT pt = { x, y };
    if (hwnd_) ClientToScreen(hwnd_, &pt);
    return pt;
}

HWND MessageInput::send_activate()
{
    if (!hwnd_) return nullptr;
    // 发送 WM_ACTIVATE 让目标认为自身激活，但不抢占系统前台
    send_or_post_w(hwnd_, WM_ACTIVATE, WA_ACTIVE, 0);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    return hwnd_;
}

void MessageInput::check_and_block_input()
{
    // 生产实现：安装低级鼠标钩子 WH_MOUSE_LL 屏蔽真实输入，
    // 操作结束后移除钩子（此处省略钩子回调细节）
}

void MessageInput::save_pos()
{
    GetCursorPos(&saved_cursor_pos_);
    GetWindowRect(hwnd_, &saved_window_rect_);
    pos_saved_ = true;
}

void MessageInput::restore_pos()
{
    if (!pos_saved_ || !hwnd_) return;
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    // 若保存位置已移出屏幕，回落到左上角
    if (!MonitorFromRect(&saved_window_rect_, MONITOR_DEFAULTTONULL)) {
        saved_window_rect_.left = 0;
        saved_window_rect_.top = 0;
    }
    SetWindowPos(hwnd_, nullptr, saved_window_rect_.left, saved_window_rect_.top, 0, 0,
                 SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
    if (config_.with_cursor_pos) {
        SetCursorPos(saved_cursor_pos_.x, saved_cursor_pos_.y);
    }
    pos_saved_ = false;
}

bool MessageInput::prepare_mouse_position(int x, int y)
{
    if (config_.with_window_pos && window_pos_invalid_movement_.load()) return false;
    if (config_.with_cursor_pos) {
        POINT sp = client_to_screen(x, y);
        if (!SetCursorPos(sp.x, sp.y)) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        return true;
    }
    if (config_.with_window_pos) {
        start_window_tracking(x, y);
        return move_window_to_align_cursor(x, y);
    }
    return true;   // 纯消息模式
}

bool MessageInput::move_window_to_align_cursor(int x, int y)
{
    if (!hwnd_) return false;

    POINT cursor_pos;
    if (!GetCursorPos(&cursor_pos)) return false;

    POINT client_origin = { 0, 0 };
    if (!ClientToScreen(hwnd_, &client_origin)) return false;

    RECT rect = {};
    if (!GetWindowRect(hwnd_, &rect)) return false;

    // 锚点算法：客户区原点与整窗原点的差值（边框宽度）
    int border_x = client_origin.x - rect.left;
    int border_y = client_origin.y - rect.top;

    int new_left = cursor_pos.x - x - border_x;
    int new_top  = cursor_pos.y - y - border_y;

    if (!SetWindowPos(hwnd_, nullptr, new_left, new_top, 0, 0,
                      SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)) {
        return false;
    }
    return true;
}

void MessageInput::start_window_tracking(int x, int y)
{
    // 生产实现：记录跟踪目标坐标，启动后台线程监听真实鼠标位移，
    // 将位移换算为窗口移动量（见 1.2.6 节），此处省略线程细节。
}

bool MessageInput::touch_down(int contact, int x, int y, int pressure)
{
    HWND target = send_activate();
    check_and_block_input();
    save_pos();
    if (!prepare_mouse_position(x, y)) return false;

    LPARAM lParam = make_mouse_lparam(target, x, y);
    auto move = contact_to_mouse_move_message(contact);
    auto down = contact_to_mouse_down_message(contact);

    send_or_post_w(target, move.message, move.w_param, lParam);  // 先移动
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    send_or_post_w(target, down.message, down.w_param, lParam);  // 再按下
    return true;
}

bool MessageInput::touch_move(int contact, int x, int y, int pressure)
{
    HWND target = hwnd_;
    if (!prepare_mouse_position(x, y)) return false;
    LPARAM lParam = make_mouse_lparam(target, x, y);
    auto move = contact_to_mouse_move_message(contact);
    return send_or_post_w(target, move.message, move.w_param, lParam);
}

bool MessageInput::touch_up(int contact)
{
    HWND target = hwnd_;
    auto up = contact_to_mouse_up_message(contact);
    LPARAM lParam = make_mouse_lparam(target, last_x_, last_y_);
    bool ok = send_or_post_w(target, up.message, up.w_param, lParam);
    restore_pos();
    return ok;
}

bool MessageInput::scroll(int dx, int dy)
{
    HWND target = send_activate();
    POINT screen_pos = client_to_screen(0, 0);  // 生产实现用最近目标点
    LPARAM lParam = MAKELPARAM(screen_pos.x, screen_pos.y);  // 滚轮用屏幕坐标
    if (dy != 0) send_or_post_w(target, WM_MOUSEWHEEL, MAKEWPARAM(0, (WORD)dy), lParam);
    if (dx != 0) send_or_post_w(target, WM_MOUSEHWHEEL, MAKEWPARAM(0, (WORD)dx), lParam);
    return true;
}

} // namespace myassist
```

**要点**：
- `SendMessageW` 保证消息按序、被窗口实际处理后返回。
- `lParam` 默认编码客户区坐标；`WM_MOUSEWHEEL` 例外，需屏幕坐标。
- `with_window_pos` 模式：光标不动、移动窗口，让"窗口客户区目标点"对准光标，适配锁定鼠标的游戏。
- 操作前 `save_pos()`、结束后 `restore_pos()`，尽量无感恢复窗口与光标位置。

---

### 3.3 键盘输入模块（sendmsg）

```cpp
// MessageInput 的键盘部分（可独立为 KeyboardInput）
#include <windows.h>
#include <string>
#include <thread>

namespace myassist {

// WM_KEYDOWN 的 lParam：重复计数 1 + 扫描码（bit16..23）
static LPARAM make_keydown_lparam(int key)
{
    UINT sc = MapVirtualKeyW((UINT)key, MAPVK_VK_TO_VSC);
    return 1 | ((LPARAM)sc << 16);
}

// WM_KEYUP 的 lParam：额外置位先前状态(bit30)与转换状态(bit31)
static LPARAM make_keyup_lparam(int key)
{
    UINT sc = MapVirtualKeyW((UINT)key, MAPVK_VK_TO_VSC);
    return 1 | ((LPARAM)sc << 16) | (1L << 30) | (1L << 31);
}

class KeyboardInput {
public:
    explicit KeyboardInput(HWND hwnd) : hwnd_(hwnd) {}

    bool key_down(int key)
    {
        if (!hwnd_ || !IsWindow(hwnd_)) return false;
        SendMessageW(hwnd_, WM_ACTIVATE, WA_ACTIVE, 0);   // 激活窗口
        return SendMessageW(hwnd_, WM_KEYDOWN, (WPARAM)key,
                            make_keydown_lparam(key)) != 0;
    }

    bool key_up(int key)
    {
        if (!hwnd_ || !IsWindow(hwnd_)) return false;
        SendMessageW(hwnd_, WM_ACTIVATE, WA_ACTIVE, 0);
        return SendMessageW(hwnd_, WM_KEYUP, (WPARAM)key,
                            make_keyup_lparam(key)) != 0;
    }

    // 一次完整按键（按下 → 50ms → 抬起）
    bool click_key(int key)
    {
        if (!key_down(key)) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        return key_up(key);
    }

    // 文本输入：逐字符 WM_CHAR
    bool input_text(const std::string& utf8_text)
    {
        std::wstring wide = utf8_to_utf16(utf8_text);
        for (wchar_t ch : wide) {
            SendMessageW(hwnd_, WM_CHAR, (WPARAM)ch, 0);
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        return true;
    }

private:
    static std::wstring utf8_to_utf16(const std::string& s)
    {
        if (s.empty()) return {};
        int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
        std::wstring out(n, 0);
        MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &out[0], n);
        return out;
    }

    HWND hwnd_ = nullptr;
};

} // namespace myassist
```

---

### 3.4 控制单元管理器（模块组装）

```cpp
// Win32ControlUnitMgr.h（简版）
#pragma once
#include <memory>
#include <opencv2/opencv.hpp>
#include "FramePoolScreencap.h"
#include "MessageInput.h"

namespace myassist {

class Win32ControlUnitMgr {
public:
    // 与 MAA 枚举对齐
    enum ScreencapMethod { Screencap_None, Screencap_GDI, Screencap_FramePool,
                           Screencap_DXGIDesktopDup, Screencap_PrintWindow, Screencap_ScreenDC };
    enum InputMethod { Input_None, Input_SendMessage, Input_SendMessageWithCursorPos,
                       Input_SendMessageWithWindowPos, Input_PostMessage };

    Win32ControlUnitMgr(HWND hwnd, ScreencapMethod scm, InputMethod mouse, InputMethod kb)
        : hwnd_(hwnd), screencap_method_(scm), mouse_method_(mouse), keyboard_method_(kb) {}

    bool connect()
    {
        if (!hwnd_ || !IsWindow(hwnd_)) return false;
        // 截图模块
        if (screencap_method_ == Screencap_FramePool) {
            screencap_ = std::make_shared<FramePoolScreencap>(hwnd_);
        }
        // 输入模块（鼠标/键盘共用 MessageInput，仅 Config 不同）
        MessageInput::Config mouse_cfg;
        mouse_cfg.with_window_pos = (mouse_method_ == Input_SendMessageWithWindowPos);
        mouse_cfg.with_cursor_pos = (mouse_method_ == Input_SendMessageWithCursorPos);
        mouse_ = std::make_shared<MessageInput>(hwnd_, mouse_cfg);
        keyboard_ = mouse_;   // 键盘同样走消息注入
        connected_ = true;
        return true;
    }

    bool screencap(cv::Mat& out)
    {
        if (!connected_ || !screencap_) return false;
        auto mat = screencap_->screencap();
        if (!mat) return false;
        out = mat.value();
        return true;
    }

    // 特性位：上层据此使用 touch_down/up 而非 click
    uint64_t get_features() const { return (1ULL << 1); /* UseMouseDownAndUpInsteadOfClick */ }

    std::shared_ptr<FramePoolScreencap> screencap_;
    std::shared_ptr<MessageInput> mouse_;
    std::shared_ptr<MessageInput> keyboard_;

private:
    HWND hwnd_ = nullptr;
    ScreencapMethod screencap_method_ = Screencap_None;
    InputMethod mouse_method_ = Input_None;
    InputMethod keyboard_method_ = Input_None;
    bool connected_ = false;
};

} // namespace myassist
```

---

### 3.5 上层 Controller（识别循环示例）

```cpp
// 识别循环骨架：截图 → 匹配 → 点击
void automation_loop(Win32ControlUnitMgr& ctrl)
{
    cv::Mat frame;
    while (running) {
        if (!ctrl.screencap(frame)) {
            std::this_thread::sleep_for(100ms);
            continue;
        }

        // ① 图像识别：定位按钮（模板匹配/特征点/OCR）
        auto target = match_button(frame, "button_start");
        if (!target) continue;

        // ② 点击（特性位要求 down/up 成对）
        ctrl.mouse_->touch_down(0, target->x, target->y, 0);
        std::this_thread::sleep_for(50ms);
        ctrl.mouse_->touch_up(0);
        std::this_thread::sleep_for(50ms);
    }
}
```

---

## 四、稳定性与兼容性方案

### 4.1 窗口最小化场景：伪最小化（Pseudo Minimize）

**问题**：窗口最小化（`IsIconic`）时，WGC 无法捕获画面（DWM 不为其合成内容）。

**方案**（MAA `PseudoMinimizeHelper`）：让窗口"看起来最小化、实则后台可绘制"。

```
apply_pseudo_minimize():
 1. 保存原始扩展样式（GetWindowLongPtr GWL_EXSTYLE）
 2. 添加 WS_EX_LAYERED | WS_EX_TRANSPARENT
 3. SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA)  → 全透明
 4. ShowWindow(hwnd, SW_SHOWNOACTIVATE)                → 取消最小化但不激活前台
 5. 置伪最小化标志

revert_pseudo_minimize():
 1. 直接恢复原始扩展样式（覆盖，避免位运算累积错误）
 2. 若原本有 WS_EX_LAYERED 则恢复原始透明度
 3. 退出时若仍伪最小化，先还原再 SW_MINIMIZE 真正最小化

监控线程（每 100ms）：
 - 若处于伪最小化且用户通过任务栏激活了该窗口（GetForegroundWindow()==hwnd）
   → revert_pseudo_minimize() 恢复正常显示
```

**要点**：
- `WS_EX_TRANSPARENT` 使窗口在伪最小化期间不拦截鼠标（穿透）。
- 每次截图前调用 `ensure_not_minimized()`，同步保证截图瞬间窗口可捕获。
- 还原时**直接覆盖**原始样式而非位运算，避免多轮切换导致样式漂移。

### 4.2 多显示器 DPI 缩放场景

**问题**：不同显示器缩放比不同，`GetWindowRect`/`ClientToScreen` 返回的可能是逻辑坐标，导致截图区域与点击坐标错位。

**方案**：

```
1. 进程设置为 Per-Monitor DPI Aware V2：
   SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
   （在 Win32ControlUnitMgr::connect() 开头调用）

2. 所有坐标计算基于物理像素：
   - 截图：WGC 捕获的帧本身就是物理像素；
   - 点击：make_mouse_lparam 中的 ClientToScreen / ScreenToClient 在 Per-Monitor Aware
     下返回物理坐标，保证与截图 ROI 对齐。

3. 多显示器时使用虚拟屏幕边界做位移钳制：
   SM_XVIRTUALSCREEN / SM_YVIRTUALSCREEN / SM_CXVIRTUALSCREEN / SM_CYVIRTUALSCREEN
```

> Per-Monitor DPI Aware V2 是 MAA `Win32ControlUnitMgr::connect()` 中显式设置的一步，注释明确说明"确保截图区域为物理像素，修复高 DPI 下截图不完整的问题"。

### 4.3 游戏窗口被遮挡场景

**问题**：目标窗口被其他窗口覆盖时，传统 `BitBlt`/`PrintWindow` 截不到内容。

**方案**：
- **WGC 天然免疫遮挡**：捕获走 DWM 合成管线，无论窗口是否被遮挡，都能拿到最终合成内容。
- 遮挡也不影响输入：`SendMessageW` 直接投递到窗口过程，不依赖前台焦点（配合 `WM_ACTIVATE` 伪激活）。
- 若目标应用为**独占全屏**（Exclusive Fullscreen）导致 WGC 失效，备选方案：
  - 提示用户切换为"无边框窗口"模式；
  - 或回退到 `DXGI_DesktopDup`（桌面复制）后按窗口矩形裁剪（MAA 的 `DesktopDupWindowScreencap`）。

### 4.4 窗口尺寸动态变化

- 每次 `screencap()` 前比对 `cap_item_.Size()` 与上次尺寸，变化则 `uninit()+init()` 重建帧池与会话。
- 点击坐标始终基于**最新一帧**的客户区尺寸换算，避免使用陈旧分辨率。

### 4.5 前台激活抑制与冷却

- Windows 会限制 `SetForegroundWindow` 被频繁调用（防抢焦点）。MAA `ForegroundUtils` 提供带冷却的前台恢复：

```cpp
bool ensure_foreground_with_cooldown(HWND hwnd)
{
    constexpr DWORD kCooldown = 5000;   // 两次尝试间隔至少 5 秒
    static DWORD last_attempt = 0;
    if (!hwnd || !IsWindow(hwnd)) return false;
    if (hwnd == GetForegroundWindow()) return true;

    DWORD now = GetTickCount();
    if (last_attempt && now - last_attempt < kCooldown) {
        return hwnd == GetForegroundWindow();
    }
    last_attempt = now;
    // 置顶 → SetForegroundWindow → 失败再置顶（双保险）
    SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
    std::this_thread::sleep_for(5ms);
    SetForegroundWindow(hwnd);
    std::this_thread::sleep_for(10ms);
    return hwnd == GetForegroundWindow();
}
```

### 4.6 其他健壮性清单

| 场景 | 处理策略 |
|---|---|
| 窗口句柄失效 | 每次操作前 `IsWindow(hwnd_)` 校验；失效则停止任务并上报 |
| 截图超时 | 2s 超时返回缓存帧；连续失败 N 次触发重建/重连 |
| 输入期间窗口被用户拖动 | with_window_pos 模式下用锚点差值而非绝对坐标，避免累积误差；`is_window_move_allowed` 校验合法移动范围 |
| 用户操作干扰 | `check_and_block_input()` 低级鼠标钩子屏蔽真实输入，操作后恢复 |
| 特权/权限 | WGC Borderless 需要 `RequestAccessAsync` 申请；未授权则跳过并记录警告 |
| 线程安全 | 截图与输入分属不同调用，管理器加互斥锁；跟踪线程状态用原子变量 |
| 应用无响应 | `SendMessageW` 会阻塞，若目标进程卡死需超时兜底（可评估切 `PostMessageW` 或子线程发送） |

---

## 五、参考资料

1. MaaAssistantArknights/MaaAssistantArknights —— `src/MaaCore/Controller/Win32Controller.{h,cpp}`、`Win32ControlUnitLoader.{h,cpp}`、`MaaFwControlUnitInterface.h`
2. MaaXYZ/MaaFramework —— `source/MaaWin32ControlUnit/`：
   - `Screencap/FramePoolScreencap.cpp`（WGC 帧池截图）
   - `Input/MessageInput.{h,cpp}`、`Input/InputUtils.h`（sendmsg 输入）
   - `Screencap/PseudoMinimizeHelper.cpp`（伪最小化）
   - `Base/ForegroundUtils.h`、`Base/UnitBase.h`、`Manager/Win32ControlUnitMgr.{h,cpp}`
3. Microsoft Learn：Windows.Graphics.Capture API、Direct3D11CaptureFramePool、IGraphicsCaptureItemInterop
4. Microsoft Learn：Win32 鼠标消息（WM_MOUSEMOVE / WM_LBUTTONDOWN / WM_MOUSEWHEEL）、键盘消息（WM_KEYDOWN / WM_CHAR）、SendMessageW

---

*本文档基于本机 MAA v5.9.2 发布版（D:\MAA）的 DLL 导出符号、TRC 日志与配置文件，
结合 MAA / MaaFramework 开源同源实现编写，可作为自研 PC 端游戏辅助工具的技术蓝图。*
