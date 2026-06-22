import json
import os
from datetime import date, datetime

BASE_DIR = os.path.dirname(__file__)
SHORT_TERM_FILE = os.path.join(BASE_DIR, "短期记忆.json")
LONG_TERM_FILE = os.path.join(BASE_DIR, "长期记忆.json")

# ===================== 短期记忆 ====================


def load_short_term():
    try:
        with open(SHORT_TERM_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_short_term(history):
    if len(history) > 20:
        history = history[-20:]
    with open(SHORT_TERM_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ===================== 长期记忆 ====================


def load_long_term():
    try:
        with open(LONG_TERM_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_long_term(memory):
    with open(LONG_TERM_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


# ===================== 规律遗忘机制（v3.5.0 新增）=====================


def decay_patterns(now_date=None):
    """
    每周对长期记忆中的行为模式执行置信度衰减。

    衰减规则:
    - last_triggered > 30 天: 每周 confidence -= 0.12
    - 用户已拒绝(rejected=True): 不衰减(已在 reject_pattern 中一次性处理)
    - confidence < 0.3: 删除该模式

    参数:
        now_date: date 对象, 默认取当天

    返回:
        dict {"removed": int, "decayed": int, "kept": int}
    """
    if now_date is None:
        now_date = date.today()

    memory = load_long_term()
    if not isinstance(memory, dict):
        memory = {}

    patterns = memory.get("patterns", [])
    if not patterns:
        return {"removed": 0, "decayed": 0, "kept": 0}

    kept = []
    decayed = 0
    removed = 0

    for p in patterns:
        if not isinstance(p, dict):
            continue

        # 获取最后触发日期
        last_str = p.get("last_triggered", "")
        rejected = p.get("rejected", False)
        confidence = p.get("confidence", 0.5)

        try:
            last_date = datetime.strptime(last_str[:10], "%Y-%m-%d").date() if last_str else None
        except (ValueError, TypeError):
            last_date = None

        # 计算距上次触发的天数
        days_since = (now_date - last_date).days if last_date else 999

        # 如果用户拒绝过，已经在 reject_pattern 中处理
        # 这里只对很久未触发的规律衰减
        if days_since > 30:
            # 每超过 30 天，每周衰减 0.12
            weeks_over = max(1, (days_since - 30) // 7)
            decay_amount = weeks_over * 0.12
            confidence = round(confidence - decay_amount, 2)
            decayed += 1

        # 低于阈值则删除
        if confidence < 0.3:
            removed += 1
            continue

        p["confidence"] = confidence
        kept.append(p)

    memory["patterns"] = kept
    memory["last_decay"] = now_date.isoformat()
    save_long_term(memory)

    return {"removed": removed, "decayed": decayed, "kept": len(kept)}


def reject_pattern(content_substring):
    """
    用户明确拒绝某个规律时立即降低其置信度。

    匹配规则: content_substring 出现在 pattern 的 content/from/to 字段中。

    参数:
        content_substring: 用户否定指向的内容描述(如"泡咖啡"、"冥想")

    返回:
        dict {"affected": int, "message": str}
    """
    memory = load_long_term()
    if not isinstance(memory, dict):
        return {"affected": 0, "message": "长期记忆为空"}

    patterns = memory.get("patterns", [])
    if not patterns:
        return {"affected": 0, "message": "没有已提炼的规律"}

    affected = 0
    for p in patterns:
        if not isinstance(p, dict):
            continue

        # 匹配 content / from / to 字段
        match_fields = [p.get("content", ""), p.get("from", ""), p.get("to", "")]
        if any(content_substring in f for f in match_fields if f):
            p["rejected"] = True
            p["confidence"] = round(p.get("confidence", 0.5) - 0.30, 2)
            if p["confidence"] < 0:
                p["confidence"] = 0.0
            p["last_triggered"] = date.today().isoformat()  # 标记处理日期
            affected += 1

    # 不立即删除低于阈值的一一留给 decay_patterns 统一清理
    memory["patterns"] = patterns
    save_long_term(memory)

    if affected == 0:
        return {"affected": 0, "message": f"没有找到与「{content_substring}」匹配的规律"}
    else:
        return {"affected": affected, "message": f"已标记 {affected} 条规律为「已拒绝」(置信度 -0.30)"}


def boost_pattern(content_substring):
    """
    用户确认某个规律时提升其置信度(反向操作, 用于正面反馈)。

    参数:
        content_substring: 用户确认的内容描述

    返回:
        dict {"affected": int, "message": str}
    """
    memory = load_long_term()
    if not isinstance(memory, dict):
        return {"affected": 0, "message": "长期记忆为空"}

    patterns = memory.get("patterns", [])
    if not patterns:
        return {"affected": 0, "message": "没有已提炼的规律"}

    affected = 0
    today_str = date.today().isoformat()
    for p in patterns:
        if not isinstance(p, dict):
            continue

        match_fields = [p.get("content", ""), p.get("from", ""), p.get("to", "")]
        if any(content_substring in f for f in match_fields if f):
            p["confidence"] = min(0.95, p.get("confidence", 0.5) + 0.10)
            p["last_triggered"] = today_str
            p["rejected"] = False  # 清除拒绝标记
            affected += 1

    memory["patterns"] = patterns
    save_long_term(memory)

    if affected == 0:
        return {"affected": 0, "message": f"没有找到与「{content_substring}」匹配的规律"}
    else:
        return {"affected": affected, "message": f"已提升 {affected} 条规律的置信度 (+0.10)"}
