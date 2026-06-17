import json
import os
import re
from datetime import datetime

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

# ===================== 核心解析函数 =====================
def parse_user_input(text):
    """从口语中提取信息（用于有完整金额的指令）"""
    if "Users" in text or "C:" in text or "Desktop" in text:
        return {"type": "expense", "amount": 0, "source": "系统路径", "date": datetime.now().strftime("%Y-%m-%d")}
    text = re.sub(r'^\d+>', '', text).strip()
    
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
    
    numbers = re.findall(r'\d+', text)
    amount = int(numbers[0]) if numbers else 0
    return {"type": data_type, "amount": amount, "source": source, "date": datetime.now().strftime("%Y-%m-%d")}

# ===================== 新增：自然语言消费意图识别 =====================
def parse_natural_language_for_expense(text):
    """
    判断一句话里是否隐含着消费行为（比如“今天早上买了两个油条”）
    返回：{'is_expense': True/False, 'item': '油条', 'amount': 0/数字}
    """
    text_lower = text.lower()
    # 1. 先看是否有明确金额
    numbers = re.findall(r'\d+', text)
    if numbers:
        return {"is_expense": True, "item": "未知", "amount": int(numbers[0])}
    
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
    """处理单条完整指令（有钱数的情况）"""
    new_record = parse_user_input(text)
    if new_record['amount'] > 0:
        data = load_data()
        data.append(new_record)
        save_data(data)
        return f"✅ 已记录：{new_record}"
    else:
        return "⚠️ 没听懂金额，请说清楚一点，比如'今天早上吃饭花了6元'"

# ===================== 独立运行入口 =====================
if __name__ == "__main__":
    print("🧪 财务模块测试模式")
    while True:
        test_input = input("输入测试指令: ")
        if test_input == "exit":
            break
        print(process_command(test_input))