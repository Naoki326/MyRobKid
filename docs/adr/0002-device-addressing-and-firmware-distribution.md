# 设备寻址与固件分发纪律

机器人靠**寻址三件套**找到 MyRobKid 服务端：固件内 OTA 地址（`firmware/sdkconfig` 的 `CONFIG_OTA_URL`）、OTA 响应下发的 WebSocket 地址（`server/data/.config.yaml` 的 `server.websocket`）、固件分发目录（`server/data/bin/`）。决定：三处必须一致（当前 OTA 地址为 `http://chenMac-mini.local:8080/xiaozhi/ota/`，:8080 走 nginx 入口；WS 地址为 `ws://chenMac-mini.local:8002/xiaozhi/v1/`）；`data/bin/` 只放验证过可启动的固件，版本号必须单调递增且跳过被污染的 2.4.7。

**入库口径**：`.gitignore:13/19` 排除了 `server/data/` 与 `server/config.yaml`，地址的**值**只登记在部署机上，不入库；仓库（公开）只存规范、模板与固件内编译进去的地址。要核对现场地址就去宿主机读文件，别指望文档里有。

**2026-09-12 修正**：上述口径写的是「地址的值不入库」，但**实际早已被自己违反**——`main/boards/zhengchen/minicam/config.json` 的 `sdkconfig_append` 里就写着 `CONFIG_OTA_URL` 的完整值，且它是本次寻址链路的真相源之一。本仓只服务自己的设备（其余 147 块板卡的配置是上游余留），地址值入库不构成额外风险。因此口径修订为：**地址的值以「能构建出正确产物」为准，允许入库；真正必须守的是产物不出现官方云地址**。守的方式从「代码审查纪律」换成 `firmware/scripts/tests/test_device_addressing.py` 的断言（6 条，纯逻辑）。

## 2026-09-12：地址兜底与板卡身份归位（Kconfig 层）

2.4.24 实施 issue #23（网络吞吐）时，为重新生成 `sdkconfig` 而删掉它并裸跑 `idf.py reconfigure`——产物里 `CONFIG_OTA_URL` **掉回了上游默认的官方云地址** `https://api.tenclass.net/xiaozhi/ota/`。当场还原，但暴露了一个结构性缺口：**官方地址一直是地址链路的最后一层兜底**。

地址的求值链（低 → 高）是：**Kconfig 默认值** → `sdkconfig.defaults*`（无此项）→ **板卡预设 `sdkconfig_append`**（经 `build/xiaozhi-build.sdkconfig.defaults` 片段传入）。平时由板卡预设覆盖，看不出问题；一旦绕过 `scripts/build.py` 以干净环境重新配置（删 `sdkconfig` 与 build 目录后直接 `reconfigure`），就落到 Kconfig 默认值——而它指向官方云。这就是 2.4.5 事故的完整机制。

决定：**把 Kconfig 的 `OTA_URL` 默认值改成自建服务端地址**，让最后一层兜底也指向自己。改在 `main/Kconfig.projbuild`，与板卡预设同值，两者一致性由测试守护。

**四条路径的实测行为**（2026-09-12，修好 Kconfig 默认值之后）：

| 配置方式 | `CONFIG_OTA_URL` | `CONFIG_BOARD_TYPE_*` |
|---|---|---|
| 干净环境裸 `reconfigure`（新 build 目录） | ✅ 自建 | ⚠️ 上游默认板卡 |
| `scripts/build.py zhengchen/minicam --name zhengchen-minicam` | ✅ 自建 | ✅ 正确 |
| 删 `sdkconfig` 但 build 目录尚在 | ✅ 自建（从片段） | ✅ 正确（从片段） |
| 删 `sdkconfig` 且删片段 | **cmake 硬报错**（`SDKCONFIG_DEFAULTS ... does not exist`） | 同左 |

最后一行是关键发现：**这台机器上删掉 `sdkconfig` 后裸跑 `reconfigure` 不会静默产出官方地址** —— CMakeCache 记住了 `SDKCONFIG_DEFAULTS` 指向 `build/xiaozhi-build.sdkconfig.defaults`，片段不在就直接失败。真正的风险窗口是**「新 build 目录」或「`fullclean` 之后」**，那里才无片段可依赖。

**澄清两处此前的错误归因**（均为本次实测推翻，勿再引用）：

1. 2.4.24 实施时那次「裸跑 `reconfigure` 把 OTA_URL 掉回官方」的记录不完整。当时 build 目录里的片段是被上一次 `build.py` 写入过的，而**删 `sdkconfig` 不删片段时 OTA_URL 实际会从片段保住**（上表第三行）。当时看到官方地址，是因为更早的操作已把片段也清掉了。结论不变（Kconfig 兜底是官方地址，必须改），但触发条件要写准：**是「无片段」，不是「无 sdkconfig」**。
2. 本条最初草稿曾把「板卡身份掉成 `BREAD_COMPACT_WIFI`」解释为「来自 CMakeCache 的 `BOARD_NAME`，所以不会被重置」，**这是错的**。板卡身份与 OTA_URL 走的是**同一个来源**：`build.py` 写入片段里的 `CONFIG_BOARD_TYPE_*`。干净环境裸 `reconfigure` 时两者**一起**掉成上游默认（上表第一行）；build 目录尚在时两者**一起**被片段保住。

诊断口诀：**两者总是一起掉，因为来自同一个片段**。「只有一个掉」的情形不存在；若只观察到其中一个，说明观察不完整（比如只看 `sdkconfig` 没看构建产物）。

新增守护：`firmware/scripts/tests/test_device_addressing.py`——断入库产物、Kconfig 兜底、板卡预设三者都不得含官方云主机名；三处来源两两同值；入库 `sdkconfig` 的板卡身份正确。这组断言取代了此前只能靠「记得用 `build.py`」的纪律。

## Context（两次事故）

- **2.4.5 事故**：固件源码 vendoring 后用主仓重编，而 2.4.4 的 `CONFIG_OTA_URL` 是当年编译时临时改动、从未存档——重编产物带着官方默认地址，设备开机即被官方云接管（童声/官方角色），且从此不再回来找我们的 OTA，只能 USB 救援。
- **升级陷阱事故**：`data/bin/` 中出现版本号更高的坏固件（2.4.7，主仓源码在 OTA 地址未修正前编译的产物）。设备每次开机自检即被"升级"，官方地址覆盖 NVS 中的 WebSocket 配置，形成无限循环——无论怎么刷回 2.4.6，下次开机又被拉走。终结手段：移除陷阱文件 + 手工构造 ota_data 锁定 ota_0。

## 2.4.8：固件构建统一在主仓

旧工程（固件雏形）的本地组件修改不可丢：`managed_components/78__esp-wifi-connect`（配网增强）与 `txp666__otto-emoji-gif-component`（定制表情）均不入库，registry 拉取的纯净版会缺失。决定：**固件构建统一在主仓**，每次构建前从旧工程目录复制这两个组件（或任何后续本地修改过的组件），2.4.8 即按此方式集成音乐播放修复 + 省电补丁 + 正确寻址，USB 直刷 ota_0 生效。

## 地址改用 mDNS 名（2026-09-10，2026-09-11 补第四处）

宿主机 Mac 的 DHCP 租约会漂移（`192.168.18.172` → `192.168.18.166`），写死 IP 的寻址三件套在每次 IP 变更后全链路失联。决定：OTA 地址与 WS 地址一律写成 mDNS 名 `chenMac-mini.local`（`scutil --get LocalHostName` 的值；mDNS 比较大小写不敏感，配置里写成 `chenmac-mini.local` 同样有效），不再随 IP 走。

**2026-09-11 补漏：音乐代理是第四处地址，09-10 那次迁移漏了它。** 音乐/播客/电台的 `play_url` 由 `plugins/music-mcp/music_mcp.py` 的 `PROXY_BASE` 拼出，默认值写死了 `192.168.18.172`；10 日改完寻址三件套后设备照连服务端不误，唯独音乐在 9 月 3 日之后彻底无声——设备拿到已不属于本机的地址（ARP `incomplete`），而固件 `play_music` 是**假成功**（先返回 true 再去连流），日志仍显示成功。用户看到的是「搜完音乐、说开始播放，机器人就不动了」。

同一纪律落到音乐链路上：

- `PROXY_HOST = "chenMac-mini.local:8080"` 是**全文件唯一写主机名的地方**，`PROXY_BASE`、授权页提示等全部由它派生；改地址只改这一行（需临时覆盖可用环境变量 `MUSIC_PROXY_BASE`）。地址值入库在仓库里，不靠「记得改现场」。
- 启动即把生效地址打到 stderr（launchd 收进 `/tmp/xiaozhi_server_launchd.log`）；地址是 IP 字面量时额外告警——这个故障本该一句日志就能看出来，不必再走串口取证。
- 回归判据：`server/.venv/bin/python tools/music_url_check.py`（跑「搜索 → play_url → 真取流」，地址退回 IP 或主机不可达即变红；被测启动命令直接读 `data/.mcp_server_settings.json`，不另存一份路径知识）。
- 脚本实体只留仓库一份：`~/xiaozhi-music-mcp/{music_mcp,qqmusic_auth_server}.py` 与 `~/.hermes/bin/qqmusic_mcp.py` 均为指向 `plugins/music-mcp/` 的符号链接（相对路径），改一次仓库文件三个入口同时生效。

**残留风险**：固件 `MusicPlayer` 等首帧的上限是 `kStartWaitTimeoutMs = 10000`，而 mDNS 解析失败时 lwIP 要走完 `DNS_MAX_RETRIES=4` 的退避（约 7 秒）。正常一次查询是毫秒级，但若某次查询丢包后又赶上解析退避，首播可能踩到 10 秒上限。音乐偶发「起不来」时，先看是不是这条。

- 固件侧能力已具备：`CONFIG_LWIP_DNS_SUPPORT_MDNS_QUERIES=y` + 板卡 `mdns_init()`，WebSocket 走的 `EspTcp::gethostbyname` 为 IPv4，`.local` 可解析。
- 两个来源必须同时改：`firmware/main/boards/zhengchen/minicam/config.json` 的 `sdkconfig_append`（`scripts/build.py` 构建时生成 sdkconfig 的真相源）与入库的 `firmware/sdkconfig`（`idf.py build` 直接使用的那个）。
- 设备端另有 NVS 的 `wifi/ota_url`，优先级高于 sdkconfig；改完必须经配网页重设，否则旧地址继续生效。
- ⚠️ **OTA 地址没有备用**：固件只认一个 OTA 地址（NVS 或 sdkconfig），主备切换只覆盖 WS。mDNS 一旦被打断（AP 隔离 / 组播被挡 / 跨子网），设备连 OTA 都摸不到，新的 WS 配置也就送不进去——这时只有两条路：进配网页改 OTA 地址，或 USB 直刷。
- WS 备用地址写在 `server.websocket_backup`，用花生壳公网域名（值见不入库的 `.config.yaml`）。公网路径与局域网无关，IP 怎么漂都不受影响。⚠️ 必须写 `wss://`：固件 `web_socket.cc` 的 URI 解析只在 `protocol == "wss"` 时给 443 端口，`https://` 会被当成 80 端口做 TLS，必失败（花生壳的 80 也不通）。TLS 校验收 `esp_crt_bundle`，已验证 `.vicp.fun` 证书链（RapidSSL → DigiCert Global Root G2）在 ESP-IDF 内置 bundle 中，校验通过；证书有效期至 2026-11-21，到期前需更新。

## 2.4.19：主备切换策略修正（2026-09-10）

2.4.18 及之前的策略是「主地址失败 → 切备用 → **把备用写进 NVS 当主地址**」，局域网一抽风就粘在公网上，直到下次成功 OTA 才刷回。2.4.19 改成内网永远首选：

- 偏好只存 RAM，主地址一连上就清掉，不写 NVS；每次开机都从「先试内网」开始。
- 备用连上后启动 `ws_probe` 后台任务，每 30 秒 TCP 探一次主地址，探通即切回首选。
- 探测走独立任务而非同步重试：主地址不可达时 lwIP 解析 `.local` 要走完 `DNS_MAX_RETRIES=4` 的退避（`DNS_TMR_INTERVAL=1s`，1+1+2+3），约 7 秒才报失败；而 `OpenAudioChannel()` 每轮对话都会调用，同步重试会把这段等待压到每次开口之前。
- 构建与发布：`firmware/CMakeLists.txt` 的 `PROJECT_VER` 改为 `2.4.19`，`idf.py build` 产物 `firmware/build/xiaozhi.bin`（3 487 024 字节，sha256 `7c6098377977db27a8c8350a1ec30aa4c7c5640bcff975359c20b09ba301f873`）拷贝为 `server/data/bin/zhengchen-minicam_2.4.19.bin`。
- 上线方式：开机 OTA 自动升级（设备当前 2.4.18，版本号单调递增）；`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`，未标记有效的镜像失败会回滚。
- 下载链路已验：局域网（`http://chenMac-mini.local:8080/xiaozhi/xiaozhi/ota/download/zhengchen-minicam_2.4.19.bin`）与公网（花生壳域名，25s 传完 3.5MB）均 200 且 sha256 一致。

## Consequences

- 诊断口诀：**设备"变官方"，先查 `data/bin/` 有没有更高的版本文件**，再查固件的 OTA 地址。
- 主仓 `sdkconfig` 的 OTA 地址是安全底线，改 venv/路径/重装环境后需确认 macOS 防火墙仍放行服务端 python（socketfilterfw 按可执行路径放行）。
- 备用地址是兜底而非日常通道：它依赖花生壳映射与公网可达，平时不参与连接（内网优先），失效只损失兜底能力。证书 2026-11-21 到期前需复核。
- 宿主机拓扑（不可见于代码）：服务端由 launchd `com.xiaozhi.server` 托管主仓 venv；nginx（8080，`/xiaozhi/` 反代 8002/8003）为备用入口与花生壳公网映射目标；MLX TTS 服务由 launchd `com.yuanbao.mlxtts` 托管（端口 9753）。
