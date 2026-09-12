#!/usr/bin/env python3
"""一次性读串口快照 / 抓一段日志——**刻意不碰 DTR/RTS**。

为什么单独写这个文件（而不是每次写临时脚本）：

会话中反复出现「诊断时把设备复位、现场毁掉」的事故。根因不是「打开串口」本身，
而是**显式写 `dtr=` / `rts=`** —— ESP32 的 USB 转串口用这两个信号的电平跳变
做自动复位，写它们就等于按了一次复位键。临时脚本里常见的
``s.dtr = None; s.rts = None``（以为「不动信号」）反而会在 open 之后产生一次
从默认电平到指定电平的跳变，照样复位。

所以这里把正确做法固化成一个可复用工具，替代「临时写一段 pyserial」这种
做法本身。与 ``tools/serial_telemetry.py`` 同一纪律：只构造、只读、只关。

用法::

    # 抓 N 秒并打印关键行（默认 30 秒，最长不限）
    server/.venv/bin/python tools/serial_snapshot.py 60

    # 抓 N 秒、把过滤正则换成自己的（只影响打印，不影响落盘）
    server/.venv/bin/python tools/serial_snapshot.py 60 --grep "State: |free sram"

    # 只抓全部原始输出（不过滤）
    server/.venv/bin/python tools/serial_snapshot.py 60 --all

    # 不复位的同时看「现在到底停在哪」：先抓一小段再看结果
    server/.venv/bin/python tools/serial_snapshot.py 10 --all

全量原始字节始终写到 ``/tmp/serial_snapshot.log``（与 serial_telemetry 的
``/tmp/serial_full.log`` 分开，两者可同时存在、互不干扰）。

**不会复位设备**——这一点由下面的 ``open_serial_safely`` 保证：它只调
``serial.Serial(port, baud, timeout=...)``，一行都不碰 dtr/rts。
"""

import argparse
import glob
import re
import sys
import time
from pathlib import Path

LOG_PATH = "/tmp/serial_snapshot.log"
BAUD = 115200

#: 默认只打这些行——够定位「卡在哪一步」，又不会把屏幕刷爆。
DEFAULT_GREP = (
    r"rst:0x|State: |free sram|minimal sram|Music |music|pipe:|"
    r"error|Error|ERROR|abort|Guru|panic|backtrace|WDT|watchdog|"
    r"stack|Task|AFE|VAD|音量|超时|失败|断开|连接"
)


def find_port() -> str | None:
    """自动探测串口（与 serial_telemetry 同一探测顺序）。"""
    ports = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
             + glob.glob("/dev/cu.SLAB*"))
    return ports[0] if ports else None


def open_serial_safely(port: str):
    """打开串口，**绝不触碰 DTR/RTS**。

    这是本文件存在的理由，请不要在这里加 ``ser.dtr = ...`` /
    ``ser.rts = ...`` —— 那会复位设备、毁掉正在观察的现场。``serial.Serial``
    的一次性构造形式本身就够用：它不做额外的电平跳变。
    """
    import serial

    return serial.Serial(port, BAUD, timeout=1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="一次性串口抓取（不碰 DTR/RTS，故不复位设备）")
    parser.add_argument("seconds", nargs="?", type=float, default=30.0,
                        help="抓取时长（秒），默认 30")
    parser.add_argument("--port", default=None, help="串口设备，默认自动探测")
    parser.add_argument("--grep", default=DEFAULT_GREP,
                        help="只打印匹配该正则的行；传空串 = 全打")
    parser.add_argument("--all", action="store_true", help="打印全部行（等价 --grep ''）")
    parser.add_argument("--log", default=LOG_PATH, help="原始字节落盘路径")
    parser.add_argument("--quiet", action="store_true", help="不打印，只落盘")
    args = parser.parse_args(argv)

    port = args.port or find_port()
    if port is None:
        print("未找到串口设备（/dev/cu.usbmodem* 等）", file=sys.stderr)
        return 2

    pattern = "" if args.all else args.grep
    matcher = re.compile(pattern) if pattern else None

    print(f"打开 {port}，抓取 {args.seconds:g}s，原始日志 → {args.log}")
    print("（不碰 DTR/RTS：本工具不会复位设备）", flush=True)

    last_ts_ms = 0
    lines = 0
    started = time.time()
    with open(args.log, "wb") as log_file:
        ser = None
        try:
            ser = open_serial_safely(port)
            buf = b""
            while time.time() - started < args.seconds:
                chunk = ser.read(4096)
                if not chunk:
                    continue
                log_file.write(chunk)
                log_file.flush()
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    text = raw.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    lines += 1
                    m = re.match(r"^[IWE] \((\d+)\)", text)
                    if m:
                        last_ts_ms = int(m.group(1))
                    if matcher is not None and not matcher.search(text):
                        continue
                    if not args.quiet:
                        print(text[:190], flush=True)
        finally:
            if ser is not None:
                ser.close()

    print(f"--- 结束：{lines} 行，最后日志时间戳 {last_ts_ms} ms "
          f"（约 {last_ts_ms / 1000:.0f}s uptime；若只有几千则设备本轮被复位过）---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
