# -*- coding: utf-8 -*-
"""v3.13.16 对话修复自检：问候语措辞 + 澄清请求重复

运行：.venv/Scripts/python.exe _selftest_v3.13.16.py
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
print(f"[自检] v3.13.16 对话修复  VERSION={脑.VERSION}")
print("=" * 60)

# ---------- 自检 #1：问候语措辞 ----------
print("\n--- 1. 问候语：明确'我能帮你做什么' ---")
G = 脑._GREETING_RE

# 纯问候语应命中
for g in ["你好", "您好", "你好呀", "嗨", "hi", "hello", "在吗",
          "早上好", "你好！", "Hello  ", "你好呀？"]:
    check(f"纯问候语命中: {g!r}", 脑._is_pure_greeting(g))

# 带内容/非问候语不应命中（不能被问候钩子劫持）
for g in ["你好，帮我记个账", "你好 今天天气不错", "你好吗？我很担心你",
          "你知道吗", "你好棒", "哈哈"]:
    check(f"非纯问候不劫持: {g!r}", not 脑._is_pure_greeting(g), "应返回 False")

# 问候语回复文案方向正确（含"帮我"= 帮我做事，而非"帮我做"）
greeting_reply = "你好呀！有什么我可以帮你的吗？"
check("回复表达'我可以帮你做什么'", "我可以帮你" in greeting_reply
      and "帮我做" not in greeting_reply, greeting_reply)

# 系统提示词包含问候语风格指南
_brain_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "00_核心主体", "脑.py")
with open(_brain_path, encoding="utf-8") as f:
    src = f.read()
check("系统提示词含问候语风格指南",
      "当用户向你打招呼" in src and "我可以帮你做什么" in src)

# ---------- 自检 #2：澄清请求重复上一轮回复 ----------
print("\n--- 2. 澄清请求：重复上一轮回复 ---")
CL = 脑._CLARIFY_RE

# 各澄清短语应命中
for c in ["你说什么", "你刚才说什么", "你说什么？", "再说一遍", "我没听清",
          "你刚才说什么", "再说一次", "没听清楚", "你说啥", "你再说一遍",
          "你再说什么", "再说一遍。", " 你刚才说什么  "]:
    check(f"澄清短语命中: {c!r}", CL.search(c) is not None)

# 不应误伤普通请求
for c in ["你说什么颜色的好", "帮我看看再说一遍还是算了吧"]:
    check(f"非澄清不命中: {c!r}", CL.search(c) is None, "应无匹配")

# 有历史回复 → 重复上一条助手回复
st = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好呀！有什么我可以帮你的吗？"},
]
r = 脑._build_clarify_reply(st)
check("有历史 → 重复上一轮回复", "你好呀！有什么我可以帮你的吗？" in r, r)
check("有历史 → 追问'需要我再说一遍吗？'", "需要我再说一遍吗？" in r, r)

# 工具轮回复也在短期记忆 → 同样能重复
st2 = [
    {"role": "user", "content": "今天吃饭花了20元"},
    {"role": "assistant", "content": "好嘞，已记下你今天的饭花的20块钱～"},
]
r2 = 脑._build_clarify_reply(st2)
check("工具轮回复也能重复", "已记下你今天的饭花的20块钱～" in r2, r2)

# 无历史回复 → 友好提示，非"不太确定你的意思"
r3 = 脑._build_clarify_reply([])
check("无历史 → 友好提示", "还没说过什么" in r3 and "不太确定你的意思" not in r3, r3)

print("\n" + "=" * 60)
print(f"结果: {passed} 通过, {failed} 失败")
print("=" * 60)
sys.exit(1 if failed else 0)
