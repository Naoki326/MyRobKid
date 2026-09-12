# 服务端页面分类方案（Server UI IA）

> 产出票据：[#18 落文档并验收](https://github.com/Naoki326/MyRobKid/issues/18)，上级地图 [#13 服务端页面分类地图](https://github.com/Naoki326/MyRobKid/issues/13)。
> 本文是地图 #13 的**终点交付物**：五张决策票（#14 盘点 / #15 命名台账 / #16 落位裁决 / #17 引擎库 / #19 拓扑 / #20 危险分级）的收敛。
> 边界：**只出方案**。不改代码、不改 `server/data/.config.yaml` 的键序与结构、不重排页面——实现是另一件票，不在本图。

**范围**：配置页 `/xiaozhi/config/` + 摄像头页 `/xiaozhi/camera/` + 面向浏览器的页面 URL 命名与页面间导航。API 路由归属只在 §8.4 标出问题，不改。

---

## 0. 前提：这份方案按「操作者可信」设计

**这一节是硬前提，读者必须先接受它，后面的密钥呈现与危险操作设计才成立。**

### 0.1 部署与鉴权事实（2026-04 摸底 + #15 实测）

- **双入口**：nginx（配置在**仓库外** `/opt/homebrew/etc/nginx/servers/apps-proxy.conf`，listen 8080）把 `/xiaozhi/config/`、`/xiaozhi/` 反代到 8003（aiohttp 页面与 OTA），`/xiaozhi/v1/` 反代到 8002（WebSocket）；公网 80 → 8080 经花生壳。同时 **8003 可直连**，绕过 nginx。
- **鉴权在仓库外**：nginx `auth_basic $auth_gate` —— 回环与局域网**免认证**，公网来源要求 Basic Auth。配置页自身的 PIN 校验已是直接放行的 stub（`config_handler.py:223-225`）。**仓库内读不出权限边界**；8003 直连时页面完全无认证。
- `read_config_from_api` 实为 **false**（`server/data/.config.yaml` 无 `manager-api` 键），故 `http_server.py` 那批 OTA 与重启路由注册生效。上游智控台 manager-web **不在本副本、未接线**；全仓无 `add_static`、无静态目录注册。
- 运行端口与模板不一致：模板 `server/config.yaml` 写 `port: 8000`，实际覆盖为 `port: 8002`（WebSocket）、`http_port: 8003`（页面与 OTA）。

### 0.2 威胁模型

**局域网与回环访问免认证是刻意选择，不是遗漏。** 方案按「操作者可信」设计——不把「局域网里可能有人」当作威胁模型，密钥字段与危险操作的呈现**不因访问来源而收紧**。安全加固（如给 8003 直连加认证）另开票，不在本图。

### 0.3 价值取向

**「更清楚」优先于「更轻」。** 涉及信息密度 vs 显式清晰的取舍时，默认站在显式清晰一侧；不以「用户自己看得出来」为由省略显式编码（地图 #13 Notes 记录的人类表态，脏标记分组的改判即由此而来，见 §5.4）。

---

## 1. 现状与问题（为什么重划）

现状只有两处页面：`server/config/config_page.html`（813 行单文件，`/xiaozhi/config/`）与 `camera_handler.py` 内嵌 `PAGE`（`/xiaozhi/camera/`）。配置页现行八个导航项：服务器/连接、角色与对话、模型引擎、意图与插件、语音与音频、认证与设备、设备与固件、全部配置。

三个层面的问题（证据全部来自 #14 字段盘点与 #15 命名台账，两份交付物分别在 [`research/page-field-taxonomy`](https://github.com/Naoki326/MyRobKid/blob/research/page-field-taxonomy/docs/research/page-field-taxonomy.md) 与 [`research/route-naming-ledger`](https://github.com/Naoki326/MyRobKid/blob/research/route-naming-ledger/docs/research/route-naming-ledger.md) 分支）：

1. **81% 的字段没有归宿**。全部 527 个叶子字段里，现行页面只渲染得动 98 个；429 个没有渲染路径，只能在「全部配置」只读 JSON 里翻。其中 416 个属于未选中的引擎/意图，13 个是真孤儿（`log.*`、`mcp_endpoint`、`voiceprint.speakers`、`xiaozhi.type/version/transport`、`server.auth_key` 等）。
2. **分组轴是 YAML 相邻性，不是功能**。「服务器」组里装着 `tts_timeout` / `tool_call_timeout` / `enable_websocket_ping` / `tts_audio_send_delay`——它们各属四个不同的域，进这一组只因模板里紧挨着 `server:` 段落写；「认证与设备」同时装着 SmartConfig 配网、设备 token 认证和「上下文源」（第三项与设备毫无关系，它是给 LLM 注入提示词的）。
3. **页面 URL 与 API 命名不一致**（§8 详述）：固件有归属相反的两个入口、页面跨前缀调设备协议路径、`/xiaozhi/camera` 与 `/xiaozhi/camera/` 双注册、`config` 前缀名下混编五种时态。

---

## 2. 分类轴与判定规则（验收判据 4：任给字段，归属由规则唯一算出）

### 2.1 R1 — 求属主（定域）：「把这个字段写死成常量，谁会受损？」

- **单个可替换组件受损**（换个引擎/插件就不再适用）→ 该组件所在的域。
- **编排者的策略受损**（与选哪个组件无关）→ 按编排者职责落 `系统` / `设备`。
- **多个互不相干的组件同时受损** → 编排者事实 → `系统`。（「多属主」延伸，替代 #14 草案的 R0 路径特判：`log.data_dir` 数据库里躺着数据库、固件库、TTS 输出——多属主 → 系统；而 `ASR.FunASR.model_dir` 只有 FunASR 受损——留在引擎卡内。引擎/插件卡内的 `model_dir` / `output_dir` 等**单属主路径不搬家**。）

### 2.2 R2 — 定分层（常用 / 更多设置）：两问，任一为「是」→ 常用

1. **有没有人引用它？** 别的字段按名字引用它（`selected_module.*` → 引擎块名、`Intent.*.functions` → 插件名、`prompt_template` → `prompt`），或**设备靠它建立连接**（`server.websocket` 一族 provisioning 载荷）。共同点：**改错 = 静默失效**。
2. **它是不是「机器人是谁 / 子系统开不开」的内容？**（`prompt`、`wakeup_words`、`voiceprint.speakers`、`server.auth.enabled`…）——操作者的日常编辑面。

其余（阈值/地址/密钥/格式/调优旋钮）→ 更多设置。（对 #14 的两处有据修正：`close_connection_no_voice_time`、`server.timezone_offset` 原标「常用」，按定案 R2 是纯参数 → **更多设置**。）

### 2.3 卡片内部：接入字段集（两层同样作用于引擎/插件卡内）

卡内：**接入字段 → 常用**（让该引擎/插件能跑起来的必填：端点、凭据、模型与声音标识、协议/语言选择），**调优字段 → 更多设置**（跑起来之后的参数）。接入判定按**字段名字集机械套用**（名字集全文见 §7 末尾）；密钥在卡内属接入（启用该引擎的必填身份项；「已配置/未配置」显式标注兜底，见 §5.5）。

### 2.4 为什么是这条轴（先例对撞与 HA 警告）

三个先例、三条轴（#14 §三逐一核对过源码）：

| 先例 | 一级轴 | 致命误判 |
|---|---|---|
| 上游智控台（S） | YAML 命名空间前缀（`paramCode.startsWith('server.')`） | 把 `Intent.*.functions` 这张插件启用清单判成「通用」；看不见启用状态 |
| OpenWrt LuCI（L） | 管理职责域（Status/System/Services/Network） | 「设备协商参数」在其世界观里根本不是可编辑项 |
| Frigate（F） | 作用域 × 宿主（global/cameras/enrichments/system…） | 所有 AI 增强混进 enrichments |

R（属主轴）在四个纠缠字段（`xiaozhi.audio_params.*`、`voiceprint.speakers`、`plugins.*`、`Intent.functions`）上全部给出可解释的落点（#14 §3.3 对撞表）。**HA 在 2026 年把页面级 Advanced Mode 整个废除了**（`7bea54851` 等三个 commit，`advancedOnly` 现为 0 命中）——「为高级内容单独设域」这个先例刚被推翻。本方案的回答：**两层是域内折叠容器，不是域**；「更多设置」默认折叠、展开零成本（见 §2.5）。

### 2.5 颗粒度与渲染规则

- 两层作用于**两处**：域内散字段（对话/设备/系统等域）与引擎/插件卡片内部。
- **「更多设置」是折叠容器，不是第三个域**；折叠态 DOM 字段数 = 0（这是「深度 ≤3」成立的依据，#17 原型实测）。
- 域内常用层为空时（系统域：常用 0），折叠区不渲染，全部字段平铺。
- **危险度不改变折叠归属**（#20）：折叠归属仍由 R2 机械算出；危险是按钮的视觉属性，不是页面位置属性（§6.6）。

---

## 3. 一级导航骨架：五域 + 逃生口

R1 套完后「高级」名下只剩 4 个纯日志字段（路径类已被多属主规则送进系统），撑不起侧栏一级域；HA 已废除页面级 Advanced Mode（§2.4）。**裁决：不设「高级」域，五域 + 页脚逃生口。**

| 域 | 职责一句话 | 字段数 | 常用 | 更多设置 |
|---|---|---|---|---|
| **对话与角色** | 提示词系、唤醒/退出词、声纹身份、上下文源 | 15 | 11 | 4 |
| **引擎** | VAD/ASR/LLM/VLLM/TTS/Memory 六族 + 选择器 + 引擎全局参数 | 450 | 302 | 148 |
| **插件与工具** | Intent 子树（意图即工具编排器）、plugins.*、外部 MCP、工具调用参数 | 32 | 27 | 5 |
| **设备** | 下发给设备的连接载荷（provisioning）、设备认证、hello 协商、发往设备的节奏与时区 | 17 | 7 | 10 |
| **系统** | 监听地址、会话/文件生命周期、传输保活、日志、跨用途路径（多属主事实） | 13 | 0 | 13 |
| **合计** | | **527** | **347** | **180** |

**逃生口**：原始配置只读 JSON 保留为**侧栏底部的非域小入口**（横线之下、非域样式），通往独立只读页——孤儿字段的最后兑底，不是侧栏第六域（§4.3）。

**两条骨架级约定**（地图已定，不再重开）：

- 不可逆操作**就地保留 + 分级视觉 + 二次确认**（§6），不收单独「危险区」页面。
- 密钥沿用「留空不改、输入覆盖」，并显式标出「已配置 / 未配置」（§5.5）。

---

## 4. 页面拓扑与共享外壳（#19 裁决 + 本票细化）

### 4.1 五域 = 五页，一域一 URL

- 一域一页，URL 见 §8.1。浏览器前进/后退/书签/分享天然可用；切换域 = 真实导航。单页方案被否（URL 永停 `/xiaozhi/config/`、刷新回默认域，现状痛点原样保留）；混合方案被否（「一域一页」规则出例外）。
- 引擎库（450 字段子应用）独立在引擎域页内（§5）；插件库与引擎库同构（§7 移交注记 3）。
- `/xiaozhi/config/` **302 到首域**（对话与角色居首）——旧链接/书签/手输不 404，nginx 与 8003 直连两条路都覆盖（302 由应用层发，不依赖 nginx）。

### 4.2 共享侧栏外壳

- 所有浏览器页面同一副侧栏骨架（窄屏收成图标列），顶栏保留全局动作。
- **保存 / 重启服务只在配置编辑页渲染**——摄像头页与逃生口页无可保存对象，顶栏动作区不渲染。
- **实现机制（本票拍板）：服务端共享模板**——一个 Python 模块生成侧栏 + 顶栏 HTML，各页 handler 调用注入正文。理由：现状无模板引擎、无静态目录（#15 已核），共享模板是单一事实源、最小改动、无客户端闪烁。JS 注入被否（首屏空壳闪烁）；各页自带外壳被否（漂移）。主题用 CSS 变量做成壳 tokens，摄像头页局部覆盖为暗色（暗色摄像头页与亮色壳并存，不强求统一）。

### 4.3 逃生口页

独立只读页（`/xiaozhi/config/raw/`，§8.1）：整份 YAML 一页看完，Ctrl+F 全局兑底保留。侧栏布局：顶部五域、底部横线下非域样式小入口。

### 4.4 摄像头页归位：设备域的实时视图

- 摄像头页与设备域**同壳互链**，归「看设备」家族（在线设备/固件/配网），非配置；**保留独立 URL**（`/xiaozhi/camera/`）供书签挂机监控，**不占一级导航**。
- **入口位置（本票细化）**：设备域运行时面内常驻「摄像头实时画面」入口；在线设备行旁的快捷入口（仅带摄像头能力的设备行显示）为可选增强。设备离线时常驻入口仍在，不依赖设备在线。

### 4.5 hash 深链

域内分组/类目状态写 URL 片段，片段不下发服务器（nginx 与 8003 直连都不用动）。**语法（本票细化）**：

- 引擎页：`#<类目小写id>` 选中类目 tab；`#<类目>/<引擎名>` 选中 tab 并展开/滚动到该引擎卡。例：`/xiaozhi/config/engine/#tts`、`/xiaozhi/config/engine/#tts/MlxKafeiStreamTTS`。
- 其余域页：`#<分组id>`，分组 id = 域内卡片/分组的 kebab-case 标识（如设备域 `#online-devices`、`#firmware`、`#smartconfig`）。
- 片段只用 ASCII id，不含中文。验收判据 2 的「2 次点击」仍按点击算；深链是额外的「一条链接 = 0 点击直达」能力（#17 的类目 tab 状态直接映射到 `#<类目>`）。

### 4.6 跨页脏状态

- 侧栏域项挂**未保存标记**（§5.4 脏计数按域聚合）；带脏切换域页先确认。
- **存储机制（本票拍板）**：编辑缓冲（未保存的值）只活在当前页内存；每域一份**脏摘要**（脏条目路径列表 + 计数，**不含任何值**，密钥值永不落地）写入 `localStorage`，键如 `config-dirty/<domain>`；保存 / 放弃 / 干净离开时清除；侧栏读全部域的摘要画点。
- 浏览器刷新/关页挂 `beforeunload` 兜底——不只护域内切换。

---

## 5. 引擎库（#17 裁决 + 原型实证）

引擎域内两层：上方「**当前生效**」（六行下拉：VAD/ASR/LLM/VLLM/TTS/Memory），下方「**全部引擎**」。实测规模：68 条引擎 / 441 字段，今天页面可见仅 30 字段（14.7 倍）——「全渲染同页」不可行。

### 5.1 承载形态：类目 tab + 跨类目搜索 + `<details>` 折叠

- 类目 tab 把 441 字段切成六块，最大的 TTS（224）仍需搜索才找得动；**折叠是真折叠**（未展开时 DOM 字段数 = 0，「深度 ≤3」据此成立）。不分页（本地单机面板，分页只增加点击数）；不另开路由（68 条路由的维护成本不划算）。
- **搜索必须跨类目**：类目内搜索在 LLM 下搜 `mlx` 得 0 条，而 TTS 实有 5 条——这是「找不到」痛点的直接成因（#17 原型自查复现）。命中项带类目标签。

### 5.2 编辑边界：未启用引擎可编辑、可保存、计入未保存修改

技术前提已核实：保存走端到端整树 diff（`handle_save` 比较 `before`/`after` 两棵掩码后的树），写未启用引擎的路径在协议上完全合法。语义上必须是「算」——用户配好了却存不下来，就是假解耦。

### 5.3 切换选中：算一次未保存修改，单独成组

切换 `selected_module.*` 进脏列表，单列「选中模块」组，条目带 `[选择]` 前缀——它是意图陈述，不是参数调整。切回原值 = 该条脏自动消失（与整树 diff 天然相容）。

### 5.4 脏标记：分两组，随选中实时重算

> 注：#17 结案时曾裁「不区分」（单圆点单计数）；人类随后依「更清楚」取向**主动改判为区分**（地图 #13 Notes，原话「即使分得清，还是按你上一轮的推荐来吧，更清楚点总是好的」）。**本文按改判后的结论写**。

- **两组**：`重启后生效`（`selected_module.*` + **当前选中**引擎的字段）与 `仅提前配好 · 当前不生效`（**未选中**引擎的字段）。
- **分组随 `selected_module` 实时重算**——切选中时旧改动会升降级（原「提前配好」的引擎变成选中，其改动升入「重启后生效」），不是保存时定死的标签。
- 组内条目按 kind 标注：参数（`1024 → 2048`）/ 选择（`[选择] ThirkingLLM → DoubaoLLM`）/ 密钥（`[密钥] 已配置 → 替换为新值`，**永不显示值**）。
- 侧栏脏点 = 本域脏计数聚合；跨域见 §4.6。

### 5.5 密钥三态与「已配置/未配置」判定语义

- 密钥是**三态**：未配置 / 已配置 / 已配置但要替换。第三态必须算脏（真实页面的 `collectDirty` 拿新值比掩码必然不等，两态建模会漏检——#17 原型踩过）。呈现沿用「留空不改、输入覆盖」。
- **「已配置 / 未配置」必须由服务端显式判定并传递，不能靠正则猜掩码形态。** 这是 #17 查明的两个同源真 bug 的教训（未配置密钥显示「已配置」，全库 51 个密钥字段全被误判；注入值伪装成已配置，6 条 openai 引擎）——根因都是**页面把「服务端掩码/注入后的显示态」当成了「配置里真实存在的值」**。修 bug 属实现票（地图 Out of scope 已登记），但本方案的归类与呈现**必须建立在不撒谎的判定语义上**：`api/full` 需为敏感字段同时给出「配置里是否存在该键」的显式信号与掩码值，二者分离。

### 5.6 默认值注入：显式惰性

未配置的引擎，页面**不显示**注入默认值；显示空值 + 占位符「未设置（运行时默认 X）」+ 黄色「注入」标记。显示态永远承认「这里没有值」。同族规则：**页面显示值 ≠ 配置里存在的值**这条陷阱写进实现守则。

---

## 6. 危险操作分级（#20 裁决）

### 6.1 定级规则（机械可算）

单一主轴：**「这个动作的最坏后果，靠再按几次按钮能不能完全撤销？」** 加一条**触发链修正**：动作自身无害但武装了停不下来的链条时，按链条的最坏后果定级。单用可逆性漏「上传固件」（上传可逆、武装链不可逆）；单用影响面漏「删除固件」；单用触发链也漏「删除固件」——三候选轴各漏一角，故复合。

### 6.2 三级与操作落位

| 级 | 定义 | 操作 | 确认档位 |
|---|---|---|---|
| **常规** | 无副作用，或意图已在输入动作里 | 保存普通修改、试连 LLM（点名其外呼副作用、消耗少量配额，明确零确认）、覆盖密钥（往 password 框打字本身就是意图表达） | 无 |
| **警示** | 撤销 = 等待或重做（可恢复的打断） | 重启服务（断连 10–30s 自愈）、SmartConfig 广播（物理世界但重新广播即可覆盖）、重启设备（**固件库无更新版本时**） | 单击确认 |
| **危险** | 撤销不可能，或撤销权不在操作者手里 | 删除固件（信息永失）、上传固件（武装自动升级链，ADR-0002 陷阱的扳机形状）、重启设备（**固件库有更新版本时动态升入**——它变成触发升级的扳机；「库中有更新版本」由现有 `api/devices` 与 `api/firmware` 两个列表对比版本即可算出，纯前端可判） | 强确认（两种形态，§6.3） |

### 6.3 危险级的两种确认形态

- **上传固件、重启设备（危险时）**：页内确认层 + 后果清单（含动态数字：在线设备数、目标固件版本）+ 单击带后果文字的确认按钮。**不设打字摩擦**——它们是正路运维操作，每次 OTA 都走；摩擦太高会催生绕过（scp 直拷 `data/bin/` 连页面都不开）。确认的价值是「让操作者知道链条被武装」，不是阻止。上传固件的确认文案要写准武装对象：**未来所有开机自检**（在线设备不立即升级，重启才升——ADR-0002）。
- **删除固件**：在上述之上**额外输入固件版本号**才能确认。短、有区分度、必看列表才知道——「显式清晰」取向在防手滑上的最小应用，只给真正不可逆且非正路的操作。

### 6.4 视觉编码（颜色 + 图标 + 文案三载体同上，不只靠颜色）

常规 = 默认按钮；警示 = 琥珀/橙 + ⚠ 图标 + 一句后果 hint；危险 = 红色实底 + ⛔ 图标 + 后果文案。

**现状三处严重度倒置随之修复**（实现时）：① 重启服务/重启设备 红 → 琥珀（自愈操作错占红色）；② 删除固件 灰 → 红（唯一不可逆反而是全页最朴素的中性灰按钮）；③ 上传固件 普通按钮 → 红（武装链零确认零视觉）。

### 6.5 确认载体：统一页内确认层

警示 = 重述一句后果 + 确认/取消；危险 = 后果清单 + 确认按钮（删除固件另加版本号输入框）。理由：确认层本身就是分级的视觉载体，原生 `confirm()` 无法分级编码、无法承载输入框与动态后果；确认层是共享外壳的一件组件（§4.2）。现存 `confirm()` 的替换属实现，本票只产出方案。

### 6.6 边界规则

- 密钥操作：今天不存在「清除密钥」操作（留空 = 不变、掩码占位 = 跳过，丢掉旧值只能输入新值覆盖）。立一条规则：**任何密钥破坏性操作按其最坏后果套同一把尺**——将来若新增显式「清除」按钮，自动归危险级，无需再开票。
- 危险度不改变折叠归属（§2.5）；上传/删除固件、重启设备按 R2 落设备域的哪层就待哪层。
- 危险确认层的动态数字同源：在线设备数、目标固件版本，都来自现有两个列表，无需新接口。

---

## 7. 全部字段的旧→新映射（527 字段）

**口径**（沿用 #14）：`list` 一律记 1 个字段；`list[dict]` 展开元素键；空 `object` 记 1。旧页归属沿用 #14 §2 口径：`无归宿（非选中）` = 只在「全部配置」只读 JSON 可见的非选中引擎/意图字段；`—` = 真孤儿。字段类型为 `-` 者 = 本机配置里未启用、只在模板/上游存在的引擎（类型取自模板）。

页面凭空字段 `server.auth.expire_seconds` 不在配置树内，**不落位、不计数、不渲染**（渲染以配置树为据，树外键自然不显示，§9）。

以下五表即裁决结论 + 旧 → 新映射（由 #16 按规则机械产出，可复算）。

旧页归属沿用 #14 §2 口径。`无归宿（非选中）`= 只在「全部配置」只读 JSON 可见的非选中引擎/意图字段；`—` = 真孤儿。

### 对话与角色（15）

机器人是谁、说什么：提示词系、唤醒/退出词、声纹身份、上下文源。改这里的字段 = 改机器人的性格与语言行为。

| 字段 | 类型 | 旧页归属 | 层 | 备注 |
|---|---|---|---|---|
| `enable_wakeup_words_response_cache` | bool | 角色与对话 | 更多设置 |  |
| `enable_greeting` | bool | 角色与对话 | 常用 |  |
| `enable_stop_tts_notify` | bool | 角色与对话 | 常用 |  |
| `stop_tts_notify_voice` | str | 角色与对话 | 常用 |  |
| `exit_commands[i]` | list | 角色与对话 | 常用 |  |
| `wakeup_words[i]` | list | 角色与对话 | 常用 |  |
| `context_providers[i]` | list | 认证与设备 | 常用 |  |
| `voiceprint.url` | NoneType | 语音与音频 | 更多设置 |  |
| `voiceprint.speakers[i]` | list | — | 常用 |  |
| `voiceprint.similarity_threshold` | float | 语音与音频 | 更多设置 |  |
| `prompt` | str | 角色与对话 | 常用 |  |
| `prompt_template` | str | 角色与对话 | 更多设置 |  |
| `system_error_response` | str | 角色与对话 | 常用 |  |
| `end_prompt.enable` | bool | 角色与对话 | 常用 |  |
| `end_prompt.prompt` | str | 角色与对话 | 常用 |  |

> **§7 对话表的一处口径与叶子的落差（本票照表执行，并记下）**：本表把
> `context_providers[i]` 记作 **1 个字段**（「`list` 一律记 1」），共 15 个。
> 但该字段的元素是 dict，实有 `url` 与 `headers.Authorization` 两个叶子 ——
> 域页按本表渲染时只能渲染**一个**「上下文源」控件（`url`）；
> `headers.Authorization` 在本表里没有对应的行，也就没有域内落位。
> 它仍然**完整保留**在旧八组页面与「原始配置」只读页里（零字段丢失，不是
> 被删掉）。落差登记在此，不改表也不逐条重新裁量 —— 归属由规则机械复算，
> 本票只按表执行。

### 引擎（450）

> **§7 引擎表的计数口径（实现时照表执行，并记下）**：本表块内 68 条引擎的
> 字段相加是 **442**（每块头写的数字相加：VAD 5 + ASR 109 + LLM 72 + VLLM 16
> + TTS 224 + Memory 16 = 442），加表头 8 条引擎全局参数（`tts_timeout`、
> `module_test.test_sentences[i]`、六行 `selected_module.*`）= **450**。
> §3 合计表的分层计数（302 常用 / 148 更多设置）含那 8 条：块内 296 / 146
> ——两张表都是对的，差异只在「算不算引擎全局参数」。本票的域表按 450/302/148
> 落地，逐条对账见 `server/tests/test_config_engine_seam.py`。

可替换组件：VAD/ASR/LLM/VLLM/TTS/Memory 六族 + 选择器 + 引擎全局参数。写死某字段成常量，受损的是该引擎的实现。

| 字段 | 类型 | 旧页归属 | 层 | 备注 |
|---|---|---|---|---|
| `tts_timeout` | int | 服务器/连接 | 更多设置 | 引擎全局参数（各 TTS 卡内同名键可覆盖） |
| `module_test.test_sentences[i]` | list | 语音与音频 | 更多设置 |  |
| `selected_module.VAD` | str | 模型引擎 | 常用 |  |
| `selected_module.ASR` | str | 模型引擎 | 常用 |  |
| `selected_module.LLM` | str | 模型引擎 | 常用 |  |
| `selected_module.VLLM` | str | 模型引擎 | 常用 |  |
| `selected_module.TTS` | str | 模型引擎 | 常用 |  |
| `selected_module.Memory` | str | 模型引擎 | 常用 |  |

**`VAD.SileroVAD`**（5 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `VAD.SileroVAD.type` | str | 常用 |  |
| `VAD.SileroVAD.threshold` | float | 更多设置 |  |
| `VAD.SileroVAD.threshold_low` | float | 更多设置 |  |
| `VAD.SileroVAD.model_dir` | str | 常用 |  |
| `VAD.SileroVAD.min_silence_duration_ms` | int | 更多设置 |  |

**`ASR.FunASR`**（4 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.FunASR.type` | str | 常用 |  |
| `ASR.FunASR.model_dir` | str | 常用 |  |
| `ASR.FunASR.output_dir` | str | 更多设置 |  |
| `ASR.FunASR.language` | str | 常用 |  |

**`LLM.ThirkingLLM`**（7 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.ThirkingLLM.type` | str | 常用 |  |
| `LLM.ThirkingLLM.model_name` | str | 常用 |  |
| `LLM.ThirkingLLM.url` | str | 常用 |  |
| `LLM.ThirkingLLM.api_key` | str | 常用 |  |
| `LLM.ThirkingLLM.max_tokens` | int | 更多设置 |  |
| `LLM.ThirkingLLM.temperature` | float | 更多设置 |  |
| `LLM.ThirkingLLM.reasoning_effort` | str | 更多设置 |  |

**`VLLM.ThirkingVLLM`**（4 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `VLLM.ThirkingVLLM.type` | str | 常用 |  |
| `VLLM.ThirkingVLLM.model_name` | str | 常用 |  |
| `VLLM.ThirkingVLLM.url` | str | 常用 |  |
| `VLLM.ThirkingVLLM.api_key` | str | 常用 |  |

**`TTS.MlxKafeiStreamTTS`**（9 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MlxKafeiStreamTTS.type` | str | 常用 |  |
| `TTS.MlxKafeiStreamTTS.url` | str | 常用 |  |
| `TTS.MlxKafeiStreamTTS.speed` | float | 更多设置 |  |
| `TTS.MlxKafeiStreamTTS.output_dir` | str | 更多设置 |  |
| `TTS.MlxKafeiStreamTTS.split_sentences` | bool | 更多设置 |  |
| `TTS.MlxKafeiStreamTTS.tts_timeout` | int | 更多设置 |  |
| `TTS.MlxKafeiStreamTTS.voice` | str | 常用 |  |
| `TTS.MlxKafeiStreamTTS.ref_audio` | str | 常用 |  |
| `TTS.MlxKafeiStreamTTS.ref_text` | str | 常用 |  |

**`Memory.nomem`**（1 字段；旧归属：模型引擎）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Memory.nomem.type` | str | 常用 |  |

**`ASR.FunASRServer`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.FunASRServer.type` | - | 常用 |  |
| `ASR.FunASRServer.host` | - | 常用 |  |
| `ASR.FunASRServer.port` | - | 常用 |  |
| `ASR.FunASRServer.is_ssl` | - | 常用 |  |
| `ASR.FunASRServer.api_key` | - | 常用 |  |
| `ASR.FunASRServer.output_dir` | - | 更多设置 |  |

**`ASR.SherpaASR`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.SherpaASR.type` | - | 常用 |  |
| `ASR.SherpaASR.model_dir` | - | 常用 |  |
| `ASR.SherpaASR.output_dir` | - | 更多设置 |  |
| `ASR.SherpaASR.model_type` | - | 常用 |  |

**`ASR.SherpaParaformerASR`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.SherpaParaformerASR.type` | - | 常用 |  |
| `ASR.SherpaParaformerASR.model_dir` | - | 常用 |  |
| `ASR.SherpaParaformerASR.output_dir` | - | 更多设置 |  |
| `ASR.SherpaParaformerASR.model_type` | - | 常用 |  |

**`ASR.DoubaoASR`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.DoubaoASR.type` | - | 常用 |  |
| `ASR.DoubaoASR.appid` | - | 常用 |  |
| `ASR.DoubaoASR.access_token` | - | 常用 |  |
| `ASR.DoubaoASR.cluster` | - | 常用 |  |
| `ASR.DoubaoASR.boosting_table_name` | - | 更多设置 |  |
| `ASR.DoubaoASR.correct_table_name` | - | 更多设置 |  |
| `ASR.DoubaoASR.output_dir` | - | 更多设置 |  |

**`ASR.DoubaoStreamASR`**（9 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.DoubaoStreamASR.type` | - | 常用 |  |
| `ASR.DoubaoStreamASR.appid` | - | 常用 |  |
| `ASR.DoubaoStreamASR.access_token` | - | 常用 |  |
| `ASR.DoubaoStreamASR.resource_id` | - | 常用 |  |
| `ASR.DoubaoStreamASR.boosting_table_name` | - | 更多设置 |  |
| `ASR.DoubaoStreamASR.correct_table_name` | - | 更多设置 |  |
| `ASR.DoubaoStreamASR.enable_multilingual` | - | 更多设置 |  |
| `ASR.DoubaoStreamASR.end_window_size` | - | 更多设置 |  |
| `ASR.DoubaoStreamASR.output_dir` | - | 更多设置 |  |

**`ASR.DoubaoStreamASRV2`**（9 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.DoubaoStreamASRV2.type` | - | 常用 |  |
| `ASR.DoubaoStreamASRV2.appid` | - | 常用 |  |
| `ASR.DoubaoStreamASRV2.access_token` | - | 常用 |  |
| `ASR.DoubaoStreamASRV2.resource_id` | - | 常用 |  |
| `ASR.DoubaoStreamASRV2.boosting_table_name` | - | 更多设置 |  |
| `ASR.DoubaoStreamASRV2.correct_table_name` | - | 更多设置 |  |
| `ASR.DoubaoStreamASRV2.enable_multilingual` | - | 更多设置 |  |
| `ASR.DoubaoStreamASRV2.end_window_size` | - | 更多设置 |  |
| `ASR.DoubaoStreamASRV2.output_dir` | - | 更多设置 |  |

**`ASR.TencentASR`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.TencentASR.type` | - | 常用 |  |
| `ASR.TencentASR.appid` | - | 常用 |  |
| `ASR.TencentASR.secret_id` | - | 常用 |  |
| `ASR.TencentASR.secret_key` | - | 常用 |  |
| `ASR.TencentASR.output_dir` | - | 更多设置 |  |

**`ASR.AliyunASR`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.AliyunASR.type` | - | 常用 |  |
| `ASR.AliyunASR.appkey` | - | 常用 |  |
| `ASR.AliyunASR.token` | - | 常用 |  |
| `ASR.AliyunASR.access_key_id` | - | 常用 |  |
| `ASR.AliyunASR.access_key_secret` | - | 常用 |  |
| `ASR.AliyunASR.output_dir` | - | 更多设置 |  |

**`ASR.AliyunStreamASR`**（8 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.AliyunStreamASR.type` | - | 常用 |  |
| `ASR.AliyunStreamASR.appkey` | - | 常用 |  |
| `ASR.AliyunStreamASR.token` | - | 常用 |  |
| `ASR.AliyunStreamASR.access_key_id` | - | 常用 |  |
| `ASR.AliyunStreamASR.access_key_secret` | - | 常用 |  |
| `ASR.AliyunStreamASR.host` | - | 常用 |  |
| `ASR.AliyunStreamASR.max_sentence_silence` | - | 更多设置 |  |
| `ASR.AliyunStreamASR.output_dir` | - | 更多设置 |  |

**`ASR.BaiduASR`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.BaiduASR.type` | - | 常用 |  |
| `ASR.BaiduASR.app_id` | - | 常用 |  |
| `ASR.BaiduASR.api_key` | - | 常用 |  |
| `ASR.BaiduASR.secret_key` | - | 常用 |  |
| `ASR.BaiduASR.dev_pid` | - | 常用 |  |
| `ASR.BaiduASR.output_dir` | - | 更多设置 |  |

**`ASR.OpenaiASR`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.OpenaiASR.type` | - | 常用 |  |
| `ASR.OpenaiASR.api_key` | - | 常用 |  |
| `ASR.OpenaiASR.base_url` | - | 常用 |  |
| `ASR.OpenaiASR.model_name` | - | 常用 |  |
| `ASR.OpenaiASR.output_dir` | - | 更多设置 |  |

**`ASR.GroqASR`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.GroqASR.type` | - | 常用 |  |
| `ASR.GroqASR.api_key` | - | 常用 |  |
| `ASR.GroqASR.base_url` | - | 常用 |  |
| `ASR.GroqASR.model_name` | - | 常用 |  |
| `ASR.GroqASR.output_dir` | - | 更多设置 |  |

**`ASR.VoskASR`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.VoskASR.type` | - | 常用 |  |
| `ASR.VoskASR.model_path` | - | 常用 |  |
| `ASR.VoskASR.output_dir` | - | 更多设置 |  |

**`ASR.Qwen3ASRFlash`**（8 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.Qwen3ASRFlash.type` | - | 常用 |  |
| `ASR.Qwen3ASRFlash.api_key` | - | 常用 |  |
| `ASR.Qwen3ASRFlash.base_url` | - | 常用 |  |
| `ASR.Qwen3ASRFlash.model_name` | - | 常用 |  |
| `ASR.Qwen3ASRFlash.output_dir` | - | 更多设置 |  |
| `ASR.Qwen3ASRFlash.enable_lid` | - | 更多设置 |  |
| `ASR.Qwen3ASRFlash.enable_itn` | - | 更多设置 |  |
| `ASR.Qwen3ASRFlash.context` | - | 更多设置 |  |

**`ASR.XunfeiStreamASR`**（8 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.XunfeiStreamASR.type` | - | 常用 |  |
| `ASR.XunfeiStreamASR.app_id` | - | 常用 |  |
| `ASR.XunfeiStreamASR.api_key` | - | 常用 |  |
| `ASR.XunfeiStreamASR.api_secret` | - | 常用 |  |
| `ASR.XunfeiStreamASR.domain` | - | 常用 |  |
| `ASR.XunfeiStreamASR.language` | - | 常用 |  |
| `ASR.XunfeiStreamASR.accent` | - | 常用 |  |
| `ASR.XunfeiStreamASR.output_dir` | - | 更多设置 |  |

**`ASR.AliyunBLStreamASR`**（12 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `ASR.AliyunBLStreamASR.type` | - | 常用 |  |
| `ASR.AliyunBLStreamASR.api_key` | - | 常用 |  |
| `ASR.AliyunBLStreamASR.model` | - | 常用 |  |
| `ASR.AliyunBLStreamASR.format` | - | 常用 |  |
| `ASR.AliyunBLStreamASR.sample_rate` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.disfluency_removal_enabled` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.semantic_punctuation_enabled` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.max_sentence_silence` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.multi_threshold_mode_enabled` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.punctuation_prediction_enabled` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.inverse_text_normalization_enabled` | - | 更多设置 |  |
| `ASR.AliyunBLStreamASR.output_dir` | - | 更多设置 |  |

**`LLM.AliLLM`**（8 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.AliLLM.type` | - | 常用 |  |
| `LLM.AliLLM.base_url` | - | 常用 |  |
| `LLM.AliLLM.model_name` | - | 常用 |  |
| `LLM.AliLLM.api_key` | - | 常用 |  |
| `LLM.AliLLM.temperature` | - | 更多设置 |  |
| `LLM.AliLLM.max_tokens` | - | 更多设置 |  |
| `LLM.AliLLM.top_p` | - | 更多设置 |  |
| `LLM.AliLLM.frequency_penalty` | - | 更多设置 |  |

**`LLM.AliAppLLM`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.AliAppLLM.type` | - | 常用 |  |
| `LLM.AliAppLLM.base_url` | - | 常用 |  |
| `LLM.AliAppLLM.app_id` | - | 常用 |  |
| `LLM.AliAppLLM.api_key` | - | 常用 |  |
| `LLM.AliAppLLM.is_no_prompt` | - | 更多设置 |  |
| `LLM.AliAppLLM.ali_memory_id` | - | 更多设置 |  |

**`LLM.DoubaoLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.DoubaoLLM.type` | - | 常用 |  |
| `LLM.DoubaoLLM.base_url` | - | 常用 |  |
| `LLM.DoubaoLLM.model_name` | - | 常用 |  |
| `LLM.DoubaoLLM.api_key` | - | 常用 |  |

**`LLM.DeepSeekLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.DeepSeekLLM.type` | - | 常用 |  |
| `LLM.DeepSeekLLM.model_name` | - | 常用 |  |
| `LLM.DeepSeekLLM.url` | - | 常用 |  |
| `LLM.DeepSeekLLM.api_key` | - | 常用 |  |

**`LLM.ChatGLMLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.ChatGLMLLM.type` | - | 常用 |  |
| `LLM.ChatGLMLLM.model_name` | - | 常用 |  |
| `LLM.ChatGLMLLM.url` | - | 常用 |  |
| `LLM.ChatGLMLLM.api_key` | - | 常用 |  |

**`LLM.OllamaLLM`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.OllamaLLM.type` | - | 常用 |  |
| `LLM.OllamaLLM.model_name` | - | 常用 |  |
| `LLM.OllamaLLM.base_url` | - | 常用 |  |

**`LLM.DifyLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.DifyLLM.type` | - | 常用 |  |
| `LLM.DifyLLM.base_url` | - | 常用 |  |
| `LLM.DifyLLM.api_key` | - | 常用 |  |
| `LLM.DifyLLM.mode` | - | 常用 |  |

**`LLM.GeminiLLM`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.GeminiLLM.type` | - | 常用 |  |
| `LLM.GeminiLLM.api_key` | - | 常用 |  |
| `LLM.GeminiLLM.model_name` | - | 常用 |  |
| `LLM.GeminiLLM.http_proxy` | - | 更多设置 |  |
| `LLM.GeminiLLM.https_proxy` | - | 更多设置 |  |

**`LLM.CozeLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.CozeLLM.type` | - | 常用 |  |
| `LLM.CozeLLM.bot_id` | - | 常用 |  |
| `LLM.CozeLLM.user_id` | - | 常用 |  |
| `LLM.CozeLLM.personal_access_token` | - | 常用 |  |

**`LLM.VolcesAiGatewayLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.VolcesAiGatewayLLM.type` | - | 常用 |  |
| `LLM.VolcesAiGatewayLLM.base_url` | - | 常用 |  |
| `LLM.VolcesAiGatewayLLM.model_name` | - | 常用 |  |
| `LLM.VolcesAiGatewayLLM.api_key` | - | 常用 |  |

**`LLM.LMStudioLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.LMStudioLLM.type` | - | 常用 |  |
| `LLM.LMStudioLLM.model_name` | - | 常用 |  |
| `LLM.LMStudioLLM.url` | - | 常用 |  |
| `LLM.LMStudioLLM.api_key` | - | 常用 |  |

**`LLM.HomeAssistant`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.HomeAssistant.type` | - | 常用 |  |
| `LLM.HomeAssistant.base_url` | - | 常用 |  |
| `LLM.HomeAssistant.agent_id` | - | 常用 |  |
| `LLM.HomeAssistant.api_key` | - | 常用 |  |

**`LLM.FastgptLLM`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.FastgptLLM.type` | - | 常用 |  |
| `LLM.FastgptLLM.base_url` | - | 常用 |  |
| `LLM.FastgptLLM.api_key` | - | 常用 |  |
| `LLM.FastgptLLM.variables.k` | - | 更多设置 |  |
| `LLM.FastgptLLM.variables.k2` | - | 更多设置 |  |

**`LLM.XinferenceLLM`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.XinferenceLLM.type` | - | 常用 |  |
| `LLM.XinferenceLLM.model_name` | - | 常用 |  |
| `LLM.XinferenceLLM.base_url` | - | 常用 |  |

**`LLM.XinferenceSmallLLM`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `LLM.XinferenceSmallLLM.type` | - | 常用 |  |
| `LLM.XinferenceSmallLLM.model_name` | - | 常用 |  |
| `LLM.XinferenceSmallLLM.base_url` | - | 常用 |  |

**`VLLM.ChatGLMVLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `VLLM.ChatGLMVLLM.type` | - | 常用 |  |
| `VLLM.ChatGLMVLLM.model_name` | - | 常用 |  |
| `VLLM.ChatGLMVLLM.url` | - | 常用 |  |
| `VLLM.ChatGLMVLLM.api_key` | - | 常用 |  |

**`VLLM.QwenVLVLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `VLLM.QwenVLVLLM.type` | - | 常用 |  |
| `VLLM.QwenVLVLLM.model_name` | - | 常用 |  |
| `VLLM.QwenVLVLLM.url` | - | 常用 |  |
| `VLLM.QwenVLVLLM.api_key` | - | 常用 |  |

**`VLLM.XunfeiSparkLLM`**（4 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `VLLM.XunfeiSparkLLM.type` | - | 常用 |  |
| `VLLM.XunfeiSparkLLM.base_url` | - | 常用 |  |
| `VLLM.XunfeiSparkLLM.model_name` | - | 常用 |  |
| `VLLM.XunfeiSparkLLM.api_key` | - | 常用 |  |

**`TTS.EdgeTTS`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.EdgeTTS.type` | - | 常用 |  |
| `TTS.EdgeTTS.voice` | - | 常用 |  |
| `TTS.EdgeTTS.output_dir` | - | 更多设置 |  |

**`TTS.DoubaoTTS`**（11 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.DoubaoTTS.type` | - | 常用 |  |
| `TTS.DoubaoTTS.api_url` | - | 常用 |  |
| `TTS.DoubaoTTS.voice` | - | 常用 |  |
| `TTS.DoubaoTTS.output_dir` | - | 更多设置 |  |
| `TTS.DoubaoTTS.authorization` | - | 常用 |  |
| `TTS.DoubaoTTS.appid` | - | 常用 |  |
| `TTS.DoubaoTTS.access_token` | - | 常用 |  |
| `TTS.DoubaoTTS.cluster` | - | 常用 |  |
| `TTS.DoubaoTTS.speed_ratio` | - | 更多设置 |  |
| `TTS.DoubaoTTS.volume_ratio` | - | 更多设置 |  |
| `TTS.DoubaoTTS.pitch_ratio` | - | 更多设置 |  |

**`TTS.HuoshanDoubleStreamTTS`**（10 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.HuoshanDoubleStreamTTS.type` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.ws_url` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.appid` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.access_token` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.resource_id` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.speaker` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTS.enable_ws_reuse` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTS.audio_params.speech_rate` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTS.audio_params.loudness_rate` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTS.additions.post_process.pitch` | - | 更多设置 |  |

**`TTS.HuoshanDoubleStreamTTSV2`**（10 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.HuoshanDoubleStreamTTSV2.type` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.ws_url` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.appid` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.access_token` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.resource_id` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.speaker` | - | 常用 |  |
| `TTS.HuoshanDoubleStreamTTSV2.enable_ws_reuse` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTSV2.audio_params.speech_rate` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTSV2.audio_params.loudness_rate` | - | 更多设置 |  |
| `TTS.HuoshanDoubleStreamTTSV2.additions.post_process.pitch` | - | 更多设置 |  |

**`TTS.CosyVoiceSiliconflow`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.CosyVoiceSiliconflow.type` | - | 常用 |  |
| `TTS.CosyVoiceSiliconflow.model` | - | 常用 |  |
| `TTS.CosyVoiceSiliconflow.voice` | - | 常用 |  |
| `TTS.CosyVoiceSiliconflow.output_dir` | - | 更多设置 |  |
| `TTS.CosyVoiceSiliconflow.access_token` | - | 常用 |  |
| `TTS.CosyVoiceSiliconflow.response_format` | - | 常用 |  |

**`TTS.CozeCnTTS`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.CozeCnTTS.type` | - | 常用 |  |
| `TTS.CozeCnTTS.voice` | - | 常用 |  |
| `TTS.CozeCnTTS.output_dir` | - | 更多设置 |  |
| `TTS.CozeCnTTS.access_token` | - | 常用 |  |
| `TTS.CozeCnTTS.response_format` | - | 常用 |  |

**`TTS.VolcesAiGatewayTTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.VolcesAiGatewayTTS.type` | - | 常用 |  |
| `TTS.VolcesAiGatewayTTS.api_key` | - | 常用 |  |
| `TTS.VolcesAiGatewayTTS.api_url` | - | 常用 |  |
| `TTS.VolcesAiGatewayTTS.model` | - | 常用 |  |
| `TTS.VolcesAiGatewayTTS.voice` | - | 常用 |  |
| `TTS.VolcesAiGatewayTTS.speed` | - | 更多设置 |  |
| `TTS.VolcesAiGatewayTTS.output_dir` | - | 更多设置 |  |

**`TTS.FishSpeech`**（19 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.FishSpeech.type` | - | 常用 |  |
| `TTS.FishSpeech.output_dir` | - | 更多设置 |  |
| `TTS.FishSpeech.response_format` | - | 常用 |  |
| `TTS.FishSpeech.reference_id` | - | 常用 |  |
| `TTS.FishSpeech.reference_audio` | - | 常用 |  |
| `TTS.FishSpeech.reference_text` | - | 常用 |  |
| `TTS.FishSpeech.normalize` | - | 更多设置 |  |
| `TTS.FishSpeech.max_new_tokens` | - | 更多设置 |  |
| `TTS.FishSpeech.chunk_length` | - | 更多设置 |  |
| `TTS.FishSpeech.top_p` | - | 更多设置 |  |
| `TTS.FishSpeech.repetition_penalty` | - | 更多设置 |  |
| `TTS.FishSpeech.temperature` | - | 更多设置 |  |
| `TTS.FishSpeech.streaming` | - | 更多设置 |  |
| `TTS.FishSpeech.use_memory_cache` | - | 更多设置 |  |
| `TTS.FishSpeech.seed` | - | 更多设置 |  |
| `TTS.FishSpeech.channels` | - | 常用 |  |
| `TTS.FishSpeech.rate` | - | 常用 |  |
| `TTS.FishSpeech.api_key` | - | 常用 |  |
| `TTS.FishSpeech.api_url` | - | 常用 |  |

**`TTS.GPT_SOVITS_V2`**（21 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.GPT_SOVITS_V2.type` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.url` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.output_dir` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.text_lang` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.ref_audio_path` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.prompt_text` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.prompt_lang` | - | 常用 |  |
| `TTS.GPT_SOVITS_V2.top_k` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.top_p` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.temperature` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.text_split_method` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.batch_size` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.batch_threshold` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.split_bucket` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.return_fragment` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.speed_factor` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.streaming_mode` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.seed` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.parallel_infer` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.repetition_penalty` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V2.aux_ref_audio_paths` | - | 常用 |  |

**`TTS.GPT_SOVITS_V3`**（15 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.GPT_SOVITS_V3.type` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.url` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.output_dir` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.text_language` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.refer_wav_path` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.prompt_language` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.prompt_text` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.top_k` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.top_p` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.temperature` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.cut_punc` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.speed` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.inp_refs` | - | 常用 |  |
| `TTS.GPT_SOVITS_V3.sample_steps` | - | 更多设置 |  |
| `TTS.GPT_SOVITS_V3.if_sr` | - | 更多设置 |  |

**`TTS.MinimaxTTSHTTPStream`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MinimaxTTSHTTPStream.type` | - | 常用 |  |
| `TTS.MinimaxTTSHTTPStream.output_dir` | - | 更多设置 |  |
| `TTS.MinimaxTTSHTTPStream.group_id` | - | 常用 |  |
| `TTS.MinimaxTTSHTTPStream.api_key` | - | 常用 |  |
| `TTS.MinimaxTTSHTTPStream.model` | - | 常用 |  |
| `TTS.MinimaxTTSHTTPStream.voice_id` | - | 常用 |  |

**`TTS.AliyunTTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.AliyunTTS.type` | - | 常用 |  |
| `TTS.AliyunTTS.output_dir` | - | 更多设置 |  |
| `TTS.AliyunTTS.appkey` | - | 常用 |  |
| `TTS.AliyunTTS.token` | - | 常用 |  |
| `TTS.AliyunTTS.voice` | - | 常用 |  |
| `TTS.AliyunTTS.access_key_id` | - | 常用 |  |
| `TTS.AliyunTTS.access_key_secret` | - | 常用 |  |

**`TTS.AliyunStreamTTS`**（8 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.AliyunStreamTTS.type` | - | 常用 |  |
| `TTS.AliyunStreamTTS.output_dir` | - | 更多设置 |  |
| `TTS.AliyunStreamTTS.appkey` | - | 常用 |  |
| `TTS.AliyunStreamTTS.token` | - | 常用 |  |
| `TTS.AliyunStreamTTS.voice` | - | 常用 |  |
| `TTS.AliyunStreamTTS.access_key_id` | - | 常用 |  |
| `TTS.AliyunStreamTTS.access_key_secret` | - | 常用 |  |
| `TTS.AliyunStreamTTS.host` | - | 常用 |  |

**`TTS.TencentTTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.TencentTTS.type` | - | 常用 |  |
| `TTS.TencentTTS.output_dir` | - | 更多设置 |  |
| `TTS.TencentTTS.appid` | - | 常用 |  |
| `TTS.TencentTTS.secret_id` | - | 常用 |  |
| `TTS.TencentTTS.secret_key` | - | 常用 |  |
| `TTS.TencentTTS.region` | - | 常用 |  |
| `TTS.TencentTTS.voice` | - | 常用 |  |

**`TTS.TTS302AI`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.TTS302AI.type` | - | 常用 |  |
| `TTS.TTS302AI.api_url` | - | 常用 |  |
| `TTS.TTS302AI.authorization` | - | 常用 |  |
| `TTS.TTS302AI.voice` | - | 常用 |  |
| `TTS.TTS302AI.output_dir` | - | 更多设置 |  |
| `TTS.TTS302AI.access_token` | - | 常用 |  |

**`TTS.OpenAITTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.OpenAITTS.type` | - | 常用 |  |
| `TTS.OpenAITTS.api_key` | - | 常用 |  |
| `TTS.OpenAITTS.api_url` | - | 常用 |  |
| `TTS.OpenAITTS.model` | - | 常用 |  |
| `TTS.OpenAITTS.voice` | - | 常用 |  |
| `TTS.OpenAITTS.speed` | - | 更多设置 |  |
| `TTS.OpenAITTS.output_dir` | - | 更多设置 |  |

**`TTS.CustomTTS`**（14 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.CustomTTS.type` | - | 常用 |  |
| `TTS.CustomTTS.method` | - | 常用 |  |
| `TTS.CustomTTS.url` | - | 常用 |  |
| `TTS.CustomTTS.params.input` | - | 更多设置 |  |
| `TTS.CustomTTS.params.response_format` | - | 常用 |  |
| `TTS.CustomTTS.params.download_format` | - | 更多设置 |  |
| `TTS.CustomTTS.params.voice` | - | 常用 |  |
| `TTS.CustomTTS.params.lang_code` | - | 更多设置 |  |
| `TTS.CustomTTS.params.return_download_link` | - | 更多设置 |  |
| `TTS.CustomTTS.params.speed` | - | 更多设置 |  |
| `TTS.CustomTTS.params.stream` | - | 更多设置 |  |
| `TTS.CustomTTS.headers` | - | 更多设置 |  |
| `TTS.CustomTTS.format` | - | 常用 |  |
| `TTS.CustomTTS.output_dir` | - | 更多设置 |  |

**`TTS.PaddleSpeechTTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.PaddleSpeechTTS.type` | - | 常用 |  |
| `TTS.PaddleSpeechTTS.protocol` | - | 常用 |  |
| `TTS.PaddleSpeechTTS.url` | - | 常用 |  |
| `TTS.PaddleSpeechTTS.spk_id` | - | 常用 |  |
| `TTS.PaddleSpeechTTS.speed` | - | 更多设置 |  |
| `TTS.PaddleSpeechTTS.volume` | - | 更多设置 |  |
| `TTS.PaddleSpeechTTS.save_path` | - | 更多设置 |  |

**`TTS.IndexStreamTTS`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.IndexStreamTTS.type` | - | 常用 |  |
| `TTS.IndexStreamTTS.api_url` | - | 常用 |  |
| `TTS.IndexStreamTTS.audio_format` | - | 常用 |  |
| `TTS.IndexStreamTTS.voice` | - | 常用 |  |
| `TTS.IndexStreamTTS.output_dir` | - | 更多设置 |  |

**`TTS.AliBLTTS`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.AliBLTTS.type` | - | 常用 |  |
| `TTS.AliBLTTS.api_key` | - | 常用 |  |
| `TTS.AliBLTTS.model` | - | 常用 |  |
| `TTS.AliBLTTS.voice` | - | 常用 |  |
| `TTS.AliBLTTS.output_dir` | - | 更多设置 |  |

**`TTS.XunFeiTTS`**（7 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.XunFeiTTS.type` | - | 常用 |  |
| `TTS.XunFeiTTS.api_url` | - | 常用 |  |
| `TTS.XunFeiTTS.app_id` | - | 常用 |  |
| `TTS.XunFeiTTS.api_secret` | - | 常用 |  |
| `TTS.XunFeiTTS.api_key` | - | 常用 |  |
| `TTS.XunFeiTTS.voice` | - | 常用 |  |
| `TTS.XunFeiTTS.output_dir` | - | 更多设置 |  |

**`TTS.MlxTTS`**（5 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MlxTTS.type` | - | 常用 |  |
| `TTS.MlxTTS.url` | - | 常用 |  |
| `TTS.MlxTTS.speed` | - | 更多设置 |  |
| `TTS.MlxTTS.output_dir` | - | 更多设置 |  |
| `TTS.MlxTTS.split_sentences` | - | 更多设置 |  |

**`TTS.MlxStreamTTS`**（6 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MlxStreamTTS.type` | - | 常用 |  |
| `TTS.MlxStreamTTS.url` | - | 常用 |  |
| `TTS.MlxStreamTTS.speed` | - | 更多设置 |  |
| `TTS.MlxStreamTTS.output_dir` | - | 更多设置 |  |
| `TTS.MlxStreamTTS.split_sentences` | - | 更多设置 |  |
| `TTS.MlxStreamTTS.tts_timeout` | - | 更多设置 |  |

**`TTS.MlxWanwanStreamTTS`**（9 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MlxWanwanStreamTTS.type` | - | 常用 |  |
| `TTS.MlxWanwanStreamTTS.url` | - | 常用 |  |
| `TTS.MlxWanwanStreamTTS.speed` | - | 更多设置 |  |
| `TTS.MlxWanwanStreamTTS.output_dir` | - | 更多设置 |  |
| `TTS.MlxWanwanStreamTTS.split_sentences` | - | 更多设置 |  |
| `TTS.MlxWanwanStreamTTS.tts_timeout` | - | 更多设置 |  |
| `TTS.MlxWanwanStreamTTS.voice` | - | 常用 |  |
| `TTS.MlxWanwanStreamTTS.ref_audio` | - | 常用 |  |
| `TTS.MlxWanwanStreamTTS.ref_text` | - | 常用 |  |

**`TTS.MlxMengwaStreamTTS`**（9 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `TTS.MlxMengwaStreamTTS.type` | - | 常用 |  |
| `TTS.MlxMengwaStreamTTS.url` | - | 常用 |  |
| `TTS.MlxMengwaStreamTTS.speed` | - | 更多设置 |  |
| `TTS.MlxMengwaStreamTTS.output_dir` | - | 更多设置 |  |
| `TTS.MlxMengwaStreamTTS.split_sentences` | - | 更多设置 |  |
| `TTS.MlxMengwaStreamTTS.tts_timeout` | - | 更多设置 |  |
| `TTS.MlxMengwaStreamTTS.voice` | - | 常用 |  |
| `TTS.MlxMengwaStreamTTS.ref_audio` | - | 常用 |  |
| `TTS.MlxMengwaStreamTTS.ref_text` | - | 常用 |  |

**`Memory.mem0ai`**（2 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Memory.mem0ai.type` | - | 常用 |  |
| `Memory.mem0ai.api_key` | - | 常用 |  |

**`Memory.powermem`**（11 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Memory.powermem.type` | - | 常用 |  |
| `Memory.powermem.enable_user_profile` | - | 更多设置 |  |
| `Memory.powermem.llm.provider` | - | 常用 |  |
| `Memory.powermem.llm.config.api_key` | - | 常用 |  |
| `Memory.powermem.llm.config.model` | - | 常用 |  |
| `Memory.powermem.embedder.provider` | - | 常用 |  |
| `Memory.powermem.embedder.config.api_key` | - | 常用 |  |
| `Memory.powermem.embedder.config.model` | - | 常用 |  |
| `Memory.powermem.embedder.config.openai_base_url` | - | 更多设置 |  |
| `Memory.powermem.vector_store.provider` | - | 常用 |  |
| `Memory.powermem.vector_store.config` | - | 更多设置 |  |

**`Memory.mem_local_short`**（2 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Memory.mem_local_short.type` | - | 常用 |  |
| `Memory.mem_local_short.llm` | - | 常用 |  |

### 插件与工具（32）

意图编排与工具面：Intent 子树（意图即工具编排器）、plugins.*、外部 MCP、工具调用参数。

| 字段 | 类型 | 旧页归属 | 层 | 备注 |
|---|---|---|---|---|
| `tool_call_timeout` | int | 服务器/连接 | 更多设置 |  |
| `mcp_endpoint` | str | — | 更多设置 | 外部 MCP 接入点 |
| `selected_module.Intent` | str | 意图与插件 | 常用 |  |

**`plugins.get_weather`**（3 字段；旧归属：意图与插件·已启用）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.get_weather.api_host` | str | 常用 |  |
| `plugins.get_weather.api_key` | str | 常用 |  |
| `plugins.get_weather.default_location` | str | 常用 |  |

**`plugins.get_news_from_chinanews`**（4 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.get_news_from_chinanews.default_rss_url` | str | 常用 |  |
| `plugins.get_news_from_chinanews.society_rss_url` | str | 常用 |  |
| `plugins.get_news_from_chinanews.world_rss_url` | str | 常用 |  |
| `plugins.get_news_from_chinanews.finance_rss_url` | str | 常用 |  |

**`plugins.get_news_from_newsnow`**（2 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.get_news_from_newsnow.url` | str | 常用 |  |
| `plugins.get_news_from_newsnow.news_sources` | str | 常用 |  |

**`plugins.home_assistant`**（3 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.home_assistant.devices[i]` | list | 常用 |  |
| `plugins.home_assistant.base_url` | str | 常用 |  |
| `plugins.home_assistant.api_key` | str | 常用 |  |

**`plugins.play_music`**（3 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.play_music.music_dir` | str | 常用 |  |
| `plugins.play_music.music_ext[i]` | list | 更多设置 |  |
| `plugins.play_music.refresh_time` | int | 更多设置 |  |

**`plugins.search_from_ragflow`**（4 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.search_from_ragflow.description` | str | 常用 |  |
| `plugins.search_from_ragflow.base_url` | str | 常用 |  |
| `plugins.search_from_ragflow.api_key` | str | 常用 |  |
| `plugins.search_from_ragflow.dataset_ids[i]` | list | 常用 |  |

**`plugins.web_search`**（4 字段；旧归属：意图与插件·未启用折叠）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `plugins.web_search.provider` | str | 常用 |  |
| `plugins.web_search.description` | str | 常用 |  |
| `plugins.web_search.max_results` | int | 更多设置 |  |
| `plugins.web_search.api_key` | str | 常用 |  |

**`Intent.function_call`**（2 字段；旧归属：意图与插件）

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Intent.function_call.type` | str | 常用 |  |
| `Intent.function_call.functions` | list | 常用 |  |

**`Intent.nointent`**（1 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Intent.nointent.type` | - | 常用 |  |

**`Intent.intent_llm`**（3 字段；旧归属：无归宿（非选中））

| 字段 | 类型 | 层 | 备注 |
|---|---|---|---|
| `Intent.intent_llm.type` | - | 常用 |  |
| `Intent.intent_llm.llm` | - | 常用 |  |
| `Intent.intent_llm.functions` | - | 常用 |  |

### 设备（17）

设备侧：下发给设备的连接载荷（provisioning）、设备认证、hello 协商、发往设备的节奏与时区。

| 字段 | 类型 | 旧页归属 | 层 | 备注 |
|---|---|---|---|---|
| `server.websocket` | str | 服务器/连接 | 常用 | provisioning 载荷 |
| `server.timezone_offset` | int | 服务器/连接 | 更多设置 |  |
| `server.auth.enabled` | bool | 认证与设备 | 常用 |  |
| `server.auth.allowed_devices[i]` | list | 认证与设备 | 常用 |  |
| `server.mqtt_gateway` | NoneType | 服务器/连接 | 常用 | provisioning 载荷 |
| `server.mqtt_signature_key` | NoneType | 服务器/连接 | 常用 | provisioning 载荷 |
| `server.udp_gateway` | NoneType | 服务器/连接 | 常用 | provisioning 载荷 |
| `server.websocket_backup` | str | 服务器/连接 | 常用 | provisioning 载荷 |
| `server.auth_key` | str | — | 更多设置 | 危险：改 = 全部设备 token 失效 |
| `tts_audio_send_delay` | int | 服务器/连接 | 更多设置 |  |
| `xiaozhi.type` | str | — | 更多设置 | 设备协商值，通常勿改 |
| `xiaozhi.version` | int | — | 更多设置 | 设备协商值，通常勿改 |
| `xiaozhi.transport` | str | — | 更多设置 | 设备协商值，通常勿改 |
| `xiaozhi.audio_params.format` | str | — | 更多设置 | 设备协商值，通常勿改 |
| `xiaozhi.audio_params.sample_rate` | int | 语音与音频 | 更多设置 |  |
| `xiaozhi.audio_params.channels` | int | 语音与音频 | 更多设置 |  |
| `xiaozhi.audio_params.frame_duration` | int | 语音与音频 | 更多设置 |  |

### 系统（13）

编排者自身：监听地址、会话/文件生命周期、传输保活、日志、跨用途路径（多属主事实）。

| 字段 | 类型 | 旧页归属 | 层 | 备注 |
|---|---|---|---|---|
| `server.ip` | str | 服务器/连接 | 更多设置 |  |
| `server.port` | int | 服务器/连接 | 更多设置 |  |
| `server.http_port` | int | 服务器/连接 | 更多设置 |  |
| `server.vision_explain` | str | 服务器/连接 | 更多设置 |  |
| `log.log_format` | str | — | 更多设置 |  |
| `log.log_format_file` | str | — | 更多设置 |  |
| `log.log_level` | str | — | 更多设置 |  |
| `log.log_dir` | str | — | 更多设置 | 路径（多属主→系统） |
| `log.log_file` | str | — | 更多设置 |  |
| `log.data_dir` | str | — | 更多设置 | 跨用途 data 目录（多属主→系统） |
| `delete_audio` | bool | 服务器/连接 | 更多设置 |  |
| `close_connection_no_voice_time` | int | 服务器/连接 | 更多设置 |  |
| `enable_websocket_ping` | bool | 服务器/连接 | 更多设置 |  |

### 接入字段名字集（卡内分层规则的机械依据）

字段名（取路径最后一段，`[i]` 去除）命中下表 → 卡内常用；否则卡内更多设置。`type` 恒为常用（只读标识）。

`accent`, `access_key_id`, `access_key_secret`, `access_token`, `agent_id`, `api_host`, `api_key`, `api_secret`, `api_url`, `app_id`, `appid`, `appkey`, `audio_format`, `authorization`, `aux_ref_audio_paths`, `base_url`, `bot_id`, `channels`, `cluster`, `dataset_ids`, `default_location`, `default_rss_url`, `description`, `dev_pid`, `devices`, `domain`, `finance_rss_url`, `format`, `functions`, `group_id`, `host`, `inp_refs`, `is_ssl`, `language`, `llm`, `method`, `mode`, `model`, `model_dir`, `model_name`, `model_path`, `model_type`, `music_dir`, `news_sources`, `personal_access_token`, `port`, `prompt_lang`, `prompt_language`, `prompt_text`, `protocol`, `provider`, `rate`, `ref_audio`, `ref_audio_path`, `ref_text`, `refer_wav_path`, `reference_audio`, `reference_id`, `reference_text`, `region`, `resource_id`, `response_format`, `secret_id`, `secret_key`, `society_rss_url`, `speaker`, `spk_id`, `text_lang`, `text_language`, `token`, `type`, `url`, `user_id`, `voice`, `voice_id`, `world_rss_url`, `ws_url`

特记：`llm`（`Intent.intent_llm.llm`、`Memory.mem_local_short.llm`）与 `functions` 是**引用承载**（按名字引用引擎/插件），恒为常用。

### 移交注记（实现时直接引用）

1. **意图双清单**：`Intent.function_call.functions` 与 `Intent.intent_llm.functions` 两份启用清单——意图两分支各自成卡（同在「插件与工具」域），两份清单都可见，切换 `selected_module.Intent` 不再有清单消失问题。
2. **插件库**：未启用插件的参数不再折叠进 `<details>` 黑洞，与引擎库同构（全部插件卡片 + 启用清单）。
3. **设备协商值**：`xiaozhi.type/version/transport/audio_params.format` 标注「设备协商值，通常勿改」（hello 握手用设备上报值覆写）。
4. **`server.auth_key`**（改 = 全部设备 token 失效）在设备·更多设置内带危险视觉（§6）。

---

## 8. 页面 URL 与命名一致性规则

`/xiaozhi/*` 是三类使用者（浏览器 / 设备固件 / MCP）共占的单一扁平命名空间（#15 台账第一条结论）。本节规则**只约束浏览器页面**；API 路由改名不在本图（§8.4 只列问题与方向）。

### 8.1 域页 URL（本票定案）

每页恰一个规范 URL，slug 为域名的英文核心词（集合域用复数、单一天然整体用单数），全部小写：

| 页面 | 规范 URL | 说明 |
|---|---|---|
| 对话与角色 | `/xiaozhi/config/dialogue/` | 首域，`/xiaozhi/config/` 302 到这里 |
| 引擎 | `/xiaozhi/config/engine/` | 引擎库所在页 |
| 插件与工具 | `/xiaozhi/config/tools/` | |
| 设备 | `/xiaozhi/config/devices/` | 运行时面（在线设备/固件/配网）+ 摄像头入口 |
| 系统 | `/xiaozhi/config/system/` | 常用层为空，平铺 |
| 逃生口 | `/xiaozhi/config/raw/` | 只读原始配置 |
| 摄像头 | `/xiaozhi/camera/` | **保留原 URL**（书签/挂机监控），归设备域实时视图，不占一级 |

**slug 一经上线即用户契约，不再改**（这就是「翻一次就回不去」的部分，ADR-0012 记录）。

### 8.2 尾斜杠：应用层 301，不依赖 nginx

现状 slash 行为取决于从 8080 还是 8003 进入（`/xiaozhi/config` 在 8003 是 404、8080 是 nginx 补的 301；`/xiaozhi/camera` 与 `/xiaozhi/camera/` 是双注册、body 逐字节相同）——同一命名空间两套语义，根因在仓库外。**规则：所有页面 URL 以尾斜杠为规范形，由应用层注册无斜杠 → 规范形的 301**；摄像头页取消双注册，保留 `/xiaozhi/camera/` 为规范形。新页面（五域 + 逃生口）生而遵守；行为不再依赖 nginx 补救。

### 8.3 页面间导航与深链

- 所有浏览器页面同壳（§4.2），侧栏五域 + 逃生口入口；摄像头页与设备域互链（§4.4）。
- hash 深链语法见 §4.5；片段纯客户端，nginx 与 8003 直连零改动。
- 旧入口 `/xiaozhi/config/` 302 到首域（§4.1）。

### 8.4 API 命名：问题清单（只标注，不改）

#15 台账查出的冲突，实现票与未来 effort 对照：

1. `api` 挂在「页面身份」下而非「能力/资源」下——`config/api/*` 与 `camera/api/*` 两套，加第三个页面就要开第三套。
2. `config` 前缀名下混编五种时态：静态读（`full`/`meta`）、持久写（`save`）、运行态（`devices` = 当前在线设备）、磁盘状态（`firmware` = `data/bin/*.bin`）、物理副作用（`smartconfig`、`test-llm`、`firmware/upload`、`restart`）——URL 不提供副作用预警。
3. 同一领域对象（固件）两个归属相反的入口：设备调 `/xiaozhi/ota/download/{filename}`，页面调 `/xiaozhi/config/api/firmware*`，操作同一个 `data/bin/` 目录，职责还被切开（页面不能下载、设备不能列出）。
4. 页面跨前缀调设备协议路径：配置页「设备与固件」直接 POST `/xiaozhi/ota/reboot`。
5. OPTIONS 覆盖不齐：全仓只有 4 个接口有 OPTIONS（含页面从不调用的 `api/auth`），页面调用最多的 `api/meta` 反而 405——手工罗列而非按类别生成的痕迹。
6. `get_vision_url()` 生成**不带 `/xiaozhi` 前缀**的 vision 地址，而下载 URL 靠运行时字符串改写拼接——历史上真实叠出过 `/xiaozhi/xiaozhi/` 双前缀，nginx 靠 rewrite 兜底（ADR-0002 实测记录）。
7. 注册了但页面从不调用：`api/auth`（stub 放行）、`api/local-wifi`。

**未来方向（非本图约束，改名属设备/固件协议面）**：按「使用者类别」分前缀（浏览器页面 API / 设备协议 / MCP 各归其位），`api` 挂资源不挂页面；副作用用方法与子路径编码。任何 API 改名须同步仓库外 nginx，并把「nginx 在仓库外」列为外部依赖声明。

### 8.5 反代层外部依赖声明

本方案**所有页面决策都不要求 nginx 改动**（302、hash、301 均应用层/客户端）。但浏览器可达 URL 的完整契约读不出仓库：nginx 把 `/xiaozhi/v1/` 分给 8002、其余给 8003，`auth_basic $auth_gate` 只对公网生效——凡涉 API 改名或入口变更的未来 effort，必须把 `/opt/homebrew/etc/nginx/servers/apps-proxy.conf` 列为同步项。

---

## 9. 字段元信息的单一事实源

**渲染依据必须是配置树，不是静态字段清单。** #17 原型实证：按静态清单（`e.fields`/`FIELD_META`）遍历时，注入/新增的键根本不渲染——真页面 6 个孤儿字段与原型的同一缺陷同源。推论：

1. **遍历配置树渲染**：树上有什么键就渲染什么；未知键默认渲染为文本框（孤儿从此可见，不靠升级页面）。
2. **元信息与键解耦声明**：中文标签、说明、控件类型、敏感判定的元信息用**声明式元信息表**（键路径模式 → 元信息）挂在渲染器上，与配置键本身解耦——新增引擎块（如本机自加的 5 条 `Mlx*TTS`、`ThirkingLLM`）零成本获得渲染。
3. **敏感与已配置态由服务端传**：`api/full` 对敏感字段同时给出「配置里是否存在该键」的显式信号与掩码值，二者分离（§5.5）；控件判定（密钥三态）建立在服务端信号上，不靠正则猜掩码形态。
4. **默认值显式惰性**（§5.6）：显示态永远承认「这里没有值」。

---

## 10. 四条验收判据逐条自检

### 10.1 零字段丢失 ✅

- **程序对账**：527 = 15 + 450 + 32 + 17 + 13（§7 五表逐行脚本复算：15/450/32/17/13，总计 527；常用 347 / 更多 180）。
- **旧归属十组分布与 #14 §2 逐组相等**：服务器/连接 16、角色与对话 11、模型引擎 36、意图与插件 3、已启用 3、未启用折叠 20、语音与音频 6、认证与设备 3、无归宿·非选中 416、真孤儿 13（合计 527）。
- `server.auth.expire_seconds` 是树外凭空字段，不计数、不落位、不渲染（§9 规则 1 生效后自然消失）。
- 复现方法：#14 附录的叶子遍历脚本（`api/full` → `leaves()`）× §7 表 diff。

### 10.2 可盲测 ✅（十道题，侧栏出发 ≤2 次点击）

| # | 任务 | 路径 | 点击 |
|---|---|---|---|
| 1 | 改唤醒词 | 侧栏「对话与角色」→ 常用层平铺 `wakeup_words` | 1 |
| 2 | 换 TTS 引擎 | 侧栏「引擎」→ 「当前生效」TTS 下拉 | 1 |
| 3 | 看设备在不在线 | 侧栏「设备」→ 在线设备区（运行时面） | 1 |
| 4 | 调 TTS 超时 | 侧栏「引擎」→ 展开「引擎全局参数」→ `tts_timeout` | 2 |
| 5 | 改角色提示词 | 侧栏「对话与角色」→ `prompt` | 1 |
| 6 | 给设备配 Wi-Fi | 侧栏「设备」→ SmartConfig 入口 | 1 |
| 7 | 轮换设备认证密钥 | 侧栏「设备」→ 更多设置 → `server.auth_key`（危险标记） | 2 |
| 8 | 启用天气插件 | 侧栏「插件与工具」→ `functions` 启用清单（常用层） | 1 |
| 9 | 声纹加一位说话人 | 侧栏「对话与角色」→ `voiceprint.speakers`（常用层） | 1 |
| 10 | 改日志级别 | 侧栏「系统」→ `log.log_level`（常用层空、平铺） | 1 |

（深链如 `/xiaozhi/config/engine/#tts` 是额外的 0 点击直达，不计入也不替代点击判据。）

### 10.3 一级 ≤7、深度 ≤3 ✅

- 一级 = 5 域 + 逃生口 = **6 ≤ 7**。
- 深度 = 侧栏(1) → 域内卡片/类目(2) → 卡内或域内「更多设置」折叠(3) **≤ 3**；折叠态 DOM 字段数 = 0（#17 实测），「更多设置」是折叠容器不是第三层域。

### 10.4 归属由规则唯一算出 ✅

R1（多属主版，§2.1）+ R2（两问版，§2.2）+ 接入字段名字集（§7 末尾）——§7 五表即按规则机械产出，脚本可复算；任给字段，归属由这三条规则唯一确定，不需要背结果表。

---

## 11. 实现迁移顺序建议

实现是另一件票（不在本图）；以下顺序供排期参考，每阶段独立可交付、可停在任一阶段：

| 阶段 | 内容 | 为什么在这个位置 |
|---|---|---|
| **Phase 0 地基**（旧八组页面内即可交付） | 服务端显式判定「已配置/未配置」（§5.5，`api/full` 拆分信号与掩码）；渲染依据改配置树；声明式元信息表（§9）；密钥三态模型 | **必须最先**：新 IA 若建在会撒谎的显示态上，会把「已有值」归错类。两个同源显示 bug（51 个密钥误判 / 6 条注入伪装）的修复搭这班车 |
| **Phase 1 五域五页** | 页面拆分 + 共享外壳（服务端模板）+ `/xiaozhi/config/` 302 + hash 深链 + 逃生口页 + 跨页脏状态（localStorage 摘要 + beforeunload） | 地基之上第一刀；逃生口页便宜（只读 JSON 已存在） |
| **Phase 2 引擎库** | 类目 tab + 跨类目搜索 + 折叠；脏分组实时重算 + `[选择]` 组；显式惰性注入 | 依赖域页存在；#17 原型的纯模块（`isSens`/`initialState`/`computeDirty`/`effective`，34/34 断言）可直接搬 |
| **Phase 3 设备域运行时面** | 在线设备/固件管理/SmartConfig 归位；摄像头页入壳互链 + 常驻入口；意图双清单两卡、插件库同构 | 运行时面（非配置）集中一处；依赖外壳 |
| **Phase 4 危险分级** | 三级视觉重编码（三处倒置修复）+ 统一页内确认层 + 动态数字 + 重启设备动态升降级 | 确认层是外壳组件（依赖 Phase 1）；动态数字依赖设备/固件列表（Phase 3） |

---

## 12. 词汇

本文使用的已落盘词条（`CONTEXT.md` 服务端段）：**页面域**、**逃生口**、**危险分级**，以及本票新增的 **引擎库**、**脏标记**。

---

## 附录：来源

- 票据：[#14 字段盘点与归属规则](https://github.com/Naoki326/MyRobKid/issues/14) · [#15 页面与路由命名台账](https://github.com/Naoki326/MyRobKid/issues/15) · [#16 字段落位裁决](https://github.com/Naoki326/MyRobKid/issues/16) · [#17 引擎库的形态与编辑边界](https://github.com/Naoki326/MyRobKid/issues/17) · [#19 页面拓扑与共享外壳](https://github.com/Naoki326/MyRobKid/issues/19) · [#20 危险操作的分级尺度与确认档位](https://github.com/Naoki326/MyRobKid/issues/20)
- 研究交付物：[字段盘点 `docs/research/page-field-taxonomy.md`](https://github.com/Naoki326/MyRobKid/blob/research/page-field-taxonomy/docs/research/page-field-taxonomy.md) @ `research/page-field-taxonomy` · [命名台账 `docs/research/route-naming-ledger.md`](https://github.com/Naoki326/MyRobKid/blob/research/route-naming-ledger/docs/research/route-naming-ledger.md) @ `research/route-naming-ledger` · [落位映射 `docs/decisions/field-placement-map.md`](https://github.com/Naoki326/MyRobKid/blob/decide/field-placement/docs/decisions/field-placement-map.md) @ `decide/field-placement`
- 决策记录：ADR-0012（分类轴与五页面域）
- 原型：`tmp/prototype-engine-library/index.html`（gitignored，本地工件，未入库）——引擎库状态模型 34/34 断言
- 相关 ADR：ADR-0002（OTA 寻址三处一致与升级陷阱——上传固件的「武装链」语义来源）
