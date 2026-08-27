# 设备寻址与固件分发纪律

机器人靠**寻址三件套**找到 MyRobKid 服务端：固件内 OTA 地址（`firmware/sdkconfig` 的 `CONFIG_OTA_URL`）、OTA 响应下发的 WebSocket 地址（`server/data/.config.yaml` 的 `server.websocket`）、固件分发目录（`server/data/bin/`）。决定：三处配置显式入库并保持一致（OTA 地址指向 `http://192.168.18.172:8003/xiaozhi/ota/`，WS 地址指向 `ws://192.168.18.172:8002/xiaozhi/v1/`）；`data/bin/` 只放验证过可启动的固件，版本号必须单调递增且跳过被污染的 2.4.7。

## Context（两次事故）

- **2.4.5 事故**：固件源码 vendoring 后用主仓重编，而 2.4.4 的 `CONFIG_OTA_URL` 是当年编译时临时改动、从未存档——重编产物带着官方默认地址，设备开机即被官方云接管（童声/官方角色），且从此不再回来找我们的 OTA，只能 USB 救援。
- **升级陷阱事故**：`data/bin/` 中出现版本号更高的坏固件（2.4.7，主仓源码在 OTA 地址未修正前编译的产物）。设备每次开机自检即被"升级"，官方地址覆盖 NVS 中的 WebSocket 配置，形成无限循环——无论怎么刷回 2.4.6，下次开机又被拉走。终结手段：移除陷阱文件 + 手工构造 ota_data 锁定 ota_0。

## Consequences

- 诊断口诀：**设备"变官方"，先查 `data/bin/` 有没有更高的版本文件**，再查固件的 OTA 地址。
- 主仓 `sdkconfig` 的 OTA 地址是安全底线，改 venv/路径/重装环境后需确认 macOS 防火墙仍放行服务端 python（socketfilterfw 按可执行路径放行）。
- 宿主机拓扑（不可见于代码）：服务端由 launchd `com.xiaozhi.server` 托管主仓 venv；nginx（8080，`/xiaozhi/` 反代 8002/8003）为备用入口与花生壳公网映射目标；MLX TTS 服务由 launchd `com.yuanbao.mlxtts` 托管（端口 9753）。
