"""
衰减调度 — 晓风Agent v3.7.0
============================
独立的定时任务模块，负责记忆系统的周期性衰减与归档。

职责:
- 每日检查：扫描 patterns.json，对长期未触发的习惯执行置信度衰减
- 归档管理：将置信度过低且长期未触发的条目移至 patterns_archive.json
- 事件日志：记录 on_pattern_decayed / on_pattern_formed / on_fact_confirmed 等事件
- 手动触发：支持用户通过"整理记忆"指令手动执行衰减

触发方式:
1. 主程序启动时自动检查（调用 scheduler_check()）
2. 用户主动触发（"整理记忆" / "清理记忆" / "衰减检查"）

设计原则:
- 本地逻辑为主：所有衰减计算纯 Python 实现，不依赖 LLM
- 每日一次：通过 last_decay 字段防止重复执行
- 幂等安全：多次调用不会产生额外副作用
"""

import os
import json
import logging
from datetime import datetime, date
from typing import Optional

# 路径设置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATTERNS_FILE = os.path.join(BASE_DIR, "patterns.json")
PATTERNS_ARCHIVE_FILE = os.path.join(BASE_DIR, "patterns_archive.json")
EVENTS_LOG_FILE = os.path.join(BASE_DIR, "memory_events.log")

# 日志
logger = logging.getLogger("decay_scheduler")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[衰减调度] %(levelname)s %(message)s"))
    logger.addHandler(_h)

# ===================== 配置常量 =====================

# 衰减阈值（天）
DECAY_THRESHOLD_DAYS = 30          # 超过此天数开始衰减
DECAY_RATE = 0.95                  # 每日衰减乘数
ARCHIVE_THRESHOLD_DAYS = 60        # 超过此天数且 confidence < 阈值则归档
ARCHIVE_CONFIDENCE_THRESHOLD = 0.3  # 归档置信度阈值
PATTERN_FORMED_FREQUENCY = 3       # 连续触发 N 次视为习惯形成

# ===================== 文件操作 =====================


def _load_patterns() -> dict:
    """加载 patterns.json"""
    try:
        with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {"version": "1.0", "patterns": []}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": "1.0", "patterns": []}


def _save_patterns(data: dict):
    """保存 patterns.json"""
    data["updated_at"] = datetime.now().isoformat()
    with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_archive() -> dict:
    """加载归档文件"""
    try:
        with open(PATTERNS_ARCHIVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {"version": "1.0", "archived": []}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": "1.0", "archived": []}


def _save_archive(data: dict):
    """保存归档文件"""
    data["last_updated"] = datetime.now().isoformat()
    with open(PATTERNS_ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _log_event(event_type: str, details: str):
    """记录记忆事件到日志文件"""
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {event_type}: {details}\n"
    try:
        with open(EVENTS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.warning(f"事件日志写入失败: {e}")

# ===================== 核心调度逻辑 =====================


def should_run_today(data: Optional[dict] = None) -> bool:
    """
    检查今天是否已经执行过衰减。

    参数:
        data: patterns.json 的数据（可选，不传则自动加载）

    返回:
        True 表示今天还未执行，应该运行
    """
    if data is None:
        data = _load_patterns()

    last_decay = data.get("last_decay", "")
    if not last_decay:
        return True  # 从未执行过

    today_str = date.today().isoformat()
    return last_decay[:10] != today_str


def run_daily_decay(now_date: Optional[date] = None, force: bool = False) -> dict:
    """
    执行每日置信度衰减（核心调度入口）。

    操作流程:
    1. 检查是否今天已执行（force=True 可跳过检查）
    2. 遍历所有 active patterns
    3. 对超过 DECAY_THRESHOLD_DAYS 天未触发的执行衰减
    4. 对低于 ARCHIVE_CONFIDENCE_THRESHOLD 且超过 ARCHIVE_THRESHOLD_DAYS 的标记归档
    5. 记录事件日志
    6. 保存并返回统计结果

    参数:
        now_date: 当前日期，默认取当天
        force: 是否强制执行（跳过"今日已执行"检查）

    返回:
        {
            "ran": bool,            # 是否实际执行了
            "reason": str,          # 跳过原因或执行摘要
            "stats": {
                "total": int,
                "decayed": int,
                "archived": int,
                "kept": int,
                "formed": int,      # 新形成的习惯
            }
        }
    """
    if now_date is None:
        now_date = date.today()

    data = _load_patterns()

    # 检查是否今日已执行
    if not force and not should_run_today(data):
        return {
            "ran": False,
            "reason": "今日已执行衰减，跳过",
            "stats": {"total": 0, "decayed": 0, "archived": 0, "kept": 0, "formed": 0},
        }

    patterns = data.get("patterns", [])
    if not patterns:
        data["last_decay"] = now_date.isoformat()
        _save_patterns(data)
        return {
            "ran": True,
            "reason": "无活跃习惯需要衰减",
            "stats": {"total": 0, "decayed": 0, "archived": 0, "kept": 0, "formed": 0},
        }

    decayed = 0
    archived = 0
    kept = 0
    formed = 0
    to_archive = []

    for p in patterns:
        if not isinstance(p, dict):
            continue

        # 跳过已归档条目
        if p.get("archived", False):
            continue

        rejected = p.get("rejected", False)
        confidence = p.get("confidence", 0.5)
        frequency = p.get("frequency", 0)
        last_str = p.get("last_trigger", "")

        try:
            last_date = datetime.strptime(last_str[:10], "%Y-%m-%d").date() if last_str else None
        except (ValueError, TypeError):
            last_date = None

        days_since = (now_date - last_date).days if last_date else 999

        # ---- 事件检测：习惯形成 ----
        if frequency >= PATTERN_FORMED_FREQUENCY and not p.get("_formed_logged", False):
            _log_event("on_pattern_formed",
                       f"id={p.get('id')} content={p.get('content', '')[:40]} "
                       f"frequency={frequency} confidence={confidence:.2f}")
            p["_formed_logged"] = True
            formed += 1

        # ---- 衰减逻辑 ----
        if rejected:
            # 已拒绝的不衰减，但检查是否需要归档
            if confidence < ARCHIVE_CONFIDENCE_THRESHOLD and days_since > ARCHIVE_THRESHOLD_DAYS:
                p["archived"] = True
                to_archive.append(p)
                archived += 1
                _log_event("on_pattern_decayed",
                           f"id={p.get('id')} content={p.get('content', '')[:40]} "
                           f"confidence={confidence:.2f} reason=rejected+expired")
            else:
                kept += 1
            continue

        if days_since > DECAY_THRESHOLD_DAYS:
            old_conf = confidence
            confidence = round(confidence * DECAY_RATE, 2)
            p["confidence"] = confidence
            decayed += 1

            logger.debug(f"衰减: [{p.get('id')}] {p.get('content', '')[:30]} "
                         f"conf {old_conf:.2f} → {confidence:.2f} (days_since={days_since})")

        # ---- 归档检查 ----
        if confidence < ARCHIVE_CONFIDENCE_THRESHOLD and days_since > ARCHIVE_THRESHOLD_DAYS:
            p["archived"] = True
            to_archive.append(p)
            archived += 1
            _log_event("on_pattern_decayed",
                       f"id={p.get('id')} content={p.get('content', '')[:40]} "
                       f"confidence={confidence:.2f} reason=decayed+expired")
        else:
            kept += 1

    # ---- 归档写入 ----
    if to_archive:
        archive_data = _load_archive()
        existing_ids = {a.get("id") for a in archive_data.get("archived", [])}
        for ap in to_archive:
            if ap.get("id") not in existing_ids:
                archive_data["archived"].append(ap)
                existing_ids.add(ap.get("id"))
        archive_data["archived_count"] = len(archive_data["archived"])
        _save_archive(archive_data)

    # ---- 保存 ----
    data["last_decay"] = now_date.isoformat()
    _save_patterns(data)

    # ---- 清理 formed 标记（避免持久化） ----
    for p in patterns:
        p.pop("_formed_logged", None)

    reason = (f"衰减完成: {decayed} 条置信度降低, "
              f"{archived} 条归档, {kept} 条保留"
              + (f", {formed} 条习惯形成" if formed > 0 else ""))

    logger.info(reason)

    return {
        "ran": True,
        "reason": reason,
        "stats": {
            "total": len([p for p in patterns if not p.get("archived", False)]),
            "decayed": decayed,
            "archived": archived,
            "kept": kept,
            "formed": formed,
        },
    }


def scheduler_check(now_date: Optional[date] = None) -> dict:
    """
    主程序启动时调用的检查入口。

    如果今天已执行过则静默跳过；
    否则执行 run_daily_decay() 并打印摘要。

    参数:
        now_date: 当前日期，默认取当天

    返回:
        run_daily_decay 的结果字典
    """
    result = run_daily_decay(now_date)

    if result["ran"]:
        stats = result["stats"]
        if stats["decayed"] > 0 or stats["archived"] > 0 or stats["formed"] > 0:
            logger.info(f"📊 {result['reason']}")
        else:
            logger.debug("📊 衰减检查: 无变化")

    return result


def manual_cleanup() -> dict:
    """
    用户手动触发"整理记忆"。

    强制执行衰减 + 归档清理 + 相似事实合并，
    返回详细报告。
    """
    from 记忆引擎 import run_decay, archive_expired, merge_similar_facts, get_memory_stats

    # 1. 衰减
    decay_result = run_decay()

    # 2. 归档清理
    archive_result = archive_expired()

    # 3. 事实去重
    merge_result = merge_similar_facts()

    # 4. 获取最新统计
    stats = get_memory_stats()

    report = (
        f"🧠 记忆整理完成:\n"
        f"   - 置信度衰减: {decay_result['decayed']} 条降低\n"
        f"   - 归档: {decay_result['archived']} 条移至归档\n"
        f"   - 事实去重: {merge_result['merged']} 条合并\n"
        f"   - 当前状态: {stats['facts_count']} 条事实, "
        f"{stats['active_patterns']} 条活跃习惯 "
        f"({stats['archived_patterns']} 条已归档)"
    )

    _log_event("manual_cleanup", report.replace("\n", " | "))
    logger.info(report)

    return {
        "decay": decay_result,
        "archive": archive_result,
        "merge": merge_result,
        "stats": stats,
        "report": report,
    }


def get_decay_status() -> dict:
    """
    获取衰减系统当前状态（用于调试和监控）。

    返回:
        {
            "last_decay": str,
            "today_ran": bool,
            "active_count": int,
            "at_risk_count": int,     # 即将被衰减的条目数
            "archive_size": int,
        }
    """
    data = _load_patterns()
    today = date.today()

    last_decay = data.get("last_decay", "从未")
    today_ran = last_decay[:10] == today.isoformat() if last_decay else False

    patterns = data.get("patterns", [])
    active = [p for p in patterns if not p.get("archived", False)]
    at_risk = 0

    for p in active:
        last_str = p.get("last_trigger", "")
        try:
            last_date = datetime.strptime(last_str[:10], "%Y-%m-%d").date() if last_str else None
        except (ValueError, TypeError):
            last_date = None
        days_since = (today - last_date).days if last_date else 999
        if days_since > DECAY_THRESHOLD_DAYS:
            at_risk += 1

    archive_data = _load_archive()
    archive_size = archive_data.get("archived_count", len(archive_data.get("archived", [])))

    return {
        "last_decay": last_decay,
        "today_ran": today_ran,
        "active_count": len(active),
        "at_risk_count": at_risk,
        "archive_size": archive_size,
    }


# ===================== 独立测试入口 =====================
if __name__ == "__main__":
    import sys

    print("🧪 衰减调度模块测试")
    print(f"   数据目录: {BASE_DIR}")
    print()

    # 状态查询
    status = get_decay_status()
    print(f"📊 当前状态:")
    print(f"   上次衰减: {status['last_decay']}")
    print(f"   今日已执行: {status['today_ran']}")
    print(f"   活跃习惯: {status['active_count']}")
    print(f"   面临衰减: {status['at_risk_count']}")
    print(f"   归档条目: {status['archive_size']}")
    print()

    # 命令行参数支持
    if "--force" in sys.argv or "-f" in sys.argv:
        print("🔧 强制执行衰减...")
        result = run_daily_decay(force=True)
        print(f"   结果: {result['reason']}")
        print(f"   统计: {result['stats']}")
    elif "--cleanup" in sys.argv or "-c" in sys.argv:
        print("🧹 执行手动清理...")
        result = manual_cleanup()
        print(result["report"])
    else:
        print("💡 使用 --force/-f 强制执行衰减")
        print("💡 使用 --cleanup/-c 执行完整清理")
        print()
        # 正常调度检查
        result = scheduler_check()
        if result["ran"]:
            print(f"   执行结果: {result['reason']}")
        else:
            print(f"   {result['reason']}")
