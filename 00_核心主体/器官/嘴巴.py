import asyncio
import edge_tts
import os
import shutil
import subprocess

async def _text_to_speech_async(text, output_file="response.mp3"):
    try:
        voice = "zh-CN-XiaoxiaoNeural"
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_file)
        return output_file
    except Exception as e:
        print(f"❌ 语音生成失败: {e}")
        return None

def _to_windows_path(wsl_path):
    """将 WSL 路径 (/mnt/c/...) 转换为 Windows 路径 (C:\\...)"""
    abs_path = os.path.abspath(wsl_path)
    if abs_path.startswith("/mnt/"):
        drive = abs_path[5:6].upper()
        rest = abs_path[6:].replace("/", "\\")
        return f"{drive}:{rest}"
    return abs_path


def _play_audio(output_file):
    """跨平台播放音频文件，按优先级尝试多种方式"""
    abs_path = os.path.abspath(output_file)
    if not os.path.exists(abs_path):
        print(f"❌ 音频文件不存在: {abs_path}")
        return

    # 1. WSL 下调用 Windows 宿主播放器
    if shutil.which("cmd.exe"):
        try:
            win_path = _to_windows_path(abs_path)
            subprocess.run(
                ["cmd.exe", "/c", "start", "/min", win_path],
                timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return
        except Exception:
            pass

    # 2. Linux 下优先 mpv
    if shutil.which("mpv"):
        subprocess.Popen(["mpv", "--really-quiet", abs_path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    # 3. ffplay 回退
    if shutil.which("ffplay"):
        subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", abs_path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    # 4. Windows 原生 start
    if os.name == "nt":
        os.system(f"start \"\" \"{abs_path}\"")
        return

    print("⚠️ 未找到可用的音频播放器 (mpv/ffplay).音频已保存,可手动播放.")

def speak(text):
    print(f"🔊 晓风说: {text}")
    # 使用项目根目录作为音频临时文件位置，保证跨平台可访问
    _PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    output_file = os.path.join(_PROJ_ROOT, "temp_response.mp3")
    try:
        asyncio.run(_text_to_speech_async(text, output_file))
        _play_audio(output_file)
    except Exception as e:
        print(f"❌ 语音播放失败: {e}")