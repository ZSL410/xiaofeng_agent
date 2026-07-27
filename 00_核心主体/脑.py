import sys
import os
import re
import json
import subprocess
import urllib.request

VERSION = "3.9.2"

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

_ROUTE_PROMPT_V2 = """你是晓风Agent的意图路由器。分析用户输入，判断意图并提取结构化参数。

当前时间:{current_time}

## 🚨 删除关键词优先规则（最高优先级）

一旦用户输入中包含 "删除"/"删掉"/"清空"/"清除"/"去掉" 中的任意一个，**必须判定为删除意图**，不再判断财务/日程/聊天等其他意图。即使同时出现 "财务"/"记账"/"查看"/"花了" 等词，删除优先。

例外：当否定词（不/不是/不要/别/并非/无需）修饰"删除"时，排除删除意图，重新判断。
- "删除今天的财务数据" → 删除意图（有"删除"，虽然有"财务"）
- "把今天这笔的金额删除" → 删除意图（有"删除"，虽然有"金额"）
- "不要删除数据" → 否定词修饰"删除" → 排除删除 → 重新判断
- "查看所有财务数据" → 无"删除" → 正常判断

## ⚠️ 否定词规则

否定词列表: 不 / 没 / 别 / 不是 / 并非 / 不要 / 不用 / 无需 / 没有想 / 不想

否定词会反转或取消其修饰的意图关键词，被否定的意图 → 排除该意图 → 按其他规则重新判断。否定词必须紧邻或修饰意图动词才生效（如"不是很好，删除吧"→"不是"修饰"好"，不修饰"删除"，仍为删除意图）。

- "查看数据不是删除" → "不是"+删除 → 排除删除 → 判断为查询
- "不是想删除，是想看看" → "不是"+删除 → 排除删除 → "看看" → 查询

## 优先级规则

1. 先应用🚨删除规则（见顶部）
2. 再应用否定词规则（见上节）
3. 剩余 → 进入模块判断（财务/日程/聊天），按各自细分规则区分 action

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
查看/添加/完成任务等日程管理操作。含"删除"关键词的文字不路由到此。

### 财务

当意图判定为"财务"时，按以下规则区分 action：

**查询 (action: "query")** — 触发词（任一命中即判定为查询）:
"查看"、"查询"、"显示"、"列出"、"统计"、"看看"、"看一下"、"有没有"、"多少"、"花了多少"、"一共"、"最大"、"最贵"、"最少"、"列表"、"查一下"、"看一看"
例: "查看所有财务数据"、"统计本月开销"、"最近花了多少钱"

**记账 (action: "record")** — 触发词 + 含金额数字:
"花了"、"付了"、"消费"、"支出"、"买了"、"花费"、"用了"、"记账"
⚠️ 必须包含金额数字（阿拉伯或中文），无金额则不是 record。
例: "今天吃饭花了十元"、"午餐花了25"

**默认**: 有金额→record，无金额且无查询触发词→ask_clarify

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

决策链（按顺序，不可跳跃）:
1. 🚨 含删除关键词 → 删除流程（ask_clarify 或 delete_reminder），否定词修饰"删除"时例外
2. 含其他否定词 → 排除被否定意图 → 重判
3. 剩余 → 财务(query/record细分) / 日程 / 聊天

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

# ===================== 3b 紧凑路由（v3.9.0） =====================

_ROUTE_PROMPT_3B = """你是晓风意图路由器。分析用户输入，判断意图并提取参数。只输出JSON，不输出任何解释。

当前时间:{current_time}

## 输出格式
{{"intent":"record|query|delete|remind|chat|clarify","target":"finance|schedule|memory|null","params":{{"amount":数字或null,"time":"today|yesterday|this_week|this_month|last_week|null","source":"类别或null","scope":"all|today|latest|last_week|keyword|null","keyword":"搜索词或null","time_offset":分钟数或null,"absolute_time":"HH:MM或null","content":"提醒内容或null"}},"confidence":0.0~1.0}}

## 意图判定规则

### record（记账）
- 必须包含金额（数字或中文数字） + 花费关键词
- 触发词：花了、付了、买了、消费、支出、花费、用了、记账
- amount提取为数字，"十元"→10，"三块五"→3.5
- 同时提取source（吃饭/购物/交通等）和time（默认today）
- target固定为finance
- ⚠️ 只有record意图才提取amount，其他意图amount一律为null

### query（查询）
- 查看数据，不修改
- 触发词：查看、查询、显示、列出、统计、看看、多少、列表、有没有、一共
- 提取time和scope（"所有"→all，"今天"→today，"最近"→latest，"本周"→this_week）
- amount必须为null（查询场景不提取金额）
- target按对象词推断：财务/记账/花了/消费→finance，日程/提醒/待办/任务/叫我→schedule，记忆/记住→memory
- 当target=schedule时，根据输入填充scope或time（如"今天的待办"→scope:"today"，"本周任务"→time:"this_week"）

### delete（删除）
- 🚨最高优先级：含删除/删掉/清空/清除/去掉 → delete
- ⚠️否定规则：当用户说"不要删除"、"不是删除"、"别删"时，意图必须标记为chat或clarify，不触发删除。否定词与"删除"之间可以隔着少量内容（如"不要删除财务数据"仍是否定删除，"不想删"也判定为否定）
- 提取scope：含"全部/所有/清空"→all，含"今天"→today，含"最近一周"→last_week
- amount必须为null（删除场景不提取金额）
- target按被删除对象推断："财务数据/记账/账单"→finance，"日程/提醒/待办"→schedule，"记忆"→memory

### remind（提醒）
- 用户想在某个时间被提醒做某事
- 触发词：提醒、叫我、喊我、通知、闹钟、叫醒、X分钟后、X小时后
- time_offset：相对分钟数（"一分钟后"→1，"半小时后"→30，"两小时后"→120）
- absolute_time：具体时刻（"8点"→"08:00"，"明天8点"→"08:00"），结合当前时间推断
- content：去掉时间词和触发词后的核心内容（≤15字），如无明确内容则填"提醒"
- target固定为schedule
- amount必须为null

### chat（聊天）
- 无工具意图的日常对话
- 问候、闲聊、确认词（是/对/好）、简单问答、否定删除的语句
- target为null，所有params字段为null

### clarify（澄清）
- 输入模糊不完整，无法确定意图
- "处理一下"/"那个"/"帮我弄一下" 缺少具体内容
- 或同时包含矛盾指令
- target为null，所有params字段为null

## target推断优先级
1. 含 财务/记账/花了/消费/收入/支出/账单 → finance
2. 含 日程/提醒/待办/任务/事件/叫我/闹钟 → schedule
3. 含 记忆/记住 → memory
4. chat、clarify和remind → 按规则自动确定（remind→schedule）
5. 仅含"数据"/"记录"无模块词 → null

## 置信度指南
- 0.9-1.0：意图明确 + 参数完整（如"今天吃饭花了20元"）
- 0.7-0.85：意图清晰但部分参数模糊（如"查一下"）
- 0.5-0.65：有歧义或参数残缺（如"那个删了"）
- <0.5：纯猜测，不应出现

## 示例

输入：今天吃饭花了二十元
输出：{{"intent":"record","target":"finance","params":{{"amount":20,"time":"today","source":"吃饭","scope":null,"keyword":null,"time_offset":null,"absolute_time":null,"content":null}},"confidence":0.95}}

输入：查看本月所有开销
输出：{{"intent":"query","target":"finance","params":{{"amount":null,"time":"this_month","source":null,"scope":"all","keyword":null,"time_offset":null,"absolute_time":null,"content":null}},"confidence":0.9}}

输入：删除昨天的财务记录
输出：{{"intent":"delete","target":"finance","params":{{"amount":null,"time":"yesterday","source":null,"scope":"today","keyword":null,"time_offset":null,"absolute_time":null,"content":null}},"confidence":0.9}}

输入：不要删除数据
输出：{{"intent":"chat","target":null,"params":{{"amount":null,"time":null,"source":null,"scope":null,"keyword":null,"time_offset":null,"absolute_time":null,"content":null}},"confidence":0.85}}

输入：一分钟后提醒我喝水
输出：{{"intent":"remind","target":"schedule","params":{{"amount":null,"time":null,"source":null,"scope":null,"keyword":null,"time_offset":1,"absolute_time":null,"content":"喝水"}},"confidence":0.95}}

输入：显示今天的待办
输出：{{"intent":"query","target":"schedule","params":{{"amount":null,"time":"today","source":null,"scope":"today","keyword":null,"time_offset":null,"absolute_time":null,"content":null}},"confidence":0.9}}

输入：你好
输出：{{"intent":"chat","target":null,"params":{{"amount":null,"time":null,"source":null,"scope":null,"keyword":null,"time_offset":null,"absolute_time":null,"content":null}},"confidence":0.95}}

输入：处理一下
输出：{{"intent":"clarify","target":null,"params":{{"amount":null,"time":null,"source":null,"scope":null,"keyword":null,"time_offset":null,"absolute_time":null,"content":null}},"confidence":0.6}}

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

        host = OLLAMA_HOST if OLLAMA_HOST else "http://127.0.0.1:11434"
        url = f"{host.rstrip('/')}/api/generate"
        req = urllib.request.Request(
            url, data=body,
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
                        # ask_clarify: 确保 params 存在并设置默认值
                        if not isinstance(params, dict):
                            params = {}
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


# ===================== 3b 紧凑路由函数（v3.9.0） =====================

def _call_ollama_3b(prompt, timeout=3):
    """调用 qwen2.5:3b 进行路由判断，返回原始响应文本，失败返回 None。"""
    body = json.dumps({
        "model": "qwen2.5:3b",
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 256, "temperature": 0.1},
    }, ensure_ascii=False).encode("utf-8")

    host = OLLAMA_HOST if OLLAMA_HOST else "http://127.0.0.1:11434"
    url = f"{host.rstrip('/')}/api/generate"
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (data.get("response", "") or "").strip()
    except Exception:
        return None


def _route_with_3b(user_input):
    """
    使用 qwen2.5:3b + _ROUTE_PROMPT_3B 进行意图路由（v3.9.0）。
    成功返回 {"intent", "target", "params", "confidence"}，失败返回 None。
    区别于 _route_intent（7b→3b→关键词链），本函数只使用 3b + 紧凑 prompt，
    目标是快速（<3s）完成语义路由。
    """
    from datetime import datetime

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = _ROUTE_PROMPT_3B.format(
        current_time=current_time,
        user_input=user_input,
    )

    result = _call_ollama_3b(prompt)
    if not result:
        return None

    # 清洗：去掉可能的 markdown 代码块
    result = re.sub(r'^```(?:json)?\s*', '', result)
    result = re.sub(r'\s*```$', '', result)
    result = result.strip()

    try:
        parsed = json.loads(result)
        if not isinstance(parsed, dict):
            return None

        intent = parsed.get("intent")
        if intent not in ("record", "query", "delete", "remind", "chat", "clarify"):
            return None

        params = parsed.get("params", {})
        if not isinstance(params, dict):
            params = {}

        return {
            "intent": intent,
            "target": parsed.get("target"),
            "params": params,
            "confidence": float(parsed.get("confidence", 0.7)),
        }
    except (json.JSONDecodeError, ValueError, TypeError):
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

    # 取消（v3.9.1 增强：含否定删除模式如"不要删除数据"/"别删财务"等）
    CANCEL_EXACT = ("算了", "取消", "不删了", "不用了", "不要了", "不删", "不了")
    if text in CANCEL_EXACT or _is_deletion_negated(text):
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


def _is_deletion_negated(user_input):
    """
    v3.9.1: 检测用户是否在否定删除请求（如"不要删除数据"）。
    返回 True 表示用户明确拒绝删除，应阻断所有删除流程。

    匹配策略：否定词（不要/不是/别/不想/不）+ 删除词（删除/删/清空/清除）。
    前缀匹配，不要求后续有数据关键词——任何"不要删除*"都应阻断。
    """
    NEGATION_PATTERNS = [
        r'不要删',       # 不要删除/不要删掉/不要删数据/不要删除记录 ...
        r'不是删',       # 不是删除/不是删数据 ...
        r'别删',         # 别删/别删除/别删数据 ...
        r'不想删',       # 不想删除/不想删数据 ...
        r'不删',         # 不删/不删除/不删了 ...
        r'不要清空',     # 不要清空/不要清空数据 ...
        r'不想清空',     # 不想清空/不想清空数据 ...
        r'别清空',       # 别清空 ...
        r'不是清空',     # 不是清空 ...
        r'不要清除',     # 不要清除 ...
        r'不想清除',     # 不想清除 ...
        r'别清除',       # 别清除 ...
    ]
    for pat in NEGATION_PATTERNS:
        if re.search(pat, user_input):
            return True
    return False


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
        # v3.9.1: 删除否定检查（最高优先级，在所有状态机之前）
        # 用户说"不要删除数据"/"别删"等时，无论是否有待处理的状态机，
        # 一律立即确认不删除并跳过所有后续流程。
        # ============================================================
        if _is_deletion_negated(user_input):
            print("🛡️ 检测到删除否定，已阻断所有删除路径")
            reply = "好的，不会删除任何数据。如果你需要查看或修改数据，可以告诉我。"
            print(f"晓风: {reply}")
            speak(reply)
            # 清除任何挂起的删除澄清/确认状态
            _pending_clarification = None
            _pending_confirmation = None
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
        # v3.9.0: 3b 紧凑路由优先（快速语义分类，<3s）
        # ============================================================
        print(f"[路由] 收到输入: {user_input!r}")

        # ============================================================
        # v3.9.1: 删除否定检查 —— 用户说"不要删除数据"时直接阻断
        # 在所有路由/工具调用之前拦截，防止 3b 误判或降级路径意外触发删除
        # ============================================================
        if _is_deletion_negated(user_input):
            print("🛡️ 检测到删除否定，已阻断删除路径")
            reply = "好的，不会删除任何数据。如果你需要查看或修改数据，可以告诉我。"
            print(f"晓风: {reply}")
            speak(reply)
            continue

        result_3b = _route_with_3b(user_input)
        if result_3b is not None and result_3b.get("confidence", 0) >= 0.6:
            intent_3b = result_3b["intent"]
            target_3b = result_3b.get("target")
            params_3b = result_3b.get("params", {})
            conf_3b = result_3b["confidence"]

            print(f"[路由] → 3b 判定: {intent_3b}"
                  + (f" (target={target_3b})" if target_3b else "")
                  + f" confidence={conf_3b:.2f}")

            # ---- record: 记账 ----
            if intent_3b == "record":
                print("🔧 正在处理财务指令（3b路由）...")
                result = call_tool("财务", user_input)
                reply = _generate_tool_reply(result)
                print(reply)
                speak(reply)
                continue

            # ---- query: 查询 ----
            elif intent_3b == "query":
                if target_3b == "finance":
                    print("🔧 正在查询财务记录（3b路由）...")
                    result = call_tool("财务", params_3b, _func="query_finance")
                elif target_3b == "schedule":
                    print("🔧 正在查询日程（3b路由）...")
                    # schedule_module 无独立 query_schedule；
                    # process_command 已内置 NLU 列表/查看逻辑
                    result = call_tool("日程", user_input)
                else:
                    # target 不明确时默认查财务
                    print("🔧 正在查询财务记录（3b路由，默认finance）...")
                    result = call_tool("财务", params_3b, _func="query_finance")

                reply = _generate_tool_reply(result)
                print(reply)
                speak(reply)
                continue

            # ---- delete: 删除 ----
            elif intent_3b == "delete":
                scope = params_3b.get("scope", "keyword") if isinstance(params_3b, dict) else "keyword"
                keyword = params_3b.get("keyword") if isinstance(params_3b, dict) else None

                if target_3b == "finance":
                    # v3.9.2: 如果已有时限或范围，直接执行，不反问
                    time = params_3b.get("time") if isinstance(params_3b, dict) else None
                    scope_from_params = params_3b.get("scope") if isinstance(params_3b, dict) else None

                    # 可直接映射到 delete_by_scope 的范围值
                    DIRECT_SCOPES = ("all", "today", "latest", "last_week")

                    if scope_from_params in DIRECT_SCOPES:
                        print(f"🔧 正在按范围删除财务记录（scope={scope_from_params}，3b路由）...")
                        result = call_tool("财务", scope_from_params, _func="delete_by_scope")
                        reply = _generate_tool_reply(result, prefix="明白了")
                        print(reply)
                        speak(reply)
                    elif time in DIRECT_SCOPES:
                        # time 字段可直接用作 scope（如 today / last_week）
                        print(f"🔧 正在按时间删除财务记录（time={time}，3b路由）...")
                        result = call_tool("财务", time, _func="delete_by_scope")
                        reply = _generate_tool_reply(result, prefix="明白了")
                        print(reply)
                        speak(reply)
                    else:
                        # 无明确时间/范围 → 进入澄清流程
                        message = "你想删除全部财务记录，还是最近一周的？还是最新一条？"
                        print(f"🤔 {message}")
                        speak(message)
                        _pending_clarification = {
                            "target_type": "finance",
                            "message": message,
                        }
                elif target_3b == "memory":
                    message = "你想删除全部记忆数据，还是最近一周的？还是最新一条？"
                    print(f"🤔 {message}")
                    speak(message)
                    _pending_clarification = {
                        "target_type": "memory",
                        "message": message,
                    }
                elif target_3b == "schedule":
                    print("🔧 正在按范围删除日程（3b路由）...")
                    if scope == "keyword" and keyword:
                        result = call_tool("日程", scope, keyword,
                                          _func="delete_by_scope")
                    else:
                        result = call_tool("日程", scope,
                                          _func="delete_by_scope")
                    reply = _generate_tool_reply(result, prefix="明白了")
                    print(reply)
                    speak(reply)
                else:
                    # target 不明确，先确认类型
                    message = "你想删除哪种数据？是财务记录、日程待办、还是记忆数据？"
                    print(f"🤔 {message}")
                    speak(message)
                    _pending_clarification = {
                        "target_type": "unknown",
                        "message": message,
                    }
                continue

            # ---- remind: 设置提醒 ----
            elif intent_3b == "remind":
                print("🔧 正在处理日程提醒（3b路由）...")
                result = call_tool("日程", params_3b,
                                  _func="add_reminder_from_params")
                print(result)
                speak(result)
                continue

            # ---- chat: 交给 7b 对话 ----
            elif intent_3b == "chat":
                # v3.9.1: 如果用户输入包含删除否定，直接确认而非走普通对话
                if _is_deletion_negated(user_input):
                    print("🛡️ 检测到删除否定（chat路由），已阻断")
                    reply = "好的，不会删除任何数据。如果你需要查看或修改数据，可以告诉我。"
                    print(f"晓风: {reply}")
                    speak(reply)
                    continue
                print("[路由] → 3b 判定为聊天，转交 7b 对话")
                short_term.append({"role": "user", "content": user_input})
                response = chat_with_xiaofeng(user_input, short_term)
                print(f"晓风: {response}")
                speak(response)
                short_term.append({"role": "assistant", "content": response})
                save_short_term(short_term)
                continue

            # ---- clarify: 请求澄清 ----
            elif intent_3b == "clarify":
                print("🤔 不太确定你的意思，请再说清楚一点"
                      "（比如'今天吃饭花了20元'或'查看今天的记录'）")
                speak("不太确定你的意思，请再说清楚一点")
                continue

        # ============================================================
        # 降级：使用原有路由逻辑（7b → 3b → 关键词匹配）
        # ============================================================
        intent = _route_intent(user_input)

        # LLM 路由失败时降级为关键词匹配
        if intent is None:
            FINANCE_KW = ["记账", "查账", "花了", "报销", "帮我记"]
            SCHEDULE_KW = ["日程", "提醒", "待办", "todo", "任务",
                           "叫我", "教", "叫醒", "喊我", "通知", "闹钟", "分钟后"]
            CORRECTION_KW = ["不是", "说错了", "改成", "应该是", "不对", "换个"]
            DELETE_KW = ["删掉", "删", "删除", "取消", "去掉", "移除"]
            # 删除关键词优先（v3.7.5 / v3.7.6 增强）：在检查财务/日程之前先判断删除意图
            # v3.9.1: 排除否定删除（如"不要删除数据"），防止降级路径误触发删除
            if any(kw in user_input for kw in DELETE_KW) and not _is_deletion_negated(user_input):
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
        # 低置信度拦截（v3.1.1，v3.8.7 扩展到财务/删除）
        # ============================================================
        if confidence < 0.6:
            if tool == "日程" and action == "add_reminder" and intent.get("params"):
                desc = _describe_reminder(intent["params"])
                print(f'🤔 你是想说「{desc}」吗？(回复"是"或"不是")')
                _pending_confirmation = {
                    "intent": intent,
                    "raw_input": user_input,
                }
                continue
            if tool == "财务":
                print("🤔 不太确定你的意思，请再说清楚一点"
                      "（比如'今天吃饭花了20元'或'查看今天的记录'）")
                speak("不太确定你的意思，请再说清楚一点")
                continue
            if tool == "日程" and action in ("delete_reminder", "manage"):
                print("🤔 不太确定你的意思，请再说清楚一点")
                speak("不太确定你的意思，请再说清楚一点")
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