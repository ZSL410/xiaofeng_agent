import sys
import os
import json
import subprocess
import urllib.request

# 确保能找到器官和记忆模块
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from 器官.嘴巴 import speak
from 器官.耳朵 import listen_once
from 器官.手 import call_tool
from 记忆.记忆引擎 import load_short_term, save_short_term, load_long_term

# 加载配置
with open(os.path.join(BASE_DIR, "模型.json"), "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

MODEL_NAME = CONFIG.get("model_name", "qwen2.5:7b")
OLLAMA_HOST = CONFIG.get("ollama_host", None)
OLLAMA_BIN = CONFIG.get("ollama_bin", None) or "ollama"


def _ollama_chat(messages, model=None):
    """统一的 Ollama 调用：优先 HTTP API（WSL 兼容），回退 CLI 子进程"""
    if model is None:
        model = MODEL_NAME

    # 优先 HTTP API 模式 —— WSL 下连接 Windows 宿主 Ollama 的标准方式
    if OLLAMA_HOST:
        url = f"{OLLAMA_HOST.rstrip('/')}/api/chat"
        body = json.dumps({"model": model, "messages": messages, "stream": False},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("message", {}).get("content", "")
        except Exception:
            # HTTP API 失败时回退到 CLI
            pass

    # CLI 子进程模式 —— 适用于 ollama 在同一系统 PATH 上的情况
    try:
        result = subprocess.run(
            [OLLAMA_BIN, "run", model],
            input=json.dumps(messages, ensure_ascii=False),
            text=True, capture_output=True, encoding="utf-8", timeout=60
        )
        return result.stdout.strip()
    except Exception as e:
        return f"晓风有点卡住了，错误：{e}"


def chat_with_xiaofeng(user_input, history):
    system_prompt = """
你是晓风，一个温暖、体贴的私人助手。你的职责是：
1. 与用户进行自然、流畅、让人放松的聊天。
2. 只有当用户明确提出"记账"、"查账"或"帮我记一下"等指令时，才去处理财务问题。
3. 请不要在日常对话中主动追问金额或消费细节，这会打扰用户的交流体验。
"""
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history[-10:]:
        messages.append(turn)
    messages.append({"role": "user", "content": user_input})

    return _ollama_chat(messages)

def main():
    print("=" * 50)
    print("🧠 晓风主体 · 模块化版 已启动")
    print("💡 可以跟我聊天，也可以直接说 '帮我记账' 让我处理财务")
    print("💡 日程指令：'添加会议明天3点' / '显示待办' / '提醒我5分钟后喝水'")
    print("💡 输入 'v' 启动语音输入")
    print("💡 输入 '退出' 结束对话")
    print("=" * 50)

    short_term = load_short_term()
    long_term = load_long_term()

    while True:
        user_input = input("\n你: ").strip()
        if user_input == "退出":
            print("👋 晓风主体已关闭")
            break

        # 语音输入模式
        if user_input == "v":
            user_input = listen_once()
            if user_input in ["语音识别失败", "模拟语音输入"]:
                print(f"⚠️ {user_input}，请重试或手动输入文字")
                continue

        # 检查是否是财务指令
        if "记账" in user_input or "查账" in user_input or "花了" in user_input:
            print("🔧 正在处理财务指令...")
            result = call_tool("财务", user_input)
            print(result)
            speak(result)
        # 检查是否是日程/待办/提醒指令
        elif any(kw in user_input for kw in ["日程", "提醒", "待办", "todo",
                                              "schedule", "任务"]):
            print("🔧 正在处理日程指令...")
            result = call_tool("日程", user_input)
            print(result)
            speak(result)
        else:
            short_term.append({"role": "user", "content": user_input})
            response = chat_with_xiaofeng(user_input, short_term)
            print(f"晓风: {response}")
            speak(response)
            short_term.append({"role": "assistant", "content": response})
            save_short_term(short_term)

if __name__ == "__main__":
    main()