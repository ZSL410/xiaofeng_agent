# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

晓风Agent 是一个模块化的个人 AI 助手，具备语音对话、记忆管理和财务记账功能。通过本地 LLM（Ollama）进行自然语言对话，并支持离线语音识别（Vosk）和语音合成（Edge TTS）。

## 运行方式

```bash
cd 00_核心主体
python 脑.py
```

启动后交互命令：
- 直接输入文字聊天
- 输入 `v` 启动语音输入（5秒录音，Vosk 离线识别）
- 输入 `退出` 关闭程序

财务报告生成（独立运行）：
```bash
cd 01_工具模块/财务模块
python generate_report.py
```

## 核心依赖

| 组件 | Python 包 | 用途 |
|---|---|---|
| LLM 对话 | `ollama`（CLI 已安装） | 聊天与自然语言理解 |
| 语音合成 | `edge-tts` | 文字转语音输出 |
| 语音识别 | `vosk`, `sounddevice`, `numpy` | 离线语音转文字 |
| 文档报告 | `python-docx` | 生成 Word 格式财务报告 |
| 离线语音模型 | `vosk-model-cn-0.22`（170MB+）下载于 `00_核心主体/` | Vosk 中文语音模型 |

Ollama 模型依赖：`qwen2.5:7b`（聊天）和 `qwen2.5:3b`（报告生成），通过 `ollama pull` 安装。

安装 Python 依赖：
```bash
pip install edge-tts vosk sounddevice numpy python-docx
```

## 代码架构

### 架构隐喻

项目用人体器官隐喻组织代码：

```
00_核心主体/
├── 脑.py          ← 主入口，编排对话循环
├── 模型.json       ← 模型名称和麦克风设备 ID
├── 器官/
│   ├── 耳朵.py     ← 语音识别输入（Vosk 离线）
│   ├── 嘴巴.py     ← 语音合成输出（Edge TTS）
│   └── 手.py       ← 动态工具调用分发器
└── 记忆/
    ├── 记忆引擎.py  ← 短期/长期记忆持久化
    ├── 短期记忆.json ← 最近 20 轮对话历史
    └── 长期记忆.json ← 跨会话持久记忆
```

### 对话流程（`脑.py`）

1. 加载 `模型.json` 配置
2. 从 JSON 文件恢复短期和长期记忆
3. 进入 REPL 循环：
   - 读取用户输入（文字或语音 `v`）
   - 关键词检测：含"记账"/"查账"/"花了"→ 转发到财务工具模块
   - 否则 → 走 LLM 对话管线（system prompt → 最近 10 轮历史 → Ollama）
   - 响应同时输出文字和语音
   - 短期记忆追加并裁剪到 20 轮

### 工具分发（`手.py`）

使用 `importlib` 动态加载工具模块。工具注册表在代码中硬编码：

```python
TOOL_MODULES = {
    "财务": "财务模块.finance_module",
    # 预定扩展: "日程", "天气"
}
```

添加新工具只需：在 `01_工具模块/` 下创建子目录，实现 `process_command()` 函数，并在 `手.py` 的 `TOOL_MODULES` 中注册。

### 财务模块（`01_工具模块/财务模块/`）

- `finance_module.py`：NLU 解析引擎 + 记账入口。`process_command()` 从口语中提取金额/类型/来源，存入 `local_archive.json`
- `generate_report.py`：读取 `local_archive.json` → Ollama 生成总结 → 输出 Word 文档到项目根目录的 `每日财务/`
- `memory.json`：用户财务画像（偏好、常用来源）

### 日程模块（`01_工具模块/日程模块/`）

- `schedule_module.py`：日程/待办/提醒管理。`process_command()` 解析自然语言指令，支持 CRUD 日程事件和待办事项
- 模块加载时自动启动后台守护线程（daemon），每 30 秒检查到期事件和待办，通过 `嘴巴.py` 播报语音提醒
- 支持重复事件（每天/每周/每月）
- 数据文件：`events.json`（日程事件）、`todos.json`（待办事项）

### 记忆系统

- **短期记忆**：`短期记忆.json`，保留最近 20 轮对话，程序退出时持久化
- **长期记忆**：`长期记忆.json`，跨会话的用户信息容器（目前框架已就绪，LLM 尚未主动写入）

## WSL 运行注意事项

项目已适配 WSL2 环境：

- **路径**：所有硬编码 Windows 路径已替换为基于 `__file__` 的相对路径，跨平台自动适配
- **Ollama 连接**：`模型.json` 中配置了 `ollama_host`，默认 `http://localhost:11434`。在 WSL 中，这指向 Windows 宿主机的 Ollama 服务（通过 HTTP API 通信）。如果 Ollama 与 Python 在同一系统，可设置 `"ollama_host": null` 回退到 CLI 子进程调用
- **音频播放**：自动检测播放器 — WSL 下通过 `cmd.exe /c start` 调用 Windows 播放器，纯 Linux 下依次尝试 `mpv` → `ffplay`，都不存在时保存文件并提示手动播放

## 其他注意事项

- Vosk 模型需手动下载（[vosk-model-cn-0.22](https://alphacephei.com/vosk/models)）并放到 `00_核心主体/` 下，模型未就位时语音输入自动回退到模拟文本
- 项目当前无版本控制、无测试框架
- Python 依赖见 `requirements.txt`
