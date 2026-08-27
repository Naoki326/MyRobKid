#!/usr/bin/env python3
"""克隆参考音频转写工具：为 MLX TTS 音色克隆生成 ref_text

用法（在主仓根目录）:
  server/.venv/bin/python tools/transcribe_ref.py <音频文件> [输出wav]

- 音频转 24kHz mono WAV（可选第二参数指定输出路径，默认 ~/.hermes/models/voice_profiles/<名字>.wav）
- 用 FunASR SenseVoice 转写文本，打印可直接粘贴进 data/.config.yaml 的 ref_text
- 例: server/.venv/bin/python tools/transcribe_ref.py ~/Downloads/wanwan.m4a
"""
import os
import sys
import subprocess
import tempfile


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = os.path.expanduser(sys.argv[1])
    if not os.path.exists(src):
        print(f"文件不存在: {src}")
        sys.exit(1)

    # 1) 转 24kHz mono WAV（ffmpeg）
    out_wav = (
        os.path.expanduser(sys.argv[2])
        if len(sys.argv) > 2
        else os.path.expanduser(
            "~/.hermes/models/voice_profiles/"
            + os.path.splitext(os.path.basename(src))[0]
            + ".wav"
        )
    )
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
         "-ar", "24000", "-ac", "1", out_wav],
        check=True,
    )
    print(f"[1/2] 已转换: {out_wav}")

    # 2) SenseVoice 转写（与服务端 ASR 同一模型）
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
    from funasr import AutoModel

    model_dir = os.path.join(os.path.dirname(__file__), "..", "server", "models", "SenseVoiceSmall")
    model = AutoModel(model=model_dir, disable_update=True)
    res = model.generate(input=out_wav)
    text = res[0]["text"].strip()
    print(f"[2/2] 转写文本（用作 ref_text）:\n\n    {text}\n")
    print(f"配置参考:\n    voice: <音色标识>\n    ref_audio: {out_wav}\n    ref_text: '{text}'")


if __name__ == "__main__":
    main()
