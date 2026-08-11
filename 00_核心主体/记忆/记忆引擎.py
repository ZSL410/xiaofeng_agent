"""
记忆引擎 — 晓风Agent v3.7.0
=============================
从"被动存储"升级为"主动学习"的核心记忆模块。

架构变更（v3.6.0 → v3.7.0）:
- 数据分离：facts.json / patterns.json / context_summary.txt 三文件独立
- 新增 LLM 辅助的语义提取接口（add_fact / add_pattern）
- 新增分层的检索注入机制（必注入 / 按需注入）
- 衰减机制独立为 衰减调度.py，但保留兼容接口
- 为向量检索、图谱关联预留字段

向后兼容:
- 原有的 decay_patterns() / reject_pattern() / boost_pattern() 保留
- load_long_term() / save_long_term() 内部重定向到 patterns.json
- 首次运行时自动从 长期记忆.json 迁移数据
"""

import json
import os
import logging
from datetime import datetime, date
from typing import Optional, Union

# ===================== 路径配置 =====================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SHORT_TERM_FILE = os.path.join(BASE_DIR, "短期记忆.json")
FACTS_FILE = os.path.join(BASE_DIR, "facts.json")
PATTERNS_FILE = os.path.join(BASE_DIR, "patterns.json")
PATTERNS_ARCHIVE_FILE = os.path.join(BASE_DIR, "patterns_archive.json")
CONTEXT_SUMMARY_FILE = os.path.join(BASE_DIR, "context_summary.txt")
LONG_TERM_FILE = os.path.join(BASE_DIR, "长期记忆.json")  # v3.6.0 兼容

# 日志
logger = logging.getLogger("memory_engine")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[记忆引擎] %(levelname)s %(message)s"))
    logger.addHandler(_h)

# ===================== v3.9.29: 统一记忆存储（15 字段标准）=====================

import re
import random
import string
from datetime import timedelta

MEMORY_FILE = os.path.join(BASE_DIR, "memory.json")
VALID_MEMORY_SUBTYPES = {"event", "fact", "habit", "reflection"}

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
    """将 HH:MM 时间映射到时段时间标签（与财务/日程模块一致）。"""
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
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
    m = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})$', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r'^(\d{4}-\d{2}-\d{2})T', s)
    if m:
        return m.group(1)
    return s


def _normalize_time(time_str):
    """确保时间格式为 HH:MM（空时间默认 12:00）。"""
    if not time_str:
        return "12:00"
    s = str(time_str).strip()
    if re.match(r'^\d{1,2}:\d{2}$', s):
        hh, mm = s.split(":")
        return f"{int(hh):02d}:{mm}"
    if re.match(r'^\d{2}:\d{2}:\d{2}$', s):
        return s[:5]
    if "T" in s:
        s = s.split("T")[1]
        if re.match(r'^\d{1,2}:\d{2}', s):
            return s[:5]
    return "12:00"


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

    def _key(r):
        d = r.get("date", "") or "0000-00-00"
        t = r.get("time", "") or "12:00"
        return (-_date_to_ordinal(d), t)

    return sorted(records, key=_key)


def _generate_id(date_str="", time_str=""):
    """生成唯一 ID: memory_{YYYYMMDD}_{HHMM}_{6字符}"""
    now = datetime.now()
    if date_str:
        try:
            d = datetime.strptime(_normalize_date(date_str), "%Y-%m-%d")
        except ValueError:
            d = now
    else:
        d = now
    if time_str:
        t_part = _normalize_time(time_str).replace(":", "")
    else:
        t_part = now.strftime("%H%M")
    rand = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"memory_{d.strftime('%Y%m%d')}_{t_part}_{rand}"


def _generate_content(period, title):
    """生成内容摘要: {period}{title} → 下午讨论记忆系统规范（无时段则仅标题）"""
    if period:
        return f"{period}{title}"
    return title or "记忆"


def _load_memory_raw() -> dict:
    """加载 memory.json 原始数据"""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {"version": "2.0", "memories": []}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": "2.0", "memories": []}


def _save_memory_raw(data: dict):
    """保存 memory.json（自动排序）"""
    records = data.get("memories", [])
    data["memories"] = _sort_records(records)
    data["updated_at"] = datetime.now().isoformat()
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _extract_time_from_range(time_range):
    """从 time_range（如 '23:24-23:51'）提取起始时间 HH:MM"""
    if not time_range:
        return ""
    m = re.search(r"(\d{2}:\d{2})", str(time_range))
    return m.group(1) if m else ""


def _date_range_for(time_filter):
    """将时间筛选词转换为 (start, end) 日期范围字符串"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if time_filter in ("today", "今天"):
        return today, today
    if time_filter in ("yesterday", "昨天"):
        d = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        return d, d
    if time_filter in ("this_week", "本周"):
        monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        sunday = (now + timedelta(days=(6 - now.weekday()))).strftime("%Y-%m-%d")
        return monday, sunday
    if time_filter in ("last_week", "上周"):
        monday = (now - timedelta(days=now.weekday() + 7)).strftime("%Y-%m-%d")
        sunday = (now - timedelta(days=now.weekday() + 1)).strftime("%Y-%m-%d")
        return monday, sunday
    if time_filter in ("this_month", "本月"):
        start = now.strftime("%Y-%m") + "-01"
        if now.month == 12:
            end = (datetime(now.year + 1, 1, 1) - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            end = (datetime(now.year, now.month + 1, 1) - timedelta(days=1)).strftime("%Y-%m-%d")
        return start, end
    return None, None


def _fact_to_memory(f):
    """将 facts.json 记录转换为 15 字段统一记忆记录"""
    content = (f.get("content") or "").strip()
    if not content:
        return None
    now = datetime.now()
    created = f.get("created_at") or ""
    date_str = f.get("date") or (created[:10] if len(created) >= 10 else now.strftime("%Y-%m-%d"))
    time_str = f.get("time") or (created.split("T")[-1][:5] if "T" in created else "12:00")
    date_str = _normalize_date(date_str)
    time_str = _normalize_time(time_str)
    period = _get_period(time_str)
    title = f.get("title") or content[:30]
    importance = max(1, min(10, int(round((f.get("importance") or 0.5) * 10))))
    return {
        "id": _generate_id(date_str, time_str),
        "type": "memory",
        "subtype": "fact",
        "date": date_str,
        "time": time_str,
        "datetime": f"{date_str}T{time_str}:00",
        "period": period,
        "title": title,
        "content": content,
        "detail": f.get("detail") or content,
        "mood": f.get("mood"),
        "tags": f.get("tags") or ([f.get("category")] if f.get("category") and f.get("category") != "其他" else []),
        "importance": importance,
        "created_at": created or now.isoformat(),
        "updated_at": f.get("updated_at"),
        "category": f.get("category"),
        "pinned": f.get("pinned", False),
        "source": f.get("source"),
        "embedding": f.get("embedding"),
    }


def _pattern_to_memory(p):
    """将 patterns.json 记录转换为 15 字段统一记忆记录（subtype=habit）"""
    content = (p.get("content") or "").strip()
    if not content:
        return None
    now = datetime.now()
    created = p.get("created_at") or p.get("last_trigger") or ""
    date_str = p.get("date") or (created[:10] if len(created) >= 10 else now.strftime("%Y-%m-%d"))
    time_str = p.get("time") or _extract_time_from_range(p.get("time_range")) or (
        created.split("T")[-1][:5] if "T" in created else "12:00")
    date_str = _normalize_date(date_str)
    time_str = _normalize_time(time_str)
    period = _get_period(time_str)
    title = p.get("title") or content[:30]
    # 将模式元数据（类型/频次/置信度）写入 detail
    detail_parts = [content]
    if p.get("type"):
        detail_parts.append(f"类型:{p['type']}")
    if p.get("frequency"):
        detail_parts.append(f"频次:{p['frequency']}")
    if p.get("confidence") is not None:
        detail_parts.append(f"置信度:{p['confidence']}")
    if p.get("time_range"):
        detail_parts.append(f"时段:{p['time_range']}")
    return {
        "id": _generate_id(date_str, time_str),
        "type": "memory",
        "subtype": "habit",
        "date": date_str,
        "time": time_str,
        "datetime": f"{date_str}T{time_str}:00",
        "period": period,
        "title": title,
        "content": content,
        "detail": p.get("detail") or "，".join(detail_parts),
        "mood": p.get("mood"),
        "tags": p.get("tags") or ([p.get("type")] if p.get("type") else []),
        "importance": max(1, min(10, int(round((p.get("confidence") or 0.5) * 10)))),
        "created_at": created or now.isoformat(),
        "updated_at": p.get("updated_at"),
        "pattern_type": p.get("type"),
        "frequency": p.get("frequency"),
        "confidence": p.get("confidence"),
        "archived": p.get("archived", False),
        "embedding": p.get("embedding"),
    }


def _migrate_to_standard_format():
    """将 facts.json / patterns.json 现有数据迁移到统一 memory.json（一次性，幂等）。

    返回: 迁移条数（已迁移过则返回 0）
    """
    memory_data = _load_memory_raw()
    existing = memory_data.get("memories", [])
    if memory_data.get("_migrated_from"):
        return 0  # 已完成过迁移

    migrated = 0
    existing_keys = set()
    for m in existing:
        existing_keys.add((m.get("subtype"), m.get("content") or ""))

    # 迁移 facts.json
    facts_data = _load_facts_raw()
    for f in facts_data.get("facts", []):
        rec = _fact_to_memory(f)
        if not rec:
            continue
        key = (rec["subtype"], rec["content"])
        if key not in existing_keys:
            existing.append(rec)
            existing_keys.add(key)
            migrated += 1

    # 迁移 patterns.json
    patterns_data = _load_patterns_raw()
    for p in patterns_data.get("patterns", []):
        if p.get("archived", False):
            continue
        rec = _pattern_to_memory(p)
        if not rec:
            continue
        key = (rec["subtype"], rec["content"])
        if key not in existing_keys:
            existing.append(rec)
            existing_keys.add(key)
            migrated += 1

    if migrated > 0:
        memory_data["memories"] = existing
        memory_data["_migrated_from"] = ["facts.json", "patterns.json"]
        _save_memory_raw(memory_data)
        logger.info(f"统一记忆迁移完成: {migrated} 条 facts/patterns → memory.json")

    return migrated


def load_memories() -> list:
    """加载统一记忆库（自动迁移 + 自动排序）"""
    _migrate_to_standard_format()
    data = _load_memory_raw()
    return _sort_records(data.get("memories", []))


def add_memory_event(title, detail=None, mood=None, tags=None,
                     importance=5, subtype="event",
                     date_str=None, time_str=None, content=None) -> dict:
    """
    创建丰富记忆条目（15 字段标准格式）。

    参数:
        title: 一句话摘要
        detail: 完整上下文（可含一段描述）
        mood: 情绪/状态标签（可选）
        tags: 关键词标签列表（可选）
        importance: 重要性 1-10
        subtype: event / fact / habit / reflection
        date_str: 日期 YYYY-MM-DD（默认今天）
        time_str: 时间 HH:MM（默认当前时间）
        content: 核心描述（默认按 {period}{title} 自动生成）

    返回:
        {"status": "added"|"skipped", "id": str, "message": str}
    """
    if not title or not title.strip():
        return {"status": "skipped", "id": "", "message": "标题为空，跳过"}
    if subtype not in VALID_MEMORY_SUBTYPES:
        subtype = "event"

    now = datetime.now()
    date_str = _normalize_date(date_str) if date_str else now.strftime("%Y-%m-%d")
    time_str = _normalize_time(time_str) if time_str else now.strftime("%H:%M")
    period = _get_period(time_str)
    dt_str = f"{date_str}T{time_str}:00"
    content = (content or "").strip() or _generate_content(period, title.strip())
    detail = (detail or "").strip() or content
    importance = max(1, min(10, int(importance)))

    record = {
        "id": _generate_id(date_str, time_str),
        "type": "memory",
        "subtype": subtype,
        "date": date_str,
        "time": time_str,
        "datetime": dt_str,
        "period": period,
        "title": title.strip(),
        "content": content,
        "detail": detail,
        "mood": mood,
        "tags": tags or [],
        "importance": importance,
        "created_at": now.isoformat(),
        "updated_at": None,
    }

    data = _load_memory_raw()
    data.setdefault("memories", []).append(record)
    _save_memory_raw(data)
    logger.info(f"记忆条目已记录: [{record['id']}] {title[:30]}... (subtype={subtype})")
    return {"status": "added", "id": record["id"], "message": f"记忆已记录 (id={record['id']})"}


def get_memories(subtype=None, time_filter=None, period=None, limit=None) -> list:
    """
    查询统一记忆库，支持按 subtype / 时间范围 / 时段筛选。

    参数:
        subtype: event / fact / habit / reflection，None 表示全部
        time_filter: today / yesterday / this_week / last_week / this_month
        period: 时段标签（凌晨/早上/上午/中午/下午/晚上/深夜）
        limit: 最大返回条数

    返回:
        已按 date 降序 + time 升序排序的记忆记录列表
    """
    records = load_memories()
    if subtype:
        records = [r for r in records if r.get("subtype") == subtype]
    if period:
        records = [r for r in records if r.get("period") == period]
    if time_filter:
        start, end = _date_range_for(time_filter)
        if start:
            records = [r for r in records if start <= (r.get("date") or "") <= end]
    if limit:
        records = records[:limit]
    return records


def _sync_fact_to_memory(fact):
    """将新增/更新的 fact 同步到统一记忆库（subtype=fact）"""
    try:
        rec = _fact_to_memory(fact)
        if not rec:
            return
        data = _load_memory_raw()
        memories = data.setdefault("memories", [])
        for i, m in enumerate(memories):
            if m.get("subtype") == "fact" and m.get("content") == rec["content"]:
                rec["id"] = m["id"]
                rec["created_at"] = m.get("created_at") or rec["created_at"]
                memories[i] = rec
                _save_memory_raw(data)
                return
        memories.append(rec)
        _save_memory_raw(data)
    except Exception as e:
        logger.warning(f"同步事实到统一记忆失败: {e}")


def _sync_pattern_to_memory(pattern):
    """将新增/更新的 pattern 同步到统一记忆库（subtype=habit）"""
    try:
        rec = _pattern_to_memory(pattern)
        if not rec:
            return
        data = _load_memory_raw()
        memories = data.setdefault("memories", [])
        for i, m in enumerate(memories):
            if m.get("subtype") == "habit" and m.get("content") == rec["content"]:
                rec["id"] = m["id"]
                rec["created_at"] = m.get("created_at") or rec["created_at"]
                memories[i] = rec
                _save_memory_raw(data)
                return
        memories.append(rec)
        _save_memory_raw(data)
    except Exception as e:
        logger.warning(f"同步习惯到统一记忆失败: {e}")


# ===================== 数据迁移（v3.6.0 → v3.7.0）=====================


def _migrate_if_needed():
    """首次启动时将 长期记忆.json 中的 patterns 迁移到 patterns.json"""
    if not os.path.exists(LONG_TERM_FILE):
        return  # 无需迁移

    if os.path.exists(PATTERNS_FILE):
        # patterns.json 已存在，判断是否已完成迁移
        try:
            with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
                if isinstance(existing, dict) and existing.get("_migrated_from"):
                    return  # 已迁移
        except (json.JSONDecodeError, FileNotFoundError):
            pass  # 文件损坏或不存在，重新迁移

    try:
        with open(LONG_TERM_FILE, "r", encoding="utf-8") as f:
            old_data = json.load(f)

        if not isinstance(old_data, dict):
            return

        old_patterns = old_data.get("patterns", [])
        if not old_patterns:
            return

        # 加载或创建 patterns.json
        patterns_data = _load_patterns_raw()

        # 分配新 ID 并补充字段
        max_id = 0
        for p in patterns_data.get("patterns", []):
            pid = p.get("id", "")
            if pid.startswith("pattern_"):
                try:
                    max_id = max(max_id, int(pid.split("_")[1]))
                except (ValueError, IndexError):
                    pass

        for op in old_patterns:
            if not isinstance(op, dict):
                continue
            max_id += 1
            op["id"] = f"pattern_{max_id:03d}"
            op.setdefault("created_at", op.get("last_triggered", datetime.now().isoformat()))
            op.setdefault("embedding", None)
            op.setdefault("related_to", [])
            op.setdefault("relation_types", [])
            op.setdefault("archived", False)
            # 统一字段名
            if "last_triggered" in op and "last_trigger" not in op:
                op["last_trigger"] = op.pop("last_triggered")
            patterns_data["patterns"].append(op)

        patterns_data["_migrated_from"] = "长期记忆.json"
        patterns_data["updated_at"] = datetime.now().isoformat()
        _save_patterns_raw(patterns_data)

        # 备份原文件
        bak_path = LONG_TERM_FILE + ".v360.bak"
        os.rename(LONG_TERM_FILE, bak_path)
        logger.info(f"数据迁移完成: {len(old_patterns)} 条 patterns 已从 长期记忆.json 迁入 patterns.json")

    except Exception as e:
        logger.warning(f"数据迁移跳过: {e}")


# ===================== 底层读写 =====================


def _load_facts_raw() -> dict:
    """加载 facts.json 原始数据"""
    try:
        with open(FACTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {"version": "1.0", "facts": []}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": "1.0", "facts": []}


def _save_facts_raw(data: dict):
    """保存 facts.json"""
    data["updated_at"] = datetime.now().isoformat()
    with open(FACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_patterns_raw() -> dict:
    """加载 patterns.json 原始数据"""
    try:
        with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {"version": "1.0", "patterns": []}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": "1.0", "patterns": []}


def _save_patterns_raw(data: dict):
    """保存 patterns.json"""
    data["updated_at"] = datetime.now().isoformat()
    with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_context_summary() -> str:
    """加载会话摘要"""
    try:
        with open(CONTEXT_SUMMARY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _save_context_summary(text: str):
    """保存会话摘要"""
    with open(CONTEXT_SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(text.strip())


# ===================== 短期记忆（保持不变）=====================


def load_short_term() -> list:
    """加载短期记忆（最近 20 轮对话）"""
    try:
        with open(SHORT_TERM_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_short_term(history: list):
    """保存短期记忆，保留最近 20 轮"""
    if len(history) > 20:
        history = history[-20:]
    with open(SHORT_TERM_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ===================== 对话归档（v3.10.0）=====================

DIALOGUE_ARCHIVE_FILE = os.path.join(BASE_DIR, "对话归档.json")
_CURRENT_DIALOGUE_ID = None  # 同一进程 = 同一会话，首次写入时生成并复用


def _generate_dialogue_id():
    """生成对话记录 ID: dialogue_{YYYYMMDD}_{HHMM}_{6字符}"""
    now = datetime.now()
    rand = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"dialogue_{now.strftime('%Y%m%d')}_{now.strftime('%H%M')}_{rand}"


def _get_session_id() -> str:
    """会话 ID：同一进程内所有对话记录共享（session_{YYYYMMDD}_{HHMM}_{xxx}）。"""
    global _CURRENT_DIALOGUE_ID
    if _CURRENT_DIALOGUE_ID is None:
        now = datetime.now()
        rand = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
        _CURRENT_DIALOGUE_ID = f"session_{now.strftime('%Y%m%d')}_{now.strftime('%H%M')}_{rand}"
    return _CURRENT_DIALOGUE_ID


def _load_dialogue_raw() -> dict:
    """加载 对话归档.json 原始数据"""
    try:
        with open(DIALOGUE_ARCHIVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {"version": "1.0", "records": []}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": "1.0", "records": []}


def _dialogue_sort_key(r):
    """对话排序键：created_at（含微秒）优先，timestamp 兜底。"""
    return r.get("created_at") or r.get("timestamp") or ""


def _save_dialogue_raw(data: dict):
    """保存对话归档（按时间降序，最新在前）"""
    records = data.get("records", [])
    records.sort(key=_dialogue_sort_key, reverse=True)
    data["records"] = records
    data["updated_at"] = datetime.now().isoformat()
    with open(DIALOGUE_ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_dialogue_record(role, original_text, summary, tags=None, mood=None,
                        related_memory_ids=None) -> dict:
    """
    新增一条对话归档记录（v3.10.0，原始对话 + 摘要）。

    参数:
        role: 'user' / 'assistant'
        original_text: 原始对话文本
        summary: 1-2 句摘要（工具轮由规则生成，纯对话由 3b 轻量生成）
        tags: 关键词标签列表（可选）
        mood: 情绪标签（可选，Phase 6 自动检测）
        related_memory_ids: 关联记忆 ID 列表（可选，指向 memory.json 条目）

    返回:
        {"status": "added"|"skipped", "id": str, "message": str}
    """
    if not original_text or not str(original_text).strip():
        return {"status": "skipped", "id": "", "message": "原始文本为空，跳过"}
    text = str(original_text).strip()
    now = datetime.now()
    record = {
        "id": _generate_dialogue_id(),
        "dialogue_id": _get_session_id(),
        "role": role if role in ("user", "assistant") else "user",
        "original_text": text,
        "summary": (summary or "").strip() or text[:20],
        "tags": tags or [],
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "mood": mood,
        "related_memory_ids": related_memory_ids or [],
        "created_at": now.isoformat(),
    }
    data = _load_dialogue_raw()
    data.setdefault("records", []).append(record)
    _save_dialogue_raw(data)
    logger.info(f"对话归档: [{record['id']}] {role} {text[:20]}...")
    return {"status": "added", "id": record["id"], "message": f"对话已归档 (id={record['id']})"}


def get_dialogue_summary(limit: int = 3) -> list:
    """
    返回最近 N 轮对话摘要（供 prompt 注入，Phase 2 渐进式披露使用）。

    每项: {id, role, summary, tags, timestamp}
    """
    data = _load_dialogue_raw()
    records = data.get("records", [])
    records.sort(key=_dialogue_sort_key, reverse=True)
    out = []
    for r in records[:limit]:
        out.append({
            "id": r.get("id"),
            "role": r.get("role"),
            "summary": r.get("summary"),
            "tags": r.get("tags", []),
            "timestamp": r.get("timestamp"),
        })
    return out


def expand_dialogue(record_id: str) -> dict:
    """
    按 ID 返回完整对话记录（含 original_text，Phase 2 按需展开使用）。
    未找到返回 None。
    """
    data = _load_dialogue_raw()
    for r in data.get("records", []):
        if r.get("id") == record_id:
            return r
    return None


def search_dialogue(keyword: str, limit: int = 5) -> list:
    """
    按关键词搜索 original_text / summary / tags（Phase 2 使用）。
    返回匹配记录列表，最新在前。
    """
    if not keyword or not str(keyword).strip():
        return []
    kw = str(keyword).strip()
    data = _load_dialogue_raw()
    records = data.get("records", [])
    records.sort(key=_dialogue_sort_key, reverse=True)
    hits = []
    for r in records:
        text = (r.get("original_text") or "") + (r.get("summary") or "")
        if kw in text or any(kw in (t or "") for t in (r.get("tags") or [])):
            hits.append(r)
        if len(hits) >= limit:
            break
    return hits


# ===================== 长期记忆（v3.6.0 兼容）=====================


def load_long_term() -> dict:
    """
    加载长期记忆（兼容 v3.6.0 接口）。
    内部从 patterns.json 读取，以旧格式返回。
    """
    data = _load_patterns_raw()
    patterns = data.get("patterns", [])
    return {
        "patterns": [p for p in patterns if not p.get("archived", False)],
        "last_refined": data.get("updated_at", ""),
        "last_decay": data.get("last_decay", ""),
    }


def save_long_term(memory: dict):
    """
    保存长期记忆（兼容 v3.6.0 接口）。
    内部合并到 patterns.json。
    """
    if not isinstance(memory, dict):
        return
    data = _load_patterns_raw()
    new_patterns = memory.get("patterns", [])
    if new_patterns:
        # 合并：按 content + type 去重
        existing_keys = set()
        for p in data.get("patterns", []):
            key = (p.get("content", ""), p.get("type", ""))
            existing_keys.add(key)

        max_id = 0
        for p in data.get("patterns", []):
            pid = p.get("id", "")
            if pid.startswith("pattern_"):
                try:
                    max_id = max(max_id, int(pid.split("_")[1]))
                except (ValueError, IndexError):
                    pass

        for np_item in new_patterns:
            key = (np_item.get("content", ""), np_item.get("type", ""))
            if key not in existing_keys:
                max_id += 1
                np_item["id"] = f"pattern_{max_id:03d}"
                np_item.setdefault("created_at", datetime.now().isoformat())
                np_item.setdefault("embedding", None)
                np_item.setdefault("related_to", [])
                np_item.setdefault("relation_types", [])
                np_item.setdefault("archived", False)
                if "last_triggered" in np_item and "last_trigger" not in np_item:
                    np_item["last_trigger"] = np_item.pop("last_triggered")
                data["patterns"].append(np_item)
                existing_keys.add(key)

    if "last_refined" in memory:
        data["updated_at"] = memory["last_refined"]
    if "last_decay" in memory:
        data["last_decay"] = memory["last_decay"]

    _save_patterns_raw(data)


# ===================== 新接口：事实管理 =====================

# 合法的 fact category 枚举
VALID_FACT_CATEGORIES = {"偏好", "身份", "技能", "习惯", "其他"}


def add_fact(content: str, category: str = "其他",
             pinned: bool = False, importance: float = 0.5,
             source: str = "对话") -> dict:
    """
    写入事实记忆。

    参数:
        content: 事实内容（一句完整陈述）
        category: 分类，必须在 {偏好, 身份, 技能, 习惯, 其他} 中
        pinned: 是否固定注入 Prompt
        importance: 重要性评分 0~1
        source: 来源（对话 / 提炼 / 用户确认）

    返回:
        {"status": "added"|"merged"|"skipped", "id": str, "message": str}
    """
    if not content or not content.strip():
        return {"status": "skipped", "id": "", "message": "内容为空，跳过"}

    content = content.strip()

    # 校验分类
    if category not in VALID_FACT_CATEGORIES:
        logger.warning(f"分类 '{category}' 不在合法枚举中，回退为 '其他'")
        category = "其他"

    # 限制 importance 范围
    importance = max(0.0, min(1.0, importance))

    data = _load_facts_raw()
    facts = data.get("facts", [])

    # 重复检测：完全相同内容
    for existing in facts:
        if existing.get("content", "") == content:
            # 更新 importance（取最大值）
            if importance > existing.get("importance", 0.5):
                existing["importance"] = importance
            existing["updated_at"] = datetime.now().isoformat()
            _save_facts_raw(data)
            logger.info(f"事实已存在（合并）: {content[:30]}...")
            return {"status": "merged", "id": existing["id"],
                    "message": f"已存在的事实已更新 (id={existing['id']})"}

    # 相似度检测（简单子串比例）
    for existing in facts:
        exist_content = existing.get("content", "")
        similarity = _text_similarity(content, exist_content)
        if similarity > 0.85:
            # 合并：保留更完整的表述
            if len(content) > len(exist_content):
                existing["content"] = content
            existing["importance"] = max(existing.get("importance", 0.5), importance)
            existing["updated_at"] = datetime.now().isoformat()
            _save_facts_raw(data)
            logger.info(f"相似事实合并 (sim={similarity:.2f}): {content[:30]}...")
            return {"status": "merged", "id": existing["id"],
                    "message": f"相似事实已合并 (sim={similarity:.2f})"}

    # 生成 ID
    max_id = 0
    for f_item in facts:
        fid = f_item.get("id", "")
        if fid.startswith("fact_"):
            try:
                max_id = max(max_id, int(fid.split("_")[1]))
            except (ValueError, IndexError):
                pass

    new_id = f"fact_{max_id + 1:03d}"
    now = datetime.now().isoformat()

    new_fact = {
        "id": new_id,
        "content": content,
        "category": category,
        "source": source,
        "pinned": pinned,
        "created_at": now,
        "updated_at": now,
        "embedding": None,
        "importance": importance,
    }

    facts.append(new_fact)
    _save_facts_raw(data)
    _sync_fact_to_memory(new_fact)  # v3.9.29: 同步到统一记忆库

    logger.info(f"新事实已写入: [{new_id}] {content[:40]}... (cat={category}, imp={importance})")
    return {"status": "added", "id": new_id, "message": f"新事实已记录 (id={new_id})"}


def update_importance(fact_id: str, delta: float) -> dict:
    """
    更新事实的重要性评分。

    参数:
        fact_id: 事实 ID
        delta: 变化量（正数提升，负数降低）

    返回:
        {"status": "ok"|"not_found", "new_importance": float}
    """
    data = _load_facts_raw()
    for f_item in data.get("facts", []):
        if f_item.get("id") == fact_id:
            new_imp = max(0.0, min(1.0, f_item.get("importance", 0.5) + delta))
            f_item["importance"] = round(new_imp, 2)
            f_item["updated_at"] = datetime.now().isoformat()
            _save_facts_raw(data)
            logger.info(f"事实 {fact_id} importance: {f_item['importance'] - delta:.2f} → {f_item['importance']:.2f}")
            return {"status": "ok", "new_importance": f_item["importance"]}
    return {"status": "not_found", "new_importance": 0.0}


def get_pinned_facts(limit: int = 5) -> list:
    """
    获取固定事实（按 importance 降序）。

    参数:
        limit: 最大返回条数

    返回:
        事实字典列表
    """
    data = _load_facts_raw()
    pinned = [f for f in data.get("facts", []) if f.get("pinned", False)]
    pinned.sort(key=lambda x: x.get("importance", 0), reverse=True)
    return pinned[:limit]


def get_memory_stats() -> dict:
    """
    获取记忆系统统计信息。

    返回:
        {facts_count, patterns_count, active_patterns, archived_patterns}
    """
    facts_data = _load_facts_raw()
    patterns_data = _load_patterns_raw()

    all_patterns = patterns_data.get("patterns", [])
    active = [p for p in all_patterns if not p.get("archived", False)]
    archived = [p for p in all_patterns if p.get("archived", False)]

    return {
        "facts_count": len(facts_data.get("facts", [])),
        "patterns_count": len(all_patterns),
        "active_patterns": len(active),
        "archived_patterns": len(archived),
    }


def merge_similar_facts() -> dict:
    """
    合并相似事实（去重维护）。

    返回:
        {"merged": int, "skipped": int}
    """
    data = _load_facts_raw()
    facts = data.get("facts", [])
    merged = 0
    kept = []

    for i, f1 in enumerate(facts):
        is_dup = False
        for j, f2 in enumerate(kept):
            sim = _text_similarity(f1.get("content", ""), f2.get("content", ""))
            if sim > 0.85:
                # 保留更长的、importance 更高的
                if f1.get("importance", 0) > f2.get("importance", 0):
                    f2["content"] = f1["content"] if len(f1["content"]) > len(f2["content"]) else f2["content"]
                    f2["importance"] = f1["importance"]
                elif len(f1["content"]) > len(f2["content"]):
                    f2["content"] = f1["content"]
                f2["updated_at"] = datetime.now().isoformat()
                merged += 1
                is_dup = True
                break
        if not is_dup:
            kept.append(f1)

    data["facts"] = kept
    _save_facts_raw(data)
    logger.info(f"事实去重: {merged} 条合并, {len(kept)} 条保留")
    return {"merged": merged, "skipped": len(facts) - merged - len(kept)}


# ===================== 新接口：习惯管理 =====================

VALID_PATTERN_TYPES = {"time_based", "interval", "association"}


def add_pattern(content: str, pattern_type: str = "time_based",
                confidence: float = 0.3) -> dict:
    """
    写入习惯记忆。

    参数:
        content: 习惯描述
        pattern_type: 类型（time_based / interval / association）
        confidence: 初始置信度 0~1

    返回:
        {"status": "added"|"merged", "id": str, "message": str}
    """
    if not content or not content.strip():
        return {"status": "skipped", "id": "", "message": "内容为空，跳过"}

    content = content.strip()

    if pattern_type not in VALID_PATTERN_TYPES:
        logger.warning(f"pattern_type '{pattern_type}' 不合法，回退为 'time_based'")
        pattern_type = "time_based"

    confidence = max(0.0, min(1.0, confidence))

    data = _load_patterns_raw()
    patterns = data.get("patterns", [])

    # 重复检测
    for existing in patterns:
        if (existing.get("content") == content
                and existing.get("type") == pattern_type
                and not existing.get("archived", False)):
            # 合并：增加 frequency，取较高 confidence
            existing["frequency"] = existing.get("frequency", 1) + 1
            existing["confidence"] = max(existing.get("confidence", 0.5), confidence)
            existing["last_trigger"] = datetime.now().isoformat()
            _save_patterns_raw(data)
            logger.info(f"习惯已存在（频率+1）: {content[:30]}...")
            return {"status": "merged", "id": existing["id"],
                    "message": f"已存在的习惯已更新 (freq={existing['frequency']})"}

    # 生成 ID
    max_id = 0
    for p in patterns:
        pid = p.get("id", "")
        if pid.startswith("pattern_"):
            try:
                max_id = max(max_id, int(pid.split("_")[1]))
            except (ValueError, IndexError):
                pass

    new_id = f"pattern_{max_id + 1:03d}"
    now = datetime.now().isoformat()

    new_pattern = {
        "id": new_id,
        "content": content,
        "type": pattern_type,
        "frequency": 1,
        "confidence": confidence,
        "last_trigger": now,
        "created_at": now,
        "embedding": None,
        "related_to": [],
        "relation_types": [],
        "rejected": False,
        "archived": False,
    }

    patterns.append(new_pattern)
    _save_patterns_raw(data)
    _sync_pattern_to_memory(new_pattern)  # v3.9.29: 同步到统一记忆库

    logger.info(f"新习惯已写入: [{new_id}] {content[:40]}... (type={pattern_type}, conf={confidence})")
    return {"status": "added", "id": new_id, "message": f"新习惯已记录 (id={new_id})"}


def update_confidence(pattern_id: str, delta: float) -> dict:
    """
    更新习惯置信度。

    参数:
        pattern_id: 习惯 ID
        delta: 变化量（正数提升，负数降低）

    返回:
        {"status": "ok"|"not_found", "new_confidence": float}
    """
    data = _load_patterns_raw()
    for p in data.get("patterns", []):
        if p.get("id") == pattern_id:
            new_conf = max(0.0, min(1.0, p.get("confidence", 0.5) + delta))
            p["confidence"] = round(new_conf, 2)
            _save_patterns_raw(data)
            logger.info(f"习惯 {pattern_id} confidence: {p['confidence'] - delta:.2f} → {p['confidence']:.2f}")
            return {"status": "ok", "new_confidence": p["confidence"]}
    return {"status": "not_found", "new_confidence": 0.0}


def get_high_confidence_patterns(min_confidence: float = 0.5, limit: int = 5) -> list:
    """
    获取高置信度习惯（用于按需注入 Prompt）。

    参数:
        min_confidence: 最低置信度阈值
        limit: 最大返回条数

    返回:
        习惯字典列表（按 confidence 降序）
    """
    data = _load_patterns_raw()
    active = [
        p for p in data.get("patterns", [])
        if (not p.get("archived", False)
            and not p.get("rejected", False)
            and p.get("confidence", 0) >= min_confidence)
    ]
    active.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return active[:limit]


# ===================== 新接口：会话摘要 =====================


def get_context_summary() -> str:
    """获取会话摘要文本（用于 Prompt 注入）"""
    return _load_context_summary()


def rebuild_context_summary(history: list, ollama_chat_fn=None) -> str:
    """
    根据最近对话历史重新生成会话摘要。

    依赖 LLM 进行语义提炼；如果 ollama_chat_fn 为 None，
    则使用简单的关键词提取作为降级方案。

    参数:
        history: 对话历史列表 [{"role": "user/assistant", "content": "..."}]
        ollama_chat_fn: 可选的 LLM 调用函数

    返回:
        生成的摘要文本
    """
    if not history:
        return ""

    recent = history[-10:]
    user_msgs = [t.get("content", "") for t in recent if t.get("role") == "user"]

    if ollama_chat_fn:
        prompt = (
            "你是会话摘要生成器。根据以下对话，用 2-4 句中文提炼核心信息：\n"
            "1) 用户身份/角色\n"
            "2) 当前偏好\n"
            "3) 近期话题\n\n"
            "对话：\n"
        )
        for turn in recent:
            role = "用户" if turn.get("role") == "user" else "晓风"
            prompt += f"{role}: {turn.get('content', '')}\n"
        prompt += "\n请用纯文本输出摘要（不要 JSON，直接输出文字）："

        try:
            response = ollama_chat_fn(prompt)
            if response and len(response.strip()) > 10:
                _save_context_summary(response.strip())
                logger.info(f"LLM 会话摘要已更新 ({len(response)} 字符)")
                return response.strip()
        except Exception as e:
            logger.warning(f"LLM 摘要生成失败，使用降级方案: {e}")

    # 降级：简单关键词提取
    topics = set()
    for msg in user_msgs:
        # 简单提取 2-4 字的关键词
        if "提醒" in msg or "日程" in msg:
            topics.add("日程管理")
        if "记账" in msg or "花了" in msg or "财务" in msg:
            topics.add("财务记账")
        if "记忆" in msg or "记住" in msg:
            topics.add("记忆系统")

    summary = f"用户近期与晓风进行了 {len(recent)} 轮对话。"
    if topics:
        summary += f" 涉及话题：{'、'.join(topics)}。"
    _save_context_summary(summary)
    logger.info(f"降级摘要已生成 ({len(summary)} 字符)")
    return summary


# ===================== 新接口：衰减与归档 =====================


def run_decay(now_date: Optional[date] = None) -> dict:
    """
    执行置信度衰减（每日一次）。

    衰减规则:
    - last_trigger > 30 天 → confidence *= 0.95
    - confidence < 0.3 且 last_trigger > 60 天 → 标记 archived: true
    - rejected=True 的条目不衰减

    参数:
        now_date: 当前日期，默认取当天

    返回:
        {"decayed": int, "archived": int, "kept": int}
    """
    if now_date is None:
        now_date = date.today()

    data = _load_patterns_raw()
    patterns = data.get("patterns", [])
    if not patterns:
        return {"decayed": 0, "archived": 0, "kept": 0}

    decayed = 0
    archived = 0
    kept = 0

    for p in patterns:
        if not isinstance(p, dict):
            continue
        if p.get("archived", False):
            continue  # 已归档的跳过

        rejected = p.get("rejected", False)
        last_str = p.get("last_trigger", "")

        try:
            last_date = datetime.strptime(last_str[:10], "%Y-%m-%d").date() if last_str else None
        except (ValueError, TypeError):
            last_date = None

        days_since = (now_date - last_date).days if last_date else 999

        if rejected:
            # 已拒绝的保持当前状态，等待 archive_expired 统一处理
            if p.get("confidence", 0.5) < 0.3 and days_since > 60:
                p["archived"] = True
                archived += 1
            else:
                kept += 1
            continue

        # 超过 30 天未触发：每日衰减
        if days_since > 30:
            p["confidence"] = round(p.get("confidence", 0.5) * 0.95, 2)
            decayed += 1

        # 置信度过低且长期未触发 → 归档
        if p.get("confidence", 0.5) < 0.3 and days_since > 60:
            p["archived"] = True
            archived += 1
        else:
            kept += 1

    data["last_decay"] = now_date.isoformat()
    _save_patterns_raw(data)

    # 将归档条目写入归档文件
    if archived > 0:
        _write_archive([p for p in patterns if p.get("archived", False)])

    logger.info(f"衰减完成: {decayed} 条置信度降低, {archived} 条归档, {kept} 条保留")
    return {"decayed": decayed, "archived": archived, "kept": kept}


def archive_expired() -> dict:
    """
    归档所有已过期且置信度过低的记忆。

    将 archived=True 的 patterns 写入 patterns_archive.json，
    并从 patterns.json 中移除。

    返回:
        {"archived": int}
    """
    data = _load_patterns_raw()
    all_patterns = data.get("patterns", [])

    to_archive = [p for p in all_patterns if p.get("archived", False)]
    active = [p for p in all_patterns if not p.get("archived", False)]

    if to_archive:
        _write_archive(to_archive)

    data["patterns"] = active
    _save_patterns_raw(data)

    logger.info(f"归档完成: {len(to_archive)} 条已移至 patterns_archive.json")
    return {"archived": len(to_archive)}


def _write_archive(archived_patterns: list):
    """将归档条目写入 patterns_archive.json（追加模式）"""
    existing = []
    try:
        with open(PATTERNS_ARCHIVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            existing = data.get("archived", []) if isinstance(data, dict) else []
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # 合并（按 id 去重）
    existing_ids = {a.get("id") for a in existing}
    for ap in archived_patterns:
        if ap.get("id") not in existing_ids:
            existing.append(ap)

    archive_data = {
        "version": "1.0",
        "archived_count": len(existing),
        "last_updated": datetime.now().isoformat(),
        "archived": existing,
    }
    with open(PATTERNS_ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive_data, f, ensure_ascii=False, indent=2)


# ===================== 检索与注入 =====================


def _estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数量。

    中文：约 1 字符 ≈ 1.5 tokens
    英文：约 1 字符 ≈ 0.3 tokens（粗略）
    """
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.3)


def build_injection_context(user_input: str = "", max_tokens: int = 2000) -> str:
    """
    根据用户输入构建 Prompt 注入上下文。

    采用分层策略:
    1. 必注入: context_summary + pinned facts (最多 5 条)
    2. 按需注入: 若涉及日程/提醒关键词 → 高置信度 patterns
    3. 全部受 max_tokens 限制

    参数:
        user_input: 当前用户输入（用于按需判断）
        max_tokens: 注入上限 tokens

    返回:
        注入用的纯文本字符串
    """
    SCHEDULE_TRIGGERS = ["提醒", "定时", "叫我", "喊我", "叫醒", "日程",
                         "待办", "任务", "todo", "闹钟", "通知", "分钟后"]

    parts = []
    token_budget = max_tokens

    # 1. 会话摘要
    summary = get_context_summary()
    if summary:
        parts.append(f"[近期摘要]\n{summary}")
        token_budget -= _estimate_tokens(summary)

    # 2. 固定事实（最多 5 条）
    pinned = get_pinned_facts(limit=5)
    if pinned:
        fact_lines = []
        for f in pinned:
            line = f"- (事实) {f['content']}"
            if _estimate_tokens(line) <= token_budget:
                fact_lines.append(line)
                token_budget -= _estimate_tokens(line)
            else:
                break
        if fact_lines:
            parts.insert(0, f"[用户记忆]\n" + "\n".join(fact_lines))

    # 3. 按需注入：涉及日程/提醒关键词
    need_patterns = any(kw in user_input for kw in SCHEDULE_TRIGGERS)
    if need_patterns and token_budget > 100:
        patterns = get_high_confidence_patterns(min_confidence=0.5, limit=5)
        if patterns:
            pattern_lines = []
            for p in patterns:
                line = f"- {p['content']} (置信度:{p.get('confidence', 0):.0%})"
                if _estimate_tokens(line) <= token_budget:
                    pattern_lines.append(line)
                    token_budget -= _estimate_tokens(line)
                else:
                    break
            if pattern_lines:
                parts.append(f"[行为习惯]\n" + "\n".join(pattern_lines))

    return "\n\n".join(parts)


# ===================== 辅助函数 =====================


def _text_similarity(a: str, b: str) -> float:
    """
    计算两段文本的相似度（基于公共子串比例）。

    返回 0~1 之间的相似度。
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    # 使用最长公共子序列比例
    shorter = a if len(a) <= len(b) else b
    longer = b if len(a) <= len(b) else a

    # 简单实现：基于公共字符比例
    common = sum(1 for c in shorter if c in longer)
    return common / len(shorter)


# ===================== v3.6.0 兼容接口 =====================


def decay_patterns(now_date: Optional[date] = None) -> dict:
    """
    [兼容 v3.6.0] 每周对行为模式执行置信度衰减。

    内部调用 run_decay() 并返回兼容格式。
    """
    result = run_decay(now_date)
    return {
        "removed": result["archived"],
        "decayed": result["decayed"],
        "kept": result["kept"],
    }


def reject_pattern(content_substring: str) -> dict:
    """
    [兼容 v3.6.0] 用户拒绝某个规律时降低置信度。

    参数:
        content_substring: 匹配关键词

    返回:
        {"affected": int, "message": str}
    """
    data = _load_patterns_raw()
    patterns = data.get("patterns", [])

    affected = 0
    for p in patterns:
        if not isinstance(p, dict) or p.get("archived", False):
            continue

        match_fields = [p.get("content", ""), p.get("from", ""), p.get("to", "")]
        if any(content_substring in f for f in match_fields if f):
            p["rejected"] = True
            p["confidence"] = round(p.get("confidence", 0.5) - 0.30, 2)
            if p["confidence"] < 0:
                p["confidence"] = 0.0
            p["last_trigger"] = date.today().isoformat()
            affected += 1

    _save_patterns_raw(data)

    if affected == 0:
        return {"affected": 0, "message": f"没有找到与「{content_substring}」匹配的规律"}
    return {"affected": affected, "message": f"已标记 {affected} 条规律为「已拒绝」(置信度 -0.30)"}


def boost_pattern(content_substring: str) -> dict:
    """
    [兼容 v3.6.0] 用户确认某个规律时提升置信度。

    参数:
        content_substring: 匹配关键词

    返回:
        {"affected": int, "message": str}
    """
    data = _load_patterns_raw()
    patterns = data.get("patterns", [])

    affected = 0
    today_str = date.today().isoformat()
    for p in patterns:
        if not isinstance(p, dict) or p.get("archived", False):
            continue

        match_fields = [p.get("content", ""), p.get("from", ""), p.get("to", "")]
        if any(content_substring in f for f in match_fields if f):
            p["confidence"] = min(0.95, p.get("confidence", 0.5) + 0.10)
            p["last_trigger"] = today_str
            p["rejected"] = False
            affected += 1

    _save_patterns_raw(data)

    if affected == 0:
        return {"affected": 0, "message": f"没有找到与「{content_substring}」匹配的规律"}
    return {"affected": affected, "message": f"已提升 {affected} 条规律的置信度 (+0.10)"}


# ===================== 启动时自动初始化 =====================

# 模块导入时自动执行数据迁移
_migrate_if_needed()


# ===================== 独立测试入口 =====================
if __name__ == "__main__":
    print("🧪 记忆引擎 v3.7.0 测试")
    print(f"   数据目录: {BASE_DIR}")
    print()

    # 测试 stats
    stats = get_memory_stats()
    print(f"📊 记忆统计: facts={stats['facts_count']}, "
          f"patterns={stats['patterns_count']} "
          f"(active={stats['active_patterns']}, archived={stats['archived_patterns']})")

    # 测试 add_fact
    r = add_fact("用户喜欢喝冰美式咖啡", category="偏好", pinned=True, importance=0.8)
    print(f"📝 add_fact: {r}")

    # 测试 add_pattern
    r = add_pattern("每天上午泡咖啡", pattern_type="time_based", confidence=0.6)
    print(f"📝 add_pattern: {r}")

    # 测试 get_pinned_facts
    facts = get_pinned_facts(limit=5)
    print(f"📌 Pinned facts: {len(facts)}")
    for f in facts:
        print(f"   - [{f['id']}] {f['content']} (importance={f['importance']})")

    # 测试 get_high_confidence_patterns
    patterns = get_high_confidence_patterns(min_confidence=0.5, limit=5)
    print(f"🔍 High-confidence patterns: {len(patterns)}")
    for p_item in patterns:
        print(f"   - [{p_item['id']}] {p_item['content']} (confidence={p_item['confidence']})")

    # 测试注入上下文构建
    ctx = build_injection_context(user_input="提醒我泡咖啡")
    print(f"\n💉 注入上下文 ({len(ctx)} 字符):")
    print(ctx[:500])

    # 测试衰减
    result = run_decay()
    print(f"\n🧹 衰减结果: {result}")
