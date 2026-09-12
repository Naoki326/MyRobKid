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
 *   3. **引擎库**（§5，仅 engine 域）：上方「当前生效」六行下拉，下方「全部
 *      引擎」按类目 tab × **跨类目搜索** × 折叠展开；
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
  computeDirty, dirtySummary, domainDirty, groupDirty, dirtyLabel,
  getPath, initialState, isSensitiveKey, placeholderText, setPath,
  setSecretInput,
} from './config_state_model.js';

const SCHEMA = JSON.parse(document.getElementById('domain-schema').textContent);
const SLUG = SCHEMA.slug;
const STORAGE_KEY = 'config-dirty/' + SLUG;

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
  return `<div class="row${dirty ? ' dirty' : ''}">
    <div class="meta"><div class="label">${esc(fieldLabel(f))}</div>
      ${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ''}</div>
    <div class="ctrl">${control(f)}</div>
  </div>`;
}

/** 域内一个分组卡片。``id`` 是 hash 深链的锚点（§4.5）。
 *
 * 「更多设置」折叠的**开合判据是域内常用层是否为空**，不是「这个分组有没有
 * more 字段」（父 spec §2.5：*域内常用层为空时，折叠区不渲染，全部字段平铺*）。
 * 系统域就是这条规则的实例：它 13 个字段全是 more，若还折一层，整页只有一个
 * 空壳，字段一个也看不见。
 */
function groupCard(g, domainHasCommon) {
  const common = g.fields.filter((f) => f.layer === 'common');
  const more = g.fields.filter((f) => f.layer === 'more');
  // 常用层为空 → 平铺（把所有字段当 common 渲染，不生成折叠容器）。
  const fold = domainHasCommon && more.length > 0;
  const flat = domainHasCommon ? common : g.fields;
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

/** 表：``<类目>.<引擎名>`` → [{path, layer}]（方案裁决过的字段）。
 *
 * 引擎名与类目**不靠这张表枚举**（见下），表只回答「这条引擎的字段怎么分层」。
 * 存的是一条**完整路径**而不是「末段」：嵌套字段（``llm.config.api_key``）
 * 的末段（``api_key``）在同一个引擎里可能出现在两个嵌套层下，只留末段会分不清。
 */
const ENTRY_FIELDS = {};
for (const g of SCHEMA.groups) {
  for (const f of g.fields) {
    const parts = f.path.split('.');
    if (parts.length < 3 || !ENGINE_CATEGORIES.includes(parts[0])) continue;
    const key = parts[0] + '.' + parts[1];
    (ENTRY_FIELDS[key] = ENTRY_FIELDS[key] || []).push({ path: f.path, layer: f.layer });
  }
}

/** 一条引擎的字段清单：**表里的层 + 树上的键**（§9 规则 1）。
 *
 * 两个来源各管一段：表说「这个键属于常用还是更多设置」，树说「这个键存不存在」。
 * 树上有而表里没列的键（本机自加引擎的新字段、上游新增的键）照样渲染——
 * 控件形状由 ``kindFor`` 按值推。反之表里有而树上没有的键**不渲染**：
 * 引擎库必须看见的是「这个引擎实际装成了什么样」，不是「方案列过什么」。
 *
 * 嵌套块（``Memory.powermem.llm.config.api_key``、``TTS.CustomTTS.params.speed``）：
 * 表里的路径是全路径，这里按「**表层里最长前缀 + 剩余段**」把它展平——
 * 展平是必须的，不然 ``powermem`` 的 11 个字段一个都看不见，而「450 全量可达」
 * 的验收就只剩一句口号。
 */
function fieldsOf(cat, name) {
  const declared = ENTRY_FIELDS[cat + '.' + name] || [];
  const owner = getPath(state, cat + '.' + name);
  const out = [];
  if (owner && typeof owner === 'object' && !Array.isArray(owner)) {
    collectFields(owner, `${cat}.${name}`, `${cat}.${name}`, declared, out);
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

/** 一条引擎卡（``<details>``）。
 *
 * 折叠态 DOM 字段数 = 0：体在 ``toggle`` 时构建，初始展开的（当前生效那条）
 * 才立即构建。这不是优化，是「深度 ≤3」验收判据的实现前提。
 */
function engineCard(cat, name, opts) {
  opts = opts || {};
  const live = getPath(state, 'selected_module.' + cat) === name;
  const owner = getPath(state, cat + '.' + name) || {};
  const fields = fieldsOf(cat, name);
  const engineDirty = Object.keys(DIRTY).some(
    (p) => p === `${cat}.${name}` || p.startsWith(`${cat}.${name}.`));
  const catPill = opts.showCat
    ? `<span class="catpill" data-cat="${esc(cat)}">${esc(cat)}</span>` : '';
  const id = `eng-${cat}-${name}`;
  return `<details class="engine${live ? ' live' : ''}${engineDirty ? ' dirty' : ''}"
    id="${esc(id)}" data-engine="${esc(cat)}.${esc(name)}" ${live ? 'open' : ''}>
    <summary>${catPill}<span class="nm">${esc(name)}</span>
      <span class="ty">${esc(owner.type ?? '?')}</span>
      <span class="nf">${fields.length} 字段</span>
      ${live ? '<span class="live-tag">● 生效中</span>' : ''}
      <span class="dirtydot" ${engineDirty ? '' : 'hidden'}></span></summary>
    <div class="engbody" data-built="0" data-cat="${esc(cat)}"
      data-name="${esc(name)}"></div>
  </details>`;
}

/** 填一个引擎体的控件（延迟到 ``toggle``）。 */
function fillEngineBody(box) {
  if (box.dataset.built === '1') return;
  const cat = box.dataset.cat;
  const name = box.dataset.name;
  const fields = fieldsOf(cat, name);
  const common = fields.filter((f) => f.layer !== 'more');
  const more = fields.filter((f) => f.layer === 'more');
  const moreBlock = more.length
    ? `<details class="more-settings"><summary>更多设置 · ${more.length} 项</summary>
        <div>${more.map(row).join('')}</div></details>`
    : '';
  box.innerHTML = (common.length ? common.map(row).join('')
    : (more.length ? '' : '<div class="hint">这条引擎在配置里没有字段。</div>'))
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

/** 重建引擎库的**外框**（tab 栏 + 当前卡列表）。卡体是按需填充的。 */
function renderEngineLibrary() {
  const body = $('domainBody');
  body.classList.remove('placeholder');

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

  // ---- 下方：全部引擎（类目 tab + 跨类目搜索 + 折叠） ----
  const q = QUERY.trim().toLowerCase();
  // 命中谓词只许有一份：命中数与「分布在 X / Y」文案共用它，写两遍就会
  // 出现「匹配 3 条 —— 分布在 0 个类目」这种自相矛盾的输出（两把尺子）。
  const hitOf = (cat, name) => {
    const ty = String(getPath(state, `${cat}.${name}.type`) ?? '');
    return name.toLowerCase().includes(q) || ty.toLowerCase().includes(q);
  };
  let cards = '';
  let note = '';
  if (q) {
    // 搜索**必须跨类目**（§5.1）。命中项带类目标签。
    const hits = [];
    const hitCats = [];
    for (const cat of ENGINE_CATEGORIES) {
      const matched = enginesOf(cat).filter((n) => hitOf(cat, n));
      if (matched.length) hitCats.push(cat);
      for (const name of matched) hits.push(engineCard(cat, name, { showCat: true }));
    }
    cards = hits.length ? hits.join('')
      : `<div class="hint" style="padding:15px 0">无匹配引擎（全部 ${ENGINE_CATEGORIES.length} 个类目里都找不到）</div>`;
    note = `搜索态（<b>跨全部 ${ENGINE_CATEGORIES.length} 个类目</b>）：匹配 <b>${hits.length}</b> 条`
      + (hitCats.length ? ` —— 分布在 ${hitCats.join(' / ')}` : '') + '。';
  } else {
    if (!CUR_CAT) CUR_CAT = ENGINE_CATEGORIES[0];
    cards = enginesOf(CUR_CAT).map((n) => engineCard(CUR_CAT, n)).join('')
      || '<div class="hint" style="padding:15px 0">这个类目下没有引擎。</div>';
    const nEntries = enginesOf(CUR_CAT).length;
    const nFields = enginesOf(CUR_CAT)
      .reduce((n, name) => n + fieldsOf(CUR_CAT, name).length, 0);
    note = `类目 <b>${esc(CUR_CAT)}</b>：<b>${nEntries}</b> 条引擎、`
      + `<b>${nFields}</b> 个字段。折叠态下 DOM 里字段数 = 0，展开才渲染。`;
  }

  body.innerHTML = slots + globalsBlock
    + `<section class="group" id="all-engines"><h2>📚 全部引擎</h2>
        <div class="desc">按类目浏览，或搜索（搜索**跨全部类目**，命中项带类目标签）。</div>
        <div class="catbar" id="catbar">${ENGINE_CATEGORIES.map((cat) =>
          `<button type="button" class="cattab${cat === CUR_CAT && !q ? ' on' : ''}"
            data-cat="${esc(cat)}">${esc(cat)}</button>`).join('')}</div>
        <input class="search" id="engSearch" type="search" value="${esc(QUERY)}"
          placeholder="🔍 搜索引擎名或 type…（例：mlx、doubao、openai）">
        <div class="libnote">${note}</div>
        <div class="lib" id="lib">${cards}</div>
      </section>`;
  bindEngineLibrary();
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
    <div class="meta">${esc((owner && owner.type) ?? '?')} · ${fieldsOf(cat, cur).length} 字段</div>
    ${d ? `<span class="badge" style="background:rgba(124,92,255,.18);color:#a78bfa">[选择] 改自 ${esc(d.from ?? '（无）')}</span>` : ''}
  </div>`;
}

function bindEngineLibrary() {
  document.querySelectorAll('.cattab').forEach((btn) => {
    btn.addEventListener('click', () => {
      CUR_CAT = btn.dataset.cat;
      QUERY = '';
      renderEngineLibrary();
      applyHash();
    });
  });
  const search = $('engSearch');
  if (search) {
    search.addEventListener('input', () => {
      QUERY = search.value;
      renderEngineLibrary();
      // 重建会把焦点弄丢；搜索框是连续输入的控件，焦点必须留在原地。
      const again = $('engSearch');
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
      renderEngineLibrary();
    });
  });
}

/* ---------------------------------------------------------------------------
 * 页级渲染
 * ------------------------------------------------------------------------ */

function render() {
  if (IS_ENGINE_DOMAIN) { renderEngineLibrary(); return; }
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

function currentSelection() {
  const sel = {};
  for (const cat of ENGINE_CATEGORIES) {
    const v = getPath(state, 'selected_module.' + cat);
    if (v !== undefined) sel[cat] = v;
  }
  return sel;
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
  if (!IS_ENGINE_DOMAIN) return;
  document.querySelectorAll('details.engine').forEach((det) => {
    const key = det.dataset.engine || '';
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
  });
  for (const cat of ENGINE_CATEGORIES) {
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
  const groups = groupDirty(DIRTY, currentSelection(), ENGINE_CATEGORIES);
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
    openCards.push(d.dataset.engine);
  });
  const openMore = [];
  document.querySelectorAll('details.more-settings[open]').forEach((d) => {
    const card = d.closest('details.engine');
    if (card) openMore.push(card.dataset.engine);
  });
  render();
  for (const key of openCards) {
    const det = document.querySelector(`details.engine[data-engine="${key}"]`);
    if (det) det.open = true;
  }
  for (const key of openMore) {
    const det = document.querySelector(`details.engine[data-engine="${key}"]`);
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

async function restartServer() {
  if (!window.confirm('确认重启服务？所有设备连接会断开，约 10-30 秒恢复。')) return;
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

init();
