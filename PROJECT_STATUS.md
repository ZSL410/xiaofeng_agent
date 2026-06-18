# 晓风Agent · 项目状态

## 当前版本

**v2.6.0** — 2026-06-18

## 变更日志

### v2.6.0 (2026-06-18)

**语音识别模块 (`耳朵.py`) — 修复 Windows 原生环境兼容性**

- **录音数据格式强化**：`_record_sounddevice()` 增加逐级校验链
  - 强制 int16 类型转换（支持 float32/float64 → int16 自动转换）
  - 形状归一化：(samples, 1) → (samples,)，多声道自动取第一声道
  - 静音/弱信号检测：peak < 10 时明确警告并中止
- **Vosk 识别管线升级**：`_recognize_pcm()` 重写识别流程
  - 流式分块送入音频（每块 0.5 秒），模拟实时语音流
  - 新增 `FinalResult()` 强制冲刷，避免缓冲残余丢失
  - `SetWords(True)` 启用词级信息便于调试
- **调试日志增强**：打印原始 shape/dtype、peak/rms 统计、完整 JSON 结果
- **Windows 麦克风诊断**：PortAudio 权限错误时给出明确的 Windows 隐私设置路径
- **路径兼容**：新增 `_resolve_model_path()` 处理 Windows 下中文字符路径（mklink /J 映射）

### v2.5.x (历史)

- 多后端录音架构（parec / sounddevice / mock）
- Vosk 离线中文语音识别
- WSL2 / Linux / Windows 跨平台支持
- Edge TTS 语音合成
- 财务记账 NLU 引擎
- 短期记忆 + 长期记忆框架
- 日程提醒守护线程
