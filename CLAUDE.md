# CLAUDE.md

晓风（v3.6.0）是本地运行的模块化 AI 助手，支持语音/文字交互、日程管理、财务记账和记忆系统。技术栈：Python 3 + Ollama（qwen2.5:7b/3b）+ Edge TTS + Vosk。

> **定位**：本文件是 Claude Code 的行为指南。深入了解模块实现或设计决策时，请读取 `docs/` 下的对应文件。索引入口见 [`docs/README.md`](docs/README.md)。

## 运行方式

```bash
# 启动晓风（终端 REPL）
python 00_核心主体/脑.py

# 独立运行：生成财务报告
python 01_工具模块/财务模块/generate_report.py

# 独立测试日程模块
python 01_工具模块/日程模块/schedule_module.py
```

**WSL 环境**：确保 `模型.json` 中 `ollama_host` 设为 `"http://localhost:11434"`（指向 Windows 宿主 Ollama）。若 Ollama 与 Python 同系统，设为 `null` 回退到 CLI 子进程。

**音频播放**：WSL 下通过 `cmd.exe /c start` 调用 Windows 播放器；纯 Linux 依次尝试 `mpv` → `ffplay`。

## 核心架构

### 器官隐喻

| 模块 | 路径 | 职责 |
|------|------|------|
| 脑 | `00_核心主体/脑.py` | 主入口，编排对话循环、LLM 意图路由 |
| 耳朵 | `00_核心主体/器官/耳朵.py` | Vosk 离线语音识别输入 |
| 嘴巴 | `00_核心主体/器官/嘴巴.py` | Edge TTS 语音合成输出 |
| 手 | `00_核心主体/器官/手.py` | 动态工具调用分发器（`importlib` 加载） |

### 路由层（v3.0.0 重构，LLM 驱动）

```
_route_intent() → LLM(qwen2.5:7b, 8s 超时)
  ├─ 成功 → intent {tool, action, params, confidence}
  │         ├─ confidence ≥ 0.6 → 直接执行
  │         └─ confidence < 0.6 → 向用户确认
  ├─ 7b 超时 → 降级 qwen2.5:3b（5s 超时）
  └─ 3b 也失败 → 关键词匹配兜底
```

> **重要**：路由层已从旧版"关键词匹配"重构为 LLM 驱动分类（v3.0.0 起），支持自然语言变体和口语容错。不要再使用正则去匹配命令关键词。

### 工具模块

| 模块 | 路径 | 核心文件 |
|------|------|----------|
| 日程 | `01_工具模块/日程模块/` | `schedule_module.py` — 自然语言 CRUD + 后台守护提醒（30 秒轮询） |
| 财务 | `01_工具模块/财务模块/` | `finance_module.py` — NLU 解析记账；`generate_report.py` — Word 报告生成 |

工具注册通过 `脑.py` 中的 `TOOLS` 列表声明（含 signals 示例），LLM 据此判断意图。添加新工具见 [`docs/02_开发手册/如何添加新工具.md`](docs/02_开发手册/如何添加新工具.md)。

### 记忆系统

| 层级 | 文件 | 说明 |
|------|------|------|
| 短期记忆 | `00_核心主体/记忆/短期记忆.json` | 最近 20 轮对话，每次启动恢复 |
| 长期记忆 | `00_核心主体/记忆/长期记忆.json` | 跨会话用户画像 + 行为模式 |
| 数据提炼 | `00_核心主体/记忆/数据提炼.py` | 自动发现时间/间隔/关联模式 |
| 遗忘机制 | `00_核心主体/记忆/记忆引擎.py` | decay / reject / boost 三函数 |

## 依赖

```bash
pip install edge-tts vosk sounddevice numpy python-docx
ollama pull qwen2.5:7b && ollama pull qwen2.5:3b
```

Vosk 中文模型 `vosk-model-cn-0.22`（~170MB）需手动下载放到 `00_核心主体/`，缺失时语音输入自动回退到模拟文本。

## 常见问题

| 问题 | 处理 |
|------|------|
| Ollama 连接失败 | 检查 `模型.json` 中 `ollama_host`；确保 Windows 端 `ollama serve` 运行 |
| 语音识别失败 | Vosk 模型未下载或路径错误，缺失时自动回退模拟文本 |
| 幽灵输入/残留命令 | v3.1.4 已通过 `_RESIDUAL_PATTERNS` 正则过滤。仍有漏网则在该列表添加模式 |
| 提醒时间不对 | "X 分钟后"始终是分钟；修正时说"不是X是Y"，勿重复创建 |
| 删除提醒失败 | 支持序号删除（"删除任务1"）和模糊删除（"把泡咖啡那个删掉"） |
| 智能播报不生效 | 检查 `模型.json` 中 `use_smart_reminder` 是否为 `true` |
| 启用调试日志 | `DEBUG=1 python 00_核心主体/脑.py` |
| 重置所有数据 | 删除 `todos.json`、`events.json`、`短期记忆.json`、`长期记忆.json` |

## 文档导航

详细文档见 `docs/` 目录，按主题组织：

| 目录 | 内容 |
|------|------|
| `docs/00_核心概念/` | 架构设计、技术栈、术语表 |
| `docs/01_功能手册/` | 对话系统、日程、财务、语音、记忆 |
| `docs/02_开发手册/` | 如何运行、如何测试、如何添加工具、常见问题 |
| `docs/03_版本历史/` | 变更日志总览 + 各版本详细记录 |
| `docs/04_记忆库/` | 置信度权重、记忆系统扩展路线图（用户习惯/交互模式模板已归档到 99_归档/） |
| `docs/99_归档/` | 过时文档 |

> 需要了解某个模块的实现细节、设计理由或版本变更时，**先查 `docs/README.md` 索引**，再按路径打开对应文件，不要凭记忆猜测。
