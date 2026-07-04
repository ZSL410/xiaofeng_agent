# 晓风（Xiaofeng Agent）

本地运行的模块化 AI 助手，支持语音/文字交互、日程管理、财务记账和记忆系统。

**当前版本**：v3.7.0 ｜ **技术栈**：Python 3 + Ollama (qwen2.5:7b/3b) + Edge TTS + Vosk

## 项目结构

```
xiaofeng_agent/
├── 00_核心主体/          ← 脑（主入口）+ 器官（耳/嘴/手）+ 记忆系统
├── 01_工具模块/          ← 日程模块 + 财务模块 + 其他工具
├── docs/                ← 完整项目文档（架构/功能/开发/版本历史）
└── CLAUDE.md            ← AI 助手指南
```

### 拟态（桌面桌宠前端）

> **注意**：拟态（Electron 桌面桌宠前端）已从本仓库分离，现为独立项目。

- **新仓库**：[https://github.com/ZSL410/nitai](https://github.com/ZSL410/nitai)
- **本地路径**：`D:\nitai`
- **用途**：基于 Electron 的像素桌宠前端，为晓风提供可视化交互界面
- **通信方式**：通过 HTTP API（端口 5001）与晓风后端通信，独立开发、独立版本管理

## 快速开始

```bash
# 安装依赖
pip install edge-tts vosk sounddevice numpy python-docx
ollama pull qwen2.5:7b && ollama pull qwen2.5:3b

# 运行晓风
python 00_核心主体/脑.py
```

> 详细文档请从 [`docs/README.md`](docs/README.md) 开始阅读。
