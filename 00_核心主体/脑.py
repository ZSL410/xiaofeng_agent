import sys
import os
import re
import json
import subprocess
import urllib.request

VERSION = "3.6.0"

# 确保能找到器官和记忆模块
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from 器官.嘴巴 import speak
from 器官.耳朵 import listen_once
from 器官.手 import call_tool
from 记忆.记忆引擎 import (load_short_term, save_short_term, load_long_term,
                              decay_patterns, reject_pattern)
from 记忆.数据提炼 import refine

# 加载配置
with open(os.path.join(BASE_DIR, "模型.json"), "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

MODEL_NAME = CONFIG.get("model_name", "qwen2.5:7b")
OLLAMA_HOST = CONFIG.get("ollama_host", None)
OLLAMA_BIN = CONFIG.get("ollama_bin", None) or "ollama"


def _ollama_chat(messages, model=None):
    """统一的 Ollama 调用：优先 HTTP API(vWSL 兼容），回退 CLI 子进程"""
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
        return f"晓风有点卡住了,错误:{e}"


# ===================== LLM 意图路由 =====================

# 工具注册表：LLM 据此判断用户意图
TOOLS = [
    {
        "name": "日程",
        "desc": "日程事件、待办事项、定时提醒",
        "signals": "26叫我、38喊我、半个小时洗衣服、提醒我5分钟后喝水、明天8点叫醒我、添加会议明天3点、显示待办、完成任务1、一小时后提醒我开会",
    },
    {
        "name": "财务",
        "desc": "记账、查账、财务管理",
        "signals": "记账午餐30元、查账这个月、帮我记一下打车25块、花了多少钱、报销、花了",
    },
]

_ROUTE_PROMPT_V2 = """你是晓风Agent的意图路由器.分析用户输入,判断意图并提取结构化参数.

当前时间:{current_time}

{tool_descriptions}

## 意图分类与参数提取

### 1. 日程提醒(add_reminder)
当用户表达了"在某个时间需要被提醒做某事"的意图:

**信号词(含语音识别容错):**
- 明确:"叫我"、"喊我"、"提醒我"、"叫醒我"、"通知我"、"叫一下"、"到点提醒"
- ⚠️ 容错:"教我"(语音识别常把"叫"误识别为"教",如"5分钟教我"实际是"5分钟叫我")
- 容错:"叫我"前只有数字(如"38叫我")→ 数字是分钟数,在当前小时触发

**时间表达:**
- 相对:"X分钟后"、"半小时后"、"X小时后"、"X分钟之后"
- 绝对:"明天X点"、"下午X点"、"晚上X点"、"X点X分"、"X:XX"
- 隐含:纯数字+信号词(如"26喊我"→当前小时:26)

**提取规则:**
- time_offset: 明确的相对分钟数.⚠️ "X分钟后"的X始终是分钟("5分钟后"→5, "两分钟后"→2, "1分钟后"→1), 不是小时!
  只有明确说"X小时后"/"X个小时后"才按小时转分钟("2小时后"→120, "一小时后"→60).
  "半小时后"→30.否则 null
- absolute_time: 明确的时钟时间.根据当前时间推断小时(如当前14:30,用户说"38叫我"→"14:38";说"明天8点"→"08:00"),否则 null
- content: 去掉时间词和信号词后的核心内容(≤10字),如"泡咖啡"、"喝水"、"起床".如果只有"叫我"没其他内容,content="提醒"
  ⚠️ 纠正文本("不是X是Y"/"改成Z")中,content取Y/Z部分的内容,不要取被否定的X部分
- raw_text: 用户原始输入全文

**即使没有明确的"提醒/叫/喊"关键词,只要整体语义是"在某个时间做某事",也视为提醒.**

### 2. 修正意图(correct_reminder) ⭐ 新增
当用户表达对上次提醒的纠正,包含以下模式:
- "不是X,是Y"、"不是X是Y"(如"不是两分钟,是五分钟"、"不是两分钟是五分钟冥想")
- "说错了,应该是Z"、"说错了应该是Z"
- "改成W"、"换成W"、"应该是W"、"不对,是W"
- "X不对,Y才对"、"X错了,Y"

此时提取**修正后的新参数**(被纠正的值,不是被否定的旧值):
  "不是X是Y" → time_offset/content 取 Y 的值,忽略 X
  "改成Z" / "应该是Z" → time_offset/content 取 Z 的值
  action 设为 "correct_reminder".
⚠️ 时间始终基于当前时间计算,不要基于旧提醒的时间累加.

修正意图通常置信度较高(≥0.85),因为用户明确在纠错.

### 3. 删除提醒(delete_reminder) ⭐ v3.2.0 新增
当用户表达删除/取消某个提醒或待办的意图,且没有指定数字序号:
- "把两个小时的那个定时删掉"、"删除泡咖啡那个提醒"
- "取消明天的会议"、"去掉那个喝水的提醒"、"把X的那个定时删掉"
- "删掉5分钟那个"、"把提醒删了"

提取规则:
- query: 用户对目标提醒的核心描述,去掉"删除"/"取消"/"那个"/"的"/"定时"/"提醒"/"把"/"给"等噪声词
  例:"把两个小时的那个定时删掉" → query="两个小时"
  例:"删除泡咖啡那个提醒" → query="泡咖啡"
  action 设为 "delete_reminder".

### 4. 日程管理(manage)
"显示日程"、"查看日程"、"添加会议"、"添加任务"、"显示待办"、"完成任务"、"删除日程"等

### 5. 财务
"记账"、"查账"、"花了"、"报销"、"帮我记"、金额+元

### 6. 聊天
其他日常对话、闲聊、问答;以及"是"/"不是"/"对"/"不对"等确认词(这些留给主循环处理)

## 置信度(confidence) 评估标准

置信度反映你对解析结果的确定程度(0-1 的浮点数):

- **0.9-1.0**:完全明确.信号词清晰、时间表达无歧义、内容完整.
  例:"5分钟后叫我喝水" → 0.95(明确信号词+明确时间+明确内容)
  例:"不是两分钟,是五分钟冥想" → 0.95(明确纠错意图)

- **0.7-0.85**:基本确定.有时间表达但信号词模糊,或内容需要推断.
  例:"26叫我" → 0.8(信号词明确但内容缺失,需推断为"提醒")
  例:"半个小时后洗衣服" → 0.8(缺少"叫我/提醒"但语义完整)

- **0.5-0.65**:不太确定.时间模糊、内容残缺、或存在歧义.
  例:"5分钟教我我泡一杯咖啡" → 0.6("教"可能是"叫"的误识别,时间明确但存在歧义)
  例:"38"(只有数字无上下文)→ 0.3

- **explanation**:一句话说明判断依据(≤30字),如"明确包含'X分钟后叫我'信号词"、"'教'推断为'叫'的语音误识别"

## 输出格式

⚠️ 所有输出都包含 confidence 和 explanation 字段!

提醒(高置信度):
{{"tool":"日程","action":"add_reminder","params":{{"time_offset":5,"absolute_time":null,"content":"泡咖啡","raw_text":"5分钟叫我泡咖啡"}},"confidence":0.95,"explanation":"明确的'X分钟后叫我'信号词"}}

提醒(低置信度,含语音容错):
{{"tool":"日程","action":"add_reminder","params":{{"time_offset":5,"absolute_time":null,"content":"泡咖啡","raw_text":"5分钟教我我泡一杯咖啡"}},"confidence":0.55,"explanation":"'教'可能是'叫'的语音误识别"}}

修正:
{{"tool":"日程","action":"correct_reminder","params":{{"time_offset":5,"absolute_time":null,"content":"冥想","raw_text":"不是两分钟是五分钟冥想"}},"confidence":0.95,"explanation":"用户明确纠正时间:2→5分钟"}}

删除提醒:
{{"tool":"日程","action":"delete_reminder","params":{{"query":"两个小时"}},"confidence":0.9,"explanation":"用户要删除特定描述的提醒"}}

日程管理:
{{"tool":"日程","action":"manage","params":null,"confidence":0.9,"explanation":"日程管理指令"}}

财务:
{{"tool":"财务","action":null,"params":null,"confidence":0.95,"explanation":"明确的记账/查账关键词"}}

聊天(确认回复——留给主循环处理):
{{"tool":"聊天","action":"confirm_response","params":null,"confidence":1.0,"explanation":"用户对确认问题的回复"}}

聊天(其他):
{{"tool":"聊天","action":null,"params":null,"confidence":0.9,"explanation":"日常闲聊"}}

无法确定意图:
{{"tool":"聊天","action":"ask_clarify","message":"请问你想设置什么提醒?","confidence":0.3,"explanation":"无法从输入中提取有效意图"}}

⚠️ 只返回一个JSON对象,不要任何解释、标点或换行.

用户输入:{user_input}
JSON:"""


def _route_intent(user_input, timeout=8):
    """
    LLM 驱动的意图路由(v3.2.0: +删除意图, 超时优化, 模型降级)。
    成功返回完整 intent dict(含 params, confidence, explanation), 失败返回 None。
    主模型 qwen2.5:7b 超时后自动降级为 qwen2.5:3b(更快).
    """
    from datetime import datetime

    desc_lines = []
    for t in TOOLS:
        desc_lines.append(f"- {t['name']}({t['desc']})示例:{t['signals']}")

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = _ROUTE_PROMPT_V2.format(
        current_time=current_time,
        tool_descriptions="\n".join(desc_lines),
        user_input=user_input,
    )

    def _try_ollama(model, req_timeout):
        """尝试用指定模型解析意图,成功返回 intent dict, 失败返回 None"""
        body = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 256, "temperature": 0},
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=req_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (data.get("response", "") or "").strip()

    def _parse_result(result):
        """从 LLM 输出中解析 intent dict, 失败返回 None"""
        # 清洗：去掉可能的 markdown 代码块
        result = re.sub(r'^```(?:json)?\s*', '', result)
        result = re.sub(r'\s*```$', '', result)
        result = result.strip()

        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                tool = parsed.get("tool")
                if tool in ("日程", "财务", "聊天"):
                    intent = {"tool": tool}
                    action = parsed.get("action")
                    if action:
                        intent["action"] = action
                    params = parsed.get("params")
                    if action in ("add_reminder", "correct_reminder") and isinstance(params, dict):
                        params.setdefault("time_offset", None)
                        params.setdefault("absolute_time", None)
                        params.setdefault("content", "")
                        params.setdefault("raw_text", user_input)
                        intent["params"] = params
                    elif action == "delete_reminder" and isinstance(params, dict):
                        params.setdefault("query", user_input)
                        intent["params"] = params
                    elif params is not None:
                        intent["params"] = params
                    confidence = parsed.get("confidence")
                    if confidence is not None:
                        try:
                            intent["confidence"] = float(confidence)
                        except (ValueError, TypeError):
                            intent["confidence"] = 0.7
                    else:
                        intent["confidence"] = 0.7
                    explanation = parsed.get("explanation", "")
                    intent["explanation"] = str(explanation) if explanation else ""
                    if action == "ask_clarify" and parsed.get("message"):
                        intent["message"] = parsed["message"]
                    return intent
        except json.JSONDecodeError:
            pass

        # 降级：正则提取
        m = re.search(r'"tool"\s*:\s*"(日程|财务|聊天)"', result)
        if m:
            tool = m.group(1)
            intent = {"tool": tool, "confidence": 0.4, "explanation": "JSON损坏-正则提取"}
            action_m = re.search(r'"action"\s*:\s*"([^"]+)"', result)
            if action_m:
                intent["action"] = action_m.group(1)
            params_m = re.search(r'"params"\s*:\s*(\{[^}]+\})', result)
            if params_m:
                try:
                    params = json.loads(params_m.group(1))
                    if intent.get("action") in ("add_reminder", "correct_reminder"):
                        params.setdefault("time_offset", None)
                        params.setdefault("absolute_time", None)
                        params.setdefault("content", "")
                        params.setdefault("raw_text", user_input)
                    elif intent.get("action") == "delete_reminder":
                        params.setdefault("query", user_input)
                    intent["params"] = params
                except json.JSONDecodeError:
                    pass
            conf_m = re.search(r'"confidence"\s*:\s*([0-9.]+)', result)
            if conf_m:
                try:
                    intent["confidence"] = float(conf_m.group(1))
                except ValueError:
                    pass
            return intent

        return None

    # 第 1 步：主模型 qwen2.5:7b（8 秒超时）
    try:
        result = _try_ollama("qwen2.5:7b", timeout)
        intent = _parse_result(result)
        if intent:
            return intent
        print(f"[路由] qwen2.5:7b 返回无法解析,尝试降级模型...")


    except Exception as e:
        print(f"⚠️ qwen2.5:7b 路由超时/失败({e}),尝试降级模型 qwen2.5:3b...")

    # 第 2 步：降级模型 qwen2.5:3b（更快，5 秒超时）
    try:
        result = _try_ollama("qwen2.5:3b", 5)
        intent = _parse_result(result)
        if intent:
            intent["explanation"] = intent.get("explanation", "") + "(降级模型)"
            return intent
    except Exception as e:
        print(f"⚠️ qwen2.5:3b 路由也失败({e}),降级为关键词匹配")

    return None


def _describe_reminder(params):
    """将结构化参数转为人类可读的一句话描述，用于确认问题。"""
    parts = []
    if params.get("time_offset") is not None:
        t = params["time_offset"]
        if t >= 60:
            parts.append(f"{t // 60}小时" + (f"{t % 60}分钟" if t % 60 else ""))
        else:
            parts.append(f"{t}分钟")
    elif params.get("absolute_time"):
        parts.append(params["absolute_time"])
    else:
        parts.append("稍后")

    content = (params.get("content") or "").strip()
    if content and content != "提醒":
        return f"{' '.join(parts)}后提醒「{content}」"
    else:
        return f"{' '.join(parts)}后提醒"


def chat_with_xiaofeng(user_input, history):
    system_prompt = """
你是晓风,一个温暖、体贴的私人助手.你的职责是:
1. 与用户进行自然、流畅、让人放松的聊天.
2. 只有当用户明确提出"记账"、"查账"或"帮我记一下"等指令时,才去处理财务问题.
3. 请不要在日常对话中主动追问金额或消费细节,这会打扰用户的交流体验.
"""
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history[-10:]:
        messages.append(turn)
    messages.append({"role": "user", "content": user_input})

    return _ollama_chat(messages)

def _try_daily_refine(long_term):
    """
    每日自动数据提炼（v3.3.0 新增）。

    检查 长期记忆.json 中的 last_refined 字段:
    - 今天已提炼过 → 跳过
    - 从未提炼或不是今天 → 静默执行 refine()
    """
    from datetime import date

    last_refined = long_term.get("last_refined", "") if isinstance(long_term, dict) else ""

    today_str = date.today().isoformat()  # "2026-06-22"
    if last_refined and last_refined[:10] == today_str:
        return  # 今天已提炼，跳过

    # 静默提炼（不打印详细列表，避免干扰启动体验）
    try:
        result = refine(verbose=False)
        if result.get("status") == "ok" and result.get("total", 0) > 0:
            print(f"📊 每日数据提炼: 发现 {result['total']} 条行为模式 "
                  f"({', '.join(f'{k}×{v}' for k, v in result.get('by_type', {}).items())})")
    except Exception as e:
        # 提炼失败不影响正常启动
        print(f"⚠️ 每日数据提炼跳过: {e}")


def _try_weekly_decay(long_term):
    """
    每周规律衰减检查（v3.5.0 新增）。

    检查 长期记忆.json 中的 last_decay 字段:
    - 距上次衰减 < 7 天 → 跳过
    - 从未衰减或 ≥ 7 天 → 执行 decay_patterns()
    """
    from datetime import date

    last_decay = long_term.get("last_decay", "") if isinstance(long_term, dict) else ""
    today_str = date.today().isoformat()

    if last_decay:
        try:
            last_date = date.fromisoformat(last_decay)
            if (date.today() - last_date).days < 7:
                return  # 本周已衰减，跳过
        except (ValueError, TypeError):
            pass  # 日期格式异常，执行衰减

    try:
        result = decay_patterns()
        removed = result.get("removed", 0)
        decayed = result.get("decayed", 0)
        kept = result.get("kept", 0)
        if removed > 0 or decayed > 0:
            print(f"🧠 规律衰减完成: {decayed} 条置信度降低, "
                  f"{removed} 条已遗忘, 保留 {kept} 条")
    except Exception as e:
        print(f"⚠️ 规律衰减跳过: {e}")


def main():
    print("=" * 50)
    print(f"🧠 晓风主体 · 模块化版 v{VERSION} 已启动")
    print("💡 可以跟我聊天，也可以直接说 '帮我记账' 让我处理财务")
    print("💡 日程指令：'添加会议明天3点' / '显示待办' / '26叫我' / '半小时后提醒'")
    print("💡 输入 'v' 启动语音输入")
    print("💡 输入 '退出' 结束对话")
    print("=" * 50)

    # 清空 Windows 按键缓冲区（防止残留的 PowerShell 命令被 input() 读取）
    import sys
    if sys.platform == "win32":
        try:
            import msvcrt
            flushed = 0
            while msvcrt.kbhit():
                msvcrt.getch()
                flushed += 1
            if flushed:
                print(f"🧹 已清空 {flushed} 个残留按键")
        except Exception:
            pass

    short_term = load_short_term()
    long_term = load_long_term()

    # ============================================================
    # 每日数据提炼（v3.3.0）：启动时检查，每天最多执行一次
    # ============================================================
    _try_daily_refine(long_term)
    _try_weekly_decay(long_term)

    # 启动日程提醒后台线程（确保模块被导入，触发 daemon 线程启动）
    try:
        import importlib
        importlib.import_module("日程模块.schedule_module")
        print("✅ 日程提醒线程已启动")
    except Exception as e:
        print(f"⚠️ 日程提醒线程启动失败（日程模块可能未就绪）: {e}")

    # ---- 确认状态机（v3.1.1）----
    _pending_confirmation = None  # 暂存低置信度意图，等待用户确认

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

        # ============================================================
        # 残留输入过滤（v3.1.4 增强）
        # 终端残留的 PowerShell/Venv 命令可能在 input() 时被自动读取
        # ============================================================
        _RESIDUAL_PATTERNS = [
            # 盘符路径: d:\...  /  d:/...  /  D:\... 等
            r'^[a-zA-Z]:[\\/]',
            # venv 激活脚本路径
            r'[\\/]\.venv[\\/]',
            r'[\\/]venv[\\/]',
            r'[\\/]Scripts[\\/]',
            r'[/]Scripts[/]',
            r'[\\/]Scripts$',
            # 关键词
            r'\.venv',
            r'\bvenv\b.*\bactivate\b',
            r'\bactivate\b.*\bvenv\b',
            r'\bactivate\b',
            r'Set-ExecutionPolicy',
            r'\bPowerShell\b',
            # & 符号（PowerShell 命令连接符，但排除正常对话中的 &）
            r'&\s*(?:chcp|powershell|cmd|\.)',
        ]
        if any(re.search(p, user_input, re.IGNORECASE) for p in _RESIDUAL_PATTERNS):
            print("[系统] 检测到残留输入，已自动忽略")
            continue

        # 文本规范化：去除多余空格（语音识别可能引入空格噪声）
        user_input = ' '.join(user_input.split())
        # 移除中文字符之间的空格（如 "一 分钟 后 叫 我" → "一分钟后叫我"）
        user_input = re.sub(r'(?<=[一-鿿])\s+(?=[一-鿿])', '', user_input)

        # ============================================================
        # 数据提炼命令（v3.3.0 新增）
        # ============================================================
        REFINE_KW = ["提炼规律", "分析习惯", "分析模式", "提炼数据", "发现规律"]
        if any(kw in user_input for kw in REFINE_KW):
            print("🔍 正在分析历史数据，提炼行为模式...")
            result = refine(verbose=True)
            print(result["message"])
            speak(result["message"])
            continue

        # ============================================================
        # 状态机：检查是否有待确认的意图
        # ============================================================
        if _pending_confirmation is not None:
            pending = _pending_confirmation
            _pending_confirmation = None  # 无论结果如何，先清除状态

            resp = user_input.strip()
            # 确认关键词
            if resp in ("是", "对", "嗯", "是的", "对的", "yes", "y", "Y", "好", "行", "可以", "确认"):
                print(f"✅ 已确认，正在执行...")
                intent = pending["intent"]
                action = intent.get("action")
                if action == "add_reminder" and intent.get("params"):
                    print("🔧 正在处理日程提醒（结构化参数）...")
                    result = call_tool("日程", intent["params"],
                                       _func="add_reminder_from_params")
                    print(result)
                    speak(result)
                elif action == "correct_reminder" and intent.get("params"):
                    print("🔧 正在修正提醒...")
                    result = call_tool("日程", intent["params"],
                                       _func="modify_last_reminder")
                    print(result)
                    speak(result)
                else:
                    result = call_tool(intent["tool"], pending["raw_input"])
                    print(result)
                    speak(result)
                continue

            # 否定关键词
            if resp in ("不是", "不", "不对", "no", "n", "N", "错了", "取消", "算了"):
                print("👌 好的，已取消。请重新告诉我你想做什么。")
                continue

            # 修正模式：不是X是Y / 改成Z / 应该是W（在否定回复后紧跟修正）
            if re.search(r'不是.*是|说错了|改成|换个|应该是', resp):
                # 将否定+修正作为新输入重新路由，但标记为修正意图
                print(f"[路由] 检测到修正模式，重新解析: {resp!r}")
                intent = _route_intent(resp)
                if intent is None:
                    intent = {"tool": "日程", "action": "correct_reminder",
                              "params": {"raw_text": resp},
                              "confidence": 0.5, "explanation": "降级修正匹配"}
                # 强制设为修正意图（如果 LLM 没识别出来）
                if intent.get("action") != "correct_reminder":
                    intent["action"] = "correct_reminder"
                    if intent.get("params"):
                        intent["params"]["raw_text"] = resp
                # 执行修正
                print("🔧 正在修正提醒...")
                params = intent.get("params") or {"raw_text": resp}
                result = call_tool("日程", params, _func="modify_last_reminder")
                print(result)
                speak(result)
                continue

            # 用户说了别的内容 → 取消暂存，当作新请求继续
            print(f"👌 已取消之前的确认请求，处理新输入...")

        # ============================================================
        # 规律拒绝处理（v3.5.0 新增）
        # 匹配 "不用X" / "不要X" / "拒绝X" 等，降低对应规律的置信度
        # ============================================================
        REJECT_PATTERNS = [
            (r'(?:不用|不要|别|拒绝|取消)\s*(.+)', "reject"),
        ]
        for pattern, action in REJECT_PATTERNS:
            m = re.search(pattern, user_input)
            if m:
                content = m.group(1).strip()
                # 排除一些太短的噪声匹配（如纯"不用"）
                if len(content) >= 2:
                    result = reject_pattern(content)
                    print(f"🧠 {result['message']}")
                    speak(result['message'])
                    # 如果影响了规律，跳过正常路由
                    if result.get("affected", 0) > 0:
                        continue
                break

        # ============================================================
        # 正常路由流程
        # ============================================================
        print(f"[路由] 收到输入: {user_input!r}")
        intent = _route_intent(user_input)

        # LLM 路由失败时降级为关键词匹配
        if intent is None:
            FINANCE_KW = ["记账", "查账", "花了", "报销", "帮我记"]
            SCHEDULE_KW = ["日程", "提醒", "待办", "todo", "任务",
                           "叫我", "教", "叫醒", "喊我", "通知", "闹钟", "分钟后"]
            CORRECTION_KW = ["不是", "说错了", "改成", "应该是", "不对", "换个"]
            DELETE_KW = ["删掉", "删", "删除", "取消", "去掉", "移除"]
            if any(kw in user_input for kw in FINANCE_KW):
                intent = {"tool": "财务", "confidence": 0.5,
                          "explanation": "降级关键词匹配"}
            elif any(kw in user_input for kw in CORRECTION_KW):
                # 修正关键词 → 尝试作为修正意图
                intent = {"tool": "日程", "action": "correct_reminder",
                          "params": {"raw_text": user_input},
                          "confidence": 0.5, "explanation": "降级修正关键词匹配"}
            elif any(kw in user_input for kw in DELETE_KW):
                # 删除关键词 → 模糊删除
                intent = {"tool": "日程", "action": "delete_reminder",
                          "params": {"query": user_input},
                          "confidence": 0.6, "explanation": "降级删除关键词匹配"}
            elif any(kw in user_input for kw in SCHEDULE_KW):
                intent = {"tool": "日程", "confidence": 0.5,
                          "explanation": "降级关键词匹配"}
            else:
                intent = {"tool": "聊天", "confidence": 0.5,
                          "explanation": "降级默认聊天"}
            print(f"[路由] → 降级为关键词匹配: {intent['tool']}")
        else:
            action = intent.get("action", "")
            conf = intent.get("confidence", 0.7)
            expl = intent.get("explanation", "")
            if action == "add_reminder":
                print(f"[路由] → LLM 判定: 日程提醒 "
                      f"(content={intent.get('params',{}).get('content','?')!r}, "
                      f"confidence={conf:.2f}, {expl})")
            elif action == "correct_reminder":
                print(f"[路由] → LLM 判定: 修正提醒 (confidence={conf:.2f}, {expl})")
            elif action == "delete_reminder":
                print(f"[路由] → LLM 判定: 删除提醒 "
                      f"(query={intent.get('params',{}).get('query','?')!r}, "
                      f"confidence={conf:.2f}, {expl})")
            else:
                print(f"[路由] → LLM 判定: {intent['tool']} (confidence={conf:.2f})")

        tool = intent["tool"]
        action = intent.get("action")
        confidence = intent.get("confidence", 0.7)

        # ============================================================
        # 低置信度拦截（v3.1.1）：add_reminder 且 confidence < 0.6
        # ============================================================
        if (tool == "日程" and action == "add_reminder"
                and intent.get("params") and confidence < 0.6):
            desc = _describe_reminder(intent["params"])
            print(f'🤔 你是想说「{desc}」吗？(回复"是"或"不是")')
            _pending_confirmation = {
                "intent": intent,
                "raw_input": user_input,
            }
            continue

        # ============================================================
        # 正常执行
        # ============================================================
        if tool == "财务":
            print("🔧 正在处理财务指令...")
            result = call_tool("财务", user_input)
            print(result)
            speak(result)

        elif tool == "日程":
            if action == "add_reminder" and intent.get("params"):
                print("🔧 正在处理日程提醒（结构化参数）...")
                result = call_tool("日程", intent["params"],
                                   _func="add_reminder_from_params")
                print(result)
                speak(result)
            elif action == "correct_reminder":
                print("🔧 正在修正提醒...")
                params = intent.get("params") or {"raw_text": user_input}
                result = call_tool("日程", params, _func="modify_last_reminder")
                print(result)
                speak(result)
            elif action == "delete_reminder":
                print("🔧 正在按描述删除提醒...")
                query = intent.get("params", {}).get("query", user_input)
                result = call_tool("日程", query, _func="delete_reminder_by_query")
                print(result)
                speak(result)
            else:
                # 日程管理类指令，走自然语言解析
                print("🔧 正在处理日程指令...")
                result = call_tool("日程", user_input)
                print(result)
                speak(result)

        else:
            print("[路由] → LLM 对话")
            short_term.append({"role": "user", "content": user_input})
            response = chat_with_xiaofeng(user_input, short_term)
            print(f"晓风: {response}")
            speak(response)
            short_term.append({"role": "assistant", "content": response})
            save_short_term(short_term)

if __name__ == "__main__":
    main()