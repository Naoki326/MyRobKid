# 设备寻址与固件分发纪律

机器人靠**寻址三件套**找到 MyRobKid 服务端：固件内 OTA 地址（`firmware/sdkconfig` 的 `CONFIG_OTA_URL`）、OTA 响应下发的 WebSocket 地址（`server/data/.config.yaml` 的 `server.websocket`）、固件分发目录（`server/data/bin/`）。决定：三处配置显式入库并保持一致（OTA 地址指向 `http://chenMac-mini.local:8080/xiaozhi/ota/`，:8080 为 nginx 入口；WS 地址指向 `ws://chenMac-mini.local:8002/xiaozhi/v1/`）；`data/bin/` 只放验证过可启动的固件，版本号必须单调递增且跳过被污染的 2.4.7。

## Context（两次事故）

- **2.4.5 事故**：固件源码 vendoring 后用主仓重编，而 2.4.4 的 `CONFIG_OTA_URL` 是当年编译时临时改动、从未存档——重编产物带着官方默认地址，设备开机即被官方云接管（童声/官方角色），且从此不再回来找我们的 OTA，只能 USB 救援。
- **升级陷阱事故**：`data/bin/` 中出现版本号更高的坏固件（2.4.7，主仓源码在 OTA 地址未修正前编译的产物）。设备每次开机自检即被"升级"，官方地址覆盖 NVS 中的 WebSocket 配置，形成无限循环——无论怎么刷回 2.4.6，下次开机又被拉走。终结手段：移除陷阱文件 + 手工构造 ota_data 锁定 ota_0。

## 地址改用 mDNS 名（2026-09-10）

宿主机 Mac 的 DHCP 租约会漂移（`192.168.18.172` → `192.168.18.166`），写死 IP 的寻址三件套在每次 IP 变更后全链路失联。决定：OTA 地址与 WS 地址一律写成 mDNS 名 `chenMac-mini.local`（`scutil --get LocalHostName`），IP 只作为备用。

- 固件侧能力已具备：`CONFIG_LWIP_DNS_SUPPORT_MDNS_QUERIES=y` + 板卡 `mdns_init()`，WebSocket 走的 `EspTcp::gethostbyname` 为 IPv4，`.local` 可解析。
- 两个来源必须同时改：`firmware/main/boards/zhengchen/minicam/config.json` 的 `sdkconfig_append`（`scripts/build.py` 构建时生成 sdkconfig 的真相源）与入库的 `firmware/sdkconfig`（`idf.py build` 直接使用的那个）。
- 设备端另有 NVS 的 `wifi/ota_url`，优先级高于 sdkconfig；改完必须经配网页重设，否则旧地址继续生效。
- 备用地址写在 `server.websocket_backup`，用花生壳公网域名（具体域名见不入库的 `server/data/.config.yaml`；仓库公开，地址不写进文档）。公网路径与局域网无关，IP 怎么漂都不受影响。⚠️ 必须写 `wss://`：固件 `web_socket.cc` 的 URI 解析只在 `protocol == "wss"` 时给 443 端口，`https://` 会被当成 80 端口做 TLS，必失败（花生壳的 80 也不通）。TLS 校验收 `esp_crt_bundle`，已验证 `.vicp.fun` 证书链（RapidSSL → DigiCert Global Root G2）在 ESP-IDF 内置 bundle 中，校验通过；证书有效期至 2026-11-21，到期前需更新。
- **内网永远是首选，不落 NVS**：`WebsocketProtocol` 只在本次开机内用 RAM 记住“主地址刚失败过”，主地址一连上就清掉；备用成功后启动 `ws_probe` 后台任务每 30 秒 TCP 探一次主地址，探通即切回。旧行为是“切到备用就把备用写进 NVS 当主地址”（粘住走公网，直到下次 OTA 才刷回），已废弃。探测走独立任务而非同步重试，是因为主地址不可达时 lwIP 的 `.local` 解析要退避 1+1+2+3 秒（DNS_TMR_INTERVAL=1s、DNS_MAX_RETRIES=4）才报失败，同步重试会把这段等待压到每一轮对话的开头上。

## 2.4.19：主备切换策略修正（2026-09-10）

2.4.18 之前的策略是「主地址失败 → 切备用 → **把备用写进 NVS 当主地址**」，一旦局域网短暂抽风就会粘在公网上，直到下次成功 OTA 才刷回。2.4.19 改成：内网永远首选，偏好只存 RAM，后台 30 秒探测主地址、恢复即切回（细节见上一节）。

- 构建与发布：`firmware/CMakeLists.txt` 的 `PROJECT_VER` 改为 `2.4.19`，`idf.py build` 产物 `firmware/build/xiaozhi.bin`（3 487 024 字节，sha256 `7c6098377977db27a8c8350a1ec30aa4c7c5640bcff975359c20b09ba301f873`）拷贝为 `server/data/bin/zhengchen-minicam_2.4.19.bin`。
- 上线方式：开机 OTA 自动升级（设备当前 2.4.18，版本号单调递增）；`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`，未标记有效的镜像失败会回滚。
- 下载链路已验：局域网（`http://chenMac-mini.local:8080/xiaozhi/xiaozhi/ota/download/zhengchen-minicam_2.4.19.bin`）与公网（花生壳域名，25s 传完 3.5MB）均 200 且 sha256 一致。

## Consequences

- 诊断口诀：**设备"变官方"，先查 `data/bin/` 有没有更高的版本文件**，再查固件的 OTA 地址。
- 主仓 `sdkconfig` 的 OTA 地址是安全底线，改 venv/路径/重装环境后需确认 macOS 防火墙仍放行服务端 python（socketfilterfw 按可执行路径放行）。
- 宿主机拓扑（不可见于代码）：服务端由 launchd `com.xiaozhi.server` 托管主仓 venv；nginx（8080，`/xiaozhi/` 反代 8002/8003）为备用入口与花生壳公网映射目标；MLX TTS 服务由 launchd `com.yuanbao.mlxtts` 托管（端口 9753）。

## 2.4.8：固件构建统一在主仓

旧工程（固件雏形）的本地组件修改不可丢：`managed_components/78__esp-wifi-connect`（配网增强）与 `txp666__otto-emoji-gif-component`（定制表情）均不入库，registry 拉取的纯净版会缺失。决定：**固件构建统一在主仓**，每次构建前从旧工程目录复制这两个组件（或任何后续本地修改过的组件），2.4.8 即按此方式集成音乐播放修复 + 省电补丁 + 正确寻址，USB 直刷 ota_0 生效。
