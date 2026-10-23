"""s2.cpp 子进程管理与 ComfyUI 控制 API。所有配置均由用户在节点界面提供。"""
import os
import atexit
import subprocess
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from urllib.parse import urlparse
from .path_utils import local_path, optional_path, executable_path

_LOCK = threading.RLock()
_PROCESS = None
_LOGS = deque(maxlen=100)
_CONFIG = None
_JOB_HANDLE = None


def _attach_windows_job(process):
    """Bind child lifetime to ComfyUI using Windows JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.

    The handle is intentionally kept open in this Python process; the OS closes
    it even on an unclean interpreter exit. No third-party dependencies.
    """
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    SIZE_T = ctypes.c_size_t
    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                    ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD),
                    ('MinimumWorkingSetSize', SIZE_T),
                    ('MaximumWorkingSetSize', SIZE_T),
                    ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', SIZE_T),
                    ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]
    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                     'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ('IoInfo', IO_COUNTERS),
                    ('ProcessMemoryLimit', SIZE_T),
                    ('JobMemoryLimit', SIZE_T),
                    ('PeakProcessMemoryUsed', SIZE_T),
                    ('PeakJobMemoryUsed', SIZE_T)]
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                              ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        return job
    except BaseException:
        kernel.CloseHandle(job)
        raise


def _close_windows_job(job):
    if job is not None and os.name == 'nt':
        import ctypes
        ctypes.WinDLL('kernel32', use_last_error=True).CloseHandle(job)


def _cleanup_on_exit():
    """Normal ComfyUI shutdown fallback (Job Object also handles abrupt exit)."""
    global _JOB_HANDLE
    with _LOCK:
        process = _PROCESS
        job = _JOB_HANDLE
        _JOB_HANDLE = None
    if job is not None:
        _close_windows_job(job)
    elif process is not None and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass


atexit.register(_cleanup_on_exit)


def _log(message):
    with _LOCK:
        _LOGS.append(f"{time.strftime('%H:%M:%S')} {message}")


def _reader(process):
    """后台读取日志，避免 stdout 缓冲区满导致子进程阻塞。"""
    try:
        for line in iter(process.stdout.readline, ''):
            if line:
                _log(line.rstrip())
    except Exception as exc:
        _log(f"日志读取异常: {exc}")
    finally:
        try:
            process.stdout.close()
        except Exception:
            pass


def _check_host(host):
    # 控制节点只负责本机启动。远程 HTTP Server 仍可在生成节点中单独填写 URL。
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('本机进程控制仅允许 127.0.0.1 / localhost / ::1；远程 Server 请直接在生成节点填写 URL。')


def _probe(host, port, timeout=1.0):
    """检测 TCP 端口是否已有 HTTP 服务；不将端口开放等同于 s2.cpp 就绪。"""
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def status(host='127.0.0.1', port=3030):
    with _LOCK:
        process = _PROCESS
        owned = process is not None and process.poll() is None
        exit_code = process.poll() if process is not None else None
        lines = list(_LOGS)[-25:]
    listening = _probe(host, port)
    if owned and listening:
        state = 'running'
    elif owned:
        state = 'starting'
    elif listening:
        state = 'external_or_occupied'
    elif process is not None and exit_code is not None:
        state = 'exited'
    else:
        state = 'stopped'
    return {'state': state, 'owned': owned, 'port_listening': listening,
            'exit_code': exit_code, 'logs': lines,
            'url': f'http://{host}:{port}/generate'}


def start(config):
    global _PROCESS, _CONFIG, _JOB_HANDLE
    host = str(config.get('host', '127.0.0.1')).strip()
    port = int(config.get('port', 3030))
    _check_host(host)
    if not 1 <= port <= 65535:
        raise ValueError('端口必须在 1–65535 之间。')
    exe = executable_path(config['executable'])
    model = local_path(config['model'], 'GGUF 模型')
    tokenizer = local_path(config['tokenizer'], 'Tokenizer')
    voices = local_path(config['voice_dir'], '音色目录')
    for label, path in [('s2.exe', exe), ('GGUF 模型', model), ('tokenizer', tokenizer)]:
        if not path.is_file():
            raise FileNotFoundError(f'{label} 不存在：{path}')
    if not voices.is_dir():
        raise FileNotFoundError(f'音色目录不存在：{voices}')
    gpu_layers = int(config.get('gpu_layers', -1))
    codec_backend = str(config.get('codec_backend', 'Auto')).strip().lower()
    codec_flags = {'follow backend': '--codec-follow-backend', 'auto': '--codec-auto', 'cpu': '--codec-cpu'}
    if codec_backend not in codec_flags:
        raise ValueError(f'未知 Codec 后端选项：{codec_backend}')
    cuda_device = int(config.get('cuda_device', 0))
    if gpu_layers < -1:
        raise ValueError('gpu_layers 仅允许 -1（自动全部层）或 0 及以上。')
    if cuda_device < 0:
        raise ValueError('CUDA 设备序号不能为负数。')
    with _LOCK:
        if _PROCESS is not None and _PROCESS.poll() is None:
            raise RuntimeError('本插件已经启动了一个 s2.cpp 进程，请先停止再更改配置。')
        if _JOB_HANDLE is not None:
            _close_windows_job(_JOB_HANDLE)
            _JOB_HANDLE = None
        if _probe(host, port):
            raise RuntimeError(f'{host}:{port} 已有服务监听；为避免冲突不会重复启动。')
        cmd = [str(exe), '--model', str(model), '--tokenizer', str(tokenizer),
               '--cuda', str(cuda_device), '--gpu-layers', str(gpu_layers),
               codec_flags[codec_backend],
               '--voice-dir', str(voices), '--server', '--host', host, '--port', str(port)]
        # Windows DLL 目录可能与 exe 所在目录不同；提供可编辑路径，而非写死本机配置。
        env = os.environ.copy()
        dll_dir = str(config.get('dll_dir', '')).strip()
        if dll_dir:
            dll_path = local_path(dll_dir, "DLL 目录")
            if not dll_path.is_dir():
                raise FileNotFoundError(f'DLL 目录不存在：{dll_path}')
            env['PATH'] = str(dll_path) + os.pathsep + env.get('PATH', '')
        show_console = bool(config.get('show_console', False)) and os.name == 'nt'
        flags = (getattr(subprocess, 'CREATE_NEW_CONSOLE', 0) if show_console else getattr(subprocess, 'CREATE_NO_WINDOW', 0)) if os.name == 'nt' else 0
        _LOGS.clear()
        _log('正在启动：' + str(exe))
        _PROCESS = subprocess.Popen(cmd, cwd=str(exe.parent), env=env,
                                    stdout=None if show_console else subprocess.PIPE,
                                    stderr=None if show_console else subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace',
                                    bufsize=1, creationflags=flags)
        try:
            _JOB_HANDLE = _attach_windows_job(_PROCESS)
        except Exception as exc:
            # Fail closed: never leave a new untracked server running.
            _log(f'无法绑定服务器进程生命周期：{exc}')
            _PROCESS.terminate()
            _PROCESS.wait(timeout=5)
            _PROCESS = None
            raise RuntimeError(f'无法绑定服务器进程生命周期，已终止新进程：{exc}') from exc
        _CONFIG = {'host': host, 'port': port}
        if not show_console:
            threading.Thread(target=_reader, args=(_PROCESS,), daemon=True).start()
        else:
            _log("已开启独立控制台；服务器详细日志请在控制台窗口查看。")
    return status(host, port)


def stop():
    global _PROCESS, _JOB_HANDLE
    with _LOCK:
        process = _PROCESS
        if process is None or process.poll() is not None:
            job = _JOB_HANDLE
            _JOB_HANDLE = None
            _close_windows_job(job)
            return {'message': '没有由本插件启动且仍在运行的 Server。'}
        _log('正在停止本插件启动的 Server...')
        process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        _log('Server 未及时退出，已强制终止本插件启动的进程。')
    with _LOCK:
        job = _JOB_HANDLE
        _JOB_HANDLE = None
    _close_windows_job(job)
    _log(f'Server 已退出，退出码 {process.returncode}')
    return {'message': '已停止本插件启动的 Server。'}


class S2ServerControl:
    """配置与 URL 传递节点；启动/停止按钮由 web 前端调用 API，不触发工作流执行。"""
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'root_dir': ('STRING', {'default': '', 'tooltip': 's2.cpp 根目录；修改后自动重新扫描并覆盖所有组件路径（包括此前手动修改的值）。'}),
            'executable': ('STRING', {'default': 's2.exe', 'tooltip': 's2.exe 完整路径，可编辑。'}),
            'model': ('STRING', {'default': 'models/s2-pro-q8_0.gguf', 'tooltip': 'GGUF 文件完整路径。'}),
            'tokenizer': ('STRING', {'default': 'tokenizer.json', 'tooltip': 'tokenizer.json 路径。'}),
            'voice_dir': ('STRING', {'default': 'voices', 'tooltip': '服务端 .s2voice 目录。'}),
            'dll_dir': ('STRING', {'default': '', 'tooltip': '可选：ggml DLL 所在目录，启动时加入子进程 PATH。'}),
            'host': ('STRING', {'default': '127.0.0.1', 'tooltip': '本机服务监听地址，推荐 127.0.0.1。'}),
            'port': ('INT', {'default': 3030, 'min': 1, 'max': 65535, 'tooltip': 'HTTP Server 监听端口，需与生成节点 URL 一致。'}),
            'cuda_device': ('INT', {'default': 0, 'min': 0, 'max': 16, 'tooltip': 'CUDA GPU 序号；通常第一块显卡为 0。'}),
            'gpu_layers': ('INT', {'default': -1, 'min': -1, 'max': 256, 'tooltip': '-1：自动卸载全部可用的 Transformer 层到 GPU；0：不向 GPU 卸载 Transformer 层；正整数：指定 GPU 层数。'}),
            'show_console': ('BOOLEAN', {'default': False, 'tooltip': 'Windows 调试选项：显示独立服务器控制台窗口。'}),
            'codec_backend': (['Auto', 'Follow backend', 'CPU'], {'default': 'Auto', 'tooltip': 'Codec 解码后端；Follow backend 优先跟随 CUDA 等推理后端。更改后需重启服务器。'}),
        }}

    RETURN_TYPES = ('STRING', 'S2_SERVER_CONFIG')
    RETURN_NAMES = ('server_url', 'server_config')
    FUNCTION = 'get_url'
    CATEGORY = 'Fish S2 GGUF nodes'
    DESCRIPTION = '填写路径，点击节点内启动/停止/刷新按钮；将 server_url 连接到生成节点。'

    def get_url(self, root_dir, executable, model, tokenizer, voice_dir, dll_dir, host, port, cuda_device, gpu_layers, show_console=False, codec_backend='Auto'):
        _check_host(host.strip())
        url = f'http://{host.strip()}:{port}/generate'
        return (url, {'server_url': url, 'executable': executable, 'model': model, 'tokenizer': tokenizer, 'voice_dir': voice_dir, 'dll_dir': dll_dir, 'cuda_device': cuda_device, 'gpu_layers': gpu_layers, 'codec_backend': codec_backend})




def scan_root(root):
    """只扫描指定项目目录中的已知位置，不递归遍历磁盘。"""
    base = local_path(root, "s2.cpp 根目录")
    if not base.is_dir():
        raise FileNotFoundError(f"s2.cpp 根目录不存在：{base}")
    def first(paths, is_dir=False):
        for item in paths:
            candidate = base / item
            if candidate.is_dir() if is_dir else candidate.is_file():
                return str(candidate.resolve())
        return ""
    exe = first(['build/bin/Release/s2.exe', 'build/Release/s2.exe', 'build/s2.exe', 'build/bin/s2', 'build/s2', 's2.exe', 's2'])
    models = sorted((base / 'models').glob('*.gguf')) if (base / 'models').is_dir() else []
    # Every immediate directory under s2.cpp is selectable, including future voices2.
    # Do not recurse into nested directories or filter by folder name.
    voices = sorted((p.resolve() for p in base.iterdir() if p.is_dir()),
                    key=lambda p: p.name.casefold())
    preferred = next((m for m in models if m.name.lower() == 's2-pro-q8_0.gguf'), None)
    model = str((preferred or models[0]).resolve()) if models else ''
    return {'root_dir': str(base), 'executable': exe, 'model': model,
            'model_candidates': [str(m.resolve()) for m in models],
            'tokenizer': first(['tokenizer.json', 'models/tokenizer.json']),
            'voice_dir': str((base / 'voices').resolve()) if (base / 'voices').is_dir() else (str(voices[0]) if voices else ''),
            'voice_candidates': [str(p) for p in voices],
            'dll_dir': first(['build/bin/Release', 'build/Release', 'build/bin'], is_dir=True),
            'missing': [k for k,v in [('executable',exe),('tokenizer',first(['tokenizer.json', 'models/tokenizer.json']))] if not v]}

def register_routes():
    """注册 ComfyUI 路由；只有点击节点按钮才执行本机进程操作。"""
    from aiohttp import web
    from server import PromptServer
    routes = PromptServer.instance.routes
    @routes.get('/fishs2/voices/list')
    async def list_voices(request):
        try:
            directory = local_path(request.query.get('directory', ''), '音色目录')
            if not directory.is_dir():
                return web.json_response({'ok': True, 'voices': []})
            names = sorted((f.stem for f in directory.glob('*.s2voice') if f.is_file()), key=str.casefold)
            return web.json_response({'ok': True, 'voices': names})
        except Exception as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=400)


    @routes.post('/fishs2/server/scan')
    async def api_scan(request):
        try:
            payload = await request.json()
            return web.json_response({'ok': True, **scan_root(payload.get('root_dir', ''))})
        except Exception as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=400)

    @routes.post('/fishs2/server/start')
    async def api_start(request):
        try:
            data = await request.json()
            result = start(data)
            return web.json_response({'ok': True, **result})
        except Exception as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=400)

    @routes.post('/fishs2/server/stop')
    async def api_stop(request):
        try:
            return web.json_response({'ok': True, **stop()})
        except Exception as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=500)

    @routes.get('/fishs2/server/status')
    async def api_status(request):
        try:
            host = request.query.get('host', '127.0.0.1')
            port = int(request.query.get('port', '3030'))
            _check_host(host)
            return web.json_response({'ok': True, **status(host, port)})
        except Exception as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=400)

    @routes.post('/fishs2/output/open-folder')
    async def api_open_output_folder(request):
        """Open only files under ComfyUI's output root (local desktop installation)."""
        from aiohttp import web
        import folder_paths
        try:
            data = await request.json()
            root = Path(folder_paths.get_output_directory()).resolve()
            path = Path(str(data.get('path', ''))).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('只能打开 ComfyUI output 中已存在的文件。')
            if os.name == 'nt':
                subprocess.Popen(['explorer.exe', '/select,', str(path)])
            elif __import__('sys').platform == 'darwin':
                subprocess.Popen(['open', '-R', str(path)])
            else:
                subprocess.Popen(['xdg-open', str(path.parent)])
            return web.json_response({'ok': True})
        except Exception as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=400)

    @routes.get('/fishs2/task/completed.wav')
    async def api_completed_audio(request):
        """Export a snapshot of contiguous completed segments; no GPU inference."""
        from aiohttp import web
        import folder_paths
        import io
        import json
        import wave
        try:
            subfolder = request.query.get('task', '')
            output_root = Path(folder_paths.get_output_directory()).resolve()
            task = (output_root / subfolder).resolve()
            if not task.is_relative_to(output_root) or not task.is_dir():
                raise ValueError('任务目录无效')
            manifest = json.loads((task / 'manifest.json').read_text(encoding='utf-8'))
            pieces = []
            rate = channels = width = None
            previous = None
            pauses = manifest.get('pauses_ms', {})
            for index, info in enumerate(manifest.get('segments', []), 1):
                if info.get('index') != index:
                    break
                file = (task / 'segments' / f'{index:04d}.wav').resolve()
                if not file.is_relative_to(task) or not file.is_file():
                    break
                with wave.open(str(file), 'rb') as wav:
                    fmt = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
                    if rate is None:
                        rate, channels, width = fmt
                    elif fmt != (rate, channels, width):
                        break
                    payload = wav.readframes(wav.getnframes())
                if previous is not None:
                    pause = pauses.get('between_segments', 350) if previous == info['speaker'] else pauses.get('between_speakers', 450)
                    pieces.append(bytes(round(rate * max(0, pause) / 1000) * channels * width))
                pieces.append(payload)
                previous = info['speaker']
            if not pieces:
                raise ValueError('暂无连续已完成的 WAV 片段')
            output = io.BytesIO()
            with wave.open(output, 'wb') as writer:
                writer.setnchannels(channels)
                writer.setsampwidth(width)
                writer.setframerate(rate)
                writer.writeframes(b''.join(pieces))
            return web.Response(body=output.getvalue(), content_type='audio/wav',
                                headers={'Content-Disposition': 'attachment; filename="FishS2-completed.wav"',
                                         'Cache-Control': 'no-store'})
        except Exception as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=400)

    @routes.post('/fishs2/voices/list')
    async def api_voice_list(request):
        """仅列出明确指定的本地音色目录中的 .s2voice 文件名。"""
        try:
            payload = await request.json()
            raw = str(payload.get('voice_dir', '')).strip()
            if not raw:
                raise ValueError('Server Control 的 voice_dir 为空。')
            directory = local_path(raw, '音色目录')
            if not directory.is_dir():
                raise ValueError(f'音色目录不存在：{directory}')
            voices = sorted((f.stem for f in directory.iterdir()
                             if f.is_file() and f.suffix.lower() == '.s2voice'), key=str.casefold)
            return web.json_response({'ok': True, 'voices': voices})
        except Exception as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=400)

    @routes.post('/fishs2/audio/upload')
    async def api_audio_upload(request):
        """接收本地上传并保存到 ComfyUI/input/FishS2Input。"""
        import hashlib
        import os
        import tempfile
        import folder_paths
        try:
            reader = await request.multipart()
            field = await reader.next()
            if field is None or field.name != 'file':
                raise ValueError('未收到音频文件。')
            suffix = Path(field.filename or '').suffix.lower()
            if suffix not in ('.wav', '.mp3', '.flac', '.ogg', '.m4a'):
                raise ValueError('支持 WAV、MP3、FLAC、OGG、M4A。')
            folder = Path(folder_paths.get_input_directory()) / 'FishS2Input'
            folder.mkdir(parents=True, exist_ok=True)
            # 先写入临时文件并计算完整 SHA-256；同一字节内容不会重复保存。
            # 临时文件始终放在目标目录，以便最后通过硬链接原子提交。
            fd, temp_name = tempfile.mkstemp(prefix='.fishs2_upload_', suffix='.tmp', dir=str(folder))
            temp = Path(temp_name)
            digest = hashlib.sha256()
            total = 0
            try:
                with os.fdopen(fd, 'wb') as out:
                    while True:
                        chunk = await field.read_chunk(size=1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > 100 * 1024 * 1024:
                            raise ValueError('音频超过 100 MB 上传限制。')
                        digest.update(chunk)
                        out.write(chunk)
                if total == 0:
                    raise ValueError('音频文件为空。')
                # 以完整哈希作为文件名，跨文件名上传仍能去重。
                # 同一哈希但不同扩展名可能影响解码，因此分别存储。
                name = f'{digest.hexdigest()}{suffix}'
                target = folder / name
                if target.exists():
                    # 已有文件也验证哈希，避免人工修改文件后错误复用。
                    existing = hashlib.sha256()
                    with target.open('rb') as inp:
                        for block in iter(lambda: inp.read(1024 * 1024), b''):
                            existing.update(block)
                    if existing.digest() != digest.digest():
                        raise ValueError('检测到同名缓存文件内容不一致，请检查 FishS2Input。')
                    reused = True
                else:
                    # os.link 使用排他创建；并发上传同一文件时不会覆盖已有文件。
                    try:
                        os.link(temp, target)
                        reused = False
                    except FileExistsError:
                        reused = True
                return web.json_response({'ok': True, 'name': name,
                                          'subfolder': 'FishS2Input', 'type': 'input',
                                          'reused': reused})
            finally:
                temp.unlink(missing_ok=True)
        except Exception as exc:
            return web.json_response({'ok': False, 'error': str(exc)}, status=400)
