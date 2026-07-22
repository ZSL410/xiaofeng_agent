import sys
import os
import re
import json
import subprocess
import urllib.request

VERSION = "3.8.4"

# 确保能找到器官和记忆模块
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from 器官.嘴巴 import speak
from 器官.耳朵 import listen_once
from 器官.手 import call_tool
from 记忆.记忆引擎 import (load_short_term, save_short_term, load_long_term,
                              decay_patterns, reject_pattern,
                              build_injection_context, get_memory_stats)
from 记忆.数据提炼 import refine
from 记忆.衰减调度 import scheduler_check, manual_cleanup, get_decay_status

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
        "signals": "记账午餐30元、查账这个月、帮我记一下打车25块、花了多少钱、报销、花了、查看今天记录、统计这个月、最近吃饭花了多少",
    },
]

_ROUTE_PROMPT_V2 = """你是晓风Agent的意图路由器。分析用户输入，判断意图并提取结构化参数。

当前时间:{current_time}

## 🚨 删除关键词优先规则（最高优先级，v3.8.4，覆盖所有其他意图）

**一旦用户输入中包含 "删除" / "删掉" / "清空" / "清除" / "去掉" 中的任意一个，该输入的意图必须判定为删除相关，不再判断财务/日程/聊天等其他意图。**

即使同时出现 "财务" / "日程" / "记账" / "查看" / "花了" / "多少" 等其他模块关键词，删除关键词的优先级高于一切。

判断逻辑（按顺序）:
1. 先检查否定词：如果有"不/不是/不要/别"否定"删除" → 否定词规则生效（见下节），排除删除 → 走其他意图
2. 无否定词 + 含删除关键词 → **直接进入删除流程**，跳过所有财务/日程判断
3. 完全不含删除关键词 → 才进入后面的优先级规则

**关键示例（必须严格遵守）**:
- "删除今天的财务数据" → **删除意图**（含"删除"关键词！虽然有"财务"但删除优先。走 ask_clarify 确认删除范围）
- "把今天这笔的金额删除" → **删除意图**（含"删除"！虽然有"金额"但不是记账。走删除流程）
- "删除数据" → **删除意图**（含"删除"，走 ask_clarify 确认删除哪种数据）
- "查看所有财务数据" → 财务查询（不含删除关键词，正常判断）
- "今天吃饭花了十元" → 记账（不含删除关键词，正常判断）
- "不要删除数据" → 否定词+删除 → 排除删除 → 其他意图

## ⚠️ 否定词规则（v3.8.2）

当用户句子中包含否定词时，这些词会**反转或取消**其后跟随的意图关键词。

**否定词列表**: 不 / 没 / 别 / 不是 / 并非 / 不要 / 不用 / 无需 / 没有想 / 不想

**处理流程**:
1. 首先检查句子中是否有否定词
2. 如果有否定词，标记被否定的意图关键词（否定词后面紧跟的动作词）
3. 被否定的意图 → **排除该意图**，重新按其他规则判断

**关键示例**:
- "查看所有数据不是删除" → 包含"不是" + "删除" → **排除删除意图** → 判断为查询 → confidence 0.9+
- "不要删除这条" → 包含"不要" + "删除" → **排除删除意图** → 判断为其他意图 → confidence 0.9+
- "不是想删除，是想看看" → 包含"不是" + "删除" → **排除删除意图** → "看看" → 查询 → confidence 0.9+
- "删除昨天的记录" → 无否定词 → 正常判断为删除

**⚠️ 注意**: 否定词必须紧邻或修饰意图动词才生效。远距离否定（如"不是很好，删除吧"→"不是"修饰"好"，不修饰"删除"）视为无否定。

## ⚠️ 优先级规则（必须遵守）

1. **🚨 删除关键词无条件优先（最高优先级）**: 见顶部"删除关键词优先规则"。含删除关键词 → 无条件走删除流程。**禁止**因为同时出现"财务"/"记账"/"查看"等词而路由到其他意图。

2. **否定词规则**: 先检查否定词 → 如果否定了"删除"则排除删除意图 → 再进入下面的判断。

3. **先判断是否为删除，再判断模块归属**: 是删除→判断目标是否明确→若无明确目标则 ask_clarify，target_type 和 message 根据下文规则动态生成。

## 工具与规则

### 日程提醒(add_reminder)
用户想在某个时间被提醒做某事。信号词:叫我/喊我/提醒我/通知我/叫醒我。语音容错:"教我"可能是"叫我"。即使无关键词，语义是"在某个时间做某事"也视为提醒。
- time_offset: 相对分钟数。X分钟后→X，半小时后→30，X小时后→X×60。无则为null
- absolute_time: 时钟时间。根据当前时间推断(如"38叫我"→当前小时:38，"明天8点"→08:00)。无则为null
- content: 去时间词和信号词后的核心内容(≤10字)，仅"叫我"时默认"提醒"
- raw_text: 用户原始输入

### 日程修正(correct_reminder)
用户纠正上次提醒:模式如"不是X是Y"/"改成Z"/"说错了应该是W"/"X不对Y才对"。提取Y/Z/W的值，忽略被否定的X。时间基于当前时间计算。

### 日程删除(delete_reminder)
用户删除日程提醒，必须有明确目标(如id编号、具体内容描述"泡咖啡"、或时间描述"两小时后的那个")。仅当目标可识别时返回此意图。
- query: 提取核心描述为query(去噪声词:那个/的/定时/提醒/把/删掉/删除)

### 日程管理(manage)
查看/添加/完成任务等日程管理操作。⚠️ 含"删除"关键词的文字不路由到此。

### 财务（⚠️ 删除意图优先：含"删除/清空"关键词先走删除流程，不路由到此）

当意图判定为"财务"时，必须进一步区分 action。按以下规则逐条判断：

**📌 查询意图 (action: "query")** — 用户在"看/问"，不是"记"

触发词（任一命中即判定为查询）:
"查看"、"查询"、"显示"、"列出"、"统计"、"看看"、"看一下"、"有没有"、"多少"、"花了多少"、"一共"、"最大"、"最贵"、"最少"、"列表"、"查一下"、"看一看"

示例:
- "查看所有财务数据" → action:"query"（命中"查看"）
- "今天的支出是多少" → action:"query"（命中"多少"）
- "看一下上个月的记录" → action:"query"（命中"看一下"）
- "统计本月开销" → action:"query"（命中"统计"）
- "最近花了多少钱" → action:"query"（命中"花了多少"）

**📌 记账意图 (action: "record")** — 用户在"记"，有明确消费行为

触发词（任一命中 + 含金额数字）才判定为 record:
"花了"、"付了"、"消费"、"支出"、"买了"、"花费"、"用了"、"记账"

⚠️ **必须包含金额数字**（阿拉伯数字或中文数字均可），无金额则**不是** record。

示例:
- "今天吃饭花了十元" → action:"record"（命中"花了"+含金额"十"）
- "买了一瓶水三块钱" → action:"record"（命中"买了"+含金额"三"）
- "午餐花了25" → action:"record"（命中"花了"+含金额"25"）

**📌 默认行为（当以上两类触发词都不命中时）**:
- 文本中有金额数字 → 默认视为 record
- 文本中无金额、无触发词 → **走 ask_clarify**，不要默认 record！

常见误判纠正:
- "查看所有财务数据" → query（不是 record！"查看"是查询词）
- "删除昨天的记录" → ask_clarify 删除流程（不是财务！删除优先）

### 澄清问询(ask_clarify)
用户发出删除/清空指令但缺少明确目标（没有具体id、内容关键词或时间描述）。

**target_type 推断规则（按优先级）:**
- 含 "财务"/"记账"/"账单"/"消费"/"收入"/"支出" → "finance"
- 含 "日程"/"提醒"/"待办"/"任务"/"事件" → "schedule"
- 含 "记忆"/"记住"/"记忆数据" → "memory"
- 仅含"数据"/"之前"/"全部"/"所有"，无模块词 → "unknown"

**message 动态生成规则（根据 target_type 选择措辞）:**
- finance: "你想删除全部财务记录，还是最近一周的？还是最新一条？"
- schedule: "你想删除全部日程/待办，还是最近一周的？还是最新一条？"
- memory: "你想删除全部记忆数据，还是最近一周的？还是最新一条？"
- unknown: "你想删除哪种数据？是财务记录、日程待办、还是记忆数据？"
  对于"清空所有"/"全部删了"等无类型表达也用此模板，把"删除"替换为"清空"。

**典型示例:**
- "删除财务数据" → ask_clarify(target_type="finance", message="你想删除全部财务记录，还是最近一周的？还是最新一条？")
- "删除日程" → ask_clarify(target_type="schedule", message="你想删除全部日程/待办，还是最近一周的？还是最新一条？")
- "删除记忆" → ask_clarify(target_type="memory", message="你想删除全部记忆数据，还是最近一周的？还是最新一条？")
- "删除数据" → ask_clarify(target_type="unknown", message="你想删除哪种数据？是财务记录、日程待办、还是记忆数据？")
- "清空所有" → ask_clarify(target_type="unknown", message="你想清空哪种数据？是财务记录、日程待办、还是记忆数据？")
- "全部删了" → ask_clarify(target_type="unknown", message="你想删除哪种数据？是财务记录、日程待办、还是记忆数据？")

### 聊天
其他对话、闲聊、问答、确认词(是/不是/对/不对)。

## 关键判断规则

意图决策链（按顺序执行，不可跳跃）:
1. **🚨 先检查删除关键词**: 文字中是否含"删除/删掉/清空/清除/去掉"?
   - 是 + 无否定词否定删除 → **无条件走删除流程**。即使含"财务"/"查看"/"记账"也不动摇。判断目标是否明确 → ask_clarify 或 delete_reminder。**禁止路由到财务！**
   - 是 + 有否定词否定删除 → 排除删除意图 → 继续步骤2
   - 否 → 继续步骤2
2. **再检查其他否定词**: 有无否定词否定其他意图?
   - 是 → 排除被否定意图 → **confidence 0.9+**
3. **判断财务/日程/聊天**: 不含删除时，才进入模块判断
   - 财务 → 按"财务意图细分规则"区分 action（query / record / ask_clarify）
   - 日程 → 按对应规则处理
   - 其他 → 聊天

## 置信度
0.9-1.0:信号词清晰+时间明确+内容完整。0.7-0.85:时间有但信号词模糊。0.5-0.65:有歧义或内容残缺。explanation:判断依据(≤30字)。

## 输出格式(只输出一个JSON对象，无其他内容)

提醒:{{"tool":"日程","action":"add_reminder","params":{{"time_offset":5,"absolute_time":null,"content":"泡咖啡","raw_text":"5分钟叫我泡咖啡"}},"confidence":0.95,"explanation":"明确的X分钟后叫我"}}
修正:{{"tool":"日程","action":"correct_reminder","params":{{"time_offset":5,"absolute_time":null,"content":"冥想","raw_text":"不是两分钟是五分钟冥想"}},"confidence":0.95,"explanation":"用户纠正时间"}}
删除:{{"tool":"日程","action":"delete_reminder","params":{{"query":"泡咖啡"}},"confidence":0.9,"explanation":"删除提醒-有明确内容"}}
管理:{{"tool":"日程","action":"manage","params":null,"confidence":0.9,"explanation":"日程管理"}}
财务记录:{{"tool":"财务","action":null,"params":null,"confidence":0.95,"explanation":"花了XX元-记账"}}
财务查询:{{"tool":"财务","action":"query","params":null,"confidence":0.9,"explanation":"查看/查询/统计"}}
财务无金额:{{"tool":"ask_clarify","action":null,"params":null,"confidence":0.6,"explanation":"财务意图但无金额无查询词"}}
澄清-日程:{{"tool":"ask_clarify","action":"delete_scope","params":{{"target_type":"schedule","message":"你想删除全部日程/待办，还是最近一周的？还是最新一条？"}},"confidence":0.95,"explanation":"模糊删除日程-需确认范围"}}
澄清-财务:{{"tool":"ask_clarify","action":"delete_scope","params":{{"target_type":"finance","message":"你想删除全部财务记录，还是最近一周的？还是最新一条？"}},"confidence":0.95,"explanation":"模糊删除财务-需确认范围"}}
澄清-记忆:{{"tool":"ask_clarify","action":"delete_scope","params":{{"target_type":"memory","message":"你想删除全部记忆数据，还是最近一周的？还是最新一条？"}},"confidence":0.95,"explanation":"模糊删除记忆-需确认范围"}}
澄清-未知:{{"tool":"ask_clarify","action":"delete_scope","params":{{"target_type":"unknown","message":"你想删除哪种数据？是财务记录、日程待办、还是记忆数据？"}},"confidence":0.95,"explanation":"无类型关键词-先确认数据类型"}}
聊天:{{"tool":"聊天","action":null,"params":null,"confidence":0.9,"explanation":"日常闲聊"}}

用户输入:{user_input}
JSON:"""


def _route_intent(user_input, timeout=8):
    """
    LLM 驱动的意图路由(v3.2.0: +删除意图, 超时优化, 模型降级)。
    成功返回完整 intent dict(含 params, confidence, explanation), 失败返回 None。
    主模型 qwen2.5:7b 超时后自动降级为 qwen2.5:3b(更快).
    """
    from datetime import datetime

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = _ROUTE_PROMPT_V2.format(
        current_time=current_time,
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
                if tool in ("日程", "财务", "聊天", "ask_clarify"):
                    intent = {"tool": tool}
                    action = parsed.get("action")
                    if action:
                        intent["action"] = action
                    params = parsed.get("params")
                    if tool == "ask_clarify":
                        # ask_clarify: 提取 params(message, target_type)
                        if isinstance(params, dict):
                            params.setdefault("target_type", "schedule")
                            params.setdefault("message", "请明确要删除的范围（全部/最近一周/今天/最新一条）")
                            intent["params"] = params
                    elif action in ("add_reminder", "correct_reminder") and isinstance(params, dict):
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
        m = re.search(r'"tool"\s*:\s*"(日程|财务|聊天|ask_clarify)"', result)
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
                    if tool == "ask_clarify":
                        params.setdefault("target_type", "schedule")
                        params.setdefault("message", "请明确要删除的范围（全部/最近一周/今天/最新一条）")
                        intent["params"] = params
                    elif intent.get("action") in ("add_reminder", "correct_reminder"):
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


def _generate_tool_reply(result, prefix="好的"):
    """
    根据工具模块返回的结构化结果，生成面向用户的自然语言回复（v3.8.0 新增）。

    架构定位：这是回复层的集中入口。各工具模块只返回结构化数据，
    不负责拼装用户可见的字符串。所有模块共享同一套回复模板，
    后续可升级为 LLM 驱动而无需修改各个工具模块。

    参数:
        result: dict — 工具模块返回的结构化结果
            {"status": "success"/"error", "type": "...", "data": {...}, "summary": "..."}
            或 {"status": "error", "message": "..."}
        prefix: str — 成功回复的前缀词，按场景可传入"好的"/"明白了"等

    返回:
        str — 面向用户的自然语言回复
    """
    if not isinstance(result, dict):
        # 兼容旧版字符串返回值（其他模块尚未迁移到结构化返回）
        return str(result)

    if result.get("status") == "error":
        return result.get("message", "处理失败，请重试")

    # 成功：根据类型生成自然语言回复
    rtype = result.get("type", "")
    summary = result.get("summary", "")

    if rtype == "delete":
        return f"{prefix}，已{summary}"
    elif rtype == "query":
        # 查询结果摘要已包含完整描述，直接返回
        return summary
    elif rtype in ("expense", "income"):
        # summary 格式如 "记录今天饭10元"，直接拼接前缀即可
        return f"{prefix}，已{summary}"
    else:
        # 兜底：直接用 summary
        return f"{prefix}，已{summary}" if summary else f"{prefix}，操作完成"


def chat_with_xiaofeng(user_input, history):
    # v3.7.0: 注入记忆上下文
    memory_context = ""
    try:
        memory_context = build_injection_context(user_input, max_tokens=2000)
    except Exception:
        pass  # 记忆注入失败不影响正常对话

    system_prompt = """
你是晓风,一个温暖、体贴的私人助手.你的职责是:
1. 与用户进行自然、流畅、让人放松的聊天.
2. 只有当用户明确提出"记账"、"查账"或"帮我记一下"等指令时,才去处理财务问题.
3. 请不要在日常对话中主动追问金额或消费细节,这会打扰用户的交流体验.
"""
    if memory_context:
        system_prompt += f"\n\n{memory_context}"

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


def _try_memory_scheduler():
    """
    记忆系统调度检查（v3.7.0 新增）。

    调用衰减调度模块执行每日衰减检查，并显示记忆统计。
    """
    try:
        result = scheduler_check()
        if result.get("ran") and result.get("stats", {}).get("decayed", 0) > 0:
            stats = result["stats"]
            print(f"🧠 记忆衰减完成: {stats['decayed']} 条置信度降低, "
                  f"{stats['archived']} 条归档, {stats['kept']} 条保留"
                  + (f", {stats['formed']} 条习惯形成" if stats.get('formed', 0) > 0 else ""))

        # 显示记忆统计摘要
        stats = get_memory_stats()
        if stats["facts_count"] > 0 or stats["active_patterns"] > 0:
            print(f"🧠 记忆库: {stats['facts_count']} 条事实, "
                  f"{stats['active_patterns']} 条活跃习惯"
                  + (f" ({stats['archived_patterns']} 条已归档)" if stats['archived_patterns'] > 0 else ""))
    except Exception as e:
        print(f"⚠️ 记忆调度跳过: {e}")


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


def _parse_delete_scope(text):
    """
    解析用户对删除范围澄清的回复，返回 (scope, target_type_override)。

    scope 取值:
        "cancel"  — 用户取消删除
        "all"     — 删除全部
        "last_week" — 删除最近一周
        "today"   — 删除今天的
        "latest"  — 删除最新一条
        "keyword" — 按内容关键词删除

    target_type_override:
        "schedule" / "finance" / None — 用户在回复中指定的目标类型
    """
    text = text.strip()

    target_type = None
    if any(kw in text for kw in ["财务", "记账", "账单", "消费", "收入", "支出"]):
        target_type = "finance"
    elif any(kw in text for kw in ["日程", "提醒", "待办", "任务", "事件"]):
        target_type = "schedule"
    elif any(kw in text for kw in ["记忆", "记住", "记忆数据"]):
        target_type = "memory"

    # 取消
    if text in ("算了", "取消", "不删了", "不用了", "不要了", "不删", "不了"):
        return "cancel", target_type

    # 全部
    if any(kw in text for kw in ["全部", "所有", "一切", "都删", "全删", "清空", "全都"]):
        return "all", target_type

    # 最近一周
    if any(kw in text for kw in ["最近一周", "这一周", "最近7天", "一周的", "这周", "上周",
                                   "近一周", "一周内", "过去一周"]):
        return "last_week", target_type

    # 今天
    if any(kw in text for kw in ["今天", "今日", "今天的", "今日的"]):
        return "today", target_type

    # 最新一条
    if any(kw in text for kw in ["最新", "最后", "最近", "最新一条", "最后一条",
                                   "最近那个", "最近一条", "最近那个", "最新的"]):
        return "latest", target_type

    # 默认：按内容关键词
    return "keyword", target_type


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
    _try_memory_scheduler()  # v3.7.0 替代 _try_weekly_decay

    # 启动日程提醒后台线程（确保模块被导入，触发 daemon 线程启动）
    try:
        import importlib
        importlib.import_module("日程模块.schedule_module")
        print("✅ 日程提醒线程已启动")
    except Exception as e:
        print(f"⚠️ 日程提醒线程启动失败（日程模块可能未就绪）: {e}")

    # ---- 确认状态机（v3.1.1）----
    _pending_confirmation = None  # 暂存低置信度意图，等待用户确认

    # ---- 澄清状态机（v3.7.4）----
    _pending_clarification = None  # 暂存模糊删除澄清请求，等待用户指定范围

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
        # 记忆管理命令（v3.7.0 新增）
        # ============================================================
        MEMORY_CLEANUP_KW = ["整理记忆", "清理记忆", "记忆整理", "衰减检查"]
        MEMORY_STATS_KW = ["记忆状态", "记忆统计", "查看记忆"]

        if any(kw in user_input for kw in MEMORY_CLEANUP_KW):
            print("🧹 正在整理记忆...")
            result = manual_cleanup()
            print(result["report"])
            speak("记忆整理完成")
            continue

        if any(kw in user_input for kw in MEMORY_STATS_KW):
            stats = get_memory_stats()
            status = get_decay_status()
            report = (
                f"🧠 记忆系统状态:\n"
                f"   - 事实: {stats['facts_count']} 条\n"
                f"   - 活跃习惯: {stats['active_patterns']} 条\n"
                f"   - 已归档: {stats['archived_patterns']} 条\n"
                f"   - 上次衰减: {status['last_decay'][:10] if status['last_decay'] else '从未'}\n"
                f"   - 面临衰减: {status['at_risk_count']} 条"
            )
            print(report)
            speak("记忆状态已显示")
            continue

        # ============================================================
        # 状态机：检查是否有待澄清的模糊删除（v3.7.4 / v3.7.6 两步增强）
        # ============================================================
        if _pending_clarification is not None:
            pending = _pending_clarification
            _pending_clarification = None

            scope_text = user_input.strip()
            scope, type_override = _parse_delete_scope(scope_text)

            if scope == "cancel":
                print("👌 好的，已取消删除操作。")
                continue

            target_type = type_override or pending.get("target_type", "schedule")
            original_target = pending.get("target_type", "schedule")

            # 两步澄清（v3.7.6）：上一轮是 unknown，用户本轮只指定了类型未指定范围
            if original_target == "unknown" and scope == "keyword" and type_override:
                type_messages = {
                    "finance": "你想删除全部财务记录，还是最近一周的？还是最新一条？",
                    "schedule": "你想删除全部日程/待办，还是最近一周的？还是最新一条？",
                    "memory": "你想删除全部记忆数据，还是最近一周的？还是最新一条？",
                }
                message = type_messages.get(target_type,
                    f"你想删除全部{target_type}数据，还是最近一周的？还是最新一条？")
                print(f"🤔 {message}")
                speak(message)
                _pending_clarification = {
                    "target_type": target_type,
                    "message": message,
                }
                continue

            # 正常执行删除
            print(f"🔧 正在按「{scope}」范围删除{target_type}数据...")
            if target_type == "finance":
                if scope == "keyword":
                    result = call_tool("财务", scope, scope_text, _func="delete_by_scope")
                else:
                    result = call_tool("财务", scope, _func="delete_by_scope")
            elif target_type == "memory":
                result = ("⚠️ 记忆数据删除暂不支持直接操作。"
                          "你可以对我说「清理记忆」或「整理记忆」来管理记忆数据。")
            else:
                if scope == "keyword":
                    result = call_tool("日程", scope, scope_text, _func="delete_by_scope")
                else:
                    result = call_tool("日程", scope, _func="delete_by_scope")
            print(result)
            # v3.8.0: 使用集中回复层包装，兼容旧版字符串和新版结构化返回
            reply = _generate_tool_reply(result, prefix="明白了")
            speak(reply)
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
            # 删除关键词优先（v3.7.5 / v3.7.6 增强）：在检查财务/日程之前先判断删除意图
            if any(kw in user_input for kw in DELETE_KW):
                # 推断目标类型 → 动态生成澄清消息
                FINANCE_CTX_KW = ["财务", "记账", "账单", "消费", "收入", "记录", "支出"]
                SCHEDULE_CTX_KW = ["日程", "提醒", "待办", "任务", "事件"]
                MEMORY_CTX_KW = ["记忆", "记住"]
                if any(kw in user_input for kw in FINANCE_CTX_KW):
                    intent = {"tool": "ask_clarify", "action": "delete_scope",
                              "params": {"target_type": "finance",
                                         "message": "你想删除全部财务记录，还是最近一周的？还是最新一条？"},
                              "confidence": 0.6, "explanation": "降级删除+财务→澄清"}
                elif any(kw in user_input for kw in SCHEDULE_CTX_KW):
                    intent = {"tool": "ask_clarify", "action": "delete_scope",
                              "params": {"target_type": "schedule",
                                         "message": "你想删除全部日程/待办，还是最近一周的？还是最新一条？"},
                              "confidence": 0.6, "explanation": "降级删除+日程→澄清"}
                elif any(kw in user_input for kw in MEMORY_CTX_KW):
                    intent = {"tool": "ask_clarify", "action": "delete_scope",
                              "params": {"target_type": "memory",
                                         "message": "你想删除全部记忆数据，还是最近一周的？还是最新一条？"},
                              "confidence": 0.6, "explanation": "降级删除+记忆→澄清"}
                elif any(kw in user_input for kw in ["全部", "所有", "清空", "数据"]):
                    # 无类型关键词 → 先确认类型
                    intent = {"tool": "ask_clarify", "action": "delete_scope",
                              "params": {"target_type": "unknown",
                                         "message": "你想删除哪种数据？是财务记录、日程待办、还是记忆数据？"},
                              "confidence": 0.6, "explanation": "降级删除无类型→先确认"}
                else:
                    intent = {"tool": "日程", "action": "delete_reminder",
                              "params": {"query": user_input},
                              "confidence": 0.6, "explanation": "降级删除关键词匹配"}
            elif any(kw in user_input for kw in FINANCE_KW):
                # v3.8.1: 区分记录和查询意图
                QUERY_KW = ["查看", "查一下", "查", "显示", "统计", "花了多少",
                            "一共", "列表", "最大", "有没有", "多少"]
                is_query = any(kw in user_input for kw in QUERY_KW)
                intent = {"tool": "财务",
                          "action": "query" if is_query else None,
                          "confidence": 0.5,
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
            elif action == "delete_scope":
                print(f"[路由] → LLM 判定: 澄清问询 "
                      f"(target={intent.get('params',{}).get('target_type','?')}, "
                      f"confidence={conf:.2f}, {expl})")
            else:
                print(f"[路由] → LLM 判定: {intent['tool']} (confidence={conf:.2f})")

        tool = intent["tool"]
        action = intent.get("action")
        confidence = intent.get("confidence", 0.7)

        # ============================================================
        # ask_clarify 拦截: 模糊删除需要澄清范围（v3.7.4）
        # ============================================================
        if tool == "ask_clarify":
            params = intent.get("params", {})
            message = params.get("message", "请明确要删除的范围（全部/最近一周/今天/最新一条）")
            target_type = params.get("target_type", "schedule")
            print(f"🤔 {message}")
            speak(message)
            _pending_clarification = {
                "target_type": target_type,
                "message": message,
            }
            continue

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
            if action == "query":
                print("🔧 正在查询财务记录...")
                query_params = intent.get("params")
                result = call_tool("财务", user_input, query_params,
                                   _func="query_finance")
            else:
                print("🔧 正在处理财务指令...")
                result = call_tool("财务", user_input)
            reply = _generate_tool_reply(result)
            print(reply)
            speak(reply)

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