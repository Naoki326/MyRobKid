# MyRobKid 采用主仓 monorepo，双上游硬切 vendoring

MyRobKid 需要一个与三个 xiaozhi 旧工程（xiaozhi-server、固件雏形 xiaozhi-tts、xiaozhi-music-mcp）区分开的新结构。决定：Robot 主仓即 monorepo，固件与服务端各占一个顶层目录（`firmware/`、`server/`）；对固件上游（78/xiaozhi-esp32）与服务端上游（xinnan-tech/xiaozhi-esp32-server）均采取**硬切 vendoring**——代码拷入即切断，不保留可合并的上游历史，换取完全自由的文件树设计。

## Considered Options

- **跟随上游**（git subtree / 定期 merge + 补丁收敛）：能拿到上游更新，但文件树必须迁就上游布局，与「重新设计文件树」的目标冲突。
- **混合**（固件硬切、服务端补丁式跟随）：被否决——服务端改造虽小（+322/−103），但双策略意味着两套维护心智，个人项目不值得。

## Consequences

- 上游的 bug 修复与新特性**不会自动到来**，需要时以人工比对方式手动移植。
- 旧工程原地保留作为存档与对照参考，不再演进。
- 主仓是 public 仓库：密钥与运行时状态（`data/`、`models/`、`.venv`、`build/`、`managed_components/`）一律 gitignore，绝不入库。
