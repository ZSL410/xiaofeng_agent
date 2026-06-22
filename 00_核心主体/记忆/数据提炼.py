"""
数据提炼模块 — 晓风Agent 长期记忆引擎

扫描用户的待办和日程历史，提炼行为模式：
- time_based:  同一内容在相似时间段重复出现（如每天 8:00-9:00 泡咖啡）
- interval:    同一内容以固定间隔创建（如每 30 分钟休息一次）
- association: 内容之间的关联性（如"冥想"后经常跟"休息"）

提炼结果写入 长期记忆.json 的 patterns 字段。
"""

import json
import os
import sys
from datetime import datetime

# 路径设置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LONG_TERM_FILE = os.path.join(BASE_DIR, "长期记忆.json")

# 确保能导入日程模块
_SCHEDULE_DIR = os.path.normpath(
    os.path.join(BASE_DIR, "..", "..", "01_工具模块", "日程模块")
)
if _SCHEDULE_DIR not in sys.path:
    sys.path.insert(0, _SCHEDULE_DIR)


def _load_long_term():
    """加载长期记忆，文件不存在或损坏时返回空字典"""
    try:
        with open(LONG_TERM_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_long_term(memory):
    """保存长期记忆到文件"""
    with open(LONG_TERM_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def refine(verbose=True):
    """
    执行数据提炼：扫描待办/日程 → 提取模式 → 写入长期记忆。

    参数:
        verbose: 是否打印详细进度信息

    返回:
        dict {
            "status": "ok" | "empty" | "error",
            "total": int,          # 提炼出的模式总数
            "by_type": dict,       # 按类型统计
            "message": str,        # 人类可读的摘要
        }
    """
    try:
        from schedule_module import extract_patterns, _load_todos, _load_events
    except ImportError as e:
        return {
            "status": "error",
            "total": 0,
            "by_type": {},
            "message": f"无法导入日程模块: {e}",
        }

    # 检查数据源
    todos = _load_todos()
    events = _load_events()
    total_items = len(todos) + len(events)
    if total_items == 0:
        return {
            "status": "empty",
            "total": 0,
            "by_type": {},
            "message": "📭 当前没有待办或日程数据可供提炼。",
        }

    if verbose:
        print(f"🔍 正在扫描 {len(todos)} 条待办 + {len(events)} 条日程...")

    # 提炼模式
    patterns = extract_patterns()

    if not patterns:
        return {
            "status": "empty",
            "total": 0,
            "by_type": {},
            "message": "📭 扫描完成，但未发现显著的重复行为模式。多使用一段时间后再试。",
        }

    # 统计类型分布
    by_type = {}
    for p in patterns:
        t = p.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    # 为每个新模式添加遗忘机制所需字段
    today_str = datetime.now().strftime("%Y-%m-%d")
    for p in patterns:
        p.setdefault("last_triggered", today_str)
        p.setdefault("rejected", False)

    # 加载现有长期记忆并合并（保留已有模式的 rejected 状态）
    memory = _load_long_term()
    old_patterns = memory.get("patterns", []) if isinstance(memory, dict) else []

    # 尝试将旧模式的状态迁移到新模式中
    for new_p in patterns:
        key_fields = ("type", "content")
        for old_p in old_patterns:
            if (isinstance(old_p, dict)
                    and old_p.get("type") == new_p.get("type")
                    and old_p.get("content") == new_p.get("content")):
                # 保留用户拒绝标记
                if old_p.get("rejected"):
                    new_p["rejected"] = True
                # 保留更早的 last_triggered (如果有)
                old_last = old_p.get("last_triggered", "")
                new_last = new_p.get("last_triggered", "")
                if old_last and old_last < new_last:
                    new_p["last_triggered"] = old_last
                break

    memory["patterns"] = patterns
    memory["last_refined"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    _save_long_term(memory)

    # 构建摘要
    type_names = {
        "time_based": "时段习惯",
        "interval": "固定间隔",
        "association": "内容关联",
    }
    type_lines = []
    for t, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        label = type_names.get(t, t)
        type_lines.append(f"{label} ×{count}")

    message = (
        f"✅ 数据提炼完成: 从 {total_items} 条记录中发现 {len(patterns)} 条模式\n"
        f"   ({', '.join(type_lines)})\n"
        f"   已写入 长期记忆.json"
    )

    if verbose:
        print(message)
        for i, p in enumerate(patterns, 1):
            t = p.get("type", "?")
            conf = p.get("confidence", 0)
            if t == "time_based":
                print(f"  {i}. [{t}] {p.get('content','?')} "
                      f"@{p.get('time_range','?')} "
                      f"({p.get('frequency')}次, conf={conf:.2f})")
            elif t == "interval":
                print(f"  {i}. [{t}] {p.get('content','?')} "
                      f"每{p.get('interval_minutes')}分钟 "
                      f"({p.get('occurrences')}次, conf={conf:.2f})")
            elif t == "association":
                print(f"  {i}. [{t}] {p.get('from','?')} → {p.get('to','?')} "
                      f"({p.get('frequency')}次, conf={conf:.2f})")

    return {
        "status": "ok",
        "total": len(patterns),
        "by_type": by_type,
        "message": message,
    }


# ===================== 独立测试入口 =====================
if __name__ == "__main__":
    print("🧪 数据提炼模块测试")
    print(f"   数据源: {_SCHEDULE_DIR}")
    print()
    result = refine(verbose=True)
    if result["status"] != "ok":
        print(result["message"])
