# MyRobKid

为 ESP32-S3 机器人（板卡 `zhengchen-minicam`）打造的小智系固件与自建服务端的 monorepo。

## 结构

- `firmware/` — 机器人固件（硬切 vendoring 自 [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)）
- `server/` — 对话服务端（硬切 vendoring 自 [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)，含本地改造）
- `plugins/music-mcp/` — QQ 音乐 MCP 插件
- `tools/` — 配网等独立小工具

## 文档

- 术语表：[CONTEXT.md](CONTEXT.md)
- 架构决策：[docs/adr/](docs/adr/)（ADR-0001：monorepo + 双上游硬切）
- Agent 约定：[AGENTS.md](AGENTS.md)

## 快速开始

```bash
# 服务端（首次需自备 config.yaml 与 models/，见 server/config.yaml.example）
cd server && python app.py

# 固件（需 ESP-IDF v6.0.2）
cd firmware && idf.py build
```
