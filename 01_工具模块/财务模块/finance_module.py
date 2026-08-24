import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta

# ===================== 配置 =====================
BASE_DIR = os.path.dirname(__file__)
DATA_FILE = os.path.join(BASE_DIR, "local_archive.json")

# v3.9.18: 查询上下文缓存 — 支持"把它们详细列举一下"继承上一轮筛选条件
_last_query_context = None

# v3.9.24: 存储格式版本号 — 用于自动迁移检测
_STORAGE_VERSION = 2
# ===============================================

# v3.9.29: 记忆系统集成 —— 记录成功记账后写入统一记忆库（失败不影响记账）
# v3.10.9: 返回写入结果并打印完整 traceback（不再静默吞异常），保证"每笔成功记账必有记忆"可诊断。
def _record_memory_event(title, detail, tags=None):
    """将一笔记账写入统一记忆库 memory.json（add_memory_event）。返回 add_memory_event 结果。"""
    try:
        _core_dir = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "00_核心主体"))
        if _core_dir not in sys.path:
            sys.path.insert(0, _core_dir)
        from 记忆.记忆引擎 import add_memory_event
        return add_memory_event(
            title=title,
            detail=detail,
            tags=tags or ["财务"],
            importance=5,
            subtype="event",
        )
    except Exception as e:
        # 记忆记录失败不阻塞记账主流程，但必须可见（打印完整 traceback）
        import traceback
        traceback.print_exc()
        print(f"⚠️ 记忆记录失败(忽略):{e}")
        return {"status": "skipped", "id": "", "message": f"记忆写入失败: {e}"}

# ===================== 数据管理 =====================
def load_data():
    """加载财务数据，自动排序（date desc + time asc），触发迁移。"""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    if not data:
        return []

    # v3.9.24: 检测是否需要迁移（缺少标准字段）
    needs_migration = any("id" not in r for r in data)
    if needs_migration:
        data = _migrate_to_standard_format(data)
        save_data(data)

    return _sort_records(data)


def save_data(data):
    """保存财务数据，自动排序后写入。"""
    sorted_data = _sort_records(data)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted_data, f, ensure_ascii=False, indent=2)


# ===================== v3.9.24: 标准化辅助函数 =====================

def _generate_id(date_str="", time_str=""):
    """生成唯一 ID: finance_{YYYYMMDD}_{HHMM}_{6chars}"""
    import random
    import string
    now = datetime.now()
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            d = now
    else:
        d = now
    if time_str:
        t_part = time_str.replace(":", "")
    else:
        t_part = now.strftime("%H%M")
    rand = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"finance_{d.strftime('%Y%m%d')}_{t_part}_{rand}"


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
    """将 HH:MM 时间映射到时段时间标签。"""
    if not time_str:
        return ""
    try:
        h, m = map(int, time_str.split(":")[:2])
    except (ValueError, AttributeError):
        return ""
    minutes = h * 60 + m
    for start, end, label in _PERIOD_MAP:
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
        if sh * 60 + sm <= minutes <= eh * 60 + em:
            return label
    return ""


_CATEGORY_RULES = [
    (["饭", "吃", "餐", "食堂", "外卖", "餐厅", "饭店", "午餐", "晚餐", "早餐", "晚饭", "午饭", "早饭"], "餐饮"),
    (["交通", "车", "公交", "地铁", "打车", "停车", "高铁", "火车", "飞机"], "交通"),
    (["水果", "零食", "小吃", "面包", "甜品"], "食品"),
    (["奶茶", "咖啡", "饮料", "饮品", "喝"], "饮品"),
    (["购物", "买", "超市", "商场", "网购", "淘宝", "京东", "拼多多"], "购物"),
    (["娱乐", "电影", "游戏", "唱K", "旅游", "KTV", "门票", "景点"], "娱乐"),
    (["房租", "物业", "水电", "燃气", "网费", "电费", "水费", "煤气"], "居住"),
    (["工资", "奖金", "兼职", "收入", "报销", "退款", "红包"], "收入"),
    (["医疗", "药", "医院", "诊所", "体检"], "医疗"),
    (["教育", "书", "课程", "培训", "学费"], "教育"),
    (["通讯", "话费", "流量", "宽带"], "通讯"),
    (["水"], "饮品"),
    (["日常消费", "生活", "日用", "杂货"], "生活"),
]


def _get_category(source):
    """根据来源关键词自动推断分类。"""
    if not source:
        return "其他"
    for keywords, category in _CATEGORY_RULES:
        for kw in keywords:
            if kw in source:
                return category
    return "其他"


def _generate_content(period, source, amount):
    """自动生成自然语言摘要内容。"""
    amt_str = str(int(amount)) if amount == int(amount) else str(amount)
    if source and period:
        return f"{period}{source}花了{amt_str}元"
    elif source:
        return f"{source}花了{amt_str}元"
    elif period:
        return f"{period}日常消费{amt_str}元"
    else:
        return f"日常消费{amt_str}元"


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
    # 07-25 or 7-25
    m = re.match(r'^(\d{1,2})[-/](\d{1,2})$', s)
    if m:
        return f"{datetime.now().year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return s


def _normalize_time(time_str):
    """确保时间格式为 HH:MM。"""
    if not time_str:
        return "12:00"
    s = str(time_str).strip()
    if re.match(r'^\d{2}:\d{2}$', s):
        return s
    if re.match(r'^\d{2}:\d{2}:\d{2}$', s):
        return s[:5]
    # "12:30:00" from datetime
    if "T" in s:
        s = s.split("T")[1]
        if re.match(r'^\d{2}:\d{2}', s):
            return s[:5]
    return "12:00"


def _sort_records(records):
    """按 date 降序 + time 升序排序。"""
    if not records:
        return records
    def _sort_key(r):
        d = r.get("date", "0000-00-00") or "0000-00-00"
        t = r.get("time", "00:00") or "00:00"
        return (-_date_to_ordinal(d), t)
    return sorted(records, key=_sort_key)


def _date_to_ordinal(d):
    """将 YYYY-MM-DD 转为整数序数，用于排序。"""
    try:
        parts = d.split("-")
        if len(parts) == 3:
            return int(parts[0]) * 10000 + int(parts[1]) * 100 + int(parts[2])
    except (ValueError, TypeError):
        pass
    return 0


# ===================== v3.9.24: 数据迁移 =====================

def _migrate_to_standard_format(records):
    """
    将旧格式记录迁移到标准化格式（v3.9.24）。
    补全缺失字段，不丢失任何现有数据。
    """
    if not records:
        return records

    migrated = []
    for r in records:
        if not isinstance(r, dict):
            continue

        # 已迁移过的记录跳过
        if "id" in r and "datetime" in r and "content" in r:
            migrated.append(r)
            continue

        # 日期规范化
        date_str = _normalize_date(r.get("date", ""))
        # 时间规范化
        raw_time = r.get("time", "")
        if not raw_time:
            # 尝试从 datetime 或 date 中提取
            raw_dt = r.get("datetime", r.get("date", ""))
            if "T" in str(raw_dt):
                raw_time = str(raw_dt).split("T")[1][:5]
        time_str = _normalize_time(raw_time)
        # datetime 组合
        dt_str = f"{date_str}T{time_str}:00"

        period = _get_period(time_str)
        source = r.get("source", "日常消费")
        amount = r.get("amount", 0)
        category = r.get("category") or _get_category(source)
        content = r.get("content") or _generate_content(period, source, amount)
        record_id = r.get("id") or _generate_id(date_str, time_str)

        new_record = {
            "id": record_id,
            "type": r.get("type", "expense"),
            "date": date_str,
            "time": time_str,
            "datetime": dt_str,
            "period": period,
            "amount": amount if isinstance(amount, (int, float)) else 0,
            "source": source,
            "category": category,
            "content": content,
            "detail": r.get("detail"),
            "created_at": r.get("created_at") or dt_str,
            "updated_at": r.get("updated_at"),
            # 保留旧字段用于兼容（不参与新逻辑，仅供调试）
            "_original_text": r.get("original_text", ""),
            "_time_ref": r.get("time_ref", ""),
        }
        migrated.append(new_record)

    debug = os.environ.get("DEBUG", "") == "1"
    if debug:
        print(f"[迁移] 已迁移 {len(migrated)} 条记录到标准化格式 (v3.9.24)")

    return migrated

# ===================== 分类映射（v3.8.0） =====================
CATEGORY_MAP = {
    "饭": "餐饮", "外卖": "餐饮",
    "水": "饮品", "奶茶": "饮品", "咖啡": "饮品",
    "水果": "食品", "零食": "食品",
    "工资": "薪资", "奖金": "薪资", "红包": "收入",
    "日常消费": "生活", "收入来源": "收入", "其他": "其他",
}

# ---- v3.9.13: 星期几映射（供 _parse_relative_date 和 _parse_date_with_llm 使用）----
_WEEKDAY_CN_MAP = {
    '周一': 0, '星期一': 0, '礼拜一': 0,
    '周二': 1, '星期二': 1, '礼拜二': 1,
    '周三': 2, '星期三': 2, '礼拜三': 2,
    '周四': 3, '星期四': 3, '礼拜四': 3,
    '周五': 4, '星期五': 4, '礼拜五': 4,
    '周六': 5, '星期六': 5, '礼拜六': 5,
    '周日': 6, '星期天': 6, '星期日': 6, '礼拜天': 6, '礼拜日': 6, '周天': 6,
}
_WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


def _find_weekday_in_text(s):
    """在字符串中查找星期几表达，返回 (weekday_index 0=周一, matched_name) 或 None。"""
    for name, idx in sorted(_WEEKDAY_CN_MAP.items(), key=lambda x: -len(x[0])):
        if name in s:
            return idx, name
    return None


def _compute_weekday_date(now, week_offset, target_weekday):
    """
    计算指定周偏移和星期几的日期。
    week_offset: 0=本周, -1=上周, -2=上上周
    target_weekday: 0=周一 ... 6=周日
    返回 datetime 对象。
    """
    this_monday = now - timedelta(days=now.weekday())
    target_monday = this_monday + timedelta(weeks=week_offset)
    return target_monday + timedelta(days=target_weekday)


def _parse_relative_date(text, now=None):
    """
    v3.9.13: 从文本中提取相对时间词并返回对应的实际日期。
    支持：今天、昨天、前天、大前天、上周X、上上周X、周X、这个月的X号、上个月。
    如果同时也提供了显式日期（如"7月25日"），显式日期优先。

    返回:
        (date_str, label) — 如 ("2026-07-27", "昨天")；无匹配返回 (None, "")
    """
    if now is None:
        now = datetime.now()

    today = now.strftime("%Y-%m-%d")
    text_clean = text.strip()
    debug = os.environ.get("DEBUG", "") == "1"

    if debug:
        print(f"[日期解析] 输入: {text_clean!r}, 当前日期: {today} (周{['一','二','三','四','五','六','日'][now.weekday()]})")

    # 先检查显式日期格式（优先级最高）
    # 2026-07-27 / 2026/07/27
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text_clean)
    if m:
        d = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return d, d

    # X月X日 / X月X号
    m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]', text_clean)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        year = now.year
        # 如果月份已过（跨年场景），用下一年
        if month < now.month:
            year += 1
        d = f"{year}-{month:02d}-{day:02d}"
        return d, f"{month}月{day}日"

    # ---- v3.9.14: 中文数字转换后的文本，用于匹配"这个月的X号"等 ----
    text_cn = _chinese_to_number(text_clean)

    # ---- v3.9.14: "这个月的X号/日" / "本月的X号/日"（必须在通用"本月"之前检查）----
    # 统一正则：匹配 这个月/本月 + 可选的"的" + 数字 + 号/日
    m = re.search(r'(?:这个月|本月)(?:的)?\s*(\d{1,2})\s*[号日]', text_cn)
    if m:
        day = int(m.group(1))
        # 基本合法性检查：日期范围 1-31
        if 1 <= day <= 31:
            try:
                d = datetime(now.year, now.month, day).strftime("%Y-%m-%d")
                if os.environ.get("DEBUG", "") == "1":
                    print(f"[日期解析] 本月X号: text={text_clean!r} → cn={text_cn!r} → day={day} → {d}")
                return d, f"本月{day}号"
            except ValueError:
                pass  # 无效日期（如 2月30日），继续降级
        elif os.environ.get("DEBUG", "") == "1":
            print(f"[日期解析] 本月X号: day={day} 超出范围 1-31，跳过")

    # 相对时间词（简单天数偏移）
    if "大前天" in text_clean:
        d = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        return d, "大前天"
    if "前天" in text_clean:
        d = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        return d, "前天"
    if "昨天" in text_clean:
        d = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        return d, "昨天"
    if "今天" in text_clean:
        return today, "今天"

    # ---- v3.9.13: "上上周X"（必须在"上周X"之前检查，避免子串误匹配）----
    if "上上周" in text_clean:
        wd = _find_weekday_in_text(text_clean)
        if wd:
            target_wd, wd_name = wd
            target_date = _compute_weekday_date(now, -2, target_wd)
            d = target_date.strftime("%Y-%m-%d")
            return d, f"上上周{wd_name}"
        else:
            # 无具体星期几，回退到上上周一
            target_date = _compute_weekday_date(now, -2, 0)
            d = target_date.strftime("%Y-%m-%d")
            return d, "上上周"

    # ---- v3.9.13: "上周X"（提取具体星期几，而非总是上周一）----
    if "上周" in text_clean:
        wd = _find_weekday_in_text(text_clean)
        if wd:
            target_wd, wd_name = wd
            target_date = _compute_weekday_date(now, -1, target_wd)
            d = target_date.strftime("%Y-%m-%d")
            if debug:
                print(f"[日期解析] 上周X: text={text_clean!r}, weekday={wd_name}({target_wd}), "
                      f"this_monday={(now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')}, "
                      f"result={d}")
            return d, f"上周{wd_name}"
        else:
            # Fallback: 上周一
            target_date = _compute_weekday_date(now, -1, 0)
            d = target_date.strftime("%Y-%m-%d")
            return d, "上周"

    if "上个月" in text_clean:
        # 上个月第一天
        first_this_month = datetime(now.year, now.month, 1)
        last_month = first_this_month - timedelta(days=1)
        d = last_month.strftime("%Y-%m") + "-01"
        return d, "上个月"
    if "本周" in text_clean or "这周" in text_clean:
        wd = _find_weekday_in_text(text_clean)
        if wd:
            target_wd, wd_name = wd
            target_date = _compute_weekday_date(now, 0, target_wd)
            d = target_date.strftime("%Y-%m-%d")
            return d, f"本周{wd_name}"
        else:
            target_date = _compute_weekday_date(now, 0, 0)
            d = target_date.strftime("%Y-%m-%d")
            return d, "本周"

    # ---- v3.9.13: 裸"周X"（本周的星期几，如"周二花五元"）----
    wd = _find_weekday_in_text(text_clean)
    if wd:
        target_wd, wd_name = wd
        target_date = _compute_weekday_date(now, 0, target_wd)
        d = target_date.strftime("%Y-%m-%d")
        return d, wd_name

    if "本月" in text_clean or "这个月" in text_clean:
        d = now.strftime("%Y-%m") + "-01"
        return d, "本月"

    if debug:
        print(f"[日期解析] 无匹配: {text_clean!r}")

    return None, ""


# ===================== LLM 日期解析（v3.9.13） =====================

_DATE_PARSE_PROMPT = """你是一个日期解析器。从用户输入中提取日期信息，返回 YYYY-MM-DD 格式。

当前日期：{current_date}，{weekday_cn}

用户输入：{user_input}

请根据用户输入提取具体日期。规则：
- "上周三" → 上周周三的具体日期
- "上上周二" → 两周前周二的具体日期
- "周二" → 本周周二的具体日期
- "这个月的二十三号" → 本月23号的具体日期
- "昨天" → 昨天的日期
- "今天" → 今天的日期
- 如果用户没有指定日期，返回 "null"

只返回日期字符串 YYYY-MM-DD 或 "null"，不要任何其他内容。"""


def _parse_date_with_llm(text, current_time=None, model="qwen2.5:3b"):
    """
    v3.9.13: 使用 LLM 解析用户输入中的日期表达。
    支持复杂的相对日期（如"上上周周二"、"上周三"等），
    这些是正则 _parse_relative_date 的补充（v3.9.13 正则也已增强）。

    参数:
        text: str — 用户原始输入
        current_time: datetime — 当前时间（默认 now）
        model: str — 使用的 Ollama 模型（默认 qwen2.5:3b，快速响应）

    返回:
        (date_str, label) — 如 ("2026-07-22", "上周三")；失败返回 (None, None)
    """
    if current_time is None:
        current_time = datetime.now()

    # 构建提示词：包含当前日期 + 中文星期几
    wd_idx = current_time.weekday()  # 0=周一
    weekday_cn = _WEEKDAY_NAMES[wd_idx]
    now_str = current_time.strftime("%Y-%m-%d")
    prompt = _DATE_PARSE_PROMPT.format(
        current_date=now_str,
        weekday_cn=weekday_cn,
        user_input=text,
    )

    # 读取 Ollama 配置
    try:
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "00_核心主体", "模型.json"
        )
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        config = {}

    host = config.get("ollama_host", "http://localhost:11434")

    debug = os.environ.get("DEBUG", "") == "1"

    try:
        body = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 20, "temperature": 0},
        }, ensure_ascii=False).encode("utf-8")

        url = f"{host.rstrip('/')}/api/generate"
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
        )

        # v3.9.13: 超时从 2s 增加到 5s，给 3b 模型更多响应时间
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = (data.get("response", "") or "").strip()

        if debug:
            print(f"[日期解析LLM] 输入: {text!r}")
            print(f"[日期解析LLM] 提示词: 当前={now_str} {weekday_cn}")
            print(f"[日期解析LLM] 原始响应: {result!r}")

        # 清洗输出：去掉可能的引号、换行、空白
        result = result.strip('"\'"\n\r .,，。')

        if not result or result.lower() == "null" or result == "":
            if debug:
                print("[日期解析LLM] 结果: null（无日期）")
            return None, None

        # 验证是否为有效的 YYYY-MM-DD 格式
        m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', result)
        if m:
            try:
                year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                datetime(year, month, day)
                if debug:
                    print(f"[日期解析LLM] 解析成功: {result}")
                return result, "LLM日期解析"
            except ValueError:
                if debug:
                    print(f"[日期解析LLM] 无效日期: {result}")
                return None, None

        # 尝试从输出中提取日期（有些模型可能输出额外文字）
        m = re.search(r'(\d{4}-\d{2}-\d{2})', result)
        if m:
            try:
                date_str = m.group(1)
                datetime.strptime(date_str, "%Y-%m-%d")
                if debug:
                    print(f"[日期解析LLM] 从响应中提取日期: {date_str}")
                return date_str, "LLM日期解析"
            except ValueError:
                pass

        if debug:
            print(f"[日期解析LLM] 无法解析响应: {result!r}")

    except Exception as e:
        if debug:
            print(f"[日期解析LLM] 调用失败: {e}")

    return None, None


def _extract_time_ref(text):
    """从原文中提取时间参照词，用于生成摘要。"""
    for word in ["前天", "昨天", "今天", "早上", "中午", "晚上", "下午"]:
        if word in text:
            return word
    return ""

# ===================== 中文数字转换（v3.7.8） =====================
def _chinese_to_number(text):
    """
    将文本中的中文数字转换为阿拉伯数字。
    支持：个位(六→6)、十位(十五→15)、百位(一百二十→120)、
          千位(三千五→3500)、万位(一万二→12000)、
          小数(三点五→3.5)、半(一块半→1.5)、角(十二块五→12.5)。
    """
    CN_DIGITS = {
        '零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
        '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
        '两': 2
    }
    CN_UNITS = {
        '十': 10, '百': 100, '千': 1000,
        '万': 10000, '亿': 100000000
    }

    def _parse_cn_int(s):
        """解析纯中文整数字符串，如 '三十五' -> 35。"""
        if not s:
            return 0
        section = 0
        digit = 0
        result = 0
        last_unit_mag = 0

        for ch in s:
            if ch in CN_DIGITS:
                digit = CN_DIGITS[ch]
            elif ch in CN_UNITS:
                unit_val = CN_UNITS[ch]
                if digit == 0:
                    digit = 1  # 单独的"十" = 10
                if unit_val >= 10000:
                    # 万/亿：将当前段乘以单位并入结果
                    section = (section + digit) * unit_val
                    result += section
                    section = 0
                    digit = 0
                    last_unit_mag = unit_val
                else:
                    section += digit * unit_val
                    digit = 0
                    last_unit_mag = unit_val

        # 处理单位后尾随数字的省略形式（如"三千五"→3500）
        if digit > 0 and last_unit_mag > 0:
            if last_unit_mag == 1000:
                section += digit * 100
                digit = 0
            elif last_unit_mag == 100:
                section += digit * 10
                digit = 0
            elif last_unit_mag == 10000:
                result += digit * 1000
                digit = 0
            elif last_unit_mag == 100000000:
                result += digit * 10000000
                digit = 0
            # last_unit_mag == 10: 数字是个位，保持不变（如"十五"→15）

        return result + section + digit

    all_cn_chars = set(CN_DIGITS.keys()) | set(CN_UNITS.keys()) | {'点'}

    # 第一步：将中文数字序列替换为阿拉伯数字
    result = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in all_cn_chars:
            start = i
            while i < len(text) and text[i] in all_cn_chars:
                i += 1
            cn_str = text[start:i]

            # 处理小数点（三点五 → 3.5）
            if '点' in cn_str:
                parts = cn_str.split('点', 1)
                int_part = _parse_cn_int(parts[0]) if parts[0] else 0
                dec_digits = ''.join(
                    str(CN_DIGITS.get(c, c)) for c in parts[1]
                )
                result.append(f'{int_part}.{dec_digits}')
            else:
                result.append(str(_parse_cn_int(cn_str)))
        else:
            result.append(ch)
            i += 1

    text = ''.join(result)

    # 第二步：处理"块X" → ".X元"（角金额：十二块五 = 12.5）
    text = re.sub(r'块(\d)(?!\d)', r'.\1元', text)

    # 第三步：处理"块半" → 补0.5（一块半 = 1.5）
    text = re.sub(
        r'(\d+)块半',
        lambda m: f'{float(m.group(1)) + 0.5}元',
        text
    )

    return text

# ===================== 核心解析函数 =====================
def parse_user_input(text):
    """从口语中提取信息（用于有完整金额的指令）"""
    if "Users" in text or "C:" in text or "Desktop" in text:
        return {"type": "expense", "amount": 0, "source": "系统路径",
                "date": datetime.now().strftime("%Y-%m-%d"), "category": "其他",
                "time_ref": "", "original_text": text}
    original_text = text.strip()
    text = re.sub(r'^\d+>', '', original_text).strip()

    # v3.7.8: 先将中文数字转换为阿拉伯数字，再提取金额
    text = _chinese_to_number(text)

    # 判断类型
    if any(word in text for word in ["花", "买", "消费", "用", "支出"]):
        data_type = "expense"
        source = "日常消费"
        for item in ["水", "饭", "奶茶", "咖啡", "水果", "外卖", "零食"]:
            if item in text:
                source = item
                break
    elif any(word in text for word in ["赚", "收", "工资", "奖金", "红包"]):
        data_type = "income"
        source = "收入来源"
    else:
        data_type = "expense"
        source = "其他"

    # 分类推断
    category = CATEGORY_MAP.get(source, "生活" if data_type == "expense" else "收入")

    # 时间参照词
    time_ref = _extract_time_ref(original_text)

    # v3.9.12: LLM 优先解析日期（支持"上周三"、"上上周周二"等复杂表达）
    # 失败时降级到正则 _parse_relative_date，最后默认 today
    now = datetime.now()
    parsed_date, date_label = _parse_date_with_llm(original_text, now)
    if not parsed_date:
        # 降级：正则相对日期解析（不依赖 LLM，保证可用性）
        parsed_date, date_label = _parse_relative_date(original_text, now)
    if parsed_date:
        record_date = parsed_date
        # 如果有相对时间词标签但 time_ref 未捕获，补充
        if not time_ref and date_label:
            time_ref = date_label
    else:
        record_date = now.strftime("%Y-%m-%d")

    # v3.9.11: 金额提取 — 使用语义模式而非盲目取首个数字。
    # 避免将日期中的数字（如"上周一"的一、"7月25日"的7/25）误判为金额。
    amount = 0
    numbers = re.findall(r'\d+\.?\d*', text) if text else []

    if numbers:
        # 策略 1: 匹配金额附近的货币词（元/块/钱/角/毛），最可靠
        currency_match = re.search(r'(\d+\.?\d*)\s*[元块钱角毛]', text)
        if currency_match:
            num_str = currency_match.group(1)
            amount = float(num_str) if '.' in num_str else int(num_str)
        else:
            # 策略 2: 匹配消费动词后的数字（消费N / 花了N / 用了N / 买了N）
            verb_match = re.search(
                r'(?:消费|花了?|用了?|买了?|付了?)\s*(\d+\.?\d*)', text
            )
            if verb_match:
                num_str = verb_match.group(1)
                amount = float(num_str) if '.' in num_str else int(num_str)
            else:
                # 策略 3: 兜底 — 取最大数值（金额通常比日期数字大）
                amounts = []
                for n in numbers:
                    try:
                        amounts.append(float(n) if '.' in n else int(n))
                    except ValueError:
                        continue
                if amounts:
                    amount = max(amounts)
    else:
        amount = 0

    return {
        "type": data_type,
        "amount": amount,
        "source": source,
        "date": record_date,
        "category": category,
        "time_ref": time_ref,
        "original_text": original_text,
    }

# ===================== 新增：自然语言消费意图识别 =====================
def parse_natural_language_for_expense(text):
    """
    判断一句话里是否隐含着消费行为（比如“今天早上买了两个油条”）
    返回：{'is_expense': True/False, 'item': '油条', 'amount': 0/数字}
    """
    text_lower = text.lower()
    # v3.7.8: 先将中文数字转换为阿拉伯数字，再提取金额
    text = _chinese_to_number(text)

    # 1. 先看是否有明确金额（含小数），优先匹配紧邻货币单位的数字
    money_match = re.search(r'(\d+\.?\d*)\s*(?:块|元|块钱|块钱|角|毛)', text)
    if money_match:
        num_str = money_match.group(1)
        amount = float(num_str) if '.' in num_str else int(num_str)
        return {"is_expense": True, "item": "未知", "amount": amount}

    # 兜底：匹配任意数字（可能为数量而非金额，置0由上层判断）
    numbers = re.findall(r'\d+\.?\d*', text)
    if numbers:
        return {"is_expense": True, "item": "未知", "amount": 0}
    
    # 2. 如果没有金额，检查是否包含潜在的消费动词和物品
    buy_words = ["买", "吃", "喝", "消费", "付"]
    item_words = ["饭", "水", "奶茶", "咖啡", "水果", "外卖", "零食", "油条", "包子", "豆浆", "面包", "汉堡", "薯条", "可乐"]
    
    # 判断是否包含消费意图
    has_buy_word = any(word in text for word in buy_words)
    has_item_word = any(word in text for word in item_words)
    
    if has_buy_word and has_item_word:
        # 尝试提取物品名称
        extracted_item = "未知物品"
        for item in item_words:
            if item in text:
                extracted_item = item
                break
        return {"is_expense": True, "item": extracted_item, "amount": 0}
    
    return {"is_expense": False, "item": "", "amount": 0}

# ===================== 工具入口函数 =====================
def process_command(text):
    """
    处理单条完整指令（有钱数的情况）。

    v3.9.24: 记录标准化 — 所有新记录自动填充 11 个标准字段。
    """
    parsed = parse_user_input(text)
    if parsed['amount'] <= 0:
        return {
            "status": "error",
            "message": "没听懂金额，请说清楚一点，比如'今天早上吃饭花了6元'",
        }

    now = datetime.now()
    date_str = _normalize_date(parsed.get("date", ""))
    time_str = parsed.get("time") or now.strftime("%H:%M")
    time_str = _normalize_time(time_str)
    dt_str = f"{date_str}T{time_str}:00"

    source = parsed.get("source", "日常消费")
    amount = parsed["amount"]
    period = _get_period(time_str)
    category = _get_category(source)
    content = _generate_content(period, source, amount)
    record_id = _generate_id(date_str, time_str)

    new_record = {
        "id": record_id,
        "type": parsed.get("type", "expense"),
        "date": date_str,
        "time": time_str,
        "datetime": dt_str,
        "period": period,
        "amount": amount,
        "source": source,
        "category": category,
        "content": content,
        "detail": None,
        "created_at": dt_str,
        "updated_at": None,
        "_original_text": parsed.get("original_text", ""),
        "_time_ref": parsed.get("time_ref", ""),
    }

    data = load_data()
    data.append(new_record)

    # v3.10.10: 保存并验证——避免"幻觉记账"（回复成功但实际未写入磁盘）。
    try:
        save_data(data)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"⚠️ 财务记录保存异常: {e}")
        return {
            "status": "error",
            "message": "保存失败，请重试",
        }

    # 重载磁盘数据，确认新记录确实已写入（若未写入则视为失败，绝不返回成功）
    try:
        saved_ids = {r.get("id") for r in load_data()}
    except Exception:
        saved_ids = set()
    if record_id not in saved_ids:
        print(f"⚠️ 财务记录验证失败: {record_id} 未写入 {DATA_FILE}")
        return {
            "status": "error",
            "message": "保存失败，请重试",
        }

    # 生成简短摘要，供上层拼装自然语言回复
    time_ref = parsed.get("time_ref", "")
    amt_str = str(int(amount)) if amount == int(amount) else str(amount)
    summary = f"记录{time_ref}{source}{amt_str}元"

    # v3.9.29: 记录到统一记忆库（v3.10.9: 每笔成功记账都写入记忆，返回结果供上层可观察）
    mem_res = _record_memory_event(
        title="记账",
        detail=f"记录{time_ref}{source}花费{amt_str}元（分类:{category}，日期:{date_str}）",
        tags=["财务", category],
    )
    mem_status = mem_res.get("status") if isinstance(mem_res, dict) else "unknown"

    return {
        "status": "success",
        "type": new_record["type"],
        "data": {
            "amount": amount,
            "source": source,
            "date": date_str,
            "category": category,
            "memory_event": mem_status,
        },
        "summary": summary,
    }


def delete_by_scope(scope, keyword=None):
    """
    按指定范围批量删除财务记录（v3.7.4 新增，v3.8.0 重构为结构化返回）。

    参数:
        scope: str — "all" | "yesterday" | "last_week" | "today" | "latest" | "keyword"
        keyword: str — 当 scope="keyword" 时,用作来源/类型匹配词

    返回:
        dict — {"status": "success"/"error", "type": "delete", "data": {...}, "summary": "..."}
    """
    data = load_data()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    if not data:
        return {"status": "error", "message": "💰 没有财务记录可以删除"}

    if scope == "all":
        count = len(data)
        save_data([])
        return {
            "status": "success",
            "type": "delete",
            "data": {"scope": "all", "count": count},
            "summary": f"清空全部{count}条财务记录",
        }

    elif scope == "last_week":
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        kept = [r for r in data if (r.get("date", "") or "") < cutoff]
        removed = len(data) - len(kept)
        if removed == 0:
            return {"status": "error", "message": "💰 最近一周没有财务记录"}
        save_data(kept)
        return {
            "status": "success",
            "type": "delete",
            "data": {"scope": "last_week", "count": removed},
            "summary": f"删除最近一周{removed}条财务记录",
        }

    elif scope == "today":
        kept = [r for r in data if (r.get("date", "") or "") != today_str]
        removed = len(data) - len(kept)
        if removed == 0:
            return {"status": "error", "message": "💰 今天没有财务记录"}
        save_data(kept)
        return {
            "status": "success",
            "type": "delete",
            "data": {"scope": "today", "count": removed},
            "summary": f"删除今天{removed}条财务记录",
        }

    elif scope == "yesterday":
        kept = [r for r in data if (r.get("date", "") or "") != yesterday_str]
        removed = len(data) - len(kept)
        if removed == 0:
            return {"status": "error", "message": "💰 昨天没有财务记录"}
        save_data(kept)
        return {
            "status": "success",
            "type": "delete",
            "data": {"scope": "yesterday", "count": removed},
            "summary": f"删除昨天{removed}条财务记录",
        }

    elif scope == "latest":
        removed = data.pop()
        save_data(data)
        return {
            "status": "success",
            "type": "delete",
            "data": {"scope": "latest", "record": removed},
            "summary": f"删除最新财务记录: {removed.get('source','?')} {removed.get('amount',0)}元",
        }

    elif scope == "keyword":
        if not keyword:
            return {"status": "error", "message": "⚠️ 请提供要删除的财务记录关键词"}
        matched = [r for r in data if keyword in (r.get("source", "") or "")]
        if not matched:
            matched = [r for r in data if keyword in (r.get("type", "") or "")]
        if not matched:
            return {"status": "error", "message": f"🔍 没有找到匹配「{keyword}」的财务记录"}
        for r in matched:
            data.remove(r)
        save_data(data)
        items = [f"{r.get('source','?')} {r.get('amount',0)}元" for r in matched]
        return {
            "status": "success",
            "type": "delete",
            "data": {"scope": "keyword", "keyword": keyword, "count": len(matched)},
            "summary": f"删除{len(matched)}条匹配财务记录: {', '.join(items[:5])}",
        }

    else:
        return {"status": "error", "message": f"⚠️ 未知的删除范围: {scope}"}

# ===================== 查询功能（v3.8.1） =====================

# 查询解析 prompt — 用于 Mode C 自然语言查询
_QUERY_PARSE_PROMPT = """你是财务查询解析器。将用户的自然语言查询转换为结构化参数。

## 输出格式 (只输出JSON, 无其他内容)
{{
  "date_range": "today"|"yesterday"|"this_week"|"last_week"|"this_month"|"last_month"|null,
  "date": "YYYY-MM-DD"|null,
  "source": "饭"|"奶茶"|...|null,
  "aggregate": "sum"|"count"|"avg"|"max"|"min"|null,
  "sort": "amount_desc"|"amount_asc"|"date_desc"|"date_asc"|null,
  "limit": <整数>|null,
  "amount_min": <数字>|null,
  "amount_max": <数字>|null,
  "detail": true|false
}}

## 规则
- date_range: "今天"→today, "昨天"→yesterday, "这周/本周"→this_week, "上周"→last_week, "这个月/本月"→this_month, "上个月"→last_month
- date: 🚨 始终填 null！日期由后端正则引擎精确计算，LLM 不准填写此字段（避免幻觉日期如 2023-04-18）
- source: 从食物/饮品/交通等类别词提取，无则为null
- aggregate: "花了多少/一共/总计"→sum, "几笔/几次/多少笔"→count, "平均"→avg, "最大/最多/最贵"→max, "最小/最少/最便宜"→min
- sort: "最多/最大/最贵"→amount_desc, "最少/最便宜"→amount_asc
- limit: 用户说"前3"/"top3"或有排序需求时设
- amount_min/amount_max: "大于50"→amount_min:50, "小于20"→amount_max:20, "50到100"→amount_min:50,amount_max:100
- detail: 用户要求"详细"/"列举"/"逐条"/"明细"/"每笔"列出时设为true，否则false

用户输入: {user_input}
JSON:"""

def _parse_query_nl(query_text):
    """
    使用 LLM (Ollama) 将自然语言查询解析为结构化参数（Mode C）。

    返回:
        dict — 结构化查询参数; 失败时返回空 dict 走兜底
    """
    import urllib.request

    # 读取 Ollama 配置
    try:
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "00_核心主体", "模型.json"
        )
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        config = {}

    model = config.get("model_name", "qwen2.5:7b")
    host = config.get("ollama_host", "http://localhost:11434")

    try:
        prompt = _QUERY_PARSE_PROMPT.format(user_input=query_text)
        body = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 128, "temperature": 0},
        }, ensure_ascii=False).encode("utf-8")

        url = f"{host.rstrip('/')}/api/generate"
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            response = (raw.get("response", "") or "").strip()

        # 清洗: 去掉可能的 markdown 包裹
        response = re.sub(r'^```(?:json)?\s*', '', response)
        response = re.sub(r'\s*```$', '', response)

        params = json.loads(response)
        if isinstance(params, dict):
            return params
    except Exception:
        pass  # LLM 解析失败, 走兜底

    return {}

# 来源提示规则：查询原文 → 来源过滤标签（v3.13.2 确定性兜底，不依赖 LLM）。
# 标签须是 _execute_query source_labels 中的键，越具体的词优先（最长关键词匹配）。
_SOURCE_HINT_RULES = [
    ("饭", ["吃饭", "餐饮", "饭", "餐", "食堂", "餐厅", "饭店",
            "午餐", "晚餐", "早餐", "午饭", "晚饭", "早饭"]),
    ("奶茶", ["奶茶", "饮料", "饮品"]),
    ("咖啡", ["咖啡"]),
    ("水", ["喝水", "矿泉水"]),
    ("水果", ["水果"]),
    ("外卖", ["外卖"]),
    ("零食", ["零食"]),
    ("交通", ["交通", "打车", "公交", "地铁", "车费", "高铁", "火车", "停车"]),
]


def _extract_source_hint(text):
    """从查询原文提取来源过滤标签（最长关键词匹配，越具体越优先）。无命中返回空串。"""
    if not text:
        return ""
    best, best_len = "", 0
    for label, kws in _SOURCE_HINT_RULES:
        for kw in kws:
            if kw in text and len(kw) > best_len:
                best, best_len = label, len(kw)
    return best


def _detect_direction(text):
    """
    从查询文本判定收支方向（v3.13.5 提取为独立函数，供 _execute_query 与
    上下文继承共用）。返回 "income"/"expense"/"all"；无关键词返回 None。
    优先级：收入词 > 支出词 > 全量词。
    """
    if not text:
        return None
    _INCOME_KW = ["收入", "赚了", "赚到", "进账", "入账", "收到", "工资", "奖金",
                  "补贴", "红包", "发了", "到账"]
    _EXPENSE_KW = ["花了", "支出", "花费", "开销", "消费", "用了", "买了", "付了",
                   "开支", "花销"]
    _ALL_KW = ["所有", "全部", "财务数据", "账单", "账目", "总账", "一共", "总共", "总计"]
    if any(kw in text for kw in _INCOME_KW):
        return "income"
    if any(kw in text for kw in _EXPENSE_KW):
        return "expense"
    if any(kw in text for kw in _ALL_KW):
        return "all"
    return None


def _execute_query(params):
    """
    根据结构化参数查询财务数据，返回结构化结果。

    参数:
        params: dict — 查询参数, 字段可含 date_range/date/source/aggregate/sort/limit/amount_min/amount_max/detail

    返回:
        dict — {"status": "success", "type": "query", "data": [...], "summary": "..."}
    """
    records = load_data()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # ---- v3.9.16: 精确日期过滤（优先级最高，覆盖 date_range）----
    exact_date = params.get("date", "")
    if isinstance(exact_date, str):
        exact_date = exact_date.strip()
    else:
        exact_date = ""

    if exact_date:
        records = [r for r in records if (r.get("date", "") or "") == exact_date]
        label_date = exact_date
        date_start = exact_date  # 标记已过滤，跳过后续范围过滤
    else:
        # ---- 日期范围过滤 ----
        date_range = params.get("date_range", "")

        # 计算截止日期
        if isinstance(date_range, str):
            date_range = date_range.strip().lower()
        else:
            date_range = ""

        # v3.9.3: 映射 3b router 的 "time" 字段到 date_range
        # 当 date_range 未设置时，从 3b 路由的 time 字段推断
        if not date_range:
            time_field = params.get("time", "")
            if time_field and isinstance(time_field, str):
                TIME_TO_RANGE = {
                    "today": "today",
                    "yesterday": "yesterday",
                    "this_week": "this_week",
                    "this_month": "this_month",
                    "last_week": "last_week",
                }
                date_range = TIME_TO_RANGE.get(time_field.strip().lower(), "")

        date_start = None
        label_date = ""

        if date_range == "today":
            date_start = today
            label_date = "今天"
        elif date_range == "yesterday":
            date_start = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            label_date = "昨天"
        elif date_range in ("this_week", "本周"):
            days_since_monday = now.weekday()
            this_monday = now - timedelta(days=days_since_monday)
            date_start = this_monday.strftime("%Y-%m-%d")
            date_end = (this_monday + timedelta(days=6)).strftime("%Y-%m-%d")
            label_date = "本周"
        elif date_range in ("last_week", "上周"):
            days_since_monday = now.weekday()
            last_monday = (now - timedelta(days=days_since_monday + 7))
            last_sunday = last_monday + timedelta(days=6)
            date_start = last_monday.strftime("%Y-%m-%d")
            date_end = last_sunday.strftime("%Y-%m-%d")
            label_date = "上周"
        elif date_range in ("this_month", "本月"):
            date_start = now.strftime("%Y-%m") + "-01"
            if now.month == 12:
                date_end = datetime(now.year + 1, 1, 1).strftime("%Y-%m-%d")
            else:
                date_end = datetime(now.year, now.month + 1, 1).strftime("%Y-%m-%d")
            label_date = "本月"
        elif date_range in ("last_month", "上个月"):
            first_this_month = datetime(now.year, now.month, 1)
            last_month_end = first_this_month - timedelta(days=1)
            date_start = last_month_end.strftime("%Y-%m") + "-01"
            date_end = last_month_end.strftime("%Y-%m-%d")
            label_date = "上个月"

        if date_start:
            if date_range in ("today", "yesterday"):
                records = [r for r in records if (r.get("date", "") or "") == date_start]
            elif date_end:  # v3.13.2: 有明确结束日期 → 闭区间过滤（此前 last_month 英文码落入开区间导致跨月）
                records = [r for r in records
                           if date_start <= (r.get("date", "") or "") <= date_end]
            else:
                records = [r for r in records
                           if (r.get("date", "") or "") >= date_start]

    # ---- 来源过滤 ----
    source_filter = params.get("source", "")
    if isinstance(source_filter, str):
        source_filter = source_filter.strip()
    # v3.13.2: 3b 路由的 query params 不提取 source（提示词仅要求 time/scope），
    # 且 raw_text 常驻使 query_finance 走 Mode B 结构化路径、跳过 LLM 源提取，
    # 导致"吃饭花了多少"返回全部记录。从查询原文确定性提取来源关键词兜底。
    if not source_filter:
        source_filter = _extract_source_hint(params.get("_query_text", ""))
    if source_filter:
        source_labels = {
            "饭": ["饭", "吃饭", "餐饮"], "奶茶": ["奶茶"], "咖啡": ["咖啡"],
            "水": ["水"], "水果": ["水果"], "外卖": ["外卖"], "零食": ["零食"],
            "交通": ["打车", "交通", "地铁", "公交", "车费"],
        }
        matched_labels = source_labels.get(source_filter, [source_filter])
        records = [r for r in records
                   if any(lb in (r.get("source", "") or "") for lb in matched_labels)]

    # ---- v3.13.3: 支出/收入 类型区分 ----
    # 3b 路由器把一切财务查询归为 query，不区分收支方向。依据查询原文判断
    # 用户想要支出、收入还是全部，过滤后再聚合——避免"今天花了多少"把收入计进支出。
    # v3.13.5: 查询原文无方向词时，回退到上下文继承的 direction（"把它们详细列出来"）。
    _qtext = str(params.get("_query_text", "") or "")
    type_mode = _detect_direction(_qtext) or params.get("direction") or "expense"

    # 收入标注：支出模式下若有收入记录，提示"另有N笔收入M元"
    _income_records = [r for r in records if r.get("type") == "income"]
    _income_ann = ""
    if type_mode == "expense" and _income_records:
        _income_total = sum(r.get("amount", 0) for r in _income_records)
        _income_amt_s = str(int(_income_total)) if _income_total == int(_income_total) else str(_income_total)
        _income_ann = f"。另有{len(_income_records)}笔收入，合计{_income_amt_s}元"

    if type_mode == "income":
        records = [r for r in records if r.get("type") == "income"]
    elif type_mode == "expense":
        records = [r for r in records if r.get("type") == "expense"]
    # type_mode == "all" → 收支都保留

    # ---- v3.13.4: 来源分组 / 最大来源识别 ----
    # "收入来源有哪些"→ 按来源列出；"最大收入来源"→ 取金额最大来源。
    # 仅当查询含"来源"语义（来源/有哪些/分别）时启用，避免误伤普通查询。
    _SRC_LIST_KW = ["来源", "有哪些", "哪几类", "哪些类", "分别", "都花在哪", "花在哪"]
    _src_group = any(kw in _qtext for kw in _SRC_LIST_KW)
    _src_max = ("来源" in _qtext) and any(kw in _qtext for kw in ("最大", "最多", "最高"))

    # ---- 金额范围过滤 ----
    if params.get("amount_min") is not None:
        records = [r for r in records if r.get("amount", 0) >= params["amount_min"]]
    if params.get("amount_max") is not None:
        records = [r for r in records if r.get("amount", 0) <= params["amount_max"]]

    # ---- 统计聚合 ----
    aggregate = params.get("aggregate", "")
    if isinstance(aggregate, str):
        aggregate = aggregate.strip().lower()
    else:
        aggregate = ""

    total_amount = sum(r.get("amount", 0) for r in records)
    count = len(records)

    if aggregate == "sum" or (not aggregate and count > 0):
        # 默认 sum 行为
        pass
    elif aggregate == "count":
        total_amount = None  # 不显示总金额, 只显示笔数
    elif aggregate == "avg" and count > 0:
        total_amount = round(total_amount / count, 2)
    elif aggregate == "max" and count > 0:
        total_amount = max(r.get("amount", 0) for r in records)
    elif aggregate == "min" and count > 0:
        total_amount = min(r.get("amount", 0) for r in records)

    # ---- 排序 ----
    sort = params.get("sort", "")
    if sort == "amount_desc":
        records.sort(key=lambda r: r.get("amount", 0), reverse=True)
    elif sort == "amount_asc":
        records.sort(key=lambda r: r.get("amount", 0))
    elif sort == "date_desc":
        records.sort(key=lambda r: r.get("date", ""), reverse=True)
    elif sort == "date_asc":
        records.sort(key=lambda r: r.get("date", ""))

    # ---- 条数限制 ----
    limit = params.get("limit")
    if limit and isinstance(limit, (int, float)) and limit > 0:
        records = records[:int(limit)]

    # ---- v3.9.16: 明细模式检测 ----
    detail = params.get("detail", False)
    if isinstance(detail, str):
        detail = detail.strip().lower() in ("true", "1", "yes")
    # 也检查 query_text 中的关键词（兜底）
    query_text_for_detail = params.get("_query_text", "")
    if not detail and query_text_for_detail:
        # v3.13.14: 补"展开"——"展开讲讲"触发明细模式
        DETAIL_KW = ["详细", "逐条", "列出", "明细", "每笔", "列举", "展开"]
        if any(kw in query_text_for_detail for kw in DETAIL_KW):
            detail = True

    # ---- 生成摘要 ----
    _type_desc = "支出" if type_mode == "expense" else ("收入" if type_mode == "income" else "财务")
    if count == 0:
        scope_desc = label_date or "全部"
        src_desc = f"「{source_filter}」" if source_filter else ""
        msg = f"{scope_desc}没有{src_desc}{_type_desc}记录"
        if _income_ann:
            msg += _income_ann
        return {
            "status": "success",
            "type": "query",
            "data": [],
            "summary": msg,
        }

    # 构建自然语言摘要
    scope_desc = label_date or ""
    src_desc = source_filter or ""

    # v3.9.24: 明细模式 — 逐条列出（含时间 + 时段）
    if detail:
        detail_lines = []
        for i, r in enumerate(records, 1):
            date_str = r.get("date", "?")
            time_str = r.get("time", "")
            source_str = r.get("source", "?")
            amt = r.get("amount", 0)
            amt_str_item = str(int(amt)) if amt == int(amt) else str(amt)
            period_str = r.get("period", "")
            type_str = "💸" if r.get("type") == "expense" else "💰"
            # 格式: 1. 💸 07-29 12:00 中午 吃饭 10元
            date_short = date_str[-5:] if len(date_str) >= 10 else date_str  # MM-DD
            time_display = f" {time_str}" if time_str else ""
            period_display = f" {period_str}" if period_str else ""
            detail_lines.append(
                f"{i}. {type_str} {date_short}{time_display}{period_display} "
                f"{source_str} {amt_str_item}元"
            )
        detail_text = "\n".join(detail_lines)
        scope_prefix = scope_desc + " " if scope_desc else ""
        if type_mode == "all":
            # v3.13.6: 明细模式下全量查询分别显示收支合计（不再混算一个总数）
            _dexp = [r for r in records if r.get("type") == "expense"]
            _dinc = [r for r in records if r.get("type") == "income"]
            _ea = sum(r.get("amount", 0) for r in _dexp)
            _ia = sum(r.get("amount", 0) for r in _dinc)
            _es = str(int(_ea)) if _ea == int(_ea) else str(_ea)
            _is = str(int(_ia)) if _ia == int(_ia) else str(_ia)
            summary = (f"{scope_prefix}共{count}笔{_type_desc}记录：\n\n{detail_text}"
                       f"\n\n支出合计：{_es}元（{len(_dexp)}笔）；收入合计：{_is}元（{len(_dinc)}笔）")
        else:
            total_amt = sum(r.get("amount", 0) for r in records)
            total_str = str(int(total_amt)) if total_amt == int(total_amt) else str(total_amt)
            summary = f"{scope_prefix}共{count}笔{_type_desc}记录：\n\n{detail_text}\n\n合计：{total_str}元"
        if _income_ann:
            summary += _income_ann
        return {
            "status": "success",
            "type": "query",
            "data": records,
            "summary": summary.strip(),
        }

    # ---- v3.13.4: 来源分组汇总（"收入来源有哪些" / "最大收入来源"）----
    if (_src_group or _src_max) and records and not aggregate:
        _agg_src = {}
        for r in records:
            s = r.get("source", "其他")
            _agg_src[s] = _agg_src.get(s, 0) + r.get("amount", 0)
        _sorted_src = sorted(_agg_src.items(), key=lambda x: -x[1])
        def _fmt_src_amt(a):
            return str(int(a)) if a == int(a) else str(a)
        if _src_max:
            _s, _a = _sorted_src[0]
            summary = f"{scope_desc}最大{_type_desc}来源: {_s} {_fmt_src_amt(_a)}元"
        else:
            lines = [f"{scope_desc}{_type_desc}来源（{len(_sorted_src)}项）:"] + \
                    [f"  {s} {_fmt_src_amt(a)}元" for s, a in _sorted_src]
            summary = "\n".join(lines)
        if _income_ann:
            summary += _income_ann
        return {
            "status": "success",
            "type": "query",
            "data": records,
            "summary": summary,
        }

    if aggregate == "count":
        summary = f"{scope_desc}{src_desc}共{count}笔{_type_desc}记录"
    elif aggregate == "avg":
        summary = f"{scope_desc}{src_desc}共{count}笔，平均{total_amount}元"
    elif aggregate == "max":
        top = records[0] if records else {}
        _agg_word = "收入" if type_mode == "income" else "开支"
        summary = f"{scope_desc}最大{_agg_word}: {top.get('source','?')} {top.get('amount',0)}元"
    elif aggregate == "min":
        top = records[0] if records else {}
        _agg_word = "收入" if type_mode == "income" else "开支"
        summary = f"{scope_desc}最小{_agg_word}: {top.get('source','?')} {top.get('amount',0)}元"
    else:
        if type_mode == "all":
            # v3.13.4: "查看所有财务数据" 显示收支明细
            _exp_r = [r for r in records if r.get("type") == "expense"]
            _inc_r = [r for r in records if r.get("type") == "income"]
            _exp_t = sum(r.get("amount", 0) for r in _exp_r)
            _inc_t = sum(r.get("amount", 0) for r in _inc_r)
            _es = str(int(_exp_t)) if _exp_t == int(_exp_t) else str(_exp_t)
            _is = str(int(_inc_t)) if _inc_t == int(_inc_t) else str(_inc_t)
            summary = (f"{scope_desc}{src_desc}共{count}笔记录"
                       f"（支出{len(_exp_r)}笔，合计{_es}元；"
                       f"收入{len(_inc_r)}笔，合计{_is}元）")
        else:
            amt_str = str(int(total_amount)) if total_amount == int(total_amount) else str(total_amount)
            summary = f"{scope_desc}{src_desc}共{count}笔{_type_desc}，合计{amt_str}元"

    # 如果只有1条且非聚合查询且是支出查询, 补充详情（收入/全量查询用默认汇总措辞）
    if count == 1 and not aggregate and type_mode == "expense":
        r = records[0]
        summary = f"{r.get('date','')} {r.get('source','?')} {r.get('amount',0)}元" + \
                  (f"（{scope_desc}仅此一笔）" if scope_desc else "")

    # v3.13.3: 支出模式下若该期间还有收入记录，追加提示（"查看所有"已含收入则不提示）
    if _income_ann:
        summary += _income_ann

    return {
        "status": "success",
        "type": "query",
        "data": records,
        "summary": summary.strip(),
    }

def query_finance(query_text, params=None):
    """
    统一的财务查询入口（v3.8.1 新增，v3.9.18 增强：+上下文继承 +精确日期 +明细模式）。

    支持两种模式:
    - Mode B (structured): params 包含明确的查询参数, 直接执行
    - Mode C (natural language): 仅 query_text, 通过 LLM 解析后执行

    v3.9.18: 当用户使用指代词（"它们"/"这些"）或纯格式请求（"详细点"）时，
    自动继承上一轮查询的筛选条件（date/time/source/scope），避免上下文丢失。

    返回:
        dict — {"status": "success"/"error", "type": "query", "data": [...], "summary": "..."}
    """
    global _last_query_context

    if not query_text:
        return {"status": "error", "message": "请提供查询条件"}

    # Mode B: 有结构化参数（v3.9.3: 仅当参数含非空字段时才走结构化路径，
    # 避免 3b router 返回全 null params 时跳过 LLM 解析导致遗漏默认逻辑）
    if params and isinstance(params, dict) and params:
        has_meaningful = any(
            v for v in params.values()
            if v is not None and v != ""
        )
        if has_meaningful:
            query_params = params
        else:
            query_params = _parse_query_nl(query_text)
    else:
        # Mode C: LLM 解析自然语言查询
        query_params = _parse_query_nl(query_text)

    # 如果 LLM 返回空（解析失败），走兜底：全量查询当天记录
    if not query_params:
        query_params = {"date_range": "today"}

    # ---- v3.13.5: 查询方向（income/expense/all）存入 params 供上下文继承 ----
    # 查询原文含明确方向词时直接设定；否则由继承机制从上一轮补齐。
    _dir_explicit = _detect_direction(query_text)
    if _dir_explicit:
        query_params["direction"] = _dir_explicit

    # ---- v3.9.17: 从原始文本提取精确日期（优先于 date_range）----
    # _parse_relative_date 使用确定性的正则计算，LLM（_parse_query_nl）可能
    # 产生幻觉日期（如把"上周周二"算成 2023-04-18）。因此始终运行正则解析，
    # 并覆盖 LLM 可能返回的错误 date 字段。
    # v3.13.2: 区分"范围词"与"单日词"。本周/上周/本月/上个月 是时间范围，
    # 必须映射为 date_range（this_week/last_week/this_month/last_month）。
    # 否则会被当作精确单日过滤 → "本周花了多少"只查周一、"这个月花了多少"只查1号。
    parsed_date, date_label = _parse_relative_date(query_text)
    if parsed_date:
        _RANGE_LABEL_TO_CODE = {
            "本周": "this_week", "上周": "last_week",
            "本月": "this_month", "上个月": "last_month",
        }
        if date_label in _RANGE_LABEL_TO_CODE:
            query_params["date_range"] = _RANGE_LABEL_TO_CODE[date_label]
            query_params.pop("date", None)  # 清除精确日期，避免其覆盖范围过滤
        else:
            query_params["date"] = parsed_date
        if os.environ.get("DEBUG", "") == "1":
            print(f"[查询日期] 正则解析: {query_text!r} → {parsed_date} ({date_label})")

    # ---- v3.9.18: 上下文继承检测 ----
    # 当用户使用指代词（"它们"/"这些"）或仅要求格式化（"详细点"），
    # 且当前查询没有明确筛选条件时，继承上一轮查询的筛选条件。
    REF_WORDS = ["它们", "这些", "那些", "刚才的", "之前的"]
    # v3.13.14: 补"展开"——"展开讲讲"应继承上下文并显示明细
    DETAIL_WORDS = ["详细", "逐条", "列出", "明细", "每笔", "列举", "展开"]
    has_ref = any(w in query_text for w in REF_WORDS)
    is_detail_req = query_params.get("detail") or any(
        w in query_text for w in DETAIL_WORDS
    )
    has_filters = bool(
        query_params.get("date")
        or query_params.get("time")
        or query_params.get("date_range")
        or (query_params.get("scope") and query_params.get("scope") != "all")
        or query_params.get("source")
    )

    if _last_query_context and not has_filters and (has_ref or is_detail_req):
        for k in ("date", "time", "date_range", "source", "scope"):
            val = _last_query_context.get(k)
            if val:
                query_params[k] = val
        # v3.13.5: 继承收支方向（"把它们详细列出来"沿用上一轮的 income/expense）
        if "direction" not in query_params and _last_query_context.get("direction"):
            query_params["direction"] = _last_query_context["direction"]
        if os.environ.get("DEBUG", "") == "1":
            inherited = {k: query_params.get(k) for k in ("date", "time", "date_range", "source", "scope", "direction") if query_params.get(k)}
            print(f"[上下文继承] 指代词={has_ref} 格式请求={is_detail_req} → 继承: {json.dumps(inherited, ensure_ascii=False)}")

    # ---- v3.9.16: 传递原始查询文本供明细模式检测 ----
    query_params["_query_text"] = query_text

    # ---- v3.9.18: 执行查询并缓存上下文 ----
    result = _execute_query(query_params)

    # 保存当前查询的筛选条件，供下一轮上下文继承
    ctx = {}
    for k in ("date", "time", "date_range", "source", "scope"):
        v = query_params.get(k)
        if v:
            ctx[k] = v
    # v3.13.5: 保存收支方向（"把它们详细列出来"需沿用 income/expense）
    if query_params.get("direction"):
        ctx["direction"] = query_params["direction"]
    if ctx:
        _last_query_context = ctx
        if os.environ.get("DEBUG", "") == "1":
            print(f"[上下文缓存] 保存: {json.dumps(ctx, ensure_ascii=False)}")

    return result


# ===================== 收入统计接口（v3.13.4） =====================

def get_income_total(date_range=None):
    """
    获取指定时间范围的收入总额。

    参数:
        date_range: today / yesterday / this_week / last_week / this_month / last_month，默认 today

    返回:
        float — 该范围内收入合计
    """
    if not date_range:
        date_range = "today"
    try:
        result = _execute_query({"date_range": date_range, "_query_text": "收入"})
        return round(sum(r.get("amount", 0) for r in result.get("data", [])), 2)
    except Exception:
        return 0.0


def get_income_sources(date_range=None):
    """
    列出指定时间范围的收入来源及金额。

    参数:
        date_range: 同 get_income_total

    返回:
        [{"source": "工资", "amount": 5000}, ...] 按金额降序
    """
    if not date_range:
        date_range = "today"
    try:
        result = _execute_query({"date_range": date_range, "_query_text": "收入来源"})
    except Exception:
        return []
    agg = {}
    for r in result.get("data", []):
        s = r.get("source", "其他")
        agg[s] = agg.get(s, 0) + r.get("amount", 0)
    return sorted([{"source": s, "amount": round(a, 2)} for s, a in agg.items()],
                  key=lambda x: x["amount"], reverse=True)


def get_income_trend(periods=3):
    """
    分析收入趋势：按最近 N 个自然月聚合收入，返回每月金额 + 整体方向。

    参数:
        periods: 回溯月份数（默认 3）

    返回:
        {"periods": [{"month": "2026-07", "amount": 100}, ...]（升序）,
         "direction": "上升"|"下降"|"平稳"}
    """
    try:
        data = load_data()
    except Exception:
        data = []
    now = datetime.now()
    months = []
    for i in range(periods - 1, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0:
            y -= 1
            m += 12
        months.append(f"{y}-{m:02d}")

    out = []
    for prefix in months:
        amt = sum(r.get("amount", 0) for r in data
                  if r.get("type") == "income" and (r.get("date") or "").startswith(prefix))
        out.append({"month": prefix, "amount": round(amt, 2)})

    # 方向：最新月 vs 最早月
    if len(out) >= 2 and out[-1]["amount"] > out[0]["amount"]:
        direction = "上升"
    elif len(out) >= 2 and out[-1]["amount"] < out[0]["amount"]:
        direction = "下降"
    else:
        direction = "平稳"
    return {"periods": out, "direction": direction}


# ===================== 独立运行入口 =====================
if __name__ == "__main__":
    print("🧪 财务模块测试模式 (v3.8.0 结构化返回)")
    while True:
        test_input = input("输入测试指令: ")
        if test_input == "exit":
            break
        result = process_command(test_input)
        print(json.dumps(result, ensure_ascii=False, indent=2))