# MyRobKid

为 ESP32-S3 机器人打造的小智系自研固件与配套自建服务端的项目。机器人出厂自带 bitcraft 固件，本项目以「自研固件 + 自研服务端」整体替换出厂与第三方方案。

## Language

### 项目

**MyRobKid**:
本仓库承载的项目总称，含两个子工程：机器人固件与自建服务端。
_Avoid_: Robot（仅是本地目录名）、机器人项目

**主仓**:
Robot 目录下的 monorepo，固件（firmware/）与服务端（server/）的共同居所；决策见 ADR-0001。
_Avoid_: 新文件夹

**固件上游**:
开源项目 xiaozhi-esp32（78），MyRobKid 固件在其源码基础上定制。
_Avoid_: 官方、原版、上游（单用）

**服务端上游**:
开源项目 xiaozhi-esp32-server（xinnan-tech），MyRobKid 服务端在其源码基础上改造。
_Avoid_: 官方服务端、上游（单用）

### 硬件

**机器人**:
MyRobKid 的硬件本体：ESP32-S3-R8-N16 + ES8388 音频 codec + 屏幕 + 摄像头。
_Avoid_: 设备（泛称）、板子

**zhengchen-minicam**:
机器人在上游源码中的板卡变体名，位于 `main/boards/zhengchen/minicam/`。

### 固件

**出厂固件**:
机器人出厂预装的固件，即 bitcraft v2.2.6.7。

**第三方固件**:
资料包中来源不明的小智固件（v2.4.2），其 OTA 与通信指向他人服务器。
_Avoid_: 自制固件（资料包文件夹名，归属未证实，具误导性）

**MyRobKid 固件**:
本项目为机器人构建的小智系固件，起点为固件雏形工程。
_Avoid_: 自定义固件、自制固件

**固件雏形**:
原 xiaozhi-tts 工程，MyRobKid 固件的前身与起点。
_Avoid_: xiaozhi-tts（新结构中不再沿用此名）

**官方云**:
出厂/上游固件默认指向的 xiaozhi 公共服务（tenclass）。持有设备在出厂时代激活形成的角色与音色；升级陷阱与寻址配置错误都会把机器人拉回它那。
_Avoid_: 原厂服务器、腾讯云

**OTA 槽位**:
机器人 flash 上的双固件分区（ota_0/ota_1）及启动指针（ota_data）。新镜像写入另一槽，启动失败可回滚；指针可被手工锁定以排除某槽。
_Avoid_: 固件分区（单数）、双系统

**音乐模式**:
设备资源分时复用的播放态：音乐播放期间停唤醒词检测、不进入聆听、摄像头按需采集，CPU/带宽让给网络与 I2S；按钮或新对话可打断并恢复全功能。见 ADR-0008。
_Avoid_: 后台播放（听感像全局的）、静音模式

**假成功**:
设备端工具不验证执行结果即返回 true 的假象（上游 PlayMusic 只等 worker 启动），日志看似成功但无声；判断播放是否真实需串口或管线遥测佐证。
_Avoid_: 成功（未验证的）、返回 true

**升级陷阱**:
固件分发目录中版本号高于在跑版本的坏固件文件；设备每次开机自检即被“升级”，连接配置被官方云覆盖，形成无限循环。症状：怎么刷都变官方音色。
_Avoid_: 灵异、被劫持、回滚 bug

**寻址三件套**:
机器人找到 MyRobKid 服务端依赖的三处配置：固件内 OTA 地址（sdkconfig）、OTA 响应下发的 WebSocket 地址（服务端 config）、固件分发目录（data/bin）。三处必须一致且入库，临时改而不存档会复现 2.4.5 事故。另有**第四处地址**——音乐代理主机（`plugins/music-mcp/music_mcp.py` 的 `PROXY_HOST`，派生所有 play_url 与授权页提示），同属这条纪律：写 mDNS 名不写 IP，且全文件只此一处，见 ADR-0002。
_Avoid_: 服务器地址（单指一处，不概括）

### 服务端

**MyRobKid 服务端**:
基于服务端上游改造的私有部署后端，向机器人提供 OTA 与对话服务。
_Avoid_: 云端、服务器（单用）、自研服务端（并非从零编写）

**MCP 插件**:
接入 MyRobKid 服务端工具生态的扩展服务，如 QQ 音乐插件。
_Avoid_: 技能、工具（单用）

**旧工程**:
MyRobKid 的三个历史工程：xiaozhi-server（服务端改造发生地）、固件雏形、xiaozhi-music-mcp。成果将迁入新结构，旧名不再沿用。
_Avoid_: 把旧工程名当作正式称呼

**MLX TTS 服务**:
宿主 Mac 上独立运行的语音合成服务（launchd 托管，克隆音色），被 MyRobKid 服务端经 HTTP 复用；单线程、共享设施，不得为其加并发改造。
_Avoid_: 元宝服务（它属于更大的 hermes 生态）、TTS 插件

**整段合成**:
一次把整句回复交给 TTS 合成再下发的方式（`MlxTTS`，split_sentences: false），听感连贯但首包延迟随回复长度增长；与逐句合成相对，两者经 selected_module.TTS 切换，见 ADR-0004/0005。
_Avoid_: 流式（泛称）、MlxStreamTTS（那是逐句合成的条目名）

**逐句合成**:
LLM 流式输出后按标点逐句切分、逐句合成并边合成边下发的方式（`MlxStreamTTS`，split_sentences: true），首包不随回复长度增长；仍用 MLX 娃娃音。见 ADR-0005。
_Avoid_: 官方流式（那是 chunk 级双向流式，本地 MLX 服务不支持）、真流式（同前）

### 运维与调试

**反馈回路**:
主仓 tools/latency_loop.py：模拟机器人走完整对话链路，分段测量延迟的诊断工具。
_Avoid_: 测试脚本（泛称）、压测

**管线遥测**:
固件 MusicPlayer 每 2 秒打印的 `pipe:` 行（ring 水位/in_buf/下载字节/推帧与失败计数），音乐卡顿定位的第一证据源；配套 USB 串口（115200）抓设备日志。
_Avoid_: 音乐日志（泛称）、debug 日志

**音乐地址自检**:
主仓 tools/music_url_check.py：跑「搜索 → play_url → 真取流」，确认设备拿到的地址能播。被测启动命令取自 `data/.mcp_server_settings.json`。判据不看日志——设备侧 play_music 是假成功，地址错了照样报成功；判据只看 URL 能不能取到音频。
_Avoid_: 音乐测试（泛称）

**省电锯齿**:
WiFi 省电模式下设备网络往返时间呈周期性锯齿（峰值可达数秒），会撕裂 60ms/帧的音频流形成一字一顿；对话期须全速。
_Avoid_: 网络抖动（成因与对策都不同）
