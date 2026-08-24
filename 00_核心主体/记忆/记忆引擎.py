"""
记忆引擎 — 晓风Agent v3.7.0（v3.11.0 新增 Phase 5 对比分析；v3.12.0 新增 Phase 6 情绪检测与记忆关联；v3.13.0 新增 Phase 7 增强对比分析）
======================================================================================================
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
VALID_MEMORY_SUBTYPES = {"event", "fact", "habit", "reflection", "emotion"}  # v3.12.1: emotion 情绪记忆

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


# ===================== 标签自动生成（v3.10.7, Phase 3）=====================

# 类型标签规则：判定记录所属功能域
_TYPE_TAG_RULES = [
    (["记账", "花了", "消费", "付款", "支出", "买了", "财务", "账单",
      "元", "块", "块钱", "收入", "开销"], "财务"),
    (["日程", "会议", "开会", "提醒", "待办", "任务", "事件", "安排", "约会",
      "聚会", "面试", "上课", "活动", "闹钟", "叫我"], "日程"),
    (["记住", "记下", "回忆", "回顾", "搜索", "记忆", "忘了", "记得"], "记忆"),
]

# 实体标签规则：常见物品/话题 → 实体标签（更具体，优先于类型标签）
_ENTITY_TAG_RULES = [
    (["咖啡", "咖啡豆"], "咖啡"),
    (["奶茶", "饮料", "果汁", "可乐", "啤酒", "红酒", "酒"], "饮品"),
    (["吃饭", "午餐", "晚餐", "早餐", "外卖", "食堂", "吃了"], "吃饭"),
    (["会议", "开会"], "会议"),
    (["健身", "跑步", "锻炼", "运动", "瑜伽", "散步"], "运动"),
    (["电影", "电视剧", "追剧", "视频", "综艺", "动画"], "娱乐"),
    (["书", "阅读", "读书", "小说", "漫画", "看书"], "阅读"),
    (["心情", "开心", "难过", "高兴", "郁闷", "烦躁", "焦虑", "生气", "累"], "情绪"),
    (["购物", "网购", "购买", "淘宝", "京东", "下单", "买"], "购物"),
    (["水", "喝水", "矿泉水"], "喝水"),
]


def _extract_tags(text, extra_text=None, limit=5):
    """
    自动提取关键词标签（Phase 3 标签生成）。

    标签来源（按优先级）:
      1. 实体标签: 咖啡/会议/吃饭/运动 等（实体规则表，更具体）
      2. 类型标签: 财务/日程/记忆/聊天（类型规则表，聊天为兜底）

    去重，按 实体→类型 排序，最多返回 limit 个。无任何命中 → 兜底「聊天」。
    """
    if not text:
        return []
    combined = str(text)
    if extra_text:
        combined = combined + " " + str(extra_text)

    tags = []
    # 1) 实体标签（更具体，优先）
    for kws, tag in _ENTITY_TAG_RULES:
        if any(kw in combined for kw in kws) and tag not in tags:
            tags.append(tag)
    # 2) 类型标签
    for kws, tag in _TYPE_TAG_RULES:
        if any(kw in combined for kw in kws) and tag not in tags:
            tags.append(tag)
    # 3) 聊天兜底：未命中任何领域类型 → 聊天
    has_type = any(t in ("财务", "日程", "记忆") for t in tags)
    if not has_type and "聊天" not in tags:
        tags.append("聊天")
    return tags[:limit]


def _merge_tags(provided, auto, limit=5):
    """合并标签：保留传入标签顺序，追加自动标签（去重），上限 limit 个。"""
    out = []
    for t in list(provided or []):
        if t and t not in out:
            out.append(t)
    for t in list(auto or []):
        if t and t not in out:
            out.append(t)
    return out[:limit]


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
    # v3.13.11: 支持上个月（时间回查"上个月"使用）
    if time_filter in ("last_month", "上个月"):
        if now.month == 1:
            start = f"{now.year - 1}-12-01"
            end = f"{now.year - 1}-12-31"
        else:
            start = f"{now.year}-{now.month - 1:02d}-01"
            end = (datetime(now.year, now.month, 1) - timedelta(days=1)).strftime("%Y-%m-%d")
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
    # v3.10.7: 无显式标签时自动生成（类别 + 内容关键词）
    tags = f.get("tags") or []
    if not tags:
        cat_tags = [f.get("category")] if f.get("category") and f.get("category") != "其他" else []
        tags = _merge_tags(cat_tags, _extract_tags(content, f.get("title")))
    record = {
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
        "tags": tags,
        "importance": importance,
        "created_at": created or now.isoformat(),
        "updated_at": f.get("updated_at"),
        "category": f.get("category"),
        "pinned": f.get("pinned", False),
        "source": f.get("source"),
        "embedding": f.get("embedding"),
    }
    _init_memory_lifecycle(record)  # v3.10.11: 创建即补齐生命周期字段
    return record


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
    record = {
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
    _init_memory_lifecycle(record)  # v3.10.11: 创建即补齐生命周期字段
    return record


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
    # v3.10.7: 自动标签生成——传入标签与内容关键词合并（去重、上限 5）
    tags = _merge_tags(tags, _extract_tags(content, title))
    # v3.12.0: Phase 6 情绪自动检测——未显式传入 mood 时，从 内容+标题 检测
    if mood is None:
        mood = detect_mood(f"{content} {title}")

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
        "tags": tags,
        "importance": importance,
        "created_at": now.isoformat(),
        "updated_at": None,
    }
    # v3.10.11: 创建即补齐生命周期字段（confidence/tier/retrieved_count/last_retrieved），
    # 保证每条新记忆都带置信度与检索计数——置信度机制对所有记录一致生效，
    # 不再依赖后续 import 时的 backfill_memory_lifecycle 兜底。
    _init_memory_lifecycle(record)

    data = _load_memory_raw()
    data.setdefault("memories", []).append(record)
    _save_memory_raw(data)
    logger.info(f"记忆条目已记录: [{record['id']}] {title[:30]}... (subtype={subtype})")
    # v3.12.0: Phase 6 记忆关联——创建后立即建立关联（失败不阻塞主流程）
    try:
        related = find_related_memories(record, limit=5)
        if related:
            link_memories(record["id"], related)
    except Exception as e:
        logger.warning(f"记忆关联失败: {e}")
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
                        related_memory_ids=None, is_tool_round=False) -> dict:
    """
    新增一条对话归档记录（v3.10.0，原始对话 + 摘要）。

    参数:
        role: 'user' / 'assistant'
        original_text: 原始对话文本
        summary: 1-2 句摘要（工具轮由规则生成，纯对话由 3b 轻量生成）
        tags: 关键词标签列表（可选）
        mood: 情绪标签（可选，Phase 6 自动检测）
        related_memory_ids: 关联记忆 ID 列表（可选，指向 memory.json 条目）
        is_tool_round: 是否为工具轮（v3.12.1）——工具轮不创建情绪记忆，
                      避免"搜索开心"等工具指令被误记为情绪表达

    返回:
        {"status": "added"|"skipped", "id": str, "message": str}
    """
    if not original_text or not str(original_text).strip():
        return {"status": "skipped", "id": "", "message": "原始文本为空，跳过"}
    text = str(original_text).strip()
    now = datetime.now()
    # v3.12.0: Phase 6 用户轮自动情绪检测（assistant 轮不检测，避免噪音）
    if role == "user" and mood is None:
        mood = detect_mood(text)
    # v3.12.1: 纯对话轮 + 强情绪 → 自动创建情绪记忆（memory.json 可检索）
    if (role == "user" and not is_tool_round and mood in _STRONG_MOODS):
        _create_emotion_memory(text, mood)
    # v3.10.7: 自动标签生成——传入标签与 原文+摘要 关键词合并（去重、上限 5）
    merged_tags = _merge_tags(tags, _extract_tags(text, summary))
    record = {
        "id": _generate_dialogue_id(),
        "dialogue_id": _get_session_id(),
        "role": role if role in ("user", "assistant") else "user",
        "original_text": text,
        "summary": (summary or "").strip() or text[:20],
        "tags": merged_tags,
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


# ===================== Phase 3 搜索接口（v3.10.7）=====================

# 星期映射：中文星期 → weekday 序号（0=周一）
_WEEKDAY_MAP = {
    "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6, "周天": 6,
    "星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3, "星期五": 4, "星期六": 5, "星期日": 6, "星期天": 6,
}


def _resolve_date_range(date_range):
    """
    将时间查询词解析为 (start, end) 日期范围字符串（Phase 3 时间索引）。

    支持:
      - 相对词: 今天/昨日/本周/这周/上周/本月/这个月（复用 _date_range_for）
      - 星期: 周一~周日 / 星期一~星期日（本周对应日）；上周X / 上星期X（上周对应日）
      - 日期: YYYY-MM-DD（单日），或 (start, end) 元组
      - 特殊: None / "all" / "全部" → (None, None)（不过滤日期）

    返回: (start, end)；无法解析时返回 (None, None)。
    """
    if date_range is None:
        return None, None
    if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        return _normalize_date(date_range[0]), _normalize_date(date_range[1])
    s = str(date_range).strip()
    if s in ("all", "全部"):
        return None, None

    # 相对时间词（今天/昨天/本周/上周/本月）
    start, end = _date_range_for(s)
    if start:
        return start, end

    # 星期几：本周对应日；带"上"前缀 → 上周对应日
    m = re.match(r"^(上)?(周|星期)([一二三四五六日天])$", s)
    if m:
        wd = f"周{m.group(3)}"
        idx = _WEEKDAY_MAP.get(wd)
        if idx is not None:
            now = datetime.now()
            d = now - timedelta(days=now.weekday() - idx)
            if m.group(1):
                d = d - timedelta(days=7)
            ds = d.strftime("%Y-%m-%d")
            return ds, ds

    # 具体日期 YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s, s

    return None, None


def search_by_time(date_range, period=None, limit=10) -> list:
    """
    按时间范围 / 时段搜索记忆记录（Phase 3 时间索引）。

    参数:
        date_range: 相对时间词（今天/昨天/本周/上周/本月/上周三/周一）或 YYYY-MM-DD，
                    或 (start, end) 元组；None/"all"/"全部" 表示不过滤日期
        period: 时段标签（凌晨/早上/上午/中午/下午/晚上/深夜），可选
        limit: 最大返回条数

    返回:
        匹配的记忆记录列表（date 降序 + time 升序）。范围无法解析时返回空列表。
    """
    records = load_memories()
    if date_range is not None and str(date_range).strip() not in ("all", "全部"):
        start, end = _resolve_date_range(date_range)
        if start:
            records = [r for r in records if start <= (r.get("date") or "") <= end]
        else:
            return []  # 无法解析 → 空结果，不误返回全量
    if period:
        records = [r for r in records if r.get("period") == period]
    if limit:
        records = records[:limit]
    return records


def search_by_tags(tags, limit=10) -> list:
    """
    按标签搜索记忆记录（Phase 3 标签索引）。

    参数:
        tags: str 单个标签，或 list 多个标签（任一命中即匹配）
        limit: 最大返回条数

    返回:
        匹配的记忆记录列表（date 降序 + time 升序）。标签匹配采用子串包含（"咖啡"可命中"咖啡豆"）。
    """
    if not tags:
        return []
    tag_list = [tags] if isinstance(tags, str) else list(tags)
    tag_list = [t for t in tag_list if t]
    if not tag_list:
        return []
    records = load_memories()
    hits = []
    for r in records:
        rtags = r.get("tags") or []
        if any(any(t in (rt or "") for t in tag_list) for rt in rtags):
            hits.append(r)
    return hits[:limit]


def search_by_keyword(keyword, limit=10) -> list:
    """
    按关键词搜索记忆 + 对话归档（Phase 3 关键词索引）。

    匹配字段:
      - 记忆库: content / title / detail / tags
      - 对话归档: original_text / summary / tags

    参数:
        keyword: 搜索关键词
        limit: 最大返回条数

    返回:
        统一结构列表，每项 {source: memory|dialogue, id, date, title, content, tags, ...}，
        按日期降序，记忆优先于对话。
    """
    if not keyword or not str(keyword).strip():
        return []
    kw = str(keyword).strip()

    results = []
    # 1) 记忆库
    for m in load_memories():
        hay = " ".join(str(m.get(f) or "") for f in ("content", "title", "detail"))
        if kw in hay or any(kw in (t or "") for t in (m.get("tags") or [])):
            results.append({
                "source": "memory",
                "id": m.get("id"),
                "subtype": m.get("subtype"),
                "date": m.get("date"),
                "time": m.get("time"),
                "period": m.get("period"),
                "title": m.get("title"),
                "content": m.get("content"),
                "tags": m.get("tags", []),
                "timestamp": m.get("datetime") or m.get("created_at"),
            })
    # 2) 对话归档
    ddata = _load_dialogue_raw()
    drecs = sorted(ddata.get("records", []), key=_dialogue_sort_key, reverse=True)
    for r in drecs:
        # v3.10.9 / v3.13.9: 跳过搜索自噪音——工具操作摘要（"工具操作：搜索X"/"添加日程："/"记账："…）
        # 与助手确认回声（"确认：🔍 找到…"/"确认：✅ 已添加待办"/"确认：🧠 记忆记录…"）。
        # 否则每次搜索都会归档此类记录，污染搜索结果、淹没真正的内容记录。
        _s = (r.get("summary") or "").strip()
        if _s.startswith(_SEARCH_NOISE_PREFIXES):
            continue
        # v3.13.12/3.13.14: 跳过时间回查/记忆查询/展开对话摘要回声（归档污染搜索）
        if ("做了这些事" in _s or "的记忆记录" in _s or _s.startswith("找到 ")
                or "📖 你说" in _s or "📖 晓风说" in _s):
            continue
        hay = " ".join(str(r.get(f) or "") for f in ("original_text", "summary"))
        if kw in hay or any(kw in (t or "") for t in (r.get("tags") or [])):
            results.append({
                "source": "dialogue",
                "id": r.get("id"),
                "role": r.get("role"),
                "date": (r.get("timestamp") or "")[:10],
                "time": (r.get("timestamp") or "")[11:16],
                "title": r.get("summary"),
                "content": r.get("original_text"),
                "tags": r.get("tags", []),
                "timestamp": r.get("timestamp"),
            })

    # 排序：日期降序（最新在前）+ 记忆优先于对话
    def _key(x):
        return (-_date_to_ordinal(x.get("date") or ""), 0 if x.get("source") == "memory" else 1)

    results.sort(key=_key)
    return results[:limit]


# ===================== 检索答案格式化（v3.13.9, Fix A） =====================

# 搜索噪音前缀：工具操作摘要与助手确认回声（搜索/记忆查询/记账/日程 都会归档此类记录）
_SEARCH_NOISE_PREFIXES = (
    "工具操作：", "记账：", "添加日程：", "添加待办：", "记忆：", "日程：",
    "确认：🔍", "确认：🟢", "确认：⚪", "确认：🧠", "确认：✅", "确认：📋", "确认：📅",
)

# 通用操作回声：内容仅表述"添加待办/记账"而无具体信息（凌晨添加待办 / 记账）
_GENERIC_ECHO_RE = re.compile(
    r"^(?:凌晨|早上|上午|中午|下午|晚上|深夜)?\s*(?:添加|新增|创建)\s*(?:待办|日程|事件|任务)$|"
    r"^(?:凌晨|早上|上午|中午|下午|晚上|深夜)?\s*记账$"
)


def _display_text(record):
    """从记忆/搜索记录中选取最可读的展示文本（v3.13.9）。

    事实 → content；事件/其他 → 优先 detail（含具体信息，如"添加待办「买咖啡豆」"），
    再 content；通用操作回声（凌晨添加待办 / 记账）视为低信息量跳过 → 回退 title。
    """
    if not isinstance(record, dict):
        return ""
    subtype = record.get("subtype") or ""
    content = (record.get("content") or "").strip()
    detail = (record.get("detail") or "").strip()
    title = (record.get("title") or "").strip()
    if subtype == "fact":
        return content or detail or title
    if subtype == "emotion":
        # v3.13.12: 情绪记忆展示为「开心（今天心情很好）」
        mood = (record.get("mood") or "").strip()
        if mood and content and mood not in content:
            return f"{mood}（{content}）"
        return content or mood or detail or title
    for candidate in (detail, content):
        if candidate and not _GENERIC_ECHO_RE.match(candidate):
            return candidate
    return title or content or detail


def _memory_domain(record):
    """将搜索命中的记录归类到展示域：事实/财务/日程/待办/情绪/习惯/对话/其他。"""
    if not isinstance(record, dict):
        return "其他"
    if record.get("source") == "dialogue":
        return "对话"
    subtype = record.get("subtype") or ""
    if subtype == "fact":
        return "事实"
    if subtype == "emotion":
        return "情绪"
    if subtype == "habit":
        return "习惯"
    text = " ".join(str(record.get(f) or "") for f in ("title", "content", "detail"))
    if any(kw in text for kw in ("记账", "花费", "花销", "支出", "花了", "买了")):
        return "财务"
    if any(kw in text for kw in ("待办", "任务", "事项")):
        return "待办"
    if any(kw in text for kw in ("日程", "事件", "会议", "提醒")):
        return "日程"
    return "其他"


def format_keyword_search(keyword, hits):
    """将关键词搜索结果格式化为分组摘要（v3.13.9，回答生成层）。

    按 事实/财务/日程/待办/情绪/习惯/对话 分组，用 _display_text 展示每条的
    可读内容（而非泛化 title）。过滤两类噪音：
      - 记忆命中：通用回声（"记账"/"添加待办"无具体信息，仅命中标签）
      - 对话命中：长列表 dump（📅/📋 日程/待办整块回复）
    返回可直接展示的字符串。
    """
    kw = str(keyword or "").strip()
    if not hits:
        return f"没有找到与「{kw}」相关的记录～"

    # ---- 过滤低信息量命中 ----
    kept = []
    for h in hits:
        text = _display_text(h)
        if h.get("source") == "dialogue":
            content = (h.get("content") or "").strip()
            if len(content) > 80 or "📅" in content or "📋" in content or "要换个日期查查看" in content:
                continue  # 长列表/整块日程回复，非用户关心的实质内容
            if text:
                kept.append(h)
        else:
            if text and not _GENERIC_ECHO_RE.match(text):
                kept.append(h)

    if not kept:
        return f"「{kw}」相关的详细记录较少，暂时无法汇总具体内容～"

    grouped = {}
    for h in kept:
        grouped.setdefault(_memory_domain(h), []).append(h)
    order = ("事实", "财务", "日程", "待办", "情绪", "习惯", "对话", "其他")
    lines = [f"找到 {len(kept)} 条与「{kw}」相关的记录：", ""]
    for dom in order:
        recs = grouped.get(dom)
        if not recs:
            continue
        lines.append(f"[{dom}]")
        for r in recs[:5]:
            text = _display_text(r)
            date = r.get("date") or ""
            date_s = f" ({date})" if date and not re.search(r"20\d{2}-\d{2}-\d{2}", text) else ""
            lines.append(f"  {text}{date_s}")
        if len(recs) > 5:
            lines.append(f"  … 等 {len(recs)} 条")
    return "\n".join(lines)


def _sum_amounts(records):
    """从财务记忆记录的 detail/content 中解析并累加金额（"花费10元"→10）。"""
    total = 0.0
    for r in records:
        m = re.search(r"(\d+(?:\.\d+)?)\s*元", _display_text(r) or "")
        if m:
            total += float(m.group(1))
    return total


def format_time_recall(records, time_label):
    """将时间回查结果格式化为分组摘要（v3.13.10, Fix B）。

    按 财务/日程/待办/事实/情绪/其他 分组：
      - 财务：记账 N 笔，共 X 元（从 detail 解析金额）
      - 日程/待办：计数
      - 事实：计数 + 列出内容
    无记录时返回「{time_label}没有查到记录哦」。
    """
    if not records:
        return f"{time_label or '那段时间'}没有查到记录哦"

    grouped = {"财务": [], "日程": [], "待办": [], "事实": [], "情绪": [], "其他": []}
    for r in records:
        dom = _memory_domain(r)
        grouped.setdefault(dom, []).append(r)

    lines = [f"{time_label or '那段时间'}你做了这些事："]
    fin = grouped["财务"]
    if fin:
        total = _sum_amounts(fin)
        total_s = str(int(total)) if total == int(total) else str(total)
        lines.append("")
        lines.append(f"记账 {len(fin)} 笔，共 {total_s} 元" if total > 0 else f"记账 {len(fin)} 笔")
    sched = grouped["日程"]
    if sched:
        lines.append("")
        lines.append(f"添加日程 {len(sched)} 个")
    todo = grouped["待办"]
    if todo:
        lines.append("")
        lines.append(f"添加待办 {len(todo)} 个")
    facts = grouped["事实"]
    if facts:
        lines.append("")
        lines.append(f"事实：{len(facts)} 条")
        for f in facts[:5]:
            t = _display_text(f)
            if t:
                lines.append(f"  {t}")
    emo = grouped["情绪"]
    if emo:
        lines.append("")
        lines.append(f"情绪：{len(emo)} 条")
    other = grouped["其他"]
    if other:
        lines.append("")
        lines.append(f"其他记录 {len(other)} 条")
    return "\n".join(lines)


def _subtype_label(subtype):
    """记忆 subtype → 中文展示标签。"""
    return {"fact": "事实", "event": "事件", "emotion": "情绪", "habit": "习惯"}.get(subtype or "", "其他")


def _append_event_lines(recs, lines):
    """事件组内：财务记录合并为「记账N笔共X元」，其余（日程/待办）逐条展示。"""
    fin = [r for r in recs if _memory_domain(r) == "财务"]
    others = [r for r in recs if _memory_domain(r) != "财务"]
    if fin:
        total = _sum_amounts(fin)
        total_s = str(int(total)) if total == int(total) else str(total)
        lines.append("")
        lines.append(f"记账 {len(fin)} 笔，共 {total_s} 元" if total > 0 else f"记账 {len(fin)} 笔")
    shown = 0
    for r in others[:5]:
        t = _display_text(r)
        if t:
            lines.append("")
            lines.append(t)
            shown += 1
    if len(others) > 5:
        lines.append("")
        lines.append(f"  … 等 {len(others)} 条")


def format_memory_query(records, header="记忆记录", time_label=None):
    """将记忆查询结果格式化为按类型分组、可读的列表（v3.13.12, Fix C）。

    按 subtype 分组（事实/事件/情绪/习惯），每条用 _display_text 展示；
    事件组内财务记录合并为「记账N笔共X元」，其余逐条列出。
    无记录返回「{time_label}没有{header}哦」。

    参数:
        records: search_by_time 返回的记忆记录
        header: 标题名词（默认"记忆记录"）
        time_label: 时间标签（如"今天"），用于标题与无记录提示
    """
    if not records:
        if time_label:
            return f"{time_label}没有{header}哦"
        return f"没有{header}哦"
    title = f"{time_label}的{header}：" if time_label else f"{header}："
    lines = [title, ""]
    groups = {}
    for r in records:
        sub = _subtype_label(r.get("subtype") or r.get("type") or "")
        groups.setdefault(sub, []).append(r)
    order = ("事实", "事件", "情绪", "习惯", "其他")
    first_group = True
    for sub in order:
        recs = groups.get(sub)
        if not recs:
            continue
        if not first_group:
            lines.append("")  # 分组之间空行
        first_group = False
        lines.append(f"[{sub}] ({len(recs)}条)")
        if sub == "事件":
            _append_event_lines(recs, lines)
        else:
            for r in recs[:6]:
                t = _display_text(r)
                if t:
                    lines.append("")
                    lines.append(t)
            if len(recs) > 6:
                lines.append("")
                lines.append(f"  … 等 {len(recs)} 条")
    return "\n".join(lines)


def format_related_query(memory_id, query_text, limit=10):
    """将关联记忆查询格式化为按强度分组、可读的输出（v3.13.13, Fix D）。

    通过 memory_id 或按 query_text 搜索找到候选记忆，取各自关联，去重后：
      - 用 _display_text 展示内容（替代泛化 title）
      - 过滤通用回声 / 空展示
      - 按强度分组（强/中/弱），附领域标签与关联原因

    参数:
        memory_id: 指定候选记忆 ID（可为 None，此时按 query_text 搜索候选）
        query_text: 主题词（用于候选搜索与「X」标签）
        limit: 每个候选取关联数上限

    返回:
        可直接展示的字符串。
    """
    kw = str(query_text or "").strip()
    candidate_ids = []
    if memory_id:
        candidate_ids = [memory_id]
    else:
        try:
            cands = search_by_keyword(kw, limit=5)
            candidate_ids = [c["id"] for c in cands if c.get("source") == "memory"]
        except Exception:
            candidate_ids = []
    if not candidate_ids:
        # 主题为空 → 兜底取最近记忆做候选
        try:
            candidate_ids = [m["id"] for m in search_by_time(None, limit=8)]
        except Exception:
            candidate_ids = []

    rels = []
    seen = set()
    for mid in candidate_ids:
        try:
            for r in get_related_memories(mid, limit=limit or 10, live=True):
                rid = r.get("id")
                if rid in seen:
                    continue
                seen.add(rid)
                rels.append(r)
        except Exception:
            continue

    # 过滤通用回声 / 空展示
    kept = []
    for r in rels:
        text = _display_text(r)
        if text and not _GENERIC_ECHO_RE.match(text):
            kept.append(r)
    if not kept:
        return f"「{kw or '该记忆'}」暂时没有关联的记忆记录"

    # v3.10.8: 展示的关联记忆计入检索（提升置信度）
    for r in kept:
        if r.get("id"):
            try:
                record_retrieval(r["id"])
            except Exception:
                pass

    grouped = {"强": [], "中": [], "弱": []}
    for r in kept:
        grouped.setdefault(r.get("strength_label", "弱"), []).append(r)

    lines = [f"「{kw or '该记忆'}」相关的记忆有：", ""]
    first_group = True
    for st in ("强", "中", "弱"):
        recs = grouped.get(st)
        if not recs:
            continue
        if not first_group:
            lines.append("")
        first_group = False
        lines.append(f"[{st}关联] ({len(recs)}条)")
        for r in recs[:5]:
            text = _display_text(r)
            dom = _memory_domain(r)
            reasons = "、".join(r.get("reasons", []) or [])
            item = f"{text} ({dom})"
            if reasons:
                item += f" - 原因：{reasons}"
            lines.append("")
            lines.append(item)
        if len(recs) > 5:
            lines.append("")
            lines.append(f"  … 等 {len(recs)} 条")
    return "\n".join(lines)


# ===================== Phase 4 置信度衰减与记忆整合（v3.10.8）=====================

ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")
MEMORY_ARCHIVE_FILE = os.path.join(ARCHIVE_DIR, "memory_archive.json")

_DAILY_DECAY = 0.005          # 每日未检索衰减
_RETRIEVAL_BOOST = 0.05       # 每次检索提升
_PROMOTE_CONFIDENCE = 0.8     # 提升 L2 的置信度阈值
_TOPIC_FREQUENCY_THRESHOLD = 3  # 主题频次阈值
_ARCHIVE_CONFIDENCE = 0.2     # 归档置信度阈值
_ARCHIVE_DAYS = 90            # 归档未检索天数
_L2_TIER = "L2"
_L1_TIER = "L1"

# 主题频次判定只统计实体主题标签（咖啡/会议/吃饭…），排除类型/语义类别标签
# （财务/日程/记忆/聊天/事件/待办 等），避免"事件"类泛化标签误触发批量提升。
_TOPIC_TAGS = {tag for _, tag in _ENTITY_TAG_RULES}


def _init_memory_lifecycle(record):
    """为记忆记录补齐生命周期字段（幂等，additive）。

    - confidence: 缺省按 importance(1-10)/10 推导
    - tier: 缺省 L1（情景记忆）
    - last_retrieved: 缺省取 created_at 日期（避免创建即开始衰减）
    - retrieved_count: 缺省 0
    """
    if not isinstance(record, dict):
        return
    if record.get("confidence") is None:
        imp = record.get("importance") or 5
        record["confidence"] = round(max(0.0, min(1.0, imp / 10.0)), 3)
    if not record.get("tier"):
        record["tier"] = _L1_TIER
    if not record.get("last_retrieved"):
        created = (record.get("created_at") or record.get("datetime") or "")
        record["last_retrieved"] = (created[:10] if len(created) >= 10
                                    else date.today().isoformat())
    if record.get("retrieved_count") is None:
        record["retrieved_count"] = 0


def backfill_memory_lifecycle() -> int:
    """为历史记忆记录补齐生命周期字段（幂等，additive）。返回补齐条数。"""
    data = _load_memory_raw()
    changed = 0
    for m in data.get("memories", []):
        before = dict(m)
        _init_memory_lifecycle(m)
        if m != before:
            changed += 1
    if changed:
        _save_memory_raw(data)
        logger.info(f"记忆生命周期字段已补齐: {changed} 条")
    return changed


def decay_confidence(memory_id: str, delta=None) -> dict:
    """
    对单条记忆应用置信度调整（v3.10.8）。

    参数:
        memory_id: 记忆 ID
        delta: 手动调整量（正加负减，封顶 1.0，下限 0.0）；
               None → 默认日衰减 -0.005

    返回:
        {"status": "ok"|"not_found", "id", "confidence"}
    """
    data = _load_memory_raw()
    for m in data.get("memories", []):
        if m.get("id") == memory_id:
            _init_memory_lifecycle(m)
            d = _DAILY_DECAY if delta is None else delta
            conf = round(max(0.0, min(1.0, (m.get("confidence") or 0.5) + d)), 3)
            m["confidence"] = conf
            m["updated_at"] = datetime.now().isoformat()
            _save_memory_raw(data)
            return {"status": "ok", "id": memory_id, "confidence": conf}
    return {"status": "not_found", "id": memory_id}


def promote_to_l2(memory_id: str, reason=None) -> dict:
    """
    将记忆从 L1（情景）提升到 L2（语义长期）（v3.10.8）。

    - 设置 tier="L2"
    - confidence 保底至 0.8
    - 记录提升原因与时间

    返回:
        {"status": "promoted"|"not_found", "id", "tier", "confidence"}
    """
    data = _load_memory_raw()
    for m in data.get("memories", []):
        if m.get("id") == memory_id:
            _init_memory_lifecycle(m)
            m["tier"] = _L2_TIER
            m["confidence"] = round(max(m.get("confidence") or 0.0, _PROMOTE_CONFIDENCE), 3)
            m["promotion_reason"] = reason or "confidence≥0.8 或主题频次达标"
            m["promoted_at"] = datetime.now().isoformat()
            m["updated_at"] = datetime.now().isoformat()
            _save_memory_raw(data)
            return {"status": "promoted", "id": memory_id, "tier": _L2_TIER,
                    "confidence": m["confidence"]}
    return {"status": "not_found", "id": memory_id}


def _resolve_now(now_date):
    """将可选 now_date 解析为 datetime（支持 date/datetime/str）。"""
    if now_date is None:
        return datetime.now()
    if isinstance(now_date, datetime):
        return now_date
    if isinstance(now_date, date):
        return datetime(now_date.year, now_date.month, now_date.day)
    return datetime.strptime(str(now_date)[:10], "%Y-%m-%d")


def _apply_daily_decay(memories, now, last_run):
    """
    增量式日衰减（v3.10.8）。

    规则: 每经过一天、记录未检索，confidence -= 0.005。
    last_run 为上次维护日期；本段期间内被检索过的记录只对"检索后的未检索天数"衰减。
    返回衰减条数（原地修改 memories）。
    """
    if last_run is None:
        return 0
    days_elapsed = (now.date() - last_run.date()).days
    if days_elapsed <= 0:
        return 0
    decayed = 0
    for m in memories:
        _init_memory_lifecycle(m)
        lr = (m.get("last_retrieved") or "")[:10]
        if lr:
            try:
                unretrieved = (now.date() - datetime.strptime(lr, "%Y-%m-%d").date()).days
            except ValueError:
                unretrieved = days_elapsed
        else:
            unretrieved = days_elapsed
        decay_days = min(days_elapsed, max(0, unretrieved))
        if decay_days > 0:
            conf = max(0.0, (m.get("confidence") or 0.5) - _DAILY_DECAY * decay_days)
            m["confidence"] = round(conf, 3)
            decayed += 1
    return decayed


def _promotion_pass(memories, now):
    """
    检查 L1 记忆提升条件（v3.10.8）:
      1) confidence ≥ 0.8 → L2
      2) 同一实体主题标签出现 ≥ 3 次 → L2
    返回提升条数（原地修改 memories）。
    """
    promoted = 0
    # 实体主题频次统计（仅统计非 L2 记忆）
    tag_count = {}
    for m in memories:
        if m.get("tier") == _L2_TIER:
            continue
        for t in (m.get("tags") or []):
            if t in _TOPIC_TAGS:
                tag_count[t] = tag_count.get(t, 0) + 1

    for m in memories:
        if m.get("tier") == _L2_TIER:
            continue
        _init_memory_lifecycle(m)
        conf = m.get("confidence") or 0.0
        if conf >= _PROMOTE_CONFIDENCE:
            m["tier"] = _L2_TIER
            m["promotion_reason"] = "confidence≥0.8"
            m["promoted_at"] = now.isoformat()
            m["updated_at"] = now.isoformat()
            promoted += 1
            continue
        topic_hits = sorted({t for t in (m.get("tags") or [])
                             if tag_count.get(t, 0) >= _TOPIC_FREQUENCY_THRESHOLD})
        if topic_hits:
            m["tier"] = _L2_TIER
            m["promotion_reason"] = f"主题频次≥{_TOPIC_FREQUENCY_THRESHOLD}: {'/'.join(topic_hits[:3])}"
            m["promoted_at"] = now.isoformat()
            m["updated_at"] = now.isoformat()
            promoted += 1
    return promoted


def run_consolidation(now_date=None) -> dict:
    """
    记忆整合（v3.10.8）: 日衰减 + L1→L2 提升。

    参数:
        now_date: 测试用当前日期（date/datetime/str），默认今天

    返回:
        {"checked", "decayed", "promoted", "last_run"}
    """
    now = _resolve_now(now_date)
    data = _load_memory_raw()
    memories = data.get("memories", [])
    if not memories:
        return {"checked": 0, "decayed": 0, "promoted": 0, "last_run": now.date().isoformat()}

    for m in memories:
        _init_memory_lifecycle(m)

    last_run = None
    if data.get("last_decay_run"):
        try:
            last_run = datetime.strptime(str(data["last_decay_run"])[:10], "%Y-%m-%d")
        except ValueError:
            last_run = None
    decayed = _apply_daily_decay(memories, now, last_run)
    promoted = _promotion_pass(memories, now)

    data["last_decay_run"] = now.date().isoformat()
    data["updated_at"] = datetime.now().isoformat()
    _save_memory_raw(data)
    return {"checked": len(memories), "decayed": decayed, "promoted": promoted,
            "last_run": now.date().isoformat()}


def _append_memory_archive(records):
    """将归档记录追加写入 archive/memory_archive.json（按 id 去重）。"""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    existing = []
    try:
        with open(MEMORY_ARCHIVE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            existing = d.get("archived", []) if isinstance(d, dict) else []
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    existing_ids = {a.get("id") for a in existing}
    for r in records:
        if r.get("id") not in existing_ids:
            existing.append(r)
    with open(MEMORY_ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump({"version": "1.0", "archived_count": len(existing),
                   "last_updated": datetime.now().isoformat(), "archived": existing},
                  f, ensure_ascii=False, indent=2)


def run_archive(now_date=None) -> dict:
    """
    归档低置信度 / 长期未检索记忆（v3.10.8）。

    条件:
      - confidence < 0.2
      - 距 last_retrieved 超过 90 天
    满足任一 → 移入 archive/memory_archive.json，从 memory.json 移除。

    参数:
        now_date: 测试用当前日期，默认今天

    返回:
        {"checked", "archived", "skipped"}
    """
    now = _resolve_now(now_date)
    data = _load_memory_raw()
    memories = data.get("memories", [])
    keep = []
    to_archive = []
    today = now.date()
    for m in memories:
        _init_memory_lifecycle(m)
        conf = m.get("confidence") or 0.0
        lr = (m.get("last_retrieved") or "")[:10]
        days = 999
        if lr:
            try:
                days = (today - datetime.strptime(lr, "%Y-%m-%d").date()).days
            except ValueError:
                pass
        if conf < _ARCHIVE_CONFIDENCE or days > _ARCHIVE_DAYS:
            m["archived_at"] = now.isoformat()
            m["archive_reason"] = ("confidence<0.2" if conf < _ARCHIVE_CONFIDENCE
                                   else f"{days}天未检索")
            to_archive.append(m)
        else:
            keep.append(m)

    if to_archive:
        _append_memory_archive(to_archive)
    data["memories"] = keep
    data["updated_at"] = datetime.now().isoformat()
    _save_memory_raw(data)
    logger.info(f"记忆归档: {len(to_archive)} 条移入 archive/memory_archive.json, 保留 {len(keep)} 条")
    return {"checked": len(memories), "archived": len(to_archive), "skipped": len(keep)}


def record_retrieval(memory_id: str) -> dict:
    """
    记录一次记忆检索（v3.10.8 检索提升）:
    retrieved_count +1，confidence +0.05（封顶 1.0），last_retrieved 更新为今天。

    返回:
        {"status": "ok"|"not_found", "id", "confidence"}
    """
    data = _load_memory_raw()
    for m in data.get("memories", []):
        if m.get("id") == memory_id:
            _init_memory_lifecycle(m)
            m["retrieved_count"] = (m.get("retrieved_count") or 0) + 1
            m["confidence"] = round(min(1.0, (m.get("confidence") or 0.5) + _RETRIEVAL_BOOST), 3)
            m["last_retrieved"] = datetime.now().strftime("%Y-%m-%d")
            m["updated_at"] = datetime.now().isoformat()
            _save_memory_raw(data)
            return {"status": "ok", "id": memory_id, "confidence": m["confidence"]}
    return {"status": "not_found", "id": memory_id}


# ===================== 渐进披露上下文（v3.10.2）=====================

_USER_TOOL_PREFIXES = ("记账：", "添加日程：", "记忆：", "日程：", "工具操作：")


def get_context_summaries(limit: int = 3) -> list:
    """
    返回最近 N 轮对话摘要（Phase 2 渐进披露：上下文只注入摘要，不注入全量记忆）。

    每项: {id, role, summary, timestamp}，最新在前。
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
            "timestamp": r.get("timestamp"),
        })
    return out


def get_recent_tool_ops(limit: int = 3) -> list:
    """
    返回最近 N 条工具操作摘要（从对话归档中识别工具轮，Phase 2 上下文注入用）。

    判定：role=user 且 summary 以工具摘要前缀开头（记账：/添加日程：/记忆：/日程：/工具操作：）。

    每项: {id, summary, timestamp}，最新在前。
    """
    data = _load_dialogue_raw()
    records = data.get("records", [])
    records.sort(key=_dialogue_sort_key, reverse=True)
    out = []
    for r in records:
        if r.get("role") != "user":
            continue
        s = (r.get("summary") or "").strip()
        if s.startswith(_USER_TOOL_PREFIXES):
            out.append({
                "id": r.get("id"),
                "summary": s,
                "timestamp": r.get("timestamp"),
            })
        if len(out) >= limit:
            break
    return out


def get_user_name() -> str:
    """从记忆库检索用户名字（"我叫XX"/"我的名字是XX"/"我是XX"），未找到返回空串。

    注意：content 与 title 分开匹配，避免拼接成"我叫霖我叫霖"导致贪婪捕获"霖我叫霖"。
    """
    try:
        for m in load_memories():
            for field in ("content", "title"):
                c = m.get(field) or ""
                for pat in (r"我叫([一-鿿]{1,4})", r"我的名字(?:是|叫)?([一-鿿]{1,4})",
                            r"(?:我是|我是叫)([一-鿿]{1,4})"):
                    mm = re.search(pat, c)
                    if mm:
                        return mm.group(1).strip()
    except Exception:
        pass
    return ""


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


# ===================== 标签补齐（v3.10.7）=====================


def backfill_tags() -> dict:
    """
    为历史记录补齐标签（Phase 3 标签生成，幂等）。

    扫描 memory.json 与 对话归档.json，为 tags 为空的记录自动生成标签。
    只填写空标签，不改动已有标签。

    返回:
        {"memory": 补齐条数, "dialogue": 补齐条数}
    """
    mem_filled = 0
    mdata = _load_memory_raw()
    for m in mdata.get("memories", []):
        if not m.get("tags"):
            m["tags"] = _extract_tags(m.get("content") or "", m.get("title"))
            mem_filled += 1
    if mem_filled:
        _save_memory_raw(mdata)

    dia_filled = 0
    ddata = _load_dialogue_raw()
    for r in ddata.get("records", []):
        if not r.get("tags"):
            r["tags"] = _extract_tags(r.get("original_text") or "", r.get("summary"))
            dia_filled += 1
    if dia_filled:
        _save_dialogue_raw(ddata)

    if mem_filled or dia_filled:
        logger.info(f"标签补齐完成: memory {mem_filled} 条, dialogue {dia_filled} 条")
    return {"memory": mem_filled, "dialogue": dia_filled}


# ===================== 每日记忆维护（v3.10.8）=====================


def _maybe_run_daily_maintenance():
    """启动时每日记忆维护（v3.10.8）：按日期幂等，每天只执行一次整合 + 归档。

    依赖 memory.json 元数据 last_maintenance 记录上次维护日期；
    同一天内重复导入不重复执行（避免每次启动都跑一遍）。
    """
    try:
        data = _load_memory_raw()
        last = data.get("last_maintenance")
        today = date.today().isoformat()
        if last == today:
            return
        run_consolidation()
        run_archive()
        build_association_network()  # v3.12.0: Phase 6 每日补全记忆关联网络
        d = _load_memory_raw()
        d["last_maintenance"] = today
        d["updated_at"] = datetime.now().isoformat()
        _save_memory_raw(d)
        logger.info(f"每日记忆维护完成: consolidation + archive + 关联网络 ({today})")
    except Exception as e:
        logger.warning(f"每日记忆维护失败: {e}")


# ===================== Phase 6 情绪检测 + 记忆关联（v3.12.0）=====================

# ---- 情绪检测 ----

# 情绪关键词映射：9 类情绪 → 触发关键词（关键词优先，命中即返回）
_MOOD_KEYWORDS = {
    "开心": ["开心", "高兴", "快乐", "棒", "好心情", "心情好", "心情很好", "心情不错",
             "太好了", "太棒了", "真棒", "爽", "哈哈", "嘿嘿", "笑死"],
    "难过": ["难过", "伤心", "哭", "失落", "难受", "沮丧", "心酸", "想哭", "不开心",
             "伤心死了", "难过死了"],
    "生气": ["生气", "愤怒", "烦", "受不了", "气死", "恼火", "烦躁", "火大", "烦死",
             "气人", "真气人"],
    "焦虑": ["焦虑", "担心", "紧张", "慌", "压力", "压力大", "睡不着", "失眠", "害怕",
             "不安", "担忧", "好担心"],
    "平静": ["平静", "放松", "舒服", "安心", "惬意", "轻松", "悠闲", "舒心"],
    "兴奋": ["兴奋", "激动", "期待", "迫不及待", "超期待", "太兴奋", "好激动",
             "太期待了"],
    "疲惫": ["累", "困", "疲惫", "没精神", "精疲力尽", "好累", "累死", "疲倦",
             "困死", "乏"],
    "专注": ["专注", "投入", "认真", "沉浸", "专心"],
    "困惑": ["困惑", "不懂", "奇怪", "迷茫", "疑惑", "搞不明白", "想不通", "什么鬼",
             "咋回事", "怎么回事"],
}
_MOOD_VALID = set(_MOOD_KEYWORDS.keys())  # 9 类合法情绪

# 情绪信号词：无直接关键词但暗示情绪起伏时，值得用 LLM 兜底判断。
# 刻意排除「今天/最近」等高频中性词，避免对每条记录都触发 LLM、拖慢主循环。
_MOOD_SIGNAL_WORDS = ["被骂", "被夸", "被", "失败", "成功", "崩溃", "糟了", "完蛋",
                      "终于", "居然", "竟然", "吓", "哭", "烦", "气", "累", "困",
                      "失眠", "受打击", "想死", "难过", "开心死了", "吓死", "不对劲",
                      "麻烦", "倒霉", "糟糕", "差点"]

_MOOD_LLM_HOOK = None  # 模块级 LLM 钩子（脑.py 启动时注入 _call_ollama_3b）


def set_mood_llm_fn(fn):
    """注入情绪检测 LLM 钩子（v3.12.0, Phase 6）。

    脑.py 启动时将 _call_ollama_3b 注入，使 detect_mood 在关键词未命中时可
    用 LLM 兜底分类。未设置时 detect_mood 保持纯关键词检测（引擎可独立运行）。
    """
    global _MOOD_LLM_HOOK
    _MOOD_LLM_HOOK = fn


def detect_mood(text, llm_fn=None):
    """
    从文本检测用户情绪（v3.12.0, Phase 6, hybrid）。

    策略:
      1. 关键词优先：命中任一情绪关键词 → 直接返回该情绪
      2. LLM 兜底：无关键词命中、但文本含情绪信号词且 LLM 可用
         （llm_fn 或模块级钩子）→ 调 LLM 分类（2s 超时）
      3. 都无 → 返回 None

    参数:
        text: 待检测文本
        llm_fn: 可选 LLM 调用函数（缺省用 set_mood_llm_fn 注入的钩子）

    返回:
        9 类情绪之一（开心/难过/生气/焦虑/平静/兴奋/疲惫/专注/困惑），无命中返回 None。
    """
    if not text or not str(text).strip():
        return None
    t = str(text).strip()

    # 1) 关键词优先：命中直接返回（首个命中的情绪）
    for mood, kws in _MOOD_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return mood

    # 2) LLM 兜底：仅当文本含情绪信号词且 LLM 可用（避免对每条记录都调 LLM）
    fn = llm_fn or _MOOD_LLM_HOOK
    if fn is None:
        return None
    if not any(sig in t for sig in _MOOD_SIGNAL_WORDS):
        return None

    prompt = (
        "判断这句话中说话人的情绪，只从以下类别选一个，只输出类别名：\n"
        "开心/难过/生气/焦虑/平静/兴奋/疲惫/专注/困惑\n"
        "如果不确定或没有明显情绪，输出：无\n\n"
        f"句子：{t}\n情绪："
    )
    try:
        resp = (fn(prompt) or "").strip()
    except Exception:
        return None
    for mood in _MOOD_VALID:
        if mood in resp:
            return mood
    return None


# 强情绪集合（v3.12.1）：仅这些情绪自动创建情绪记忆；平静/疲惫/专注/困惑 等弱情绪不建记忆
_STRONG_MOODS = {"开心", "难过", "焦虑", "生气", "兴奋"}

# 情绪描述提取：去掉常见时间/主语前缀，取首个分句，作为情绪记忆的 content
_EMO_TIME_PREFIXES = ("这几天", "这两天", "这阵子", "最近", "今天", "昨天", "前天",
                      "现在", "刚刚", "刚才", "我", "我们")
_EMO_SEPARATORS = ("。", "，", ",", "！", "！", "？", "?", "；", ";")


def _extract_emotion_content(text):
    """从用户输入提取情绪描述短句（去时间/主语前缀 + 取首个分句），作情绪记忆 content。"""
    t = str(text or "").strip()
    # 1) 循环去除开头的 时间/主语 前缀（最长优先）
    while t:
        hit = False
        for p in sorted(_EMO_TIME_PREFIXES, key=len, reverse=True):
            if t.startswith(p):
                t = t[len(p):].strip()
                hit = True
                break
        if not hit:
            break
    # 2) 取首个分句
    for sep in _EMO_SEPARATORS:
        if sep in t:
            t = t.split(sep)[0]
            break
    return (t or str(text or "").strip())[:24].strip()


def _create_emotion_memory(text, mood):
    """为强情绪创建情绪记忆（v3.12.1）。失败不阻塞对话归档流程。"""
    try:
        content = _extract_emotion_content(text)
        add_memory_event(
            title="情绪",
            content=content,
            detail=str(text).strip(),
            mood=mood,
            tags=["情绪", mood],
            subtype="emotion",
        )
        logger.info(f"情绪记忆已创建: mood={mood} content={content[:20]}...")
    except Exception as e:
        logger.warning(f"情绪记忆创建失败: {e}")


# ---- 记忆关联（知识图谱）----

_ASSOC_TIME_WINDOW = 3600          # 时间临近窗口：1 小时（秒）
_STRONG_STRENGTH = 1.0             # 强关联：同实体 + 同标签 + 时间临近
_MEDIUM_STRENGTH = 0.7             # 中关联：同实体（部分条件）
_WEAK_STRENGTH = 0.4               # 弱关联：仅同标签 ≥2 或仅时间临近


def _mem_datetime_ts(record):
    """记忆记录 → 时间戳（秒）。解析 datetime 字段；失败返回 None。"""
    dt_str = record.get("datetime") or ""
    if not dt_str:
        dt_str = f"{record.get('date') or ''}T{record.get('time') or '12:00'}:00"
    try:
        return datetime.strptime(str(dt_str)[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
    except (ValueError, TypeError):
        return None


def _mem_entities(record):
    """记忆记录 → 实体主题标签（_ENTITY_TAG_RULES 命中的标签，排除类型/语义类别标签）。"""
    return [t for t in (record.get("tags") or []) if t in _TOPIC_TAGS]


def find_related_memories(new_memory, limit=5):
    """
    查找与新记忆相关的已有记忆（v3.12.0, Phase 6）。

    匹配维度:
      - 同实体: 实体主题标签重叠 ≥1（咖啡/会议/吃饭/运动…）
      - 同标签: 标签重叠 ≥2
      - 时间临近: datetime 相差 ≤1 小时

    关联强度（与规格一致）:
      - strong (1.0): 同实体 + 同标签 + 时间临近
      - medium (0.7): 同实体（含部分条件）
      - weak (0.4): 仅同标签 ≥2，或仅时间临近

    返回:
        [{"id", "strength", "reasons", "linked_at"}, ...] 按 strength 降序，最多 limit 个。
    """
    if not new_memory or not isinstance(new_memory, dict):
        return []
    new_id = new_memory.get("id")
    new_tags = {t for t in (new_memory.get("tags") or []) if t}
    new_entities = set(_mem_entities(new_memory))
    new_ts = _mem_datetime_ts(new_memory)

    results = []
    for m in load_memories():
        mid = m.get("id")
        if not mid or mid == new_id:
            continue
        m_tags = {t for t in (m.get("tags") or []) if t}
        m_entities = set(_mem_entities(m))
        shared_entities = new_entities & m_entities
        shared_tags = new_tags & m_tags

        reasons = []
        if shared_entities:
            reasons.append("同实体:" + "/".join(sorted(shared_entities)))
        if len(shared_tags) >= 2:
            reasons.append("同标签")
        m_ts = _mem_datetime_ts(m)
        time_close = False
        if new_ts is not None and m_ts is not None:
            time_close = abs(new_ts - m_ts) <= _ASSOC_TIME_WINDOW
        if time_close:
            reasons.append("时间临近")

        if not reasons:
            continue
        if shared_entities and len(shared_tags) >= 2 and time_close:
            strength = _STRONG_STRENGTH
        elif shared_entities:
            strength = _MEDIUM_STRENGTH
        else:
            strength = _WEAK_STRENGTH

        results.append({"id": mid, "strength": strength,
                        "reasons": reasons, "linked_at": datetime.now().isoformat()})

    results.sort(key=lambda x: x["strength"], reverse=True)
    return results[:limit]


def _merge_related_link(record, target_id, strength, reasons, now):
    """additive 合并单条关联到记录 related_ids（原地修改，不覆盖已有）。返回是否变更。"""
    links = record.setdefault("related_ids", [])
    for l in links:
        if l.get("id") == target_id:
            before = (l.get("strength"), tuple(l.get("reasons") or []))
            l["strength"] = round(max(l.get("strength", 0) or 0, strength), 2)
            l["reasons"] = list(dict.fromkeys((l.get("reasons") or []) + reasons))
            l["linked_at"] = now
            return before != (l["strength"], tuple(l["reasons"]))
    links.append({"id": target_id, "strength": round(strength, 2),
                  "reasons": list(dict.fromkeys(reasons)), "linked_at": now})
    return True


def link_memories(memory_id, related):
    """
    将关联列表写入记忆记录（v3.12.0, Phase 6, additive 双向）。

    对 memory_id 与其每个关联目标：在双方记录的 related_ids 中合并关联
    （不覆盖已有；同对已存在时 strength 取 max、reasons 合并去重）。

    参数:
        memory_id: 主记忆 ID
        related: find_related_memories 返回的关联列表

    返回:
        {"status": "ok"|"not_found", "linked": int, "updated": int}
    """
    if not related:
        return {"status": "ok", "linked": 0, "updated": 0}
    data = _load_memory_raw()
    by_id = {m.get("id"): m for m in data.get("memories", [])}
    if memory_id not in by_id:
        return {"status": "not_found", "linked": 0, "updated": 0}

    now = datetime.now().isoformat()
    updated = 0
    for rel in related:
        target_id = rel.get("id")
        if not target_id or target_id not in by_id:
            continue
        strength = rel.get("strength", _WEAK_STRENGTH)
        reasons = rel.get("reasons", [])
        changed = _merge_related_link(by_id[memory_id], target_id, strength, reasons, now)
        changed |= _merge_related_link(by_id[target_id], memory_id, strength, reasons, now)
        if changed:
            updated += 1

    data["updated_at"] = datetime.now().isoformat()
    _save_memory_raw(data)
    return {"status": "ok", "linked": updated, "updated": updated}


def build_association_network(limit_per=5):
    """
    批量重建记忆关联网络（v3.12.0, Phase 6）。

    遍历全部记忆，两两调用 find_related_memories + link_memories（幂等、additive）。
    用于每日维护，补全历史记忆的关联。

    参数:
        limit_per: 每条记忆最多关联条数

    返回:
        {"checked": int, "linked": int}
    """
    memories = load_memories()
    linked = 0
    for m in memories:
        try:
            related = find_related_memories(m, limit=limit_per)
            if related:
                r = link_memories(m.get("id"), related)
                linked += r.get("linked", 0)
        except Exception as e:
            logger.warning(f"关联网络构建单条失败: {e}")
    logger.info(f"关联网络构建完成: checked={len(memories)}, linked={linked}")
    return {"checked": len(memories), "linked": linked}


def _strength_label(strength):
    """关联强度数值 → 中文标签（强/中/弱）。"""
    try:
        s = float(strength)
    except (TypeError, ValueError):
        s = 0
    if s >= 0.9:
        return "强"
    if s >= 0.6:
        return "中"
    return "弱"


def get_related_memories(memory_id, limit=None, live=True):
    """
    查询某记忆的关联记忆（v3.12.0, Phase 6；v3.12.1 增强结构化返回；v3.12.2 实时兜底）。

    返回的每条关联记忆为原记录副本，并附加关联元数据:
      - strength: 关联强度数值（1.0/0.7/0.4）
      - strength_label: 强/中/弱
      - reasons: 关联原因列表（如 ["同实体:咖啡", "同标签"]）

    参数:
        memory_id: 记忆 ID
        limit: 最大返回条数
        live: v3.12.2 实时兜底——该记忆尚无已存储关联（related_ids 为空）时，
              调用 find_related_memories 实时计算，保证"XX和什么相关"总有结果
              （即使关联网络尚未批量构建）

    返回:
        按关联强度降序的关联记忆列表（含 content/detail/title + 关联元数据）；
        无关联或未找到返回 []。
    """
    data = _load_memory_raw()
    by_id = {m.get("id"): m for m in data.get("memories", [])}
    rec = by_id.get(memory_id)
    if not rec:
        return []
    links = rec.get("related_ids") or []
    if not links and live:
        # v3.12.2: 无已存储关联 → 实时计算兜底（不落库，仅本次查询生效）
        try:
            links = find_related_memories(rec, limit=limit or 5)
        except Exception:
            links = []
    links = sorted(links, key=lambda x: x.get("strength", 0), reverse=True)
    out = []
    for l in links:
        target = by_id.get(l.get("id"))
        if not target:
            continue
        enriched = dict(target)
        enriched["strength"] = l.get("strength", _WEAK_STRENGTH)
        enriched["strength_label"] = _strength_label(enriched["strength"])
        enriched["reasons"] = list(l.get("reasons", []) or [])
        out.append(enriched)
        if limit and len(out) >= limit:
            break
    return out


# ===================== 启动时自动初始化 =====================

# 模块导入时自动执行数据迁移 + 标签补齐 + 生命周期字段补齐（均幂等）
_migrate_if_needed()
backfill_tags()
backfill_memory_lifecycle()
_maybe_run_daily_maintenance()


# ===================== Phase 5 对比分析（v3.11.0）=====================

# 数据源路径：财务 / 日程（跨模块只读，不做写回）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
FINANCE_FILE = os.path.join(_PROJECT_ROOT, "01_工具模块", "财务模块", "local_archive.json")
SCHEDULE_EVENTS_FILE = os.path.join(_PROJECT_ROOT, "01_工具模块", "日程模块", "events.json")
SCHEDULE_TODOS_FILE = os.path.join(_PROJECT_ROOT, "01_工具模块", "日程模块", "todos.json")

# 时段显示顺序（凌晨→深夜）
_PERIOD_ORDER = ["凌晨", "早上", "上午", "中午", "下午", "晚上", "深夜"]

# 相对时间词 → 中文标签（用于回复措辞）
_PERIOD_LABEL_MAP = {
    "today": "今天", "yesterday": "昨天",
    "this_week": "本周", "last_week": "上周",
    "this_month": "本月", "last_month": "上月",
}

# 活动显示名：数据源里的原始词 → 用户友好的活动表述
_ACTIVITY_LABEL = {
    "咖啡": "喝咖啡", "奶茶": "喝奶茶", "饮品": "喝东西", "水": "喝水",
    "饭": "吃饭", "吃饭": "吃饭", "餐饮": "吃饭", "外卖": "点外卖",
    "会议": "开会", "运动": "运动", "跑步": "跑步", "健身": "健身",
    "购物": "购物", "买": "购物", "看电影": "看电影", "娱乐": "娱乐",
    "阅读": "阅读", "读书": "读书", "喝水": "喝水",
}

# 类别关键词 → 标准分类（与财务模块 _CATEGORY_RULES 一致，本地独立副本避免跨模块 import）
_CATEGORY_KW_MAP = [
    (["饭", "吃饭", "餐饮", "食堂", "外卖", "午餐", "晚餐", "早餐", "晚饭", "午饭"], "餐饮"),
    (["交通", "打车", "地铁", "公交", "停车", "高铁", "火车", "飞机", "车费", "车"], "交通"),
    (["咖啡", "奶茶", "饮料", "饮品", "喝"], "饮品"),
    (["购物", "超市", "商场", "网购", "淘宝", "京东", "买"], "购物"),
    (["娱乐", "电影", "游戏", "旅游", "门票", "KTV"], "娱乐"),
    (["房租", "物业", "水电", "电费", "水费", "网费", "燃气", "居住"], "居住"),
    (["医疗", "药", "医院", "体检", "诊所"], "医疗"),
    (["教育", "书", "课程", "培训", "学费", "读书"], "教育"),
    (["水果", "零食", "小吃", "面包", "甜品"], "食品"),
    (["话费", "流量", "宽带", "通讯"], "通讯"),
]


def _load_json_list(path):
    """加载 JSON 列表文件，文件缺失/损坏返回空列表。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _load_finance_records():
    """加载财务记录（local_archive.json 标准 15 字段格式）。"""
    return _load_json_list(FINANCE_FILE)


def _finance_in_range(records, start, end, types=("expense",)):
    """筛选指定日期范围 [start, end] 的财务记录（默认只算支出，income 不计入开销）。"""
    out = []
    for r in records:
        if types and (r.get("type") or "expense") not in types:
            continue
        d = (r.get("date") or "")[:10]
        if start and d < start:
            continue
        if end and d > end:
            continue
        out.append(r)
    return out


def _fmt_amount(x):
    """金额显示：整数去小数（350 → "350"，3.5 → "3.5"）。"""
    try:
        x = float(x)
    except (TypeError, ValueError):
        x = 0
    return str(int(x)) if x == int(x) else str(x)


def _percent_change(current, previous):
    """相比 previous 的变化百分比（%）。previous=0 时返回 None（无法计算）。"""
    if not previous:
        return None
    return round((current - previous) / previous * 100)


def _has_any(text, keywords):
    """text 中是否包含任一关键词。"""
    return any(kw in text for kw in keywords)


def _period_range(period):
    """将 period（相对词）解析为 (start, end)；无法解析时回退到本月范围。"""
    start, end = _resolve_date_range(period) if period else (None, None)
    if not start:
        start, end = _date_range_for("this_month")
    return start, end


def _period_cn(period):
    """相对词 → 中文标签；未知词显示原词。"""
    key = str(period or "").strip()
    if key in _PERIOD_LABEL_MAP:
        return _PERIOD_LABEL_MAP[key]
    if key in ("本周", "这周"):
        return "本周"
    if key in ("上周",):
        return "上周"
    if key in ("本月", "这个月"):
        return "本月"
    if key in ("上月", "上个月"):
        return "上月"
    return key or ""


def compare_week_over_week():
    """对比本周 vs 上周支出（Phase 5 功能 1）。

    返回 dict:
        {this_total, last_total, this_count, last_count, difference, percent, has_data}
    """
    start, end = _date_range_for("this_week")
    last_start, last_end = _date_range_for("last_week")
    records = _load_finance_records()
    this_week = _finance_in_range(records, start, end)
    last_week = _finance_in_range(records, last_start, last_end)
    this_total = sum(r.get("amount", 0) or 0 for r in this_week)
    last_total = sum(r.get("amount", 0) or 0 for r in last_week)
    return {
        "this_total": this_total, "last_total": last_total,
        "this_count": len(this_week), "last_count": len(last_week),
        "difference": this_total - last_total,
        "percent": _percent_change(this_total, last_total),
        "has_data": bool(this_week or last_week),
    }


def compare_month_over_month():
    """对比本月 vs 上月支出（Phase 5 功能 2）。

    返回 dict: {this_total, last_total, this_count, last_count, difference, percent, has_data}
    """
    start, end = _date_range_for("this_month")
    first_this = datetime.now().replace(day=1)
    last_month_end = first_this - timedelta(days=1)
    last_start = last_month_end.replace(day=1).strftime("%Y-%m-%d")
    last_end = last_month_end.strftime("%Y-%m-%d")
    records = _load_finance_records()
    this_month = _finance_in_range(records, start, end)
    last_month = _finance_in_range(records, last_start, last_end)
    this_total = sum(r.get("amount", 0) or 0 for r in this_month)
    last_total = sum(r.get("amount", 0) or 0 for r in last_month)
    return {
        "this_total": this_total, "last_total": last_total,
        "this_count": len(this_month), "last_count": len(last_month),
        "difference": this_total - last_total,
        "percent": _percent_change(this_total, last_total),
        "has_data": bool(this_month or last_month),
    }


def get_category_summary(period="this_month"):
    """按分类统计某时间段支出（Phase 5 功能 3）。

    参数:
        period: 相对时间词（本月/上周/本周…），默认本月

    返回 dict: {period, label, total, count, breakdown: [(分类, 金额), ...]}
    """
    start, end = _period_range(period)
    records = _finance_in_range(_load_finance_records(), start, end)
    by_cat = {}
    for r in records:
        cat = r.get("category") or "其他"
        by_cat[cat] = by_cat.get(cat, 0) + (r.get("amount", 0) or 0)
    breakdown = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)
    return {
        "period": period, "label": _period_cn(period),
        "total": sum(by_cat.values()), "count": len(records),
        "breakdown": breakdown,
    }


def compare_category_across_periods(category, base_period="this_month", compare_period="last_month"):
    """对比某分类在相邻两个时段的支出（Phase 5 功能 3 进阶）。

    参数:
        category: 分类名（餐饮/交通/饮品…）
        base_period: 基准时段（默认本月）
        compare_period: 对比时段（默认上月）

    返回 dict: {category, base_label, compare_label, base_total, compare_total, percent, has_data}
    """
    start, end = _period_range(base_period)
    c_start, c_end = _period_range(compare_period)
    records = _load_finance_records()
    base = [r for r in _finance_in_range(records, start, end)
            if (r.get("category") or "") == category]
    comp = [r for r in _finance_in_range(records, c_start, c_end)
            if (r.get("category") or "") == category]
    base_total = sum(r.get("amount", 0) or 0 for r in base)
    comp_total = sum(r.get("amount", 0) or 0 for r in comp)
    return {
        "category": category,
        "base_label": _period_cn(base_period), "compare_label": _period_cn(compare_period),
        "base_total": base_total, "compare_total": comp_total,
        "percent": _percent_change(base_total, comp_total),
        "has_data": bool(base or comp),
    }


def get_highest_expense(period="this_month"):
    """某时段内最大单笔开支（Phase 5 功能 4）。

    参数:
        period: 相对时间词（本月/本周/上周…），默认本月

    返回: {amount, source, date, category} 或 None（无数据）
    """
    start, end = _period_range(period)
    records = _finance_in_range(_load_finance_records(), start, end)
    if not records:
        return None
    top = max(records, key=lambda r: (r.get("amount", 0) or 0))
    return {
        "amount": top.get("amount", 0),
        "source": top.get("source", "?"),
        "date": (top.get("date") or "")[:10],
        "category": top.get("category", ""),
    }


def get_frequent_activities(limit=5):
    """提取高频活动（Phase 5 功能 5）。

    统计来源:
      1. 记忆实体标签（咖啡/会议/吃饭/运动…）
      2. 财务来源（咖啡/饭/奶茶…，排除 日常消费/其他）
      3. 对话归档用户轮的实体标签

    返回: [{activity, count}, ...] 按次数降序
    """
    counts = {}
    # 1) 记忆实体标签
    for m in load_memories():
        for t in (m.get("tags") or []):
            if t in _TOPIC_TAGS:
                counts[t] = counts.get(t, 0) + 1
    # 2) 财务来源
    for r in _load_finance_records():
        src = (r.get("source") or "").strip()
        if src and src not in ("日常消费", "其他", "收入来源"):
            counts[src] = counts.get(src, 0) + 1
    # 3) 对话归档用户轮
    for r in _load_dialogue_raw().get("records", []):
        if r.get("role") != "user":
            continue
        for t in (r.get("tags") or []):
            if t in _TOPIC_TAGS:
                counts[t] = counts.get(t, 0) + 1
    top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [{"activity": act, "count": cnt} for act, cnt in top]


def get_spending_pattern(period="this_week"):
    """按时段分析支出分布（Phase 5 功能 6）。

    参数:
        period: 相对时间词（本周/本月…），默认本周

    返回 dict: {period, label, total, count, breakdown: [{period, amount, percent}, ...]}
    """
    start, end = _period_range(period)
    records = _finance_in_range(_load_finance_records(), start, end)
    by_period = {}
    for r in records:
        p = r.get("period") or _get_period(r.get("time"))
        if not p:
            p = "其他"
        by_period[p] = by_period.get(p, 0) + (r.get("amount", 0) or 0)
    total = sum(by_period.values())
    breakdown = []
    for p in _PERIOD_ORDER:
        amt = by_period.get(p, 0)
        if amt > 0:
            pct = round(amt / total * 100) if total else 0
            breakdown.append({"period": p, "amount": amt, "percent": pct})
    # 未知时段兜底放最后
    for p, amt in by_period.items():
        if p not in _PERIOD_ORDER and amt > 0:
            breakdown.append({"period": p, "amount": amt, "percent": round(amt / total * 100) if total else 0})
    breakdown.sort(key=lambda x: x["percent"], reverse=True)
    return {
        "period": period, "label": _period_cn(period),
        "total": total, "count": len(records), "breakdown": breakdown,
    }


def generate_weekly_summary():
    """本周综合总结（Phase 5 功能 7）：财务 + 日程 + 记忆活动。"""
    start, end = _date_range_for("this_week")
    # 1) 财务：总额 + 主要分类
    fin = _finance_in_range(_load_finance_records(), start, end)
    fin_total = sum(r.get("amount", 0) or 0 for r in fin)
    by_cat = {}
    for r in fin:
        cat = r.get("category") or "其他"
        by_cat[cat] = by_cat.get(cat, 0) + (r.get("amount", 0) or 0)
    top_cats = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:2]
    # 2) 日程：本周事件/待办 + 会议数
    sched_items = [s for s in _load_json_list(SCHEDULE_EVENTS_FILE) + _load_json_list(SCHEDULE_TODOS_FILE)
                   if start <= ((s.get("date") or "")[:10]) <= end]
    meetings = sum(1 for s in sched_items
                   if _has_any((s.get("title") or "") + (s.get("content") or ""), ["会议", "开会"]))
    # 3) 记忆活动：本周实体标签
    mem_counts = {}
    for m in load_memories():
        if not (start <= (m.get("date") or "")[:10] <= end):
            continue
        for t in (m.get("tags") or []):
            if t in _TOPIC_TAGS:
                mem_counts[t] = mem_counts.get(t, 0) + 1
    activities = sorted(mem_counts.items(), key=lambda x: x[1], reverse=True)[:2]
    return {
        "total": fin_total, "count": len(fin), "top_categories": top_cats,
        "schedule_count": len(sched_items), "meetings": meetings,
        "activities": activities,
        "has_data": bool(fin or sched_items or mem_counts),
    }


# ===================== Phase 7 增强对比分析（v3.13.0）=====================

# 习惯总结排除的非行为类标签（情绪是情绪记忆的标签，聊天是兜底标签）
_HABIT_EXCLUDE = {"情绪", "聊天"}


def _week_start_date(date_str):
    """将 YYYY-MM-DD 日期映射到所在周的周一（date 对象）；解析失败返回 None。"""
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return d - timedelta(days=d.weekday())


def _habit_duration_text(created_at, now=None):
    """习惯持续时长文本：已持续N天 / N周（≥2 周显示周）。"""
    if not created_at:
        return ""
    if now is None:
        now = datetime.now()
    try:
        start = datetime.strptime(str(created_at)[:10], "%Y-%m-%d")
        days = max(0, (now - start).days)
    except (ValueError, TypeError):
        return ""
    if days == 0:
        return "今天开始"
    if days < 14:
        return f"已持续{days}天"
    return f"已持续{days // 7}周"


def get_habit_summary(limit=5):
    """提取并总结用户习惯（Phase 7 功能 1）。

    数据源:
      1. patterns.json 行为模式（content + frequency + 持续时长）
      2. get_frequent_activities() 高频活动（记忆实体标签 + 财务来源 + 对话归档）

    同内容习惯合并频次；按频次降序返回 limit 个。

    返回:
        {"habits": [{"habit", "frequency", "duration_text", "source"}], "summary": str}
    """
    habits = {}
    # 1) patterns.json 习惯（含持续时长）
    for p in _load_patterns_raw().get("patterns", []):
        if p.get("archived") or p.get("rejected"):
            continue
        content = (p.get("content") or "").strip()
        if not content:
            continue
        freq = int(p.get("frequency") or 1)
        created = p.get("created_at") or p.get("last_trigger") or ""
        if content in habits:
            habits[content]["frequency"] += freq
        else:
            habits[content] = {"habit": content, "frequency": freq,
                               "created_at": created, "source": "pattern"}
    # 2) 高频活动（记忆/财务/对话）——显示名与 patterns 同键合并
    #    排除非行为类标签（情绪是情绪记忆的标签，不是行为习惯）
    for a in get_frequent_activities(limit=10):
        act = a["activity"]
        if act in _HABIT_EXCLUDE:
            continue
        label = _ACTIVITY_LABEL.get(act, act)
        if label in habits:
            habits[label]["frequency"] += a["count"]
        else:
            habits[label] = {"habit": label, "frequency": a["count"],
                             "created_at": "", "source": "activity"}

    ranked = sorted(habits.values(), key=lambda x: x["frequency"], reverse=True)[:limit]
    now = datetime.now()
    for h in ranked:
        h["duration_text"] = _habit_duration_text(h.get("created_at"), now)

    if not ranked:
        summary = "暂时还没发现你的习惯，多记录一些生活轨迹吧"
    else:
        names = "、".join(h["habit"] for h in ranked[:3])
        top = ranked[0]
        summary = (f"你最近的习惯有：{names}。其中「{top['habit']}」最频繁"
                   f"（{top['frequency']}次），{top['duration_text'] or '刚形成'}。")
    return {"habits": ranked, "summary": summary}


def analyze_spending_trend(periods=3):
    """分析最近 N 周（数据不足按 N 月）消费趋势（Phase 7 功能 2）。

    按周聚合支出，取最近 periods 个时段对比:
      - direction: rising/falling/stable（首尾变化 ±10% 判定）
      - percent: 首尾百分比变化（前段为 0 时返回 None）
      - insight: 识别最近时段相对前段涨幅最大的支出分类

    返回:
        {"has_data": bool, "periods", "unit": "周"|"月",
         "series": [{"label", "total"}], "direction", "direction_cn", "percent", "insight"}
    """
    records = [r for r in _load_finance_records()
               if (r.get("type") or "expense") == "expense"]
    if not records:
        return {"has_data": False}

    # 按周聚合
    week_totals = {}
    for r in records:
        d = (r.get("date") or "")[:10]
        ws = _week_start_date(d)
        if ws:
            key = ws.isoformat()
            week_totals[key] = week_totals.get(key, 0.0) + (r.get("amount", 0) or 0)

    if len(week_totals) >= 2:
        unit = "周"
        buckets = week_totals
    else:
        # 数据不足两周 → 按月聚合
        unit = "月"
        buckets = {}
        for r in records:
            d = (r.get("date") or "")[:10]
            key = d[:7]
            if key:
                buckets[key] = buckets.get(key, 0.0) + (r.get("amount", 0) or 0)
    if len(buckets) < 2:
        return {"has_data": False}

    order = sorted(buckets.keys())[-periods:]
    now_week = _week_start_date(datetime.now().strftime("%Y-%m-%d"))
    series = []
    for key in order:
        if unit == "周":
            label = "本周" if key == now_week.isoformat() else f"{int(key[5:7])}-{int(key[8:10])}周"
        else:
            label = f"{int(key[5:7])}月"
        series.append({"label": label, "total": round(buckets[key], 2)})

    earliest = series[0]["total"]
    latest = series[-1]["total"]
    percent = _percent_change(latest, earliest)

    if percent is None:
        direction = "rising" if latest > 0 else "stable"
    elif percent >= 10:
        direction = "rising"
    elif percent <= -10:
        direction = "falling"
    else:
        direction = "stable"
    direction_cn = {"rising": "上升", "falling": "下降", "stable": "平稳"}[direction]

    # 洞察：各分类在各时段合计，识别最近时段涨幅最大的分类
    cat_series = {}
    for r in records:
        d = (r.get("date") or "")[:10]
        if unit == "月":
            key = d[:7]
        else:
            ws = _week_start_date(d)
            key = ws.isoformat() if ws else None
        if key not in order:
            continue
        cat = r.get("category") or "其他"
        lst = cat_series.setdefault(cat, [0.0] * len(order))
        lst[order.index(key)] += r.get("amount", 0) or 0
    candidates = []
    for cat, amounts in cat_series.items():
        if len(amounts) < 2 or cat == "其他":
            continue
        earlier = sum(amounts[:-1]) / (len(amounts) - 1)
        candidates.append((amounts[-1] - earlier, cat, amounts[-1]))
    insight = ""
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        delta, cat, latest_amt = candidates[0]
        if direction == "rising" and delta > 0:
            insight = f"主要增长来自{cat}支出（最近时段{_fmt_amount(latest_amt)}元），建议关注"
        elif delta > 0:
            insight = f"主要支出集中在{cat}（{_fmt_amount(latest_amt)}元）"
        else:
            insight = "最近时段各分类支出均有回落，控制得不错"

    return {
        "has_data": True, "periods": len(series), "unit": unit,
        "series": series, "direction": direction, "direction_cn": direction_cn,
        "percent": percent, "insight": insight,
    }


def get_personalized_recommendations():
    """基于消费与习惯生成个性化建议（Phase 7 功能 3）。

    数据源: 近 30 天财务支出（分类 + 来源）+ patterns.json 习惯 + 高频活动。
    规则: 咖啡 / 外卖 / 交通 等高频分类 → 对应省钱建议，估算每月可节省金额。

    返回:
        {"has_data": bool, "recommendations": [{"suggestion", "reason", "benefit"}]}
    """
    records = [r for r in _load_finance_records()
               if (r.get("type") or "expense") == "expense"]
    if not records:
        return {"has_data": False, "recommendations": []}
    now = datetime.now()
    cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    recent = [r for r in records if (r.get("date") or "")[:10] >= cutoff]
    if not recent:
        recent = records
    weeks = max(1.0, round((now - datetime.strptime(cutoff, "%Y-%m-%d")).days / 7.0))

    recs = []
    # 1) 咖啡 → 自制咖啡（高频触发，估算 60% 节省）
    coffee = [r for r in recent
              if _has_any((r.get("source") or "") + (r.get("category") or ""), ["咖啡"])]
    if len(coffee) >= 3:
        avg = sum(r.get("amount", 0) or 0 for r in coffee) / len(coffee)
        per_week = len(coffee) / weeks
        if per_week >= 1.0:
            recs.append({
                "suggestion": "可以考虑自制咖啡",
                "reason": f"最近咖啡支出较多（约{per_week:.0f}次/周，平均{_fmt_amount(avg)}元/次）",
                "benefit": f"每月可节省约{_fmt_amount(round(avg * per_week * 4 * 0.6))}元",
            })
    # 2) 外卖/外出就餐 → 自己做饭（估算 30% 节省）
    meal = [r for r in recent
            if _has_any((r.get("source") or "") + (r.get("category") or ""), ["外卖", "饭"])]
    if len(meal) >= 5:
        avg = sum(r.get("amount", 0) or 0 for r in meal) / len(meal)
        per_week = len(meal) / weeks
        recs.append({
            "suggestion": "可以考虑自己做饭",
            "reason": f"最近外出/外卖就餐较多（约{per_week:.0f}次/周）",
            "benefit": f"每月可节省约{_fmt_amount(round(avg * per_week * 4 * 0.3))}元",
        })
    # 3) 交通 → 公共交通（估算 50% 节省）
    trans = [r for r in recent if (r.get("category") or "") == "交通"]
    if len(trans) >= 3:
        avg = sum(r.get("amount", 0) or 0 for r in trans) / len(trans)
        per_week = len(trans) / weeks
        recs.append({
            "suggestion": "可以优先选择地铁/公交出行",
            "reason": f"最近交通支出较多（约{per_week:.0f}次/周）",
            "benefit": f"每月可节省约{_fmt_amount(round(avg * per_week * 4 * 0.5))}元",
        })
    # 4) 兜底：预算管理建议（无具体分类命中时）
    if not recs:
        total = sum(r.get("amount", 0) or 0 for r in recent)
        recs.append({
            "suggestion": "可以尝试每周设定消费预算",
            "reason": f"最近30天总支出{_fmt_amount(total)}元",
            "benefit": "帮助控制冲动消费",
        })
    return {"has_data": True, "recommendations": recs}


def run_analysis(user_input):
    """
    对比分析统一入口（v3.11.0，Phase 5 路由集成）。

    依据用户输入中的分析信号词，下发到对应分析函数并拼装自然语言回复。
    非分析类输入返回 None（交由上层正常路由）。

    返回: 自然语言回复字符串，或 None（非分析意图）。
    """
    text = str(user_input or "")
    if not text.strip():
        return None

    # 记忆记录/日程添加/删除等强意图 → 不是分析类，交还上层路由
    if _has_any(text, ["记住", "记下", "请记住", "帮我记住", "添加", "新增", "安排",
                       "创建", "预约", "删除", "删掉", "取消"]):
        return None

    NO_DATA_MSG = "这段时间还没有数据记录哦"

    # ---- 8) 习惯总结（Phase 7）："我最近有什么习惯" ----
    if _has_any(text, ["习惯"]):
        data = get_habit_summary(limit=5)
        if not data["habits"]:
            return "暂时还没发现你的习惯，多记录一些生活轨迹吧"
        lines = [f"{i}. {h['habit']}（{h['frequency']}次，{h['duration_text'] or '刚形成'}）"
                 for i, h in enumerate(data["habits"], 1)]
        return " ".join(lines) + " " + data["summary"]

    # ---- 9) 消费趋势（Phase 7）："最近消费趋势怎么样" ----
    if _has_any(text, ["趋势", "走势", "走向", "变化趋势"]):
        data = analyze_spending_trend(periods=3)
        if not data["has_data"]:
            return "数据太少，暂时分析不出消费趋势"
        series_txt = "、".join(f"{s['label']}{_fmt_amount(s['total'])}元" for s in data["series"])
        earliest = data["series"][0]["total"]
        latest = data["series"][-1]["total"]
        pct = data["percent"]
        if pct is None:
            trend_txt = (f"最近{data['periods']}{data['unit']}支出{data['direction_cn']}"
                         f"（最新{_fmt_amount(latest)}元）")
        elif data["direction"] == "rising":
            trend_txt = (f"最近{data['periods']}{data['unit']}支出持续上升，"
                         f"从{_fmt_amount(earliest)}元增至{_fmt_amount(latest)}元，增幅{abs(pct)}%")
        elif data["direction"] == "falling":
            trend_txt = (f"最近{data['periods']}{data['unit']}支出有所下降，"
                         f"从{_fmt_amount(earliest)}元降至{_fmt_amount(latest)}元，降幅{abs(pct)}%")
        else:
            trend_txt = (f"最近{data['periods']}{data['unit']}支出保持平稳"
                         f"（{_fmt_amount(earliest)}元→{_fmt_amount(latest)}元）")
        parts = [f"最近{data['periods']}{data['unit']}消费趋势：{series_txt}", trend_txt]
        if data["insight"]:
            parts.append(data["insight"])
        return "；".join(parts)

    # ---- 10) 个性化建议（Phase 7）："有什么建议给我" ----
    if _has_any(text, ["建议", "怎么省钱", "如何省钱", "省钱"]):
        data = get_personalized_recommendations()
        if not data["has_data"]:
            return "暂时还没有足够数据给你建议，先记几笔账吧"
        if not data["recommendations"]:
            return "根据目前的数据，暂时没有特别的省钱建议，继续保持就好"
        lines = []
        for i, rec in enumerate(data["recommendations"], 1):
            line = f"{i}. {rec['suggestion']}：{rec['reason']}"
            if rec.get("benefit"):
                line += f"，{rec['benefit']}"
            lines.append(line)
        return "；".join(lines)

    # 消费语境检测：类别分析必须伴随消费语义（"我喜欢喝拿铁"不是支出分析）
    _SPENDING_CTX = ["花了", "花销", "消费", "开销", "支出", "花费", "账单", "记账",
                     "买了", "用了", "付了", "花了多少", "一共花", "总共花"]
    has_spending_ctx = _has_any(text, _SPENDING_CTX)

    # ---- 0) 类别对比："这个月吃饭花了多少？比上个月呢？" ----
    # 检测具体类别词 + 相邻时段对比词 → 类别跨期对比
    cat_word = None
    for kws, cat in _CATEGORY_KW_MAP:
        if any(kw in text for kw in kws):
            cat_word = cat
            break
    has_period_cmp = _has_any(text, ["比上个月", "和上个月", "跟上个月", "较上月", "上月比",
                                     "比上周", "和上周", "跟上礼拜", "上周比"])
    if cat_word and has_period_cmp and has_spending_ctx:
        # 决定基准/对比时段：输入含"本月/这个月"且对比上月，或含"本周/这周"且对比上周
        if _has_any(text, ["本周", "这周"]) and _has_any(text, ["上周", "上礼拜"]):
            data = compare_category_across_periods(cat_word, "this_week", "last_week")
        else:
            data = compare_category_across_periods(cat_word, "this_month", "last_month")
        if not data["has_data"]:
            return NO_DATA_MSG
        base_amt = _fmt_amount(data["base_total"])
        comp_amt = _fmt_amount(data["compare_total"])
        pct = data["percent"]
        if pct is None:
            trend = "上月没有支出"
        elif pct > 0:
            trend = f"比{data['compare_label']}增加{pct}%"
        elif pct < 0:
            trend = f"比{data['compare_label']}减少{abs(pct)}%"
        else:
            trend = "与上月持平"
        return (f"{data['base_label']}{data['category']}支出{base_amt}元，"
                f"{data['compare_label']}{data['category']}支出{comp_amt}元，{trend}")

    # ---- 1) 频繁活动："我最近经常做什么？" ----
    if _has_any(text, ["经常", "频繁", "常做", "老是", "总在", "常干什么", "经常干什么", "经常做"]):
        acts = get_frequent_activities(limit=8)
        if not acts:
            return "暂时还没发现你经常做的活动，多记录一些生活轨迹吧"
        # 按显示名合并（"饭"+"吃饭" → "吃饭"），避免同一活动出现两次
        merged = {}
        for a in acts:
            label = _ACTIVITY_LABEL.get(a["activity"], a["activity"])
            merged[label] = merged.get(label, 0) + a["count"]
        top = sorted(merged.items(), key=lambda x: x[1], reverse=True)[:5]
        lines = [f"{i}. {label} ({cnt}次)" for i, (label, cnt) in enumerate(top, 1)]
        return "、".join(lines)

    # ---- 2) 消费时段模式："我一般什么时候花钱最多？" ----
    if _has_any(text, ["什么时候", "几点", "时段", "时间段", "哪个时间"]) and _has_any(
            text, ["花", "消费", "开销", "支出", "买东西", "花钱"]):
        data = get_spending_pattern("this_week")
        if not data["breakdown"]:
            return NO_DATA_MSG
        parts = [f"{b['period']} ({b['percent']}%)" for b in data["breakdown"][:3]]
        return "、".join(parts)

    # ---- 3) 周对比："本周花了多少？比上周多吗？" ----
    if _has_any(text, ["比上周", "和上周", "跟上周", "较上周", "上周比", "比上礼拜",
                       "上周多", "上周少", "比上周多", "比上周少"]):
        data = compare_week_over_week()
        if not data["has_data"]:
            return NO_DATA_MSG
        t = _fmt_amount(data["this_total"])
        l = _fmt_amount(data["last_total"])
        pct = data["percent"]
        if pct is None:
            trend = f"上周共花费{l}元" if data["last_total"] else "上周没有支出记录"
            return f"本周共花费{t}元，{trend}"
        if pct > 0:
            trend = f"比上周多{pct}%"
        elif pct < 0:
            trend = f"比上周少{abs(pct)}%"
        else:
            trend = "与上周持平"
        return f"本周共花费{t}元，上周共花费{l}元，{trend}"

    # ---- 4) 月对比："这个月开销比上个月大吗？" ----
    if _has_any(text, ["比上个月", "和上个月", "跟上个月", "较上月", "上月比", "上个月多", "上个月少"]):
        data = compare_month_over_month()
        if not data["has_data"]:
            return NO_DATA_MSG
        t = _fmt_amount(data["this_total"])
        l = _fmt_amount(data["last_total"])
        pct = data["percent"]
        if pct is None:
            trend = f"上月共花费{l}元" if data["last_total"] else "上月没有支出记录"
            return f"本月共花费{t}元，{trend}"
        if pct > 0:
            trend = f"比上月多{pct}%"
        elif pct < 0:
            trend = f"比上月少{abs(pct)}%"
        else:
            trend = "与上月持平"
        return f"本月共花费{t}元，上月共花费{l}元，{trend}"

    # ---- 5) 最高开支："这个月最大的开支是什么？" ----
    if _has_any(text, ["最大", "最高", "最贵", "最大开支", "开销最大", "花钱最多",
                       "花得最多", "支出最多", "消费最多", "最贵一笔"]):
        # 时段推断：含"周"→本周；含"月"→本月；否则本周
        if _has_any(text, ["上个月", "上月"]):
            period = "last_month"
        elif _has_any(text, ["上周"]):
            period = "last_week"
        elif _has_any(text, ["这个月", "本月"]):
            period = "this_month"
        elif _has_any(text, ["本周", "这周"]):
            period = "this_week"
        else:
            period = "this_week"
        top = get_highest_expense(period)
        if not top:
            return NO_DATA_MSG
        amt = _fmt_amount(top["amount"])
        return f"最大开支：{top['source']} {amt}元 ({top['date']})"

    # ---- 6) 分类汇总："这个月吃饭花了多少？"（无对比词） ----
    if cat_word and has_spending_ctx:
        if _has_any(text, ["本月", "这个月"]):
            period = "this_month"
        elif _has_any(text, ["本周", "这周"]):
            period = "this_week"
        else:
            period = "this_month"
        data = get_category_summary(period)
        cat_amt = next((a for c, a in data["breakdown"] if c == cat_word), 0)
        label = _period_cn(period)
        if not data["count"]:
            return NO_DATA_MSG
        return f"{label}{cat_word}支出{_fmt_amount(cat_amt)}元"

    # ---- 7) 周总结："这周我做了什么？" / "总结一下这周" ----
    if (re.search(r"(这周|本周)[^。？!?\n]{0,10}?(做了|干了|发生了什么|有什么|都做了什么|都干了)", text)
            or _has_any(text, ["总结", "周报", "小结", "概况"])):
        data = generate_weekly_summary()
        if not data["has_data"]:
            return NO_DATA_MSG
        parts = [f"本周总结：共花费{_fmt_amount(data['total'])}元"]
        if data["top_categories"]:
            cat_desc = "、".join(f"{c}({_fmt_amount(a)}元)" for c, a in data["top_categories"])
            parts.append(f"主要支出在{cat_desc}")
        if data["schedule_count"]:
            parts.append(f"共{data['schedule_count']}项日程")
        if data["meetings"]:
            parts.append(f"完成{data['meetings']}次会议")
        if data["activities"]:
            act_desc = "、".join(f"{_ACTIVITY_LABEL.get(a, a)}({c}次)" for a, c in data["activities"])
            parts.append(f"经常{act_desc}")
        return "，".join(parts)

    return None


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
