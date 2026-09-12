# 服务端页面与路由命名台账

> 本文件是 issue #15 的 resolution 载体，服务于 map #13（「服务端页面分类地图」）。
> 边界：**只盘点与列清单，不改路由**。路由改名在 #13 的 Out of scope 内；命名规则本身在方案文档里定。
> 所有结论标注来源（文件路径 + 行号）；「已验证」= 实测（curl 现场服务器或读仓库外 nginx 配置）。

---

## 1. 结论摘要

1. **`/xiaozhi/*` 是单一扁平命名空间，被三类使用者共占**：浏览器页面（2 个）、设备固件（OTA / 固件下载 / WebSocket）、MCP 工具调用方（视觉分析）。三者前缀上完全无法区分——页面路径与设备协议路径平级相邻。
2. **页面层的两级命名互不一致**：配置页页面路径 `/xiaozhi/config/`，管理接口 `/xiaozhi/config/api/*`；摄像头页页面路径 `/xiaozhi/camera/`，管理接口 `/xiaozhi/camera/api/*`。**两个页面各自把 `api` 挂在「页面身份」之下，而不是挂在「能力/资源」之下**，同类接口没有统一家；再加第三个页面就得再开第三套。
3. **`config` 前缀名不诚实**：`/xiaozhi/config/api/` 名下同时住着配置读写（`full`/`save`/`restart`）、运行态查询（`devices` = 当前 WebSocket 在线设备）、磁盘状态查询（`firmware` = `data/bin/*.bin`）、物理副作用动作（`smartconfig`、`test-llm`、`firmware/upload`）。前缀描述的是**页面归属**，不是资源类型，故**无法从 URL 判断一次调用是否会产生持久副作用**。
4. **同一领域对象（固件）有两个归属相反的入口**：设备调 `/xiaozhi/ota/download/{filename}`，页面调 `/xiaozhi/config/api/firmware*`，二者操作**同一个 `data/bin/` 目录**；职责还被切开（页面不能下载、设备不能列出）。更尖锐的是**页面跨前缀去调设备侧 OTA 路径**（重启设备，`config_page.html:327`）。
5. **trailing slash 不是统一契约**：`/xiaozhi/camera` 与 `/xiaozhi/camera/` 是**两条独立注册、body 逐字节相同**的路由；而 `/xiaozhi/config` 在 8003 是 404，其 301 完全由 nginx 补出。**同一命名空间下 slash 语义取决于从 8080 还是 8003 进入。**
6. **`http_server.py:146-161` 的「重复注册」经实测不成立**（无 (method, path) 重复对），但它暴露了另一个真实不一致：全仓**只有 4 个接口具备 `OPTIONS` 能力**，其余含摄像头页全部 405；而这 4 个里包含**页面从不调用的 `api/auth`**，却漏掉页面调用最频繁的 `api/meta`。这是「手工罗列而非按类别生成」的痕迹（详见 §4.5）。
7. **关键约束：nginx 配置在仓库外**（`/opt/homebrew/etc/nginx/servers/apps-proxy.conf`，已验证）。故**单靠仓库内容无法完整推导浏览器可达 URL 契约**——仓库内同时存在带前缀（`firmware/sdkconfig:969`、`app.py:82`）与不带前缀（`util.py:536`、`app.py:87`）两种字面量，且 `server/config.yaml` / `server/data/.config.yaml` 均被 gitignore，端口与 `read_config_from_api` 只能从运行环境读取。命名规则文档必须把反代层列为外部依赖。

---

## 2. 页面台账（浏览器可见页面）

全仓共 **2 个**浏览器可见页面。两者都是 HTML 内嵌在 Python handler 里，无模板引擎；**全仓无 `add_static`、无静态目录注册**（已验证）。

| # | 路由 | 用途 | 实现 | 可达性 |
|---|---|---|---|---|
| P1 | `GET /xiaozhi/config/` | 配置页：8 个分组表单 + 设备/固件管理 + SmartConfig 配网 | `server/core/api/config_handler.py:212`（`handle_page`），HTML 在 `server/config/config_page.html`（813 行；`GROUPS` 定义于 `:170-178`） | 8080 200 / 8003 200 |
| P2 | `GET /xiaozhi/camera/` | 摄像头监控页：轮询设备快照 | `server/core/api/camera_handler.py:138`（`handle_page`），HTML 在 `camera_handler.py:20-131` 的 `PAGE` 字符串 | 8080 200 / 8003 200 |

### 2.1 注册与可达性核对（8003/8080 实测）

| 探测路径 | 8003 直连 | 8080 经 nginx | 说明 |
|---|---|---|---|
| `/xiaozhi/config/` | 200 | 200 | 唯一正式注册的配置页路径（`http_server.py:95`） |
| `/xiaozhi/config` | **404** | **301** | 8003 无此注册；8080 的 301 由 nginx 的 `location /xiaozhi/config/`（仓库外 `:323`）目录式跳转补出 |
| `/xiaozhi/camera/` | 200 | 200 | `http_server.py:163` |
| `/xiaozhi/camera` | **200** | **200** | `http_server.py:164-167`——**与上一条是两条独立注册，同一 handler 挂两次，返回 body 完全相同**（4225 bytes，`diff` 无差异） |

**结论**：`/xiaozhi/camera` 与 `/xiaozhi/camera/` 是**重复注册的双胞胎**（应用层冗余，非 nginx 重写）；`/xiaozhi/config` **没有**无斜杠孪生路由。且 `/xiaozhi/camera` 因 nginx 的 `location /xiaozhi/` 兜底而**不会**被 301，`/xiaozhi/config` 却会——**两个页面的 slash 行为不同，原因在仓库外**。

### 2.2 两条进入路径与鉴权（已验证）

- **路径 A（公网 / 主入口）：nginx 8080 → aiohttp 8003**。仓库外 `:323` `location /xiaozhi/config/` 与 `:332` `location /xiaozhi/` 均 `proxy_pass http://127.0.0.1:8003`；后者是兜底，摄像头页由它承接。公网 80 → 8080 经花生壳（`:300`）。
- **路径 B（局域网直连）：`http://<host>:8003/xiaozhi/config/`**，绕过反代。故 **`/xiaozhi/` 既是 aiohttp 的真实路由前缀，又是 nginx 的反代路径**——二者当前字符串相同，**从 URL 无法判断走的是哪条链路**。
- **鉴权在仓库外**：nginx `auth_basic $auth_gate`（仓库外 `:87`），`$auth_gate` 的 map 在 `:55-63`，回环/局域网/链路本地免认证，其余来源要求 Basic Auth。配置页自身的 PIN 校验已废弃为 stub（`config_handler.py:223-225`，直接放行）。**权限边界无法从仓库内的路由定义读出。**
- **WebSocket 走另一个上游端口**：仓库外 `:309-321` `location /xiaozhi/v1/` → `127.0.0.1:8002`，而页面走 8003。**同一个 `/xiaozhi/` 前缀在 nginx 层被按子路径分给两个上游端口**——命名空间被多类使用者共享，在部署层直接可见。
- 进程由 launchd `com.xiaozhi.server` 托管（cwd `server/`，跑 `app.py`）。

### 2.3 页面间导航现状

**两个页面之间没有任何互相链接**（已验证：`config_page.html` 全文无 `camera` 字样，`grep -c camera` 为 0；`camera_handler.py` 只含自身 api 路径）。导航完全依赖手输 URL。配置页内部是单页 8 分组 tab（`:170-178`、`:255` 的 `switchGroup`），**组间切换不改变 URL**——**页面内分组状态不可寻址，无法深链到某个分组**。

---

## 3. 非浏览器路由台账

`http_server.py` 共 **32 条**路由注册（脚本枚举；`:54-176`）。另有 1 条不在此文件的 WebSocket 入口。按调用方分类：

### 3.1 设备固件（ESP32-S3 本体）

| 路由 | 方法 | 用途 | 实现 |
|---|---|---|---|
| `/xiaozhi/ota/` | GET/POST/OPTIONS | OTA 自检：上报版本/型号 → 下发 WebSocket 地址 + 新固件 URL | `ota_handler.py:373` / `:145`；注册 `http_server.py:54-58` |
| `/xiaozhi/ota/download/{filename}` | GET/OPTIONS | 固件下载（仅 `data/bin/*.bin`） | `ota_handler.py:450`；注册 `:60-67` |
| `/xiaozhi/ota/reboot` | GET/POST/OPTIONS | 远程重启设备（重启后自动 OTA） | `ota_handler.py:390`；注册 `:69-71` |
| `ws://…/xiaozhi/v1/` | WebSocket | 语音协议主通道 | **非 aiohttp**：`websocket_server.py:80`，端口 8002 |

**固件侧硬编码（改名约束，已验证）**：
- `firmware/sdkconfig:969`：`CONFIG_OTA_URL="http://chenMac-mini.local:8080/xiaozhi/ota/"` —— 设备启动即请求；`firmware/main/ota.cc:48-52` 先读 `settings` 的 `ota_url`，为空才回落 `CONFIG_OTA_URL`。
- `firmware/main/ota.cc:220-225`：**固件下载 URL 不在固件内**，来自 OTA 响应 JSON 的 `firmware.url` → 服务端**有**改下载路径的自由度。
- WebSocket 地址同样由服务端下发（`http_server.py:38` 回落 `ws://{local_ip}:{port}/xiaozhi/v1/`；`websocket_protocol.cc:66-67` 只做 `Connect(url)`）。
- **耦合分两档**：`/xiaozhi/ota/` 与 `/xiaozhi/v1/` 是**设备侧硬编码**，改名必须同步刷固件；`/xiaozhi/ota/download/*` 是**服务端下发**，本可改，但**已刷出的历史固件仍会用旧 URL**，故实际仍受约束（与 ADR-0002「三处必须一致」的约束同源）。

### 3.2 MCP 工具 / 视觉分析

| 路由 | 方法 | 用途 | 调用方 |
|---|---|---|---|
| `/xiaozhi/mcp/vision/explain` | GET/POST/OPTIONS | 视觉分析（JWT 鉴权） | MCP 工具侧。`server/core/utils/util.py:522-537` 的 `get_vision_url()` 生成 **`http://{ip}:{port}/mcp/vision/explain`——不带 `/xiaozhi` 前缀**（`util.py:536`）；`app.py:87` 打印的也是无前缀版 |

### 3.3 页面自身的 API（浏览器 fetch 发起）

配置页 `fetch` 调用点（`config_page.html`，括号内为该路径在页面中的出现次数）：`api/meta`（3，`:242,:243,:793`）、`api/full`（2，`:243,:778`）、`api/save`（1，`:776`）、`api/restart`（1，`:789`）、`api/devices`（1，`:291`）、`api/firmware`（3，`:292` 及列表渲染）、`api/firmware/upload`（1，`:343`）、`api/firmware/delete`（1，`:358`）、`api/smartconfig`（1，`:696`）、`api/test-llm`（1，`:376`）、**跨前缀** `/xiaozhi/ota/reboot`（1，`:327`）。
摄像头页：`api/device-info`（`camera_handler.py:89`）、`api/snapshot`（页面轮询）。

> **两个注册了但页面从不调用的接口（已验证，`grep -c` = 0）**：`api/auth`（`http_server.py:96`；`config_handler.py:223-225` 自述为「兼容接口…直接放行」）与 `api/local-wifi`（`http_server.py:121`）。

### 3.4 人（curl）

`http_server.py:68` 的注释即文档：`curl http://<ip>:8003/xiaozhi/ota/reboot?device_id=xx`。`/xiaozhi/ota/reboot` 因此在「设备」与「人」之间共用（`device_registry.py:4` 亦以此为说明）。

### 3.5 混合程度判定

- 页面（P1/P2）与设备协议（OTA、download、WS）**平级相邻**，仅靠 `ota` 一级词分隔——而 `ota` 描述的是**协议阶段**，不是**使用者**。
- `mcp` 词根暗示「MCP 工具」，实际是 HTTP 视觉接口，且其真实生成地址**不带 `/xiaozhi`**（§3.2），即词根与前缀都不指向实际使用方式。
- 没有任何机制（前缀、子域、端口、中间件）在**应用层**区分调用方类别。唯一区分信号是 nginx 的端口分流（`/xiaozhi/v1/` → 8002，其余 → 8003），而它**定义在仓库外**。

---

## 4. 命名冲突清单

### 4.1 固件：设备入口与页面入口，同物两名、归属相反

- **路由**：设备侧 `GET /xiaozhi/ota/download/{filename}`（`http_server.py:60`，`ota_handler.py:450`）↔ 页面侧 `GET /xiaozhi/config/api/firmware`、`POST …/firmware/upload`、`POST …/firmware/delete`（`http_server.py:134,138,142`）。
- **谁调**：前者**设备固件**（URL 由 OTA 响应下发）；后者**配置页**（`config_page.html:292,343,358`）。
- **实际做什么**：两者操作**同一个 `data/bin/` 目录**（`config_handler.py:71` 定义 `bin_dir`；`:88-89` 扫目录；`ota_handler.py` 的候选列表同样扫该目录）。
- **命名张力**：同一领域对象（固件）的两入口挂在**两个语义相反**的前缀下——「`ota` = 设备/协议」对「`config` = 页面/配置」。职责且被切开：**页面能上传/删除却不能下载，设备能下载却不能列出**。前缀既没表达「谁在做」，也没表达「这是同一资源」。

### 4.2 页面跨边界调用设备协议路径（固件域内最尖锐）

- **路由**：`POST /xiaozhi/ota/reboot?device_id=xx`（`http_server.py:69-70`），**由配置页「设备与固件」分组发起**（`config_page.html:324-332` 的 `rebootDevice()`，按钮在 `:303`）。
- **谁调**：设计语境是无头运维（§3.4），但页面把它**接成了自身功能**。
- **实际做什么**：`ota_handler.py:390+` 从 `device_registry.get_online()` 找设备并下发重启。
- **命名张力**：UI 的「设备与固件」分组，其功能面**横跨 `/xiaozhi/config/api/*` 与 `/xiaozhi/ota/*` 两个前缀**。分组是 UI 概念、前缀是协议概念，二者无法一一对应——故「按页面分组归类」时 OTA 组天然不落在 `config` 前缀下。（这也是 nginx 需要单独一条 `location /xiaozhi/config/` 的原因之一。）

### 4.3 摄像头页的 API 挂在页面路径下

- **路由**：`GET /xiaozhi/camera/api/snapshot`（`http_server.py:168`）、`GET /xiaozhi/camera/api/device-info`（`:172`）。
- **谁调**：摄像头页自身（`camera_handler.py:89` 与快照轮询）。
- **实际做什么**：`handle_snapshot`（`:164`）代理设备 JPEG；`handle_device_info`（`:141`）解析设备 mDNS 域名。
- **命名张力**：语义类别与 `/xiaozhi/config/api/*` **完全相同**（都是「页面用的 HTTP 管理/数据接口」），却因所属页面不同而挂在另一个父节点下。**`/api/` 的父级表达的是「哪个页面」而非「什么资源」**——这是 §1-2 的根因。

### 4.4 `config` 前缀名不副实：四种时态混编

`/xiaozhi/config/api/` 名下并排住着**时态与副作用都不同**的东西：

| 路由 | 实际做什么 | 时态/副作用 | 来源 |
|---|---|---|---|
| `api/full` | 读合并后的生效配置（敏感字段掩码） | 静态读 | `config_handler.py:227` |
| `api/meta` | 返回配置文件路径 | 静态读 | `:236` |
| `api/save` | 写 `data/.config.yaml`（diff 提交） | 持久写 | `:244` |
| `api/restart` | 重启服务 | 副作用 | `:411` |
| `api/test-llm` | 试连 LLM | 副作用探测 | `:354` |
| `api/devices` | **当前 WebSocket 在线设备**（`device_registry.get_online()`） | **运行态、瞬时** | `:75-83` |
| `api/firmware` | **扫 `data/bin/*.bin` 磁盘文件** | **磁盘状态、非配置** | `:85-97` |
| `api/firmware/upload` / `delete` | 写/删 `data/bin/` | **文件系统写** | `:100` / `:131` |
| `api/smartconfig` | 向空口广播配网包 | **物理副作用** | `:463` |
| `api/local-wifi` | 本机 Wi-Fi 信息 | 运行态（且页面未调用） | `:433` |

- **命名张力**：`config` 描述**页面归属**，而路由下住着运行态（在线设备）与磁盘状态（固件库）——两者**不受 `save` 控制、也不写进 `config.yaml`**，改配置不会改变它们。「配置读写」与「运行态/磁盘观测」是两类根本不同的操作却共享前缀，**`api/full`（只读）与 `api/restart`（有副作用）平级相邻**，前缀不提供任何副作用预警。

### 4.5 `http_server.py:146-161` 的「重复注册」——实测结论

- **事实**：`:146-161` 是 4 条 `web.options`（`api/auth`、`api/full`、`api/save`、`api/restart`），与上方 `:96,:100,:108,:116` 的 `web.post` **是不同 HTTP 方法**，非同一方法重复。**全文件 32 条注册中无任何 (method, path) 重复对**（脚本枚举）。
- **它们服务谁（**修正**）**：仓库内**不存在任何 OPTIONS preflight 代码**（全仓无 `Access-Control-Request-Method` / `Vary: Origin`）。这 4 条是为**跨源页面**准备的（CORS 头硬编码为 `Access-Control-Allow-Origin: *`、`Allow-Credentials: true`，见 `base_handler.py:12-16`）；注意 `application/json` **不在** CORS safelisted content-type 内，故页面若跨源访问则 `api/save` 等确实会触发 preflight——即这批 OPTIONS **可能触发但无法从仓库获知触发者**（预检由浏览器发起，不落在任何源码行）。
- **真实的不一致（已验证）**：只有这 4 个接口有 `OPTIONS`（200），其余 `api/meta`、`api/test-llm`、`api/devices`、`api/firmware`、`api/local-wifi`、`api/smartconfig`、`camera/api/snapshot` **全部 405**。而这 4 个里含**页面从不调用的 `api/auth`**（`grep -c` = 0），却**漏掉页面调用最多的 `api/meta`**（3 处）。
- **归类为命名问题**：这是一组**没有按类别成组的路由**——4 条 OPTIONS 插在 config 组尾部，既不属于 OTA 组也不属于摄像头组，靠手工罗列而非按类别生成。它说明「路由按前缀分组」的现状**漏掉了「同前缀、跨方法」这一维度**，`OPTIONS` 覆盖是否齐备与 REST 语义无关、只与当年手写的 4 行有关。
- **附带一处运行时路径改写**：`ota_handler.py:322-327` 生成下载 URL 的方式是 `get_vision_url(config).replace("/mcp/vision/explain", f"/xiaozhi/ota/download/{fname}")`——**运行时把一条路径字符串改写成另一条**；而 `get_vision_url` 默认返回**不带 `/xiaozhi` 前缀**的 `/mcp/vision/explain`（`util.py:536`）。故下载 URL 的实际前缀由 `server.vision_explain` 配置决定，日志自带告警：「如果地址前缀有误，请检查配置文件中的 server.vision_explain」（`ota_handler.py:334`）。**同一 handler 内同时存在带前缀与不带前缀两种路径字面量。**

### 4.6 前缀本身的漏出（`/xiaozhi` 出现两次）

nginx 里存在 `location ^~ /xiaozhi/xiaozhi/ { rewrite ^/xiaozhi/xiaozhi/(.*)$ /xiaozhi/$1 last; }`（仓库外 `:305-307`），且其**注释直接点名根因**：「修正 ota_handler 生成的重复前缀 `/xiaozhi/xiaozhi/ota/download/...`」（`:304`）。ADR-0002 的下载链路实测记录也恰是双前缀形态：`…:8080/xiaozhi/xiaozhi/ota/download/zhengchen-minicam_2.4.19.bin`（`docs/adr/0002-device-addressing-and-firmware-distribution.md:46`）。即**前缀被真实叠过一次**，反代层只能靠 rewrite 兜底——这是「前缀既是应用内部路由、又是反代路径映射」双重身份留下的疤痕（与 §3.2 的 `get_vision_url` 无前缀默认值互相对应）。

---

## 5. 参考来源

**仓库内（代码）**
- `server/core/http_server.py`（193 行；`:38` WS 回落地址；`:50-71` OTA 组；`:74-87` vision 组；`:93-176` config/camera 组；`:146-161` 4 条 OPTIONS）
- `server/core/api/config_handler.py`（500 行；`:65` 类；`:69` `custom_path`；`:71` `bin_dir`；`:75` devices；`:85` firmware list；`:100` upload；`:131` delete；`:212` page；`:223-225` auth stub；`:227` full；`:236` meta；`:244` save；`:354` test-llm；`:411` restart；`:433` local-wifi；`:463` smartconfig）
- `server/core/api/camera_handler.py`（193 行；`:1-11` 路由自述；`:18-19` 设备地址常量；`:20-131` 内嵌 `PAGE`；`:134` 类；`:138` page；`:141` device-info；`:164` snapshot；`:89` 页面内 fetch）
- `server/core/api/ota_handler.py`（493 行；`:145` post；`:322-327` 下载 URL 改写；`:334` 前缀告警；`:373` get；`:390` reboot；`:450` download）
- `server/core/api/vision_handler.py:20`（类）
- `server/core/api/base_handler.py`（24 行；`:12-16` 硬编码 CORS；`:18` `handle_options`）
- `server/config/config_page.html`（813 行；`:170-178` 8 分组；`:228-236` `api()`；`:255` `switchGroup`；`:287-292` devices/firmware；`:303,:324-332` reboot；`:336-360` 固件上传/删除；`:686-696` smartconfig；`:774-793` save/restart）
- `server/core/utils/util.py:522-537`（`get_vision_url`，`:536` 返回无 `/xiaozhi` 前缀地址）
- `server/core/utils/device_registry.py`（`:4` 注释；`:30` `get_online`）
- `server/app.py:79,82,87,110`（启动横幅打印的 HTTP / OTA / vision / WS 地址）
- `server/core/websocket_server.py:80,169`（WS 服务；`Server is running` 健康响应）
- `server/config/config_loader.py:42-45`（`manager-api` 判据；`:65` 置 `read_config_from_api = True`）
- `firmware/sdkconfig:969`（`CONFIG_OTA_URL`）
- `firmware/main/ota.cc:48-52`（url 回落）、`:220-225`（下载 URL 来自响应）
- `firmware/main/protocols/websocket_protocol.cc:66-67`（`Connect(url)`，地址来自服务端）
- `docs/adr/0002-device-addressing-and-firmware-distribution.md:3,46,53`（寻址三件套；`/xiaozhi/xiaozhi/` 双前缀实测；宿主机拓扑）
- `.gitignore:13,19`（忽略 `server/data/`、`server/config.yaml`）

**仓库外（必须外部核对，无法从仓库推导）**
- `/opt/homebrew/etc/nginx/servers/apps-proxy.conf`：`:55-63` `$auth_gate` map；`:87-88` `auth_basic`；`:300` 花生壳 80 → 8080；`:303` `location /xiaozhi/`；`:304-307` 双前缀 rewrite（注释点名 `ota_handler`）；`:309-321` `/xiaozhi/v1/` → `8002`（WS）；`:323-331` `/xiaozhi/config/` → `8003`；`:332-344` `/xiaozhi/` → `8003`（兜底）。
- launchd `com.xiaozhi.server`（cwd `server/`，跑 `app.py`）。

**已实测（curl，现场服务器）**
- 8003：`/xiaozhi/config/` 200、`/xiaozhi/config` **404**、`/xiaozhi/camera/` 200、`/xiaozhi/camera` **200（body 与上者逐字节相同，4225 bytes）**、`/xiaozhi/ota/` 200、`/xiaozhi/ota/reboot`（无 `device_id`）404、`/xiaozhi/mcp/vision/explain` 200、`/xiaozhi/v1/` 404（WS 不在此端口）。
- 8080：`/xiaozhi/config` **301**，其余同上均 200；`/xiaozhi/v1/` 200（返回 `Server is running`，故该路径不可能被页面占用）。
- `OPTIONS /xiaozhi/config/api/{auth,full,save,restart}` → 200（响应头含 `Access-Control-Allow-Origin: *`、`Allow-Credentials: true`、`Allow-Methods: GET, POST, OPTIONS`）；`{meta,test-llm,devices,firmware,local-wifi,smartconfig}` 与 `camera/api/snapshot` → **405**。
- 8002 与 8003 由同一 `python3.1` 进程（PID 73891）监听。
- 页面引用计数（`grep -c`，用于判定死路由）：`api/auth` 0、`api/local-wifi` 0；`api/meta` 3、`api/firmware` 3、`api/full` 2；其余各 1。
- 全仓无 `add_static`、无静态目录注册；无 OPTIONS preflight 代码。`server/data/.config.yaml` 无 `manager-api` 键 → 走 `config_loader.py:44-45` 合并分支，`read_config_from_api` 为 **false**，故 `http_server.py:50-71` 的 OTA 路由**确已注册**。

**上游未接线**：智控台 manager-web 不在本副本（`server/docker-compose_all.yml:30-32` 仅以 `image: …:web_latest` 引用外部镜像，非本地构建）。
