"""
日程模块 — 晓风Agent 工具模块
功能：日常事件管理、待办事项管理、定时提醒
后台守护线程每 30 秒检查到期提醒，通过 嘴巴.py 播报
"""

import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta

# ===================== 路径与导入 =====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EVENTS_FILE = os.path.join(BASE_DIR, "events.json")
TODOS_FILE = os.path.join(BASE_DIR, "todos.json")

# 确保能找到 器官.嘴巴（用于 speak 播报提醒）
_CORE_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "00_核心主体"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)
try:
    from 器官.嘴巴 import speak
except ImportError:
    # 独立测试或无 TTS 环境时使用 print 替代
    def speak(text):
        print(f"🔊 [语音播报] {text}")

# ===================== 线程安全 =====================
_lock = threading.Lock()

# ===================== 数据管理 =====================


def _load_events():
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_events(events):
    _debug_log(f"[文件] 写入 events.json: 路径={EVENTS_FILE!r}, 条数={len(events)}")
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


def _load_todos():
    try:
        with open(TODOS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_todos(todos):
    _debug_log(f"[文件] 写入 todos.json: 路径={TODOS_FILE!r}, 条数={len(todos)}")
    with open(TODOS_FILE, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)


def _get_next_id():
    """扫描现有 events 和 todos，返回下一个可用 ID"""
    events = _load_events()
    todos = _load_todos()
    max_id = 0
    for e in events:
        if e.get("id", 0) > max_id:
            max_id = e["id"]
    for t in todos:
        if t.get("id", 0) > max_id:
            max_id = t["id"]
    return max_id + 1


# ===================== 时间解析 =====================


def _normalize_chinese_numbers(text):
    """
    将中文数字表达转换为阿拉伯数字，方便后续正则匹配。
    处理：半 → 0.5, 一→1, 两/二→2, ..., 十→10, 十五→15, 二十→20 等
    """
    # 数字映射
    CN_DIGITS = {
        "零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
        "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
    }
    # "半" → 特殊处理 "半小时后" → "30分钟后"（半小时 = 30 分钟）
    text = re.sub(r"半小?时?\s*(后|之后|以后)", r"30分钟\1", text)
    text = re.sub(r"半\s*分[钟]?\s*(后|之后|以后)", r"0.5分钟\1", text)

    # "X十Y" → 十位数（十五→15, 二十三→23）
    text = re.sub(
        r"([一二两三四五六七八九])十([一二两三四五六七八九])",
        lambda m: str(CN_DIGITS.get(m.group(1), "?")).rstrip("0")
        + str(CN_DIGITS.get(m.group(2), "?")),
        text
    )
    # "X十" → 整十（二十→20, 三十→30）
    text = re.sub(
        r"([二两三四五六七八九])十",
        lambda m: str(int(CN_DIGITS.get(m.group(1), "0")) * 10),
        text
    )
    # "十X" → 十几（十一→11, 十五→15）
    text = re.sub(
        r"十([一二两三四五六七八九])",
        lambda m: "1" + CN_DIGITS.get(m.group(1), "?"),
        text
    )
    # 单独的 "十" → "10"（在时间上下文中，"十分钟后" → "10分钟后"）
    text = re.sub(r"十\s*(分|秒)", r"10\1", text)

    # 最小单位的数字替换（用词边界避免 "一个小时" → 不需要转换的情况）
    # 只在"分钟"、"点"、"秒"前转换中文数字
    text = re.sub(
        r"([一二两三四五六七八九])\s*分",
        lambda m: CN_DIGITS.get(m.group(1), m.group(1)) + "分",
        text
    )
    text = re.sub(
        r"([一二两三四五六七八九])\s*点",
        lambda m: CN_DIGITS.get(m.group(1), m.group(1)) + "点",
        text
    )

    return text


def _parse_date(text):
    """
    从文本中提取日期，返回 (date_str, remaining_text)
    remaining_text 中去掉了日期关键词以便后续提取标题
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # 相对日期（优先匹配更长的词）
    if "后天" in text:
        d = (now + timedelta(days=2)).strftime("%Y-%m-%d")
        text = text.replace("后天", "").replace("后日", "")
        return d, text
    if "明天" in text or "明日" in text:
        d = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        text = text.replace("明天", "").replace("明日", "")
        return d, text
    if "今天" in text or "今日" in text:
        text = text.replace("今天", "").replace("今日", "")
        return today, text

    # 带年月日的日期格式：2026-06-15 或 2026/06/15
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        d = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        text = text.replace(m.group(0), "")
        return d, text

    # 月日：6月15日 / 6月15号
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        year = now.year
        if month < now.month:
            year += 1
        d = f"{year}-{month:02d}-{day:02d}"
        text = text.replace(m.group(0), "")
        return d, text

    # 星期几（简单处理）
    weekday_map = {
        "周一": 0, "星期二": 1, "周二": 1, "星期三": 2, "周三": 2,
        "星期四": 3, "周四": 3, "星期五": 4, "周五": 4,
        "星期六": 5, "周六": 5, "周日": 6, "星期日": 6,
    }
    for name, target_wd in weekday_map.items():
        if name in text:
            days_ahead = target_wd - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            d = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            # 移除星期名，并清理前置的"每"（"每周五" → "每周五".replace("周五","")会留下"每"）
            text = re.sub(r'每?' + re.escape(name), '', text, count=1).strip()
            return d, text

    return today, text


def _parse_time(text):
    """
    从文本中提取时间，返回 (time_str, remaining_text)
    remaining_text 中去掉了时间部分以便后续提取标题
    """
    # 预处理：将中文数字转为阿拉伯数字，方便正则匹配
    text = _normalize_chinese_numbers(text)
    time_str = ""
    remaining = text

    # 1. "X分钟后" / "X分钟之后" / "X分钟以后" — 相对时间
    m = re.search(r"(\d+)\s*分[钟]?\s*(后|之后|以后)", text)
    if m:
        mins = int(m.group(1))
        future = datetime.now() + timedelta(minutes=mins)
        time_str = future.strftime("%H:%M")
        remaining = text.replace(m.group(0), "")
        return time_str, remaining

    # 2. "下午3点半" / "上午9点20分"
    m = re.search(
        r"(下午|晚上|傍晚|凌晨|早上|上午|中午)?"
        r"\s*(\d{1,2})\s*点\s*"
        r"(?:(\d{1,2})\s*分)?\s*(半)?",
        text
    )
    if m:
        period = m.group(1) or ""
        hour = int(m.group(2))
        minute = 0
        if m.group(3):
            minute = int(m.group(3))
        elif m.group(4) == "半":
            minute = 30

        if period in ("下午", "晚上", "傍晚") and hour < 12:
            hour += 12
        elif period in ("凌晨", "早上", "上午") and hour == 12:
            hour = 0
        elif period == "中午" and hour < 12:
            hour = 12

        time_str = f"{hour:02d}:{minute:02d}"
        remaining = text.replace(m.group(0), "")
        return time_str, remaining

    # 3. "15:00" / "3:30pm" / "3:00 PM"
    m = re.search(r"(\d{1,2})[：:](\d{2})\s*(p\.?m\.?|P\.?M\.?|am|AM)?", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        suffix = (m.group(3) or "").lower().replace(".", "")
        if suffix in ("pm", "下午") and hour < 12:
            hour += 12
        elif suffix in ("am", "上午") and hour == 12:
            hour = 0
        time_str = f"{hour:02d}:{minute:02d}"
        remaining = text.replace(m.group(0), "")
        return time_str, remaining

    # 4. "3pm"（无冒号）
    m = re.search(r"(\d{1,2})\s*(p\.?m\.?|P\.?M\.?|a\.?m\.?)", text)
    if m:
        hour = int(m.group(1))
        suffix = m.group(2).lower().replace(".", "")
        minute = 0
        if suffix in ("pm", "下午") and hour < 12:
            hour += 12
        elif suffix in ("am", "上午") and hour == 12:
            hour = 0
        time_str = f"{hour:02d}:{minute:02d}"
        remaining = text.replace(m.group(0), "")
        return time_str, remaining

    return time_str, remaining


# ===================== LLM 时间解析 =====================

_TIME_PARSE_PROMPT = """你是一个精确的时间解析助手。当前时间是 {current_time}。

请将用户的自然语言表达转换为具体的日期和时间。只返回格式 "YYYY-MM-DD HH:MM"，不要任何解释、标点或额外文字。

解析规则（按优先级）：
1. "X叫我" / "X喊我" / "X叫我一下" / "X到点提醒"：在当前小时的第 X 分钟提醒。但如果当前分钟数 > X，则自动推到下一个小时。
   例：当前 14:35，用户说"26叫我" → 下一个 26 分是 15:26 → 返回当天 15:26
   例：当前 14:10，用户说"26叫我" → 本小时 26 分是 14:26 → 返回当天 14:26
2. "X分钟后"：当前时间 + X 分钟。如 "5分钟后" 就是当前时间加5分钟。
3. "半个小时" / "半小时后"：当前时间 + 30 分钟。
4. "X个小时后" / "X小时后"：当前时间 + X 小时。
5. "明天X点" / "明天上午X点" / "明天下午X点"：明天的对应时间。
6. "下午X点" / "上午X点" / "晚上X点"：当天的对应时间段。
7. "X:XX" / "XX:XX"：直接解析为当天时间。如果该时间已过，推到明天。
8. 如果无法解析，返回 "FAIL"

用户表达：{user_text}
时间："""


def _parse_time_with_llm(user_text, current_time=None):
    """
    使用本地 Ollama 模型将自然语言时间表达转为标准格式。
    超时或失败返回 (None, None)，由调用方降级处理。

    参数:
        user_text: 用户原始输入文本
        current_time: datetime 对象，默认取当前时间
    返回:
        (date_str, time_str) 如 ("2026-06-19", "15:26")，失败返回 (None, None)
    """
    if current_time is None:
        current_time = datetime.now()

    now_str = current_time.strftime("%Y-%m-%d %H:%M")
    prompt = _TIME_PARSE_PROMPT.format(current_time=now_str, user_text=user_text)

    try:
        body = json.dumps({
            "model": "qwen2.5:7b",
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 30, "temperature": 0},
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = (data.get("response", "") or "").strip()

        _debug_log(f"[LLM时间解析] 原始返回: {result!r}")

        # 清洗输出
        result = result.strip('"\'`\n\r 。.，,')
        if result.upper() == "FAIL" or not result:
            return None, None

        # 尝试匹配 "YYYY-MM-DD HH:MM" 格式
        m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", result)
        if m:
            return m.group(1), m.group(2)

        # 尝试匹配 "HH:MM" 格式（只有时间，补当天日期）
        m = re.search(r"(\d{2}:\d{2})", result)
        if m:
            return current_time.strftime("%Y-%m-%d"), m.group(1)

        _debug_log(f"[LLM时间解析] 无法从返回中提取时间: {result!r}")

    except Exception as e:
        print(f"⚠️ LLM 时间解析失败（将降级处理）：{e}")

    return None, None


# ===================== 日程事件 CRUD =====================


def _add_event(title, date_str, time_str, repeat="none"):
    events = _load_events()
    event = {
        "id": _get_next_id(),
        "title": title,
        "date": date_str,
        "time": time_str,
        "repeat": repeat,
        "notified": False,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    events.append(event)
    _save_events(events)
    repeat_msg = {"none": "", "daily": "（每天重复）", "weekly": "（每周重复）",
                  "monthly": "（每月重复）"}.get(repeat, "")
    return f"✅ 已添加日程：{title}（{date_str} {time_str}）{repeat_msg}"


def _delete_event(event_id):
    events = _load_events()
    for e in events:
        if e.get("id") == event_id:
            title = e["title"]
            events.remove(e)
            _save_events(events)
            return f"✅ 已删除日程：{title}"
    return f"❌ 未找到 ID 为 {event_id} 的日程"


def _list_events(date_str=None):
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    events = _load_events()
    today_events = [e for e in events if e.get("date") == date_str]
    if not today_events:
        return f"📅 {date_str} 没有日程安排"
    today_events.sort(key=lambda e: e.get("time", "00:00"))
    lines = [f"📅 {date_str} 的日程："]
    for e in today_events:
        repeat_tag = {"none": "", "daily": " 🔄每天",
                      "weekly": " 🔄每周", "monthly": " 🔄每月"}.get(
            e.get("repeat", "none"), "")
        lines.append(f"  [{e['id']}] {e['time']} {e['title']}{repeat_tag}")
    return "\n".join(lines)


# ===================== 待办事项 CRUD =====================


def _add_todo(title, due_date="", due_time=""):
    todos = _load_todos()
    todo = {
        "id": _get_next_id(),
        "title": title,
        "due_date": due_date,
        "due_time": due_time,
        "completed": False,
        "notified": False,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _debug_log(f"[写入] _add_todo: title={title!r}, due_date={due_date!r}, due_time={due_time!r}")
    todos.append(todo)
    _save_todos(todos)
    parts = [f"✅ 已添加待办：{title}"]
    if due_date and due_time:
        parts.append(f"（到期：{due_date} {due_time}）")
    elif due_date:
        parts.append(f"（到期：{due_date}）")
    elif due_time:
        parts.append(f"（到期时间：{due_time}）")
    return " ".join(parts)


def _delete_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t.get("id") == todo_id:
            title = t["title"]
            todos.remove(t)
            _save_todos(todos)
            return f"✅ 已删除待办：{title}"
    return f"❌ 未找到 ID 为 {todo_id} 的待办"


def _complete_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t.get("id") == todo_id:
            if t.get("completed"):
                return f"ℹ️ 待办「{t['title']}」已经完成了"
            t["completed"] = True
            _save_todos(todos)
            return f"✅ 已完成待办：{t['title']}"
    return f"❌ 未找到 ID 为 {todo_id} 的待办"


def _list_todos(show_all=False):
    todos = _load_todos()
    if not show_all:
        todos = [t for t in todos if not t.get("completed", False)]

    if not todos:
        return "📋 没有待办事项 🎉"

    # 排序：未完成优先 → 按到期时间排序
    def sort_key(t):
        dt = t.get("due_date", "") or "9999-99-99"
        tm = t.get("due_time", "") or "99:99"
        return (t.get("completed", False), dt, tm)

    todos_sorted = sorted(todos, key=sort_key)

    lines = ["📋 待办事项："]
    for t in todos_sorted:
        status = "✅" if t.get("completed") else "⬜"
        due = ""
        if t.get("due_date") and t.get("due_time"):
            due = f" ⏰{t['due_date']} {t['due_time']}"
        elif t.get("due_date"):
            due = f" ⏰{t['due_date']}"
        elif t.get("due_time"):
            due = f" ⏰{t['due_time']}"
        lines.append(f"  {status} [{t['id']}] {t['title']}{due}")
    return "\n".join(lines)


# ===================== 提醒检查（后台线程） =====================

# DEBUG 环境变量控制详细扫描日志（默认关闭，避免刷屏）
_DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


def _debug_log(msg):
    """仅在 DEBUG 模式下输出日志"""
    if _DEBUG:
        print(msg)


def _safe_speak(msg):
    """安全播报：捕获 speak() 内部所有异常，防止线程崩溃"""
    try:
        speak(msg)
    except Exception as e:
        print(f"⚠️ 提醒语音播报失败：{e}")


# ===================== 配置加载 =====================

_MODEL_CONFIG_PATH = os.path.normpath(
    os.path.join(BASE_DIR, "..", "..", "00_核心主体", "模型.json")
)


def _load_use_smart_reminder():
    """读取 模型.json 中的 use_smart_reminder 开关，默认 True"""
    try:
        with open(_MODEL_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("use_smart_reminder", True)
    except Exception:
        return True


# ===================== 智能提醒文案生成 =====================

_SMART_REMINDER_PROMPT = """你是一个温暖、体贴的私人提醒助手。根据用户设置的提醒内容，生成一句简短、温暖、个性化的提醒播报。

要求：
- 不超过 20 个字
- 语气温暖自然，像朋友在提醒
- 不要出现"提醒"二字
- 只输出播报句子本身，不要任何解释

原始提醒内容：{content}
播报句子："""


def _generate_smart_reminder(raw_msg, timeout=3):
    """
    调用本地 Ollama 模型生成个性化提醒文案。
    超时或失败则降级为通用文案。

    参数:
        raw_msg: 原始提醒消息，如 "⏰ 日程提醒：泡咖啡"
        timeout: HTTP 请求超时秒数
    返回:
        人性化播报字符串，不超过 20 字
    """
    # 检查开关
    if not _load_use_smart_reminder():
        return raw_msg

    # 提取提醒内容（去掉前缀标签）
    content = re.sub(r'^[⏰📅📋]\s*(日程|待办|任务)?\s*提醒[：:]?\s*', '', raw_msg).strip()
    if not content:
        return "您的提醒时间到了"

    prompt = _SMART_REMINDER_PROMPT.format(content=content)

    try:
        body = json.dumps({
            "model": "qwen2.5:7b",
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 30},
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = (data.get("response", "") or "").strip()

        # 清洗：去掉可能的引号、换行
        result = result.strip('"\'"\n\r ')
        if len(result) > 20:
            result = result[:20]
        if result:
            return result

    except Exception as e:
        print(f"⚠️ 智能提醒生成失败（降级为通用文案）：{e}")

    # 降级：返回通用文案
    return "您的提醒时间到了"


def _check_reminders():
    """检查所有未提醒的事件和待办，到期则播报"""
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    _debug_log(f"[提醒扫描] {now.strftime('%Y-%m-%d %H:%M:%S')} — 检查到期提醒...")

    triggered = []

    # ---- 检查事件 ----
    events = _load_events()
    _debug_log(f"[提醒扫描] 已加载 {len(events)} 条日程事件")
    events_modified = False
    for event in events:
        ev_date = event.get("date", "")
        ev_time = event.get("time", "")
        ev_notified = event.get("notified", False)
        if (ev_date == current_date
                and ev_time
                and ev_time <= current_time
                and not ev_notified):
            triggered.append(f"⏰ 日程提醒：{event['title']}")
            event["notified"] = True
            events_modified = True

            # 处理重复事件：计算下一次发生时间，重置通知
            if event.get("repeat", "none") != "none":
                try:
                    dt = datetime.strptime(event["date"], "%Y-%m-%d")
                except ValueError:
                    continue
                if event["repeat"] == "daily":
                    dt += timedelta(days=1)
                elif event["repeat"] == "weekly":
                    dt += timedelta(weeks=1)
                elif event["repeat"] == "monthly":
                    dt += timedelta(days=30)
                event["date"] = dt.strftime("%Y-%m-%d")
                event["notified"] = False
    if events_modified:
        _save_events(events)

    # ---- 检查待办 ----
    todos = _load_todos()
    _debug_log(f"[提醒扫描] 已加载 {len(todos)} 条待办事项")
    todos_modified = False
    for todo in todos:
        td_due_date = todo.get("due_date", "")
        td_due_time = todo.get("due_time", "")
        td_completed = todo.get("completed", False)
        td_notified = todo.get("notified", False)
        if (not td_completed
                and td_due_date == current_date
                and td_due_time
                and td_due_time <= current_time
                and not td_notified):
            triggered.append(f"⏰ 待办提醒：{todo['title']}")
            todo["notified"] = True
            todos_modified = True
    if todos_modified:
        _save_todos(todos)

    # ---- 播报 ----
    if triggered:
        print(f"🔔 提醒触发 ({now.strftime('%H:%M')})：共 {len(triggered)} 条")
        with _lock:
            for msg in triggered:
                print(msg)
                # 生成智能提醒文案（用于语音播报）
                smart_msg = _generate_smart_reminder(msg)
                # 在独立线程中调用 speak()，避免阻塞提醒检查循环
                try:
                    threading.Thread(
                        target=_safe_speak, args=(smart_msg,),
                        daemon=True, name="reminder-speak"
                    ).start()
                except Exception as e:
                    print(f"⚠️ 提醒语音播报线程启动失败：{e}")
    else:
        _debug_log(f"[提醒扫描] 暂无到期提醒")


def _reminder_loop():
    """后台守护线程主循环：每 30 秒检查一次"""
    _debug_log("[提醒线程] 后台提醒循环已启动，每30秒扫描一次")
    while True:
        try:
            time.sleep(30)
            _check_reminders()
        except Exception as e:
            import traceback
            print(f"⚠️ 提醒检查出错：{e}")
            traceback.print_exc()
            # 出错后短暂休眠，避免错误日志刷屏
            time.sleep(5)


# 模块加载时启动守护线程（daemon=True 确保主进程退出时自动终止）
_reminder_thread = None


def start_reminder_thread():
    """启动日程提醒后台线程（幂等：已运行时不会重复启动）"""
    global _reminder_thread
    if _reminder_thread is not None and _reminder_thread.is_alive():
        return  # 已经在运行
    _reminder_thread = threading.Thread(
        target=_reminder_loop, daemon=True, name="schedule-reminder"
    )
    _reminder_thread.start()
    _debug_log("🔔 日程提醒守护线程已启动 (每30秒检查一次)")


# 模块导入时自动启动
start_reminder_thread()


# ===================== 自然语言解析与入口 =====================

_HELP_TEXT = """
📋 日程模块使用说明：

  📅 日程管理：
    "添加会议明天下午3点"
    "添加每天9点起床"
    "删除日程1"
    "显示今天的日程"

  📋 待办管理：
    "添加任务买牛奶"
    "完成任务1"
    "删除任务1"
    "显示待办"

  ⏰ 提醒：
    "提醒我5分钟后喝水"
    "提醒我下午5点打电话"
    "5分钟后叫我"
    "明天8点叫醒我"
    "一分钟后喊我"
"""


def _clean_title(raw_title):
    """从用户输入中清理出干净的标题文本"""
    # 移除已知的噪声词
    noise_words = [
        "添加", "新增", "创建", "加一个", "增加", "安排",
        "每天", "每周", "每月", "提醒", "记得",
        "上午", "下午", "晚上", "傍晚", "凌晨", "早上", "中午",
        "事件", "事项", "到期",
        "叫醒我", "叫醒", "叫我", "喊我", "通知", "闹钟", "叫一下",
    ]
    for w in noise_words:
        raw_title = raw_title.replace(w, "")
    # 移除残留的时间数字（单独的数字）
    raw_title = re.sub(r'\b\d+\s*[点时分]\b', '', raw_title)
    raw_title = re.sub(r'\b\d{1,2}[:：]\d{2}\b', '', raw_title)
    raw_title = raw_title.strip()
    # 如果清理后为空，回退到原始输入中的关键词
    return raw_title


def _has_time_pattern(text):
    """检测文本中是否包含时间模式"""
    # 匹配各种时间格式（与 _parse_time 的正则保持一致）
    patterns = [
        r"(上午|下午|晚上|傍晚|凌晨|早上|中午)\s*\d+\s*点",
        r"\d{1,2}[:：]\d{2}",
        r"\d+\s*点\s*(半|\d+\s*分)",
        r"\d+\s*分[钟]?\s*(后|之后|以后)",   # 匹配 _parse_time 的 "X分钟后" 模式
        r"\d+\s*p\.?m",
        # 自然表达扩展（v2.7.2）
        r"\d{1,2}\s*(叫我|喊我|叫我一下|到点叫我|到点提醒|到时提醒)",  # "26叫我" 风格
        r"半个?\s*小?时\s*(后|之后|以后)?",                          # "半个小时" / "半小时后"
        r"\d+\s*个?\s*小?时\s*(后|之后|以后)",                        # "一小时后" / "2个小时后"
        r"(明天|后天|今天|明日)\s*\d*\s*点",                          # "明天8点" 无前缀
        r"(叫我|喊我|叫我一下|叫醒我)\s*$",                           # 纯提醒关键词结尾
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def process_command(text):
    """日程模块主入口：解析自然语言指令并执行"""
    text = text.strip()
    if not text:
        return _HELP_TEXT

    # 预处理 1: 规范化空格（语音识别可能引入多余空格）
    text = ' '.join(text.split())
    # 移除中文字符之间的空格（如 "叫 我" → "叫我"）
    text = re.sub(r'(?<=[一-鿿])\s+(?=[一-鿿])', '', text)
    # 预处理 2: 将中文数字转为阿拉伯数字，方便后续正则匹配
    original_text = text
    text = _normalize_chinese_numbers(text)
    if original_text != text:
        _debug_log(f"[解析] 中文数字转换: {original_text!r} → {text!r}")
    _debug_log(f"[解析] 处理后文本: {text!r}")

    # ==================== 日程事件 ====================

    # 添加事件：添加/新增/安排 + 标题 + 时间
    m = re.match(r"(添加|新增|创建|加一个|增加|安排)\s*(.*)", text)
    if m:
        raw = m.group(2).strip()
        # 检测是否为事件：有关键词或包含时间
        EVENT_KEYWORDS = ["会议", "事件", "日程", "安排", "约会", "聚会",
                          "开会", "会", "上课", "课", "面试", "活动",
                          "每天", "每周", "每月"]
        TODO_KEYWORDS = ["任务", "待办", "事项", "todo"]

        is_event = any(kw in text for kw in EVENT_KEYWORDS)
        is_todo = any(kw in text for kw in TODO_KEYWORDS)

        if is_todo:
            # 在下文 todo 分支处理，这里跳过
            pass
        elif is_event or _has_time_pattern(raw):
            repeat = "none"
            if "每天" in text:
                repeat = "daily"
            elif "每周" in text:
                repeat = "weekly"
            elif "每月" in text:
                repeat = "monthly"

            # 解析时间和日期
            date_str, after_date = _parse_date(raw)
            time_str, after_time = _parse_time(after_date)

            # 提取标题：先尝试从剩余文本中提取
            title = _clean_title(after_time)
            if not title:
                # 回退：从原始文本中提取
                for kw in EVENT_KEYWORDS:
                    if kw in text:
                        title = kw
                        break
            if not title:
                title = "日程事件"

            if not time_str:
                return "⚠️ 请提供时间，例如「添加会议明天下午3点」"

            return _add_event(title, date_str, time_str, repeat)
        elif not is_todo:
            # 非 todo 也非 event，给出提示
            return "⚠️ 请明确是日程事件（如「添加会议明天3点」）还是待办任务（如「添加任务买牛奶」）"

    # 删除事件：删除/移除/取消 日程/事件/会议 + 编号
    m = re.search(r"(删除|移除|取消)\s*(日程|事件|会议)\s*(\d+)", text)
    if m:
        return _delete_event(int(m.group(3)))

    # 查看日程
    if any(kw in text for kw in ["查看日程", "显示日程", "日程列表", "列出日程",
                                   "今天日程", "今天有什么", "日程安排",
                                   "我的日程", "日程"]):
        date_str, _ = _parse_date(text)
        return _list_events(date_str)

    # ==================== 提醒（→ 创建带到期的待办）====================
    # 匹配多种提醒表达：
    #   "提醒我5分钟后喝水"、"记得下午3点开会"
    #   "5分钟后叫我"、"明天8点叫醒我"、"一分钟后喊我"
    #   "26叫我"、"半个小时要洗衣服"、"一小时后提醒我"
    REMINDER_KEYWORDS_RE = r"提醒|记得|叫我|叫醒|喊我|通知|闹钟|叫我一下|到时提醒|到点提醒|到点叫我"
    has_reminder_kw = bool(re.search(REMINDER_KEYWORDS_RE, text))
    has_time = _has_time_pattern(text)
    _debug_log(f"[解析] 提醒检测: 关键词={has_reminder_kw}, 有时间模式={has_time}")

    if has_reminder_kw:
        # 第 1 步：尝试正则解析时间
        time_str, after_time = _parse_time(text)
        date_str = ""
        if time_str:
            date_str, after_date = _parse_date(text)
            _debug_log(f"[解析] 正则时间解析成功: date={date_str}, time={time_str}")
        else:
            # 第 2 步：正则失败，尝试 LLM 时间解析
            _debug_log(f"[解析] 正则解析失败，尝试 LLM 时间解析...")
            llm_date, llm_time = _parse_time_with_llm(text)
            if llm_time:
                time_str = llm_time
                date_str = llm_date
                after_time = text  # LLM 已消费全文，剩余文本用原文提取标题
                _debug_log(f"[解析] LLM 时间解析成功: date={date_str}, time={time_str}")
            else:
                # 第 3 步：降级兜底 — 当前时间 + 1 分钟，保证提醒不丢失
                fallback = datetime.now() + timedelta(minutes=1)
                time_str = fallback.strftime("%H:%M")
                date_str = fallback.strftime("%Y-%m-%d")
                after_time = text
                print(f"⚠️ 时间解析失败，使用 1 分钟后（{date_str} {time_str}）作为保底")

        if time_str:
            # 清理提醒关键词前缀（如 "提醒我喝水" → "喝水"）
            cleaned = re.sub(
                r'^(提醒|记得|叫我|叫醒|叫醒我|喊我|通知|闹钟|叫我一下|到时提醒|到点提醒|到点叫我)\s*(我)?\s*',
                '', after_time
            )
            _debug_log(f"[解析] 标题清理: after_time={after_time!r} → cleaned={cleaned!r}")
            title = _clean_title(cleaned)
            if not title:
                title = _clean_title(after_time)
            if not title:
                title = _clean_title(text)
            if not title:
                title = "提醒事项"
            _debug_log(f"[解析] 最终标题: {title!r}, date={date_str}, time={time_str}")
            return _add_todo(title, date_str, time_str)
        else:
            return "⚠️ 请提供提醒时间，例如「提醒我5分钟后喝水」或「5分钟后叫我」"

    # ==================== 待办事项 ====================

    # 添加待办
    m = re.search(r"(添加|新增|创建|加一个)\s*(任务|待办|事项|todo)\s*(.*)", text, re.IGNORECASE)
    if m:
        raw = m.group(3).strip()
        # 尝试提取到期时间（链式调用：先日期后时间，传递剩余文本以清洁标题）
        due_date, after_date = _parse_date(raw)
        due_time, after_time = _parse_time(after_date)
        title = _clean_title(after_time)
        if not title:
            title = _clean_title(raw)
        if not title:
            return "⚠️ 请描述任务内容，例如「添加任务买牛奶」"
        return _add_todo(title, due_date, due_time)

    # 删除待办
    m = re.search(r"(删除|移除)\s*(任务|待办)\s*(\d+)", text)
    if m:
        return _delete_todo(int(m.group(3)))

    # 完成待办
    m = re.search(r"(完成|做完|搞定|勾掉|标记完成)\s*(任务|待办)\s*(\d+)", text)
    if m:
        return _complete_todo(int(m.group(3)))

    # 查看待办
    if any(kw in text for kw in ["显示待办", "查看待办", "列出待办", "待办列表",
                                   "我的任务", "显示任务", "查看任务",
                                   "有什么任务", "待办事项", "待办", "todo",
                                   "tasks"]):
        show_all = "全部" in text or "所有" in text
        return _list_todos(show_all=show_all)

    # ==================== 帮助 ====================
    if any(kw in text for kw in ["帮助", "help", "说明", "功能"]):
        return _HELP_TEXT

    return _HELP_TEXT


# ===================== 最近提醒操作（修正机制用） =====================


def delete_last_reminder():
    """
    删除最近创建的一条未完成的待办提醒（按 created_at 时间戳排序）。

    用于"修正"场景：用户说"不是X，是Y"时，先删旧提醒再建新提醒。

    返回:
        (title, date_str, time_str) | None — 被删除提醒的信息，无提醒时返回 None
    """
    todos = _load_todos()
    if not todos:
        return None
    # 按创建时间降序，取最新的未完成项
    todos_sorted = sorted(todos, key=lambda t: t.get("created_at", ""), reverse=True)
    last = todos_sorted[0]
    title = last.get("title", "")
    date_str = last.get("due_date", "")
    time_str = last.get("due_time", "")
    # 从列表中移除并保存
    todos.remove(last)
    _save_todos(todos)
    _debug_log(f"[修正] 已删除最近提醒: title={title!r}, date={date_str}, time={time_str}")
    return (title, date_str, time_str)


def modify_last_reminder(new_params):
    """
    修改最近创建的提醒：删除旧的，用新参数创建。

    用于路由层的 correct_reminder 动作：用户纠正提醒参数后，
    删除旧提醒并以修正后的参数创建新提醒。

    参数:
        new_params: dict，与 add_reminder_from_params 格式相同

    返回:
        str  操作结果消息
    """
    old = delete_last_reminder()
    if old is None:
        # 没有旧的可删，直接当作新提醒创建
        _debug_log("[修正] 无可删除的旧提醒，按新提醒创建")
        return add_reminder_from_params(new_params)

    old_title, old_date, old_time = old
    old_desc = f"「{old_title}」"
    if old_date and old_time:
        old_desc += f"（{old_date} {old_time}）"

    # 用新参数创建
    result = add_reminder_from_params(new_params)
    _debug_log(f"[修正] 已完成修改: {old_desc} → 新提醒")
    return f"已修改提醒：{old_desc} → {result}"


# ===================== 结构化参数入口（路由层直调） =====================


def add_reminder_from_params(params):
    """
    接收路由层传来的结构化参数，直接创建提醒（待办事项）。

    这是 process_command() 的上层替代路径：
    - 路由层（脑.py）已完成 NLU 解析
    - 本函数只负责将结构化参数转为待办条目
    - 避免了在日程模块内重复解析自然语言

    参数:
        params: dict，包含以下字段:
            - time_offset: int | None  相对分钟数（如 5 表示 5 分钟后）
            - absolute_time: str | None  绝对时钟时间，格式 "HH:MM"（如 "14:30"）
            - content: str  提醒的核心内容（如 "泡咖啡"、"喝水"）
            - raw_text: str  用户原始输入全文（降级回退用）

    返回:
        str  操作结果消息，格式与 process_command() 兼容
    """
    if not params or not isinstance(params, dict):
        return "⚠️ 提醒参数为空或格式不正确"

    content = (params.get("content") or "").strip()
    raw_text = (params.get("raw_text") or "").strip()
    time_offset = params.get("time_offset")
    absolute_time = params.get("absolute_time")

    now = datetime.now()
    _debug_log(f"[结构化提醒] 收到 params: time_offset={time_offset!r}, "
               f"absolute_time={absolute_time!r}, content={content!r}, raw_text={raw_text!r}")

    # ---- 第 1 步：根据参数计算目标时间 ----
    if time_offset is not None:
        # 相对时间偏移（最可靠）
        try:
            time_offset = int(time_offset)
        except (ValueError, TypeError):
            time_offset = 1
        future = now + timedelta(minutes=max(1, time_offset))
        date_str = future.strftime("%Y-%m-%d")
        time_str = future.strftime("%H:%M")
        _debug_log(f"[结构化提醒] 相对时间: +{time_offset}分钟 → {date_str} {time_str}")

    elif absolute_time and isinstance(absolute_time, str):
        # 绝对时钟时间
        time_str = absolute_time.strip()
        date_str = now.strftime("%Y-%m-%d")
        # 验证格式
        try:
            target_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            # 如果时间已过，推到明天
            if target_dt <= now:
                target_dt += timedelta(days=1)
                date_str = target_dt.strftime("%Y-%m-%d")
                _debug_log(f"[结构化提醒] 时间已过，推到明天: {date_str} {time_str}")
        except ValueError:
            _debug_log(f"[结构化提醒] 时间格式异常: {time_str!r}，回退到 LLM 解析")
            time_str = ""
            date_str = ""

    else:
        # 两者都为 None：降级处理
        time_str = ""
        date_str = ""

    # ---- 第 2 步：时间解析失败时，尝试从 raw_text 降级解析 ----
    if not time_str:
        _debug_log(f"[结构化提醒] 参数中无有效时间，尝试从 raw_text 解析...")
        if raw_text:
            # 先尝试正则
            time_str, _ = _parse_time(_normalize_chinese_numbers(raw_text))
            if time_str:
                date_str, _ = _parse_date(raw_text)
                _debug_log(f"[结构化提醒] 正则降级成功: {date_str} {time_str}")
            else:
                # 再尝试 LLM
                llm_date, llm_time = _parse_time_with_llm(raw_text)
                if llm_time:
                    time_str = llm_time
                    date_str = llm_date
                    _debug_log(f"[结构化提醒] LLM 降级成功: {date_str} {time_str}")

        if not time_str:
            # 最终兜底：1 分钟后
            fallback = now + timedelta(minutes=1)
            time_str = fallback.strftime("%H:%M")
            date_str = fallback.strftime("%Y-%m-%d")
            print(f"⚠️ 结构化提醒时间解析失败，使用 1 分钟后（{date_str} {time_str}）作为保底")

    # ---- 第 3 步：内容兜底 ----
    if not content:
        # 尝试从 raw_text 中提取
        if raw_text:
            cleaned = re.sub(
                r'(提醒|记得|叫我|叫醒|叫醒我|喊我|通知|闹钟|教我|叫我一下|到时提醒|到点提醒|到点叫我)\s*(我)?\s*',
                '', raw_text
            )
            content = _clean_title(cleaned)
        if not content:
            content = "提醒事项"

    _debug_log(f"[结构化提醒] 最终: title={content!r}, date={date_str}, time={time_str}")
    return _add_todo(content, date_str, time_str)


# ===================== 独立测试入口 =====================
if __name__ == "__main__":
    print("🧪 日程模块测试模式")
    print("输入命令测试日程功能，输入 exit 退出")
    print(_HELP_TEXT)
    while True:
        try:
            test_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 退出测试")
            break
        if test_input == "exit":
            break
        print(process_command(test_input))
