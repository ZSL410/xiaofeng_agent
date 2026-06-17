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
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


def _load_todos():
    try:
        with open(TODOS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_todos(todos):
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


def _check_reminders():
    """检查所有未提醒的事件和待办，到期则播报"""
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    triggered = []

    # ---- 检查事件 ----
    events = _load_events()
    events_modified = False
    for event in events:
        if (event.get("date") == current_date
                and event.get("time", "") <= current_time
                and not event.get("notified", False)):
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
    todos_modified = False
    for todo in todos:
        if (not todo.get("completed", False)
                and todo.get("due_date", "") == current_date
                and todo.get("due_time", "")
                and todo.get("due_time", "") <= current_time
                and not todo.get("notified", False)):
            triggered.append(f"⏰ 待办提醒：{todo['title']}")
            todo["notified"] = True
            todos_modified = True
    if todos_modified:
        _save_todos(todos)

    # ---- 播报 ----
    if triggered:
        with _lock:
            for msg in triggered:
                print(msg)
                try:
                    speak(msg)
                except Exception as e:
                    print(f"⚠️ 提醒语音播报失败：{e}")


def _reminder_loop():
    """后台守护线程主循环：每 30 秒检查一次"""
    while True:
        time.sleep(30)
        try:
            _check_reminders()
        except Exception as e:
            print(f"⚠️ 提醒检查出错：{e}")


# 模块加载时启动守护线程（daemon=True 确保主进程退出时自动终止）
_reminder_thread = threading.Thread(
    target=_reminder_loop, daemon=True, name="schedule-reminder"
)
_reminder_thread.start()


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
"""


def _clean_title(raw_title):
    """从用户输入中清理出干净的标题文本"""
    # 移除已知的噪声词
    noise_words = [
        "添加", "新增", "创建", "加一个", "增加", "安排",
        "每天", "每周", "每月", "提醒", "记得",
        "上午", "下午", "晚上", "傍晚", "凌晨", "早上", "中午",
        "事件", "事项", "到期",
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
    # 匹配各种时间格式
    patterns = [
        r"(上午|下午|晚上|傍晚|凌晨|早上|中午)\s*\d+\s*点",
        r"\d{1,2}[:：]\d{2}",
        r"\d+\s*点\s*(半|\d+\s*分)",
        r"\d+\s*分钟?后",
        r"\d+\s*p\.?m",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def process_command(text):
    """日程模块主入口：解析自然语言指令并执行"""
    text = text.strip()
    if not text:
        return _HELP_TEXT

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
    m = re.search(r"(提醒|记得)\s*(?:我)?\s*(.*)", text)
    if m:
        reminder_content = m.group(2).strip()
        time_str, after_time = _parse_time(reminder_content)
        if time_str:
            date_str, after_date = _parse_date(reminder_content)
            title = _clean_title(after_time)
            if not title:
                title = _clean_title(reminder_content)
            if not title:
                title = "提醒事项"
            return _add_todo(title, date_str, time_str)
        else:
            return "⚠️ 请提供提醒时间，例如「提醒我5分钟后喝水」或「提醒我下午5点打电话」"

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
