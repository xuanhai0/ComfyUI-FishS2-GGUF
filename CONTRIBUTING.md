# Contributing and bug reports

Thanks for considering a contribution! This is a spare-time hobby project, without guaranteed support or release cadence.

When reporting a reproducible bug, please include:

1. ComfyUI version, operating system, Python version, GPU/VRAM.
2. ComfyUI-FishS2-GGUF version/commit, s2.cpp version/commit, GGUF variant and whether the server starts.
3. Exact node settings needed to reproduce (exclude absolute personal/private paths).
4. Relevant **sanitized** ComfyUI console and s2.cpp logs, and the expected/observed behavior.
5. Whether it reproduces with a new workflow, or only a workflow saved in an earlier plugin version.

Please **do not upload** private reference voices, confidential prompts, API keys, personal paths, or third-party copyrighted material without authorization.

Pull requests that preserve saved-workflow node mappings, use native ComfyUI widgets and avoid custom in-node HTML overlays are preferred. Please separate runtime-tested changes from static-only checks.
