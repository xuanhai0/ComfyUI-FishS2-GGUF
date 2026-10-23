# Windows 编译与部署 s2.cpp：特别注意编译、Release 目录和模型文件布局

[English](INSTALL_WINDOWS_S2CPP.md) | **简体中文** · [返回项目首页](README.zh-CN.md)

> **如果你的 s2.cpp 已经确认可独立运行 → [直接跳到本指南底部的 ComfyUI 节点设置](#comfyui-node-setup)**，或 [查看带图的节点设置教程](README.zh-CN.md#comfyui-node-setup)。

**太长不看 / TL;DR：** [编译依赖](#build-prerequisites) · [实际文件目录](#file-layout-and-actual-build-outputs) · [CLI/HTTP 独立验证](#validate-the-engine-before-comfyui) · [ComfyUI 节点设置](#comfyui-node-setup)。


本指南重点不是讲 ComfyUI 插件安装，而是讲 **在 Windows 上编译独立的 s2.cpp 推理引擎、找到真实的可执行文件/DLL、核对目录、先独立验证再接入插件**。插件**不会**自动编译 s2.cpp，也**不包含**模型、引擎、CUDA 文件。

- **s2.cpp 上游项目（优先参考）：** https://github.com/rodrigomatta/s2.cpp
- **上游编译说明：** https://github.com/rodrigomatta/s2.cpp#building
- **Fish S2 Pro GGUF 下载来源：** https://huggingface.co/rodrigomt/s2-pro-gguf
- **Fish Audio S2 Pro 与授权：** https://huggingface.co/fishaudio/s2-pro

> **Alpha 阶段提醒（2026 年 10 月开发期间）：** s2.cpp 上游 README 明确声明是 **ALPHA / EXPERIMENTAL**，属于社区实验性推理引擎，可能出现编译问题、参数变动和破坏性更新。本指南仅总结 Windows/MSVC 常见部署陷阱、当前代码实际扫描逻辑以及开发环境中遇到的路径差异，不能保证未来的 s2.cpp commit 使用完全相同的命令。

## Build prerequisites

## 1. 先安装并检查编译前置条件

以 **Windows + NVIDIA CUDA** 为例：

1. **Git**：必须正确获取 `ggml` 子模块。
2. **CMake ≥ 3.14**：配置项目、调用编译器。
3. **Visual Studio 2022 / Build Tools 2022**：安装 **Desktop development with C++（使用 C++ 的桌面开发）**，确保有 x64 MSVC 和 Windows SDK。上游最低要求为 MSVC 2019+，这里使用 VS 2022 举例。
4. **CUDA Toolkit ≥ 12.4**：上游对 MSVC 2019+ / GGML CUDA 构建提出此版本要求；需要相互兼容的 NVIDIA 显卡驱动和 Toolkit。**只装 NVIDIA 驱动，或只装 PyTorch CUDA wheel，不等于装了 CUDA 编译工具链。**

建议打开 **x64 Native Tools Command Prompt for VS 2022**，依次执行：

```bat
git --version
cmake --version
cl
nvcc --version
```

- `cl` 无法识别：优先检查 VS 的 C++ workload 和启动终端是否载入 MSVC 环境。
- `nvcc` 无法识别：优先检查 CUDA Toolkit 安装和环境变量，不要先去重装 ComfyUI 的 Python 环境。
- MSVC/CUDA 兼容性报错：检查 CUDA Toolkit 是否支持当前 MSVC toolset，并以**上游 README 和 NVIDIA 官方支持表**为准。

## 2. 克隆源码时必须带上 ggml 子模块

```bat
git clone --recurse-submodules https://github.com/rodrigomatta/s2.cpp.git
cd s2.cpp
git submodule status
```

如果 `ggml` 文件夹空缺或未初始化，在 **s2.cpp 根目录**执行：

```bat
git submodule update --init --recursive
```

直接使用 GitHub 的 **Download ZIP** 下载上游源码时，可能拿不到实际子模块内容；这是 CMake 找不到 `ggml/CMakeLists.txt` 的常见原因之一。

## 3. Windows 下编译 CUDA/Release

以下命令用于**安装了 Visual Studio 2022** 的 x64 Windows 环境，在 s2.cpp 源码根目录执行：

```bat
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 -DS2_CUDA=ON
cmake --build build --config Release --parallel 8
```

**注意上游 README 与 Windows 的差异：**

- 上游示例使用 `--parallel $(nproc)` 这样的 Unix 写法，在 Windows CMD 不应原样照搬。
- 上游说可执行文件位于 `build/s2`，但那是概括性的说明；**Windows + Visual Studio 的多配置生成器**可能把 Release 产物放到 `build/bin/Release/s2.exe`、`build/Release/s2.exe` 或其他位置。
- **必须使用 `--config Release`**，不然可能编译出 Debug 产物，路径和性能都不一致。
- 不要把 `s2.exe` 随意复制到插件目录。让 s2.cpp 保持独立，插件使用它的**真实绝对路径**。

## File layout and actual build outputs

## 4. 目录结构：不要假定教程中的路径就是实际路径

建议整理成以下结构（**仅为示意，不包含任何作者本机路径**）：

```text
s2.cpp/                           ← 源码根目录，对应插件的 root_dir
  CMakeLists.txt
  ggml/                           ← git 子模块
  tokenizer.json                  ← tokenizer 文件
  models/
    s2-pro-q8_0.gguf              ← 单独下载的 GGUF 文件
  voices/                          ← 需要时自行创建
    example.s2voice               ← 可选：生成后的音色文件
  build/
    bin/
      Release/
        s2.exe                    ← 一种常见的 Windows Release 路径
        ggml*.dll                 ← 实际位置依构建情况而异
```

**几个文件不要混淆：**

- `s2.exe`：编译得到的引擎；不等于 GGUF 模型。
- `.gguf`：需要另外下载。插件在 `root_dir/models/` 下扫描；不在这里时可以手动输入模型路径。
- `tokenizer.json`：需单独正确放置；代码仅扫描 `root_dir/tokenizer.json` 和 `root_dir/models/tokenizer.json`。
- `ggml*.dll`：编译生成的动态运行库；可能不在 `s2.exe` 的同一目录。
- `.s2voice`：音色配置，放在 Server Control 实际选择的 `voice_dir` 下。目录必须先存在。

PowerShell 逐项查找实际位置（**在 s2.cpp 根目录执行**）：

```powershell
Get-ChildItem .\build -Recurse -File -Filter s2.exe | Select-Object -ExpandProperty FullName
Get-ChildItem .\build -Recurse -File -Filter 'ggml*.dll' | Select-Object -ExpandProperty FullName
Test-Path .\tokenizer.json
Get-ChildItem .\models -Filter '*.gguf' -ErrorAction SilentlyContinue
Test-Path .\voices
```

如果 `voices` 目录不存在：

```powershell
New-Item -ItemType Directory -Path .\voices -Force
```

**为什么这是坑？** 仅凭源码目录名或 README 示例去填节点，可能得到 `file not found`、找不到 GGUF 或 DLL 加载失败。不要盲目搬动 DLL 或下载来历不明的 DLL，先找实际编译产物，再对应地填写插件路径。

## Validate the engine before ComfyUI

## 5. 不经过 ComfyUI，先独立验证编译结果

在 s2.cpp 根目录打开 PowerShell。以下示例假定 `s2.exe` 位于 `build/bin/Release/`；**路径不同必须自行替换**。

**（A）确认 CLI 可启动：**

```powershell
$exe = (Resolve-Path '.\build\bin\Release\s2.exe').Path
$model = (Resolve-Path '.\models\s2-pro-q8_0.gguf').Path
$tokenizer = (Resolve-Path '.\tokenizer.json').Path
& $exe --help
```

如果出现找不到 `ggml*.dll`，先找到实际 DLL 文件夹，再在**当前 PowerShell 会话**临时加入路径（下面只是位置示例）：

```powershell
$dllDir = (Resolve-Path '.\build\bin\Release').Path
$env:PATH = "$dllDir;$env:PATH"
& $exe --help
```

**（B）离线生成一个短 WAV：**

```powershell
& $exe --model $model --tokenizer $tokenizer --cuda 0 --gpu-layers -1 --codec-auto --text "Hello from s2.cpp." --output '.\smoke-test.wav'
Test-Path '.\smoke-test.wav'
```

确认 WAV 能**正常播放**，不只是生成了一个同名文件。

**（C）单独启动 HTTP Server：** 另开一个 PowerShell 窗口，用同样的 `$exe`、`$model`、`$tokenizer` 变量准备好路径，然后运行：

```powershell
& $exe --model $model --tokenizer $tokenizer --cuda 0 --gpu-layers -1 --codec-auto --voice-dir '.\voices' --server --host 127.0.0.1 --port 3030
```

在另一终端测试：

```powershell
Test-NetConnection 127.0.0.1 -Port 3030
curl.exe -X POST http://127.0.0.1:3030/generate --form 'text=Hello from the HTTP server.' --output '.\server-smoke-test.wav'
```

`Test-NetConnection` 只能证明端口可访问；成功返回、生成并播放 WAV 才是端到端验证。**最后停止这个独立服务**，再交由 ComfyUI 节点启动，否则端口 3030 会被占用。

## 6. 插件 Server Control：为什么自动扫描不等于全盘寻找

当前插件的真实扫描策略（由源代码确认）是：

| 控件 | root_dir 自动扫描的位置 | 不匹配时怎么办 |
|---|---|---|
| `executable` | `build/bin/Release/s2.exe`、`build/Release/s2.exe`、`build/s2.exe` 等几个固定位置 | 手动填写 `s2.exe` 的绝对路径 |
| `model` | **仅** `root_dir/models/*.gguf` | 手动填实际 GGUF 路径 |
| `tokenizer` | `root_dir/tokenizer.json` 或 `root_dir/models/tokenizer.json` | 手动填写完整路径 |
| `voice_dir` | 优先 `root_dir/voices`，也列出其它一级子目录作候选 | 建立专用 voices 目录并手动选择 |
| `dll_dir` | 首选几个常见 `build/...` 文件夹 | 填写 `ggml*.dll` 实际所在目录 |

正确顺序是：**先填 `root_dir` → 自动扫描 → 对照上一步的 PowerShell 查找结果 → 手动修正各字段 → 启动 Server。**

特别注意：**重新修改 `root_dir` 会触发重新扫描，覆盖手动调整过的路径值。** 因此建议最后才修改手动字段。

预期参数：`cuda_device=0`、`gpu_layers=-1`、`codec_backend=Auto`；不同硬件应相应调整。模型加载成功后，再通过插件跑一次极短语音任务。

**推荐：服务器路径确认后，将 Server Control 的 `server_config` 输出连接到所用其他节点的 `server_config` 输入。** Generate 自动使用并隐藏原 `server_url`；Voice Create 自动继承并隐藏可执行文件、模型、Tokenizer、音色/DLL 目录和 GPU 参数；Voice Input 可从相连的 Server Control 读取音色目录，方便选择已保存音色。注意不要把 `server_config` 输出与另一个 `server_url` 输出混淆；**连线不会自动启动服务器**，仍需要手动启动。

## 7. 常见报错速查

| 现象 | 优先检查 |
|---|---|
| 找不到 `cl` | VS C++ workload 或 MSVC Developer Prompt |
| 找不到 `nvcc` | CUDA Toolkit 和编译环境，不是 ComfyUI/PyTorch |
| 找不到 `ggml/CMakeLists.txt` | clone 时漏了 `--recurse-submodules` |
| MSVC/CUDA compiler incompatibility | CUDA 与 MSVC toolset 兼容性、上游规定的 Toolkit 版本 |
| 编译成功却找不到 `s2.exe` | Release/Debug 与 CMake generator 产物位置 |
| 双击 `s2.exe` 报 DLL 缺失 | 找真实的 `ggml*.dll` 文件夹，用 `dll_dir` / PATH |
| 节点找不到 GGUF | 自动扫描只查 `<root>/models`，改手动绝对路径 |
| 找不到 tokenizer | 检查两个候选位置或直接填绝对路径 |
| 无 voice 目录 | 自行创建，并让 Voice Create 与 Server Control 使用同一目录 |
| 端口占用、启动失败 | 已有 s2.cpp 服务在运行；先关闭旧进程 |
| UI 显示运行但无音频 | 检查 Server Monitor；先通过 CLI/HTTP 生成测试 |

其他问题另见上游 README：Alpha 阶段无真正并行生成，请求并发可能返回 `503`；FFmpeg 主要用于压缩音频参考输入，**不是编译 s2.cpp 的前置条件**。模型/引擎许可请参阅 [第三方说明](THIRD_PARTY_NOTICES.md)。

**验证范围声明：** 本指南基于 s2.cpp 上游说明、当前插件源码扫描逻辑和开发阶段 Windows 目录结构经验整理；本轮并没有在全新 Windows 主机上实际重新编译和跑通每一步。请根据具体版本调整命令。


## ComfyUI Node Setup

独立 CLI 和 HTTP 测试均成功后，可以 [直接打开中文带图的 ComfyUI 节点设置教程](README.zh-CN.md#comfyui-node-setup)。添加 **Fish S2 Server Control**，使用 **Scan Components** 核对 `executable`、`model`、`tokenizer`、`voice_dir`、`dll_dir`。**配置完成后推荐将其 `server_config` 输出连接到 Generate，以及实际使用的 Voice Create / Voice Input 节点的同名输入端口**；连接后相关服务器配置会被自动复用，Generate 的地址控件和 Voice Create 的重复路径/GPU 控件也会自动隐藏。随后启动服务并检查日志，再运行一段短文本。带图的两种用法分别展示下拉音色与 Voice Input 外接音色覆盖。
