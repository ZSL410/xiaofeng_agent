"""
耳朵.py — 语音识别模块  v3.7.7

录音后端优先级（自动检测）:
  1. parec (PulseAudio)     — WSL2 + WSLg 最可靠方案，零额外 Python 依赖
  2. sounddevice (PortAudio) — 跨平台，原生 Linux / Windows 主流方案
  3. mock                   — 所有后端不可用时的模拟回退

Vosk 模型: 自动相对于本文件定位到 ../vosk-model-cn-0.22/

v3.7.7 改进:
  - AGC (自动增益控制): 根据语音段 RMS 自动归一化音量,减少音量波动
  - 动态 VAD 阈值: 根据环境噪声自动校准阈值,适应安静/嘈杂环境
  - 音频预处理: DC 偏移去除,提升识别稳定性
  - 无语音超时: 启动后 N 秒未检测到语音则提前中止,避免空等
  - 后处理纠错: 常见 Vosk 误识别自动修正(教我→叫我 等)
  - 增强调试日志: 能量/增益/阈值等关键参数可观测

v3.7.2 改进:
  - VAD (Voice Activity Detection): 基于 RMS 能量的动态录音,不再使用固定 5s 时长
  - 静音检测自动停止: 连续 SILENCE_DURATION 秒低于阈值则停止录音
  - 最大录音时长保护: MAX_RECORD_SECONDS 超时强制停止,返回提示
  - 流式录音: sounddevice 后端改用 InputStream 非阻塞流式采集,降低 CPU 占用

v2.6.0 改进:
  - 录音数据严格校验:int16 类型、形状归一化、静音检测
  - 流式分块送入 Vosk + FinalResult() 强制冲刷，提升识别率
  - 详细的调试日志(shape/dtype/peak/rms/JSON 结果）
  - Windows 麦克风权限问题的明确诊断提示

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

# ---- VAD (Voice Activity Detection) 配置 ----
SILENCE_THRESHOLD = 500      # 默认 RMS 阈值（动态校准前使用）
SILENCE_DURATION = 1.5       # 连续静音多少秒后自动停止录音
MAX_RECORD_SECONDS = 25      # 最大录音时长 (超时强制停止,避免挂死)
CHUNK_DURATION = 0.1         # 每个音频块的时长 (秒),用于流式 VAD 检测

# ---- 动态 VAD 阈值校准 (v3.7.7) ----
VAD_CALIBRATION_SEC = 0.3    # 录音开始后多少秒用于测量背景噪声
VAD_NOISE_MULTIPLIER = 2.5   # 阈值 = 噪声底噪 × 倍数
VAD_MIN_THRESHOLD = 100      # 最安静环境下的最低阈值（防止过度敏感）

# ---- AGC 自动增益控制 (v3.7.7) ----
ENABLE_AGC = True            # 是否启用自动增益控制
AGC_TARGET_RMS = 2000        # 目标 RMS 能量值
AGC_CALIBRATION_SEC = 0.5    # 前 N 秒用于计算增益因子
AGC_MIN_GAIN = 0.3           # 最小增益倍数（防止过度放大噪声）
AGC_MAX_GAIN = 5.0           # 最大增益倍数（防止削波）
AGC_TOLERANCE_RATIO = 0.5    # RMS 在 target×(1±tolerance) 内则跳过 AGC

# ---- 无语音超时 (v3.7.7) ----
NO_VOICE_TIMEOUT = 3.0       # 启动后 N 秒未检测到任何语音则中止录音

# ---- 音频预处理 (v3.7.7) ----
ENABLE_DC_REMOVAL = True     # 是否去除 DC 偏移

# ---- 后处理纠错 (v3.7.7) ----
# 常见 Vosk 误识别 → 正确文字（按优先级排序，长词优先）
POST_CORRECTIONS = {
    "教一下": "叫一下",
    "教醒我": "叫醒我",
    "教醒": "叫醒",
    "教我": "叫我",
    "教": "叫",
}

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
_model = None          # vosk.Model 实例,创建新 KaldiRecognizer 时复用


def _resolve_model_path():
    """
    将模型路径转换为 Vosk C++ 库可接受的格式。

    Vosk 底层 C++ 代码在 Windows 上使用窄字符 API(CreateFileA 等），
    无法处理含中文/Unicode 的路径。这里通过子进程调用 Windows 的
    ``mklink /J`` 创建目录连接，映射到一个纯 ASCII 路径。
    """
    resolved = os.path.abspath(MODEL_PATH)

    # Linux / WSL / macOS — 原生支持 UTF-8 路径，无需处理
    if sys.platform != "win32":
        return resolved

    # 路径全为 ASCII → 直接使用
    try:
        resolved.encode("ascii")
    except UnicodeEncodeError:
        pass
    else:
        return resolved

    # --- Windows 下路径含非 ASCII 字符 → 创建目录连接 ---
    import tempfile

    link_dir = os.path.join(tempfile.gettempdir(), "xf_vosk_model")
    if os.path.exists(link_dir):
        # 连接已存在：验证是否指向正确位置
        try:
            # 通过读取一个已知文件判断连接是否有效
            test_file = os.path.join(link_dir, "am", "final.mdl")
            if os.path.exists(test_file):
                _log(f"Vosk 模型路径映射: {resolved} → {link_dir} (已存在)")
                return link_dir
        except Exception:
            pass
        # 连接损坏，重建
        _log("移除旧的模型连接...")
        try:
            subprocess.run(
                ["cmd", "/c", "rmdir", link_dir],
                capture_output=True, timeout=10
            )
        except Exception:
            pass
        # 如果 rmdir 失败，尝试删除目录（可能是真实目录而非连接）
        if os.path.exists(link_dir):
            try:
                import shutil as _shutil
                _shutil.rmtree(link_dir, ignore_errors=True)
            except Exception:
                pass

    # 创建目录连接（Junction — 不需要管理员权限，仅限本机目录）
    _log(f"创建 Vosk 模型路径映射: {resolved} → {link_dir}")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", link_dir, resolved],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        _warn(f"mklink /J 失败 (rc={result.returncode}): {result.stderr.strip()}")
        # 回退：直接返回原始路径，让 Vosk 自己去处理（可能失败）
        return resolved

    if os.path.isdir(link_dir):
        _log(f"Vosk 模型路径映射成功: {link_dir}")
        return link_dir
    else:
        _warn("目录连接创建后不可用,回退到原始路径")
        return resolved


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
        msg = f"Vosk 模型不完整,缺少子目录: {missing}\n请重新下载完整模型并解压."
        _warn(msg)
        return False, msg

    # 3. 导入并加载
    try:
        import vosk
    except ImportError:
        msg = "vosk 未安装.请运行: pip install vosk"
        _warn(msg)
        return False, msg

    # 4. 解决 Windows 下非 ASCII 路径兼容问题
    model_path = _resolve_model_path()

    try:
        vosk.SetLogLevel(-1)              # 抑制 Vosk 内部调试输出
        _model = vosk.Model(model_path)
        _model_loaded = True
        _log(f"Vosk 模型加载成功 ({model_path})")
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
            _log("parec 存在但 PULSE_SERVER 不可达,跳过")
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
    _warn("未找到可用录音后端,语音输入将使用模拟模式.")
    _warn("WSL2 安装指南(系统包):")
    _warn("  sudo apt install pulseaudio-utils    # parec + pactl")
    _log("后端: mock (模拟)")
    return _AUDIO_BACKEND


# ============================================================================
# VAD (Voice Activity Detection) 工具
# ============================================================================

def _compute_rms(audio_bytes: bytes) -> float:
    """
    计算 PCM 音频数据的 RMS（均方根）能量值。

    参数:
        audio_bytes: s16le 原始 PCM 字节

    返回:
        float — RMS 值,用于与 SILENCE_THRESHOLD 比较
    """
    import numpy as np
    samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float64)
    if len(samples) == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples ** 2)))


def _rms_from_array(samples) -> float:
    """
    从 numpy 数组计算 RMS 能量值。

    参数:
        samples: numpy 数组 (int16)

    返回:
        float — RMS 值
    """
    import numpy as np
    arr = np.asarray(samples, dtype=np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr ** 2)))


# ============================================================================
# 音频预处理工具 (v3.7.7)
# ============================================================================

def _apply_dc_removal(samples):
    """
    去除 DC 偏移 —— 最简单的"高通滤波"，移除麦克风的直流偏置。
    对于语音识别，DC 偏移会干扰 VAD 的能量计算。

    参数:
        samples: numpy 数组 (int16 或 float64)

    返回:
        numpy 数组 (int16)
    """
    import numpy as np
    arr = samples.astype(np.float64)
    arr -= np.mean(arr)
    return np.clip(arr, -32768, 32767).astype(np.int16)


def _apply_agc(samples, calibration_sec=None):
    """
    自动增益控制：将音频 RMS 归一化到目标水平。

    在前 calibration_sec 秒内计算平均 RMS，据此计算增益因子。
    如果 RMS 已处于合理范围（AGC_TOLERANCE_RATIO），跳过放大避免引入噪声。

    参数:
        samples:    numpy 数组 (int16)
        calibration_sec: 用于计算增益的音频秒数，None 则默认 AGC_CALIBRATION_SEC

    返回:
        (processed: np.int16, gain_db: float, info: str)
    """
    import numpy as np

    if calibration_sec is None:
        calibration_sec = AGC_CALIBRATION_SEC

    arr = samples.astype(np.float64)
    calib_samples = int(calibration_sec * SAMPLE_RATE)
    calib_segment = arr[:min(calib_samples, len(arr))]

    if len(calib_segment) == 0:
        return samples, 0.0, "无校准数据,跳过"

    current_rms = float(np.sqrt(np.mean(calib_segment ** 2)))
    if current_rms < 1.0:
        return samples, 0.0, f"RMS≈0,跳过AGC"

    # 检查是否已在合理范围内
    lower = AGC_TARGET_RMS * (1.0 - AGC_TOLERANCE_RATIO)
    upper = AGC_TARGET_RMS * (1.0 + AGC_TOLERANCE_RATIO)
    if lower <= current_rms <= upper:
        _log(f"AGC: RMS={current_rms:.0f} 在目标范围内 [{lower:.0f}-{upper:.0f}],跳过")
        return samples, 0.0, "已达标,跳过"

    # 计算增益
    gain = AGC_TARGET_RMS / current_rms
    gain = max(AGC_MIN_GAIN, min(AGC_MAX_GAIN, gain))
    gain_db = 20.0 * np.log10(gain)

    arr *= gain
    result = np.clip(arr, -32768, 32767).astype(np.int16)

    new_rms = float(np.sqrt(np.mean(result.astype(np.float64) ** 2)))
    info = f"RMS {current_rms:.0f}→{new_rms:.0f} (gain={gain:.2f}x, {gain_db:+.1f}dB)"
    _log(f"AGC: {info}")
    return result, gain_db, info


def _apply_corrections(text):
    """
    对 Vosk 识别结果应用后处理纠错。

    按 POST_CORRECTIONS 字典做全字匹配替换，长词优先（已在字典中按长度排列）。

    参数:
        text: str — 原始识别文字

    返回:
        str — 纠错后的文字
    """
    if not text:
        return text

    result = text
    applied = []
    for wrong, correct in POST_CORRECTIONS.items():
        if wrong in result:
            result = result.replace(wrong, correct)
            applied.append(f"{wrong}→{correct}")

    if applied:
        _log(f"纠错: {', '.join(applied)}")

    return result


# ============================================================================
# 后端 1: parec 录音 (VAD 动态版)
# ============================================================================

def _record_parec():
    """
    使用 parec 通过 WSLg PulseAudio 录制原始 PCM（VAD 动态版）。

    采用流式读取 + RMS 能量 VAD:
    - 检测到语音时持续录音
    - 连续 SILENCE_DURATION 秒静音后自动停止
    - 超过 MAX_RECORD_SECONDS 秒强制停止

    返回:
        (audio_data: bytes | None, status: str)
        status: "ok" | "timeout" | "no_audio" | "error"
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

    print("🎤 正在听,请说话...(说完自动停止)")

    # 每个 chunk 的字节数: 0.1s @ 16kHz mono s16le = 3200 bytes
    chunk_bytes = int(SAMPLE_RATE * 2 * CHUNK_DURATION)

    parec = subprocess.Popen(
        ["parec", "--format=s16le", "--rate=16000",
         "--channels=1", "--latency-msec=50"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )

    audio_data = b""
    silence_frames = 0          # 连续静音帧计数
    frames_per_silence = int(SILENCE_DURATION / CHUNK_DURATION)  # 需要连续多少帧静音才停止
    max_frames = int(MAX_RECORD_SECONDS / CHUNK_DURATION)
    total_frames = 0
    has_speech = False          # 是否曾检测到语音

    # 动态 VAD 校准 (v3.7.7)
    calib_frames = int(VAD_CALIBRATION_SEC / CHUNK_DURATION)
    noise_rms_samples = []      # 校准期间收集的 RMS 值
    vad_threshold = SILENCE_THRESHOLD  # 初始使用默认值，校准后动态更新
    vad_calibrated = False

    # 无语音超时 (v3.7.7)
    no_voice_timeout_frames = int(NO_VOICE_TIMEOUT / CHUNK_DURATION)
    no_voice_count = 0

    start_time = time.time()
    try:
        while total_frames < max_frames:
            chunk = parec.stdout.read(chunk_bytes)
            if not chunk:
                break  # parec 进程结束

            audio_data += chunk
            total_frames += 1

            # VAD: 计算当前 chunk 的 RMS
            rms = _compute_rms(chunk)

            # 动态 VAD 校准：前 N 帧测量背景噪声
            if not vad_calibrated and total_frames <= calib_frames:
                noise_rms_samples.append(rms)
                if total_frames == calib_frames:
                    noise_floor = sum(noise_rms_samples) / max(len(noise_rms_samples), 1)
                    vad_threshold = max(noise_floor * VAD_NOISE_MULTIPLIER, VAD_MIN_THRESHOLD)
                    vad_calibrated = True
                    _log(f"VAD 校准完成: noise_floor={noise_floor:.1f}, "
                         f"threshold={vad_threshold:.1f} (默认={SILENCE_THRESHOLD})")

            if rms >= vad_threshold:
                # 检测到语音
                silence_frames = 0
                no_voice_count = 0
                has_speech = True
            else:
                # 静音
                silence_frames += 1
                if not has_speech:
                    no_voice_count += 1

            # 无语音超时：启动后 N 秒内无任何语音则中止
            if not has_speech and no_voice_count >= no_voice_timeout_frames:
                _log(f"无语音超时 ({NO_VOICE_TIMEOUT}s), 中止录音")
                break

            if has_speech and silence_frames >= frames_per_silence:
                _log(f"VAD 检测到静音 ({silence_frames * CHUNK_DURATION:.1f}s), 停止录音 "
                     f"(threshold={vad_threshold:.0f})")
                break

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
    _log(f"parec 录制: {len(audio_data)} bytes ({actual_sec:.1f}s), "
         f"frames={total_frames}, has_speech={has_speech}, "
         f"vad_threshold={vad_threshold:.0f}")

    # 判断状态
    if not has_speech:
        if no_voice_count >= no_voice_timeout_frames:
            _warn(f"启动后 {NO_VOICE_TIMEOUT}s 未检测到语音,请确认麦克风是否正常工作.")
            return None, "no_audio"
        _warn(f"未检测到有效语音 (录音 {actual_sec:.1f}s).")
        _warn("请检查 Windows 麦克风隐私设置是否允许桌面应用访问,")
        _warn("以及 WSLg 是否能监听麦克风.")
        return None, "no_audio"

    if total_frames >= max_frames:
        _warn(f"录音超时 ({MAX_RECORD_SECONDS}s), 强制停止.")
        return audio_data, "timeout"

    if len(audio_data) < SAMPLE_RATE * 2 * 1:   # 不足 1 秒
        _warn(f"录音数据过短 ({actual_sec:.1f}s).")
        return None, "no_audio"

    # ---- 后处理：DC 偏移去除 + AGC (v3.7.7) ----
    import numpy as np
    samples = np.frombuffer(audio_data, dtype=np.int16).copy()

    if ENABLE_DC_REMOVAL:
        samples = _apply_dc_removal(samples)

    if ENABLE_AGC:
        samples, gain_db, agc_info = _apply_agc(samples)

    audio_data = samples.tobytes()
    return audio_data, "ok"


# ============================================================================
# 后端 2: sounddevice 录音
# ============================================================================

def _record_sounddevice():
    """
    使用 sounddevice (PortAudio) 进行 VAD 动态录音。

    采用 sd.InputStream 非阻塞流式采集:
    - 100ms 音频块 → 计算 RMS 能量
    - RMS >= SILENCE_THRESHOLD → 检测到语音,持续录音
    - 连续 SILENCE_DURATION 秒静音 → 自动停止
    - 超过 MAX_RECORD_SECONDS → 强制停止

    返回:
        (audio_data: bytes | None, status: str)
        status: "ok" | "timeout" | "no_audio" | "error"
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
        _warn("请确认麦克风已连接并在 Windows 隐私设置中允许桌面应用访问麦克风.")
        return None, "error"

    print("🎤 正在听,请说话...(说完自动停止)")
    _log(f"录音参数: samplerate={SAMPLE_RATE}, channels={CHANNELS}, dtype=int16, "
         f"device_idx={device_idx}, max={MAX_RECORD_SECONDS}s, "
         f"default_vad_threshold={SILENCE_THRESHOLD}, silence_dur={SILENCE_DURATION}s, "
         f"agc={ENABLE_AGC}, dc_removal={ENABLE_DC_REMOVAL}")

    # =========================================================================
    # 流式录音 + VAD (v3.7.7: 动态阈值 + 无语音超时)
    # =========================================================================
    audio_chunks = []           # 收集所有 int16 音频块
    silence_frames = 0          # 连续静音帧计数
    frames_per_silence = int(SILENCE_DURATION / CHUNK_DURATION)
    max_frames = int(MAX_RECORD_SECONDS / CHUNK_DURATION)
    total_frames = 0
    has_speech = False
    stream_error = None

    # 动态 VAD 校准
    calib_frames = int(VAD_CALIBRATION_SEC / CHUNK_DURATION)
    noise_rms_samples = []
    vad_threshold = SILENCE_THRESHOLD  # 初始默认值
    vad_calibrated = False

    # 无语音超时
    no_voice_timeout_frames = int(NO_VOICE_TIMEOUT / CHUNK_DURATION)
    no_voice_count = 0
    abort_no_voice = False

    # 每个 chunk 的采样数
    chunk_samples = int(SAMPLE_RATE * CHUNK_DURATION)

    def _audio_callback(indata, frames, time_info, status):
        """InputStream 回调：每 CHUNK_DURATION 秒触发一次"""
        nonlocal silence_frames, total_frames, has_speech, stream_error
        nonlocal vad_threshold, vad_calibrated, no_voice_count, abort_no_voice
        if status:
            _log(f"音频流状态: {status}")
            if status.input_overflow:
                _warn("音频缓冲区溢出,可能有数据丢失")
        # 将当前帧加入缓冲区
        audio_chunks.append(indata.copy())
        total_frames += 1

        # VAD: 计算 RMS
        rms = _rms_from_array(indata)

        # 动态 VAD 校准：前 N 帧测量背景噪声
        if not vad_calibrated and total_frames <= calib_frames:
            noise_rms_samples.append(rms)
            if total_frames == calib_frames:
                noise_floor = sum(noise_rms_samples) / max(len(noise_rms_samples), 1)
                vad_threshold = max(noise_floor * VAD_NOISE_MULTIPLIER, VAD_MIN_THRESHOLD)
                vad_calibrated = True
                _log(f"VAD 校准完成: noise_floor={noise_floor:.1f}, "
                     f"threshold={vad_threshold:.1f} (默认={SILENCE_THRESHOLD})")

        if rms >= vad_threshold:
            silence_frames = 0
            no_voice_count = 0
            has_speech = True
        else:
            silence_frames += 1
            if not has_speech:
                no_voice_count += 1

        # 无语音超时：启动后 N 秒内无任何语音则中止
        if not has_speech and no_voice_count >= no_voice_timeout_frames:
            _log(f"无语音超时 ({NO_VOICE_TIMEOUT}s), 中止录音")
            abort_no_voice = True
            raise sd.CallbackStop()

        # 检查停止条件
        if has_speech and silence_frames >= frames_per_silence:
            _log(f"VAD 静音检测触发 ({silence_frames * CHUNK_DURATION:.1f}s, "
                 f"threshold={vad_threshold:.0f})")
            raise sd.CallbackStop()
        if total_frames >= max_frames:
            _log(f"录音达到最大时长 ({MAX_RECORD_SECONDS}s)")
            raise sd.CallbackStop()

    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype='int16',
            device=device_idx,
            blocksize=chunk_samples,
            callback=_audio_callback,
        )
        stream.start()
    except sd.PortAudioError as e:
        err_str = str(e).lower()
        _warn(f"sounddevice PortAudio 错误: {e}")
        if any(kw in err_str for kw in (
            "permission", "access", "device unavailable",
            "invalid device", "unanticipated host"
        )):
            _warn(">>> Windows 用户请检查麦克风权限:")
            _warn("    设置 → 隐私和安全性 → 麦克风")
            _warn("    确保「麦克风访问」和「允许桌面应用访问麦克风」已开启.")
        return None, "error"
    except Exception as e:
        _warn(f"sounddevice 音频流创建失败: {e}")
        return None, "error"

    # 等待流结束（CallbackStop 或超时）
    try:
        while stream.active:
            time.sleep(0.05)  # 50ms 轮询,低 CPU 占用
    except KeyboardInterrupt:
        _log("录音被用户中断")
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    # =========================================================================
    # 数据处理 — 合并所有 chunk 并转换为 PCM bytes
    # =========================================================================
    if not audio_chunks:
        _warn("录音未产生任何数据")
        return None, "no_audio"

    import numpy as np
    recording = np.concatenate(audio_chunks, axis=0)

    original_shape = recording.shape
    original_dtype = recording.dtype
    _log(f"录音原始数据: shape={original_shape}, dtype={original_dtype}, "
         f"chunks={len(audio_chunks)}")

    # 1) 确保数据类型为 int16
    if recording.dtype != np.int16:
        _log(f"数据类型从 {recording.dtype} 转换为 int16")
        if recording.dtype in (np.float32, np.float64):
            recording = np.clip(recording * 32767, -32768, 32767)
        recording = recording.astype(np.int16)

    # 2) 确保形状为 (samples,)
    if recording.ndim == 2:
        if recording.shape[1] == 1:
            recording = recording.ravel()
            _log(f"形状从 {original_shape} ravel → {recording.shape}")
        elif recording.shape[1] > 1:
            _warn(f"录音为 {recording.shape[1]} 声道,仅取第一声道")
            recording = recording[:, 0].copy()

    # 3) 静音 / 信号强度检测
    peak = int(np.max(np.abs(recording)))
    rms = float(np.sqrt(np.mean(recording.astype(np.float64) ** 2)))
    actual_sec = len(recording) / SAMPLE_RATE
    _log(f"音频统计: peak={peak}, rms={rms:.1f}, duration={actual_sec:.1f}s, "
         f"has_speech={has_speech}, vad_threshold={vad_threshold:.0f}")

    if peak < 10:
        _warn(f"录音信号极弱 (peak={peak}),可能麦克风静音、被占用或权限不足.")
        _warn("请检查 Windows 麦克风隐私设置和硬件静音开关.")
        return None, "no_audio"

    # ---- 后处理：DC 偏移去除 + AGC (v3.7.7) ----
    if ENABLE_DC_REMOVAL:
        recording = _apply_dc_removal(recording)

    if ENABLE_AGC:
        recording, gain_db, agc_info = _apply_agc(recording)

    # 4) 转为原始 PCM 字节 (s16le, 16kHz, mono)
    audio_data = recording.tobytes()
    _log(f"sounddevice 录制完成: {len(audio_data)} bytes ({actual_sec:.1f}s)")

    # 5) 判断状态
    if abort_no_voice:
        return audio_data, "no_audio"
    if not has_speech:
        return audio_data, "no_audio"
    if total_frames >= max_frames:
        return audio_data, "timeout"

    return audio_data, "ok"


# ============================================================================
# Vosk 语音识别
# ============================================================================

def _recognize_pcm(pcm_data):
    """
    将原始 PCM (s16le, 16kHz, mono) 送入 Vosk 进行识别。

    采用流式分块送入，模拟实时语音流，并在最后调用 FinalResult()
    强制冲刷识别器缓冲以获取残余结果。

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

    data_len = len(pcm_data)
    duration_sec = data_len / (SAMPLE_RATE * 2)
    _log(f"识别输入: {data_len} bytes ({duration_sec:.1f}s)")

    # 数据校验 — 至少 0.5 秒
    min_bytes = SAMPLE_RATE * 2 // 2   # 0.5s = 16000 bytes
    if data_len < min_bytes:
        return False, f"录音时长不足 ({duration_sec:.1f}s < 0.5s)"

    # 每次识别创建新的 KaldiRecognizer，避免跨录音状态污染
    try:
        import vosk
        recognizer = vosk.KaldiRecognizer(_model, SAMPLE_RATE)
        recognizer.SetWords(True)   # 启用词级信息便于调试
    except Exception as e:
        _warn(f"创建识别器失败: {e}")
        return False, "识别器创建失败"

    try:
        # =====================================================================
        # 流式分块送入音频 — 每块约 0.5 秒，模拟实时识别流
        # =====================================================================
        chunk_size = SAMPLE_RATE * 2 // 2   # 0.5 秒 = 16000 bytes
        final_text = ""
        partial_text = ""

        for offset in range(0, data_len, chunk_size):
            chunk = pcm_data[offset:offset + chunk_size]
            chunk_sec = offset / (SAMPLE_RATE * 2)

            if recognizer.AcceptWaveform(chunk):
                result = json.loads(recognizer.Result())
                final_text = result.get("text", "").strip()
                _log(f"识别(终态 @{chunk_sec:.1f}s): json={json.dumps(result, ensure_ascii=False)}")
            else:
                partial = json.loads(recognizer.PartialResult())
                partial_text = partial.get("partial", "").strip()
                if _VERBOSE and partial_text:
                    _log(f"识别(部分 @{chunk_sec:.1f}s): '{partial_text}'")

        # =====================================================================
        # 强制冲刷 FinalResult — 喂完所有数据后必须调用
        # =====================================================================
        try:
            final_result = json.loads(recognizer.FinalResult())
            forced_text = final_result.get("text", "").strip()
            _log(f"识别(FinalResult): json={json.dumps(final_result, ensure_ascii=False)}")
            if forced_text:
                final_text = forced_text
        except Exception as e:
            _log(f"FinalResult 异常(非致命): {e}")

        # =====================================================================
        # 合并结果 — 优先 FinalResult/AcceptWaveform 终态，其次部分结果
        # =====================================================================
        text = final_text or partial_text
        if text:
            _log(f"识别成功: '{text}'")
            return True, text
        else:
            _log("识别结果为空 — 未检测到有效语音")
            return False, "未检测到语音"

    except Exception as e:
        _warn(f"Vosk 识别异常: {e}")
        return False, f"识别异常: {e}"


# ============================================================================
# 公共 API
# ============================================================================

def listen_once():
    """
    录制一段语音并返回识别文本（VAD 动态录音版, v3.7.7 增强）。

    返回:
        str — 识别到的文字（已通过 POST_CORRECTIONS 纠错），或以下特殊值:
        "模拟语音输入"       — 所有后端不可用或模型未加载
        "录音失败"           — 录音过程出错
        "语音识别失败"       — 录到了但未识别出有效语音
        "没有检测到语音，请重试" — VAD 未检测到有效语音（含无语音超时）
    """
    backend = _detect_audio_backend()
    _log(f"使用后端: {backend}")

    # ---- 录音 ----
    pcm_data = None
    rec_status = "error"

    if backend == "parec":
        pcm_data, rec_status = _record_parec()
    elif backend == "sounddevice":
        pcm_data, rec_status = _record_sounddevice()
    else:   # "mock" 或未知
        return "模拟语音输入"

    # ---- 处理录音状态 ----
    if pcm_data is None or rec_status == "error":
        _warn("录音失败 — 未获取到音频数据")
        return "录音失败"

    if rec_status == "no_audio":
        _log("VAD 未检测到有效语音")
        return "没有检测到语音，请重试"

    if rec_status == "timeout":
        _warn(f"录音超时 ({MAX_RECORD_SECONDS}s)")
        # 超时时仍尝试识别已录内容
        duration = len(pcm_data) / (SAMPLE_RATE * 2)
        print(f"⚠️ 录音已达最大时长 {MAX_RECORD_SECONDS} 秒 ({duration:.1f}s), 请缩短说话内容")

    # ---- 识别 ----
    ok, text = _recognize_pcm(pcm_data)

    if ok:
        # 后处理纠错 (v3.7.7)
        corrected = _apply_corrections(text)
        if corrected != text:
            print(f"📝 识别到文字: {text} → 纠错后: {corrected}")
        else:
            print(f"📝 识别到文字: {text}")
        _log(f"最终输出: '{corrected}' (原始: '{text}')")
        return corrected
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
        print(f"\n  要启用录音,请安装 pulseaudio-utils:")
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
