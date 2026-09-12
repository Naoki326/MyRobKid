/* ============================================================================
 * config_domain_page.js — 域页的渲染与编辑逻辑（issue #26，父 spec §4/§7/§9）
 *
 * 这一份脚本被五个域页共用（正文不同、骨架同一副）。它做四件事：
 *
 *   1. **按域表渲染**：域表由服务端注入（``#domain-schema``），表里每一行变成
 *      一个控件；域内「常用 / 更多设置」两层（§2.5，更多设置默认折叠）；
 *   2. **按配置树取值**：值从 ``api/full`` 的 ``config`` 拉，渲染依据是配置树
 *      （§9 规则 1）——域表决定「哪些字段有落位」，配置树决定「键存不存在」，
 *      两者各管一段，谁也不替谁撒谎；
 *   3. **脏计算复用状态模型**：``computeDirty`` / ``groupDirty`` /
 *      ``dirtySummary`` / ``domainDirty`` 全来自 config_state_model.js（#25 的
 *      地基）。页面里**没有第二套脏计算**——这是 #17 那两个同源显示 bug 的教训；
 *   4. **跨页脏状态**（§4.6）：把本域脏摘要写进 ``localStorage['config-dirty/<域>']``
 *      （**只含路径与计数、不含任何值**），侧栏读它画点；带脏离开先确认
 *      （``window.__xzhDirtyGuard`` 钩子，壳负责接事件）。
 *
 * 密钥三态（§5.5）沿用 #25 的 ``setSecretInput``：输入**不落配置树**，只进
 * 密钥槽；留空 = 保持原值（不产生任何改动）。
 * ========================================================================== */

import {
  computeDirty, dirtySummary, domainDirty, groupDirty, dirtyLabel,
  getPath, initialState, isSensitiveKey, placeholderText, setPath,
  setSecretInput,
} from './config_state_model.js';

const SCHEMA = JSON.parse(document.getElementById('domain-schema').textContent);
const SLUG = SCHEMA.slug;
const STORAGE_KEY = 'config-dirty/' + SLUG;

/** 引擎域类目——「选中 / 未选中」只对引擎块有意义（见状态模型的 classifyDirty）。
 *
 * 域页不是引擎域，所以本页的脏条目一律属「重启后生效」；但 `selected_module.*`
 * 本身可能出现在脏里，所以这个集合不能省。
 */
const ENGINE_CATEGORIES = ['VAD', 'ASR', 'LLM', 'VLLM', 'TTS', 'Memory'];

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

/** 一个字段的控件。密钥走三态（服务端信号判「已配置」，绝不猜掩码形态）。 */
function control(f) {
  const val = getPath(state, f.path);
  if (f.kind === 'list') {
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
  if (f.kind === 'bool') {
    const on = val === true;
    return `<label class="switch-row"><span class="switch"><input type="checkbox"
      data-path="${esc(f.path)}" ${on ? 'checked' : ''}><span class="sl"></span></span>
      <span class="sw-label">${on ? '开启' : '关闭'}</span></label>`;
  }
  if (f.kind === 'number') {
    const v = (val === undefined || val === null) ? '' : val;
    return `<input type="number" data-path="${esc(f.path)}" value="${esc(v)}"
      ${f.step ? `step="${esc(f.step)}"` : ''}>`;
  }
  const ph = (val === undefined || val === null || val === '')
    ? placeholderText(CONFIG || {}, f.path) : '';
  if (f.kind === 'textarea') {
    return `<textarea data-path="${esc(f.path)}" rows="6"
      placeholder="${esc(ph)}">${esc(val ?? '')}</textarea>`;
  }
  return `<input type="text" data-path="${esc(f.path)}" value="${esc(val ?? '')}"
    placeholder="${esc(ph)}">`;
}

function row(f) {
  return `<div class="row">
    <div class="meta"><div class="label">${esc(f.label)}</div>
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

function render() {
  const body = $('domainBody');
  // §2.5：域内常用层为空时折叠区不渲染、全部字段平铺（系统域就是这样）。
  const domainHasCommon = SCHEMA.groups.some(
    (g) => g.fields.some((f) => f.layer === 'common'));
  body.classList.remove('placeholder');
  body.innerHTML = SCHEMA.groups.map((g) => groupCard(g, domainHasCommon)).join('');
  bindInputs();
}

/* ---------------------------------------------------------------------------
 * 脏状态（跨页摘要写在 localStorage，只含路径与计数）
 * ------------------------------------------------------------------------ */

function currentSelection() {
  const sel = {};
  for (const cat of ['VAD', 'ASR', 'LLM', 'VLLM', 'TTS', 'Memory']) {
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

function bindInputs() {
  document.querySelectorAll('[data-path]').forEach((el) => {
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

  document.querySelectorAll('[data-list]').forEach((el) => {
    el.addEventListener('input', () => {
      const items = getPath(state, el.dataset.list);
      if (Array.isArray(items)) items[Number(el.dataset.idx)] = el.value;
      markChanged();
    });
  });

  document.querySelectorAll('[data-add]').forEach((el) => {
    el.addEventListener('click', () => {
      const items = getPath(state, el.dataset.add);
      if (Array.isArray(items)) items.push('');
      render();
      markChanged();
    });
  });

  document.querySelectorAll('[data-del]').forEach((el) => {
    el.addEventListener('click', () => {
      const items = getPath(state, el.dataset.del);
      if (Array.isArray(items)) items.splice(Number(el.dataset.idx), 1);
      render();
      markChanged();
    });
  });
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
 * hash 深链（§4.5）：``#<分组id>`` 定位域内分组
 * ------------------------------------------------------------------------ */
function applyHash() {
  const id = decodeURIComponent(window.location.hash.replace(/^#/, ''));
  if (!id) return;
  const target = document.getElementById(id);
  if (!target) return;
  // 目标若在折叠容器里（「更多设置」），先展开——否则滚过去是个空壳。
  const details = target.querySelector('details.more-settings');
  if (details) details.open = true;
  target.scrollIntoView({ block: 'start' });
  target.classList.add('hash-flash');
  setTimeout(() => target.classList.remove('hash-flash'), 1500);
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
