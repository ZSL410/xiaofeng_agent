# -*- coding: utf-8 -*-
"""v3.13.17 对话修复自检：待记账状态机 + "你说什么+内容"自然提问预路由

运行：.venv/Scripts/python.exe -X utf8 _selftest_v3.13.17.py
（需在项目根目录下，依赖 .venv 内 edge_tts/vosk 等，或本机已装依赖）
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "00_核心主体"))

import 脑

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


print("=" * 60)
print(f"[自检] v3.13.17 待记账状态机 + 自然提问预路由  VERSION={脑.VERSION}")
print("=" * 60)

# 版本
check("VERSION == 3.13.17", 脑.VERSION == "3.13.17", 脑.VERSION)

# ---------- Fix 1：待记账状态机 ----------
print("\n--- 1. 待记账状态机（记账意图两轮表达）---")
check("「你好，帮我记个账」非纯问候（不被问候钩子劫持）",
      脑._is_pure_greeting("你好，帮我记个账") is False)
check("「你好，帮我记个账」非澄清、非自然提问（不被澄清/WH钩子劫持）",
      脑._CLARIFY_RE.search("你好，帮我记个账") is None
      and 脑._WH_QUESTION_RE.search("你好，帮我记个账") is None)
check("「你好，帮我记个账」无金额 → 应进入待记账状态",
      脑._contains_amount("你好，帮我记个账") is False)

# 模拟待记账状态机决策（与 脑.py 内联逻辑一致）
def simulate_pending(user_input):
    resp = user_input.strip()
    if resp in ("算了", "不记了", "不用了", "取消", "不用", "不记", "不了", "没有"):
        return "cancel"
    if 脑._contains_amount(resp):
        return "record"
    return "fallthrough"


check("待记账后补金额「今天吃饭花了25元」→ 记账",
      simulate_pending("今天吃饭花了25元") == "record")
check("待记账后补金额「午饭25元」→ 记账",
      simulate_pending("午饭25元") == "record")
check("待记账后取消「算了」→ 取消",
      simulate_pending("算了") == "cancel")
check("待记账后取消「不记了」→ 取消",
      simulate_pending("不记了") == "cancel")
check("待记账后新请求「查一下待办」→ 放行正常路由（不吞轮）",
      simulate_pending("查一下待办") == "fallthrough")
check("待记账后查询「今天花了多少钱」→ 放行（不误记）",
      simulate_pending("今天花了多少钱") == "fallthrough")
check("待记账后无关内容「嗯」→ 放行",
      simulate_pending("嗯") == "fallthrough")

# ---------- Fix 2："你说什么+内容"自然提问 ----------
print("\n--- 2. '你说什么+内容' 自然提问预路由 ---")
WH = 脑._WH_QUESTION_RE
CL = 脑._CLARIFY_RE

# 有内容 → 自然提问（WH 命中、CL 不命中）
for q in ["你说什么颜色的好", "你说什么颜色", "你说什么颜色的好看",
          "你说什么都行", "你说什么好吃"]:
    check(f"自然提问: {q!r}", WH.search(q) is not None and CL.search(q) is None,
          f"WH={WH.search(q)} CL={CL.search(q)}")

# 独立澄清 → 澄清重复（v3.13.16 行为保留）。
# 主循环先查 _CLARIFY_RE（命中即重复并 continue），故澄清短语只要 CL 命中即生效；
# 即使 "你说什么来着" 也会被 WH 子串命中，但 CLARIFY 优先级更高（先查先赢）。
for c in ["你说什么", "你说什么？", "你说什么。", "你说什么来着", "再说一遍",
          "我没听清", "你刚才说什么"]:
    check(f"澄清请求保留（CLARIFY 优先）: {c!r}", CL.search(c) is not None,
          f"CL={CL.search(c)}")
check("「你说什么来着」为澄清（CLARIFY 命中且优先于 WH）",
      CL.search("你说什么来着") is not None)

# 回归：问候语钩子不受影响
check("问候语回归「你好」", 脑._is_pure_greeting("你好") is True)
check("问候语回归「hello」", 脑._is_pure_greeting("hello") is True)

print("\n" + "=" * 60)
print(f"结果: {passed} 通过, {failed} 失败")
print("=" * 60)
sys.exit(1 if failed else 0)
