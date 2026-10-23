# ComfyUI-FishS2-GGUF

通过独立的 [s2.cpp](https://github.com/rodrigomatta/s2.cpp) 推理引擎，在 ComfyUI 中本地运行 **Fish Audio S2-Pro GGUF 语音生成**；支持多角色、音色复用、分段、预览和 WAV 输出。

**维护者：[xuanhai0](https://github.com/xuanhai0)** · [English](README.md) | [简体中文](README.zh-CN.md)

> **你的 s2.cpp 已经验证能生成语音？→ [直接跳转到 ComfyUI 节点设置](#comfyui-node-setup)。** 不需要重新编译 s2.cpp。
>
> **还没部署 s2.cpp？→ [Windows 编译与目录避坑指南](INSTALL_WINDOWS_S2CPP.zh-CN.md)。** 在本插件 2026 年 10 月开发期间，s2.cpp 上游明确标为 **Alpha / Experimental**。请同时参考 [s2.cpp 官方仓库](https://github.com/rodrigomatta/s2.cpp)。

## 太长不看 / TL;DR

| 首先检查 | 一键跳转 |
|---|---|
| VS/MSVC、CMake、CUDA Toolkit 和 `ggml` 子模块完整 | [编译前置条件](INSTALL_WINDOWS_S2CPP.zh-CN.md#build-prerequisites) |
| 真正找到 `s2.exe`、`ggml*.dll`、GGUF 和 tokenizer；Windows Release 文件位置不一定符合上游示例 | [实际文件路径检查](INSTALL_WINDOWS_S2CPP.zh-CN.md#file-layout-and-actual-build-outputs) |
| s2.cpp 先在 **ComfyUI 外部**通过 CLI 和 HTTP 两次短语音测试 | [独立验证](INSTALL_WINDOWS_S2CPP.zh-CN.md#validate-the-engine-before-comfyui) |
| **先配置 Server Control**，再用 `server_config` 连接其他 Fish S2 节点，统一接管服务器相关设置 | [节点连接与自动接管](#comfyui-node-setup) |
| 检查 `gpu_layers=-1`、`codec_backend=Auto` 和两个分句开关均为 `true` | [关键默认参数](#key-setting-checks) |
| 区分直接选择已保存音色与 `Voice Input` 外接音色覆盖 | [两种节点连接示意](#illustrated-workflows) |

## 主要功能

- 在 ComfyUI 内管理本地 s2.cpp HTTP 服务：路径扫描、启动/停止、状态查询、独立 Server Monitor。
- **Fish S2 Voice Create**：使用参考音频及对应文字生成、保存可复用的 `.s2voice` 音色。
- **Fish S2 Generate**：1–10 位角色；支持 `[speakerN]` 标签、内置音色下拉选择及外部音色覆盖。
- 分段生成、任务日志、WAV 预览与下载、最终响度及峰值处理。
- 尽可能使用 ComfyUI 原生 Widgets；Server/Task Monitor 为独立窗口。

**独立项目：** 并非 Fish Audio 或 ComfyUI 官方产品。仓库**不包含** GGUF 权重、tokenizer、s2.cpp 二进制/源代码、第三方语音样本或用户音色。需自行从上游取得并遵循对应许可。

## 安装前提与插件安装

- 已安装 ComfyUI，使用其自带/对应的 Python 环境；原插件代码依赖 `torch`、`numpy`、`scipy`、`aiohttp` 等组件。
- 已独立安装并测试可用的 [s2.cpp](https://github.com/rodrigomatta/s2.cpp)，以及 [S2-Pro GGUF](https://huggingface.co/rodrigomt/s2-pro-gguf)、`tokenizer.json` 和音色目录。
- 开发环境主要为 **Windows + NVIDIA CUDA**；不宣称其他组合已经回归通过。
- FFmpeg 可处理部分压缩参考音频，**不是编译 s2.cpp 的必需工具**；插件会尝试自动寻找。

**安装步骤：** 备份原插件目录及工作流 JSON → 退出 ComfyUI → 将本仓库文件放到 `ComfyUI/custom_nodes/ComfyUI-FishS2-GGUF/`（Portable 版位于 `ComfyUI_windows_portable/ComfyUI/custom_nodes/`）→ 不要并排安装多个同名插件 → 重启 ComfyUI 并按 `Ctrl+F5` 强制刷新。

若 s2.cpp 尚未验证，请先看 [中文 Windows 编译与目录避坑指南](INSTALL_WINDOWS_S2CPP.zh-CN.md) / [English build guide](INSTALL_WINDOWS_S2CPP.md)；应当先独立生成可播放 WAV，再接入节点。

## Illustrated Workflows

**推荐做法：** 先在 **Fish S2 Server Control** 中完成路径、端口与 GPU 参数配置，再将其 `server_config` 输出连接到工作流中其他带有 `server_config` 输入的 Fish S2 节点。下游节点会复用对应的服务器配置，避免重复填写路径或服务地址。各节点具体接管范围见 [ComfyUI 节点设置](#comfyui-node-setup)。

### 方案 A：直接选择已保存的音色

![模式 A：Generate 下拉选择已有音色，本地图中绝对路径已遮挡。](docs/images/01-saved-voice-selection.png)

将 **Fish S2 Server Control** 的 `server_config` 连接到 **Fish S2 Generate** 的 `server_config`。在 Generate 的 `selected_voice_1` 中选择已有音色；使用 **Refresh Voices** 主动刷新列表。**Fish S2 Voice Create** 可制作 `.s2voice` 音色，并分别连接 Server Control。图中紫色的 **Voice Input 未接入 Generate**，因此并未覆盖内置音色选择。

### 方案 B：Voice Input 外接音色覆盖

![模式 B：Voice Input 输出连到 Generate 的 voice_1，本地图中绝对路径已遮挡。](docs/images/02-external-voice-input.png)

Server Control 的 `server_config` 连接至 Voice Input；将 **Voice Input 的音色输出**连到 Generate 的 `voice_1`。Generate 会提示 **External voice connected**，外接音色优先于 `selected_voice_1`。多角色时还可连接更多 `voice_N`。

> 两图只用来示意连接。图片中服务状态为 **STOPPED**，不能把截图解读为服务正常运行的证明。音频字段为空是演示状态。本地绝对路径已替换为不可逆的遮挡像素。

## ComfyUI Node Setup

**这一节适用于已经独立验证 s2.cpp CLI 和 HTTP Server 可用的用户。**

> **强烈建议：先配服务器，再连其他节点。** 在 **Fish S2 Server Control** 中核对目录、服务地址和 GPU 参数后，将其 **`server_config` 输出**（不是 `server_url` 输出）连接到 **Fish S2 Generate**、**Fish S2 Voice Create** 和 **Fish S2 Voice Input** 对应的 `server_config` 输入（只连接实际使用的节点即可）。连接后会自动接管相关配置：Generate 使用服务器提供的 HTTP 地址，并隐藏本地 `server_url`；Voice Create 自动复用 `executable`、`model`、`tokenizer`、`voice_dir`、`dll_dir`、`cuda_device`、`gpu_layers`，并隐藏这些重复控件；Voice Input 则以连接的服务器音色目录作为已保存音色的来源。Voice Input 有时能从其连接的 Generate 节点间接推断目录，但**显式连接**更清晰，尤其适合多个服务器并存的工作流。**连接本身不会自动启动服务器**，仍需在 Server Control 中主动启动。

1. 添加 **Fish S2 Server Control**。`root_dir` 填 s2.cpp 源码根目录，点击 **Scan Components**；逐个核对 `executable`、`Model`、`Voice Library`、`tokenizer`、`dll_dir`。插件只是扫描部分候选目录，不会全盘搜索。不匹配时手动填写真实路径；再次改动 `root_dir` 可能重新扫描并覆盖手工选项。
2. 在 NVIDIA CUDA 环境，建议从 `host=127.0.0.1`、`port=3030`、`cuda_device=0`、`gpu_layers=-1`、`codec_backend=Auto` 开始。确保没有其他 s2.cpp 实例占用端口。点击 **Start Server**，通过 **Refresh Status / Open Monitor** 观察实际服务日志。
3. 添加 **Fish S2 Generate**，将 **Server Control → Generate** 的 `server_config` 端口相连（不要误用 `server_url` 输出）。此时 Generate 的服务地址由 Server Control 提供，原 `server_url` 控件会自动隐藏。设置 `speaker_count`，按 **Refresh Voices** 选音色；如需外接/临时音色，按方案 B 接入 **Fish S2 Voice Input**。
4. 填一小段测试文字并点击 **Queue Prompt**；可在 **Open Task Monitor** 查看任务状态，成功后点击 **Preview WAV / Download WAV**。
5. 如需保存新音色，建议同样连接 **Server Control → Voice Create** 的 `server_config`，由其统一提供可执行文件、模型、Tokenizer、目录与 GPU 配置；再提供参考音频和准确的 transcript 生成 `.s2voice`。使用 **Voice Input** 时也推荐直接连接 Server Control 的 `server_config`，以定位对应音色目录。完成后在 Generate 刷新音色列表。

```text
[speaker1] 你好，这是第一位说话人。
[speaker2] 你好，这是第二位说话人。
```

单角色也可直接输入普通文本；多角色时每行应以 `[speakerN]` 标签开头。**旧工作流会保留之前保存的参数值，不一定自动更新成新版本默认值。**

## Key Setting Checks

| 控件 | 新节点默认值 | 含义 |
|---|---|---|
| Server `gpu_layers` | `-1` | CUDA 模式卸载全部可用 Transformer 层；`0` 不卸载这些层。 |
| Server `codec_backend` | `Auto` | 由 s2.cpp benchmark 选择 Codec 后端。 |
| Generate `per_sentence` | `true` | 每条 `[speakerN]` 台词独立提交；插件不进一步按句号拆分。 |
| Generate `let_server_segment_sentences` | `true` | 请求服务端内部继续按句子拆分，向 s2.cpp 发送 `segment_sentences=true`。 |
| Generate `normalize_loudness` / `target_lufs` | `true` / `-18.0` | 成品音频响度处理。 |
| Generate `peak_limiter` / `peak_ceiling_db` | `true` / `-1.0` | 峰值限制。 |

**已知限制：** 旧工作流的 `widgets_values` 可能因历史版本控件顺序不同而错位，尤其留意 `target_lufs` 和 `peak_ceiling_db`。当前分段缓存**不能保证**重新 Queue 后自动找回旧任务（输出前缀含时间戳）。成功生成后通常会清理缓存片段。s2.cpp 暂无真正的多请求 GPU 并行，忙时额外请求可能返回 HTTP 503。请根据个人环境实测。

## 授权、支持与鸣谢

本插件仓库中有权授权的原创代码和文档采用 [GNU GPL-3.0-only](LICENSE)，**Copyright (C) 2026 xuanhai0**。GPL-3.0 允许商业使用和收费分发，但分发受 GPL 覆盖的修改版时必须遵守相应的开源义务；它并不禁止他人收费出售本插件。此许可证**不改变** Fish Audio S2-Pro 权重、tokenizer、s2.cpp 等第三方材料的授权。Fish Audio Research License 对商用另有要求，完整使用前请检查上游许可及 [第三方声明](THIRD_PARTY_NOTICES.md)。克隆或生成真人声音也需要取得合法授权及同意。

本项目由 **xuanhai0** 业余时间维护，不承诺商业级支持、答复时限或兼容性。**v0.5.3 为首次公开发布版本**，但尚未在全新 Windows 环境完成完整回归测试，不能视作已通过生产环境验证。

**鸣谢：** 本插件的初始创建和后续大量代码迭代、排错、测试方案及文档修订，**显著依赖 OpenAI ChatGPT 的辅助**。由 **xuanhai0** 提出具体需求、确定设计方向、在 Windows 本地测试，并负责维护与发布。ChatGPT 是 AI 辅助工具，不代表 OpenAI 官方维护或赞助本项目。详见 [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)。
