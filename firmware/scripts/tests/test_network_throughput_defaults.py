#!/usr/bin/env python3
"""网络内存约束与吞吐来源契约（ADR-0011 / issue #23）。

判据两条：

  1. **内存上限**：接收缓冲与 lwIP 窗口不得高于当前值——它们是真实分配的内部
     RAM，往上调就是与 camera/LVGL/AFE 抢内存（曾把 free sram 打到 9919、
     minimal 2095，出现 `Failed to create music start task`）。
  2. **吞吐来源**：WiFi 驱动 IRAM 加速必须开启——它是段内挪移、不占运行时堆，
     是唯一一项只赚不赔的改动。

为什么必须有这条测试：**配置错了设备仍能正常启动**——运行时的行为断言覆盖不到
（OOM 只在特定负载下暴露，且表现为别处的功能失败）。只有数值契约能拦住。

三个真相源都要查（ADR-0002 的纪律：改一处不够）：

  1. `sdkconfig.defaults` + `sdkconfig.defaults.esp32s3` 合并后的有效默认值；
  2. 板卡构建预设（`main/boards/zhengchen/minicam/config.json` 的
     `sdkconfig_append`）——它排在目标默认值之后，能覆盖它们；
  3. 入库的 `sdkconfig`——`idf.py build` 直接读它，不读 defaults。

数值上限写在下面的 `NUMERIC_CEILING` / `REQUIRED_ON` / `REQUIRED_OFF` 里，是
契约本体，不是构建旋钮。

运行：python3 -m unittest scripts.tests.test_network_throughput_defaults -v
（纯逻辑，不需要设备、不跑构建。）
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build", ROOT / "scripts/build.py")
build = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build)

# 机器人的板卡目录与构建条目（同 main/boards/zhengchen/minicam/config.json）。
ROBOT_BOARD = "zhengchen/minicam"
ROBOT_BUILD = "zhengchen-minicam"

# 数值约束（2026-09-12 修正）。
#
# 初版这里写的是「不低于 IDF 默认」的**下限**，方向是错的：接收缓冲与 lwIP
# 窗口是**真实分配的内部 RAM**，往上调就是在跟 camera/LVGL/AFE 抢内存。
# 实测：提到 8/16/6 + 窗口 16384 后，设备 free sram 从 41067 掉到 9919、
# minimal 掉到 2095，出现 `Failed to create music start task`（OOM）。
#
# 所以这里锁的是**上限**：这组值不得高于当前值。要提性能请走 PSRAM 承载
# 网络缓冲（CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP），那是另一条路径。
#
# lwIP 窗口为何连「上调」都不需要：带宽时延积分析——局域网 RTT 3.5–30ms
# 下，5760B 窗口的理论上限是 188–1607KB/s，而验收目标是 200KB/s。窗口从未
# 限制过吞吐，为它花内存是纯浪费。
NUMERIC_CEILING = {
    "CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM": 3,
    "CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM": 6,
    "CONFIG_ESP_WIFI_RX_BA_WIN": 3,
    "CONFIG_LWIP_TCP_WND_DEFAULT": 5760,
    "CONFIG_LWIP_TCP_SND_BUF_DEFAULT": 5760,
}

# IRAM 加速：**必须关闭**（2026-09-12 二次修正）。
#
# 上一版这里写的是「必须开启」，依据是「IRAM 加速是段内挪移、不占运行时堆」。
# **那条依据是错的。** IRAM 文本与 DRAM 数据共享同一 333.8KB 段，文本变长就把
# _data_start 往后推——实测两项加速共吃 **17.8KB** 运行时常驻内存
# （_data_start 0x3fc9d000 → 0x3fca1700）。
#
# 只回退缓冲与窗口、保留 IRAM 加速后，设备 free sram 仅回到 17-19KB，
# 音乐 worker 的 8192B 栈（MALLOC_CAP_INTERNAL）仍分不出来：
#     E MusicPlayer: Failed to create music worker task
#     E Application: Failed to start music: ...
# minimal sram 掉到 7663 < 8192。
#
# 所以它列入 REQUIRED_OFF：内部 SRAM 没有余量可供任何吞吐调优。
# 要吞吐请走 PSRAM 承载网络缓冲，或解上游组件的调度天花板（那条不花内存）。
# IDF 默认即 y，本板显式关闭。
REQUIRED_ON = ()

# 刻意不启用的开关（ADR-0011 已裁定）。列在这里是为了让「顺手打开它」变成
# 一次需要改测试的决定，而不是一次静默优化。
#   - ESP_WIFI_IRAM_OPT / RX_IRAM_OPT：见上，吃 17.8KB 运行时常驻内存，本板吃不起。
#   - SPIRAM_TRY_ALLOCATE_WIFI_LWIP：初版以「延迟」为由否决；回退后它是吞吐的
#     主要出路，但在未完成延迟验证前不得擅自开启——故仍列在这里。
#   - LWIP_WND_SCALE：影响所有连接（含 60ms/帧的音频 WebSocket），
#     收益在大文件、风险在音频。
REQUIRED_OFF = (
    "CONFIG_ESP_WIFI_IRAM_OPT",
    "CONFIG_ESP_WIFI_RX_IRAM_OPT",
    "CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP",
    "CONFIG_LWIP_WND_SCALE",
)


def _read_assignments(path):
    """解析 sdkconfig 片段：`CONFIG_X=v` 与 `# CONFIG_X is not set`（后者为 n）。"""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# CONFIG_") and stripped.endswith(" is not set"):
            values[stripped[len("# "):-len(" is not set")]] = "n"
        elif stripped.startswith("CONFIG_") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key] = value
    return values


def _effective_defaults(target):
    """合并项目基础默认值与目标默认值（目标覆盖基础，同 IDF 的加载顺序）。"""
    merged = _read_assignments(ROOT / "sdkconfig.defaults")
    target_values = _read_assignments(ROOT / f"sdkconfig.defaults.{target}")
    return build._sdkconfig_assignments(
        [f"{key}={value}" for key, value in {**merged, **target_values}.items()]
    )


def _board_append(board, build_name):
    """取板卡构建预设的 sdkconfig_append（它排在目标默认值之后）。"""
    config_path = ROOT / "main/boards" / board / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for build_config in config.get("builds", []):
        if build_config.get("name") == build_name:
            return list(build_config.get("sdkconfig_append", []))
    raise AssertionError(f"{config_path}: build {build_name!r} not found")


def _ceiling_violations(assignments):
    """返回所有超出内存上限的项；空列表即合规。

    数值项要求**显式写出**（缺失即失败）：这批值必须出现在 defaults 里，
    否则读的人看不见、diff 里也看不见，正是上次静默退回高值的方式。
    布尔项相反——缺失等于 IDF 默认，而 IDF 对这里两个开关的默认恰好就是
    我们要的值（加速项 y、PSRAM 承载项 n），所以「删掉那行覆盖」是合法修法，
    只有显式写反才失败。
    """
    problems = []
    for key, maximum in sorted(NUMERIC_CEILING.items()):
        raw = assignments.get(key)
        if raw is None:
            problems.append(f"{key} 未在 defaults 中显式给出（需 <= {maximum}）")
            continue
        try:
            value = int(raw)
        except ValueError:
            problems.append(f"{key}={raw} 不是数字（需 <= {maximum}）")
            continue
        if value > maximum:
            problems.append(
                f"{key}={value} > {maximum}——这是真实分配的内部 RAM，"
                "上调会与 camera/LVGL/AFE 抢内存（曾导致 OOM）"
            )
    for key in REQUIRED_ON:
        # 缺失 = IDF 默认 y = 符合意图；只有显式关掉才算回退。
        if assignments.get(key) == "n":
            problems.append(f"{key} 被显式关闭，必须开启（吞吐换内存的那笔交易已作废）")
    for key in REQUIRED_OFF:
        # 缺失 = IDF 默认 n = 符合意图；只有显式打开才算越界。
        if assignments.get(key) == "y":
            problems.append(f"{key} 被显式开启，ADR-0011 已裁定不启用")
    return problems


class NetworkThroughputDefaultsTests(unittest.TestCase):
    """入库配置不得突破内存上限、且保留吞吐来源（ADR-0011 / issue #23）。"""

    def assert_within_ceiling(self, assignments, where):
        problems = _ceiling_violations(assignments)
        self.assertEqual(
            [],
            problems,
            f"{where} 突破网络内存上限（ADR-0011 / issue #23）：\n  "
            + "\n  ".join(problems),
        )

    def test_target_defaults_stay_within_memory_ceiling(self):
        self.assert_within_ceiling(
            _effective_defaults("esp32s3"),
            "sdkconfig.defaults + sdkconfig.defaults.esp32s3",
        )

    def test_board_preset_does_not_raise_the_ceiling(self):
        # 板卡预设排在目标默认值之后，能覆盖它们——这里正是它会被调高的地方。
        merged = _effective_defaults("esp32s3")
        appended = build._sdkconfig_assignments(
            _board_append(ROBOT_BOARD, ROBOT_BUILD)
        )
        self.assert_within_ceiling(
            {**merged, **appended},
            f"{ROBOT_BOARD} 构建预设 {ROBOT_BUILD} 的生效配置",
        )

    def test_checked_in_sdkconfig_stays_within_memory_ceiling(self):
        # `idf.py build` 读的是这个文件，不是 defaults；只改 defaults 会静默失配。
        self.assert_within_ceiling(
            _read_assignments(ROOT / "sdkconfig"),
            "入库的 firmware/sdkconfig",
        )

    def test_no_board_preset_disables_wifi_iram_optimizations(self):
        # 这条不针对机器人的板卡，而是拦住「某个板卡顺手关掉加速」这一类回退。
        offenders = []
        for config_path in sorted((ROOT / "main/boards").rglob("config*.json")):
            config = json.loads(config_path.read_text(encoding="utf-8"))
            for build_config in config.get("builds", []):
                for option in build_config.get("sdkconfig_append", []):
                    key, _, value = option.partition("=")
                    if key in REQUIRED_ON and value != "y":
                        offenders.append(
                            f"{config_path.relative_to(ROOT)}: {option}"
                        )
        self.assertEqual([], offenders, "板卡预设关掉了 WiFi IRAM 加速")

    def test_static_rx_buffers_cover_the_ba_window(self):
        # IDF 的 Kconfig 帮助明确建议静态接收缓冲数 >= 聚合窗口，
        # 否则大聚合窗口在缓冲不足时反而拖低吞吐。
        assignments = _effective_defaults("esp32s3")
        static_rx = int(assignments["CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM"])
        ba_window = int(assignments["CONFIG_ESP_WIFI_RX_BA_WIN"])
        self.assertGreaterEqual(
            static_rx,
            ba_window,
            "静态接收缓冲数应不少于 AMPDU 接收聚合窗口（IDF Kconfig 建议）",
        )


if __name__ == "__main__":
    unittest.main()
