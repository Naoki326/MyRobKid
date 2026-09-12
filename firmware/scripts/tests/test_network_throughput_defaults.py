#!/usr/bin/env python3
"""网络吞吐基线契约（ADR-0011 / issue #23）。

判据只有一条：入库的 WiFi/网络栈配置不得低于吞吐下限。

为什么必须有这条测试：**配置被改回低值后设备仍能正常启动**——运行时的任何
行为断言都覆盖不到这种回退，只有数值契约能。本仓的这批配置是从上游（ESP-HI /
ESP32-C3）复制来的省内存取向，历史上已经因此把吞吐压在 10–30KB/s 量级；没有
契约，下次从上游同步默认配置时会静默退回。

三个真相源都要查（ADR-0002 的纪律：改一处不够）：

  1. `sdkconfig.defaults` + `sdkconfig.defaults.esp32s3` 合并后的有效默认值；
  2. 板卡构建预设（`main/boards/zhengchen/minicam/config.json` 的
     `sdkconfig_append`）——它排在目标默认值之后，能把下限压回去；
  3. 入库的 `sdkconfig`——`idf.py build` 直接读它，不读 defaults。

数值下限写在下面的 `NUMERIC_FLOOR` / `REQUIRED_ON` / `REQUIRED_OFF` 里，是
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

# 数值下限。取值理由见 ADR-0011：向 IDF 默认靠拢但不照抄，给内存留余量。
NUMERIC_FLOOR = {
    # IDF 默认分别是 10 / 32 / 6；这里取中，不照抄。
    "CONFIG_ESP_WIFI_STATIC_RX_BUFFER_NUM": 8,
    "CONFIG_ESP_WIFI_DYNAMIC_RX_BUFFER_NUM": 16,
    "CONFIG_ESP_WIFI_RX_BA_WIN": 6,
    # IDF 默认 5760（= 4×MSS），未开窗口缩放时上限 65535。
    "CONFIG_LWIP_TCP_WND_DEFAULT": 16384,
    "CONFIG_LWIP_TCP_SND_BUF_DEFAULT": 16384,
}

# IDF 默认即 y，本项目曾显式关掉（省 10KB + 17KB IRAM，代价是吞吐下降）。
REQUIRED_ON = (
    "CONFIG_ESP_WIFI_IRAM_OPT",
    "CONFIG_ESP_WIFI_RX_IRAM_OPT",
)

# 刻意不启用的开关（ADR-0011 已裁定）。列在这里是为了让「顺手打开它」变成
# 一次需要改测试的决定，而不是一次静默优化。
#   - SPIRAM_TRY_ALLOCATE_WIFI_LWIP：省内部 RAM，但 IDF 警告会引入额外延迟，
#     与本项目音频路径对延迟的敏感冲突（ADR-0003 的省电锯齿同源）。
#   - LWIP_WND_SCALE：影响所有连接（含 60ms/帧的音频 WebSocket），
#     收益在大文件、风险在音频。
REQUIRED_OFF = (
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


def _floor_violations(assignments):
    """返回所有违反吞吐基线的项；空列表即合规。

    数值项要求**显式写出**（缺失即失败）：这批值必须出现在 defaults 里，
    否则读的人看不见、diff 里也看不见，正是上次静默退回低值的方式。
    布尔项相反——缺失等于 IDF 默认，而 IDF 对这里两个开关的默认恰好就是
    我们要的值（加速项 y、PSRAM 承载项 n），所以「删掉那行覆盖」是合法修法，
    只有显式写反才失败。
    """
    problems = []
    for key, minimum in sorted(NUMERIC_FLOOR.items()):
        raw = assignments.get(key)
        if raw is None:
            problems.append(f"{key} 未在 defaults 中显式给出（需 >= {minimum}）")
            continue
        try:
            value = int(raw)
        except ValueError:
            problems.append(f"{key}={raw} 不是数字（需 >= {minimum}）")
            continue
        if value < minimum:
            problems.append(f"{key}={value} < {minimum}")
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
    """入库配置不得低于吞吐下限（ADR-0011 / issue #23）。"""

    def assert_meets_floor(self, assignments, where):
        problems = _floor_violations(assignments)
        self.assertEqual(
            [],
            problems,
            f"{where} 低于网络吞吐基线（ADR-0011 / issue #23）：\n  "
            + "\n  ".join(problems),
        )

    def test_target_defaults_meet_throughput_floor(self):
        self.assert_meets_floor(
            _effective_defaults("esp32s3"),
            "sdkconfig.defaults + sdkconfig.defaults.esp32s3",
        )

    def test_board_preset_does_not_lower_the_floor(self):
        # 板卡预设排在目标默认值之后，能覆盖它们——这里正是它会被压回去的地方。
        merged = _effective_defaults("esp32s3")
        appended = build._sdkconfig_assignments(
            _board_append(ROBOT_BOARD, ROBOT_BUILD)
        )
        self.assert_meets_floor(
            {**merged, **appended},
            f"{ROBOT_BOARD} 构建预设 {ROBOT_BUILD} 的生效配置",
        )

    def test_checked_in_sdkconfig_meets_throughput_floor(self):
        # `idf.py build` 读的是这个文件，不是 defaults；只改 defaults 会静默失配。
        self.assert_meets_floor(
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
