# PROJECT_STATUS.md — 拟态 (Mimic) Desktop Pet v3.7.1

> 最后更新：2026-06-27 | 版本：**v3.7.1** — Phase 1 交互体验优化版

## 项目定位

Windows 桌面宠物（Electron），像素小人，具备肢体动画、眼球追踪、饮食反馈、消息队列、点击反应、打字对话等丰富交互。作为晓风Agent 体系的数据入口（文件拖放）。

## 当前功能

### 视觉
- 16×20 网格像素小人：圆头、身体、可动四肢、头发、脚
- **肢体动画**：手臂（放下/挥手/伸手/指向）+ 腿（站立/蹲下/跳跃）
- **表情系统**（数据驱动）：6 种眼睛（含 v2.1 half_closed）+ 4 种嘴巴 + 腮红 + 打哈欠
- 三种体型（小50/中80/大110），切换时有**缩放过渡动画**
- 透明无边框窗口，always-on-top，像素无抗锯齿
- 🆕 **点击穿透**：`setIgnoreMouseEvents` 鼠标事件穿透到下层窗口，日常不遮挡操作

### 动画（FSM 6 状态）
| 状态 | 动画 |
|------|------|
| idle | 呼吸起伏 + 眨眼 + **眼球追踪鼠标** + **30s 无操作打哈欠** + **60s→sleepy / 120s→sleeping（含 Zzz 气泡 + 鼠标唤醒伸懒腰）** |
| working | **张嘴吃文件**（nom 节奏 ~400ms）+ 碎屑飞向嘴巴 + 手臂伸手 + **nom 音效** |
| happy | 弹跳 + 双臂举起挥舞 + **chirp 音效 + 心形粒子** + 2s 自动回 idle |
| surprised | 后退 + 双臂张开 + 抖动衰减 + **boing 音效** + 2.5s 回 idle |
| alert | 蹲下蓄力 → 跳起 + 单臂指天 + 星星眼 + **星形粒子** + 常驻 |
| **bongo** | 🆕 坐姿 + 双手交替拍击 + **bongo 音效** + 10s 自动回 idle（输入 "bongo" 触发）|
| **listening** | 🆕 戴耳机 + 闭眼 + 身体摇摆 + **音符持续飘出**（输入 "听歌" / "music" 触发）|

### 交互
- **眼球追踪**：idle 时瞳孔跟随鼠标位置
- **点击反应**：摸头→害羞笑+弹跳+**chirp音效** / 拍身体→跳起+抗议+**boing音效** / 双击→旋转弹跳+**火花粒子+boing音效**
- **打字对话**：按 Enter → 弹出输入框 → Mock 回复（支持中文关键词匹配），输入 "bongo" 触发彩蛋，可选后端 `/api/chat`
- **Pointer Capture 拖拽** 🆕：更可靠的窗口拖拽，不再丢失 mouseup
- 鼠标拖拽移动窗口 + 右键菜单切换体型
- 文件/文件夹拖放 → 递归复制到目标目录 → **吃饭动画 + 进度条**
- 🆕 **穿透模式**：托盘菜单 / 右键菜单 / `Ctrl+Shift+P` 全局快捷键切换，穿透时鼠标直达下层窗口
- 🆕 **听歌模式**：输入"听歌"/"music"→戴耳机闭眼摇摆+音符飘出；输入"停止"/"stop"退出
- 🆕 **音乐自动检测**：后台 3 秒轮询进程列表，检测到 Spotify/QQ音乐/网易云等自动触发听歌动画

### 气泡系统 v2
- **消息队列**：多条消息顺序显示，不截断
- **嘴边定位**：气泡从角色嘴巴上方"长"出，带小尾巴 → 嘴巴
- **进度条**：文件复制时显示 `nom-progress` 进度条
- **思考气泡**："…" 动画（chat 模式）
- **Zzz 气泡** 🆕：sleeping 时偶发显示

### 音效系统 🆕
- **Web Audio API 合成**：无需外部音频文件
- chirp（开心）| yawn（打哈欠/伸懒腰）| boing（点击/弹跳）| nom（进食）| bongo（敲击）
- 懒初始化 AudioContext，失败静默降级

### 粒子特效 🆕
- 心形 ❤（happy）| 星形 ✦（alert/bongo）| 火花 ·（双击）| 碎屑（eating）
- 🆕 **音符 ♪**（bongo 敲击时飘出）| 🆕 **雨滴 💧**（idle 超过 10 秒后偶尔下落）
- 物理模拟：飘升 + 漂移 + 淡出

### 位置记忆 🆕
- 窗口移动/关闭时自动保存位置到 `config/position.json`
- 启动时恢复上次位置

### 后端集成
- 文件复制完成 → POST `/api/file/process`
- Chat → POST `/api/chat`（可选，无后端时 Mock）
- `config.json` 配置开关

## 代码结构

```
拟态开发版/
├── main.js                         # 主进程入口
├── main/{config,window,tray,ipc-handlers,position-store}.js
├── config/{config.json,position.json}
├── renderer/
│   ├── index.html                  # HTML + CSS + 脚本加载
│   ├── app.js                      # 引导入口 (~70行)
│   ├── mimic.js                    # 全局命名空间 + Anim 状态
│   ├── layout.js                   # 动态窗口布局 + 缩放过渡
│   ├── audio.js                    # 🆕 Web Audio 音效合成
│   ├── fsm/
│   │   ├── fsm-core.js             # FSM 引擎
│   │   ├── expressions.js          # 表情定义表 (8 expressions)
│   │   └── states/{idle,working,happy,surprised,alert,bongo}.js
│   ├── rendering/
│   │   ├── pixel-character.js      # 像素小人（身体+四肢+脸）
│   │   └── particles.js            # 🆕 粒子特效引擎
│   ├── bubble/
│   │   └── bubble.js               # 消息队列 + 嘴边定位
│   └── interaction/
│       ├── drag.js                 # 🆕 Pointer Capture 拖拽 + 位置保存
│       ├── contextmenu.js          # 右键菜单
│       ├── drop.js                 # 文件拖放 + 后端 API
│       ├── eye-tracking.js         # 鼠标眼球追踪
│       ├── click.js                # 身体部位点击反应 (+音效)
│       └── chat.js                 # Enter 对话 + Mock API + bongo 触发
└── config.json
```

## 运行

```bash
cd 拟态开发版 && npm start          # Windows 原生
cd /mnt/d/xiaofeng_agent/拟态开发版 && npm run start:wsl  # WSL2
```

## 变更日志

### v3.7.1 (2026-06-27) — Phase 1 交互体验优化
- 🎯 **眼球跟随鼠标增强**：`interaction/eye-tracking.js` 重写
  - 基于角色头部中心（而非窗口中心）计算偏移量
  - 眼珠移动范围扩大至 ±2 格水平、±1 格垂直（严格限制在头部区域内）
  - 鼠标离开窗口时眼珠平滑回到中心
  - 配合 `pixel-character.js` 支持浮点偏移渲染
- 🖱️ **点击部位反馈升级**：`interaction/click.js` 增强
  - 点击头部 → `happy` 状态（弹跳 + 微笑 + chirp 音效 + 心形粒子），1.5s 后恢复 idle
  - 点击身体 → `surprised` 状态（眼睛睁大 + 嘴巴张开 + boing 音效 + 火花粒子），1.5s 后恢复 idle
  - 双击 → 旋转弹跳 + 火花粒子 + 2.5s happy
- 🔋 **低功耗空闲模式**：`app.js` 新增帧率控制
  - 角色处于 `idle` 且连续 30 秒无鼠标移动 → 降至 15fps（通过帧计数跳过绘制）
  - 鼠标移动立即恢复 60fps 全速绘制
  - 切换体型时 `mousemove` 事件自动重置空闲计时器
- 📋 版本号升至 v3.7.1

### v2.5.0 (2026-06-16)
- 🆕 **音乐自动检测**：`main/music-detector.js` 后台进程轮询
  - 每 3 秒扫描系统进程列表（`tasklist` / `ps`），匹配配置的播放器名
  - 检测到音乐 → `music-playing` IPC → 自动进入 listening 状态（仅 idle 时）
  - 音乐停止 → `music-stopped` IPC → 自动退出到 idle（仅自动触发时）
  - 手动输入"听歌"进入的 listening 不受自动退出影响
- 📋 `config.json` 新增：`musicDetect`（开关），`musicCheckInterval`（秒），`musicPlayers`（播放器列表）
- 📋 默认监控：spotify, qqmusic, cloudmusic（网易云）, netease, kwmusic（酷我）, kugou（酷狗）
- 🎨 `listening.js` 支持显示检测到的播放器名称

### v2.4.0 (2026-06-16)
- 🆕 **听歌模式 (listening)**：FSM 第 7 状态
  - 触发：聊天输入 `听歌` / `music` / `耳机`
  - 动画：耳机戴在头上 + 闭眼(closed_arch) + 身体摇摆 + 手臂微动
  - 特效：每 600ms 飘出音符粒子
  - 退出：输入 `停止` / `stop` 回到 idle
- 🎨 `pixel-character.js` 新增 `drawHeadphones()` — 头梁+耳罩像素绘制
- 🎨 `expressions.js` 新增 `listening` 表情（closed_arch + 腮红）
- 🎨 `chat.js` 新增"停止"关键词，可同时退出 listening/bongo 状态

### v2.3.0 (2026-06-16)
- 🆕 **音符粒子 ♪**：`particles.js` 新增 `drawMusicNote()`，Bongo Cat 敲击时每 4 拍飘出 2 个音符
- 🆕 **雨滴粒子 💧**：`particles.js` 新增 `drawRaindrop()`，idle 超 10 秒后 ~0.08%/帧概率下落
- 粒子系统扩展至 6 种类型：heart | star | spark | eat | **note** | **rain**

### v2.2.0 (2026-06-16)
- 🆕 **点击穿透功能**（`setIgnoreMouseEvents(true, { forward: true })`）
  - 穿透模式下鼠标事件直达下层窗口，不遮挡日常操作
  - 三种切换方式：托盘菜单 checkbox / 右键菜单 checkbox / `Ctrl+Shift+P` 全局快捷键
  - 全局快捷键在主进程注册（`globalShortcut`），确保穿透模式下仍可切换回交互模式
  - 配置文件 `config.json` 支持 `passthrough` 选项，控制启动时是否默认穿透
  - 穿透状态在托盘菜单和右键菜单中实时同步显示

### v2.1.4 (2026-06-16)
- 🔧 修复：拖动窗口时 `TypeError: conversion failure` 崩溃
  - `drag.js`：移除 `setPointerCapture`（WSL2 兼容问题），改用 `document` 级追踪
  - `drag.js`：`Math.round()` 强制整数 dx/dy + NaN 回退 0 + try-catch 包裹 IPC send
  - `ipc-handlers.js`：`window-drag` / `resize-window` handler 支持 4 种参数格式自动识别
    （flat args / 单参数 / 对象 `{dx,dy}` / 数组 `[dx,dy]`）
  - `ipc-handlers.js`：`Math.round` 最终坐标 + try-catch 包裹 `setPosition` / `setSize`

### v2.1.3 (2026-06-16)
- 🔧 修复：`TypeError: conversion failure` — 所有 IPC 通道从对象参数改为扁平参数
  - `window-drag`: `{dx,dy}` → `dx,dy` 分离传递，消除对象序列化边缘情况
  - `resize-window`: `{w,h}` → `w,h` 分离传递
  - 主进程 handler 全部去掉参数解构，改为直接接收

### v2.1.2 (2026-06-16)
- 🔧 修复：所有 IPC 通道类型安全加固
  - `window-drag`：主进程 `Number(dx/dy)` + `isNaN` 守卫；渲染进程 `Number(e.screenX/Y)` 强制转换
  - `resize-window`：主进程 `Number(w/h)` + `isNaN/<=0` 守卫；渲染进程发送前验证
  - `set-pet-size`：主进程 `Number(petSize)` + `isNaN/<=0` 守卫
  - `size-changed`：渲染进程接收端 `Number(size)` + `isNaN` 守卫
  - `layout.js applySize()`：入口 `Number(size)` + `isNaN` 守卫

### v2.1.1 (2026-06-16)
- 🔧 修复：无法关闭应用的严重问题 — 添加了三种退出方式
  - 右键菜单新增"退出"选项（之前只有体型切换）
  - 新增 `Ctrl+Q` 键盘快捷键退出
  - 统一托盘/右键/快捷键退出逻辑到 `quitApp()`
- 🔧 修复：`window.js` close 事件支持 `forceQuit` 标志区分隐藏/退出
- 🔧 修复：`mimic.js` 版本号硬编码错误（2.0.0 → 2.1.1）

### v2.1.0 (2026-06-16)
- 🆕 Web Audio 合成音效系统（chirp/yawn/boing/nom/bongo）
- 🆕 粒子特效引擎（heart/star/spark/eat）
- 🆕 睡眠序列：60s→sleepy, 120s→sleeping, 鼠标唤醒
- 🆕 Pointer Capture 拖拽
- 🆕 窗口位置记忆
- 🆕 Bongo Cat 敲击彩蛋（输入 "bongo" 触发）
- 🆕 half_closed 眼型（sleepy 表情）
- 改进：idle 状态重构为子状态管理
- 改进：click.js 集成音效和粒子触发
- 改进：happy 粒子效果

### v2.0.0 (2026-06-16)
- 重写版：模块化架构，FSM 5 状态
- 像素角色 + 肢体动画 + 表情系统
- 眼球追踪 + 点击反应 + 打字对话
- 气泡系统 v2 + 消息队列 + 嘴边定位
- 文件拖放 + 后端通知

## 下一步 (v3.8+)

- [x] 眼球追踪增强（头部中心计算）✅ v3.7.1
- [x] 点击身体→surprised 状态 ✅ v3.7.1
- [x] 低功耗空闲模式（30s→15fps）✅ v3.7.1
- [x] 点击穿透（setIgnoreMouseEvents）✅ v2.2.0
- [x] 更多粒子形状（音符、雨滴）✅ v2.3.0
- [ ] 情绪状态机扩展（无聊、兴奋）
- [ ] 定时提醒集成（连接日程模块）
- [ ] 主题切换（暗色模式）
