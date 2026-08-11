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


# v3.9.29: 记忆系统集成 —— 记录日程/待办操作到统一记忆库（失败不影响主流程）
def _record_memory_event(title, detail, tags=None):
    """将一次日程/待办操作写入统一记忆库 memory.json（add_memory_event）。"""
    try:
        from 记忆.记忆引擎 import add_memory_event
        add_memory_event(
            title=title,
            detail=detail,
            tags=tags or ["日程"],
            importance=5,
            subtype="event",
        )
    except Exception as e:
        print(f"⚠️ 记忆记录失败(忽略):{e}")

# ===================== 线程安全 =====================
_lock = threading.Lock()

# ===================== 数据管理 =====================


def _load_events():
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    # v3.9.25: 自动迁移 + 自动排序（同财务模块）
    migrated, count = _migrate_schedule_to_standard_format(data, "event")
    if count > 0:
        _save_events(migrated)
    return _sort_records(migrated)


def _save_events(events):
    _debug_log(f"[文件] 写入 events.json: 路径={EVENTS_FILE!r}, 条数={len(events)}")
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(_sort_records(events), f, ensure_ascii=False, indent=2)


def _load_todos():
    try:
        with open(TODOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    # v3.9.25: 自动迁移 + 自动排序（同财务模块）
    migrated, count = _migrate_schedule_to_standard_format(data, "todo")
    if count > 0:
        _save_todos(migrated)
    return _sort_records(migrated)


def _save_todos(todos):
    _debug_log(f"[文件] 写入 todos.json: 路径={TODOS_FILE!r}, 条数={len(todos)}")
    with open(TODOS_FILE, "w", encoding="utf-8") as f:
        json.dump(_sort_records(todos), f, ensure_ascii=False, indent=2)


def _get_next_id():
    """扫描现有 events 和 todos，返回下一个可用 ID（旧整数 ID，兼容保留）"""
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


# ===================== v3.9.25: 存储标准化 =====================

_STORAGE_VERSION = 2

_PERIOD_MAP = [
    ("00:00", "05:59", "凌晨"),
    ("06:00", "08:59", "早上"),
    ("09:00", "11:59", "上午"),
    ("12:00", "13:59", "中午"),
    ("14:00", "17:59", "下午"),
    ("18:00", "21:59", "晚上"),
    ("22:00", "23:59", "深夜"),
]


def _get_period(time_str):
    """将 HH:MM 时间映射到时段时间标签（与财务模块一致）。"""
    if not time_str:
        return ""
    try:
        h, m = map(int, str(time_str).split(":")[:2])
    except (ValueError, AttributeError):
        return ""
    minutes = h * 60 + m
    for start, end, label in _PERIOD_MAP:
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        if sh * 60 + sm <= minutes <= eh * 60 + em:
            return label
    return ""


def _normalize_date(date_str):
    """将各种日期格式统一为 YYYY-MM-DD。"""
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")
    s = str(date_str).strip()
    # 2026-07-25
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
    # 2026/07/25
    m = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})$', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 2026-07-25T12:30:00
    m = re.match(r'^(\d{4}-\d{2}-\d{2})T', s)
    if m:
        return m.group(1)
    # 07-25 / 7-25
    m = re.match(r'^(\d{1,2})[-/](\d{1,2})$', s)
    if m:
        return f"{datetime.now().year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return s


def _normalize_time(time_str):
    """确保时间格式为 HH:MM（空时间保持为空，用于全天事件/无到期待办）。"""
    if not time_str:
        return ""
    s = str(time_str).strip()
    # HH:MM
    if re.match(r'^\d{1,2}:\d{2}$', s):
        hh, mm = s.split(":")
        return f"{int(hh):02d}:{mm}"
    # HH:MM:SS
    if re.match(r'^\d{2}:\d{2}:\d{2}$', s):
        return s[:5]
    # "12:30:00" from datetime
    if "T" in s:
        s = s.split("T")[1]
        if re.match(r'^\d{1,2}:\d{2}', s):
            return s[:5]
    return ""


def _date_to_ordinal(d):
    """将 YYYY-MM-DD 转为整数序数，用于排序。"""
    try:
        parts = d.split("-")
        if len(parts) == 3:
            return int(parts[0]) * 10000 + int(parts[1]) * 100 + int(parts[2])
    except (ValueError, TypeError):
        pass
    return 0


def _sort_records(records):
    """按 date 降序（最新在前）+ time 升序（同日最早在前）排序。"""
    if not records:
        return records

    def _sort_key(r):
        d = r.get("date", "") or "0000-00-00"
        t = r.get("time", "") or "00:00"
        return (-_date_to_ordinal(d), t)

    return sorted(records, key=_sort_key)


def _generate_id(prefix, date_str="", time_str=""):
    """生成唯一 ID: schedule_{prefix}_{YYYYMMDD}_{HHMM}_{6chars}"""
    import random
    import string
    now = datetime.now()
    if date_str:
        try:
            d = datetime.strptime(_normalize_date(date_str), "%Y-%m-%d")
        except ValueError:
            d = now
    else:
        d = now
    if time_str:
        t_part = _normalize_time(time_str).replace(":", "") or now.strftime("%H%M")
    else:
        t_part = now.strftime("%H%M")
    rand = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"schedule_{prefix}_{d.strftime('%Y%m%d')}_{t_part}_{rand}"


def _generate_event_content(period, title):
    """生成事件摘要: {period}{title} → 下午开会（无时段则仅标题）"""
    if period:
        return f"{period}{title}"
    return title or "日程事件"


def _generate_todo_content(period, title):
    """生成待办摘要: {period}需要{title} → 晚上需要买牛奶（无时段则 需要{title}）"""
    if period:
        return f"{period}需要{title}"
    return f"需要{title}" if title else "待办事项"


def _is_standard_record(record, record_type):
    """判断记录是否已采用标准格式（新字符串 ID + datetime + content）。"""
    if not isinstance(record, dict):
        return False
    rid = str(record.get("id", ""))
    # v3.9.26: 以 schedule_ 前缀 ID 为迁移标记（不要求 datetime 非空，
    # 否则无日期待办会被误判为旧格式反复迁移）
    return (rid.startswith(f"schedule_{record_type}_")
            and record.get("content") is not None)


def _migrate_schedule_to_standard_format(records, record_type):
    """
    将旧格式记录迁移到标准化格式（v3.9.25）。
    补全缺失字段，不丢失任何现有数据。
    返回 (records, migrated_count)。
    """
    if not records:
        return records, 0
    migrated = []
    count = 0
    for r in records:
        if not isinstance(r, dict):
            migrated.append(r)
            continue
        if _is_standard_record(r, record_type):
            migrated.append(r)
            continue

        # ---- 兼容字段读取：事件用 date/time，待办用 due_date/due_time ----
        # v3.9.26: 无日期记录保留空 date（不默认今天），避免"无日期待办"被迁移成今天
        raw_date = (r.get("date") or r.get("due_date") or "").strip()
        date_str = _normalize_date(raw_date) if raw_date else ""
        time_str = _normalize_time(r.get("time") or r.get("due_time") or "")
        title = (r.get("title") or "").strip() or (
            "日程事件" if record_type == "event" else "待办事项")
        period = r.get("period") or _get_period(time_str)
        if record_type == "event":
            content = r.get("content") or _generate_event_content(period, title)
        else:
            content = r.get("content") or _generate_todo_content(period, title)
        dt_str = f"{date_str}T{time_str}:00" if (date_str and time_str) else ""

        old_id = r.get("id")
        if str(old_id).startswith("schedule_"):
            new_id = old_id
        else:
            new_id = _generate_id(record_type, date_str, time_str)

        new_record = {
            "id": new_id,
            "type": record_type,
            "date": date_str,
            "time": time_str,
            "datetime": r.get("datetime") or dt_str,
            "period": period,
            "title": title,
            "content": content,
            "detail": r.get("detail"),
            "status": "done" if r.get("completed") else (r.get("status") or "pending"),
            "created_at": r.get("created_at") or dt_str,
            "updated_at": r.get("updated_at"),
            "notified": r.get("notified", False),
        }
        if record_type == "event":
            new_record["repeat"] = r.get("repeat", "none")
            new_record["location"] = r.get("location")
        else:
            # 待办兼容字段：保留 due_date / due_time / completed
            new_record["due_date"] = r.get("due_date", date_str)
            new_record["due_time"] = r.get("due_time", time_str)
            new_record["completed"] = r.get("completed", False)

        # 保留旧记录中的任何未知扩展字段（不丢失数据）
        for k, v in r.items():
            if k not in new_record:
                new_record[k] = v

        migrated.append(new_record)
        count += 1

    return migrated, count


def _resolve_item_ref(items, ref):
    """
    将用户引用（字符串 ID 或 数字序号）解析为具体记录。

    - ref 为字符串且是纯数字 → 视为列表中的序号（1-based）
    - ref 为字符串 → 精确匹配新格式 ID，或兼容旧整数 ID
    返回记录 dict，未命中返回 None
    """
    if ref is None:
        return None
    if isinstance(ref, str) and ref.strip().isdigit():
        ref = int(ref.strip())
    if isinstance(ref, int):
        if 1 <= ref <= len(items):
            return items[ref - 1]
        return None
    ref_str = str(ref)
    for it in items:
        if str(it.get("id", "")) == ref_str:
            return it
    return None


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

    # 0. "一会儿"/"待会儿"/"过会儿" — 模糊时间，默认 5 分钟后（v3.9.26）
    if re.search(r"(一会儿|待会儿|过会儿|等会儿|稍后)", text):
        future = datetime.now() + timedelta(minutes=5)
        time_str = future.strftime("%H:%M")
        remaining = re.sub(r"(一会儿|待会儿|过会儿|等会儿|稍后)", "", text)
        return time_str, remaining

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
    m = re.search(r"(\d{1,2})[::](\d{2})\s*(p\.?m\.?|P\.?M\.?|am|AM)?", text)
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

_TIME_PARSE_PROMPT = """你是一个精确的时间解析助手.当前时间是 {current_time}.

请将用户的自然语言表达转换为具体的日期和时间.只返回格式 "YYYY-MM-DD HH:MM",不要任何解释、标点或额外文字.

解析规则(按优先级):
1. "X叫我" / "X喊我" / "X叫我一下" / "X到点提醒":在当前小时的第 X 分钟提醒.但如果当前分钟数 > X,则自动推到下一个小时.
   例:当前 14:35,用户说"26叫我" → 下一个 26 分是 15:26 → 返回当天 15:26
   例:当前 14:10,用户说"26叫我" → 本小时 26 分是 14:26 → 返回当天 14:26
2. "X分钟后":当前时间 + X 分钟.⚠️ X 始终是分钟数, 不是小时!
   例:"2分钟后" → 当前时间+2分钟(不是+2小时).
   例:"5分钟后" → 当前时间+5分钟.
   例:"两分钟后" → 当前时间+2分钟(不是+120分钟).
3. "半个小时" / "半小时后":当前时间 + 30 分钟.
4. "X个小时后" / "X小时后":当前时间 + X 小时.(仅当用户明确说"小?时后"时才按小时算)
   例:"2小时后" → 当前时间+2小时=120分钟.
   例:"一小时后" → 当前时间+1小时.
5. "明天X点" / "明天上午X点" / "明天下午X点":明天的对应时间.
6. "下午X点" / "上午X点" / "晚上X点":当天的对应时间段.
7. "X:XX" / "XX:XX":直接解析为当天时间.如果该时间已过,推到明天.
8. ⚠️ 纠正文本(如"不是2分钟是5分钟"/"改成10分钟"):
   提取纠正后的新时间,不要提取被否定的旧时间."不是X是Y"中的Y才是正确时间.
   例:"不是两分钟是五分钟" → 提取5分钟(不是2分钟).
9. 如果无法解析,返回 "FAIL"

用户表达:{user_text}
时间:"""


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
        result = result.strip('"\'`\n\r ..,,')
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
        print(f"⚠️ LLM 时间解析失败(将降级处理):{e}")

    return None, None


# ===================== 日程事件 CRUD =====================


def _add_event(title, date_str, time_str, repeat="none"):
    """创建日程事件（v3.9.25 存储标准化，11 字段标准格式）。"""
    date_str = _normalize_date(date_str)
    time_str = _normalize_time(time_str)
    period = _get_period(time_str)
    dt_str = f"{date_str}T{time_str}:00" if (date_str and time_str) else ""
    event = {
        "id": _generate_id("event", date_str, time_str),
        "type": "event",
        "date": date_str,
        "time": time_str,
        "datetime": dt_str,
        "period": period,
        "title": title,
        "content": _generate_event_content(period, title),
        "detail": None,
        "location": None,
        "status": "pending",
        "repeat": repeat,
        "notified": False,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_at": None,
    }
    events = _load_events()
    events.append(event)
    _save_events(events)

    repeat_msg = {"none": "", "daily": "(每天重复)", "weekly": "(每周重复)",
                  "monthly": "(每月重复)"}.get(repeat, "")

    # v3.9.29: 记录到统一记忆库
    _record_memory_event(
        title="添加日程",
        detail=f"添加日程「{title}」({date_str} {time_str}){repeat_msg}".strip(),
        tags=["日程", "事件"],
    )

    return f"✅ 已添加日程:{title}({date_str} {time_str}){repeat_msg}"


def _delete_event(event_id):
    events = _load_events()
    target = _resolve_item_ref(events, event_id)
    if target is None:
        return f"❌ 未找到 ID 为 {event_id} 的日程"
    title = target.get("title", "日程")
    events.remove(target)
    _save_events(events)
    return f"✅ 已删除日程:{title}"


def _list_events(date_str=None):
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    events = _load_events()
    today_events = [e for e in events if e.get("date") == date_str]
    if not today_events:
        return f"📅 {date_str} 没有日程安排"
    # 序号使用在完整排序列表中的全局位置，与 _delete_event(数字) 一致
    position = {id(e): i for i, e in enumerate(events, 1)}
    today_events.sort(key=lambda e: e.get("time", "00:00"))
    lines = [f"📅 {date_str} 的日程:"]
    for e in today_events:
        repeat_tag = {"none": "", "daily": " 🔄每天",
                      "weekly": " 🔄每周", "monthly": " 🔄每月"}.get(
            e.get("repeat", "none"), "")
        lines.append(f"  [{position[id(e)]}] {e['time']} {e['title']}{repeat_tag}")
    return "\n".join(lines)


# ===================== 待办事项 CRUD =====================


def _add_todo(title, due_date="", due_time=""):
    """创建待办（v3.9.25 存储标准化，11 字段标准格式）。"""
    due_date = _normalize_date(due_date) if due_date else ""
    due_time = _normalize_time(due_time)
    period = _get_period(due_time)
    dt_str = f"{due_date}T{due_time}:00" if (due_date and due_time) else ""
    todo = {
        "id": _generate_id("todo", due_date, due_time),
        "type": "todo",
        "date": due_date,
        "time": due_time,
        "datetime": dt_str,
        "period": period,
        "title": title,
        "content": _generate_todo_content(period, title),
        "detail": None,
        "status": "pending",
        "due_date": due_date,
        "due_time": due_time,
        "completed": False,
        "notified": False,
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_at": None,
    }
    _debug_log(f"[写入] _add_todo: title={title!r}, due_date={due_date!r}, due_time={due_time!r}")
    todos = _load_todos()
    todos.append(todo)
    _save_todos(todos)

    # v3.9.29: 记录到统一记忆库
    due_desc = ""
    if due_date and due_time:
        due_desc = f"(到期:{due_date} {due_time})"
    elif due_date:
        due_desc = f"(到期:{due_date})"
    elif due_time:
        due_desc = f"(到期时间:{due_time})"
    _record_memory_event(
        title="添加待办",
        detail=f"添加待办「{title}」{due_desc}".strip(),
        tags=["日程", "待办"],
    )

    parts = [f"✅ 已添加待办:{title}"]
    if due_date and due_time:
        parts.append(f"(到期:{due_date} {due_time})")
    elif due_date:
        parts.append(f"(到期:{due_date})")
    elif due_time:
        parts.append(f"(到期时间:{due_time})")
    return " ".join(parts)


def _delete_todo(todo_id):
    todos = _load_todos()
    # v3.9.28: 数字序号按完整展示顺序（date 降序 + time 升序，未完成优先）解析，
    # 与"显示所有待办"/delete_by_index 一致；字符串 ID 精确匹配
    if isinstance(todo_id, int) or (isinstance(todo_id, str) and todo_id.strip().isdigit()):
        display = _todos_display_order(list(todos), show_all=True)
        target = _resolve_item_ref(display, todo_id)
    else:
        target = _resolve_item_ref(todos, todo_id)
    if target is None:
        return f"❌ 未找到 ID 为 {todo_id} 的待办"
    title = target.get("title", "待办")
    todos.remove(target)
    _save_todos(todos)
    return f"✅ 已删除待办:{title}"


def _complete_todo(todo_id):
    todos = _load_todos()
    if isinstance(todo_id, int) or (isinstance(todo_id, str) and todo_id.strip().isdigit()):
        display = _todos_display_order(list(todos), show_all=True)
        target = _resolve_item_ref(display, todo_id)
    else:
        target = _resolve_item_ref(todos, todo_id)
    if target is None:
        return f"❌ 未找到 ID 为 {todo_id} 的待办"
    if target.get("completed"):
        return f"ℹ️ 待办「{target['title']}」已经完成了"
    target["completed"] = True
    target["status"] = "done"
    target["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    _save_todos(todos)
    return f"✅ 已完成待办:{target['title']}"


def _todos_display_order(todos, show_all=False):
    """返回待办展示/序号删除共用的排序列表：未完成优先 → date 降序 → time 升序。

    v3.9.28: 与 query_schedule / _sort_records 保持一致（date 降序 + time 升序），
    保证"删除待办N"的序号与用户看到的列表一致。
    """
    if not show_all:
        todos = [t for t in todos if not t.get("completed", False)]

    def _key(t):
        d = (t.get("date") or t.get("due_date") or "").strip()
        tm = (t.get("time") or t.get("due_time") or "").strip() or "99:99"
        return (t.get("completed", False), -_date_to_ordinal(d), tm)

    return sorted(todos, key=_key)


def _list_todos(show_all=False, date_str=None):
    """列出待办。date_str 非空时仅显示该日期的待办（如"显示今天的待办"）。"""
    todos_sorted = _todos_display_order(_load_todos(), show_all=show_all)
    if date_str:
        todos_sorted = [t for t in todos_sorted
                        if (t.get("due_date") or t.get("date")) == date_str]

    if not todos_sorted:
        if date_str:
            return f"📋 {date_str} 没有待办事项 🎉"
        return "📋 没有待办事项 🎉"

    lines = [f"📋 {date_str} 的待办事项:" if date_str else "📋 待办事项:"]
    for i, t in enumerate(todos_sorted, 1):
        status = "✅" if t.get("completed") else "⬜"
        due = ""
        if t.get("due_date") and t.get("due_time"):
            due = f" ⏰{t['due_date']} {t['due_time']}"
        elif t.get("due_date"):
            due = f" ⏰{t['due_date']}"
        elif t.get("due_time"):
            due = f" ⏰{t['due_time']}"
        lines.append(f"  {status} [{i}] {t['title']}{due}")
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
        print(f"⚠️ 提醒语音播报失败:{e}")


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

_SMART_REMINDER_PROMPT = """你是一个温暖、体贴的私人提醒助手.根据用户设置的提醒内容,生成一句简短、温暖、个性化的提醒播报.

要求:
- 不超过 20 个字
- 语气温暖自然,像朋友在提醒
- 不要出现"提醒"二字
- 只输出播报句子本身,不要任何解释

原始提醒内容:{content}
播报句子:"""


def _generate_smart_reminder(raw_msg, timeout=1.5):
    """
    调用本地 Ollama 模型生成个性化提醒文案。
    超时或失败则降级为通用文案。

    参数:
        raw_msg: 原始提醒消息，如 "⏰ 日程提醒：泡咖啡"
        timeout: HTTP 请求超时秒数（v3.9.8 降至 1.5s，避免阻塞提醒播报）
    返回:
        人性化播报字符串，不超过 20 字
    """
    # 检查开关
    if not _load_use_smart_reminder():
        return raw_msg

    # 提取提醒内容（去掉前缀标签）
    content = re.sub(r'^[⏰📅📋]\s*(日程|待办|任务)?\s*提醒[::]?\s*', '', raw_msg).strip()
    if not content:
        return raw_msg  # v3.9.8: 不回退到通用文案，改用原始消息

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
        print(f"⚠️ 智能提醒生成失败(降级为原始提醒):{e}")

    # v3.9.8: 降级返回带标题的友好文案，而非生硬的"您的提醒时间到了"
    return f"提醒：{content}"


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
            triggered.append(f"⏰ 日程提醒:{event['title']}")
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
            triggered.append(f"⏰ 待办提醒:{todo['title']}")
            todo["notified"] = True
            todos_modified = True
    if todos_modified:
        _save_todos(todos)

    # ---- 播报 ----
    if triggered:
        print(f"🔔 提醒触发 ({now.strftime('%H:%M')}):共 {len(triggered)} 条")
        with _lock:
            for msg in triggered:
                print(msg)
                # v3.9.8: 提取标题生成基本提醒文案，避免 LLM 调用阻塞播报
                # msg 格式如 "⏰ 日程提醒:泡咖啡" 或 "⏰ 待办提醒:喝水"
                title = re.sub(r'^[⏰📅📋]\s*(日程|待办|任务)?\s*提醒[::]?\s*', '', msg).strip()
                basic_msg = f"提醒：{title}" if title else msg
                # 如果启用了智能提醒且 LLM 可用，异步生成智能文案覆盖
                if _load_use_smart_reminder():
                    try:
                        smart_msg = _generate_smart_reminder(msg)
                        if smart_msg and smart_msg != basic_msg:
                            basic_msg = smart_msg
                    except Exception:
                        pass  # 智能生成失败，使用基本文案
                # 在独立线程中调用 speak()，避免阻塞提醒检查循环
                try:
                    threading.Thread(
                        target=_safe_speak, args=(basic_msg,),
                        daemon=True, name="reminder-speak"
                    ).start()
                except Exception as e:
                    print(f"⚠️ 提醒语音播报线程启动失败:{e}")
    else:
        _debug_log(f"[提醒扫描] 暂无到期提醒")


def _reminder_loop():
    """后台守护线程主循环：每 30 秒检查一次"""
    _debug_log("[提醒线程] 后台提醒循环已启动,每30秒扫描一次")
    while True:
        try:
            time.sleep(30)
            _check_reminders()
        except Exception as e:
            import traceback
            print(f"⚠️ 提醒检查出错:{e}")
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


# v3.9.25: 模块加载时执行一次存储标准化迁移（_load_* 内部自动迁移+排序+回写，幂等）
_load_events()
_load_todos()


# ===================== 自然语言解析与入口 =====================

_HELP_TEXT = """
📋 日程模块使用说明:

  📅 日程管理:
    "添加会议明天下午3点"
    "添加每天9点起床"
    "删除日程1"
    "显示今天的日程"

  📋 待办管理:
    "添加任务买牛奶"
    "完成任务1"
    "删除任务1"
    "显示待办"
    "删除泡咖啡那个提醒"      ← 按内容模糊删除
    "把两个小时的定时删掉"     ← 按时间描述删除

  ⏰ 提醒:
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
    raw_title = re.sub(r'\b\d{1,2}[::]\d{2}\b', '', raw_title)
    raw_title = raw_title.strip()
    # 如果清理后为空，回退到原始输入中的关键词
    return raw_title


def _has_time_pattern(text):
    """检测文本中是否包含时间模式"""
    # 匹配各种时间格式（与 _parse_time 的正则保持一致）
    patterns = [
        r"(上午|下午|晚上|傍晚|凌晨|早上|中午)\s*\d+\s*点",
        r"\d{1,2}[::]\d{2}",
        r"\d+\s*点\s*(半|\d+\s*分)",
        r"\d+\s*分[钟]?\s*(后|之后|以后)",   # 匹配 _parse_time 的 "X分钟后" 模式
        r"\d+\s*p\.?m",
        # 自然表达扩展（v2.7.2）
        r"\d{1,2}\s*(叫我|喊我|叫我一下|到点叫我|到点提醒|到时提醒)",  # "26叫我" 风格
        r"半个?\s*小?时\s*(后|之后|以后)?",                          # "半个小时" / "半小时后"
        r"\d+\s*个?\s*小?时\s*(后|之后|以后)",                        # "一小时后" / "2个小时后"
        r"(明天|后天|今天|明日)\s*\d*\s*点",                          # "明天8点" 无前缀
        r"(叫我|喊我|叫我一下|叫醒我)\s*$",                           # 纯提醒关键词结尾
        r"(一会儿|待会儿|过会儿|等会儿|稍后)",                        # 模糊时间（v3.9.26）
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _has_date_word(text):
    """检测文本中是否包含日期表达（今天/明天/后天/周X/月X日等）。

    v3.9.28: 用于识别"添加后天出门"这类只有日期没有时间的日程添加请求。
    """
    if re.search(r"(今天|今日|明天|明日|后天|大后天|昨天|昨日|前天)", text):
        return True
    if re.search(r"[周星期][一二三四五六日天]", text):          # 周一/星期二/周五
        return True
    if re.search(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]", text):    # 6月15日/6月15号
        return True
    if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text):        # 2026-08-09
        return True
    if re.search(r"(本月|下月|上月|这个月|下个月|下礼拜|下周|这个礼拜)", text):
        return True
    return False


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

    # v3.9.28: 自然日程记录表达 —— "后天有日程安排给我记一下" / "有个日程" / "帮我记一下后天开会"
    # 这类句子没有"添加/创建"等显式动作词，但明显是记录日程的请求
    NATURAL_ADD_RE = re.compile(
        r"(有日程安排|有个日程|有日程|日程安排给我记|"
        r"帮我记(?:一下)?|帮我记一下|"
        r"记(?:一下|上|着)?.*日程|安排一下.*日程)"
    )
    # v3.9.28: 排除财务记账类表达（"帮我记一下花了30元"应走财务，不是日程）
    _FINANCE_NOISE_RE = re.compile(r"(花了|买了|消费|花了钱|记账|支出|元|块|多少钱)")
    if NATURAL_ADD_RE.search(text) and not _FINANCE_NOISE_RE.search(text):
        _debug_log(f"[解析] 检测到自然日程记录表达: {text!r}")
        date_str, after_date = _parse_date(text)
        time_str, after_time = _parse_time(after_date)
        # 提取标题：去掉日期、时间以及"有日程/记一下/帮我"等噪声
        title = re.sub(
            r"(有日程安排|有个日程|有日程|日程安排|给我|帮我|记一下|记上|记着|记|"
            r"安排一下|安排|日程|事件|后天|明天|今天|大后天|昨日|今日|明日)",
            "", after_time).strip()
        title = _clean_title(title)
        if not title:
            title = "日程安排"
        if not time_str and not _has_date_word(text):
            time_str = ""
        return _add_event(title, date_str, time_str)

    # v3.10.1: "有+日程词"自然表达 —— "明天有个重要的会议" / "后天有个任务" / "下周一有个面试"
    # 没有"添加/创建"等显式动作词，但"有+日程/会议/任务"明显是记录日程/待办的请求
    # 中间允许形容词等修饰（"有个重要的会议"），最多 10 字
    HAS_EVENT_WORD_RE = re.compile(r"有\s*(?:一个|个)?\s*.{0,10}?\s*(会议|事件|日程|安排|约会|聚会|面试|上课|活动|开会)")
    HAS_TODO_WORD_RE = re.compile(r"有\s*(?:一个|个)?\s*.{0,10}?\s*(任务|待办|事项|todo)", re.IGNORECASE)
    # 排除：查询/疑问（"有什么会议"）、财务（"会议支出花了X元"）、提醒（"记得有个会议"）
    _HAS_QUERY_NOISE_RE = re.compile(r"(什么|哪些|有没有|几个|吗|呢|多少钱)")
    _HAS_REMIND_NOISE_RE = re.compile(r"(提醒|记得|叫我|叫醒|喊我|通知|闹钟|到时|到点)")
    if (HAS_EVENT_WORD_RE.search(text) or HAS_TODO_WORD_RE.search(text)) \
            and not _HAS_QUERY_NOISE_RE.search(text) \
            and not _FINANCE_NOISE_RE.search(text) \
            and not _HAS_REMIND_NOISE_RE.search(text):
        _debug_log(f"[解析] 检测到'有+日程词'自然表达: {text!r}")
        # 无日期也无时间 → 无法确定时间，澄清
        if not _has_date_word(text) and not _has_time_pattern(text):
            return "请问是什么类型的日程？"
        date_str, after_date = _parse_date(text)
        time_str, after_time = _parse_time(after_date)
        # 提取标题：去掉日期/时间后的剩余文本，再清理"有/个/下/上/我"等噪声
        title = re.sub(
            r"(有|有一个|有个|一个|个|下|上|我|我们|需要)",
            "", after_time).strip()
        title = _clean_title(title)
        if not title:
            m_word = HAS_TODO_WORD_RE.search(text) or HAS_EVENT_WORD_RE.search(text)
            title = m_word.group(1) if m_word else "日程安排"
        if HAS_TODO_WORD_RE.search(text):
            return _add_todo(title, date_str, time_str)
        return _add_event(title, date_str, time_str)

    # 添加事件：添加/新增/安排 + 标题 + 时间
    m = re.match(r"(添加|新增|创建|加一个|增加|安排)\s*(.*)", text)
    if m:
        raw = m.group(2).strip()
        # 检测是否为事件：有关键词、包含时间、或包含日期表达（v3.9.28 支持"添加后天出门"）
        EVENT_KEYWORDS = ["会议", "事件", "日程", "安排", "约会", "聚会",
                          "开会", "会", "上课", "课", "面试", "活动",
                          "每天", "每周", "每月"]
        TODO_KEYWORDS = ["任务", "待办", "事项", "todo"]

        is_event = any(kw in text for kw in EVENT_KEYWORDS)
        is_todo = any(kw in text for kw in TODO_KEYWORDS)

        if is_todo:
            # 在下文 todo 分支处理，这里跳过
            pass
        elif is_event or _has_time_pattern(raw) or _has_date_word(raw):
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

            # v3.9.28: 只有日期（如"添加后天出门"）时允许无时间，作为全天事件
            if not time_str and not _has_date_word(raw):
                return "⚠️ 请提供时间,例如「添加会议明天下午3点」"

            return _add_event(title, date_str, time_str, repeat)
        elif not is_todo:
            # 非 todo 也非 event，给出提示
            return "⚠️ 请明确是日程事件(如「添加会议明天3点」)还是待办任务(如「添加任务买牛奶」)"

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
            _debug_log(f"[解析] 正则解析失败,尝试 LLM 时间解析...")
            llm_date, llm_time = _parse_time_with_llm(text)
            if llm_time:
                time_str = llm_time
                date_str = llm_date
                after_time = text  # LLM 已消费全文,剩余文本用原文提取标题
                _debug_log(f"[解析] LLM 时间解析成功: date={date_str}, time={time_str}")
            else:
                # 第 3 步：降级兜底 — 当前时间 + 1 分钟，保证提醒不丢失
                fallback = datetime.now() + timedelta(minutes=1)
                time_str = fallback.strftime("%H:%M")
                date_str = fallback.strftime("%Y-%m-%d")
                after_time = text
                print(f"⚠️ 时间解析失败,使用 1 分钟后({date_str} {time_str})作为保底")

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
            return "⚠️ 请提供提醒时间,例如「提醒我5分钟后喝水」或「5分钟后叫我」"

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
            return "⚠️ 请描述任务内容,例如「添加任务买牛奶」"
        return _add_todo(title, due_date, due_time)

    # 删除待办（按序号）
    m = re.search(r"(删除|移除)\s*(任务|待办)\s*(\d+)", text)
    if m:
        return _delete_todo(int(m.group(3)))

    # 删除提醒/待办（按内容描述，v3.2.0 新增）
    # 匹配"把两个小时的那个定时删掉"、"删除泡咖啡"、"取消明天的会议" 等
    DELETE_KW_RE = r"删除|移除|取消|去掉|删掉"
    if re.search(DELETE_KW_RE, text):
        # 排除已由上述数字 ID 模式处理的情况
        has_numeric_id = bool(re.search(r"(?:日程|事件|会议|任务|待办)\s*\d+", text))
        if not has_numeric_id:
            _debug_log(f"[解析] 检测到模糊删除意图: {text!r}")
            return delete_reminder_by_query(text)

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
        # 日期限定：今天/明天/后天 → 仅显示该日期的待办（v3.9.25）
        date_filter = None
        if re.search(r"(今天|今日|明天|明日|后天)", text):
            date_filter, _ = _parse_date(text)
        return _list_todos(show_all=show_all, date_str=date_filter)

    # ==================== 帮助 ====================
    if any(kw in text for kw in ["帮助", "help", "说明", "功能"]):
        return _HELP_TEXT

    return _HELP_TEXT


# ===================== 最近提醒操作（修正机制用） =====================


def query_schedule(user_input, params=None):
    """
    查询日程/待办（v3.9.4 新增）。
    支持来自 3b router 的结构化参数和自然语言兜底。

    参数:
        user_input: str — 用户原始输入
        params: dict | None — 3b router 的结构化查询参数（time / scope）

    返回:
        dict — {"status": "success", "type": "query", "data": {...}, "summary": "..."}
        或 str — 兜底字符串（兼容旧版 process_command 返回格式）
    """
    time_filter = None
    scope = None
    if params and isinstance(params, dict):
        time_filter = params.get("time")
        scope = params.get("scope")

    # v3.9.27: 兜底——若路由层未传 time，从用户输入中推断时间范围，
    # 避免"查看本周的日程"等因 LLM 未提取 time 而退化成显示全部（含历史旧数据）
    if not time_filter and user_input:
        if re.search(r"(本周|这周|这个礼拜)", user_input):
            time_filter = "this_week"
        elif re.search(r"(今天|今日)", user_input):
            time_filter = "today"
        elif re.search(r"(昨天|昨日)", user_input):
            time_filter = "yesterday"
        elif re.search(r"(上周|上礼拜)", user_input):
            time_filter = "last_week"
        elif re.search(r"(本月|这个月|这个月里)", user_input):
            time_filter = "this_month"

    # ---- 推断日期过滤 ----
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    date_start = None
    date_end = None
    label = ""

    if time_filter == "today":
        date_start = today
        date_end = today
        label = "今天"
    elif time_filter in ("yesterday", "昨天"):
        date_start = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        date_end = date_start
        label = "昨天"
    elif time_filter in ("this_week", "本周"):
        monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        sunday = (now + timedelta(days=(6 - now.weekday()))).strftime("%Y-%m-%d")
        date_start = monday
        date_end = sunday
        label = "本周"
    elif time_filter in ("last_week", "上周"):
        days_since_monday = now.weekday()
        last_monday = (now - timedelta(days=days_since_monday + 7))
        last_sunday = last_monday + timedelta(days=6)
        date_start = last_monday.strftime("%Y-%m-%d")
        date_end = last_sunday.strftime("%Y-%m-%d")
        label = "上周"
    elif time_filter in ("this_month", "本月"):
        date_start = now.strftime("%Y-%m") + "-01"
        # 月末: 下月 1 号 - 1 天
        if now.month == 12:
            next_month = datetime(now.year + 1, 1, 1)
        else:
            next_month = datetime(now.year, now.month + 1, 1)
        date_end = (next_month - timedelta(days=1)).strftime("%Y-%m-%d")
        label = "本月"

    # scope="all" 或未指定时间 → 不过滤日期
    if scope == "all" and not time_filter:
        date_start = None

    # ---- 查询待办 ----
    todos = _load_todos()
    active_todos = [t for t in todos if not t.get("completed", False)]

    if date_start:
        # v3.9.26: 指定了时间筛选（today/this_week/this_month 等）时，只显示该时间范围内的待办，
        # 无到期日的待办（date 为空）不纳入——否则筛选会退化成"显示全部"。
        filtered_todos = []
        for t in active_todos:
            due = (t.get("date") or t.get("due_date") or "").strip()
            if not due:
                continue  # 有时间筛选时，无日期待办不属于任何时间范围
            if date_end and date_start <= due <= date_end:
                filtered_todos.append(t)
            elif not date_end and due >= date_start:
                filtered_todos.append(t)
        active_todos = filtered_todos

    # ---- 查询事件 ----
    events = _load_events()
    if date_start:
        filtered_events = []
        for e in events:
            edate = e.get("date", "") or ""
            if date_end:
                if date_start <= edate <= date_end:
                    filtered_events.append(e)
            else:
                if edate >= date_start:
                    filtered_events.append(e)
        events = filtered_events

    # ---- 构建响应 ----
    lines = []
    time_prefix = f"{label}" if label else ""

    # v3.9.25: 统一排序规则 date 降序 + time 升序
    if events:
        events = _sort_records(events)
        header = f"📅 {time_prefix}的日程:" if time_prefix else "📅 日程安排:"
        lines.append(header)
        for i, e in enumerate(events, 1):
            repeat_tag = {"none": "", "daily": " 🔄每天",
                          "weekly": " 🔄每周", "monthly": " 🔄每月"}.get(
                e.get("repeat", "none"), "")
            lines.append(f"  [{i}] {e.get('date','?')} {e.get('time','')} {e['title']}{repeat_tag}")

    if active_todos:
        active_todos = _sort_records(active_todos)
        header = f"📋 {time_prefix}的待办:" if time_prefix else "📋 待办事项:"
        lines.append(header)
        for i, t in enumerate(active_todos, 1):
            due = ""
            if t.get("due_date") and t.get("due_time"):
                due = f" ⏰{t['due_date']} {t['due_time']}"
            elif t.get("due_date"):
                due = f" ⏰{t['due_date']}"
            elif t.get("due_time"):
                due = f" ⏰{t['due_time']}"
            status = "✅" if t.get("completed") else "⬜"
            lines.append(f"  {status} [{i}] {t['title']}{due}")

    if not lines:
        if time_prefix:
            return f"📅 {time_prefix}没有日程安排，也没有待办事项 🎉"
        return "📋 没有日程安排和待办事项 🎉"

    summary = "\n".join(lines)
    return {
        "status": "success",
        "type": "query",
        "data": {"events": len(events), "todos": len(active_todos)},
        "summary": summary,
    }


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


# ===================== 按内容删除提醒（v3.2.0 新增）=====================


def delete_reminder_by_query(query):
    """
    按自然语言描述删除提醒/待办。支持模糊匹配标题和时间描述。

    参数:
        query: str — 用户对提醒的描述（如"两个小时的那个定时"、"泡咖啡"、"明天的会议"）

    返回:
        str — 操作结果消息
    """
    todos = _load_todos()
    if not todos:
        return "📋 当前没有待办提醒可以删除"

    # 只处理未完成的待办
    active = [t for t in todos if not t.get("completed", False)]
    if not active:
        return "📋 当前没有未完成的待办提醒"

    # 清洗查询：去掉噪声词
    noise = r'删除|取消|去掉|删掉|移除|那个|这个|的|定时|提醒|任务|待办|把|给|我'
    cleaned = re.sub(noise, '', query).strip()

    # 尝试从清洗后的查询中提取时间描述（如"两个小时"、"5分钟"）
    time_minutes = None
    time_m = re.search(r'(\d+)\s*个?\s*小?时', cleaned)
    if time_m:
        time_minutes = int(time_m.group(1)) * 60  # 小时 → 分钟
        cleaned = re.sub(r'\d+\s*个?\s*小?时', '', cleaned).strip()
    else:
        time_m = re.search(r'(\d+)\s*分[钟]?', cleaned)
        if time_m:
            time_minutes = int(time_m.group(1))
            cleaned = re.sub(r'\d+\s*分[钟]?', '', cleaned).strip()

    _debug_log(f"[模糊删除] query={query!r}, cleaned={cleaned!r}, time_minutes={time_minutes!r}")

    # 为每个活跃待办打分
    now = datetime.now()
    scored = []
    for t in active:
        score = 0
        title = t.get("title", "")
        due_time = t.get("due_time", "")
        due_date = t.get("due_date", "")

        # 内容匹配
        if cleaned and cleaned in title:
            score += 10
        elif cleaned and len(cleaned) >= 2:
            # 逐字匹配
            matched = sum(1 for c in cleaned if c in title)
            if matched >= max(2, len(cleaned) * 0.5):
                score += matched * 2

        # 时间匹配：比较待办到期时间与查询中提取的时间偏移
        if time_minutes is not None and due_time and due_date:
            try:
                due_dt = datetime.strptime(f"{due_date} {due_time}", "%Y-%m-%d %H:%M")
                diff = int((due_dt - now).total_seconds() / 60)
                if abs(diff - time_minutes) <= 5:
                    score += 8
                elif abs(diff - time_minutes) <= 15:
                    score += 4
            except (ValueError, TypeError):
                pass

        if score > 0:
            scored.append((t, score))

    if not scored:
        return f"🔍 没有找到匹配「{query}」的提醒。试试「显示待办」查看所有提醒。"

    # 按得分降序
    scored.sort(key=lambda x: x[1], reverse=True)

    if len(scored) == 1 or scored[0][1] >= scored[1][1] + 4:
        # 唯一匹配或得分明显领先，直接删除
        t, s = scored[0]
        _debug_log(f"[模糊删除] 唯一匹配: [{t['id']}] {t['title']} (score={s})")
        return _delete_todo(t["id"])
    else:
        # 多个候选，列出让用户选择
        lines = [f"🔍 找到 {len(scored)} 个匹配的提醒，请告诉我序号:"]
        for i, (t, _) in enumerate(scored[:5], 1):
            due = ""
            if t.get("due_date") and t.get("due_time"):
                due = f" ⏰{t['due_date']} {t['due_time']}"
            elif t.get("due_time"):
                due = f" ⏰{t['due_time']}"
            lines.append(f"  {i}. {t['title']}{due}")
        return "\n".join(lines)


def delete_by_scope(scope, keyword=None):
    """
    按指定范围批量删除提醒和日程（v3.7.4 新增）。

    参数:
        scope: str — "all" | "last_week" | "today" | "latest" | "keyword"
        keyword: str — 当 scope="keyword" 时,用作内容匹配词；未提供则回退到原始 query

    返回:
        str — 操作结果消息
    """
    todos = _load_todos()
    events = _load_events()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    if scope == "all":
        # 删除所有未完成待办和所有日程事件
        active_todos = [t for t in todos if not t.get("completed", False)]
        count_t = len(active_todos)
        count_e = len(events)
        # 保留已完成的待办
        completed = [t for t in todos if t.get("completed", False)]
        _save_todos(completed)
        _save_events([])
        total = count_t + count_e
        if total == 0:
            return "📋 没有需要删除的数据"
        parts = []
        if count_t > 0:
            parts.append(f"{count_t} 条待办")
        if count_e > 0:
            parts.append(f"{count_e} 条日程")
        return f"✅ 已清空全部数据: {'、'.join(parts)}"

    elif scope == "last_week":
        # 删除最近 7 天内创建的未完成待办和日程
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        old_t = [t for t in todos if not t.get("completed", False)
                 and (t.get("created_at", "") >= cutoff)]
        old_e = [e for e in events if e.get("created_at", "") >= cutoff]
        count_t = len(old_t)
        count_e = len(old_e)
        # 保留不在范围内的
        kept_t = [t for t in todos if t not in old_t]
        kept_e = [e for e in events if e not in old_e]
        _save_todos(kept_t)
        _save_events(kept_e)
        total = count_t + count_e
        if total == 0:
            return "📋 最近一周没有需要删除的数据"
        parts = []
        if count_t > 0:
            parts.append(f"{count_t} 条待办")
        if count_e > 0:
            parts.append(f"{count_e} 条日程")
        return f"✅ 已删除最近一周数据: {'、'.join(parts)}"

    elif scope == "today":
        # 删除今天创建的未完成待办和日程
        cutoff_start = today_str + "T00:00:00"
        cutoff_end = today_str + "T23:59:59"
        old_t = [t for t in todos if not t.get("completed", False)
                 and cutoff_start <= (t.get("created_at", "") or "") <= cutoff_end]
        old_e = [e for e in events if cutoff_start <= (e.get("created_at", "") or "") <= cutoff_end]
        count_t = len(old_t)
        count_e = len(old_e)
        kept_t = [t for t in todos if t not in old_t]
        kept_e = [e for e in events if e not in old_e]
        _save_todos(kept_t)
        _save_events(kept_e)
        total = count_t + count_e
        if total == 0:
            return "📋 今天没有需要删除的数据"
        parts = []
        if count_t > 0:
            parts.append(f"{count_t} 条待办")
        if count_e > 0:
            parts.append(f"{count_e} 条日程")
        return f"✅ 已删除今天数据: {'、'.join(parts)}"

    elif scope == "latest":
        # 删除最新一条未完成待办（优先）或日程
        active_todos = [t for t in todos if not t.get("completed", False)]
        if active_todos:
            latest = max(active_todos, key=lambda x: x.get("created_at", ""))
            title = latest.get("title", "")
            todos.remove(latest)
            _save_todos(todos)
            return f"✅ 已删除最新待办: {title}"
        elif events:
            latest = max(events, key=lambda x: x.get("created_at", ""))
            title = latest.get("title", "")
            events.remove(latest)
            _save_events(events)
            return f"✅ 已删除最新日程: {title}"
        else:
            return "📋 没有可删除的数据"

    elif scope == "keyword":
        # 按内容关键词删除: 用 keyword 作为查询文本，复用模糊删除逻辑
        if keyword:
            return delete_reminder_by_query(keyword)
        else:
            return "⚠️ 请提供要删除的内容关键词"

    else:
        return f"⚠️ 未知的删除范围: {scope}"


def delete_by_index(index, record_type="todo"):
    """
    按序号删除待办/日程（v3.9.26 新增）。

    供路由层在"删除待办1"/"删除任务2"等场景使用：
    只删除指定序号的那一条，绝不批量删除。

    参数:
        index: int | str — 序号（1 起）或字符串 ID
        record_type: str — "todo" | "event"

    返回:
        str — 操作结果消息
    """
    try:
        index_int = int(index) if not isinstance(index, int) else index
    except (ValueError, TypeError):
        return f"⚠️ 无效的序号: {index}"

    if record_type == "event":
        events = _load_events()
        target = _resolve_item_ref(events, index_int)
        if target is None:
            return f"❌ 未找到序号为 {index} 的日程"
        title = target.get("title", "日程")
        events.remove(target)
        _save_events(events)
        return f"✅ 已删除日程: {title}"
    else:
        todos = _load_todos()
        # v3.9.28: 序号按完整展示顺序（date 降序 + time 升序，未完成优先）解析，
        # 与"显示所有待办"/query_schedule 完全一致，确保"删除待办N"删的是用户看到的那条
        display = _todos_display_order(list(todos), show_all=True)
        target = _resolve_item_ref(display, index_int)
        if target is None:
            return f"❌ 未找到序号为 {index} 的待办"
        title = target.get("title", "待办")
        todos.remove(target)
        _save_todos(todos)
        return f"✅ 已删除待办: {title}"


def modify_last_reminder(new_params):
    """
    修改最近创建的提醒：删除旧的，从纠正文本中重新解析时间并以当前时间为基准创建新提醒。

    核心设计（v3.1.3 重写）：
    - 不再盲目信任路由层解析的 time_offset / absolute_time
    - 从 raw_text 重新解析时间，始终以 datetime.now() 为基准
    - 操作是"替换"而非"追加"：先删旧的后建新的
    - 无法提取时间时明确提示用户重新输入

    参数:
        new_params: dict，与 add_reminder_from_params 格式相同

    返回:
        str  操作结果消息
    """
    now = datetime.now()

    # ---- 第 1 步：删除最近一条未完成待办 ----
    old = delete_last_reminder()
    old_title = old[0] if old else ""
    old_date = old[1] if old else ""
    old_time = old[2] if old else ""

    if old:
        old_desc = f"「{old_title}」({old_date} {old_time})" if old_date and old_time else f"「{old_title}」"
        _debug_log(f"[修正] 已删除旧提醒: {old_desc}")
    else:
        _debug_log("[修正] 无可删除的旧提醒,按新提醒创建")

    # ---- 第 2 步：从纠正文本中重新解析时间（以当前时间为基准） ----
    raw_text = (new_params.get("raw_text") or "").strip()

    time_str = ""
    date_str = ""

    if raw_text:
        # 2a: LLM 优先（更擅长理解"不是X是Y"的语义，知道后一个数字才是正确值）
        llm_date, llm_time = _parse_time_with_llm(raw_text, current_time=now)
        if llm_time:
            time_str = llm_time
            date_str = llm_date or now.strftime("%Y-%m-%d")
            _debug_log(f"[修正] LLM 时间重解析成功: {date_str} {time_str}")

        # 2b: LLM 失败，尝试正则
        if not time_str:
            normalized = _normalize_chinese_numbers(raw_text)
            time_str, after_time = _parse_time(normalized)
            if time_str:
                date_str, _ = _parse_date(normalized)
                # 验证：正则可能在"不是2分钟是5分钟后"中误匹配到"2分钟"
                # 额外检查确保提取的时间在未来（排除已过期的数字）
                try:
                    parsed_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    if parsed_dt <= now:
                        _debug_log(f"[修正] 正则解析结果已过期({date_str} {time_str}),尝试重新匹配")
                        # 尝试只匹配"是X分钟"后面的数字（修正意图的标志）
                        alt_m = re.search(r'是\s*(\d+)\s*分[钟]?\s*(后|之后|以后)', normalized)
                        if alt_m:
                            mins = int(alt_m.group(1))
                            future = now + timedelta(minutes=max(1, mins))
                            time_str = future.strftime("%H:%M")
                            date_str = future.strftime("%Y-%m-%d")
                            _debug_log(f"[修正] 从'是X'模式中提取: +{mins}分钟 → {date_str} {time_str}")
                        else:
                            time_str = ""
                    else:
                        _debug_log(f"[修正] 正则时间重解析成功: {date_str} {time_str}")
                except ValueError:
                    _debug_log(f"[修正] 正则解析结果验证失败,丢弃")
                    time_str = ""

    # 2c: 上述都失败，尝试路由层参数的 time_offset / absolute_time（始终以 now 为基准）
    if not time_str:
        time_offset = new_params.get("time_offset")
        if time_offset is not None:
            try:
                time_offset = int(time_offset)
                future = now + timedelta(minutes=max(1, time_offset))
                time_str = future.strftime("%H:%M")
                date_str = future.strftime("%Y-%m-%d")
                _debug_log(f"[修正] 使用路由层 time_offset={time_offset}(now基准): {date_str} {time_str}")
            except (ValueError, TypeError):
                pass

    if not time_str:
        absolute_time = new_params.get("absolute_time")
        if absolute_time and isinstance(absolute_time, str):
            time_str = absolute_time.strip()
            date_str = now.strftime("%Y-%m-%d")
            try:
                target_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                if target_dt <= now:
                    target_dt += timedelta(days=1)
                    date_str = target_dt.strftime("%Y-%m-%d")
            except ValueError:
                time_str = ""
            if time_str:
                _debug_log(f"[修正] 使用路由层 absolute_time(now基准): {date_str} {time_str}")

    # ---- 第 3 步：无法提取时间 → 提示用户 ----
    if not time_str:
        _debug_log(f"[修正] 无法从 '{raw_text}' 中提取有效时间")
        return "⚠️ 请重新输入正确的时间(如「5分钟后」或「下午3点」)"

    # ---- 第 4 步：提取内容 ----
    content = (new_params.get("content") or "").strip()
    if not content or content == "提醒":
        if raw_text:
            # 去掉纠正模板: "不是X是Y" → 提取Y部分
            cleaned = re.sub(r'不是.*?是\s*', '', raw_text)
            cleaned = re.sub(r'说错了[，,]*\s*应该是\s*', '', cleaned)
            cleaned = re.sub(r'改成|换个|应该是|不对[,，]*', '', cleaned)
            cleaned = re.sub(
                r'(提醒|记得|叫我|叫醒|叫醒我|喊我|通知|闹钟|叫我一下|到时提醒|到点提醒|到点叫我)\s*(我)?\s*',
                '', cleaned
            )
            content = _clean_title(cleaned)
        if not content:
            content = old_title if old_title else "提醒事项"

    # ---- 第 5 步：创建全新提醒（替换）- ---
    result = _add_todo(content, date_str, time_str)

    if old:
        _debug_log(f"[修正] 已完成替换: {old_desc} → {content}({date_str} {time_str})")
        return f"✅ 已修改提醒: {old_desc} → {result}"
    else:
        return result


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

    # v3.9.27: 模糊时间词防御——若原文含"一会儿/待会儿/过会儿/等会儿/稍后"，
    # 即使 LLM 猜了 time_offset（常见误判为 1），也统一修正为 5 分钟
    if raw_text and re.search(r"(一会儿|待会儿|过会儿|等会儿|稍后)", raw_text):
        _debug_log(f"[结构化提醒] ⚠️ 检测到模糊时间词，time_offset 强制为 5 分钟")
        time_offset = 5
        absolute_time = None

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

        # v3.9.7: 防御性检查——检测 LLM 是否误将"X分钟"解析为"X小时"
        # 现象：time_offset很大（≥50）但 raw_text 中只有"分钟"没有"小时"
        if time_offset >= 50 and raw_text:
            has_minute = bool(re.search(r'(\d+|[一二两三四五六七八九])\s*分[钟]?', raw_text))
            has_hour = bool(re.search(r'(\d+|[一二两三四五六七八九])\s*[个]?\s*小?时', raw_text))
            if has_minute and not has_hour:
                _debug_log(f"[结构化提醒] ⚠️ 检测到疑似LLM时间误判: "
                           f"time_offset={time_offset}但raw_text含分钟不含小时,尝试重新解析")
                parsed_time, _ = _parse_time(_normalize_chinese_numbers(raw_text))
                if parsed_time:
                    # 重新计算: 正则解析的结果是绝对的HH:MM,需要转为相对偏移
                    try:
                        parsed_dt = datetime.strptime(
                            f"{now.strftime('%Y-%m-%d')} {parsed_time}", "%Y-%m-%d %H:%M")
                        new_offset = int((parsed_dt - now).total_seconds() / 60)
                        if new_offset < time_offset and new_offset > 0:
                            _debug_log(f"[结构化提醒] ✅ 时间修正: {time_offset}→{new_offset}分钟")
                            time_offset = new_offset
                            future = now + timedelta(minutes=new_offset)
                            date_str = future.strftime("%Y-%m-%d")
                            time_str = future.strftime("%H:%M")
                    except (ValueError, TypeError):
                        pass

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
                _debug_log(f"[结构化提醒] 时间已过,推到明天: {date_str} {time_str}")
        except ValueError:
            _debug_log(f"[结构化提醒] 时间格式异常: {time_str!r},回退到 LLM 解析")
            time_str = ""
            date_str = ""

    else:
        # 两者都为 None：降级处理
        time_str = ""
        date_str = ""

    # ---- 第 2 步：时间解析失败时，尝试从 raw_text 降级解析 ----
    if not time_str:
        _debug_log(f"[结构化提醒] 参数中无有效时间,尝试从 raw_text 解析...")
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
            print(f"⚠️ 结构化提醒时间解析失败,使用 1 分钟后({date_str} {time_str})作为保底")

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


# ===================== 行为模式提炼（v3.3.0 新增）=====================


def _cluster_values(values, window):
    """
    将一组数值按指定窗口大小聚类。
    返回嵌套列表，每个子列表是窗口内的数值集合。
    """
    if not values:
        return []
    sorted_vals = sorted(values)
    clusters = []
    current = [sorted_vals[0]]
    for v in sorted_vals[1:]:
        if v - current[0] <= window:
            current.append(v)
        else:
            clusters.append(current)
            current = [v]
    clusters.append(current)
    return clusters


def extract_patterns():
    """
    扫描所有待办和日程事件，提炼用户行为模式。

    分析维度:
    1. time_based  — 同一内容在相似时间段重复出现
    2. interval    — 同一内容以固定间隔创建
    3. association — 内容 A 之后频繁出现内容 B

    返回:
        list[dict]  提炼出的模式列表，按 confidence 降序排列
    """
    todos = _load_todos()
    events = _load_events()

    patterns = []

    # ================================================================
    # 1. time_based: 相同内容在相似时间段重复
    # ================================================================
    content_groups = {}  # key → [items]
    for t in todos:
        title = t.get("title", "").strip()
        if not title:
            continue
        # 归一化：去掉"我"前缀，统一内容表达
        key = re.sub(r'^我', '', title)
        content_groups.setdefault(key, []).append(t)

    # 同样处理事件
    for e in events:
        title = e.get("title", "").strip()
        if not title:
            continue
        key = re.sub(r'^我', '', title)
        content_groups.setdefault(key, []).append(e)

    for key, items in content_groups.items():
        if len(items) < 2:
            continue

        minutes_list = []
        for item in items:
            t_str = item.get("due_time") or item.get("time", "")
            if t_str:
                try:
                    h, m = map(int, t_str.split(":"))
                    minutes_list.append(h * 60 + m)
                except ValueError:
                    continue

        if len(minutes_list) < 2:
            continue

        # 60 分钟窗口聚类
        clusters = _cluster_values(minutes_list, 60)
        for cluster in clusters:
            if len(cluster) >= 2:
                lo, hi = min(cluster), max(cluster)
                time_range = f"{lo // 60:02d}:{lo % 60:02d}-{hi // 60:02d}:{hi % 60:02d}"
                freq = len(cluster)
                patterns.append({
                    "type": "time_based",
                    "content": key,
                    "time_range": time_range,
                    "frequency": freq,
                    "confidence": round(min(0.95, 0.5 + freq * 0.1), 2),
                })

    # ================================================================
    # 2. interval: 同一内容以固定间隔创建
    # ================================================================
    for key, items in content_groups.items():
        if len(items) < 3:
            continue

        items_sorted = sorted(items, key=lambda x: x.get("created_at", ""))
        intervals = []
        for i in range(1, len(items_sorted)):
            try:
                t1 = datetime.strptime(items_sorted[i - 1]["created_at"], "%Y-%m-%dT%H:%M:%S")
                t2 = datetime.strptime(items_sorted[i]["created_at"], "%Y-%m-%dT%H:%M:%S")
                diff = (t2 - t1).total_seconds() / 60
                if diff > 0:
                    intervals.append(diff)
            except (ValueError, KeyError):
                continue

        if len(intervals) < 2:
            continue

        # 检查间隔一致性（±25% 容差）
        avg = sum(intervals) / len(intervals)
        if avg <= 0:
            continue
        consistent = all(abs(i - avg) / avg <= 0.25 for i in intervals)
        if consistent:
            patterns.append({
                "type": "interval",
                "content": key,
                "interval_minutes": round(avg),
                "occurrences": len(items),
                "confidence": round(min(0.95, 0.6 + len(items) * 0.05), 2),
            })

    # ================================================================
    # 3. association: A 出现后频繁跟随 B
    # ================================================================
    all_items = todos + events
    all_sorted = sorted(all_items, key=lambda x: x.get("created_at", ""))
    pairs = {}
    for i in range(1, len(all_sorted)):
        a = (all_sorted[i - 1].get("title") or "").strip()
        b = (all_sorted[i].get("title") or "").strip()
        if a and b and a != b:
            pairs[f"{a}→{b}"] = pairs.get(f"{a}→{b}", 0) + 1

    for pair_key, count in pairs.items():
        if count >= 2:
            a, b = pair_key.split("→", 1)
            patterns.append({
                "type": "association",
                "from": a,
                "to": b,
                "frequency": count,
                "confidence": round(min(0.9, 0.4 + count * 0.15), 2),
            })

    # 按置信度降序
    patterns.sort(key=lambda p: p.get("confidence", 0), reverse=True)
    _debug_log(f"[模式提炼] 共发现 {len(patterns)} 条模式")
    return patterns


# ===================== 独立测试入口 =====================
if __name__ == "__main__":
    print("🧪 日程模块测试模式")
    print("输入命令测试日程功能,输入 exit 退出")
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
