import sys
import os
import importlib

# 工具模块路径 — 自动相对于项目根目录，兼容 Windows / WSL / Linux
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS_PATH = os.path.join(_PROJECT_ROOT, "01_工具模块")
if TOOLS_PATH not in sys.path:
    sys.path.insert(0, TOOLS_PATH)

def call_tool(tool_name, *args, **kwargs):
    """调用指定的工具模块

    支持两种调用模式：
    1. 默认模式：call_tool("日程", user_input) → 调用模块的 process_command()
    2. 命名函数模式：call_tool("日程", params, _func="add_reminder_from_params") → 调用指定函数
    """
    func_name = kwargs.pop("_func", "process_command")
    print(f"🖐️ 正在调用工具: {tool_name} → {func_name}")
    
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
        # 优先调用指定的函数名，回退到 process_command
        target_func = getattr(module, func_name, None)
        if target_func is None:
            print(f"❌ 工具模块 {tool_name} 没有 {func_name} 函数")
            return None
        return target_func(*args, **kwargs)
    except Exception as e:
        print(f"❌ 调用工具 {tool_name} 时出错: {e}")
        return None