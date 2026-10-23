# Third-party licenses and attribution

This repository contains **only the ComfyUI integration plugin source code and related documentation**. It does not redistribute Fish Audio models, s2.cpp source/binaries, tokenizer data, GGUF model weights, or cloned voice files.

## Fish Audio S2-Pro and s2.cpp

- [Fish Audio S2-Pro model and license](https://huggingface.co/fishaudio/s2-pro/blob/main/LICENSE.md)
- [s2.cpp repository and licensing details](https://github.com/rodrigomatta/s2.cpp)
- [Fish Audio website](https://fish.audio)

> This model is licensed under the Fish Audio Research License, Copyright © 39 AI, INC. All Rights Reserved.

**Built with Fish Audio.** Fish Audio owns its model-related rights and marks. ComfyUI-FishS2-GGUF (maintained by xuanhai0) is an independent third-party integration and is not endorsed by Fish Audio.

The S2-Pro weights are licensed for research and non-commercial use under the Fish Audio Research License. Commercial use needs a separate written agreement with Fish Audio. The s2.cpp project's documentation also describes its engine source as a derivative covered by the same license.

The [GNU GPL-3.0-only license](LICENSE) of this repository applies **only to its original plugin code and documentation, to the extent the maintainer has the rights to license them**; Copyright (C) 2026 xuanhai0. This GPL-3.0 license **does not grant rights to any third-party materials or assets obtained separately**, including s2.cpp, S2-Pro weights, tokenizer data, or voices. Review and comply with the upstream licenses in their entirety. Commercial use of separately licensed components is subject to those components' terms, irrespective of this plugin's GPL license.

## ComfyUI, Python and FFmpeg dependencies

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) — separate project; see its license.
- [FFmpeg](https://ffmpeg.org/legal.html) — separate installation; license depends on build and enabled components.
- Python dependencies (e.g. `torch`, `numpy`, `scipy`, `aiohttp`) — use their respective licenses.

If you redistribute a copy of Fish Audio materials (which this repository does **not** do), comply with their complete redistribution and notice requirements as well.

## AI-assisted development

The plugin was created and extensively revised with substantial assistance from **OpenAI ChatGPT**. See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md). This is not an OpenAI project or an OpenAI endorsement.
