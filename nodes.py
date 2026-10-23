"""Fish Audio S2-Pro / s2.cpp HTTP 节点。

设计原则：
1. 不在 ComfyUI 内加载 GGUF；仅连接已经启动的 s2.cpp HTTP Server。
2. S2Voice Input 将两种互斥来源包装成同一种配置：缓存 voice ID 或参考音频+文字。
3. 主节点按 [speaker1]...[speaker10] 分段、串行请求、拼接并输出标准 ComfyUI AUDIO。
4. 不依赖 requests；使用 Python 标准库 urllib，降低安装门槛。
"""

import io
import os
import time
import hashlib
import subprocess
import shutil
import tempfile
import json
import re
import uuid
import wave
import urllib.error
import urllib.request
import threading
import socket
from urllib.parse import urlsplit
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from .path_utils import local_path, optional_path, executable_path
from .audio_repair import repair_segments


VOICE_TYPE = "S2VOICE_CONFIG"
SAVED_VOICE_TYPE = "S2VOICE"
DEFAULT_URL = "http://127.0.0.1:3030/generate"


def _audio_to_wav_bytes(audio):
    """将 ComfyUI AUDIO ({waveform, sample_rate}) 转为服务器可上传的 PCM WAV。"""
    if not isinstance(audio, dict) or "waveform" not in audio or "sample_rate" not in audio:
        raise ValueError("参考音频必须是 ComfyUI AUDIO 类型（包含 waveform 和 sample_rate）。")
    waveform = audio["waveform"].detach().float().cpu()
    if waveform.ndim == 3:  # [batch, channels, samples]
        if waveform.shape[0] != 1:
            raise ValueError("参考音频仅支持 batch=1，请先选择单条音频。")
        waveform = waveform[0]
    if waveform.ndim == 2:  # [channels, samples] -> mono
        waveform = waveform.mean(dim=0)
    if waveform.ndim != 1 or waveform.numel() == 0:
        raise ValueError("参考音频为空或形状不支持。")
    pcm = (waveform.clamp(-1, 1).numpy() * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(int(audio["sample_rate"]))
        writer.writeframes(pcm)
    return buf.getvalue()


def _wav_to_tensor(data):
    """解码 HTTP 返回的完整 WAV；目前仅支持未压缩 PCM（含常见 16-bit）。"""
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            rate = reader.getframerate()
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            frames = reader.readframes(reader.getnframes())
            if reader.getcomptype() != "NONE":
                raise ValueError("服务器返回压缩 WAV，暂不支持。")
    except (wave.Error, EOFError) as exc:
        raise ValueError("服务器未返回有效 WAV；请确认未启用 chunked PCM 输出。") from exc
    if not frames or channels < 1 or rate <= 0:
        raise ValueError("服务器返回空音频或无效采样率。")
    if width == 2:
        values = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        values = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif width == 4:
        values = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"不支持的 WAV 位深：{width * 8} bit")
    values = values.reshape(-1, channels).T.copy()
    return torch.from_numpy(values), rate  # [channels, samples]


def _encode_form(fields, reference_bytes=None, reference_ext=".wav"):
    """标准 multipart/form-data，避免 Windows PowerShell curl 的 JSON 引号问题。"""
    boundary = "----comfyfishs2" + uuid.uuid4().hex
    parts = bytearray()
    for key, value in fields.items():
        parts.extend((f"--{boundary}\r\n"
                      f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                      f"{value}\r\n").encode("utf-8"))
    if reference_bytes is not None:
        parts.extend((f"--{boundary}\r\n"
                      f'Content-Disposition: form-data; name="reference"; filename="reference{reference_ext}"\r\n'
                      f"Content-Type: {'audio/mpeg' if reference_ext == '.mp3' else 'audio/wav'}\r\n\r\n").encode("utf-8"))
        parts.extend(reference_bytes)
        parts.extend(b"\r\n")
    parts.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(parts), f"multipart/form-data; boundary={boundary}"


def _input_root():
    """从 ComfyUI 获取 input 根目录；不写死任何安装位置。"""
    import folder_paths
    root = Path(folder_paths.get_input_directory()) / "FishS2Input"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_uploaded_file(name):
    """限制读取到 FishS2Input 内，防止文件名中的路径穿越。"""
    if not name or Path(name).name != name:
        raise ValueError("音频文件名无效，请使用节点的上传按钮。")
    path = (_input_root() / name).resolve()
    if path.parent != _input_root().resolve() or not path.is_file():
        raise FileNotFoundError(f"上传音频不存在：{name}，请重新上传。")
    if path.suffix.lower() not in (".wav", ".mp3", ".flac", ".ogg", ".m4a"):
        raise ValueError("仅支持 WAV / MP3 / FLAC / OGG / M4A。")
    if path.stat().st_size == 0:
        raise ValueError("上传音频为空。")
    return path


def _find_ffmpeg():
    """Find ffmpeg without requiring Windows PATH edits."""
    configured = os.environ.get("FISHS2_FFMPEG", "").strip().strip('"')
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    roots = [Path(__file__).resolve().parent, Path.cwd(), Path(os.sys.executable).resolve().parent]
    try:
        import folder_paths
        roots.append(Path(folder_paths.base_path))
    except (ImportError, AttributeError):
        pass
    checked = set()
    for root in roots:
        for parent in (root, *list(root.parents)[:4]):
            if parent in checked:
                continue
            checked.add(parent)
            for candidate in (parent / 'ffmpeg.exe', parent / 'bin' / 'ffmpeg.exe'):
                if candidate.is_file():
                    return str(candidate)
            try:
                for folder in parent.glob('ffmpeg*'):
                    candidate = folder / 'bin' / 'ffmpeg.exe'
                    if candidate.is_file():
                        return str(candidate)
            except OSError:
                pass
    raise RuntimeError("找不到 FFmpeg。可设置 FISHS2_FFMPEG 指向 ffmpeg.exe；无需修改系统 PATH。")


def _write_wav(path, audio, rate):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = audio.detach().float().cpu().clamp(-1, 1)
    interleaved = (pcm.T.contiguous().numpy() * 32767).astype('<i2')
    temporary = path.with_name(path.name + '.partial')
    with wave.open(str(temporary), 'wb') as writer:
        writer.setnchannels(pcm.shape[0])
        writer.setsampwidth(2)
        writer.setframerate(int(rate))
        writer.writeframes(interleaved.tobytes())
    os.replace(temporary, path)


def _date_prefix(prefix):
    from datetime import datetime
    now = datetime.now()
    replacements = {'yyyy': '%Y', 'yy': '%y', 'MM': '%m', 'dd': '%d',
                    'hh': '%H', 'mm': '%M', 'ss': '%S'}
    def replace(match):
        fmt = match.group(1)
        # Avoid replacing a token inside another token.
        pattern = re.compile('|'.join(sorted(replacements, key=len, reverse=True)))
        return now.strftime(pattern.sub(lambda m: replacements[m.group()], fmt))
    return re.sub(r'%date:([^%]+)%', replace, prefix, flags=re.I)


def _safe_output_base(output_root, prefix):
    prefix = _date_prefix(prefix.strip().replace('\\', '/') or 'FishS2/FishS2')
    parts = Path(prefix).parts
    if Path(prefix).is_absolute() or any(part in ('..', '.') or ':' in part for part in parts):
        raise ValueError('文件名前缀必须是 output 内的相对路径。')
    base = (output_root / prefix).resolve()
    if not base.is_relative_to(output_root):
        raise ValueError('输出路径不能越过 ComfyUI output 目录。')
    return base


def _write_manifest(path, payload):
    temporary = path.with_name(path.name + '.partial')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def _reference_bytes(path):
    """Normalize every compressed input format to WAV before handing to s2.cpp."""
    if path.suffix.lower() == '.wav':
        return path.read_bytes(), '.wav'
    ffmpeg = _find_ffmpeg()
    print(f'[FishS2] FFmpeg: {ffmpeg}')
    result = subprocess.run([ffmpeg, '-hide_banner', '-v', 'error', '-i', str(path),
                             '-ac', '1', '-ar', '44100', '-f', 'wav', '-acodec', 'pcm_s16le', 'pipe:1'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if result.returncode or not result.stdout:
        raise RuntimeError('音频转码失败：' + result.stderr.decode('utf-8', 'replace')[-700:])
    return result.stdout, '.wav'


class S2VoiceInput:
    """统一音色节点：缓存、上传、上游 AUDIO、上游 S2VOICE 四种入口。"""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "source_mode": (["参考音频", "缓存 .s2voice"], {"default": "参考音频", "tooltip": '选择参考音频或缓存音色，两种来源互斥。'}),
            "uploaded_audio": ("STRING", {"default": "", "tooltip": "点击节点上的上传按钮；文件自动保存到 input/FishS2Input。"}),
            "reference_text": ("STRING", {"default": "", "multiline": True, "tooltip": "参考音频逐字转录；可连接上游 STRING。"}),
            "voice_id": ("STRING", {"default": "", "tooltip": "缓存音色 ID（例如 speaker1）或 .s2voice 文件名。"}),
        }, "optional": {
            "reference_audio": ("AUDIO", {"tooltip": "上游 AUDIO 优先于本地上传文件。"}),
            "reference_text_input": ("STRING", {"forceInput": True, "tooltip": "上游转录文本，优先于节点内文本框。"}),
            "saved_voice": (SAVED_VOICE_TYPE, {"tooltip": "S2Voice Create 的输出，优先于其他来源。"}),
            "server_config": ("S2_SERVER_CONFIG", {"tooltip": "连接 Fish S2 Server Control，自动扫描其 voice_dir 中的 .s2voice 音色。"}),
        }}

    RETURN_TYPES = (VOICE_TYPE,)
    RETURN_NAMES = ("音色配置",)
    FUNCTION = "build"
    CATEGORY = "Fish S2 GGUF nodes"

    def build(self, source_mode, uploaded_audio, reference_text, voice_id,
              reference_audio=None, reference_text_input=None, saved_voice=None, server_config=None):
        if saved_voice is not None:
            if not isinstance(saved_voice, dict) or not saved_voice.get("voice"):
                raise ValueError("[S2Voice Input] 上游 S2VOICE 无效。")
            return ({"kind": "cached", "voice": saved_voice["voice"],
                     "voice_dir": saved_voice.get("voice_dir", "")},)
        if source_mode == "缓存 .s2voice":
            vid = voice_id.strip()
            if vid.lower().endswith(".s2voice"):
                vid = vid[:-8]
            if not vid:
                raise ValueError("[S2Voice Input] 缓存模式缺少 .s2voice ID。")
            return ({"kind": "cached", "voice": vid},)
        transcript = reference_text_input if reference_text_input is not None else reference_text
        if not str(transcript).strip():
            raise ValueError("[S2Voice Input] 缺少参考文本：填写多行文本或连接上游 STRING。")
        if reference_audio is not None:
            audio_bytes, ext = _audio_to_wav_bytes(reference_audio), ".wav"
        elif uploaded_audio.strip():
            audio_bytes, ext = _reference_bytes(_safe_uploaded_file(uploaded_audio.strip()))
        else:
            raise ValueError("[S2Voice Input] 缺少参考音频：请上传文件或连接上游 AUDIO。")
        return ({"kind": "reference", "reference": audio_bytes,
                 "reference_ext": ext, "reference_text": str(transcript).strip()},)


class S2VoiceCreate:
    """使用 s2.cpp 官方 CLI 的 --save-voice 功能生成持久化音色。"""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "executable": ("STRING", {"default": "s2", "tooltip": "s2.exe 的完整路径或 PATH 中的命令。"}),
            "model": ("STRING", {"default": "", "tooltip": "GGUF 模型完整路径。"}),
            "tokenizer": ("STRING", {"default": "", "tooltip": "tokenizer.json 完整路径。"}),
            "voice_dir": ("STRING", {"default": "voices", "tooltip": "缓存 .s2voice 的目标文件夹，需与 Server 的 voice_dir 对应。"}),
            "dll_dir": ("STRING", {"default": "", "tooltip": "可选：Windows CUDA DLL 目录。"}),
            "voice_name": ("STRING", {"default": "my_voice", "tooltip": '保存的新音色名称，只允许英文、数字、下划线和连字符。'}),
            "uploaded_audio": ("STRING", {"default": "", "tooltip": "使用节点内上传按钮导入参考音频。"}),
            "reference_text": ("STRING", {"default": "", "multiline": True, "tooltip": '参考音频对应的完整转录文本，不能留空。'}),
            "cuda_device": ("INT", {"default": 0, "min": -1, "max": 16, "tooltip": 'CUDA GPU 序号，-1 表示禁用 CUDA（取决于 CLI 支持）。'}),
            "gpu_layers": ("INT", {"default": -1, "min": -1, "max": 200, "tooltip": '-1：自动卸载全部可用 Transformer 层到 GPU；0：不向 GPU 卸载 Transformer 层；正整数：指定 GPU 层数。'}),
            "overwrite": ("BOOLEAN", {"default": False, "tooltip": '是否允许覆盖已有的同名 .s2voice 文件。'}),
        }, "optional": {
            "server_config": ("S2_SERVER_CONFIG", {"tooltip": "可连接 S2 Server Control，自动复用可执行文件、模型和目录配置。"}),
            "voice_config": (VOICE_TYPE, {"tooltip": "连接 S2Voice Input 的统一输出；必须为参考音频模式，优先于本节点内的音频和转录文本。"}),
            "reference_audio": ("AUDIO",),
            "reference_text_input": ("STRING", {"forceInput": True, "tooltip": '上游 STRING 转录文本，优先于节点文本框。'}),
        }}
    RETURN_TYPES = (SAVED_VOICE_TYPE, "STRING")
    RETURN_NAMES = ("S2VOICE", "音色文件路径")
    FUNCTION = "create"
    OUTPUT_NODE = True
    CATEGORY = "Fish S2 GGUF nodes"

    def create(self, executable, model, tokenizer, voice_dir, dll_dir, voice_name, uploaded_audio,
               reference_text, cuda_device, gpu_layers, overwrite,
               reference_audio=None, reference_text_input=None, voice_config=None, server_config=None):
        if server_config is not None:
            executable = server_config.get("executable", executable)
            model = server_config.get("model", model)
            tokenizer = server_config.get("tokenizer", tokenizer)
            voice_dir = server_config.get("voice_dir", voice_dir)
            dll_dir = server_config.get("dll_dir", dll_dir)
            cuda_device = server_config.get("cuda_device", cuda_device)
            gpu_layers = server_config.get("gpu_layers", gpu_layers)
        name = voice_name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            raise ValueError("[S2Voice Create] 音色名称只能包含字母、数字、下划线、连字符。")
        # 统一入口：连接 S2Voice Input 后，不再要求重复上传音频或填写转录文本。
        # 仅允许参考音频配置创建新音色；缓存音色无法反推出原始参考音频和文本。
        if voice_config is not None:
            if not isinstance(voice_config, dict) or voice_config.get("kind") != "reference":
                raise ValueError("[S2Voice Create] 上游 S2Voice Input 必须选择『参考音频』模式；缓存 .s2voice 无法重新创建。")
            transcript = voice_config.get("reference_text", "")
            data = voice_config.get("reference")
            ext = voice_config.get("reference_ext", ".wav")
            if not isinstance(data, bytes) or not data:
                raise ValueError("[S2Voice Create] 上游配置缺少有效参考音频。")
        else:
            transcript = reference_text_input if reference_text_input is not None else reference_text
            if reference_audio is None and not uploaded_audio.strip():
                raise ValueError("[S2Voice Create] 缺少参考音频：连接 S2Voice Input，或提供本地/上游 AUDIO。")
            if reference_audio is not None:
                data, ext = _audio_to_wav_bytes(reference_audio), ".wav"
            else:
                data, ext = _reference_bytes(_safe_uploaded_file(uploaded_audio.strip()))
        if not str(transcript).strip():
            raise ValueError("[S2Voice Create] 缺少参考文本。")
        exe = str(executable_path(executable))
        if not Path(exe).is_file():
            raise FileNotFoundError(f"[S2Voice Create] s2 可执行文件不存在：{exe}")
        for label, value in (("模型", model), ("Tokenizer", tokenizer)):
            if not value.strip() or not local_path(value).is_file():
                raise FileNotFoundError(f"[S2Voice Create] {label}文件不存在：{value}")
        directory = local_path(voice_dir, "音色目录")
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / f"{name}.s2voice"
        if output.exists() and not overwrite:
            raise FileExistsError(f"[S2Voice Create] 音色已存在：{output}。请更换名称，或明确开启 overwrite。")
        with tempfile.TemporaryDirectory(prefix="fishs2_voice_") as temp:
            ref = Path(temp) / ("reference" + ext)
            ref.write_bytes(data)
            if ext.lower() != ".wav":
                ffmpeg = _find_ffmpeg()
                print(f"[FishS2] FFmpeg: {ffmpeg}")
                converted = Path(temp) / "reference_converted.wav"
                conversion = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(ref),
                                             "-ac", "1", "-ar", "24000", str(converted)],
                                            capture_output=True, text=True, timeout=120)
                if conversion.returncode or not converted.is_file():
                    raise RuntimeError("[S2Voice Create] 参考音频转换失败：" + conversion.stderr[-1000:])
                ref = converted
            # CLI 的 --save-voice 目前仍执行一次合成，故需要一个极短的占位文本。
            cmd = [exe, "--model", str(local_path(model, "GGUF 模型")), "--tokenizer", str(local_path(tokenizer, "Tokenizer")),
                   "--prompt-audio", str(ref), "--prompt-text", str(transcript).strip(),
                   "--voice", name, "--voice-dir", str(directory), "--save-voice",
                   "--text", "你好。", "--output", str(Path(temp) / "probe.wav"), "--max-tokens", "128"]
            if cuda_device >= 0:
                cmd += ["--cuda", str(cuda_device), "--gpu-layers", str(gpu_layers)]
            # Windows CUDA DLL 常在 build/bin/Release，继承 PATH；可在系统 PATH 中配置。
            try:
                env = os.environ.copy()
                if dll_dir.strip():
                    if not local_path(dll_dir, "DLL 目录").is_dir():
                        raise FileNotFoundError(f"DLL 目录不存在：{dll_dir}")
                    env["PATH"] = str(local_path(dll_dir, "DLL 目录")) + os.pathsep + env.get("PATH", "")
                run = subprocess.run(cmd, cwd=str(Path(exe).parent), env=env, capture_output=True,
                                     text=True, errors="replace", timeout=600)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("[S2Voice Create] CLI 创建音色超时（600 秒）。") from exc
            if run.returncode or not output.is_file():
                raise RuntimeError("[S2Voice Create] CLI 失败：" + (run.stderr + run.stdout)[-1400:])
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"[S2Voice Create] 音色保存验证失败：{output}")
        return {"ui": {"fishs2_created_voice": [str(output)]}, "result": ({"voice": name, "voice_dir": str(directory)}, str(output))}


def _generate_with_monitor(request, warning_after, server_config=None, health_interval=60, health_timeout=5):
    """持续等待完整 WAV；warning_after 仅为提醒阈值，不中断推理。"""
    done = threading.Event()
    result = {}
    def worker():
        try:
            with urllib.request.urlopen(request, timeout=None) as response:
                result["wav"] = response.read()
        except Exception as exc:
            result["error"] = exc
        finally:
            done.set()
    threading.Thread(target=worker, daemon=True, name="FishS2GenerateHTTP").start()
    endpoint = urlsplit(request.full_url)
    host = endpoint.hostname
    port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    started = time.monotonic()
    checks = []
    warned = False
    while not done.is_set():
        if done.wait(health_interval):
            break
        elapsed = time.monotonic() - started
        if not warned and elapsed >= warning_after:
            checks.append(f"已等待 {elapsed:.0f} 秒，超过提醒阈值 {warning_after} 秒，继续等待生成")
            warned = True
        # 本插件管理的本地进程已退出时可直接报告；远程服务器不据此判断。
        if server_config is not None and host in ('127.0.0.1', 'localhost', '::1'):
            from . import server_control
            with server_control._LOCK:
                process = server_control._PROCESS
                owned = (server_control._CONFIG or {}).get('port') == port
            if owned and process is not None and process.poll() is not None:
                raise RuntimeError(f"本地 s2.cpp 进程已退出（退出码 {process.returncode}）。")
        try:
            with socket.create_connection((host, port), timeout=health_timeout):
                checks.append('TCP 可连接')
        except OSError:
            checks.append('TCP 检查未通过（不代表推理失败）')
        import comfy.model_management
        comfy.model_management.throw_exception_if_processing_interrupted()
    if "error" in result:
        raise result["error"]
    return result["wav"], checks


class _FishS2Pipeline:
    """主节点：多角色文本 -> HTTP WAV -> 拼接 -> ComfyUI AUDIO。"""

    @classmethod
    def INPUT_TYPES(cls):
        optional = {f"voice_{i}": (VOICE_TYPE, {"tooltip": f"对应文本中的 [speaker{i}] 标记。"}) for i in range(1, 11)}
        return {
            "required": {
                "text": ("STRING", {"multiline": True, "default": "[speaker1]你好，这是第一位角色。\n[speaker2]你好，我是第二位角色。", "tooltip": '要合成的文本；多角色节点使用 [speaker1] 等标签切换角色。'}),
                "server_url": ("STRING", {"default": "", "tooltip": "默认留空；连接 server_config 自动使用上游地址，否则手动填写完整 HTTP /generate 地址。"}),
                "temperature": ("FLOAT", {"default": 0.58, "min": 0.01, "max": 2.0, "step": 0.01, "tooltip": '采样温度，越高通常越随机。'}),
                "top_p": ("FLOAT", {"default": 0.88, "min": 0.01, "max": 1.0, "step": 0.01, "tooltip": '核采样累计概率阈值。'}),
                "top_k": ("INT", {"default": 40, "min": 1, "max": 500, "tooltip": '每步采样保留的候选数。'}),
                "max_new_tokens": ("INT", {"default": 8192, "min": 32, "max": 32768, "step": 32, "tooltip": '每段生成最多使用的 token 数，过小可能截断音频。'}),
                "let_server_segment_sentences": ("BOOLEAN", {"default": True, "tooltip": '是否允许 s2.cpp 服务器在每条提交的台词内部再次按句拆分（默认开启）。'}),
                "sentence_pause_ms": ("INT", {"default": 250, "min": 0, "max": 5000, "step": 10, "tooltip": '服务器句间停顿目标值，毫秒，实际可能有波动。'}),
                "between_segments_ms": ("INT", {"default": 350, "min": 0, "max": 10000, "step": 50, "tooltip": '同一角色不同合成片段之间的静音时长，毫秒。'}),
                "between_speakers_ms": ("INT", {"default": 450, "min": 0, "max": 10000, "step": 50, "tooltip": '切换说话人时的静音时长，毫秒。'}),
                "timeout_seconds": ("INT", {"default": 900, "min": 10, "max": 86400, "tooltip": '长时间生成提醒阈值（秒），默认900；只记录提醒，不中断生成请求。为兼容旧工作流保留参数名。'}),
                "max_segment_chars": ("INT", {"default": 1000, "min": 20, "max": 2000, "tooltip": '客户端自动切分文本时每段最大字符数。'}),
                "retry_count": ("INT", {"default": 1, "min": 0, "max": 5, "tooltip": '失败后的额外重试次数，不包括第一次请求。'}),
                "normalize_loudness": ("BOOLEAN", {"default": True, "tooltip": "自适应响度均衡：温和调整不同角色/片段的响度，保留情绪动态。"}),
                "target_lufs": ("FLOAT", {"default": -18.0, "min": -30.0, "max": -10.0, "step": 0.5, "tooltip": "参考响度目标（LUFS；缺少 scipy 时使用 RMS 近似并在日志中注明）。"}),
                "peak_limiter": ("BOOLEAN", {"default": True, "tooltip": "平滑峰值保护，避免输出削波；不能还原已经破音的原始音频。"}),
                "peak_ceiling_db": ("FLOAT", {"default": -1.0, "min": -12.0, "max": -0.1, "step": 0.1, "tooltip": "输出采样峰值上限，dBFS；默认 -1 dBFS。"}),
            },
            "optional": {**optional, "server_config": ("S2_SERVER_CONFIG", {"tooltip": "可选：连接 S2 Server Control，自动采用其 HTTP URL。"})},
        }

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("合成音频", "执行日志")
    FUNCTION = "generate"
    CATEGORY = "Fish S2 GGUF nodes"
    DESCRIPTION = "从 [speaker1] 至 [speaker10] 解析角色，串行调用 s2.cpp HTTP Server 并拼接 WAV。"

    def generate(self, text, server_url, temperature, top_p, top_k, max_new_tokens,
                 let_server_segment_sentences, sentence_pause_ms, between_segments_ms,
                 timeout_seconds, max_segment_chars, retry_count, between_speakers_ms=450, server_config=None,
                 normalize_loudness=True, target_lufs=-18.0, peak_limiter=True, peak_ceiling_db=-1.0, task_dir=None, resume_segments=True, per_sentence=True, **voices):
        if server_config is not None:
            server_url = server_config.get("server_url", server_url)
        url = server_url.strip()
        if not url:
            raise ValueError("请连接 S2 Server Control 的 server_config，或手动填写 server_url。")
        if not url.startswith(("http://", "https://")):
            raise ValueError("server_url 必须以 http:// 或 https:// 开头。")
        if not text.strip():
            raise ValueError("合成文本不能为空。")

        # 使用 [speakerN] 作为分段边界；未标记的开头文本默认属于 speaker1。
        tokens = re.split(r"(\[speaker(?:10|[1-9])\])", text, flags=re.IGNORECASE)
        current = "voice_1"
        segments = []
        for token in tokens:
            match = re.fullmatch(r"\[speaker(10|[1-9])\]", token, flags=re.IGNORECASE)
            if match:
                current = f"voice_{match.group(1)}"
            elif token.strip():
                # per_sentence=True: 每个 speaker 标签后的台词独立请求；不按标点再拆。
                # 关闭后保留旧版连续同角色合并行为。
                if not per_sentence and segments and segments[-1][0] == current:
                    segments[-1] = (current, segments[-1][1] + "\n" + token.strip())
                else:
                    segments.append((current, token.strip()))
        if not segments:
            raise ValueError("未找到可合成的文本。")

        # 预先校验全部角色，避免已经生成部分音频后才发现缺少音色。
        for slot, _ in segments:
            if voices.get(slot) is None:
                raise ValueError(f"[Fish S2 Generate] 文本使用 [{slot.replace('voice_', 'speaker')}]，但对应音色未连接。")
        # 在角色分段之后做长度拆分；只在标点/空格附近断开，尽量避免切断词句。
        split_segments = []
        for slot, segment_text in segments:
            remaining = segment_text.strip()
            while not per_sentence and len(remaining) > max_segment_chars:
                head = remaining[:max_segment_chars]
                breaks = [m.end() for m in re.finditer(r"[。！？!?；;，,\n\s]", head)]
                cut = breaks[-1] if breaks and breaks[-1] >= max_segment_chars // 3 else max_segment_chars
                split_segments.append((slot, remaining[:cut].strip()))
                remaining = remaining[cut:].strip()
            if remaining:
                split_segments.append((slot, remaining))
        segments = split_segments

        params = {
            "stream": True,  # 内部流式合成，但返回完整 WAV；不使用 chunked PCM。
            "segment_sentences": bool(let_server_segment_sentences),
            "sentence_pause_ms": int(sentence_pause_ms),
            "max_new_tokens": int(max_new_tokens),
            "temperature": float(temperature),
            "top_p": float(top_p),
            "top_k": int(top_k),
        }
        import uuid as _uuid
        run_id = _uuid.uuid4().hex
        def emit_progress(event, **data):
            try:
                from server import PromptServer
                PromptServer.instance.send_sync("fishs2:segment", {"run_id": run_id, "event": event, **data})
            except Exception:
                pass
        manifest = None
        if task_dir is not None:
            task_dir = Path(task_dir)
            task_dir.mkdir(parents=True, exist_ok=True)
            identity = {'segments': segments, 'params': params,
                        'voices': {key: {'kind': val.get('kind'), 'voice': val.get('voice'),
                                         'voice_dir': val.get('voice_dir'),
                                         'reference_sha256': hashlib.sha256(val.get('reference', b'')).hexdigest() if val.get('kind') == 'reference' else None,
                                         'reference_text': val.get('reference_text')}
                                   for key, val in voices.items() if isinstance(val, dict)},
                        'repair': [normalize_loudness, target_lufs, peak_limiter, peak_ceiling_db],
                        'pauses': [between_segments_ms, between_speakers_ms]}
            fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            manifest_path = task_dir / 'manifest.json'
            if manifest_path.exists():
                try:
                    old = json.loads(manifest_path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    old = {}
                if old.get('fingerprint') != fingerprint:
                    raise RuntimeError('已有片段的任务参数或文本不匹配；请换用其他任务目录。')
                manifest = old
            else:
                manifest = {'fingerprint': fingerprint, 'segments': [], 'status': 'generating', 'pauses_ms': {'between_segments': between_segments_ms, 'between_speakers': between_speakers_ms}}
            (task_dir / 'segments').mkdir(exist_ok=True)
            _write_manifest(manifest_path, manifest)
        emit_progress("start", segments=[{"speaker": slot.replace("voice_", "speaker"), "text": content} for slot, content in segments],
                      task_subfolder=str(task_dir.relative_to(Path(__import__('folder_paths').get_output_directory()).resolve())).replace('\\', '/') if task_dir else '')
        decoded_segments = []
        target_rate = None
        target_channels = None
        logs = []

        for index, (slot, segment_text) in enumerate(segments, 1):
            started_segment = time.monotonic()
            cache_file = (task_dir / 'segments' / f'{index:04d}.wav') if task_dir else None
            if resume_segments and cache_file and cache_file.is_file():
                try:
                    audio, rate = _wav_to_tensor(cache_file.read_bytes())
                    if target_rate is None:
                        target_rate, target_channels = rate, audio.shape[0]
                    if rate != target_rate or audio.shape[0] != target_channels:
                        raise ValueError('片段采样率或通道数不一致')
                    decoded_segments.append((slot, audio))
                    duration = audio.shape[-1] / rate
                    emit_progress('completed', index=index - 1, duration=round(duration, 3), cached=True)
                    logs.append(f'{index}. {slot} / 已复用 / {duration:.2f}s')
                    emit_progress('log', line=logs[-1])
                    continue
                except Exception as exc:
                    logs.append(f'{index}. 缓存无效，重新生成：{exc}')
            config = voices.get(slot)
            if config is None:
                raise ValueError(f"第 {index} 段使用 [{slot.replace('voice_', 'speaker')}]，但没有连接对应的 S2Voice Input。")
            fields = {"text": segment_text, "params": json.dumps(params, ensure_ascii=False)}
            reference = None
            if config["kind"] == "cached":
                fields["voice"] = config["voice"]
                if config.get("voice_dir"):
                    fields["voice_dir"] = config["voice_dir"]
                source_desc = f"缓存 {config['voice']}"
            elif config["kind"] == "reference":
                fields["reference_text"] = config["reference_text"]
                reference = config["reference"]
                source_desc = "即时参考音频"
            else:
                raise ValueError(f"未知音色配置：{config.get('kind')}")

            payload, content_type = _encode_form(fields, reference, config.get("reference_ext", ".wav"))
            request = urllib.request.Request(url, data=payload, method="POST",
                                             headers={"Content-Type": content_type,
                                                      "Accept": "audio/wav"})
            import comfy.model_management
            for attempt in range(retry_count + 1):
                comfy.model_management.throw_exception_if_processing_interrupted()
                try:
                    wav_bytes, checks = _generate_with_monitor(request, int(timeout_seconds), server_config)
                    if checks:
                        logs.append(f"第 {index} 段健康检查：" + "、".join(checks))
                    break
                except urllib.error.HTTPError as exc:
                    detail = exc.read(1000).decode("utf-8", errors="replace")
                    # 只对临时性服务器繁忙/网关错误重试，避免反复提交非法参数。
                    if exc.code not in (429, 502, 503, 504) or attempt == retry_count:
                        raise RuntimeError(f"s2.cpp HTTP {exc.code}，第 {index} 段：{detail}") from exc
                except urllib.error.URLError as exc:
                    if attempt == retry_count:
                        raise RuntimeError(f"无法连接 s2.cpp Server 或请求超时（第 {index} 段）：{exc}") from exc
                time.sleep(min(1 + attempt, 3))

            audio, rate = _wav_to_tensor(wav_bytes)
            if target_rate is None:
                target_rate = rate
                target_channels = audio.shape[0]
            else:
                if audio.shape[0] != target_channels:
                    # 通道数统一：mono <-> stereo，其他情况明确报错。
                    if target_channels == 1:
                        audio = audio.mean(dim=0, keepdim=True)
                    elif audio.shape[0] == 1:
                        audio = audio.repeat(target_channels, 1)
                    else:
                        raise ValueError("音频通道数不一致，无法自动拼接。")
                if rate != target_rate:
                    # 线性插值用于兼容不同采样率；高保真需求后续可替换专业重采样。
                    n = round(audio.shape[-1] * target_rate / rate)
                    audio = F.interpolate(audio.unsqueeze(0), size=n, mode="linear",
                                          align_corners=False).squeeze(0)
            decoded_segments.append((slot, audio))
            if cache_file:
                _write_wav(cache_file, audio, target_rate)
            duration = audio.shape[-1] / target_rate
            elapsed = time.monotonic() - started_segment
            emit_progress("completed", index=index - 1, duration=round(duration, 3), elapsed=round(elapsed, 3), cached=False)
            logs.append(f"{index}. {slot} / {source_desc} / {duration:.2f}s / 推理 {elapsed:.2f}s / RTF {elapsed / duration:.3f}")
            emit_progress('log', line=logs[-1])
            if manifest is not None:
                manifest['segments'] = [*manifest['segments'][:index-1],
                    {'index': index, 'speaker': slot, 'voice': source_desc, 'text': segment_text,
                     'file': f'segments/{index:04d}.wav', 'duration': duration, 'elapsed': elapsed}]
                _write_manifest(task_dir / 'manifest.json', manifest)

        # 在最终拼接之前进行温和响度均衡；峰值保护不会更改 F0。
        processed, repair_logs = repair_segments(
            decoded_segments, target_rate, normalize_loudness=normalize_loudness,
            target_lufs=target_lufs, peak_limiter=peak_limiter,
            peak_ceiling_db=peak_ceiling_db)
        if normalize_loudness or peak_limiter:
            logs.append("自适应音频修复：" + "; ".join(repair_logs))
        combined = []
        for idx, (slot, audio) in enumerate(processed):
            if idx:
                prev_slot = processed[idx - 1][0]
                gap_ms = between_speakers_ms if slot != prev_slot else between_segments_ms
                silence_n = round(target_rate * gap_ms / 1000)
                combined.append(torch.zeros((target_channels, silence_n), dtype=torch.float32))
            combined.append(audio)
        # ComfyUI AUDIO 标准：[batch, channels, samples] + sample_rate。
        waveform = torch.cat(combined, dim=-1).unsqueeze(0)
        if manifest is not None:
            manifest['pauses_ms'] = {'between_segments': between_segments_ms, 'between_speakers': between_speakers_ms}
            manifest['status'] = 'completed' 
            _write_manifest(task_dir / 'manifest.json', manifest)
        emit_progress("done", count=len(segments))
        logs.append(f"合计：{waveform.shape[-1] / target_rate:.2f}s，{target_rate} Hz")
        return ({"waveform": waveform, "sample_rate": target_rate}, "\n".join(logs))


class FishS2Generate:
    """v0.5.0 main generate node, single/multi-speaker HTTP synthesis and WAV output."""

    @classmethod
    def INPUT_TYPES(cls):
        base = _FishS2Pipeline.INPUT_TYPES()
        required = {"text": base["required"]["text"]}
        required["speaker_count"] = ("INT", {"default": 1, "min": 1, "max": 10, "step": 1,
                                               "tooltip": "使用的角色数量；通过 [speakerN] 切换角色。"})
        # Optional built-in cached voices; external voice_N connections take precedence.
        for i in range(1, 11):
            required[f"selected_voice_{i}"] = ("STRING", {"default": "", "tooltip": f"内置音色选择 speaker{i}：从服务器 voice_dir 的 .s2voice 列表选择；外部 voice_{i} 连线优先。"})
        required["save_filename_prefix"] = ("STRING", {"default": "FishS2/%date:yyyy-MM-dd%_%date:hh-mm-ss%", "tooltip": "ComfyUI output 目录内的文件名前缀。"})
        required['resume_completed_segments'] = ('BOOLEAN', {'default': True, 'tooltip': '相同任务 ID、文本、参数与音色时复用已经保存的片段。'})
        required['delete_segments_after_completion'] = ('BOOLEAN', {'default': True, 'tooltip': '最终 WAV 成功保存后删除本次任务的分段 WAV 和 manifest；生成失败或中断时保留分段以供恢复。'})
        required.update({k:v for k,v in base["required"].items() if k != "text"})
        required["per_sentence"] = ("BOOLEAN", {"default": True, "tooltip": "按每条 [speakerN] 标记的完整台词单独请求服务器；同一角色连续台词不合并。关闭则使用旧版合并规则。"})
        return {"required": required, "optional": base["optional"]}

    RETURN_TYPES = ("AUDIO", "STRING", "STRING")
    RETURN_NAMES = ("合成音频", "执行日志", "WAV 文件路径")
    FUNCTION = "generate"
    CATEGORY = "Fish S2 GGUF nodes"
    OUTPUT_NODE = True
    DESCRIPTION = "统一单/多角色生成，自动保存 WAV 到 ComfyUI output 并在节点中提供试听。"

    def generate(self, text, server_url, temperature, top_p, top_k, max_new_tokens,
                 let_server_segment_sentences, sentence_pause_ms, between_segments_ms,
                 timeout_seconds, max_segment_chars, retry_count, speaker_count,
                 save_filename_prefix, between_speakers_ms=450, server_config=None, resume_completed_segments=True,
                 delete_segments_after_completion=True, normalize_loudness=True, target_lufs=-18.0, peak_limiter=True,
                 peak_ceiling_db=-1.0, per_sentence=True, **voices):
        count = int(speaker_count)
        used = [int(n) for n in re.findall(r"\[speaker(10|[1-9])\]", text, re.IGNORECASE)]
        if any(n > count for n in used):
            raise ValueError(f"文本中存在超过 speaker_count={count} 的角色标记。")
        # Each built-in selection is a saved voice ID. A wired S2Voice Input always wins.
        for i in range(1, count + 1):
            slot = f"voice_{i}"
            selected = str(voices.pop(f"selected_voice_{i}", "") or "").strip()
            if voices.get(slot) is None and selected:
                directory = (server_config or {}).get("voice_dir", "")
                if not directory:
                    raise ValueError(f"speaker{i} 已选择缓存音色，但未连接 Server Control（无法确定 voice_dir）。")
                safe_name = Path(selected).stem
                if not re.fullmatch(r"[A-Za-z0-9_-]+", safe_name):
                    raise ValueError(f"speaker{i} 音色名称无效：{selected}")
                if not (local_path(directory) / (safe_name + ".s2voice")).is_file():
                    raise FileNotFoundError(f"speaker{i} 缓存音色不存在：{safe_name}.s2voice")
                voices[slot] = {"kind": "cached", "voice": safe_name, "voice_dir": directory}
        if voices.get("voice_1") is None:
            raise ValueError("请选择 speaker1 内置音色，或连接 voice_1（Fish S2 Voice Input）。")
        import folder_paths
        output_root = Path(folder_paths.get_output_directory()).resolve()
        target_base = _safe_output_base(output_root, save_filename_prefix)
        task_dir = target_base.parent / (target_base.name + '_segments')
        audio, log = _FishS2Pipeline().generate(
            text=text, server_url=server_url, temperature=temperature, top_p=top_p,
            top_k=top_k, max_new_tokens=max_new_tokens,
            let_server_segment_sentences=let_server_segment_sentences, per_sentence=per_sentence, sentence_pause_ms=sentence_pause_ms,
            between_segments_ms=between_segments_ms, between_speakers_ms=between_speakers_ms,
            timeout_seconds=timeout_seconds, max_segment_chars=max_segment_chars,
            retry_count=retry_count, server_config=server_config,
            normalize_loudness=normalize_loudness, target_lufs=target_lufs,
            peak_limiter=peak_limiter, peak_ceiling_db=peak_ceiling_db,
            task_dir=task_dir, resume_segments=resume_completed_segments, **voices)
        target_base.parent.mkdir(parents=True, exist_ok=True)
        output_path = target_base.with_name(target_base.name + "_" + uuid.uuid4().hex[:10] + ".wav")
        _write_wav(output_path, audio['waveform'][0], audio['sample_rate'])
        # Keep segment cache if synthesis or final WAV writing fails. Only delete
        # this task's own cache directory after the final output is on disk.
        if delete_segments_after_completion and task_dir.is_dir():
            try:
                shutil.rmtree(task_dir)
                log += "\n最终 WAV 已保存；分段缓存已自动删除。"
            except OSError as exc:
                log += f"\n警告：最终 WAV 已保存，但分段缓存清理失败：{exc}"
        relative = output_path.relative_to(output_root)
        item = {"filename": relative.name, "subfolder": "" if str(relative.parent) == "." else str(relative.parent).replace("\\", "/"), "type": "output"}
        return {"ui": {"audio": [item], "fishs2_log": [log], "fishs2_path": [str(output_path)]},
                "result": (audio, log + "\n已保存：" + str(output_path), str(output_path))}

NODE_CLASS_MAPPINGS = {
    "S2VoiceInput": S2VoiceInput,
    "S2VoiceCreate": S2VoiceCreate,
    "FishS2Generate": FishS2Generate,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "S2VoiceInput": "Fish S2 Voice Input",
    "S2VoiceCreate": "Fish S2 Voice Create",
    "FishS2Generate": "Fish S2 Generate",
}
