import sys
import os
import importlib

# 工具模块路径 — 自动相对于项目根目录，兼容 Windows / WSL / Linux
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS_PATH = os.path.join(_PROJECT_ROOT, "01_工具模块")
if TOOLS_PATH not in sys.path:
    sys.path.insert(0, TOOLS_PATH)

def call_tool(tool_name, *args, **kwargs):
    """调用指定的工具模块"""
    print(f"🖐️ 正在调用工具: {tool_name}")
    
    # 工具名到模块路径的映射（请根据你的实际文件名调整）
    TOOL_MODULES = {
        "财务": "财务模块.finance_module",
        "日程": "日程模块.schedule_module",  # 未来添加
        "天气": "天气模块.weather_module",   # 未来添加
    }
    
    if tool_name not in TOOL_MODULES:
        print(f"❌ 未找到工具: {tool_name}")
        return None
    
    try:
        module = importlib.import_module(TOOL_MODULES[tool_name])
        # 假设每个工具模块都有一个 process_command 函数
        if hasattr(module, "process_command"):
            return module.process_command(*args, **kwargs)
        else:
            print(f"❌ 工具模块 {tool_name} 没有 process_command 函数")
            return None
    except Exception as e:
        print(f"❌ 调用工具 {tool_name} 时出错: {e}")
        return None