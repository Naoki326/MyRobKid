#!/usr/bin/env node
/* ============================================================================
 * test_config_state_model.mjs — 配置页状态模型缝（issue #25，两道测试缝之一）
 *
 * 被测对象：server/config/config_state_model.js（**无 DOM 依赖**的纯模块）。
 *
 * 为什么单列一条缝：父 spec §5.5/§5.6 的两个已知显示 bug 都长在状态模型里
 * （「页面把服务端掩码/注入后的显示态当成配置里真实存在的值」），而 DOM 渲染
 * 管不着它们。本票按 spec 要求把纯逻辑抽出来、用 node 断言跑 —— 这批判定来自
 * #17 原型的 34/34 断言（形状：{ 路径, kind: 参数|选择|密钥, from → to }）。
 *
 * 判别力（本文件最易糊弄处）：
 *   1. 密钥第三态必须算脏 —— 两态建模会漏检「已配置但要替换」，
 *      用户在输入框打了新密钥、保存按钮却是灰的；
 *   2. 注入默认值**不得**进配置树 —— 写进去就等于页面在撒谎；
 *   3. 生效分组随选中**实时重算** —— 切选中后旧改动要升降级。
 *
 * 不做的事：不测 DOM（折叠、确认层、搜索命中按 spec 不自动化）。
 *
 * 运行：node --test server/tests/test_config_state_model.mjs
 * ========================================================================== */

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DIRTY_KIND,
  RUNTIME_DEFAULT_PATHS,
  classifyDirty,
  computeDirty,
  dirtyLabel,
  displayValue,
  effectiveDefault,
  groupDirty,
  initialState,
  isConfiguredSecret,
  isPlaceholderSecret,
  isSensitiveKey,
  placeholderText,
  setSecretInput,
  setSelection,
  setValue,
} from '../config/config_state_model.js';

/* ---------------------------------------------------------------------------
 * 夹具：一棵「有真实密钥 + 有模板占位符 + 有未配置键」的配置，外加一个
 * 未配 reasoning_effort 的 openai 引擎（复刻 6 条 openai 注入 bug 的形状）。
 * ------------------------------------------------------------------------ */
function fixture() {
  return {
    config: {
      server: { ip: '0.0.0.0', port: 8002 },
      selected_module: { LLM: 'ThirkingLLM', TTS: 'MlxTTS' },
      LLM: {
        ThirkingLLM: {
          type: 'openai',
          model_name: 'deepseek-v4.1-flash',
          api_key: 'sk-a****7890',
          max_tokens: 1024,
        },
        DoubaoLLM: {
          type: 'openai',
          model_name: 'doubao-seed',
          api_key: '你的doubao web key',
        },
      },
      TTS: { MlxTTS: { type: 'mlx', url: 'http://127.0.0.1:9753/tts' } },
    },
    config_state: {
      'LLM.ThirkingLLM.api_key': { configured: true },
      'LLM.DoubaoLLM.api_key': { configured: false },
    },
  };
}

/** 起点页面（选中 = ThirkingLLM）。 */
function page() {
  const p = initialState(fixture());
  return { ...p, selection: { ...p.cfg.selected_module }, origSelection: { ...p.cfg.selected_module } };
}

/* ---------------------------------------------------------------------------
 * 1. 敏感判定（isSens）
 * ------------------------------------------------------------------------ */
test('敏感键按键名判定，不靠掩码形态', () => {
  assert.equal(isSensitiveKey('api_key'), true);
  assert.equal(isSensitiveKey('access_token'), true);
  assert.equal(isSensitiveKey('secret_key'), true);
  assert.equal(isSensitiveKey('personal_access_token'), true);
  assert.equal(isSensitiveKey('server.auth_key'), true, '完整路径也认（看末段）');
  assert.equal(isSensitiveKey('auth_key'), true);
  assert.equal(isSensitiveKey('mqtt_signature_key'), true, '_key 后缀命中');
  assert.equal(isSensitiveKey('base_url'), false);
  assert.equal(isSensitiveKey('model_name'), false);
  assert.equal(isSensitiveKey('max_tokens'), false, '_tokens 后缀不是 _token');
  assert.equal(isSensitiveKey(''), false);
  assert.equal(isSensitiveKey(null), false);
  // 大小写不敏感：与 python 侧 _is_sensitive_key 同一把尺，两边不允许收窄出差异。
  assert.equal(isSensitiveKey('Authorization'), true, '大写形态同样是敏感键');
  assert.equal(isSensitiveKey('context_providers.headers.Authorization'), true);
  assert.equal(isSensitiveKey('API_KEY'), true);
});

test('掩码形态本身不构成「敏感」，也不构成「已配置」', () => {
  // 两个反例：都是掩码形态的字符串，但 configured 判据看的是原值。
  assert.equal(isConfiguredSecret('********'), true, '非空串算有值（原值层面）');
  assert.equal(isConfiguredSecret('你的deepseek web key'), false, '模板占位符不算');
  assert.equal(isConfiguredSecret(''), false);
  assert.equal(isConfiguredSecret(null), false);
  assert.equal(isPlaceholderSecret('你的api_key'), true);
  assert.equal(isPlaceholderSecret('sk-real'), false);
});

test('数组内的敏感键也收集存在信号（与 python 侧同一遍历口径）', () => {
  // 口径一旦不一致，就会出现「有掩码值、无信号」的键，页面回落判成「已配置」，
  // 正是本票要根除的那个谎报的另一条路径。
  // 路径必须是页面 getPath 认识的点号形态（context_providers.0...），不是 [0]。
  const payload = {
    config: {
      context_providers: [
        { name: 'a', api_key: '你的api_key' },
        { name: 'b', api_key: 'sk-a-real-secret' },
      ],
    },
    config_state: {
      'context_providers.0.api_key': { configured: false },
      'context_providers.1.api_key': { configured: true },
    },
  };
  const p = initialState(payload);
  assert.equal(
    p.secrets['context_providers.0.api_key'].configured, false,
    '数组内占位符 → 未配置（路径必须是页面点号形态）');
  assert.equal(
    p.secrets['context_providers.1.api_key'].configured, true,
    '数组内真密钥 → 已配置');
});

/* ---------------------------------------------------------------------------
 * 2. 初始状态：存在信号是唯一判据（两个 bug 的修法）
 * ------------------------------------------------------------------------ */
test('已配置密钥：信号 true → 已配置', () => {
  const p = page();
  assert.equal(p.secrets['LLM.ThirkingLLM.api_key'].configured, true);
  assert.equal(p.secrets['LLM.ThirkingLLM.api_key'].input, '');
});

test('模板占位符：信号 false → 未配置（bug 1 的最小复现）', () => {
  const p = page();
  assert.equal(
    p.secrets['LLM.DoubaoLLM.api_key'].configured, false,
    '占位符掩码后同样是 ********，靠掩码猜必然误判「已配置」');
});

test('服务端没给信号时回落判原值，仍不看掩码形态', () => {
  const payload = fixture();
  delete payload.config_state;
  const p = initialState(payload);
  assert.equal(p.secrets['LLM.ThirkingLLM.api_key'].configured, true);
  assert.equal(p.secrets['LLM.DoubaoLLM.api_key'].configured, false);
  // 真接入 `********` 这种纯掩码字符串同样不算已配置？——它非空、非占位符，
  // 在「原值层面」算有值。行为到这里为止，不引入第三种猜测。
  assert.equal(isConfiguredSecret('********'), true);
});

/* ---------------------------------------------------------------------------
 * 3. 显式惰性：注入默认值只活在显示层
 * ------------------------------------------------------------------------ */
test('未配置的 openai 引擎 reasoning_effort：显示注入态，不落进配置树', () => {
  const p = page();
  const d = displayValue(p.cfg, 'LLM.DoubaoLLM.reasoning_effort');
  assert.equal(d.state, 'injected');
  assert.equal(d.value, 'medium');
  assert.equal(
    p.cfg.LLM.DoubaoLLM.reasoning_effort, undefined,
    '注入值绝不能写进 cfg —— 写进去就是「页面把显示态当事实态」');
});

test('注入态文案显式承认「这里没有值」', () => {
  const p = page();
  assert.equal(
    placeholderText(p.cfg, 'LLM.DoubaoLLM.reasoning_effort'),
    '未设置（运行时默认 medium）');
  assert.equal(placeholderText(p.cfg, 'LLM.ThirkingLLM.some_unset'), '（未设置）');
});

test('配置里真的有值时不显示注入文案', () => {
  const p = page();
  p.cfg.LLM.ThirkingLLM.reasoning_effort = 'high';
  const d = displayValue(p.cfg, 'LLM.ThirkingLLM.reasoning_effort');
  assert.equal(d.state, 'set');
  assert.equal(d.value, 'high');
});

test('非 openai 引擎不享受注入默认值', () => {
  const p = page();
  p.cfg.LLM.LocalOllama = { type: 'ollama' };
  assert.equal(displayValue(p.cfg, 'LLM.LocalOllama.reasoning_effort').state, 'unset');
});

test('运行时默认值表按路径模式命中', () => {
  assert.equal(effectiveDefault('LLM.AnyEngine.reasoning_effort').value, 'medium');
  assert.equal(effectiveDefault('TTS.MlxTTS.speed'), undefined);
  assert.equal(effectiveDefault('reasoning_effort'), undefined, '模式要整段匹配');
});

test('注入条件被声明（type=openai），供页面补注入槽', () => {
  assert.equal(
    effectiveDefault('LLM.DoubaoLLM.reasoning_effort').condition, 'type=openai');
  assert.ok(Object.keys(RUNTIME_DEFAULT_PATHS).includes('LLM.*.reasoning_effort'),
    '页面需要具体模式表才能给未配置字段留槽位');
});

/* ---------------------------------------------------------------------------
 * 4. 脏计算：密钥三态 / 参数 / 新增 / 删除 / 选择
 * ------------------------------------------------------------------------ */
test('未改动 → 无脏条目', () => {
  assert.deepEqual(computeDirty(page()), {});
});

test('密钥三态之一：未配置，不动 → 不脏', () => {
  const p = page();
  const d = computeDirty(p);
  assert.equal(d['LLM.DoubaoLLM.api_key'], undefined);
});

test('密钥三态之二：已配置，不动 → 不脏', () => {
  const d = computeDirty(page());
  assert.equal(d['LLM.ThirkingLLM.api_key'], undefined);
});

test('密钥三态之三：已配置但要替换 → 必须算脏（两态建模会漏检）', () => {
  const p = setSecretInput(page(), 'LLM.ThirkingLLM.api_key', 'sk-brand-new');
  const d = computeDirty(p);
  assert.ok(d['LLM.ThirkingLLM.api_key'], '第三态必须进脏列表');
  assert.equal(d['LLM.ThirkingLLM.api_key'].kind, DIRTY_KIND.SECRET);
  assert.equal(d['LLM.ThirkingLLM.api_key'].to, '替换为新值');
  assert.equal(
    dirtyLabel(d['LLM.ThirkingLLM.api_key']),
    '[密钥] 已配置（掩码） → 替换为新值');
});

test('密钥脏条目永不显示值', () => {
  const p = setSecretInput(page(), 'LLM.ThirkingLLM.api_key', 'sk-super-secret');
  const label = dirtyLabel(computeDirty(p)['LLM.ThirkingLLM.api_key']);
  assert.ok(!label.includes('sk-super-secret'), '密钥值绝不外泄到脏列表');
});

test('未配置密钥填新值 → 也是脏（从无到有）', () => {
  const p = setSecretInput(page(), 'LLM.DoubaoLLM.api_key', 'sk-first-time');
  const d = computeDirty(p);
  assert.equal(d['LLM.DoubaoLLM.api_key'].kind, DIRTY_KIND.SECRET);
  assert.equal(d['LLM.DoubaoLLM.api_key'].from, '未配置');
});

test('参数改动 → kind=param，带 from → to', () => {
  const p = setValue(page(), 'LLM.ThirkingLLM.max_tokens', 2048);
  const d = computeDirty(p);
  assert.equal(d['LLM.ThirkingLLM.max_tokens'].kind, DIRTY_KIND.PARAM);
  assert.equal(d['LLM.ThirkingLLM.max_tokens'].from, 1024);
  assert.equal(d['LLM.ThirkingLLM.max_tokens'].to, 2048);
  assert.equal(dirtyLabel(d['LLM.ThirkingLLM.max_tokens']), '1024 → 2048');
});

test('新增键 → kind=added', () => {
  const p = setValue(page(), 'LLM.ThirkingLLM.top_p', 0.9);
  const d = computeDirty(p);
  assert.equal(d['LLM.ThirkingLLM.top_p'].kind, DIRTY_KIND.ADDED);
  assert.equal(d['LLM.ThirkingLLM.top_p'].from, '（无）');
});

test('删除键 → 算脏（否则「清空输入」丢不掉旧值）', () => {
  const p = page();
  delete p.state.LLM.ThirkingLLM.max_tokens;
  const d = computeDirty(p);
  assert.equal(d['LLM.ThirkingLLM.max_tokens'].kind, DIRTY_KIND.REMOVED);
});

test('选择切换单独成组，kind=select，带 [选择] 前缀', () => {
  const p = setSelection(page(), 'LLM', 'DoubaoLLM');
  const d = computeDirty(p);
  assert.ok(d['selected_module.LLM']);
  assert.equal(d['selected_module.LLM'].kind, DIRTY_KIND.SELECT);
  assert.equal(
    dirtyLabel(d['selected_module.LLM']), '[选择] ThirkingLLM → DoubaoLLM');
});

test('切回原值 = 该条脏自动消失（与整树 diff 天然相容）', () => {
  let p = setSelection(page(), 'LLM', 'DoubaoLLM');
  assert.ok(computeDirty(p)['selected_module.LLM']);
  p = setSelection(p, 'LLM', 'ThirkingLLM');
  assert.equal(computeDirty(p)['selected_module.LLM'], undefined);
});

/* ---------------------------------------------------------------------------
 * 5. 生效分组：随选中实时重算
 * ------------------------------------------------------------------------ */
test('当前选中引擎的字段 → 重启后生效', () => {
  assert.equal(classifyDirty('LLM.ThirkingLLM.max_tokens', { LLM: 'ThirkingLLM' }), 'active');
});

test('未选中引擎的字段 → 仅提前配好', () => {
  assert.equal(classifyDirty('LLM.DoubaoLLM.max_tokens', { LLM: 'ThirkingLLM' }), 'staged');
});

test('selected_module.* 永远算重启后生效', () => {
  assert.equal(classifyDirty('selected_module.LLM', { LLM: 'ThirkingLLM' }), 'active');
});

test('分组随选中实时重算：切选中让旧改动升降级', () => {
  const p = setValue(page(), 'LLM.DoubaoLLM.max_tokens', 512);
  const dirty = computeDirty(p);
  let g = groupDirty(dirty, { LLM: 'ThirkingLLM' });
  assert.equal(g.staged.length, 1, '未选中 → 仅提前配好');
  assert.equal(g.active.length, 0);

  // 把选中切到 DoubaoLLM：同一条脏升入「重启后生效」。
  const promoted = groupDirty(dirty, { LLM: 'DoubaoLLM' });
  assert.equal(promoted.active.length, 1);
  assert.equal(promoted.staged.length, 0);
  assert.equal(promoted.active[0].path, 'LLM.DoubaoLLM.max_tokens');
});

test('两个分组的组名沿用 spec 措辞', () => {
  const g = groupDirty({}, {});
  assert.deepEqual(Object.keys(g).sort(), ['active', 'staged']);
});

/* ---------------------------------------------------------------------------
 * 6. 未启用引擎可编辑、可保存、计入脏（§5.2）
 * ------------------------------------------------------------------------ */
test('未启用引擎的字段改动计入脏标记（不是静默丢弃）', () => {
  const p = setValue(page(), 'LLM.DoubaoLLM.model_name', 'doubao-pro');
  const d = computeDirty(p);
  assert.ok(d['LLM.DoubaoLLM.model_name']);
});

test('未启用引擎的密钥替换同样算脏', () => {
  const p = setSecretInput(page(), 'LLM.DoubaoLLM.api_key', 'sk-x');
  assert.ok(computeDirty(p)['LLM.DoubaoLLM.api_key']);
});

/* ---------------------------------------------------------------------------
 * 7. 纯函数性：动作不修改入参（页面 React 化 / 重算的前提）
 * ------------------------------------------------------------------------ */
test('编辑动作返回新状态，不改原对象', () => {
  const p = page();
  const snapshot = JSON.stringify(p);
  setValue(p, 'LLM.ThirkingLLM.max_tokens', 2048);
  setSecretInput(p, 'LLM.ThirkingLLM.api_key', 'x');
  setSelection(p, 'LLM', 'DoubaoLLM');
  assert.equal(JSON.stringify(p), snapshot, '纯函数不得改入参');
});
