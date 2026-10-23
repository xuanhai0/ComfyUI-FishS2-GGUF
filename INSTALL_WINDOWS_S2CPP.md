# Building s2.cpp on Windows (NVIDIA CUDA)

**English** | [简体中文](INSTALL_WINDOWS_S2CPP.zh-CN.md) · [Project homepage](README.md)

> **Already verified s2.cpp works independently? → [Skip to ComfyUI Node Setup at the bottom of this guide](#comfyui-node-setup)**, or [open the illustrated node tutorial](README.md#comfyui-node-setup).

**TL;DR:** [Build prerequisites](#build-prerequisites) · [Files and directories](#file-layout-and-actual-build-outputs) · [Independent CLI/HTTP tests](#validate-the-engine-before-comfyui) · [ComfyUI node setup](#comfyui-node-setup).


This guide covers **compiling the separate upstream s2.cpp engine**, locating its actual Windows build artifacts, and validating the engine **before** connecting it to ComfyUI-FishS2-GGUF. The plugin does **not** compile or bundle the engine, model, or CUDA libraries.

- **Upstream project and authoritative documentation:** https://github.com/rodrigomatta/s2.cpp
- **Upstream engine build instructions:** https://github.com/rodrigomatta/s2.cpp#building
- **S2-Pro GGUF model variants:** https://huggingface.co/rodrigomt/s2-pro-gguf
- **Fish Audio model / license:** https://huggingface.co/fishaudio/s2-pro

> **Alpha software (as of this plugin's development in October 2026):** upstream s2.cpp describes itself as an experimental community engine, not production-ready. Commands, flags and build structure may change. Prefer the upstream README for the specific commit you check out. This guide explains the **Windows/MSVC and directory-layout friction points**, not an alternative engine distribution. Build commands below are guidance, **not a tested one-click installer**.

## Build prerequisites

## 1. Prerequisites: check before cloning

Windows + NVIDIA CUDA build:

1. **Git**, to clone the engine and its `ggml` git submodule.
2. **CMake 3.14+**, to configure/build.
3. **Visual Studio 2022 or Build Tools for Visual Studio 2022**, with **Desktop development with C++** (MSVC x64 compiler and Windows SDK). The upstream minimum is MSVC 2019; these commands use the VS 2022 generator as an example.
4. **CUDA Toolkit 12.4+** and an NVIDIA driver suitable for that toolkit. A CUDA-capable GPU is required for the CUDA backend. **The CUDA Toolkit used for compilation is not interchangeable with a Python/PyTorch CUDA wheel.**
5. Enough free disk space for source, GGUF weights and build output.

Open an **x64 Native Tools Command Prompt for VS 2022**, or another terminal where the MSVC environment is initialized. Verify:

```bat
git --version
cmake --version
cl
nvcc --version
```

If `cl` is not recognized, fix the Visual Studio C++ workload or launch a Developer Command Prompt. If `nvcc` is not recognized, install/check the CUDA Toolkit (not just NVIDIA drivers) and check the CUDA environment; do not start changing ComfyUI Python packages as the first step.

## 2. Clone the correct project **including submodules**

```bat
git clone --recurse-submodules https://github.com/rodrigomatta/s2.cpp.git
cd s2.cpp
git submodule status
```

`ggml` is a submodule, not an optional folder. If it is missing or empty, run from the engine root:

```bat
git submodule update --init --recursive
```

A GitHub **Download ZIP** of the upstream source may not include the populated submodule. Use recursive Git clone for this build path.

## 3. Build with MSVC and CUDA (VS 2022 example)

From the **s2.cpp source root**:

```bat
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 -DS2_CUDA=ON
cmake --build build --config Release --parallel 8
```

**Windows-specific distinction:** the upstream examples use Unix-style `$(nproc)` and show `build/s2`. That does **not** mean your Windows binary must be there. The Visual Studio generator is **multi-configuration**: specify `--config Release`, then **locate the actual artifact** rather than assuming its directory. CUDA compilation requires a compatible MSVC/CUDA version pair (upstream notes CUDA >= 12.4 for MSVC 2019+).

Do **not** copy `s2.exe` into the ComfyUI node directory. Keep s2.cpp built separately and point the plugin to its real absolute path.

## File layout and actual build outputs

## 4. Windows directory/layout differences (the common trap)

A recommended organization (illustrative paths only):

```text
s2.cpp/                             <- source root; choose as root_dir
  CMakeLists.txt
  ggml/                             <- populated submodule
  tokenizer.json                    <- provided/obtained for S2 Pro
  models/
    s2-pro-q8_0.gguf                <- downloaded separately
  voices/                           <- create this directory yourself
    sample.s2voice                 <- optional, generated later
  build/
    bin/
      Release/
        s2.exe                      <- one possible MSVC output location
        ggml*.dll                   <- location varies by build
```

The *actual* executable or DLLs can be elsewhere, e.g. `build/Release/s2.exe`, `build/s2.exe`, or another `build/` subdirectory, depending on generator, revision and build configuration. **This tree is not a claim that s2.cpp automatically downloads weights or creates these directories.**

To find the executable **and every GGML DLL** with PowerShell (from the engine root):

```powershell
Get-ChildItem .\build -Recurse -File -Filter s2.exe | Select-Object -ExpandProperty FullName
Get-ChildItem .\build -Recurse -File -Filter 'ggml*.dll' | Select-Object -ExpandProperty FullName
Test-Path .\tokenizer.json
Get-ChildItem .\models -Filter '*.gguf' -ErrorAction SilentlyContinue
Test-Path .\voices
```

If the last test is `False`, create it:

```powershell
New-Item -ItemType Directory -Path .\voices -Force
```

**Match files to their roles**: `s2.exe` is the inference executable; `*.gguf` is a model file; `tokenizer.json` is a separate tokenizer; `ggml*.dll` are dynamic runtime libraries, not the model; `.s2voice` files belong in the selected voice directory. If Windows complains about missing DLLs, use the directory actually containing those DLLs in the plugin's `dll_dir` field, or follow the upstream packaging instructions. Do not guess or download DLLs from unknown websites.

## Validate the engine before ComfyUI

## 5. Verify s2.cpp *without* ComfyUI first

Open PowerShell from the engine root. The following sample assumes the executable was found at `build/bin/Release/s2.exe`. **Replace `$exe` and `$model` with your real paths.**

```powershell
$exe = (Resolve-Path '.\build\bin\Release\s2.exe').Path
$model = (Resolve-Path '.\models\s2-pro-q8_0.gguf').Path
$tokenizer = (Resolve-Path '.\tokenizer.json').Path
& $exe --help
```

If the executable does not start due to `ggml*.dll` errors, locate the DLL folder first. For this PowerShell session only, add that **actual folder** to `PATH`, for example:

```powershell
$dllDir = (Resolve-Path '.\build\bin\Release').Path  # change if your DLLs are elsewhere
$env:PATH = "$dllDir;$env:PATH"
& $exe --help
```

Next test direct synthesis (the first model load may take time):

```powershell
& $exe --model $model --tokenizer $tokenizer --cuda 0 --gpu-layers -1 --codec-auto --text "Hello from s2.cpp." --output '.\smoke-test.wav'
Test-Path '.\smoke-test.wav'
```

Confirm the WAV **plays correctly**, not just that a file exists. If CLI synthesis fails, fix s2.cpp and its dependencies **before** testing ComfyUI.

Finally test HTTP mode, in a *separate foreground PowerShell window*:

```powershell
& $exe --model $model --tokenizer $tokenizer --cuda 0 --gpu-layers -1 --codec-auto --voice-dir '.\voices' --server --host 127.0.0.1 --port 3030
```

In another terminal:

```powershell
Test-NetConnection 127.0.0.1 -Port 3030
curl.exe -X POST http://127.0.0.1:3030/generate --form 'text=Hello from the HTTP server.' --output '.\server-smoke-test.wav'
```

Port connectivity alone is **not** proof of synthesis; check the HTTP request and playable WAV. Stop this standalone server before using the plugin's **Start Server** button, or you will get a port conflict. Keep it bound to `127.0.0.1` for local use.

## 6. Configure paths in the ComfyUI Server Control node

1. Install the plugin under `ComfyUI/custom_nodes/ComfyUI-FishS2-GGUF/`, **not** inside s2.cpp.
2. Set **`root_dir` to the s2.cpp source root**. The plugin scans a *fixed set of common locations* (it is **not** an unlimited recursive scanner). It looks for `s2.exe` under `build/bin/Release`, `build/Release`, `build`, `build/bin` and at the root; for GGUFs **only in `<root>/models`**; and for tokenizer at `<root>/tokenizer.json` or `<root>/models/tokenizer.json`.
3. Check the populated **`executable`, `model`, `tokenizer`, `voice_dir`, and `dll_dir`** fields. When they differ from your actual layout, **paste absolute paths** manually. The voice directory must already exist before the server starts.
4. **Changing `root_dir` again invokes scanning and may overwrite manual path corrections.** Set the root first, then manually correct the fields, then leave `root_dir` unchanged unless you intend to rescan.
5. Set `cuda_device=0`, `gpu_layers=-1`, `codec_backend=Auto` (as appropriate to your system). Start the server and check the separate monitor for actual loading; a green UI indication alone is not a full synthesis test.

No GGUF, tokenizer or `.s2voice` is included with this repository. An existing saved workflow may retain older parameter values despite newer node defaults.

## 7. Build / path troubleshooting quick reference

| Symptom | Check first |
|---|---|
| `cl` not found | VS C++ workload; use x64 Native Tools/Developer Prompt |
| `nvcc` not found | Full CUDA Toolkit version/PATH (>=12.4 per upstream), not PyTorch CUDA |
| `ggml/CMakeLists.txt` missing | Recursive clone / `git submodule update --init --recursive` |
| CMake MSVC/CUDA compiler incompatibility | MSVC version supported by installed CUDA Toolkit; upstream build notes |
| `s2.exe` missing after success | Release vs Debug and generator-dependent output path; recursive file search |
| DLL missing on startup | Find `ggml*.dll`; configure `dll_dir` / relevant runtime libraries |
| Model not found in plugin picker | Put GGUF under `<root>/models` or manually enter its real path |
| `tokenizer.json` not found | Check repository root and `models/`; set direct path |
| Voice list empty / server refuses to start | Create the voice directory; verify `voice_dir` path and `.s2voice` files |
| Server immediately exits | Check terminal/Server Monitor output for missing files/DLLs or compile flags |
| Port 3030 in use | Stop prior standalone s2.cpp process; avoid duplicate server instances |
| Interface says running, but no WAV | Try direct CLI and HTTP smoke tests; check backend logs |

**Other known limitations:** s2.cpp is alpha. Upstream currently handles only one active synthesis at a time and may return HTTP 503 to concurrent calls. FFmpeg matters for compressed input files; it is **not** a prerequisite for compiling s2.cpp. Model/backend performance varies. For full limitations and license restrictions, see the upstream repository and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

**Scope note:** This guide was prepared from upstream documentation and plugin source inspection, incorporating development-era Windows layout observations. The exact MSVC/CUDA commands have **not** been freshly executed on a clean Windows system for this release.


## ComfyUI Node Setup

If CLI and HTTP smoke tests above passed, you can now [jump directly to the illustrated English node guide](README.md#comfyui-node-setup). Add **Fish S2 Server Control**; use `Scan Components` to confirm the `executable`, `model`, `tokenizer`, `voice_dir`, and `dll_dir`; connect its `server_config` output to **Fish S2 Generate**. Start and monitor the server, then run a short synthesis. The two screenshot examples cover built-in and externally connected voice selection: [workflow A](README.md#illustrated-workflows).
