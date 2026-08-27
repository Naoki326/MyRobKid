"""
mlx.py — 本机 MLX Qwen3-TTS 服务（Apple Silicon GPU 加速）

调用本地 MLX TTS HTTP 服务（默认 127.0.0.1:9753）生成语音。
服务返回 WAV 文件路径，本 provider 读取文件内容返回。

音色克隆：可选 ref_audio/ref_text 指定参考音频（绝对路径，服务端不做 ~ 展开）；
不传则用服务端默认音色（ref_yuanbao，官方娃娃音）。
"""
import os
import requests
from core.providers.tts.base import TTSProviderBase
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.url = config.get("url", "http://127.0.0.1:9753/tts")
        self.speed = float(config.get("speed", 0.85))
        # 音色标识：用于唤醒回应等缓存按音色区分（可配 voice 区分不同 MLX 模型/音色）
        self.voice = config.get("voice", "mlx")
        # 克隆音色：参考音频绝对路径 + 其转写文本（如湾湾小何）
        # 注意：服务端 os.path.exists() 不展开 ~，必须写绝对路径
        self.ref_audio = config.get("ref_audio") or None
        self.ref_text = config.get("ref_text") or None
        self.audio_file_type = "wav"
        self.output_file = config.get("output_dir", "tmp/")
        if self.ref_audio and not os.path.exists(self.ref_audio):
            logger.bind(tag=TAG).warning(
                f"克隆参考音频不存在，将回退服务端默认音色: {self.ref_audio}"
            )

    async def text_to_speak(self, text, output_file):
        try:
            payload = {"text": text, "speed": self.speed}
            if self.ref_audio:
                payload["ref_audio"] = self.ref_audio
            if self.ref_text:
                payload["ref_text"] = self.ref_text
            resp = requests.post(
                self.url,
                json=payload,
                timeout=self.tts_timeout,
            )
            if resp.status_code != 200:
                error_msg = f"MLX TTS请求失败: {resp.status_code} - {resp.text}"
                logger.bind(tag=TAG).error(error_msg)
                raise Exception(error_msg)

            data = resp.json()
            wav_path = data.get("wav")
            if not wav_path or not os.path.exists(wav_path):
                raise Exception(f"MLX TTS返回的wav文件不存在: {wav_path}")

            with open(wav_path, "rb") as f:
                audio_bytes = f.read()


            # 清理 MLX 服务生成的临时文件（避免 /tmp 堆积）
            try:
                os.remove(wav_path)
            except OSError:
                pass

            if output_file:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, "wb") as f:
                    f.write(audio_bytes)
            else:
                return audio_bytes
        except requests.Timeout:
            error_msg = f"MLX TTS请求超时: {self.url}"
            logger.bind(tag=TAG).error(error_msg)
            raise Exception(error_msg)
        except requests.ConnectionError:
            error_msg = f"MLX TTS服务未启动: {self.url}"
            logger.bind(tag=TAG).error(error_msg)
            raise Exception(error_msg)
