"""
耳朵.py — 语音识别模块

录音后端优先级（自动检测）:
  1. parec (PulseAudio)     — WSL2 + WSLg 最可靠方案，零额外 Python 依赖
  2. sounddevice (PortAudio) — 跨平台，原生 Linux / Windows 主流方案
  3. mock                   — 所有后端不可用时的模拟回退

Vosk 模型: 自动相对于本文件定位到 ../../vosk-model-cn-0.22/

调试: 设置环境变量 XIAOFENG_DEBUG=1 启用详细日志
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

# ============================================================================
# 配置常量
# ============================================================================
SAMPLE_RATE = 16000          # Vosk 离线模型要求 16 kHz
CHANNELS = 1                 # 单声道
RECORD_SECONDS = 5           # 默认录音时长

# Vosk 模型路径 — 相对于器官/ → 00_核心主体/
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(_BASE, "vosk-model-cn-0.22")

# ============================================================================
# 日志工具
# ============================================================================
_VERBOSE = os.environ.get("XIAOFENG_DEBUG", "") != ""


def _log(msg):
    if _VERBOSE:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[耳朵 {ts}] {msg}")


def _warn(msg):
    print(f"[耳朵 ⚠] {msg}")


# ============================================================================
# 模型加载（模块级单例，懒加载）
# ============================================================================
_model_loaded = False
_model = None          # vosk.Model 实例，创建新 KaldiRecognizer 时复用


def _load_vosk_model():
    """加载 Vosk 离线语音识别模型。返回 (是否成功, 错误信息)。"""
    global _model_loaded, _model

    if _model_loaded:
        return True, None

    # 1. 检查模型目录
    if not os.path.isdir(MODEL_PATH):
        msg = (
            f"Vosk 模型目录不存在: {MODEL_PATH}\n"
            "请下载 vosk-model-cn-0.22:\n"
            "  https://alphacephei.com/vosk/models\n"
            f"并解压到: {_BASE}/"
        )
        _warn(msg)
        return False, msg

    # 2. 检查模型完整性
    required_dirs = ["am", "conf", "graph", "ivector"]
    missing = [d for d in required_dirs if not os.path.exists(os.path.join(MODEL_PATH, d))]
    if missing:
        msg = f"Vosk 模型不完整，缺少子目录: {missing}\n请重新下载完整模型并解压。"
        _warn(msg)
        return False, msg

    # 3. 导入并加载
    try:
        import vosk
    except ImportError:
        msg = "vosk 未安装。请在 venv 中运行: pip install vosk"
        _warn(msg)
        return False, msg

    try:
        vosk.SetLogLevel(-1)              # 抑制 Vosk 内部调试输出
        _model = vosk.Model(MODEL_PATH)
        _model_loaded = True
        _log(f"Vosk 模型加载成功 ({MODEL_PATH})")
        return True, None
    except Exception as e:
        msg = f"Vosk 模型加载失败: {e}"
        _warn(msg)
        return False, msg


# ============================================================================
# 录音后端检测
# ============================================================================
_AUDIO_BACKEND = None   # "parec" | "sounddevice" | "mock"


def _detect_audio_backend():
    """检测可用的录音后端，按优先级返回并缓存结果。"""
    global _AUDIO_BACKEND

    if _AUDIO_BACKEND is not None:
        return _AUDIO_BACKEND

    _log("检测录音后端...")

    # --- 1) parec (PulseAudio) ---
    if shutil.which("parec"):
        pulse_server = os.environ.get("PULSE_SERVER", "")
        if pulse_server and os.path.exists(pulse_server.replace("unix:", "")):
            _AUDIO_BACKEND = "parec"
            _log("后端: parec (PulseAudio via WSLg) ✓")
            return _AUDIO_BACKEND
        elif shutil.which("pulseaudio"):
            # 可能在原生 Linux 上运行 PulseAudio
            _AUDIO_BACKEND = "parec"
            _log("后端: parec (PulseAudio local) ✓")
            return _AUDIO_BACKEND
        else:
            _log("parec 存在但 PULSE_SERVER 不可达，跳过")
    else:
        _log("parec 未找到")

    # --- 2) sounddevice (PortAudio) ---
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        if devices:
            input_devs = [d for d in devices if d.get("max_input_channels", 0) > 0]
            if input_devs:
                _AUDIO_BACKEND = "sounddevice"
                _log(f"后端: sounddevice ✓ ({len(input_devs)} 个输入设备)")
                return _AUDIO_BACKEND
        _log("sounddevice 可用但未找到输入设备")
    except ImportError:
        _log("sounddevice 未安装")
    except Exception as e:
        _log(f"sounddevice 检测异常: {e}")

    # --- 3) 无可用后端 → 模拟 ---
    _AUDIO_BACKEND = "mock"
    _warn("未找到可用录音后端，语音输入将使用模拟模式。")
    _warn("WSL2 安装指南（系统包）:")
    _warn("  sudo apt install pulseaudio-utils    # parec + pactl")
    _log("后端: mock (模拟)")
    return _AUDIO_BACKEND


# ============================================================================
# 后端 1: parec 录音
# ============================================================================

def _record_parec():
    """
    使用 parec 通过 WSLg PulseAudio 录制原始 PCM。
    这是 WSL2 下最可靠的方案，因为 parec 直接与 PulseAudio 通信，
    不需要 PortAudio 的 Pulse 后端支持。
    """
    pulse_server = os.environ.get("PULSE_SERVER", "")
    env = {**os.environ, "PULSE_SERVER": pulse_server}

    # 诊断：列出可用录音源
    if _VERBOSE and shutil.which("pactl"):
        try:
            result = subprocess.run(
                ["pactl", "get-default-source"],
                capture_output=True, text=True, timeout=5, env=env
            )
            _log(f"PulseAudio 默认录音源: {result.stdout.strip()}")
        except Exception as e:
            _log(f"pactl get-default-source 失败: {e}")

    print("🎤 正在听，请说话...（说完自动停止）")

    # parec 输出到 stdout（默认行为，无文件参数时写 stdout）
    parec = subprocess.Popen(
        ["parec", "--format=s16le", "--rate=16000",
         "--channels=1", "--latency-msec=50"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )

    # 收集音频数据
    audio_data = b""
    start_time = time.time()
    try:
        while time.time() - start_time < RECORD_SECONDS:
            chunk = parec.stdout.read(640)   # ~20ms @ 16kHz 16-bit mono
            if chunk:
                audio_data += chunk
            if parec.poll() is not None:
                break
    finally:
        parec.terminate()
        try:
            _stderr_output = parec.stderr.read().decode("utf-8", errors="replace").strip()
            if _stderr_output and _VERBOSE:
                _log(f"parec stderr: {_stderr_output}")
        except Exception:
            pass
        try:
            parec.wait(timeout=2)
        except subprocess.TimeoutExpired:
            parec.kill()

    actual_sec = len(audio_data) / (SAMPLE_RATE * 2)
    _log(f"parec 录制: {len(audio_data)} bytes ({actual_sec:.1f}s)")

    if len(audio_data) < SAMPLE_RATE * 2 * 1:   # 不足 1 秒 → 可能静音/未说话
        _warn(f"录音数据过短 ({actual_sec:.1f}s)。")
        _warn("请检查 Windows 麦克风隐私设置是否允许桌面应用访问，")
        _warn("以及 WSLg 是否能监听麦克风。")
        return None

    return audio_data


# ============================================================================
# 后端 2: sounddevice 录音
# ============================================================================

def _record_sounddevice():
    """
    使用 sounddevice (PortAudio) 录音。
    在原生 Linux / Windows 上效果最佳；
    在 WSL2 中仅当 PortAudio 编译了 PulseAudio 后端时才可用。
    """
    import sounddevice as sd
    import numpy as np

    # 枚举输入设备用于调试
    if _VERBOSE:
        devices = sd.query_devices()
        _log(f"sounddevice 设备列表 ({len(devices) if devices else 0} 个):")
        if devices:
            for i, d in enumerate(devices):
                _log(f"  [{i}] {d['name']} (in={d.get('max_input_channels', 0)}, "
                     f"out={d.get('max_output_channels', 0)}, "
                     f"sr={d.get('default_samplerate', 0):.0f})")

    # 查找默认输入设备
    device_idx = None
    try:
        default = sd.query_devices(kind='input')
        device_idx = default.get('index') if default else None
        _log(f"默认输入: [{device_idx}] {default.get('name', '?') if default else '无'}")
    except Exception as e:
        _log(f"查询默认输入设备失败: {e}")

    # WSL2 特殊处理：优先匹配名字中包含 pulse/RDP 的设备
    if device_idx is None:
        try:
            all_devs = sd.query_devices()
            if all_devs:
                for i, d in enumerate(all_devs):
                    name = d.get("name", "").lower()
                    if d.get("max_input_channels", 0) > 0 and (
                        "pulse" in name or "remote" in name or "rdp" in name
                    ):
                        device_idx = i
                        _log(f"WSL2 匹配 PulseAudio 设备: [{i}] {d['name']}")
                        break
        except Exception:
            pass

        # 最后手段：使用第一个有输入通道的设备
        if device_idx is None:
            try:
                all_devs = sd.query_devices()
                if all_devs:
                    for i, d in enumerate(all_devs):
                        if d.get("max_input_channels", 0) > 0:
                            device_idx = i
                            _log(f"使用第一个输入设备: [{i}] {d['name']}")
                            break
            except Exception:
                pass

    if device_idx is None:
        _warn("sounddevice: 未找到任何输入设备")
        return None

    print("🎤 正在听，请说话...（说完自动停止）")

    try:
        recording = sd.rec(
            int(RECORD_SECONDS * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype='int16',
            device=device_idx,
            blocking=True
        )
        sd.wait()
        audio_data = np.asarray(recording, dtype='int16').tobytes()
        actual_sec = len(audio_data) / (SAMPLE_RATE * 2)
        _log(f"sounddevice 录制: {len(audio_data)} bytes ({actual_sec:.1f}s)")
        return audio_data
    except sd.PortAudioError as e:
        _warn(f"sounddevice PortAudio 错误: {e}")
        return None
    except Exception as e:
        _warn(f"sounddevice 录音失败: {e}")
        return None


# ============================================================================
# Vosk 语音识别
# ============================================================================

def _recognize_pcm(pcm_data):
    """
    将原始 PCM (s16le, 16kHz, mono) 送入 Vosk 进行识别。

    参数:
        pcm_data: bytes — 原始 PCM 音频数据

    返回:
        (ok: bool, text: str)
        ok=True  → text 为识别到的文字
        ok=False → text 为错误描述
    """
    ok, err = _load_vosk_model()
    if not ok:
        return False, err

    # 数据校验
    if len(pcm_data) < SAMPLE_RATE * 2 * 1:  # 不足 1 秒
        return False, "录音时长不足"

    # 每次识别创建新的 KaldiRecognizer，避免跨录音状态污染
    try:
        import vosk
        recognizer = vosk.KaldiRecognizer(_model, SAMPLE_RATE)
        recognizer.SetWords(False)
    except Exception as e:
        _warn(f"创建识别器失败: {e}")
        return False, "识别器创建失败"

    try:
        if recognizer.AcceptWaveform(pcm_data):
            result = json.loads(recognizer.Result())
            text = result.get("text", "").strip()
            _log(f"识别(终态): '{text}'")
            return (True, text) if text else (False, "未检测到语音")

        # 尝试获取部分结果
        partial = json.loads(recognizer.PartialResult())
        text = partial.get("partial", "").strip()
        _log(f"识别(部分): '{text}'")
        return (True, text) if text else (False, "未检测到语音")
    except Exception as e:
        _warn(f"Vosk 识别异常: {e}")
        return False, f"识别异常: {e}"


# ============================================================================
# 公共 API
# ============================================================================

def listen_once():
    """
    录制一段语音并返回识别文本。

    返回:
        str — 识别到的文字，或以下特殊值:
        "模拟语音输入"   — 所有后端不可用或模型未加载
        "录音失败"       — 录音过程出错
        "语音识别失败"   — 录到了但未识别出有效语音
    """
    backend = _detect_audio_backend()
    _log(f"使用后端: {backend}")

    # ---- 录音 ----
    pcm_data = None

    if backend == "parec":
        pcm_data = _record_parec()
    elif backend == "sounddevice":
        pcm_data = _record_sounddevice()
    else:   # "mock" 或未知
        return "模拟语音输入"

    if pcm_data is None:
        _warn("录音失败 — 未获取到音频数据")
        return "录音失败"

    # ---- 识别 ----
    ok, text = _recognize_pcm(pcm_data)

    if ok:
        print(f"📝 识别到文字: {text}")
        return text
    else:
        _log(f"识别失败: {text}")
        return "语音识别失败"


# ============================================================================
# 诊断工具
# ============================================================================

def diagnose():
    """打印完整诊断信息，帮助排查音频和模型问题。"""
    print("=" * 60)
    print("🔍 晓风 · 语音模块诊断")
    print("=" * 60)

    # 环境
    print(f"\n📋 环境:")
    print(f"  平台: {sys.platform}")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  PULSE_SERVER: {os.environ.get('PULSE_SERVER', '(未设置)')}")
    print(f"  DISPLAY: {os.environ.get('DISPLAY', '(未设置)')}")

    # Vosk 模型
    print(f"\n📦 Vosk 模型:")
    print(f"  路径: {MODEL_PATH}")
    if os.path.isdir(MODEL_PATH):
        import glob
        for sub in ["am", "conf", "graph", "ivector"]:
            subpath = os.path.join(MODEL_PATH, sub)
            status = "✓" if os.path.exists(subpath) else "✗"
            print(f"  {status} {sub}/")
        ok, err = _load_vosk_model()
        if ok:
            print(f"  加载: ✓ 成功")
        else:
            print(f"  加载: ✗ {err}")
    else:
        print(f"  ✗ 模型目录不存在")

    # 系统工具
    print(f"\n🔧 系统录音工具:")
    for tool in ["parec", "pactl"]:
        path = shutil.which(tool)
        status = f"✓ ({path})" if path else "✗ 未安装"
        print(f"  {tool}: {status}")

    # Python 包
    print(f"\n🐍 Python 音频包:")
    for pkg, desc in [("sounddevice", "PortAudio 绑定"), ("vosk", "语音识别"),
                       ("numpy", "数值计算")]:
        try:
            m = __import__(pkg)
            ver = getattr(m, "__version__", "?")
            print(f"  {pkg}: ✓ {ver} — {desc}")
        except ImportError:
            print(f"  {pkg}: ✗ 未安装 — {desc}")

    # 后端检测
    print(f"\n🔌 录音后端检测:")
    _AUDIO_BACKEND = None   # 重置以强制重新检测
    backend = _detect_audio_backend()
    print(f"  当前后端: {backend}")
    if backend == "mock":
        print(f"\n  要启用录音，请安装 pulseaudio-utils:")
        print(f"    sudo apt install pulseaudio-utils")

    # 如果在 WSL2 且 parec 可用，列出 PulseAudio 源
    if backend == "parec" and shutil.which("pactl"):
        env = {**os.environ, "PULSE_SERVER": os.environ.get("PULSE_SERVER", "")}
        try:
            result = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True, text=True, timeout=5, env=env
            )
            if result.returncode == 0 and result.stdout.strip():
                print(f"\n📡 PulseAudio 录音源:")
                for line in result.stdout.strip().split("\n")[:5]:
                    print(f"  {line}")
        except Exception:
            pass

    print("\n" + "=" * 60)


# ============================================================================
# 独立运行入口
# ============================================================================
if __name__ == "__main__":
    os.environ["XIAOFENG_DEBUG"] = "1"
    diagnose()
    print("\n🧪 录音测试...\n")
    result = listen_once()
    print(f"\n结果: '{result}'")
