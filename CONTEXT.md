# MyRobKid

为 ESP32-S3 机器人打造的小智系自研固件与配套自建服务端的项目。机器人出厂自带 bitcraft 固件，本项目以「自研固件 + 自研服务端」整体替换出厂与第三方方案。

## Language

### 项目

**MyRobKid**:
本仓库承载的项目总称，含两个子工程：机器人固件与自建服务端。
_Avoid_: Robot（仅是本地目录名）、机器人项目

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
