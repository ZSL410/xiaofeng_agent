"""
晓风.py — 晓风Agent 启动入口

这是 脑.py 的兼容入口别名，方便记忆和启动。
"""

import os
import sys

# 确保能找到同级模块
_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from 脑 import main

if __name__ == "__main__":
    main()
