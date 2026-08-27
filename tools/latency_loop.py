#!/usr/bin/env python3
"""对话延迟反馈回路(模拟设备:WS握手→发Opus音频→测各段延迟)
用法: server/.venv/bin/python tools/latency_loop.py [问题] [--realtime]
"""
import asyncio, json, sys, subprocess, tempfile, time, wave, os
import websockets
import opuslib_next

WS_URL = os.environ.get("LOOP_WS", "ws://127.0.0.1:8002/xiaozhi/v1/")
TAG = "[LOOP]"

def pcm_from_say(text):
    wav_path = tempfile.mktemp(suffix=".wav")
    subprocess.run(["say", "-o", wav_path, "--data-format=LEI16@16000", "-v", "Tingting", text], check=True)
    with wave.open(wav_path, "rb") as w:
        pcm = w.readframes(w.getnframes())
    os.unlink(wav_path)
    return pcm

async def run(text, realtime=False, tail_silence_s=1.8):
    pcm = pcm_from_say(text)
    pcm += b"\x00\x00" * int(16000 * tail_silence_s)
    enc = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_AUDIO)
    fb = 960 * 2
    frames = [pcm[i:i+fb] for i in range(0, len(pcm), fb)]
    if len(frames[-1]) < fb:
        frames[-1] += b"\x00\x00" * (fb - len(frames[-1]))
    hello = {"type":"hello","version":1,"features":{"mcp":True},"transport":"websocket",
             "audio_params":{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}}
    t0=t_voice_end=t_stt=t_first=None; stt_text=None
    async with websockets.connect(WS_URL, additional_headers={
        "device-id":"3c:dc:75:fe:81:44","client-id":"b64cb7dd-136b-4339-a398-06454995036a"},
        max_size=None, compression=None) as ws:
        await ws.send(json.dumps(hello))
        while True:
            if json.loads(await asyncio.wait_for(ws.recv(), 10)).get("type")=="hello": break
        async def reader():
            nonlocal t_stt, t_first, stt_text
            while True:
                m = await ws.recv()
                if isinstance(m, bytes):
                    if t_first is None: t_first = time.perf_counter()
                else:
                    d = json.loads(m)
                    if d.get("type")=="stt":
                        t_stt = time.perf_counter(); stt_text = d.get("text")
                        print(f"{TAG} STT: {stt_text!r}")
                    if d.get("type")=="tts" and d.get("state")=="stop":
                        import __main__; __main__.g_stop = time.perf_counter()
        task = asyncio.create_task(reader())
        await ws.send(json.dumps({"session_id":"debug","type":"listen","state":"start","mode":"auto"}))
        await asyncio.sleep(0.3)
        t0 = time.perf_counter()
        n_voice = len(frames) - int(tail_silence_s/0.06)
        for i, f in enumerate(frames):
            await ws.send(enc.encode(f, 960))
            if i == n_voice-1: t_voice_end = time.perf_counter()
            if realtime: await asyncio.sleep(0.06)
        global g_stop; g_stop = None
        deadline = time.perf_counter() + 90
        while time.perf_counter() < deadline:
            await asyncio.sleep(0.1)
            if g_stop and time.perf_counter() > g_stop + 1.0: break
        task.cancel()
    r = lambda a,b: f"{(b-a)*1000:.0f}ms" if a and b else "N/A"
    print(f"{TAG} ===== {text!r} =====")
    print(f"{TAG} 说完话->STT      : {r(t_voice_end,t_stt)}")
    print(f"{TAG} 说完话->首包音频 : {r(t_voice_end,t_first)}")
    if t_first is None: print(f"{TAG} !! 未收到回复音频"); sys.exit(1)

if __name__ == "__main__":
    args=[a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(run(args[0] if args else "你好呀", realtime="--realtime" in sys.argv))
