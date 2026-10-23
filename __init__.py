"""ComfyUI-FishS2-GGUF 插件入口。"""
from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .server_control import S2ServerControl, register_routes

NODE_CLASS_MAPPINGS['S2ServerControl'] = S2ServerControl
NODE_DISPLAY_NAME_MAPPINGS['S2ServerControl'] = 'Fish S2 Server Control'
WEB_DIRECTORY = './web'

try:
    register_routes()
except ImportError:
    # 允许在非 ComfyUI 环境执行语法检查和单元测试。
    pass

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']
