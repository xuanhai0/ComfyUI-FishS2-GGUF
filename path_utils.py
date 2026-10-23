"""跨平台路径工具：本地路径与 HTTP URL 严格分离。"""
import os
from pathlib import Path


def local_path(value, label="路径"):
    """兼容 Windows 正/反斜杠和粘贴引号；不处理 URL。"""
    raw = str(value or "").strip().strip('"').strip("'").strip()
    if not raw:
        raise ValueError(f"{label} 未填写。")
    if raw.lower().startswith(("http://", "https://")):
        raise ValueError(f"{label} 必须是本地文件路径，而不是 URL。")
    if os.name == "nt":
        raw = raw.replace("/", "\\")
    return Path(raw).expanduser().resolve()


def optional_path(value):
    return local_path(value) if str(value or "").strip() else None


def executable_path(value):
    """允许 s2.exe 绝对路径，或在 PATH 中找到的命令。"""
    import shutil
    raw = str(value or "").strip().strip('"').strip("'")
    found = shutil.which(raw)
    return local_path(found or raw, "s2.cpp 可执行文件")
