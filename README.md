<p align="center">
  <a href="https://wwbnb.lanzouw.com/ifUTC41znwfg" style="display:inline-block;padding:11px 24px;margin:6px 8px;border-radius:8px;background:#2b8a3e;color:#ffffff;font-size:15px;font-weight:700;text-decoration:none;">📦 蓝奏云 · 下载发行版</a>
  <a href="https://atomgit.com/A9iska/Ark9Tools" style="display:inline-block;padding:11px 24px;margin:6px 8px;border-radius:8px;background:#1f6feb;color:#ffffff;font-size:15px;font-weight:700;text-decoration:none;">💻 GitCode · 查看源码</a>
  <a href="https://github.com/Aik358/Ark9Tools" style="display:inline-block;padding:11px 24px;margin:6px 8px;border-radius:8px;background:#24292f;color:#ffffff;font-size:15px;font-weight:700;text-decoration:none;">🐙 GitHub · 镜像仓库</a>
</p>

<blockquote>
<strong>🔑 蓝奏云提取码：26tw</strong><br>
如果按钮打不开，请直接复制以下链接使用：<br>
GitCode：https://atomgit.com/A9iska/Ark9Tools<br>
GitHub：https://github.com/Aik358/Ark9Tools<br>
蓝奏云：https://wwbnb.lanzouw.com/ifUTC41znwfg
</blockquote>

# Ark9Tools

> 【明日方舟】在游戏中全自动绘制像素画！从图像到游戏！
> 奇象巡展像素画自动绘画工具 —— **原生 PC 端**，支持 Win10 / Win11。

## 项目简介

Ark9Tools 是一个运行在 **Windows 电脑端**的本地游戏自动化工具中心，面向《明日方舟》的「奇象巡展」像素画编辑器。

它把一张图片自动转换成 24×24 的像素矩阵，连接游戏窗口完成画布与色板校准后，**在游戏里一笔一笔把画自动画出来**。

与「只能操作在安卓模拟器里运行的游戏」的同类工具不同，Ark9Tools 控制的是**原生 PC 端游戏进程**：直接操作你电脑上运行的游戏窗口，不需要装任何安卓模拟器，更轻、更省资源、延迟更低。

> Ark9Tools 是独立工具，不是 MAA 或 MaaAssistantArknights 的发行版本。项目参考了公开的 Win32 控制单元设计思路，详细说明见 [MAA_PC端操作模式技术报告与实践方案.md](tools/MAA_PC端操作模式技术报告与实践方案.md)。

## 核心特性

- **全自动绘画**：导入图片 → 像素化 → 自动校准 → 游戏内逐格绘制。
- **PC 原生**：直接控制 Windows 游戏窗口，无需模拟器。
- **自动校准**：用 OpenCV 检测画布边框 / 网格、色板布局，失败时可手动校准兜底。
- **实时校色**：每次选色都从当前可见色板重新定位目标色块，避免滚动位置不同步导致的系统性错色。
- **HDR 支持**：内置 `hdrcapture` 自动 SDR 捕获（HDR / Auto HDR 屏与 SDR 屏统一输出），绘画颜色判断更准。
- **安全边界**：只做窗口截图 + Win32 窗口消息 / 窗口位置控制，**不读取、不修改、不注入游戏内存**。
- **急停**：绘画中按 `ESC` 或 `F8` 可随时全局急停。

## 功能概览

Ark9Tools 围绕「奇象巡展」像素画，提供四件事一条龙：

1. **收藏（识别收藏夹）**：把游戏内「画像收藏」页亮出来，工具自动滚页、截图、识别每一张收藏的像素矩阵，存入本地收藏库，**一张不丢**。
2. **识图（识别官方宣传图）**：把官方宣传图 / 贺图直接拖进窗口，自动检测 24×24 网格画布并读取像素矩阵——官方负责画，我们负责「偷师」。
3. **转图（普通图片转像素画）**：任意图片拖入，裁剪、调对比度后三秒转成 24×24 像素画；白底可跳过、附带每色格数统计。
4. **开画（画到游戏里）**：连接游戏窗口 → 自动校准 → 按颜色频次逐格绘制；进度实时可见，`F8` / `ESC` 一键急停。

## 当前模块

| 模块 | 状态 | 用途 |
|---|---:|---|
| 工具中心 | 可用 | 查看工作区、模块入口和连接状态 |
| MAA-PixelPainter | 可用 | 像素化、游戏校准、自动绘画 |
| 连接中心 | 可用 | 管理共享游戏窗口与校准上下文 |
| 截图诊断 | 可用 | 只读检查窗口、权限、截图与色板参数 |
| 任务编排 | 规划中 | 为后续自动化任务预留扩展接口 |

## MAA-PixelPainter 工作流

进入 `MAA-PixelPainter` 后，按模块内部的三步完成任务：

1. **图像处理**：导入图片，调整饱和度、亮度、抖动方式和像素规格，生成 24×24 的逻辑色矩阵。
2. **游戏校准**：连接游戏窗口，检测或手动校准画布与色板坐标。
3. **执行绘画**：开始前自动检测当前色板网格与实际色块颜色；校准通过后再绘制。

PixelPainter 的任务完成后会返回 Ark9Tools 工具中心，宿主应用不会退出。

## 启动与权限

建议从以下入口启动：

```text
setup_hdr_env.bat   # 首次：创建 Python 3.13 虚拟环境并安装全部依赖
run.bat             # 以管理员身份启动 Ark9Tools
```

启动脚本会：

- 请求管理员权限；
- 首次运行自动创建 Python 3.13 虚拟环境并安装依赖；
- 启动 Ark9Tools 源码版本。

游戏窗口如果以管理员权限运行，Ark9Tools 也必须接受 UAC 提权。否则 Windows UIPI 会阻止窗口移动与输入消息，表现为点击、拖动或色板切换无效。

## 推荐操作顺序

1. 启动游戏，保持窗口可见且不要最小化。
2. 运行 `setup_hdr_env.bat`（仅首次），再双击 `run.bat`，在 UAC 确认管理员权限。
3. **先手动点击游戏窗口**使其处于前台。
4. 在 Ark9Tools 中进入 `连接中心` 或 PixelPainter 的 `游戏校准`，确认窗口和坐标。
5. 回到 `MAA-PixelPainter`，完成图像处理后点击开始绘画。
6. 绘画期间使用 `F8` 或 `ESC` 全局急停。

若自动前置游戏窗口失败，Ark9Tools 不会创建绘画任务；请先手动点击游戏窗口后重试。

### 关于最小化

当前稳定模式不模拟目标游戏的真实最小化。MAA 官方的 `PseudoMinimizeHelper + FramePool` 是 C++/WinRT/D3D11 的完整生命周期方案，包含窗口扩展样式、帧池重建、超时、尺寸变化和异常恢复；简单的 Python `SetWindowPos` 替代方案可能导致 Unity 窗口截图失败或崩溃，因此不在稳定模式中启用。

可以最小化 **Ark9Tools 自身**：绘画开始后会出现独立的“绘画监控”窗口，显示进度、当前颜色、速度、剩余时间和停止按钮。主工具窗口可以最小化，监控窗口不会改变游戏窗口状态。目标游戏请保持可见且不要真实最小化，确保 DXGI 区域截图和色板校准稳定。

## 色板校准与选色

游戏色板是一个需要**按住内容区连续上下拖动**的面板，并非固定的滚轮逐行列表。Ark9Tools 在画布点击前执行以下安全校准：

1. 使用当前桌面截图实时检测色板首列、首行、间距与可见行数。
2. 连续拖动色板并采样各视图中的实际 RGB 色块。
3. 将逻辑色 `X01~X38` 与实际游戏色块匹配；近重复逻辑色允许共享同一实际色块。
4. 每次选色时从当前可见色板重新定位目标色块。

如果网格检测失败、拖动后色板没有变化、颜色色差过大或所需颜色不可见，任务会在画布点击前停止，并在日志中写明失败原因。这样比按固定色板索引直接点击更慢，但能避免滚动位置不同步造成的系统性错色。

## MAA 伪最小化实验模式

输入模式中提供“实验：MAA 伪最小化”。它是独立的 opt-in 路径，不会替换稳定的 `WindowPos` 模式。

实验层已经实现并验证了以下安全边界：

- 保存/恢复目标窗口扩展样式；
- `WS_EX_LAYERED`、`WS_EX_TRANSPARENT` 与 `SW_SHOWNOACTIVATE` 生命周期封装；
- 捕获超时、空帧、尺寸变化和连续失败计数；
- 异常时自动关闭实验捕获会话并恢复窗口状态。

官方完整能力还需要原生 Windows 组件：`FramePool`、D3D11 staging texture、帧池重建、低级鼠标钩子和 `WindowPos` 跟踪线程。官方参考源码保存在 `tools/official_maa/MaaFramework`。当前环境没有 Visual Studio/Windows SDK 原生编译链，因此实验模式在缺少 `native_maa_backend` 时会自动回退到稳定 `WindowPos`，不会强行执行半成品。

稳定绘画仍建议使用 `WindowPos（MAA PC 推荐）`。实验模式只用于后续原生组件接入后的验证。

## 截图策略

- 窗口捕获优先使用 WGC，失败时可回退区域截图。
- 色板颜色校准使用当前桌面截图，避免 DXGI/WGC 缓冲旧帧和 HDR 色调映射导致的颜色误判。
- 截图诊断模块只读，不发送点击或拖动，可用于在绘画前检查窗口、权限与色板参数。

## HDR 支持

捕获优先使用 `hdrcapture` 的 `auto` 模式，把 HDR / Auto HDR 屏统一转成 SDR（BGRA8）再交给识别代码；初始化或连续取帧失败时自动回退到稳定的 DXGI 区域截图。色板校色使用当前桌面画面，规避 HDR 色调映射导致的颜色误判。

## 文件结构

```text
Ark9Tools/
├── main.py                         # Ark9Tools 宿主与模块路由
├── painter.py                      # PixelPainter 绘画控制、色板拖动与实时校色
├── pixelate.py                     # 图片到 24×24 逻辑色矩阵
├── palette.py                      # X01~X38 逻辑色表与颜色距离
├── calibration.py                  # 画布、色板坐标与自动检测
├── calibration_dialog.py           # 可视化手动校准
├── win32_input.py                  # Win32 消息、WindowPos 与前台控制
├── win32_capture.py                # 区域截图与窗口捕获
├── window_capture.py               # WGC 指定窗口捕获
├── hdrcapture_adapter.py           # HDR 自动 SDR 捕获适配层
├── experimental_backend.py         # MAA 伪最小化实验后端（opt-in）
├── experimental_capture.py         # 实验捕获（FramePool/D3D11 占位）
├── privilege.py                    # 管理员权限检查与提权辅助
├── history_store.py                # 收藏/历史像素数据存取（收藏库）
├── requirements.txt                # Python 依赖清单
├── MAA_PixelPainter.spec           # PyInstaller 打包规格
├── build_exe.bat                   # 一键编译为 exe
├── Ark9Tools.iss                   # Inno Setup 安装包脚本
├── run.bat                         # 管理员启动、依赖检查和源码运行
├── setup_hdr_env.bat               # 创建 Python 3.13 虚拟环境并装依赖
├── pixel_painter_config.json       # 本地窗口与校准配置（不入库）
├── assets/
│   ├── app.ico                     # 程序图标
│   ├── logo.png                    # 程序 logo
│   ├── loading.webm                # 加载动画
│   └── loading_frames/             # 加载动画逐帧（frame_01~17.png）
└── tools/
    ├── diagnostics/                # 开发诊断脚本（含大量 _debug_*.py，不影响正常使用）
    ├── test_pixel_validation.py    # 像素校验测试
    ├── test_report.md              # 校验测试报告
    ├── MAA_PC端操作模式技术报告与实践方案.md  # PC 端操作模式技术报告
    └── official_maa/               # 上游 MaaFramework（不入库，独立项目）
```

## 依赖

- Windows 10 / 11
- 首次运行 `setup_hdr_env.bat` 自动创建 Python 3.13 虚拟环境并安装依赖
- 依赖：`hdrcapture`、`PySide6`、`NumPy`、`Pillow`、`OpenCV`、`pywin32`、`mss`

## 安全边界与免责

Ark9Tools **不读取或修改游戏内存，不注入游戏进程**，仅通过窗口截图与 Win32 窗口消息 / 窗口位置操作。

需要管理员权限的原因是 Windows UIPI 会阻止普通权限程序向高权限窗口发送输入。

请自行确认你的自动化操作符合目标游戏及平台的相关规则。

## 更多

- PC 端操作模式的技术细节见 [`MAA_PC端操作模式技术报告与实践方案.md`](tools/MAA_PC端操作模式技术报告与实践方案.md)。
- 源码与更新（GitCode，主仓库）：https://atomgit.com/A9iska/Ark9Tools
- GitHub 镜像（自动同步）：https://github.com/Aik358/Ark9Tools
- 发行版（蓝奏云，含安装包）：https://wwbnb.lanzouw.com/ifUTC41znwfg （密码：26tw）
