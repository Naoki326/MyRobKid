#!/usr/bin/env python3
"""抓取机器人串口日志，实时提取 pipe: 管线遥测。

用法: server/.venv/bin/python tools/serial_telemetry.py [秒数，默认 120]
插上 USB 后自动探测 /dev/cu.usbmodem*，全量日志存 /tmp/serial_full.log。
"""
import glob
import sys
import time

import serial

DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 120
ports = glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*") + glob.glob("/dev/cu.SLAB*")
if not ports:
    print("❌ 没找到 USB 串口（/dev/cu.usbmodem*）。确认 USB 线已插好。")
    sys.exit(1)
port = ports[0]
print(f"✅ 打开 {port}，抓取 {DURATION}s，全量日志 → /tmp/serial_full.log")
print("   关注行: pipe: ring=…(ring水位) in_buf=…(解码缓冲) read=…/2s(网络读) pushed=… fail=…(推帧失败)")

ser = serial.Serial(port, 115200, timeout=1)
buf = b""
pipe_lines = []
start = time.time()
with open("/tmp/serial_full.log", "wb") as f:
    while time.time() - start < DURATION:
        chunk = ser.read(4096)
        if not chunk:
            continue
        f.write(chunk)
        f.flush()
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            ts = time.strftime("%H:%M:%S")
            if "pipe:" in text or "Music" in text or "music" in text:
                tagged = f"[{ts}] {text}"
                pipe_lines.append(tagged)
                print(tagged)
print(f"\n==== 完成，共 {len(pipe_lines)} 行关键日志，全量见 /tmp/serial_full.log ====")
