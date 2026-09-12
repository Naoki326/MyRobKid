/* ============================================================================
 * config_state_model.js — 配置页的纯状态模型（无 DOM 依赖）
 *
 * 本模块是配置页「显示态地基」的可测试核心（issue #25，父 spec §5.5/§5.6/§9）：
 *
 *   1. 敏感判定（isSensitiveKey）——键名一把尺，页面与元信息表共用；
 *   2. 初始状态（initialState）——api/full 的 {config, config_state} 变成页面
 *      编辑状态；默认值注入是**显式惰性**的（effectiveDefault 只活在显示层）；
 *   3. 脏计算（computeDirty）——密钥三态、选择切换单独成组、加入/删除；
 *   4. 生效分组（classifyDirty）——重启后生效 / 仅提前配好，随选中实时重算。
 *
 * 为什么抽成无 DOM 模块：父 spec §5.6 的教训是「页面显示值 ≠ 配置里存在的值」。
 * 这条陷阱只能靠**状态模型**挡住，不能靠在渲染代码里小心。模块无 DOM 依赖，
 * 用 node:test 直接跑（两道测试缝之一，见 server/tests/test_config_state_model.mjs）。
 *
 * 词义沿用 CONTEXT.md 服务端段：**脏标记**、**引擎库**。
 * ========================================================================== */

/** 敏感键名白名单（与 python 侧 SENSITIVE_KEYS 保持一致）。 */
export const SENSITIVE_KEYS = [
  'api_key', 'access_token', 'secret_key', 'secret_id', 'app_secret',
  'token', 'password', 'authorization', 'auth_key', 'personal_access_token',
  'api_secret', 'private_key', 'config_pin',
];

/** 键名是否敏感。页面与元信息表共用这把尺，不允许各自另立一套。
 *
 * 判据看**末段键名**：页面有时传完整路径（``LLM.X.api_key``）、有时传裸键名
 * （``api_key``），两种调用都得给出同一个答案。
 */
export function isSensitiveKey(key) {
  if (typeof key !== 'string' || key === '') return false;
  const bare = key.includes('.') ? key.split('.').pop() : key;
  if (bare === '') return false;
  // 大小写不敏感：``headers.Authorization`` 与 ``authorization`` 是同一个东西。
  // 与 python 侧 _is_sensitive_key 同一把尺，两边不允许收窄出差异。
  const lowered = bare.toLowerCase();
  return SENSITIVE_KEYS.includes(lowered)
    || lowered.endsWith('_key')
    || lowered.endsWith('_secret')
    || lowered.endsWith('_token');
}

/** 模板占位符（「你的xxx」）：形如密钥但不是真值。 */
export function isPlaceholderSecret(value) {
  return typeof value === 'string' && value.includes('你');
}

/** 这个敏感值算「已配置」吗——判据是原值，不是掩码形态。 */
export function isConfiguredSecret(value) {
  if (value === null || value === undefined) return false;
  if (typeof value !== 'string') return true;
  if (value === '') return false;
  return !isPlaceholderSecret(value);
}

/* ---------------------------------------------------------------------------
 * 初始状态
 * ------------------------------------------------------------------------ */

/**
 * 把 ``api/full`` 的响应变成页面的编辑状态。
 *
 * @param {{config: object, config_state?: object}} payload
 * @returns {{cfg, orig, state, origState}}
 *   - ``cfg``：**配置树上真实存在**的值（未注入）——脏计算的基准；
 *   - ``state``：页面当前编辑值（初始等于 cfg）；
 *   - ``origState``：服务端给的存在信号（路径 → {configured}），
 *     密钥三态的唯一判据来源。
 *
 * 关键：``cfg`` 只含配置里**真的有**的键。默认值注入不写进 cfg ——
 * 它由 ``effectiveDefault`` 在显示层提供，这样页面才敢说「未设置」。
 */
export function initialState(payload) {
  const config = (payload && payload.config) || {};
  const configState = (payload && payload.config_state) || {};
  const cfg = deepClone(config);
  return {
    cfg,
    state: deepClone(cfg),
    origState: deepClone(configState),
    secrets: initialSecrets(cfg, configState),
  };
}

/**
 * 密钥控件的初始状态：三态的起点。
 *
 * 第三态（已配置但要替换）在用户往输入框里打字时产生，见 ``setSecretInput``。
 * ``configured`` 取自服务端信号；信号缺失时回落到「原值算不算已配置」，
 * 但**绝不**看掩码形态（``********`` 既可能是真密钥也可能是占位符）。
 */
export function initialSecrets(cfg, configState, prefix = '', out = {}) {
  if (Array.isArray(cfg)) {
    // 与 python 侧 _secret_state / _mask_tree 同一遍历口径，路径用
    // **页面 getPath 认识的点号语义**（``context_providers.0.headers.X``）：
    // 信号必须能被消费方真的查到，否则等于没给。
    cfg.forEach((item, i) => {
      if (item && typeof item === 'object') initialSecrets(item, configState, `${prefix}.${i}`, out);
    });
    return out;
  }
  if (cfg && typeof cfg === 'object') {
    for (const [k, v] of Object.entries(cfg)) {
      const path = prefix ? `${prefix}.${k}` : String(k);
      if (v && typeof v === 'object') {
        initialSecrets(v, configState, path, out);
      } else if (isSensitiveKey(k)) {
        out[path] = {
          kind: 'secret',
          configured: signalFor(configState, path, v),
          input: '',
        };
      }
    }
  }
  return out;
}

function signalFor(configState, path, value) {
  const cell = configState ? configState[path] : undefined;
  if (cell && typeof cell === 'object' && 'configured' in cell) {
    return Boolean(cell.configured);
  }
  // 服务端没给信号（旧接口 / 未知键）：用原值判，不看掩码形态。
  return isConfiguredSecret(value);
}

/* ---------------------------------------------------------------------------
 * 显式惰性：运行时默认值只活在显示层
 * ------------------------------------------------------------------------ */

/**
 * 这个字段的「运行时默认值」（注入值），只在**配置里没有这个键**时生效。
 *
 * 父 spec §5.6：未配置的引擎**不显示**注入默认值；显示空值 + 占位符
 * 「未设置（运行时默认 X）」+ 注入标记。所以注入值只出现在这里，
 * 绝不写进 ``cfg`` —— 写进去就是「页面把显示态当事实态」。
 */
export const RUNTIME_DEFAULTS = {
  // openai 类型 LLM 未配推理级别时，运行时补 medium（原 applyLlmDefaults 的真相）。
  'LLM.*.reasoning_effort': 'medium',
};

/** 按路径模式取运行时默认值（无匹配返回 undefined）。 */
export function effectiveDefault(path) {
  for (const [pattern, value] of Object.entries(RUNTIME_DEFAULTS)) {
    if (pathMatches(pattern, path)) {
      if (parts2(pattern)[0] === 'LLM' && parts2(path)[2] === 'reasoning_effort') {
        return { value, condition: 'type=openai' };
      }
      return { value };
    }
  }
  return undefined;
}

function parts2(p) {
  return String(p).split('.');
}

/** 路径是否匹配模式（`*` 为单段通配）。 */
function pathMatches(pattern, path) {
  const pp = parts2(pattern);
  const parts = parts2(path);
  if (pp.length !== parts.length) return false;
  for (let i = 0; i < pp.length; i++) {
    if (pp[i] !== '*' && pp[i] !== parts[i]) return false;
  }
  return true;
}

/** 运行时默认值的**具体路径模式**表（供页面给未配置字段留注入槽）。 */
export const RUNTIME_DEFAULT_PATHS = { ...RUNTIME_DEFAULTS };

/**
 * 页面上这个字段该显示什么。
 *
 * 返回值三态：
 *   - ``{state:'set', value}``          —— 配置里真的有值；
 *   - ``{state:'injected', value}``     —— 未配置，但运行时会有默认值（显式惰性）；
 *   - ``{state:'unset'}``               —— 未配置且无默认值。
 */
export function displayValue(cells, path) {
  const own = ownValue(cells, path);
  if (own !== undefined && own !== null && own !== '') {
    return { state: 'set', value: own };
  }
  const injected = effectiveDefault(path);
  if (injected && containerIsOpenai(cells, path)) {
    return { state: 'injected', value: injected.value };
  }
  return { state: 'unset' };
}

/** 注入条件：所在引擎块 type=openai（原 applyLlmDefaults 的判据）。
 *
 * 路径形状是 ``LLM.<引擎名>.<字段>``（引擎块路径 = 去掉末段），所以只要
 * 字段属于某个 openai 型的 LLM/VLLM 块就算。
 */
function containerIsOpenai(cells, path) {
  const parts = String(path).split('.');
  if (parts.length < 3) return false;
  const owner = getPath(cells, parts.slice(0, -1).join('.'));
  return Boolean(owner) && typeof owner === 'object' && owner.type === 'openai';
}

/** 页面显示文案：注入态必须说清「这里没有值」。 */
export function placeholderText(cells, path) {
  const d = displayValue(cells, path);
  if (d.state === 'injected') return `未设置（运行时默认 ${d.value}）`;
  return '（未设置）';
}

/* ---------------------------------------------------------------------------
 * 脏计算：单一事实源
 * ------------------------------------------------------------------------ */

export const DIRTY_KIND = {
  PARAM: 'param',       // 参数
  SELECT: 'select',     // 选择
  SECRET: 'secret',     // 密钥
  ADDED: 'added',       // 新增键
  REMOVED: 'removed',   // 删除键
};

/**
 * 脏路径集合。key = 完整路径，value = ``{kind, from, to}``。
 *
 * 密钥是**三态**而非两态（父 spec §5.5）——第三态「已配置但要替换」必须算脏，
 * 否则用户在输入框里打了新密钥、页面说「没有未保存修改」，保存按钮还是灰的。
 */
export function computeDirty(page) {
  const out = {};
  walkDirty(page.cfg, page.state, '', out, page);
  // 删除的键（cfg 有、state 没有）。
  collectRemoved(page.cfg, page.state, '', out);

  // 选择切换单独成组：它是意图陈述，不是参数调整。
  for (const [cat, name] of Object.entries(page.selection || {})) {
    const before = (page.origSelection || {})[cat];
    if (name !== before) {
      out[`selected_module.${cat}`] = {
        kind: DIRTY_KIND.SELECT, from: before, to: name,
      };
    }
  }
  return out;
}

function walkDirty(orig, cur, base, out, page) {
  if (!cur || typeof cur !== 'object') return;
  for (const [k, v] of Object.entries(cur)) {
    const path = base ? `${base}.${k}` : k;
    const ov = orig ? orig[k] : undefined;
    if (isSensitiveKey(k)) {
      const secret = secretCell(page, path);
      const wasConfigured = secret ? secret.wasConfigured : false;
      const nowInput = secret ? (secret.input || '') : '';
      if (nowInput !== '') {
        // 第三态：不管原本配没配，输入了新值就是要替换 → 算脏。
        out[path] = {
          kind: DIRTY_KIND.SECRET,
          from: wasConfigured ? '已配置（掩码）' : '未配置',
          to: '替换为新值',
        };
      }
      continue;
    }
    if (v && typeof v === 'object' && !Array.isArray(v)
        && ov && typeof ov === 'object' && !Array.isArray(ov)) {
      walkDirty(ov, v, path, out, page);
    } else if (ov === undefined) {
      out[path] = { kind: DIRTY_KIND.ADDED, from: '（无）', to: v };
    } else if (!deepEqual(ov, v)) {
      out[path] = { kind: DIRTY_KIND.PARAM, from: ov, to: v };
    }
  }
}

function collectRemoved(orig, cur, base, out) {
  if (!orig || typeof orig !== 'object') return;
  for (const [k, v] of Object.entries(orig)) {
    const path = base ? `${base}.${k}` : k;
    const cv = cur ? cur[k] : undefined;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      collectRemoved(v, cv, path, out);
    } else if (cv === undefined) {
      out[path] = {
        kind: isSensitiveKey(k) ? DIRTY_KIND.SECRET : DIRTY_KIND.REMOVED,
        from: isSensitiveKey(k) ? '已配置' : v,
        to: isSensitiveKey(k) ? '未配置' : '（无）',
      };
    }
  }
}

/**
 * 脏条目的**生效性**分类：这条改动重启后会不会被采纳？
 *
 * - ``selected_module.*``             → active（它本身就在改写「谁生效」）
 * - ``CAT.name.field``，name 当前选中 → active
 * - ``CAT.name.field``，name 未选中   → staged（只是提前配好）
 * - 非引擎域的散字段（系统域 ``log.*`` / ``server.*``、对话与角色域
 *   ``prompt`` / ``voiceprint.*``…）  → active
 *
 * 最后一条不是新裁决，而是同一句话的推论：#17 把脏分两组是为了区分「重启后
 * 生效」与「仅提前配好·当前不生效」——**后者专指未选中引擎**（配好了但当前
 * 不生效的那类）。域内散字段不存在「不生效」这回事：它们没有「选中」这个状态，
 * 改了就重启生效。把它们一律扫进 staged 会让系统域的每一次修改都被标成
 * 「当前不生效」——那是分类在说谎，不是保守。
 *
 * 分类**随当前选中动态变化**：改了 A 引擎后又把选中切到 B，A 的改动就从
 * 「重启后生效」降级为「仅提前配好」。这是有意为之，且必须可见（§5.4）。
 */
export function classifyDirty(path, selection, engineCategories) {
  if (String(path).startsWith('selected_module.')) return 'active';
  const parts = String(path).split('.');
  // 引擎块路径的形状是 ``<类目>.<引擎名>.<字段>``。只有类目名真的在引擎域
  // 类目集合里（VAD/ASR/LLM/VLLM/TTS/Memory），才谈得上「选中 / 未选中」。
  //
  // 不传类目集合时**逐字保持旧口径**：任何三段路径都当引擎块，其余一律
  // 归 ``staged``（旧页面 config_page.html 的调用形态，不允许静默改行为）。
  // 传了类目集合才按「是否真在引擎域类目里」细化，非引擎域散字段归 ``active``
  // ——它们没有「选中」状态，「仅提前配好」对它不成立。
  if (!engineCategories) {
    const isEnginePath = parts.length >= 3;
    if (isEnginePath) {
      return (selection && selection[parts[0]] === parts[1]) ? 'active' : 'staged';
    }
    return 'staged';
  }
  const isEnginePath = parts.length >= 3 && engineCategories.includes(parts[0]);
  if (isEnginePath) {
    return (selection && selection[parts[0]] === parts[1]) ? 'active' : 'staged';
  }
  return 'active';
}

/** 两个生效分组（组名沿用父 spec §5.4 的措辞）。 */
export const DIRTY_GROUPS = {
  active: '重启后生效',
  staged: '仅提前配好 · 当前不生效',
};

/** 按生效性把脏条目分组（供侧栏/保存栏渲染）。 */
export function groupDirty(dirty, selection, engineCategories) {
  const groups = { active: [], staged: [] };
  for (const [path, entry] of Object.entries(dirty)) {
    const bucket = classifyDirty(path, selection, engineCategories);
    groups[bucket].push({ path, ...entry });
  }
  return groups;
}

/** 脏条目的显示文本（密钥**永不显示值**）。 */
export function dirtyLabel(entry) {
  const prefix = entry.kind === DIRTY_KIND.SELECT ? '[选择] '
    : entry.kind === DIRTY_KIND.SECRET ? '[密钥] ' : '';
  return `${prefix}${entry.from} → ${entry.to}`;
}

/* ---------------------------------------------------------------------------
 * 跨页脏状态（父 spec §4.6）
 *
 * 编辑缓冲（未保存的值）只活在当前页内存；跨页只能传**摘要**——脏条目路径
 * 列表 + 计数。这两条纯函数就是摘要的形状与产出，页面拿它写 localStorage，
 * 侧栏拿它画脏点。
 *
 * 为什么单独成函数而不是让页面顺手写：父 spec 的硬约束是「摘要只含路径与计数、
 * 不含任何值」。顺手写在页面里的话，把 entry.to 也写进去是**最自然**的那行代码
 * （反正已经有了），而密钥新值一旦落进 localStorage 就永远不可能收回。所以
 * 这里用**白名单重建**而不是「过滤掉 from/to」——白名单漏不了后来新增的字段。
 * ------------------------------------------------------------------------ */

/** 脏摘要：只提取路径（顺带排序，跨页比对稳定）。
 *
 * 产出物是纯字符串数组，**没有任何值**——它是 ``localStorage`` 里唯一允许
 * 存在的形状。
 */
export function dirtySummary(dirty) {
  const paths = Object.keys(dirty || {});
  paths.sort();
  return { count: paths.length, paths };
}

/** 按域前缀把脏路径分桶。
 *
 * ``domainForPath`` 是页面注入的判据（域 → 顶层键/完整路径前缀），本模块不
 * 内置域表——域表属于 Python 侧的字段归属（page_domains.py），在这里再抄一份
 * 就是两把尺子，迟早分叉。
 *
 * @param {object} dirty computeDirty 的结果
 * @param {(path: string) => string|undefined} domainForPath 路径 → 域 slug
 * @returns {object} {域 slug: {count, paths:[...]}}，无脏的域**不出现**
 */
export function domainDirty(dirty, domainForPath) {
  const out = {};
  for (const path of Object.keys(dirty || {})) {
    const domain = domainForPath(path);
    if (!domain) continue;
    if (!out[domain]) out[domain] = [];
    out[domain].push(path);
  }
  const result = {};
  for (const [domain, paths] of Object.entries(out)) {
    paths.sort();
    result[domain] = { count: paths.length, paths };
  }
  return result;
}

/* ---------------------------------------------------------------------------
 * 编辑动作（纯函数：返回新状态，不改入参）
 * ------------------------------------------------------------------------ */

/** 密钥输入框打字 → 第三态（已配置但要替换）。 */
export function setSecretInput(page, path, input) {
  const secrets = { ...page.secrets };
  const cur = secrets[path] || { kind: 'secret', configured: false, input: '' };
  secrets[path] = { ...cur, input: String(input), configured: cur.configured };
  return { ...page, secrets };
}

/** 普通字段编辑。 */
export function setValue(page, path, value) {
  const state = deepClone(page.state);
  setPath(state, path, value);
  return { ...page, state };
}

/** 切换选中引擎。 */
export function setSelection(page, cat, name) {
  return { ...page, selection: { ...(page.selection || {}), [cat]: name } };
}

/* ---------------------------------------------------------------------------
 * 工具
 * ------------------------------------------------------------------------ */

export function getPath(obj, path) {
  return String(path).split('.').reduce(
    (o, k) => (o === null || o === undefined ? undefined : o[k]), obj);
}

export function setPath(obj, path, value) {
  const parts = String(path).split('.');
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) {
      cur[parts[i]] = {};
    }
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = value;
  return obj;
}

export function deepClone(o) {
  return JSON.parse(JSON.stringify(o));
}

function deepEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function ownValue(cells, path) {
  return getPath(cells, path);
}

function secretCell(page, path) {
  const s = page.secrets ? page.secrets[path] : undefined;
  if (!s) return undefined;
  return { wasConfigured: s.configured, input: s.input || '' };
}
