import json
import os
import re
import urllib.request
from datetime import datetime, timedelta

# ===================== 配置 =====================
BASE_DIR = os.path.dirname(__file__)
DATA_FILE = os.path.join(BASE_DIR, "local_archive.json")
# ===============================================

# ===================== 数据管理 =====================
def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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

    v3.8.0: 返回结构化结果，由上层（脑.py）负责生成用户可见的回复。
    """
    new_record = parse_user_input(text)
    if new_record['amount'] > 0:
        data = load_data()
        data.append(new_record)
        save_data(data)

        # 生成简短摘要，供上层拼装自然语言回复
        time_ref = new_record.get("time_ref", "")
        source = new_record["source"]
        amount = new_record["amount"]
        amt_str = str(int(amount)) if amount == int(amount) else str(amount)
        summary = f"记录{time_ref}{source}{amt_str}元"

        return {
            "status": "success",
            "type": new_record["type"],
            "data": {
                "amount": amount,
                "source": source,
                "date": new_record["date"],
                "category": new_record["category"],
            },
            "summary": summary,
        }
    else:
        return {
            "status": "error",
            "message": "没听懂金额，请说清楚一点，比如'今天早上吃饭花了6元'",
        }


def delete_by_scope(scope, keyword=None):
    """
    按指定范围批量删除财务记录（v3.7.4 新增，v3.8.0 重构为结构化返回）。

    参数:
        scope: str — "all" | "last_week" | "today" | "latest" | "keyword"
        keyword: str — 当 scope="keyword" 时,用作来源/类型匹配词

    返回:
        dict — {"status": "success"/"error", "type": "delete", "data": {...}, "summary": "..."}
    """
    data = load_data()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

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
  "source": "饭"|"奶茶"|...|null,
  "aggregate": "sum"|"count"|"avg"|"max"|"min"|null,
  "sort": "amount_desc"|"amount_asc"|"date_desc"|"date_asc"|null,
  "limit": <整数>|null,
  "amount_min": <数字>|null,
  "amount_max": <数字>|null
}}

## 规则
- date_range: "今天"→today, "昨天"→yesterday, "这周/本周"→this_week, "上周"→last_week, "这个月/本月"→this_month, "上个月"→last_month
- source: 从食物/饮品/交通等类别词提取，无则为null
- aggregate: "花了多少/一共/总计"→sum, "几笔/几次/多少笔"→count, "平均"→avg, "最大/最多/最贵"→max, "最小/最少/最便宜"→min
- sort: "最多/最大/最贵"→amount_desc, "最少/最便宜"→amount_asc
- limit: 用户说"前3"/"top3"或有排序需求时设
- amount_min/amount_max: "大于50"→amount_min:50, "小于20"→amount_max:20, "50到100"→amount_min:50,amount_max:100

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

def _execute_query(params):
    """
    根据结构化参数查询财务数据，返回结构化结果。

    参数:
        params: dict — 查询参数, 字段可含 date_range/source/aggregate/sort/limit/amount_min/amount_max

    返回:
        dict — {"status": "success", "type": "query", "data": [...], "summary": "..."}
    """
    records = load_data()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

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
        date_start = (now - timedelta(days=days_since_monday)).strftime("%Y-%m-%d")
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
        label_date = "本月"
    elif date_range in ("last_month", "上个月"):
        first_this_month = datetime(now.year, now.month, 1)
        last_month_end = first_this_month - timedelta(days=1)
        date_start = last_month_end.strftime("%Y-%m") + "-01"
        date_end = last_month_end.strftime("%Y-%m-%d")
        label_date = "上个月"

    if date_start:
        if date_range == "today" or date_range == "yesterday":
            records = [r for r in records if (r.get("date", "") or "") == date_start]
        elif date_range in ("last_week", "上个月"):
            records = [r for r in records
                       if date_start <= (r.get("date", "") or "") <= date_end]
        else:
            records = [r for r in records
                       if (r.get("date", "") or "") >= date_start]

    # ---- 来源过滤 ----
    source_filter = params.get("source", "")
    if isinstance(source_filter, str):
        source_filter = source_filter.strip()
    if source_filter:
        source_labels = {
            "饭": ["饭", "吃饭", "餐饮"], "奶茶": ["奶茶"], "咖啡": ["咖啡"],
            "水": ["水"], "水果": ["水果"], "外卖": ["外卖"], "零食": ["零食"],
            "交通": ["打车", "交通", "地铁", "公交", "车费"],
        }
        matched_labels = source_labels.get(source_filter, [source_filter])
        records = [r for r in records
                   if any(lb in (r.get("source", "") or "") for lb in matched_labels)]

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

    # ---- 生成摘要 ----
    if count == 0:
        scope_desc = label_date or "全部"
        src_desc = f"「{source_filter}」" if source_filter else ""
        return {
            "status": "success",
            "type": "query",
            "data": [],
            "summary": f"{scope_desc}没有{src_desc}财务记录",
        }

    # 构建自然语言摘要
    scope_desc = label_date or ""
    src_desc = source_filter or ""

    if aggregate == "count":
        summary = f"{scope_desc}{src_desc}共{count}笔记录"
    elif aggregate == "avg":
        summary = f"{scope_desc}{src_desc}共{count}笔，平均{total_amount}元"
    elif aggregate == "max":
        top = records[0] if records else {}
        summary = f"{scope_desc}最大开支: {top.get('source','?')} {top.get('amount',0)}元"
    elif aggregate == "min":
        top = records[0] if records else {}
        summary = f"{scope_desc}最小开支: {top.get('source','?')} {top.get('amount',0)}元"
    else:
        amt_str = str(int(total_amount)) if total_amount == int(total_amount) else str(total_amount)
        summary = f"{scope_desc}{src_desc}共{count}笔，合计{amt_str}元"

    # 如果只有1条且非聚合查询, 补充详情
    if count == 1 and not aggregate:
        r = records[0]
        summary = f"{r.get('date','')} {r.get('source','?')} {r.get('amount',0)}元" + \
                  (f"（{scope_desc}仅此一笔）" if scope_desc else "")

    return {
        "status": "success",
        "type": "query",
        "data": records,
        "summary": summary.strip(),
    }

def query_finance(query_text, params=None):
    """
    统一的财务查询入口（v3.8.1 新增）。

    支持两种模式:
    - Mode B (structured): params 包含明确的查询参数, 直接执行
    - Mode C (natural language): 仅 query_text, 通过 LLM 解析后执行

    返回:
        dict — {"status": "success"/"error", "type": "query", "data": [...], "summary": "..."}
    """
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

    return _execute_query(query_params)

# ===================== 独立运行入口 =====================
if __name__ == "__main__":
    print("🧪 财务模块测试模式 (v3.8.0 结构化返回)")
    while True:
        test_input = input("输入测试指令: ")
        if test_input == "exit":
            break
        result = process_command(test_input)
        print(json.dumps(result, ensure_ascii=False, indent=2))