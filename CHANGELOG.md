# Changelog

## v0.5.3-preview (2026-10-23)

Initial public-release **candidate** based on the local v0.5.3 GPU + sentence segmentation hotfix.

- Default `gpu_layers=-1` in Server Control and Voice Create, and permit `-1` in server argument validation.
- Default Codec backend to `Auto`.
- Default `per_sentence=true` and `let_server_segment_sentences=true`.
- Keep ComfyUI-native widgets, separate Server/Task monitors, 1–10 speaker support and Preview/Download WAV.
- Prepare the public repository under the descriptive **ComfyUI-FishS2-GGUF** name, credited to **xuanhai0**; registered node types stay unchanged to preserve existing workflows.

**Validation:** syntax/ZIP integrity checks only for these last default-setting changes; end-to-end Windows tests remain pending. Known issues include old-workflow widget migration and automatic cache resume across separate queue runs.

- Added English/Chinese quick-start READMEs with anchor navigation, Windows s2.cpp compilation/directory guides, privacy-redacted workflow screenshots, and AI assistance acknowledgments. Runtime Python and JavaScript remain unchanged.

- Changed the intended public-source license from MIT to **GNU GPL-3.0-only** before the first GitHub upload; updated both READMEs and third-party notices. The runtime code is unchanged.
