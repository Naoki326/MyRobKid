# 已删除：`资料包/源码/`（上游 v2.4.4 + 本地定制）

**删除日期**：2026-09-13
**原因**：清理 1.1G 的旧源码目录；设备实际运行的是 `firmware/`（v2.4.30，入库）。

## 这里保存了什么

删除前把该目录里**不在 `firmware/` 里的独有内容**导出成 patch：

| 文件 | 内容 |
|---|---|
| `v2.4.4-local-commits.patch` | 该目录自己的 **2 个本地提交**（相对上游 `bb9122a`） |
| `v2.4.4-local-uncommitted.patch` | 删除时的 **20 个文件未提交改动**（795 插入 / 22 删除），含新文件 `music_player.cc/.h` |

### 两个本地提交

| commit | 日期 | 说明 |
|---|---|---|
| `db64aac` | 2026-08-22 | add zhengchen-minicam board support (local customization) |
| `319e19d` | 2026-08-23 | zhengchen-minicam: 摄像头监控 + SmartConfig 配网优化 + mDNS 直连 |

## 为什么不能直接删

该目录**不是纯上游代码**，它含 zhengchen-minicam 板级的**原创定制**。删除前逐项核对过与 `firmware/` 的关系：

| 定制内容 | `firmware/` 里的状态 |
|---|---|
| zhengchen-minicam 板级代码（`zhengchen_minicam.cc` / LCD 显示） | ✅ 已迁移（且已演进：676 行 vs 790 行） |
| 摄像头快照服务 | ✅ 已迁移 |
| SmartConfig 配网状态机 | ✅ 已迁移 |
| `music_player.cc/.h` | ✅ 已迁移（且已大幅演进：958/265 行 vs 366/60 行） |
| **服务端下发省电配置**（`device_config` → `SetSleepTimeout` → `power_save_timeout`） | ❌ **`firmware/` 里没有** |

### 已知缺口（保留记录）

`firmware/` **不解析** OTA 响应里的 `device_config.power_save_timeout`：

- 服务端一直在下发 `{"power_save_timeout": 60}`（见 `server/data/device_config.json` 与 OTA handler）
- 旧版 `main/ota.cc` 有对应解析（写入 `Settings("power")` 的 `sleep_timeout_s`）
- 旧版 `PowerSaveTimer` 有 `SetSleepTimeout()` 方法

**影响**：该配置在当前固件里**不生效**。此缺口的具体影响（是否与唤醒/省电行为异常有关）**尚未查证**，留待后续。相关代码在 `v2.4.4-local-uncommitted.patch` 里（`main/ota.cc`、`main/boards/common/power_save_timer.cc`）。

## 如何恢复

```bash
# 重建上游基线（v2.4.4）
git clone <upstream-repo> /tmp/xiaozhi-v2.4.4 && cd /tmp/xiaozhi-v2.4.4
git checkout bb9122a                    # 两个本地提交的基点

# 应用本地提交与未提交改动
git am /Users/chenjingjing/Robot/docs/legacy/v2.4.4-local-commits.patch
git apply /Users/chenjingjing/Robot/docs/legacy/v2.4.4-local-uncommitted.patch
```

> 注：`git apply` 前需确认目标树与基点一致；patch 基于 `bb9122a`（"Add streamed notify playback (#2191)"）。

## 未保存的内容

- `.claude/`（编辑器配置，无价值）
- `build/`、`managed_components/`（编译产物与可重新拉取的依赖）

## 仍然保留的（未删除）

`资料包/` 下这些**没有**删（它们不是源码）：

- `烧录工具/`（34M，含 `flash_download_tool.zip` 与烧录教程）
- `备用固件（黑:白表情包）/`（v2.2.6.7 两个 zip）
- `接口说明.docx`
