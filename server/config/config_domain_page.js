/* ============================================================================
 * config_domain_page.js — 域页的渲染与编辑逻辑（issue #26/#27，父 spec §4/§5/§7/§9）
 *
 * 这一份脚本被五个域页共用（正文不同、骨架同一副）。它做五件事：
 *
 *   1. **按配置树渲染**：值从 ``api/full`` 的 ``config`` 拉，渲染依据是配置树
 *      （§9 规则 1）——域表决定「字段怎么分层」，配置树决定「键存不存在」，
 *      两者各管一段，谁也不替谁撒谎；
 *   2. **域内两层**（§2.5）：常用平铺、更多设置折进 ``<details>``——**折叠态 DOM
 *      字段数 = 0**，这是「深度 ≤3」成立的依据；
 *   3. **组件库**（§5 / §7，engine 与 tools 两域）：上方「当前生效」选择器，
 *      下方「全部…」按组轴 tab × **跨组搜索** × 折叠展开。引擎库（六类目）
 *      与插件库（单组，无 tab 栏）**共用同一份实现**，差异参数化在 spec 里——
 *      §7 移交注记 2 说的「同构」在代码上就是这句话；
 *   4. **脏计算复用状态模型**：``computeDirty`` / ``groupDirty`` /
 *      ``dirtySummary`` / ``domainDirty`` 全来自 config_state_model.js（#25 的
 *      地基）。页面里**没有第二套脏计算**——这是 #17 那两个同源显示 bug 的教训；
 *   5. **跨页脏状态**（§4.6）：把本域脏摘要写进 ``localStorage['config-dirty/<域>']``
 *      （**只含路径与计数、不含任何值**），侧栏读它画点；带脏离开先确认
 *      （``window.__xzhDirtyGuard`` 钩子，壳负责接事件）。
 *
 * 密钥三态（§5.5）沿用 #25 的 ``setSecretInput``：输入**不落配置树**，只进
 * 密钥槽；留空 = 保持原值（不产生任何改动）。
 * ========================================================================== */

import {
  ENGINE_CATEGORIES as ENGINE_CATEGORIES_FALLBACK,
  INTENT_BRANCHES as INTENT_BRANCHES_FALLBACK,
  computeDirty, dirtySummary, domainDirty, groupDirty, dirtyLabel,
  getPath, initialState, isSensitiveKey, placeholderText, setPath,
  setSecretInput, toolsScope,
} from './config_state_model.js';
import {
  CONFIRM_MODE, LEVEL, LEVEL_VISUALS, confirmButtonText, levelOf,
  needsVersionTyping, operationConsequences,
} from './config_danger_model.js';

const SCHEMA = JSON.parse(document.getElementById('domain-schema').textContent);
const SLUG = SCHEMA.slug;
const STORAGE_KEY = 'config-dirty/' + SLUG;

/* 域表里 list 字段写的是**元素形态**（§7 的计数口径：``wakeup_words`` 与
 * ``devices[i]`` 各记 1 个字段），但页面控件要的是**数组本身**的路径：
 * ``devices[i]`` 这个点号路径解不出来，硬渲染就是一个永远空白的输入框——
 * 「可编辑」当场失效，而且看不出是哪里错。
 *
 * 所以在入口处归一一次：``X[i]`` → ``X``，控件形状定为 ``list``。这样 §7 表的
 * 写法与 ``api/full`` 的 ``config_state`` 信号路径（点号形态、数组用下标
 * ``devices.0``）各归其位，两者不再相互委屈。
 *
 * 归一放在**入口**而不是每个消费点：字段路径被控件、脏前缀、域归属三处消费，
 * 每处各解一次就是三把尺子。
 */
for (const g of SCHEMA.groups) {
  for (const f of g.fields) {
    if (!f.path.includes('[i]')) continue;
    f.path = f.path.replace(/\[i\]$/, '');
    if (f.kind === 'list' || f.kind === 'text' || f.kind === 'auto') f.kind = 'list';
  }
}

/** 引擎域类目（六族）——**单一事实源是服务端注入的域表**。
 *
 * ``classifyDirty`` 靠它把「真在引擎域里的路径」与「域内散字段」分开：只有首段
 * 真在类目集合里（``TTS.MlxTTS.url``），才谈得上「选中 / 未选中」。
 *
 * 为什么从注入的域表读、不在这里写第三份：类目清单一旦分叉，后果是「某类目下
 * 改了字段却归错组」——静默、只表现在脏分组上、极难归因。服务端
 * ``page_domains.ENGINE_CATEGORIES`` 是那一份，域页与 ``api/full`` 都从它派生。
 * 域表缺这个键（旧骨架 / 非引擎域）时回落成状态模型里的同一份常量。
 * 当前三处注入（已上线域 / 占位域 / raw 页）均已覆盖，此回落是空层防御。
 */
const ENGINE_CATEGORIES = SCHEMA.engine_categories || ENGINE_CATEGORIES_FALLBACK;

/** 引擎库（§5）只在引擎域上渲染。判据是**域 slug**，不是「有没有 lib 容器」。 */
const IS_ENGINE_DOMAIN = SLUG === 'engine';

/** 意图分支清单（`Intent.*` 的键）——**单一事实源是服务端注入的域表**。
 *
 * 理由与 ``ENGINE_CATEGORIES`` 逐字同源：分支清单一旦分叉，「切换意图引擎」的
 * 候选就会少一条，而少的那条恰好是本机没配的那条（`nointent`），表现为
 * 「切过去之后就切不回来了」。唯一的一份在 ``page_domains.INTENT_BRANCHES``。
 */
const INTENT_BRANCHES = SCHEMA.intent_branches || INTENT_BRANCHES_FALLBACK;

/** 插件与工具域（§7）——插件库与引擎库**同构**（§5.1 / §7 移交注记 2）。
 *
 * 形态差一处：引擎按类目 tab 切六块，插件没有天然的组轴（七个插件就是七个），
 * 所以**无 tab 栏**；搜索与真折叠逐条相同（AC 明写「同构」）。
 */
const IS_TOOLS_DOMAIN = SLUG === 'tools';

/** 设备域（#29，父 spec §4.4 / §7）。
 *
 * 它同时有两层东西：17 个配置字段（provisioning / 认证 / hello 协商 / 节奏）
 * 与**运行时面**（在线设备 / 固件库 / SmartConfig）+ 常驻摄像头入口。
 * 运行时面不是可保存的字段，所以它们不在域表 ``groups`` 里，而在服务端注入的
 * ``runtime_panels`` 里——单一事实源在 ``page_domains.DEVICES_RUNTIME``。
 */
const IS_DEVICES_DOMAIN = SLUG === 'devices';

/** 摄像头入口（§4.4）——**单一事实源在服务端注入的域表**（壳的 `CAMERA_PAGE`）。
 *
 * 为什么不在这里写字面量：文案或路径在壳与页面各存一份就会分叉，而分叉的
 * 表现是一个错别字或者一条死链——没有测试会红。设备域的入口也是**常驻**的：
 * 它不依赖在线设备列表（设备离线也在），这是 AC 明写的一条。
 */
const CAMERA = SCHEMA.camera || null;

/** 本域的运行时面面板（非配置字段）。非设备域注入的是空数组。 */
const RUNTIME_PANELS = SCHEMA.runtime_panels || [];

/** 配置里真实存在的值（脏计算的基准）+ 服务端存在信号。 */
let PAGE = null;
let CONFIG = null;
let CONFIG_STATE = {};
/** 页面编辑值（普通字段）。 */
let state = {};
/** 密钥输入槽（路径 → 用户敲进去的新值；空串 = 保持原值）。 */
const SECRET_INPUT = {};
/** 当前脏条目（computeDirty 的结果）。 */
let DIRTY = {};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* 点号语义的 getPath / setPath 一律从状态模型 import —— 页面**不另写一份**。
 * 理由与脏计算同源：路径口径一旦分叉，``context_providers.0.url`` 在一边解得开、
 * 在另一边解不开，服务端的 ``config_state`` 信号就会“给了但查不到”。
 */

/* ---------------------------------------------------------------------------
 * 域表 → 字段 → 域归属（脏分桶用）
 * ------------------------------------------------------------------------ */

/** 域表里所有字段路径（含 list 元素路径的展开前缀）。 */
const FIELD_PATHS = [];
for (const g of SCHEMA.groups) for (const f of g.fields) FIELD_PATHS.push(f.path);

/** 路径属于本域吗。
 *
 * 判据两条：命中域表里的某个字段路径（精确或前缀），或命中该字段的直属父级
 * （``context_providers`` 这类 list 记 1 个字段，但元素键是 ``context_providers.0.x``）。
 */
function belongsToDomain(path) {
  for (const p of FIELD_PATHS) {
    if (path === p) return true;
    if (path.startsWith(p + '.')) return true;
    // list 字段：``wakeup_words.0`` 归 ``wakeup_words``。
    if (/^\d+$/.test(path.slice(p.length + 1).split('.')[0] || '')
        && path.startsWith(p + '.0')) return true;
  }
  // 分组容器本身（数组长度的增删）也归本域。
  return FIELD_PATHS.some((p) => p.startsWith(path + '.'));
}

/* ---------------------------------------------------------------------------
 * 控件渲染
 * ------------------------------------------------------------------------ */

/** 声明式元信息表（键名 → 中文标签 / 说明）——与配置键**解耦**（§9 规则 2）。
 *
 * 引擎卡的字段标签按**末段键名**查这张表：表里没有的键直接显键名
 * （`top_k` 就是 `top_k`，不是「未知字段」）。新增引擎（本机自加的 `Mlx*TTS`）
 * 零成本获得中文标签——不靠给每条引擎写一段。
 *
 * 与旧页面 `config_page.html` 的 `FIELD_META` 同源同尺：这里只留引擎卡真正
 * 用得上的那一小张（控件形状由值决定，不需要在表里声明）。
 */
const FIELD_LABEL = {
  type: '类型', model_name: '模型名', model: '模型名',
  base_url: '接口地址', url: '接口地址', api_url: '接口地址',
  ws_url: 'WebSocket 地址', output_dir: '输出目录',
  voice: '音色', voice_id: '音色 ID', speaker: '音色',
  speed: '语速', temperature: '温度', max_tokens: '最大 token 数',
  top_p: 'top_p', top_k: 'top_k', appid: '应用 ID', app_id: '应用 ID',
  appkey: 'AppKey', cluster: '集群', region: '区域', language: '语言',
  tts_timeout: 'TTS 超时(秒)', model_dir: '模型目录',
  split_sentences: '逐句合成', enable_multilingual: '多语言',
  end_window_size: '断句窗口(ms)', response_format: '响应格式',
  sample_rate: '采样率', channels: '声道数', frame_duration: '帧时长(ms)',
  ref_audio: '参考音频路径', ref_text: '参考音频文本',
  reasoning_effort: '推理级别', threshold: '阈值',
  threshold_low: '低阈值', min_silence_duration_ms: '最短静音(ms)',
  api_key: 'API 密钥', access_token: 'access token', token: 'token',
  secret_id: 'secret ID', secret_key: 'secret key',
  access_key_id: 'access key ID', access_key_secret: 'access key secret',
  provider: '提供方', enable_user_profile: '启用用户画像',
  openai_base_url: 'OpenAI base URL', is_ssl: '启用 SSL',
  host: '主机', port: '端口', model_path: '模型路径',
  model_type: '模型类型', dev_pid: 'dev_pid',
  enable_lid: '启用语种识别', enable_itn: '启用文本规范',
  is_no_prompt: '不使用本地 prompt', ali_memory_id: '百练 memory ID',
  mode: '模式', bot_id: 'bot ID', user_id: 'user ID',
  agent_id: 'agent ID', personal_access_token: '个人访问令牌',
  http_proxy: 'HTTP 代理', https_proxy: 'HTTPS 代理',
  resource_id: '资源 ID', enable_ws_reuse: '复用 WebSocket',
  reference_id: '参考音色 ID', reference_audio: '参考音频',
  reference_text: '参考文本', top_p_2: 'top_p',
  protocol: '协议', spk_id: '说话人 ID', save_path: '保存路径',
  audio_format: '音频格式', api_secret: 'API secret',
  format: '格式', method: '请求方法', authorization: '鉴权头',
};

/** 字段行的标签：中文表优先，回落到**相对路径**（嵌套字段的区分只会来自路径）。 */
function fieldLabel(field) {
  const bare = field.path.split('.').pop();
  const named = FIELD_LABEL[bare];
  if (!named) return field.label;
  // 嵌套字段（``llm.config.api_key``）用「路径 · 中文」：只给中文会两条长得一样。
  return field.label === bare ? named : `${field.label} · ${named}`;
}

/** 一个字段的控件。密钥走三态（服务端信号判「已配置」，绝不猜掩码形态）。
 *
 * ``f.kind`` 是**表里声明的形状**；但表只列了「方案里裁决过」的字段，树上还有
 * 表外的键（本机自加引擎的新字段、上游新增的键）。表外字段的 ``kind`` 由
 * ``kindFor`` 按**运行时值**推——形状跟值走，不跟表走（§9 规则 1）。
 */
function control(f) {
  const val = getPath(state, f.path);
  const kind = f.kind === 'auto' ? kindFor(val) : f.kind;
  // 空 object：显式说「这里是个空块」，不给控件（没有可编的值）。
  // 它的存在本身是信息：§7 按「空 object 记 1 个字段」计数，页面数得出来。
  if (kind === 'emptyobj') {
    return '<span class="hint">（空配置块）</span>';
  }
  if (kind === 'list') {
    const items = Array.isArray(val) ? val : [];
    return `<div class="keylist">${items.map((it, i) =>
      `<div class="krow"><input type="text" data-list="${esc(f.path)}" data-idx="${i}"
        value="${esc(it)}"><button class="del" data-del="${esc(f.path)}"
        data-idx="${i}" type="button">✕</button></div>`).join('')}
      <button class="addbtn" data-add="${esc(f.path)}" type="button">+ 添加一项</button></div>`;
  }
  if (isSensitiveKey(f.path)) {
    const sig = CONFIG_STATE[f.path];
    const configured = sig ? !!sig.configured
      : (val !== undefined && val !== null && val !== '');
    const slot = SECRET_INPUT[f.path] || '';
    const badge = configured
      ? '<span class="badge" style="background:rgba(52,211,153,.15);color:var(--ok)">已配置</span>'
      : '<span class="badge" style="background:rgba(139,152,179,.15);color:var(--dim)">未配置</span>';
    const replacing = slot !== ''
      ? '<span class="badge secret-replace-mark" style="background:rgba(251,191,36,.18);color:var(--warn)">将替换</span>' : '';
    return `${badge}${replacing}<input type="password" data-path="${esc(f.path)}"
      data-sens="1" value="${esc(slot)}" autocomplete="new-password"
      placeholder="${configured ? '已配置 · 留空不变，输入新值覆盖' : '未配置 · 输入新值'}">`;
  }
  // 下拉：候选来自**注入的域表**（意图分支）而不是页面里的字面量。
  // 写错一个名字 = 意图引擎静默失效，所以只能是下拉（与引擎页六行同理）。
  if (kind === 'select') {
    const choices = f.options || [];
    const known = choices.includes(val);
    const extra = (val !== undefined && val !== null && val !== '' && !known)
      ? `<option value="${esc(val)}" selected>${esc(val)}（未配置）</option>` : '';
    const opts = choices.map((c) =>
      `<option value="${esc(c)}" ${c === val ? 'selected' : ''}>${esc(c)}</option>`).join('');
    const d = DIRTY[f.path];
    // 选择器控件统一用 ``data-selcat``（引擎页六行与意图分支同一把尺），
    // 它不是普通文本输入：选了就走 setPath + markChanged，与 ``data-path`` 同一条路。
    return `<div class="slot"><div class="selc"><select data-selcat="${esc(f.selcat || f.path)}">`
      + opts + extra + '</select></div>'
      + (d ? `<span class="badge" style="background:rgba(124,92,255,.18);color:#a78bfa">[选择] 改自 ${esc(d.from ?? '（无）')}</span>` : '')
      + '</div>';
  }
  if (kind === 'bool') {
    const on = val === true;
    return `<label class="switch-row"><span class="switch"><input type="checkbox"
      data-path="${esc(f.path)}" ${on ? 'checked' : ''}><span class="sl"></span></span>
      <span class="sw-label">${on ? '开启' : '关闭'}</span></label>`;
  }
  if (kind === 'number') {
    const v = (val === undefined || val === null) ? '' : val;
    return `<input type="number" data-path="${esc(f.path)}" value="${esc(v)}"
      ${f.step ? `step="${esc(f.step)}"` : ''}>`;
  }
  const ph = (val === undefined || val === null || val === '')
    ? placeholderText(CONFIG || {}, f.path) : '';
  if (kind === 'textarea') {
    return `<textarea data-path="${esc(f.path)}" rows="6"
      placeholder="${esc(ph)}">${esc(val ?? '')}</textarea>`;
  }
  return `<input type="text" data-path="${esc(f.path)}" value="${esc(val ?? '')}"
    placeholder="${esc(ph)}">`;
}

/** 表外字段的控件形状：按**运行时值**推。
 *
 * 表只列方案裁决过的字段；树上还有表外的键（本机自加引擎、上游新增）。
 * 形状跟值走的好处是「新增引擎零成本获得渲染」（§9 规则 2）：把 Mlx*TTS 的
 * speed 从 0.85 改成 true，下次就渲染成开关——不靠改表。
 */
function kindFor(val) {
  if (Array.isArray(val)) return 'list';
  if (typeof val === 'boolean') return 'bool';
  if (typeof val === 'number') return 'number';
  if (typeof val === 'string' && val.length > 120) return 'textarea';
  return 'text';
}

function row(f) {
  const dirty = DIRTY[f.path];
  // 字段级危险标注（§7 移交注记 4）：它与**操作**的三级分级（§6.2）是两根轴
  // ——本条只说「这个输入框旁边要提醒一句话」，不参与分级、不触发确认层
  // （三级判定在 config_danger_model.js）。路径不在页面里硬编码：
  // 声明在表上，视觉在页面上——两处不要同一件事。
  const danger = f.danger
    ? `<div class="danger-note">⚠️ ${esc(f.danger_note || '危险操作，请确认后再改')}</div>`
    : '';
  return `<div class="row${dirty ? ' dirty' : ''}${f.danger ? ' danger-row' : ''}">
    <div class="meta"><div class="label">${esc(fieldLabel(f))}</div>
      ${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ''}</div>
    <div class="ctrl">${control(f)}${danger}</div>
  </div>`;
}

/** 域内一个分组卡片。``id`` 是 hash 深链的锚点（§4.5）。
 *
 * 「更多设置」折叠的**开合判据是域内常用层是否为空**，不是「这个分组有没有
 * more 字段」（父 spec §2.5：*域内常用层为空时，折叠区不渲染，全部字段平铺*）。
 * 系统域就是这条规则的实例：它 13 个字段全是 more，若还折一层，整页只有一个
 * 空壳，字段一个也看不见。
 */
function groupCard(g, domainHasCommon, transform) {
  // ``transform``：个别分组需要在渲染前改写字段（如意图选择器要补
  // ``options``）。默认为恒等——**不允许**为了这一个特例另写一张分组卡：
  // 「更多设置」的折叠口径只许有一份（两把尺子的老毛病）。
  const fields = transform ? transform(g.fields) : g.fields;
  const common = fields.filter((f) => f.layer === 'common');
  const more = fields.filter((f) => f.layer === 'more');
  // 常用层为空 → 平铺（把所有字段当 common 渲染，不生成折叠容器）。
  const fold = domainHasCommon && more.length > 0;
  const flat = domainHasCommon ? common : fields;
  const moreBlock = fold
    ? `<details class="more-settings"><summary>更多设置 · ${more.length} 项</summary>
        <div>${more.map(row).join('')}</div></details>`
    : '';
  return `<section class="group" id="${esc(g.id)}">
    <h2>${esc(g.title)}</h2>
    ${g.desc ? `<div class="desc">${esc(g.desc)}</div>` : ''}
    ${flat.map(row).join('')}
    ${moreBlock}
  </section>`;
}

/* ---------------------------------------------------------------------------
 * 引擎库（§5，仅 engine 域）
 *
 * 承载形态：类目 tab + **跨类目搜索** + ``<details>`` 真折叠。三条都是裁决，
 * 不是实现口味：
 *
 *   - **类目 tab** 把 450 字段切成六块（不分页——本地单机面板，分页只增加
 *     点击数；不另开路由——68 条路由的维护成本不划算）；
 *   - **搜索跳类目**（§5.1）：在 LLM 下搜 ``mlx`` 得 0 条，而 TTS 实有 5 条
 *     ——这是「找不到」痛点的直接成因。命中项必须带**类目标签**，
 *     否则搜出来的 TTS 引擎会被当成 LLM 的；
 *   - **折叠是真折叠**（§2.5）：「深度 ≤3」成立的依据是折叠态 DOM 字段数 = 0。
 *     这一点决定了引擎体必须按需构建（``details`` 的 ``toggle`` 时才填），
 *     而不是先渲染再藏。
 * ------------------------------------------------------------------------ */

/** 表：**组件根路径** → [{path, layer}]（方案裁决过的字段）。
 *
 * 组件（引擎 / 插件 / 意图分支）的字段在域表里是一条条**完整路径**：
 *   - 引擎：``<类目>.<引擎名>.<字段>``     → 根 ``<类目>.<引擎名>``
 *   - 插件：``plugins.<插件名>.<字段>``     → 根 ``plugins.<插件名>``
 *   - 分支：``Intent.<分支>.<字段>``       → 根 ``Intent.<分支>``
 *
 * 组件名**不靠这张表枚举**（见下），表只回答「这条组件的字段怎么分层」。
 * 存的是一条**完整路径**而不是「末段」：嵌套字段（``llm.config.api_key``）
 * 的末段（``api_key``）在同一个组件里可能出现在两个嵌套层下，只留末段会分不清。
 *
 * **一张表服务两个库**：``fieldsOf(root)`` 只认根路径，引擎卡与插件卡
 * 走的是同一个函数——这就是「同构」在代码上的落点。
 */
const DECLARED_FIELDS = {};
for (const g of SCHEMA.groups) {
  for (const f of g.fields) {
    const root = componentRootOf(f.path);
    if (!root) continue;
    (DECLARED_FIELDS[root] = DECLARED_FIELDS[root] || []).push({ path: f.path, layer: f.layer });
  }
}

/** 一条组件字段路径的**组件根**（引擎 ``CAT.Name`` / 插件 ``plugins.Name`` /
 *  意图分支 ``Intent.Branch``）；不属于任何组件时返回 null。
 *
 * 判据是**路径形状 + 法定的组件首段**，不是「三段就算组件」：
 * ``tool_call_timeout``（一段）、``mcp_endpoint``（一段）、
 * ``selected_module.Intent``（两段，是选择器不是组件）都不属于组件，
 * 它们由域内普通分组渲染。
 */
function componentRootOf(path) {
  const parts = String(path).split('.');
  if (parts.length < 3) return null;
  if (parts[0] === 'plugins' || parts[0] === 'Intent') return parts[0] + '.' + parts[1];
  if (ENGINE_CATEGORIES.includes(parts[0])) return parts[0] + '.' + parts[1];
  return null;
}

/** 引擎域的组件根清单（``<类目>.<引擎名>``），供枚举类目的引擎名用。
 *
 * 引擎域的组件根形如 ``CAT.Name``；这里只取一遍作为名字回落来源。
 */
const ENTRY_FIELDS = {};
for (const [root, fields] of Object.entries(DECLARED_FIELDS)) {
  if (ENGINE_CATEGORIES.includes(root.split('.')[0])) ENTRY_FIELDS[root] = fields;
}

/** 一条组件（引擎 / 插件 / 意图分支）的字段清单：**表里的层 + 树上的键**
 * （§9 规则 1）。
 *
 * 两个来源各管一段：表说「这个键属于常用还是更多设置」，树说「这个键存不存在」。
 * 树上有而表里没列的键（本机自加引擎的新字段、上游新增的键）照样渲染——
 * 控件形状由 ``kindFor`` 按值推。反之表里有而树上没有的键**不渲染**：
 * 组件库必须看见的是「这个组件实际装成了什么样」，不是「方案列过什么」。
 *
 * 嵌套块（``Memory.powermem.llm.config.api_key``、``TTS.CustomTTS.params.speed``）：
 * 表里的路径是全路径，这里按「**表层里最长前缀 + 剩余段**」把它展平——
 * 展平是必须的，不然 ``powermem`` 的 11 个字段一个都看不见，而「450 全量可达」
 * 的验收就只剩一句口号。
 *
 * ``root`` 是组件根路径（``LLM.ThirkingLLM`` / ``plugins.get_weather`` /
 * ``Intent.intent_llm``）——引擎卡与插件卡共用一个实现，这就是同构。
 */
function fieldsOf(root) {
  const declared = DECLARED_FIELDS[root] || [];
  const owner = getPath(state, root);
  const out = [];
  if (owner && typeof owner === 'object' && !Array.isArray(owner)) {
    collectFields(owner, root, root, declared, out);
  }
  return out;
}

/** 把一条引擎的子树展平成字段行（嵌套块不算一层——卡的深度不随配置变深）。
 *
 * 展平后的标签用**相对引擎根的路径**（``llm.config.api_key``）而不是末段：
 * ``powermem`` 里有两个 ``api_key``（llm 与 embedder 各一），只显示末段就是
 * 「两个长得一模一样的 api_key」——改错一个看不出来。路径标签是唯一能区分它们
 * 的东西，而且它与收起来的那层是同一串名字。
 */
function collectFields(node, prefix, root, declared, out) {
  for (const [k, v] of Object.entries(node)) {
    const path = `${prefix}.${k}`;
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      if (Object.keys(v).length > 0) {
        collectFields(v, path, root, declared, out);
        continue;
      }
      // 空 object：§7 口径把「空 object 记 1 个字段」（`Memory.powermem.vector_store.config`
      // 就是现场）。不展子键也不丢弃——它要留一个可见的行，否则 450 那个数
      // 在页面上对不上，而且「这里有个空块」这件事被静默了。
    }
    // 层：优先用表里的声明（支持嵌套路径）；表里没有的按「更多设置」——
    // 表外字段不靠猜就是安全的：扫进折叠区比扫进常用区少一层「越权」。
    const declaredRow = declared.find((d) => d.path === path);
    out.push({
      path,
      tail: path.slice(root.length + 1),
      label: path.slice(root.length + 1),
      kind: (v !== null && typeof v === 'object' && !Array.isArray(v)) ? 'emptyobj' : 'auto',
      layer: declaredRow ? declaredRow.layer : 'more',
    });
  }
}

/** 一条组件的卡片（``<details>``）——**引擎库与插件库共用**。
 *
 * 折叠态 DOM 字段数 = 0：体在 ``toggle`` 时构建，初始展开的（当前生效那条）
 * 才立即构建。这不是优化，是「深度 ≤3」验收判据的实现前提。
 *
 * ``root`` 是组件根路径（``LLM.ThirkingLLM`` / ``plugins.get_weather``）；
 * ``opts`` 里的差异只有两处：``fieldAttr`` 命名（引擎 ``data-engine`` /
 * 插件 ``data-plugin``，各自是 hash 深链的锚点）与类目标签。其余逐字相同。
 */
function componentCard(root, opts) {
  opts = opts || {};
  const parts = root.split('.');
  const group = parts[0];
  const name = parts.slice(1).join('.');
  const live = opts.live === undefined ? false : opts.live;
  const owner = getPath(state, root) || {};
  const fields = fieldsOf(root);
  const componentDirty = Object.keys(DIRTY).some(
    (p) => p === root || p.startsWith(root + '.'));
  const catPill = opts.tag
    ? `<span class="catpill" data-cat="${esc(opts.tag)}">${esc(opts.tag)}</span>` : '';
  const id = `${opts.idPrefix}-${group}-${name}`;
  // 两个锚点属性：``data-engine`` / ``data-plugin`` 是**域特定的深链锚点**
  // （§4.5 只承诺这一类），``data-lib-root`` 是**两个库共用的**根路径锚点
  // ——重建时保持折叠状态、脏前缀匹配这类跨库逻辑只认后者，不必知道
  // 当前是哪个域（写两套选择器就是两把尺子）。
  return `<details class="engine${live ? ' live' : ''}${componentDirty ? ' dirty' : ''}"
    id="${esc(id)}" ${opts.fieldAttr}="${esc(root)}" data-lib-root="${esc(root)}"
    ${live ? 'open' : ''}>
    <summary>${catPill}<span class="nm">${esc(name)}</span>
      <span class="ty">${esc(owner.type ?? '?')}</span>
      <span class="nf">${fields.length} 字段</span>
      ${live ? '<span class="live-tag">● ' + esc(opts.liveLabel || '生效中') + '</span>'
        : `<span class="live-tag" hidden>● ${esc(opts.liveLabel || '生效中')}</span>`}
      <span class="dirtydot" ${componentDirty ? '' : 'hidden'}></span></summary>
    <div class="engbody" data-built="0" data-root="${esc(root)}"></div>
  </details>`;
}

/** 引擎域的一条引擎卡（``cat.name``，当前选中则展开并标「生效中」）。 */
function engineCard(cat, name, opts) {
  opts = opts || {};
  return componentCard(cat + '.' + name, {
    idPrefix: 'eng', fieldAttr: 'data-engine',
    tag: opts.showCat ? cat : '',
    live: getPath(state, 'selected_module.' + cat) === name,
    liveLabel: '生效中',
  });
}

/** 填一个组件体的控件（延迟到 ``toggle``）。 */
function fillEngineBody(box) {
  if (box.dataset.built === '1') return;
  const fields = fieldsOf(box.dataset.root);
  const common = fields.filter((f) => f.layer !== 'more');
  const more = fields.filter((f) => f.layer === 'more');
  const moreBlock = more.length
    ? `<details class="more-settings"><summary>更多设置 · ${more.length} 项</summary>
        <div>${more.map(row).join('')}</div></details>`
    : '';
  box.innerHTML = (common.length ? common.map(row).join('')
    : (more.length ? '' : '<div class="hint">这条组件在配置里没有字段。</div>'))
    + moreBlock;
  box.dataset.built = '1';
  bindInputs(box);
}

/** 当前类目（tab 态），以及搜索词（跨类目态）。 */
let CUR_CAT = null;
let QUERY = '';

/** 引擎名清单直接**从配置树与 selected_module.* 读**，不是从表里读。
 *
 * 这是本票最要紧的一件事：「全部引擎可达」的「全部」不是「方案里列过的 68 条」
 * ——本机自加的 `Mlx*TTS` 在模板里根本没有条目，只能从树上发现；而
 * `selected_module.Memory = nomem` 指到一条树上不存在的引擎，也只能从选择器上
 * 发现（否则「当前生效」会显示一个点不进去的名字）。两个来源的并集才是
 * 「可达的全部」。
 */
function enginesOf(cat) {
  const names = [];
  const push = (n) => { if (n && !names.includes(n)) names.push(n); };
  const tree = getPath(state, cat);
  if (tree && typeof tree === 'object' && !Array.isArray(tree)) {
    for (const [k, v] of Object.entries(tree)) {
      if (v && typeof v === 'object' && !Array.isArray(v)) push(k);
    }
  }
  push(getPath(state, 'selected_module.' + cat));
  // 表里列过的名字排在后面补上：它们在本机配置里可能根本没装（如 Memory.nomem），
  // 而「当前生效」必须能选到它们。
  for (const key of Object.keys(ENTRY_FIELDS)) {
    const [c, n] = key.split('.');
    if (c === cat) push(n);
  }
  return names;
}

/** 重建引擎库的**外框**（tab 栏 + 当前卡列表）。卡体是按需填充的。
 *
 * 上方「当前生效」与引擎全局参数由引擎域自己给；库本身是共用的（见下）。
 */
function renderEngineLibrary() {
  // ---- 上方：当前生效六行（selected_module.* 的编辑控件，§5） ----
  const selectors = SCHEMA.groups.find((g) => g.id === 'selectors');
  const globals = SCHEMA.groups.find((g) => g.id === 'globals');
  const slots = selectors
    ? `<section class="group" id="selectors"><h2>${esc(selectors.title)}</h2>
        <div class="desc">${esc(selectors.desc)}</div>
        ${ENGINE_CATEGORIES.map((cat) => slotRow(cat)).join('')}</section>`
    : '';
  const globalsBlock = globals
    ? `<details class="group asdetails" id="globals"><summary>${esc(globals.title)}
        · ${globals.fields.length} 项</summary>
        <div class="desc">${esc(globals.desc)}</div>
        ${globals.fields.map(row).join('')}</details>`
    : '';
  renderLibrary(ENGINE_LIBRARY, slots + globalsBlock);
}

/* ---------------------------------------------------------------------------
 * 组件库（引擎库 / 插件库共用）
 *
 * 父 spec §5.1 给引擎库定的承载形态（**类目 tab + 跨类目搜索 + ``<details>``
 * 真折叠**）与 §7 移交注记 2 给插件库定的「与引擎库同构」，指的是**同一套**
 * 机制。两个域各抄一份实现就是第二把尺子：搜索的跨度、折叠的时机、命中项的
 * 标签会在两边慢慢长不一样，而用户看到的是「同一个库两种脾气」。
 *
 * 差异参数化在 ``spec`` 里，只有三处：
 *
 *   - **组轴**：引擎按类目切六块（有 tab 栏）；插件没有天然的组轴
 *     （七个插件就是七个），故无 tab 栏、单组全列；
 *   - **锚点属性**：``data-engine`` / ``data-plugin``（各自是 hash 深链锚点）；
 *   - **文案**：名词与搜索框提示。
 *
 * 搜索**必须跨组**（§5.1）：类目内搜索在 LLM 下搜 ``mlx`` 得 0 条，而 TTS
 * 实有 5 条——这是「找不到」痛点的直接成因。
 * ------------------------------------------------------------------------ */

/** 引擎库的 spec。 */
const ENGINE_LIBRARY = {
  id: 'all-engines',
  title: '📚 全部引擎',
  desc: '按类目浏览，或搜索（搜索**跨全部类目**，命中项带类目标签）。',
  noun: '引擎',
  groups: ENGINE_CATEGORIES,
  searchId: 'engSearch',
  searchPlaceholder: '🔍 搜索引擎名或 type…（例：mlx、doubao、openai）',
  catLabel: '类目',
  namesOf: (cat) => enginesOf(cat),
  rootOf: (cat, name) => `${cat}.${name}`,
  typeOf: (cat, name) => getPath(state, `${cat}.${name}.type`),
  cardOf: (cat, name, opts) => engineCard(cat, name, opts),
  tagOf: (cat) => cat,
  emptyText: '这个类目下没有引擎。',
  missingText: (n) => `无匹配引擎（全部 ${n} 条引擎里都找不到）`,
};

/** 插件库的 spec——与引擎库**共用** ``renderLibrary``。
 *
 * 与引擎库唯一的形状差异是**没有组轴**（``groups: [PLUGIN_GROUP]`` 单组，
 * ``renderLibrary`` 据此不画 tab 栏）：插件名之间没有类目那样的天然分块，
 * 硬造一个「插件类目」就是凭空多一层。搜索、真折叠、卡内两层、脏标记
 * 逐条相同。
 *
 * 插件名清单**从配置树读**（与引擎名清单同理）：``api/full`` 给的是模板与
 * 用户配置的**合并树**，所以模板里列过的七个插件都在——包括本机没启用的
 * 六个。它们的参数不再折进 ``<details>`` 黑洞（本票的 AC）。
 */
const PLUGIN_GROUP = 'plugins';
const PLUGIN_LIBRARY = {
  id: 'all-plugins',
  title: '🧩 全部插件',
  desc: '每个插件的接入参数。未启用的插件同样可见、可编、可保存'
    + '（与引擎库同构：搜索 + 真折叠，不折进黑洞）。',
  noun: '插件',
  groups: [PLUGIN_GROUP],
  searchId: 'plugSearch',
  searchPlaceholder: '🔍 搜索插件名…（例：weather、news、music）',
  catLabel: '组',
  namesOf: () => pluginNames(),
  rootOf: (cat, name) => `plugins.${name}`,
  typeOf: (cat, name) => getPath(state, `plugins.${name}.provider`),
  cardOf: (cat, name, opts) => pluginCard(name, opts),
  tagOf: () => '',
  emptyText: '配置里没有插件。',
  missingText: (n) => `无匹配插件（在全部 ${n} 个插件里都找不到）`,
};

/** 插件名清单：**从配置树读**（与引擎库的「从树读」同理，§9 规则 1）。
 *
 * 不从域表里的 ``plugins.*`` 字段枚举反推：域表只说「这些字段怎么分层」，
 * 配置树才说「装了哪些插件」。一个在树上存在、域表里没列过的插件（上游新增）
 * 必须照样可达——这与本机自加的 ``Mlx*TTS`` 是同一条规则。
 *
 * 排序按插件名（配置树键序），让两张库的卡片顺序稳定。
 */
function pluginNames() {
  const names = [];
  const push = (n) => { if (n && !names.includes(n)) names.push(n); };
  const tree = getPath(state, 'plugins');
  if (tree && typeof tree === 'object' && !Array.isArray(tree)) {
    for (const [k, v] of Object.entries(tree)) {
      if (v && typeof v === 'object' && !Array.isArray(v)) push(k);
    }
  }
  // 树上有而表里有的名字都算；域表里列过但树上没有的（未装）也补上——
  // 与引擎库同理：库是「可配的全部」，不是「此刻存在的全部」。
  for (const root of Object.keys(DECLARED_FIELDS)) {
    if (root.startsWith('plugins.')) push(root.slice('plugins.'.length));
  }
  return names;
}

/** 一条插件卡（``plugins.<插件名>``）。
 *
 * 「启用中」的判据与脏分组的判据**同一把尺**：插件名在**选中意图分支**的
 * ``functions`` 清单里。同理不做第二把尺子。
 */
function pluginCard(name, opts) {
  const scope = currentToolsScope();
  return componentCard('plugins.' + name, {
    idPrefix: 'plug', fieldAttr: 'data-plugin',
    tag: '',
    live: scope.enabledPlugins.includes(name),
    liveLabel: '已启用',
  });
}

/** 库的外框（tab 栏 + 搜索 + 卡列表）。卡体按需填充（折叠态 DOM 字段数 = 0）。 */
function renderLibrary(spec, before) {
  const body = $('domainBody');
  body.classList.remove('placeholder');
  const groups = spec.groups;
  const q = QUERY.trim().toLowerCase();
  // 命中谓词只许有一份：命中数与「分布在 X / Y」文案共用它，写两遍就会
  // 出现「匹配 3 条 —— 分布在 0 个类目」这种自相矛盾的输出（两把尺子）。
  const hitOf = (group, name) => {
    const ty = String(spec.typeOf(group, name) ?? '');
    return name.toLowerCase().includes(q) || ty.toLowerCase().includes(q);
  };
  // 库里总共有多少个条目：搜索无果时的文案要说准数量（说组数就变成了
  // 「在全部 1 个插件里都找不到」这种废话）。
  const totalItems = groups.reduce((n, g) => n + spec.namesOf(g).length, 0);
  let cards = '';
  let note = '';
  if (q) {
    // 搜索**必须跨组**（§5.1）。命中项带组标签。
    const hits = [];
    const hitGroups = [];
    for (const group of groups) {
      const matched = spec.namesOf(group).filter((n) => hitOf(group, n));
      if (matched.length) hitGroups.push(group);
      for (const name of matched) {
        hits.push(spec.cardOf(group, name, { showCat: true }));
      }
    }
    cards = hits.length ? hits.join('') : `<div class="hint" style="padding:15px 0">${esc(spec.missingText(totalItems))}</div>`;
    note = `搜索态（<b>跨全部 ${groups.length > 1 ? `${groups.length} 个${esc(spec.catLabel)}` : `${totalItems} 个${esc(spec.noun)}`}</b>）：匹配 <b>${hits.length}</b> 条`
      + (hitGroups.length && groups.length > 1
        ? ` —— 分布在 ${hitGroups.join(' / ')}` : '') + '。';
  } else {
    if (groups.length > 1) {
      if (!CUR_CAT || !groups.includes(CUR_CAT)) CUR_CAT = groups[0];
      cards = spec.namesOf(CUR_CAT).map((n) => spec.cardOf(CUR_CAT, n)).join('')
        || `<div class="hint" style="padding:15px 0">${esc(spec.emptyText)}</div>`;
      const nEntries = spec.namesOf(CUR_CAT).length;
      const nFields = spec.namesOf(CUR_CAT)
        .reduce((n, name) => n + fieldsOf(spec.rootOf(CUR_CAT, name)).length, 0);
      note = `${esc(spec.catLabel)} <b>${esc(CUR_CAT)}</b>：<b>${nEntries}</b> 条${esc(spec.noun)}、`
        + `<b>${nFields}</b> 个字段。折叠态下 DOM 里字段数 = 0，展开才渲染。`;
    } else {
      const group = groups[0];
      cards = spec.namesOf(group).map((n) => spec.cardOf(group, n)).join('')
        || `<div class="hint" style="padding:15px 0">${esc(spec.emptyText)}</div>`;
      const nEntries = spec.namesOf(group).length;
      const nFields = spec.namesOf(group)
        .reduce((n, name) => n + fieldsOf(spec.rootOf(group, name)).length, 0);
      note = `共 <b>${nEntries}</b> 个${esc(spec.noun)}、<b>${nFields}</b> 个字段。`
        + '折叠态下 DOM 里字段数 = 0，展开才渲染。';
    }
  }
  const tabs = groups.length > 1
    ? `<div class="catbar" id="catbar">${groups.map((cat) =>
      `<button type="button" class="cattab${cat === CUR_CAT && !q ? ' on' : ''}"
        data-cat="${esc(cat)}">${esc(cat)}</button>`).join('')}</div>`
    : '';
  body.innerHTML = (before || '')
    + `<section class="group" id="${esc(spec.id)}"><h2>${esc(spec.title)}</h2>
        <div class="desc">${esc(spec.desc)}</div>
        ${tabs}
        <input class="search" id="${esc(spec.searchId)}" type="search" value="${esc(QUERY)}"
          placeholder="${esc(spec.searchPlaceholder)}">
        <div class="libnote">${note}</div>
        <div class="lib" id="lib">${cards}</div>
      </section>`;
  bindLibrary(spec);
  // 初始展开的卡（当前生效那一条）要立即填体；其余保持空壳（折叠态字段数 = 0）。
  document.querySelectorAll('.engine[open] .engbody').forEach(fillEngineBody);
  bindInputs(body);
}

/** 「当前生效」一行：类目名 + 下拉 + 选中项的类型/字段数 + 切换脏标记。 */
function slotRow(cat) {
  const names = enginesOf(cat);
  const cur = getPath(state, 'selected_module.' + cat);
  const owner = getPath(state, `${cat}.${cur}`);
  const d = DIRTY['selected_module.' + cat];
  const opts = names.map((n) =>
    `<option value="${esc(n)}" ${n === cur ? 'selected' : ''}>${esc(n)}</option>`).join('');
  // 配置里指向了一个本机没装的引擎（如 selected_module.Memory = nomem）：
  // 下拉里必须有它，否则用户看到的是「没有选中任何东西」。
  const extra = (cur && !names.includes(cur))
    ? `<option value="${esc(cur)}" selected>${esc(cur)}（未配置）</option>` : '';
  return `<div class="slot" id="slot-${esc(cat)}">
    <div class="cat">${esc(cat)}</div>
    <div class="selc"><select data-selcat="${esc(cat)}">${opts}${extra}</select></div>
    <div class="meta">${esc((owner && owner.type) ?? '?')} · ${fieldsOf(cat + '.' + cur).length} 字段</div>
    ${d ? `<span class="badge" style="background:rgba(124,92,255,.18);color:#a78bfa">[选择] 改自 ${esc(d.from ?? '（无）')}</span>` : ''}
  </div>`;
}

/** 一个库的交互：tab 切换、搜索（保持焦点）、``<details>`` 按需填体。 */
function bindLibrary(spec) {
  document.querySelectorAll('.cattab').forEach((btn) => {
    btn.addEventListener('click', () => {
      CUR_CAT = btn.dataset.cat;
      QUERY = '';
      rerenderLibrary(spec);
      applyHash();
    });
  });
  const search = $(spec.searchId);
  if (search) {
    search.addEventListener('input', () => {
      QUERY = search.value;
      rerenderLibrary(spec);
      // 重建会把焦点弄丢；搜索框是连续输入的控件，焦点必须留在原地。
      const again = $(spec.searchId);
      if (again) { again.focus(); again.setSelectionRange(QUERY.length, QUERY.length); }
    });
  }
  document.querySelectorAll('details.engine').forEach((det) => {
    det.addEventListener('toggle', () => {
      const box = det.querySelector('.engbody');
      if (box && det.open) fillEngineBody(box);
    });
  });
  document.querySelectorAll('select[data-selcat]').forEach((sel) => {
    sel.addEventListener('change', () => {
      setPath(state, 'selected_module.' + sel.dataset.selcat, sel.value);
      markChanged();
      rerenderLibrary(spec);
    });
  });
}

/** 重画当前库（tab / 搜索 / 下拉变化时）。
 *
 * 引擎库的重画会连带重画上方的「当前生效」与全局参数（它们在同一段
 * ``before`` 里）；工具域的重画同理带上散字段卡与意图分支卡。
 */
function rerenderLibrary(spec) {
  if (spec === ENGINE_LIBRARY) renderEngineLibrary();
  else renderToolsLibrary();
}

/* ---------------------------------------------------------------------------
 * 工具域（§7）
 *
 * 形状：三个部分，逐条照 §7 表与移交注记：
 *
 *   1. **当前生效意图引擎** + **工具调用**（散字段）：域内普通卡片，
 *      ``selected_module.Intent`` 是选择器（与引擎页六行同构）；
 *   2. **意图分支卡**（§7 移交注记 1）：``function_call`` / ``nointent`` /
 *      ``intent_llm`` 各一张——**两份 functions 清单同时可见**，切换
 *      ``selected_module.Intent`` 不再让清单消失；
 *   3. **插件库**（§7 移交注记 2）：与引擎库同构（搜索 + 真折叠），
 *      未启用插件的参数不再折进 ``<details>`` 黑洞。
 *
 * 为什么意图分支也做成卡而不是塞进「当前生效」那一块：§7 把每一条分支的字段
 * 当成一个独立条目计数（``Intent.function_call`` 2 / ``nointent`` 1 /
 * ``intent_llm`` 3），分成三张卡才能让 32 这个数在页面上数得出来。
 */
function renderToolsLibrary() {
  // 域内普通分组（选择器 / 工具调用 / 三条分支）与插件库分开渲染：分组卡的
  // 折叠规则（§2.5）与库的折叠规则（真折叠 + 懒构建）是两件事。
  const domainHasCommon = SCHEMA.groups.some(
    (g) => g.fields.some((f) => f.layer === 'common'));
  const plain = SCHEMA.groups.filter((g) => g.id !== 'plugins');
  const before = plain.map((g) => toolsGroupCard(g, domainHasCommon)).join('');
  renderLibrary(PLUGIN_LIBRARY, before);
}

/** 工具域的一张普通分组卡。
 *
 * ``intent-selector`` 是特例：``selected_module.Intent`` 的控件是**下拉**
 * （候选 = ``INTENT_BRANCHES``），不是文本框——写错一个名字就是「意图引擎
 * 静默失效」，而它没有下拉之外的防呆手段（与引擎页的六行同理）。
 */
function toolsGroupCard(g, domainHasCommon) {
  // 与 ``groupCard`` 是同一实现，差异只在意图选择器要补候选项。
  return groupCard(g, domainHasCommon, g.id === 'intent-selector'
    ? (fs) => fs.map((f) => ({ ...f, kind: 'select', options: INTENT_BRANCHES,
        selcat: 'Intent' }))
    : null);
}

/* ---------------------------------------------------------------------------
 * 设备域（#29，父 spec §4.4 / §7）
 *
 * 设备域的职责是「看设备 + 配设备」，所以它一页里有两层：
 *
 *   1. **配置字段**（17 个，域表驱动）：provisioning 载荷 / 设备认证 /
 *      hello 协商 / 节奏与时区——就是普通域页的 `groupCard`。
 *   2. **运行时面**（在线设备 / 固件库 / SmartConfig）+ **常驻摄像头入口**：
 *      面板声明由服务端注入（单一事实源 `page_domains.DEVICES_RUNTIME`），
 *      本模块只管把它们画出来并接上**旧页面已有的接口**。
 *
 * 为什么运行时面不写成域表里的字段：它们没有可保存的值——在线设备是运行态，
 * 上传/删除固件与配网广播是物理副作用。把它们当成字段会进脏列表、会进保存
 * 请求，而服务端根本没地方接受它们。
 *
 * 迁移口径（#29）：从旧 `config_page.html` 的「设备与固件（OTA）」区与
 * 「SmartConfig 设备配网」区搬过来，「操作顺序」等文案照抄。
 *
 * **危险分级与统一确认层在 #30 落地**（父 spec §6）：三个运行时操作按 §6.2
 * 落位表归级（重启设备动态升/降、上传/删除固件为危险），确认走壳注入的
 * 页内确认层；分级判定与后果文案来自共用的 `config_danger_model.js`。
 * ------------------------------------------------------------------------ */

/** 设备域页：配置分组（平铺规则同其他散字段域）+ 运行时面卡片。 */
function renderDevicesDomain() {
  const body = $('domainBody');
  body.classList.remove('placeholder');
  const domainHasCommon = SCHEMA.groups.some(
    (g) => g.fields.some((f) => f.layer === 'common'));
  body.innerHTML = SCHEMA.groups.map((g) => groupCard(g, domainHasCommon)).join('')
    + runtimePanelsHtml();
  bindInputs(body);
  bindRuntimePanels();
  // 本函数只在设备域跑（调用点自带 ``IS_DEVICES_DOMAIN`` 守卫），无需再判一次。
  refreshOta();
}

/** 一张运行时面卡片的壳（id 即 §4.5 的 hash 锚点）。 */
function runtimePanelShell(p, extra) {
  return `<section class="group" id="${esc(p.id)}">
    <h2>${esc(p.title)}${p.badge ? ` <span class="badge">${esc(p.badge)}</span>` : ''}</h2>
    <div class="desc">${esc(p.desc)}</div>
    ${extra || ''}</section>`;
}

/** 运行时面卡片：摄像头入口（常驻）+ 三个面板。
 *
 * 顺序有意如此：**摄像头入口在页首**。AC 明写「设备离线时常驻摄像头入口仍在」
 * ——把它挂在在线设备列表里（最自然的那一版）意味着设备一离线入口就消失。
 * 它由**壳级事实**（注入的 `camera`）渲染，与任何运行时状态无关。
 */
function runtimePanelsHtml() {
  const camera = CAMERA ? runtimePanelShell({
    id: 'camera-live',
    title: `${CAMERA.icon} ${CAMERA.label}`,
    desc: '设备域的实时视图：保留独立 URL，可当挂机监控的书签长期开着。'
      + '它不依赖设备是否在线——设备离线时仍可以从这里进去看状态。',
  }, '<div class="row"><div class="ctrl">'
    + `<a class="btn primary" href="${esc(CAMERA.path)}">${esc(CAMERA.label)} ↗</a>`
    + `<span class="hint" style="margin-left:10px">${esc(CAMERA.path)}</span>`
    + '</div></div>') : '';
  const panels = RUNTIME_PANELS.map((p) => RUNTIME_BODIES[p.id]
    ? runtimePanelShell(p, RUNTIME_BODIES[p.id]()) : '').join('');
  return camera + panels;
}

/** 面板正文（按注入的 id 分发）。
 *
 * 分发 key 用的是**注入的 id**（§4.5 的深链锚点），不是页面里的新名字：
 * 服务端说有哪些面板，页面说每个面板长什么样——两边认同一个 id。
 */
const RUNTIME_BODIES = {
  'online-devices': () => `
    <div class="runtime-actions">
      <span class="badge" id="otaDevCount">…</span>
      <button class="btn" type="button" onclick="refreshOta()">🔄 刷新</button>
    </div>
    <div id="otaDevices" class="runtime-list">加载中…</div>`,
  'firmware': () => `
    <div class="runtime-actions"><span class="badge" id="otaFwCount">…</span></div>
    <div id="otaFirmwares" class="runtime-list">加载中…</div>
    <div class="runtime-actions">
      <input type="file" id="otaFile" accept=".bin">
      <button class="btn danger" id="otaUploadBtn" type="button"
        data-danger-op="upload_firmware" data-danger-level="danger"
        title="危险：放进固件库 = 武装未来所有开机自检（ADR-0002）"
        onclick="uploadFirmware()">⛔ ⬆ 上传固件</button>
    </div>`,
  'smartconfig': () => `
    <div class="runtime-form">
      <div class="runtime-actions">
        <input type="text" id="scSsid" placeholder="Wi-Fi 名称 (SSID)">
        <button class="btn" type="button" onclick="autofillWifi()">⚡ 自动填充</button>
      </div>
      <input type="text" id="scPass" placeholder="Wi-Fi 密码">
      <div class="runtime-actions">
        <button class="btn warn" id="scBtn" type="button"
          data-danger-op="smartconfig" data-danger-level="warning"
          onclick="sendSmartConfig()">⚠ 📡 开始广播</button>
        <span id="scStatus" class="hint"></span>
      </div>
    </div>`,
};

function bindRuntimePanels() {
  // 面板里的按钮走内联 onclick（与旧页面同一先例），所以这里把几个只在设备域
  // 存在的函数挂到 window 上（模块作用域不自动挂 window）。
  window.refreshOta = refreshOta;
  window.uploadFirmware = uploadFirmware;
  window.deleteFirmware = deleteFirmware;
  window.rebootDevice = rebootDevice;
  window.autofillWifi = autofillWifi;
  window.sendSmartConfig = sendSmartConfig;
}

/* ---- 运行时面：在线设备 + 固件库（搬自 config_page.html 的「设备与固件」区） ----
 *
 * 危险分级（#30，父 spec §6）在这一节落地。三个操作按 §6.2 落位表归级：
 *
 *   - 重启设备：**动态项**——固件库无更新版本时是警示，有更新版本时升为危险
 *     （它变成触发升级的扳机）。级别由 ``config_danger_model.levelOf`` 算，
 *     判据是**两个既有列表的版本对比**（§6.6「无需新接口」）；
 *   - 上传固件：危险（武装自动升级链，ADR-0002）；
 *   - 删除固件：危险（信息永失），且**额外输入固件版本号**才能确认（§6.3）。
 *
 * ⚠️ 漂移风险：旧八组页面（``config_page.html``）的同名函数**仍在线上**
 * （``/xiaozhi/config/`` 在 #31 收线前继续保持可访）。两份共用同一份
 * ``config_danger_model.js`` 与同一件确认层（壳注入），所以分级判定与视觉
 * 编码不会分叉；两边的差别只剩「面板长什么样」。
 *
 * 两个请求并发（旧页面就是 `Promise.all`），但**各自报自己的错**：
 * 旧页面用一个 try 把两次请求绑死，结果是「设备列表超时 → 固件库也空了」。
 */

/** 运行时面缓存：两个列表的最近一次结果。
 *
 * 危险分级需要它们（重启设备的升降级要拿设备版本与固件库版本对比），
 * 而 ``levelOf`` 是**纯函数**——它不自己去拉数据，由页面把这两个列表喂给它。
 * 缓存同时供「上传固件」算在线设备数 / 目标版本（§6.3 的动态数字）。
 */
const RUNTIME = { devices: [], firmwares: [] };

async function refreshOta() {
  if (!$('otaDevices')) return;
  const devBox = $('otaDevices');
  const fwBox = $('otaFirmwares');
  const [dev, fw] = await Promise.all([
    fetchJsonOrError('/xiaozhi/config/api/devices'),
    fetchJsonOrError('/xiaozhi/config/api/firmware'),
  ]);
  if (dev.ok) {
    const devices = dev.data.devices || [];
    RUNTIME.devices = devices;
    const badge = $('otaDevCount');
    if (badge) badge.textContent = devices.length + ' 台';
    devBox.innerHTML = devices.length ? devices.map((d) => {
      // 每台设备自己算级别：同一页上两台设备可能一台警示、一台危险
      // （库里有它型号的更新版本，而没有另一台的）。
      const lvl = levelOf('reboot_device', { firmwares: RUNTIME.firmwares, device: d });
      const vis = LEVEL_VISUALS[lvl];
      return `
      <div class="row">
        <div class="meta">
          <div class="label">${esc(d.device_id)}</div>
          <div class="hint">IP: ${esc(d.client_ip || '-')}</div>
        </div>
        <div class="ctrl" style="display:flex;gap:10px;align-items:center">
          <span style="color:var(--ok);font-size:12px">● 在线</span>
          <button class="btn${vis.buttonClass ? ' ' + vis.buttonClass : ''}" type="button"
            data-danger-op="reboot_device" data-danger-level="${esc(lvl)}"
            data-device-id="${esc(d.device_id)}"
            onclick="rebootDevice('${esc(d.device_id)}')">${vis.icon ? vis.icon + ' ' : ''}⟳ 重启并检查更新</button>
        </div>
      </div>`;
    }).join('')
      : '<div class="hint">暂无在线设备 —— 设备空闲时会断开连接，'
        + '唤醒后即会出现在这里</div>';
  } else {
    devBox.innerHTML = '<div class="hint" style="color:var(--err)">加载失败: '
      + esc(dev.error) + '</div>';
  }
  if (fw.ok) {
    const firmwares = fw.data.firmwares || [];
    RUNTIME.firmwares = firmwares;
    const badge = $('otaFwCount');
    if (badge) badge.textContent = firmwares.length + ' 个';
    fwBox.innerHTML = firmwares.length ? firmwares.map((f) => `
      <div class="row">
        <div class="meta">
          <div class="label">${esc(f.model || '?')} `
          + `<span style="color:var(--accent)">v${esc(f.version || '?')}</span></div>
          <div class="hint">${esc(f.filename)} · ${(f.size / 1048576).toFixed(2)} MB`
          + ` · ${esc(new Date(f.mtime * 1000).toLocaleString())}</div>
        </div>
        <div class="ctrl" style="display:flex;gap:10px;align-items:center">
          <button class="btn danger" type="button"
            data-danger-op="delete_firmware" data-danger-level="danger"
            onclick="deleteFirmware('${esc(f.filename)}')">${LEVEL_VISUALS.danger.icon} 🗑 删除</button>
        </div>
      </div>`).join('')
      : '<div class="hint">固件库为空</div>';
    // 固件列表更新后，设备行的级别可能变了（刚上传了更高版本）——重算一次
    // 按钮视觉。**只有两处都重算**才不会出现「固件库说有新版本、设备行还是
    // 琥珀色」这种自相矛盾的读数。
    if (dev.ok) repaintDeviceRebootButtons();
  } else {
    fwBox.innerHTML = '<div class="hint" style="color:var(--err)">加载失败: '
      + esc(fw.error) + '</div>';
  }
}

/** 设备行按钮的分级视觉重画（固件库变动后调用）。
 *
 * 分级视觉的三载体（§6.4）：类名（颜色）、按钮文字前缀（图标）、title（文案）。
 */
function repaintDeviceRebootButtons() {
  document.querySelectorAll('[data-danger-op="reboot_device"]').forEach((btn) => {
    const id = btn.dataset.deviceId || '';
    const device = RUNTIME.devices.find((d) => d.device_id === id) || { device_id: id };
    const lvl = levelOf('reboot_device', { firmwares: RUNTIME.firmwares, device });
    const vis = LEVEL_VISUALS[lvl];
    btn.className = 'btn' + (vis.buttonClass ? ' ' + vis.buttonClass : '');
    btn.dataset.dangerLevel = lvl;
    btn.title = vis.icon
      ? (vis.label + '：' + operationConsequences('reboot_device',
        { firmwares: RUNTIME.firmwares, device }).join(' '))
      : '';
  });
}

/** 只取 JSON 的请求：失败不抛，把错误当成一个可显示的结果。 */
async function fetchJsonOrError(path) {
  try {
    const r = await fetch(path);
    const data = await r.json().catch(() => null);
    if (!r.ok || !data) throw new Error((data && data.error) || ('HTTP ' + r.status));
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

/** 统一页内确认层（§6.5）：分级判定 + 后果文案在这里，视觉渲染在壳里。
 *
 * 返回 Promise<boolean>；判定全走 ``config_danger_model`` 的纯函数——
 * 页面里**没有第二套分级规则**（两份在线副本共用这一份）。
 */
function confirmDanger(operation, context, opts) {
  const o = opts || {};
  const level = levelOf(operation, context);
  const facts = Object.assign({}, context, opts && opts.facts);
  return window.xzhConfirm({
    level,
    title: o.title || '确认操作',
    consequences: operationConsequences(operation, facts),
    confirmText: confirmButtonText(operation, facts),
    // 打字摩擦（§6.3）：只有删除固件有，且对象是**固件版本号**。
    typeToConfirm: needsVersionTyping(operation)
      ? {
        label: '输入固件版本号以确认',
        value: (facts.firmware && facts.firmware.version) || '',
        hint: '版本号在固件库列表里——'
          + '这一步是故意的：删除不可逆，且不是正路运维（ADR / §6.3）。',
      }
      : null,
  });
}

/** 重启设备并让它检查 OTA 更新（§6.2 动态项：警示 / 危险）。
 *
 * 「固件库有更新版本」由 **两个既有列表对比版本**算出（§6.6 无需新接口），
 * 算出的级别决定确认层的形态：警示 = 一句后果，危险 = 后果清单。
 */
async function rebootDevice(deviceId) {
  const device = RUNTIME.devices.find((d) => d.device_id === deviceId)
    || { device_id: deviceId };
  const ctx = { firmwares: RUNTIME.firmwares, device };
  const ok = await confirmDanger('reboot_device', ctx, {
    title: `重启设备 ${deviceId}`,
  });
  if (!ok) return;
  try {
    const r = await fetch(
      `/xiaozhi/ota/reboot?device_id=${encodeURIComponent(deviceId)}`,
      { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.message || ('HTTP ' + r.status));
    toast(`已发送重启指令给 ${deviceId} ✔`, 'ok');
    setTimeout(refreshOta, 1500);
  } catch (e) {
    toast('重启失败: ' + e.message, 'err');
  }
}

/** 上传固件（multipart，文件名必须是 型号_版本.bin）。
 *
 * 危险级（§6.2）：武装自动升级链。确认层带**后果清单**与动态数字
 * （在线设备数、目标固件版本），并写准武装对象 = **未来所有开机自检**
 * （ADR-0002：在线设备不立即升级，重启才升）。**不设打字摩擦**（§6.3）。
 */
async function uploadFirmware() {
  const inp = $('otaFile');
  if (!inp || !inp.files.length) { toast('请先选择 .bin 固件文件', 'err'); return; }
  const file = inp.files[0];
  // 目标固件版本从文件名推（后端命名契约：型号_版本.bin）。
  const m = /^(.+)_([^_]+)\.bin$/.exec(file.name);
  const pendingFirmware = {
    filename: file.name,
    model: m ? m[1] : '',
    version: m ? m[2] : '',
  };
  const ok = await confirmDanger('upload_firmware', {
    devices: RUNTIME.devices,
    firmwares: RUNTIME.firmwares,
    pendingFirmware,
  }, { title: `上传固件 ${file.name}` });
  if (!ok) return;
  const fd = new FormData();
  fd.append('file', file);
  const btn = $('otaUploadBtn');
  if (btn) { btn.disabled = true; btn.textContent = '⬆ 上传中…'; }
  try {
    const r = await fetch('/xiaozhi/config/api/firmware/upload',
      { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
    toast(`固件 ${d.filename} 上传成功 ✔`, 'ok');
    inp.value = '';
    refreshOta();
  } catch (e) {
    toast('上传失败: ' + e.message, 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⬆ 上传固件'; }
  }
}

/** 删除固件（危险级 + **输入固件版本号**才能确认，§6.3）。
 *
 * 这是三级里唯一带打字摩擦的操作：不可逆且不是正路运维。版本号取自固件库
 * 列表里的那条记录（``RUNTIME.firmwares``），不打字根本解不开确认按钮。
 */
async function deleteFirmware(filename) {
  const firmware = RUNTIME.firmwares.find((f) => f.filename === filename)
    || { filename };
  const ok = await confirmDanger('delete_firmware', { firmware, firmwares: RUNTIME.firmwares },
    { title: `删除固件 ${filename}` });
  if (!ok) return;
  try {
    await api('/xiaozhi/config/api/firmware/delete',
      { method: 'POST', body: JSON.stringify({ filename }) });
    toast('已删除 ' + filename, 'ok');
    refreshOta();
  } catch (e) {
    toast('删除失败: ' + e.message, 'err');
  }
}

/* ---- 运行时面：SmartConfig 配网（搬自 config_page.html 的配网区） ---- */

/** Wi-Fi 自动填充：先问服务端（系统钥匙串），读不到再回落上次手输的凭据。 */
async function autofillWifi() {
  const el = $('scStatus');
  try {
    const r = await fetch('/xiaozhi/config/api/local-wifi');
    const d = await r.json();
    if (d && d.ssid) {
      $('scSsid').value = d.ssid;
      $('scPass').value = d.password || '';
      if (el) el.textContent = '已填充系统 Wi-Fi 凭据';
      return;
    }
  } catch (e) { /* 读不到系统 Wi-Fi（macOS 隐私限制），走下面的回落 */ }
  let last = null;
  try { last = localStorage.getItem('scWifi'); } catch (e) { last = null; }
  if (last) {
    try {
      const d = JSON.parse(last);
      $('scSsid').value = d.ssid || '';
      $('scPass').value = d.pass || '';
      if (el) el.textContent = '已填充上次使用的 Wi-Fi';
      return;
    } catch (e) { /* 坏数据，当没有 */ }
  }
  if (el) el.textContent = '无历史记录（macOS 隐私限制读不到系统 Wi-Fi），请手输，下次自动记住';
}

/** SmartConfig 广播（约 30 秒）。**警示级**（§6.2）：物理世界有副作用，但重新
 * 广播一次即可覆盖——确认层只重述一句后果，不带后果清单。
 * 「操作顺序」警告在面板 desc 里。
 */
async function sendSmartConfig() {
  const ssid = $('scSsid').value.trim();
  const password = $('scPass').value;
  const st = $('scStatus');
  if (!ssid) { st.textContent = 'SSID 不能为空'; return; }
  const ok = await confirmDanger('smartconfig', {}, { title: '开始 SmartConfig 广播' });
  if (!ok) return;
  try { localStorage.setItem('scWifi', JSON.stringify({ ssid, pass: password })); }
  catch (e) { /* 隐私模式写不进 localStorage，不阻断配网 */ }
  const btn = $('scBtn');
  if (btn) btn.disabled = true;
  st.textContent = '广播中…（约 30 秒）';
  try {
    const r = await fetch('/xiaozhi/config/api/smartconfig', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid, password }),
    });
    const d = await r.json();
    st.textContent = d.ok
      ? '📡 广播中…设备收到后自动连网并上线' : ('失败: ' + (d.error || ''));
  } catch (e) { st.textContent = '请求失败: ' + e; }
  setTimeout(() => { if (btn) btn.disabled = false; }, 5000);
}

/* ---------------------------------------------------------------------------
 * 页级渲染
 * ------------------------------------------------------------------------ */

function render() {
  if (IS_ENGINE_DOMAIN) { renderEngineLibrary(); return; }
  if (IS_TOOLS_DOMAIN) { renderToolsLibrary(); return; }
  if (IS_DEVICES_DOMAIN) { renderDevicesDomain(); return; }
  const body = $('domainBody');
  // §2.5：域内常用层为空时折叠区不渲染、全部字段平铺（系统域就是这样）。
  const domainHasCommon = SCHEMA.groups.some(
    (g) => g.fields.some((f) => f.layer === 'common'));
  body.classList.remove('placeholder');
  body.innerHTML = SCHEMA.groups.map((g) => groupCard(g, domainHasCommon)).join('');
  bindInputs(body);
}

/* ---------------------------------------------------------------------------
 * 脏状态（跨页摘要写在 localStorage，只含路径与计数）
 * ------------------------------------------------------------------------ */

/** 当前选中（``selected_module.*``）：引擎六族 + 意图分支。
 *
 * ``Intent`` 必须一起收：工具域的两份 functions 清单靠它判生效性（§5.4 的分组
 * 随选中实时重算），漏了它就变成「所有意图分支都算未选中」——分类在说谎。
 */
function currentSelection() {
  const sel = {};
  for (const cat of ENGINE_CATEGORIES) {
    const v = getPath(state, 'selected_module.' + cat);
    if (v !== undefined) sel[cat] = v;
  }
  const intent = getPath(state, 'selected_module.Intent');
  if (intent !== undefined) sel.Intent = intent;
  return sel;
}

/** 工具域的分组范围：选中分支 + 它的 functions 清单（同一份状态模型函数）。
 *
 * **与 ``currentSelection`` 同一时刻重算**，所以「切意图引擎 → 两份清单的生效性
 * 升降级」是同一帧里的事，不存在缓存不一致的窗口。
 */
function currentToolsScope() {
  return toolsScope(state, INTENT_BRANCHES);
}

/** 本域脏条目（域表之外的脏不属于本页，不显示也不写摘要）。 */
function domainDirtyEntries() {
  const all = computeDirty(PAGE);
  const out = {};
  for (const [path, entry] of Object.entries(all)) {
    if (belongsToDomain(path)) out[path] = entry;
  }
  return out;
}

function markChanged() {
  PAGE.state = state;
  PAGE.cfg = CONFIG;
  DIRTY = domainDirtyEntries();

  const n = Object.keys(DIRTY).length;
  const saveBtn = $('saveBtn');
  if (saveBtn) saveBtn.disabled = n === 0;
  const info = $('saveInfo');
  if (info) info.textContent = n > 0 ? '保存后需重启服务使配置生效' : '暂无未保存修改';

  // ---- 跨页摘要：只写路径与计数，绝不写值（§4.6） ----
  const summary = dirtySummary(DIRTY);
  try {
    if (summary.count > 0) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(summary));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch (e) { /* 隐私模式等场景下写不进 localStorage，不阻断编辑 */ }
  // 侧栏脏点由壳在**页面加载时**读一次。摘要是本页刚写下的，壳已经读过了，
  // 所以必须主动叫它重画——否则第一次编辑不会画点，要点第二下才出来。
  paintSidebarDots();

  renderDirtyPanel();
  refreshEngineMarks();
}

/** 就地把引擎卡上的脏点 / 当前生效标记 / 选中行标记重画。
 *
 * 为什么不整个重建：编辑事件会重建 DOM，而重建会把正打字的输入框焦点弄丢。
 * 这里只改那些「一变就看得见」的小节点，控件的值不动。卡的**行内**脏标记
 * （``.row.dirty``）同样就地改——行级标记与卡级圆点是两处不同的视觉提示。
 */
function refreshEngineMarks() {
  if (!IS_ENGINE_DOMAIN && !IS_TOOLS_DOMAIN) return;
  document.querySelectorAll('details.engine').forEach((det) => {
    // 卡上的锚点属性按域不同（``data-engine`` / ``data-plugin``），但都是
    // **组件根路径**——脏前缀匹配与深链用的是同一个值（两处同源）。
    const key = det.dataset.libRoot || '';
    const dirty = Object.keys(DIRTY).some(
      (p) => p === key || p.startsWith(key + '.'));
    det.classList.toggle('dirty', dirty);
    const dot = det.querySelector('.dirtydot');
    if (dot) dot.hidden = !dirty;
    det.querySelectorAll('.engbody .row').forEach((rowEl) => {
      const input = rowEl.querySelector('[data-path], [data-list]');
      const path = input ? (input.dataset.path || input.dataset.list) : '';
      rowEl.classList.toggle('dirty', Boolean(DIRTY[path]));
    });
    // 插件的「已启用」标记同样随 functions 清单实时重画（切意图引擎时
    // 整库的启用态会变）——它也是「一行小节点」，不需要重建 DOM。
    if (IS_TOOLS_DOMAIN && det.dataset.plugin) {
      const name = det.dataset.plugin.slice('plugins.'.length);
      const live = currentToolsScope().enabledPlugins.includes(name);
      det.classList.toggle('live', live);
      const tag = det.querySelector('.live-tag');
      if (tag) tag.hidden = !live;
    }
  });
  // 选择器（引擎六族 + 意图分支）的 DOM 值与状态对齐。
  const selCats = IS_TOOLS_DOMAIN ? ['Intent'] : ENGINE_CATEGORIES;
  for (const cat of selCats) {
    const sel = document.querySelector(`select[data-selcat="${cat}"]`);
    const cur = getPath(state, 'selected_module.' + cat);
    if (sel && cur !== undefined && sel.value !== cur) sel.value = cur;
  }
}

/** 通知壳重画侧栏脏点。
 *
 * 壳是另一段脚本（可能与本模块同时就位），它认两种通知：
 *   - ``xzh:dirty-changed`` 自定义事件（推荐，不依赖加载先后）；
 *   - ``window.__xzhPaintDirtyDots``（兑底，壳已就位但没听到事件时）。
 * 两个都给：模块脚本与 classic 脚本的执行顺序不属于页面能控的事，“脏的权威在
 * 域页、脏点的渲染在壳”这条分工不该建在时序假设上。
 */
function paintSidebarDots() {
  try {
    window.dispatchEvent(new Event('xzh:dirty-changed'));
  } catch (e) { /* 事件不可用时走下面的兑底 */ }
  if (typeof window.__xzhPaintDirtyDots === 'function') {
    window.__xzhPaintDirtyDots();
  }
}

function renderDirtyPanel() {
  const box = $('dirtyPanel');
  if (!box) return;
  const n = Object.keys(DIRTY).length;
  if (n === 0) { box.innerHTML = ''; return; }
  // 工具域多传一层「组件范围」：`Intent` 分支与插件都是可选中/可启用的组件，
  // 不传这一层的话 `plugins.get_weather.api_key` 这种三段路径会被当成散字段。
  // 两种域传不同的范围，但用的是**同一个** `groupDirty`（不是两份分组实现）。
  const tools = IS_TOOLS_DOMAIN ? currentToolsScope() : undefined;
  const groups = groupDirty(DIRTY, currentSelection(), ENGINE_CATEGORIES, tools);
  const block = (title, items) => items.length
    ? `<div><b>${title}</b> · ${items.length} 条${items.map((it) =>
      `<div class="dirty-item">${esc(dirtyLabel(it))}</div>`).join('')}</div>`
    : '';
  box.innerHTML = block('重启后生效', groups.active)
    + block('仅提前配好 · 当前不生效', groups.staged);
}

/* ---------------------------------------------------------------------------
 * 交互
 * ------------------------------------------------------------------------ */

function bindInputs(root) {
  const scope = root || document;
  scope.querySelectorAll('[data-path]').forEach((el) => {
    const path = el.dataset.path;
    const evt = el.type === 'checkbox' ? 'change' : 'input';
    el.addEventListener(evt, () => {
      // 密钥：输入不落配置树，只进密钥槽（第三态的载体）。留空 = 保持原值。
      if (el.dataset.sens === '1') {
        SECRET_INPUT[path] = el.value;
        PAGE = setSecretInput(PAGE, path, el.value);
        // 「将替换」标记就地翻转（重建会把焦点弄丢）。
        const slot = el.parentElement;
        let mark = slot && slot.querySelector('.secret-replace-mark');
        if (el.value !== '' && !mark && slot) {
          mark = document.createElement('span');
          mark.className = 'badge secret-replace-mark';
          mark.style.cssText = 'background:rgba(251,191,36,.18);color:var(--warn)';
          mark.textContent = '将替换';
          slot.insertBefore(mark, el);
        } else if (el.value === '' && mark) {
          mark.remove();
        }
        markChanged();
        return;
      }
      let val;
      if (el.type === 'checkbox') val = el.checked;
      else if (el.type === 'number') val = el.value === '' ? null : Number(el.value);
      else val = el.value;
      if (el.type === 'checkbox') {
        const lb = el.closest('.switch-row')?.querySelector('.sw-label');
        if (lb) lb.textContent = val ? '开启' : '关闭';
      }
      if (val === null || val === '') {
        const parts = path.split('.');
        const parent = parts.length > 1 ? getPath(state, parts.slice(0, -1).join('.')) : null;
        if (parent && typeof parent === 'object') delete parent[parts[parts.length - 1]];
      } else {
        setPath(state, path, val);
      }
      markChanged();
    });
  });

  scope.querySelectorAll('[data-list]').forEach((el) => {
    el.addEventListener('input', () => {
      const items = getPath(state, el.dataset.list);
      if (Array.isArray(items)) items[Number(el.dataset.idx)] = el.value;
      markChanged();
    });
  });

  scope.querySelectorAll('[data-add]').forEach((el) => {
    el.addEventListener('click', () => {
      const items = getPath(state, el.dataset.add);
      if (Array.isArray(items)) items.push('');
      rerenderKeepingFoldState();
      markChanged();
    });
  });

  scope.querySelectorAll('[data-del]').forEach((el) => {
    el.addEventListener('click', () => {
      const items = getPath(state, el.dataset.del);
      if (Array.isArray(items)) items.splice(Number(el.dataset.idx), 1);
      rerenderKeepingFoldState();
      markChanged();
    });
  });
}

/** 重建页面，但**保持折叠状态**。
 *
 * 列表增删（`[data-add]` / `[data-del]`）要重建才能刷新行号；而搜索框/下拉的
 * 输入事件走 ``markChanged`` 不重建（重建会把焦点弄丢）。两条路径分开是有意的：
 * 重建一个展开的引擎卡会把用户刚打开的折叠合上，那是界面在自作主张。
 */
function rerenderKeepingFoldState() {
  const openCards = [];
  document.querySelectorAll('details.engine[open]').forEach((d) => {
    openCards.push(d.dataset.libRoot);
  });
  const openMore = [];
  document.querySelectorAll('details.more-settings[open]').forEach((d) => {
    const card = d.closest('details.engine');
    if (card) openMore.push(card.dataset.libRoot);
  });
  render();
  for (const key of openCards) {
    const det = document.querySelector(`details.engine[data-lib-root="${cssEscape(key)}"]`);
    if (det) det.open = true;
  }
  for (const key of openMore) {
    const det = document.querySelector(`details.engine[data-lib-root="${cssEscape(key)}"]`);
    if (det) det.querySelectorAll('details.more-settings').forEach((m) => { m.open = true; });
  }
}

/* ---------------------------------------------------------------------------
 * API
 * ------------------------------------------------------------------------ */

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  const data = await r.json().catch(() => ({ ok: false, error: '响应解析失败' }));
  if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}

function toast(msg, type = '') {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'show ' + type;
  clearTimeout(t._h);
  t._h = setTimeout(() => { t.className = ''; }, 2600);
}

async function loadConfig() {
  const d = await api('/xiaozhi/config/api/full');
  CONFIG_STATE = d.config_state || {};
  CONFIG = d.config || {};
  // 状态模型（#25 地基）负责「配置树 → 页面初值 + 密钥三态起点」。
  PAGE = initialState({ config: CONFIG, config_state: CONFIG_STATE });
  PAGE.selection = currentSelection();
  PAGE.origSelection = JSON.parse(JSON.stringify(PAGE.selection));
  state = PAGE.state;
  for (const k of Object.keys(SECRET_INPUT)) delete SECRET_INPUT[k];
}

async function saveAll() {
  const btn = $('saveBtn');
  try {
    if (btn) { btn.disabled = true; btn.textContent = '💾 保存中…'; }
    // 密钥输入写进 after 树（服务端只认 after；掩码占位被跳过）。
    for (const [path, val] of Object.entries(SECRET_INPUT)) {
      if (val !== '') setPath(state, path, val);
    }
    const r = await api('/xiaozhi/config/api/save', {
      method: 'POST', body: JSON.stringify({ before: CONFIG, after: state }),
    });
    toast(`已保存 ${r.changed || 0} 处修改`, 'ok');
    await loadConfig();
    render();
    markChanged();
    toast('保存成功，点击「重启服务生效」使配置生效', 'ok');
  } catch (e) {
    toast('保存失败: ' + e.message, 'err');
  } finally {
    if (btn) { btn.textContent = '💾 保存修改'; }
  }
}

/** 重启服务使配置生效。**警示级**（§6.2 / §6.4 修严重度倒置：红 → 琥珀）。
 *
 * 它是自愈操作（断连 10-30s 后由 launchctl 拉起），原页面把它渲染成红色实底
 * 的 ``btn danger``——这是 §6.4 点名的三处严重度倒置之一。确认层是单击确认
 * （重述一句后果），不是强确认。
 */
async function restartServer() {
  const ok = await confirmDanger('restart_server', {}, { title: '重启服务' });
  if (!ok) return;
  try {
    await api('/xiaozhi/config/api/restart', { method: 'POST', body: '{}' });
    toast('重启指令已发出，服务恢复中…', 'ok');
  } catch (e) {
    toast('重启失败: ' + e.message, 'err');
  }
}

/* ---------------------------------------------------------------------------
 * hash 深链（§4.5）
 *
 * 语法：
 *   - ``#<类目小写id>``         —— 引擎页选中类目 tab（``#tts``）
 *   - ``#<类目>/<引擎名>``      —— 选中 tab 并展开/滚动到该引擎卡
 *     （``#tts/MlxKafeiStreamTTS``）
 *   - ``#<分组id>``              —— 其余域页定位域内分组
 *
 * 片段纯客户端（nginx 与 8003 直连零改动），而且只含 ASCII：中文 id 进 hash
 * 会在不同浏览器上被编成不同形态，深链就不可转述了。
 * ------------------------------------------------------------------------ */
function applyHash() {
  const id = decodeURIComponent(window.location.hash.replace(/^#/, ''));
  if (!id) return;

  // ---- 引擎页的两段形式：``#<类目>/<引擎名>`` ----
  if (IS_ENGINE_DOMAIN && id.includes('/')) {
    const [cat, name] = id.split('/', 2);
    const known = ENGINE_CATEGORIES.find((c) => c.toLowerCase() === cat.toLowerCase());
    if (known) {
      CUR_CAT = known;
      QUERY = '';
      renderEngineLibrary();
      const det = document.querySelector(
        `details.engine[data-engine="${cssEscape(known + '.' + name)}"]`);
      if (det) {
        det.open = true;
        fillEngineBody(det.querySelector('.engbody'));
        det.scrollIntoView({ block: 'start' });
        det.classList.add('hash-flash');
        setTimeout(() => det.classList.remove('hash-flash'), 1500);
      }
      return;
    }
  }
  // ---- 引擎页的一段形式：``#<类目小写id>`` 选 tab ----
  if (IS_ENGINE_DOMAIN) {
    const known = ENGINE_CATEGORIES.find((c) => c.toLowerCase() === id.toLowerCase());
    if (known) {
      CUR_CAT = known;
      QUERY = '';
      renderEngineLibrary();
      const tab = document.querySelector(`.cattab[data-cat="${cssEscape(known)}"]`);
      if (tab) tab.scrollIntoView({ block: 'start' });
      return;
    }
  }

  // ---- 工具域：``#<插件名>`` 直达插件卡（§4.5 的 ``#<分组id>`` 延伸） ----
  // 插件卡与分组卡都是域内的锚点目标，但插件名可能恰好与某个分组 id 同名
  // （都是 ASCII），所以先试插件卡、再回落分组 id（顺序写死，不许两边都查）。
  if (IS_TOOLS_DOMAIN) {
    // 按**锚点属性**查（与引擎页同一把尺），不按元素 id 拼字符串：
    // id 的模板（``plug-plugins-<名>``）与这里探的名字不同源，拼 id 会
    // 得到一个永远不存在的值——分支看似在、其实永不进入。
    const plug = document.querySelector(
      `details.engine[data-plugin="${cssEscape('plugins.' + id)}"]`);
    if (plug) {
      plug.open = true;
      fillEngineBody(plug.querySelector('.engbody'));
      plug.scrollIntoView({ block: 'start' });
      plug.classList.add('hash-flash');
      setTimeout(() => plug.classList.remove('hash-flash'), 1500);
      return;
    }
  }

  const target = document.getElementById(id);
  if (!target) return;
  // 目标若在折叠容器里（「更多设置」），先展开——否则滚过去是个空壳。
  const details = target.querySelector('details.more-settings');
  if (details) details.open = true;
  target.scrollIntoView({ block: 'start' });
  target.classList.add('hash-flash');
  setTimeout(() => target.classList.remove('hash-flash'), 1500);
}

/** CSS 属性选择器里的值转义（引擎名里不会有引号，但路径里的 `.` 不能当通配）。
 *
 * 引擎名是配置树上的键、不是我们写的字符串——不转义就是 `TTS.a".b` 这类名字
 * 把选择器打断。
 */
function cssEscape(v) {
  return String(v).replace(/["\\]/g, '\\$&');
}

/* ---------------------------------------------------------------------------
 * 起手
 * ------------------------------------------------------------------------ */
async function init() {
  try {
    await loadConfig();
    render();
    markChanged();
    applyHash();
    window.addEventListener('hashchange', applyHash);
  } catch (e) {
    $('domainBody').innerHTML =
      `<div class="placeholder" style="color:var(--err)">加载失败：${esc(e.message)}</div>`;
    toast('加载失败: ' + e.message, 'err');
  }
}

/* 壳的离开确认读这里（§4.6）。壳不猜脏、不重算——域页才是脏的权威。 */
window.__xzhDirtyGuard = {
  count: () => Object.keys(DIRTY).length,
  paths: () => Object.keys(DIRTY).sort(),
};

/* 内联 onclick 的显式桥（与旧页面同一先例：模块作用域不自动挂 window）。 */
window.xzhSave = saveAll;
window.xzhRestart = restartServer;
/* 确认层可由控制台或外部脚本直接调用（调试口径与审计口径同一条）。 */
window.xzhConfirmDanger = confirmDanger;

init();
