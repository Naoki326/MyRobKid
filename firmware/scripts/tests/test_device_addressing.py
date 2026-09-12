#!/usr/bin/env python3
"""寻址纪律守护（ADR-0002 的 2.4.5 事故 / 2.4.24 复核）。

本测试只守一件事：**编译产物里的寻址不得指回官方云，板卡身份不得被重置**。

为什么需要它：这两样都能在完全无法察觉的情况下悄悄发生，而后果是设备
「变官方」——开机即被官方云接管，此后无论刷哪个版本都变回官方音色，只能
USB 直刷救援（ADR-0002 记录的 2.4.5 事故与「升级陷阱」事故）。

触发路径已经实测过（2026-09-12，issue #23 实施时）：

    rm sdkconfig && idf.py reconfigure

Kconfig 会从零按来源链重新求值。若当时没走 `scripts/build.py`，板卡预设
（`sdkconfig_append`）不参与，于是：

  - `CONFIG_OTA_URL` 落回 `main/Kconfig.projbuild` 的默认值；
  - `CONFIG_BOARD_TYPE_*` 落回按 IDF_TARGET 选的上游默认板卡。

两者都是「没有报错、构建成功、产物可启动」的静默失败——只有断言能拦住。

运行：python3 -m unittest scripts.tests.test_device_addressing -v
（纯逻辑，不需要设备、不跑构建、不需要网络。）
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 机器人的板卡身份（同 main/boards/zhengchen/minicam/config.json）。
ROBOT_BOARD = "zhengchen/minicam"
ROBOT_BUILD = "zhengchen-minicam"
ROBOT_BOARD_TYPE = "CONFIG_BOARD_TYPE_ZHENGCHEN_MINICAM"

# 官方云（固件客户端在固件上游 `main/Kconfig.projbuild` 里的默认值）。
# 产物中出现它 = 设备下一次开机会去官方云自检。
OFFICIAL_CLOUD_HOSTS = ("api.tenclass.net",)

# Kconfig 里被 reconfigure 掉成的上游默认板卡（ESP32-S3）。
# 它不是「错误值」本身，而是「板卡身份被重置」的信号。
UPSTREAM_S3_DEFAULT_BOARD = "CONFIG_BOARD_TYPE_BREAD_COMPACT_WIFI"


def _read_assignments(path):
    """解析 sdkconfig：`CONFIG_X=v` 与 `# CONFIG_X is not set`（后者为 n）。"""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# CONFIG_") and stripped.endswith(" is not set"):
            values[stripped[len("# "):-len(" is not set")]] = "n"
        elif stripped.startswith("CONFIG_") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key] = value
    return values


def _unquote(value):
    return value.strip().strip('"')


def _kconfig_default_ota_url():
    """读 main/Kconfig.projbuild 里 OTA_URL 的默认值（最后一层兜底）。"""
    content = (ROOT / "main/Kconfig.projbuild").read_text(encoding="utf-8")
    block = re.search(
        r"^config OTA_URL\s*$(.*?)^\S",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if not block:
        raise AssertionError("main/Kconfig.projbuild: config OTA_URL not found")
    default = re.search(r'^\s*default\s+"([^"]+)"\s*$', block.group(1), re.MULTILINE)
    if not default:
        raise AssertionError("main/Kconfig.projbuild: OTA_URL has no default")
    return default.group(1)


def _board_preset_ota_url():
    """读板卡预设里注入的 CONFIG_OTA_URL（上层覆盖）。"""
    path = ROOT / "main/boards" / ROBOT_BOARD / "config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    for build_config in config.get("builds", []):
        if build_config.get("name") != ROBOT_BUILD:
            continue
        for option in build_config.get("sdkconfig_append", []):
            key, _, value = option.partition("=")
            if key == "CONFIG_OTA_URL":
                return _unquote(value)
    raise AssertionError(f"{path}: build {ROBOT_BUILD!r} does not set CONFIG_OTA_URL")


class DeviceAddressingTests(unittest.TestCase):
    """入库的固件产物与预设不得指回官方云（ADR-0002）。"""

    def assert_not_official(self, url, where):
        for host in OFFICIAL_CLOUD_HOSTS:
            self.assertNotIn(
                host,
                url,
                f"{where} 指向官方云（{host}）。设备下一次开机会去官方云自检，"
                "被接管后无论刷哪个版本都变回官方音色，只能 USB 直刷救援"
                "（ADR-0002 的 2.4.5 事故）。",
            )

    def test_checked_in_sdkconfig_is_not_official_cloud(self):
        # `idf.py build` 直接读这个文件——它是真正被编译进去的地址。
        assignments = _read_assignments(ROOT / "sdkconfig")
        raw = assignments.get("CONFIG_OTA_URL")
        self.assertIsNotNone(
            raw,
            "入库的 firmware/sdkconfig 没有 CONFIG_OTA_URL——"
            "很可能是删掉后重新生成的，请用 "
            "`python3 scripts/build.py zhengchen/minicam --name zhengchen-minicam`",
        )
        self.assert_not_official(_unquote(raw), "入库的 firmware/sdkconfig")

    def test_kconfig_fallback_is_not_official_cloud(self):
        # 最后一层兜底：删掉 sdkconfig 裸跑 idf.py reconfigure 时落到它。
        # 若它仍是官方地址，那条路径就会静默产出「变官方」的固件。
        self.assert_not_official(
            _kconfig_default_ota_url(),
            "main/Kconfig.projbuild 的 OTA_URL 默认值",
        )

    def test_board_preset_is_not_official_cloud(self):
        self.assert_not_official(
            _board_preset_ota_url(),
            f"{ROBOT_BOARD} 构建预设的 CONFIG_OTA_URL",
        )

    def test_addressing_agrees_across_sources(self):
        # 寻址三件套里两处固件侧来源（Kconfig 兜底与板卡预设）必须同值。
        # 不同值意味着「走 build.py」与「裸跑 reconfigure」会产出不同寻址的
        # 固件——这正是 2.4.5 事故的形状。
        self.assertEqual(
            _kconfig_default_ota_url(),
            _board_preset_ota_url(),
            "Kconfig 默认值与板卡预设的 OTA_URL 不一致：两条构建路径会产出"
            "寻址不同的固件（ADR-0002 寻址纪律要求三处一致）",
        )

    def test_sdkconfig_keeps_robot_board_identity(self):
        # 板卡身份被重置是「裸跑 reconfigure」的可靠信号：它和 OTA_URL 同时
        # 掉回上游默认，且同样静默。板卡身份错了，状态上报、板级外设初始化
        # 全都跟着错。
        assignments = _read_assignments(ROOT / "sdkconfig")
        self.assertEqual(
            "y",
            assignments.get(ROBOT_BOARD_TYPE),
            f"入库的 firmware/sdkconfig 不是本项目的板卡（缺 {ROBOT_BOARD_TYPE}=y）。"
            "板卡身份被重置通常意味着裸跑了 `idf.py reconfigure`——"
            "它会同时丢掉板卡身份与 OTA 寻址。请用 "
            "`python3 scripts/build.py zhengchen/minicam --name zhengchen-minicam`",
        )
        self.assertNotEqual(
            "y",
            assignments.get(UPSTREAM_S3_DEFAULT_BOARD),
            f"入库的 firmware/sdkconfig 带上了上游默认板卡 "
            f"（{UPSTREAM_S3_DEFAULT_BOARD}=y）——板卡身份被重置。请用 "
            "`python3 scripts/build.py zhengchen/minicam --name zhengchen-minicam`",
        )

    def test_board_identity_is_derived_from_the_board_directory(self):
        # 板卡身份不写在 config.json 里，而是 scripts/build.py 按板卡目录名推导
        # （zhengchen/minicam -> CONFIG_BOARD_TYPE_ZHENGCHEN_MINICAM）。
        # 守推导结果，因为它是「走 build.py」与「裸跑 reconfigure」的分岔点：
        # 后者拿不到这个值，于是静默落到上游默认板卡。
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build", ROOT / "scripts/build.py"
        )
        build = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(build)
        self.assertEqual(
            ROBOT_BOARD_TYPE,
            build._resolve_board_config(ROBOT_BOARD, "esp32s3", []),
            f"{ROBOT_BOARD} 推导出的板卡身份变了——入库的 sdkconfig 与新构"
            "建产物会不再一致",
        )


if __name__ == "__main__":
    unittest.main()
