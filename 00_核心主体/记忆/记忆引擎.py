import json
import os

BASE_DIR = os.path.dirname(__file__)
SHORT_TERM_FILE = os.path.join(BASE_DIR, "短期记忆.json")
LONG_TERM_FILE = os.path.join(BASE_DIR, "长期记忆.json")

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

def load_long_term():
    try:
        with open(LONG_TERM_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_long_term(memory):
    with open(LONG_TERM_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)