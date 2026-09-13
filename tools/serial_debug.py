#!/usr/bin/env python3
"""设备调试工具：长时间串口记录 + 精确复位 + 状态提取。

与 ``serial_snapshot.py`` 的区别：那个只为「看一眼」，这个为**复现故障**——
需要跨越复位前后的完整时间线，并且能在不丢日志的前提下复位设备。

用法：
    # 记录 60 秒（自动带上从设备上电/复位开始的内容）
    python tools/serial_debug.py watch 60

    # 复位设备并记录 45 秒（复现启动路径）
    python tools/serial_debug.py reboot 45

    # 从已有日志里提取状态时间线
    python tools/serial_debug.py timeline /tmp/xxx.log

设计约束（继承 serial_snapshot 的纪律）：
- 打开串口时**不主动拉 DTR/RTS**（那会复位设备），要复位就用 esptool 显式做。
- 复位走 esptool 的 hard_reset——与手动按 RST 等价，不碰 flash。
"""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import serial
except ImportError:
    print("需要 pyserial（server/.venv 里有）", file=sys.stderr)
    raise

REPO = Path(__file__).resolve().parents[1]
ESPTOOL = Path.home() / ".espressif/python_env/idf6.0_py3.12_env/bin/esptool"

#: 值得单独拎出来的事件：状态机、唤醒、连接、音乐、错误。
INTERESTING = re.compile(
    r"State:|Wake|wake|Protocol|connect|Connect|WS:|Wifi|Ota|Activation|"
    r"Music|ERROR|E \(|W \(|BeginWake|listen|Listen|AFE|WakeNet|audio|Audio|"
    r"battery|Battery|Button|button|Click|click"
)


def find_port() -> str:
    import glob
    for pat in ("/dev/cu.usbmodem*", "/dev/cu.wchusbserial*", "/dev/cu.SLAB*"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    raise SystemExit("找不到串口设备")


def open_port(port: str):
    """打开串口但不复位（**一行都不碰** DTR/RTS）。

    这里从前会写 ``ser.dtr = False; ser.rts = False``（open 前后各一次），
    以为「主动拉低就是不动信号」——错了：那会产生一次从默认电平到指定电平
    的**跳变**，ESP32 的 USB 转串口正是用 DTR/RTS 跳变做自动复位的，于是
    每次开录都按了一次 RST。实测（2026-09）：这样开会记到
    ``rst:0x15 (USB_UART_CHIP_RESET)``，而裸 open 不会。

    对一个「跨复位记录完整时间线、为复现故障而写」的工具来说，这是最伤的
    缺陷——它恰恰会把要观察的现场（卡死前的状态）毁在开录那一刻。所以与
    ``serial_snapshot.py:70`` 同一纪律：只构造、只读、只关。
    """
    return serial.Serial(port, 115200, timeout=0.05)


def hard_reset(port: str) -> bool:
    """用 esptool 硬复位设备（等价于按 RST 键）。"""
    if not ESPTOOL.exists():
        print(f"  ⚠ esptool 不存在: {ESPTOOL}", file=sys.stderr)
        return False
    r = subprocess.run(
        [str(ESPTOOL), "--port", port, "--before", "default_reset",
         "--after", "hard_reset", "chip_id"],
        capture_output=True, text=True, timeout=60,
    )
    return r.returncode == 0


def watch(seconds: int, log: Path, reset_first: bool, quiet: bool) -> int:
    port = find_port()
    print(f"串口 {port}，记录 {seconds}s → {log}", flush=True)

    ser = open_port(port)
    if reset_first:
        print("  复位设备中（esptool hard_reset）…", flush=True)
        ser.close()
        ok = hard_reset(port)
        print(f"  复位{'成功' if ok else '失败'}，重新打开串口", flush=True)
        time.sleep(0.5)
        ser = open_port(port)

    start = time.time()
    buf = b""
    with log.open("wb") as f:
        while time.time() - start < seconds:
            try:
                chunk = ser.read(4096)
            except Exception as e:
                print(f"  读取异常: {e}", file=sys.stderr)
                break
            if not chunk:
                continue
            f.write(chunk)
            f.flush()
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").rstrip("\r")
                if not quiet:
                    print(text, flush=True)
    ser.close()
    print(f"--- 记录结束 → {log} ---", flush=True)
    return 0


def timeline(path: Path) -> int:
    """从日志里提取事件时间线（按 uptime 排序，过滤噪声）。"""
    if not path.exists():
        print(f"日志不存在: {path}", file=sys.stderr)
        return 1
    text = path.read_text(encoding="utf-8", errors="replace")
    ts = re.compile(r"^([IWE]) \((\d+)\)\s+(.*)$")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        m = ts.match(line)
        if not m:
            continue
        level, ups, rest = m.group(1), int(m.group(2)), m.group(3)
        if "SystemInfo: free sram" in rest:
            continue  # 噪声：内存打印
        rows.append((ups, level, rest))

    if not rows:
        print("（没有可提取的带时间戳日志行）")
        return 0

    print(f"共 {len(rows)} 行事件（已滤内存打印）\n")
    t0 = rows[0][0]
    for ups, level, rest in rows:
        mark = " " if level == "I" else ("!" if level == "W" else "X")
        print(f"{mark} [{(ups - t0) / 1000:7.3f}s] {rest[:150]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="设备串口调试（记录/复位/时间线）")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="记录串口")
    w.add_argument("seconds", type=int)
    w.add_argument("--log", default="/tmp/serial_debug.log")
    w.add_argument("--reset", action="store_true", help="记录前先硬复位")
    w.add_argument("--quiet", action="store_true", help="不打印，只落盘")

    r = sub.add_parser("reboot", help="复位并记录")
    r.add_argument("seconds", type=int, nargs="?", default=45)
    r.add_argument("--log", default="/tmp/serial_reboot.log")
    r.add_argument("--quiet", action="store_true")

    t = sub.add_parser("timeline", help="从日志提取事件时间线")
    t.add_argument("log")

    a = p.parse_args(argv)

    if a.cmd == "watch":
        return watch(a.seconds, Path(a.log), a.reset, a.quiet)
    if a.cmd == "reboot":
        return watch(a.seconds, Path(a.log), True, a.quiet)
    if a.cmd == "timeline":
        return timeline(Path(a.log))
    return 1


if __name__ == "__main__":
    sys.exit(main())
