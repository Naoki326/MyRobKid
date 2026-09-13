/* ============================================================================
 * config_danger_model.js — 危险操作分级与确认层内容的纯模型（issue #30，父 spec §6）
 *
 * 本模块是 §6「危险操作分级」的可测试核心，**无 DOM 依赖**：
 *
 *   1. **落位表**（§6.2）：操作 → 常规 / 警示 / 危险。级是**按钮的属性**，
 *      不是页面位置属性（§6.6）——所以这张表按操作名索引，不按字段路径。
 *   2. **动态升降级**（§6.2）：重启设备在「固件库有更新版本」时从警示升为
 *      危险。版本对比是纯前端可判的（两个既有列表，无需新接口，§6.6），
 *      所以它必须是一个纯函数——这是本模块存在的第一个理由。
 *   3. **确认形态**（§6.3）：危险级的两种形态——上传固件/重启设备（危险时）
 *      是「后果清单 + 带后果文字的确认按钮」；删除固件再加**输入版本号**。
 *   4. **视觉三载体**（§6.4）：颜色 + 图标 + 文案，缺一不可，不许只靠颜色。
 *      三载体的声明在这里，渲染在共享外壳的确认层组件里。
 *   5. **后果文案**：上传固件的武装对象要写准 = **未来所有开机自检**
 *      （ADR-0002：在线设备不立即升级，重启才升）。
 *
 * 为什么抽成无 DOM 模块：§6 唯一需要「算」的东西是重启设备的升降级与后果清单
 * 里的动态数字。把它留在渲染代码里就等于放弃这一处判定逻辑的可测性
 * （父 spec 明确不引入浏览器测试基建）。纯函数可以用 node:test 直接跑
 * （测试缝见 server/tests/test_config_danger_model.mjs）。
 *
 * 词义沿用 CONTEXT.md 服务端段：**危险分级**（常规 / 警示 / 危险）。
 * 全部域页（``config_domain_page.js``）共用本模块——分级判定与确认层内容
 * 只有这一份。#31 收线前它同时喂两份在线副本（旧八组页面 + 新域页）；
 * 旧页退役后消费方只剩域页，本模块本身不变（资产没动，只是副本没了）。
 * ========================================================================== */

/** 三级（§6.2）。名字就是方案里的中文级名的英文形态，不要内部代号。 */
export const LEVEL = {
  NORMAL: 'normal',
  WARNING: 'warning',
  DANGER: 'danger',
};

/** 三级序列（由轻到重）。顺序即比较顺序（``highestLevel`` 用它）。 */
export const DANGER_LEVELS = [LEVEL.NORMAL, LEVEL.WARNING, LEVEL.DANGER];

/** 各级的视觉三载体（§6.4）。
 *
 * 颜色 + 图标 + 文案三载体同上，**缺一不可、不许只靠颜色**（§6.4 明文）。
 * 这里给的是**声明**；渲染在共享外壳的确认层组件与按钮类名上
 * （``config_shell.SHELL_CSS`` 的 ``.btn.warn`` / ``.btn.danger``）。
 *
 * ``normal`` 的图标是空串：默认按钮就是它的载体（§6.4「常规 = 默认按钮」），
 * 给它加图标反而会让「有图标 = 要小心」这条读数失效。
 */
export const LEVEL_VISUALS = {
  [LEVEL.NORMAL]: {
    label: '常规',
    color: 'default',
    icon: '',
    buttonClass: '',
    confirmClass: '',
    note: '无副作用，或意图已在输入动作里。',
  },
  [LEVEL.WARNING]: {
    label: '警示',
    color: 'amber',
    icon: '⚠',
    buttonClass: 'warn',
    confirmClass: 'warn',
    note: '撤销 = 等待或重做（可恢复的打断）。',
  },
  [LEVEL.DANGER]: {
    label: '危险',
    color: 'red',
    icon: '⛔',
    buttonClass: 'danger',
    confirmClass: 'danger',
    note: '撤销不可能，或撤销权不在操作者手里。',
  },
};

/** 每级的确认档位（§6.2 的「确认档位」列）。 */
export const CONFIRM_MODE = {
  [LEVEL.NORMAL]: 'none',
  [LEVEL.WARNING]: 'click',
  [LEVEL.DANGER]: 'strong',
};

/** §6.2 落位表：操作 → 级别。
 *
 * **每个操作都要显式登记**——``levelOf`` 对未登记的操作会抛错，不给回落。
 * 理由：回落成常规等于悄悄放走一个可能危险的动作，而它的现场表现是
 * 「这个按钮没有确认层」——看不出是漏登记还是故意如此。
 *
 * ``reboot_device`` 不在这张表里：它是**动态**项（警示 / 危险由固件库版本
 * 对比决定），见 ``levelOf``。
 */
export const OPERATION_LEVELS = {
  // ---- 常规：无副作用，或意图已在输入动作里 ----
  save: LEVEL.NORMAL,
  test_llm: LEVEL.NORMAL,
  overwrite_secret: LEVEL.NORMAL,
  // ---- 警示：撤销 = 等待或重做 ----
  restart_server: LEVEL.WARNING,
  smartconfig: LEVEL.WARNING,
  // ---- 危险：撤销不可能，或撤销权不在操作者手里 ----
  delete_firmware: LEVEL.DANGER,
  upload_firmware: LEVEL.DANGER,
};

/** 动态操作：级别由上下文算（§6.2 的重启设备）。
 *
 * 判别力所在：``reboot_device`` 若被塞进 ``OPERATION_LEVELS`` 里定死，
 * 「固件库放入更高版本后升为危险」这条 AC 就没法成立。
 */
export const DYNAMIC_OPERATIONS = ['reboot_device'];

/** 需要输入固件版本号才能确认的操作（§6.3）。只有删除固件。 */
const VERSION_TYPING_OPERATIONS = {
  delete_firmware: 'version',
};

/* ---------------------------------------------------------------------------
 * 版本比较（重启设备升降级的判据，§6.6「无需新接口」）
 * ------------------------------------------------------------------------ */

/** 版本串 → 数字段数组；认不出返回 ``null``。
 *
 * 认的形状与后端 ``handle_firmware_list`` 的解析正则同源
 * （``^(.+)_([0-9]+(?:\.[0-9]+)*(?:[-+][0-9A-Za-z.-]+)?)\.bin$``）：
 * 数字段用点分隔，可跟一个 ``-rc1`` / ``+build`` 后缀（后缀里的数字也收，
 * 排在主段之后）。
 */
export function normalizeVersion(value) {
  if (typeof value !== 'string') return null;
  const raw = value.trim().replace(/^v/i, '');
  if (raw === '') return null;
  const head = raw.split(/[-+]/)[0];
  const parts = head.split('.');
  const nums = [];
  for (const part of parts) {
    if (!/^[0-9]+$/.test(part)) return null;
    nums.push(Number(part));
  }
  if (nums.length === 0) return null;
  // 后缀里的数字段（-rc1）：让 1.0.0-rc1 与 1.0.0-rc2 能被区分。
  const tail = raw.split(/[-+]/).slice(1).join('.').match(/[0-9]+/g);
  if (tail) nums.push(...tail.map(Number));
  return nums;
}

/** 比较两个版本：``1`` / ``0`` / ``-1``；任一侧认不出返回 ``null``。
 *
 * **按数字段比较，不是字符串比较**——``"2.10.0" > "2.9.9"`` 在字符串序里
 * 是假的，而这恰好是现场最常出现的形态（次版本号过了 9）。
 */
export function compareVersions(a, b) {
  const va = normalizeVersion(a);
  const vb = normalizeVersion(b);
  if (va === null || vb === null) return null;
  const n = Math.max(va.length, vb.length);
  for (let i = 0; i < n; i += 1) {
    const x = va[i] === undefined ? 0 : va[i];
    const y = vb[i] === undefined ? 0 : vb[i];
    if (x > y) return 1;
    if (x < y) return -1;
  }
  return 0;
}

/** 固件库中是否有**同型号**的更高版本（重启设备升降级的判据）。
 *
 * 规则与读法：
 *   - 只比同型号（``other-board`` 的高版本与 ``zhengchen-minicam`` 无关）；
 *   - **相等不算**「有更新版本」（相等不触发升级）；
 *   - 固件库里连这个型号都没有 → 不可能升级 → ``false``；
 *   - 设备的版本认不出（``?`` / 空 / 缺失）→ **保守地算有更新**：
 *     不能证明「库里没有更新的」，而误判成警示的代价是用户以为重启是自愈
 *     操作、实际触发了固件升级。
 */
export function hasNewerFirmware(model, deviceVersion, firmwares) {
  const rows = Array.isArray(firmwares) ? firmwares : [];
  const sameModel = rows.filter((f) => f && f.model === model);
  if (sameModel.length === 0) return false;
  const known = normalizeVersion(deviceVersion);
  if (known === null) return true;
  for (const f of sameModel) {
    if (compareVersions(f.version, deviceVersion) === 1) return true;
  }
  return false;
}

/** 设备行上的型号：``api/devices`` 只给 ``device_id`` / ``client_ip``。
 *
 * 型号从 ``device_id`` 推（现场形态 ``aa:bb:cc:dd:ee:ff`` 不含型号时返回
 * 空串，此时 ``hasNewerFirmware`` 会因「库里没有这个型号」返回 false）。
 * 页面若能从别处拿到更准的型号，可以用 ``device.model`` 覆盖。
 */
function modelOf(device) {
  if (!device) return '';
  if (device.model) return String(device.model);
  return '';
}

function versionOf(device) {
  if (!device) return null;
  if (device.version !== undefined && device.version !== null) {
    return String(device.version);
  }
  return null;
}

/* ---------------------------------------------------------------------------
 * 定级
 * ------------------------------------------------------------------------ */

/** 操作 → 级别。``reboot_device`` 需要 ``context``（§6.2 的动态项）。
 *
 * 未登记的操作抛错（理由见 ``OPERATION_LEVELS`` 的注释）。
 */
export function levelOf(operation, context) {
  if (DYNAMIC_OPERATIONS.includes(operation)) {
    if (!context) {
      throw new Error(
        `${operation} 是动态项：需要上下文（firmwares / device）才算得出级别——`
        + '缺上下文静默降级表现为「这个按钮看起来是警示」');
    }
    if (operation === 'reboot_device') {
      return rebootDeviceLevel(context);
    }
    // 新增动态项时在这里显式分支（不许靠 fallback 猜）。
    throw new Error(`未知的动态操作: ${operation}`);
  }
  const level = OPERATION_LEVELS[operation];
  if (!level) throw new Error(`未知操作: ${operation}（未在 §6.2 落位表登记）`);
  return level;
}

/** 重启设备（§6.2 动态项）：固件库有更新版本 → 危险，否则警示。 */
function rebootDeviceLevel(context) {
  const device = context.device || null;
  if (hasNewerFirmware(modelOf(device), versionOf(device), context.firmwares)) {
    return LEVEL.DANGER;
  }
  return LEVEL.WARNING;
}

/** 取最高的级别（渲染时把「静态级别」与「动态修正」合成一个）。
 *
 * 例：重启设备的基础级是警示，固件库有更新版本时升成危险——合起来
 * 取最高，避免「先判警示再打补丁」那种两处各说一句话的写法。
 */
export function highestLevel(...levels) {
  let best = LEVEL.NORMAL;
  for (const lvl of levels) {
    if (DANGER_LEVELS.indexOf(lvl) > DANGER_LEVELS.indexOf(best)) best = lvl;
  }
  return best;
}

/** 这个操作是否需要**输入固件版本号**才能确认（§6.3）。
 *
 * 默认答的是「打字对象」（``'version'`` / ``null``）。第二条覆盖用于钉住
 * 「对象是版本号而不是文件名」——写成 ``field: 'filename'`` 要答 false。
 */
export function needsVersionTyping(operation, opts) {
  const expected = VERSION_TYPING_OPERATIONS[operation] || null;
  const field = (opts && opts.field) || 'version';
  return expected !== null && expected === field;
}

/* ---------------------------------------------------------------------------
 * 动态数字与后果文案（§6.3 / §6.6）
 * ------------------------------------------------------------------------ */

/** 后果清单里的动态数字——**同源**：都来自现有 ``api/devices`` /
 * ``api/firmware`` 两个列表，无需新接口（§6.6）。
 */
export function descriptiveFacts(operation, context) {
  const ctx = context || {};
  const devices = Array.isArray(ctx.devices) ? ctx.devices : [];
  const firmwares = Array.isArray(ctx.firmwares) ? ctx.firmwares : [];
  const device = ctx.device || null;
  const pending = ctx.pendingFirmware || null;
  return {
    operation,
    onlineDeviceCount: devices.length,
    firmwareCount: firmwares.length,
    target: pending ? pending.filename : '',
    targetModel: pending ? (pending.model || '') : modelOf(device),
    targetVersion: pending ? (pending.version || '') : '',
    deviceId: device ? (device.device_id || '') : '',
    deviceVersion: device ? (versionOf(device) || '') : '',
    // 重启设备（危险时）的目标版本 = 库里同型号的最高版本。
    upgradeVersion: device && ctx.firmwares
      ? highestVersionFor(modelOf(device), firmwares)
      : '',
  };
}

/** 固件库里同型号的最高版本串（认不出的行跳过）；没有则空串。 */
function highestVersionFor(model, firmwares) {
  const rows = Array.isArray(firmwares) ? firmwares : [];
  let best = null;
  for (const f of rows) {
    if (!f || f.model !== model) continue;
    if (compareVersions(f.version, best === null ? '0' : best) === 1
        || (best !== null && compareVersions(f.version, best) === 0)) {
      if (best === null || compareVersions(f.version, best) === 1) best = f.version;
    }
  }
  return best === null ? '' : String(best);
}

/** 一个操作的后果清单（§6.3）。常规级返回空数组（零确认）。
 *
 * 措辞口径：
 *   - **警示**：一句后果（§6.4「一句后果 hint」/ §6.5「重述一句后果」）；
 *   - **危险**：后果清单，含动态数字；
 *   - **上传固件**：武装对象写准 = **未来所有开机自检**（ADR-0002：
 *     在线设备不立即升级，重启才升）——写成「在线设备会自动升级」是反的。
 */
export function operationConsequences(operation, context) {
  const facts = descriptiveFacts(operation, context);
  // 级别走 ``levelOf`` 这唯一一条路：动态项（重启设备）在这里也照 §6.2 算
  // ——另写一处「大概算算」就是两把尺子。
  const level = levelOf(operation, context);
  if (level === LEVEL.NORMAL) return [];
  switch (operation) {
    case 'upload_firmware': {
      const ver = facts.targetVersion || '（未知版本）';
      return [
        `这份固件将被放进固件库：${facts.targetModel || '（未知型号）'} v${ver}。`,
        `武装的对象是**未来所有开机自检**——设备每次开机检查 OTA 时，若库中版本高于自身版本就会自动下载刷入（ADR-0002：在线设备**不**立即升级）。`,
        facts.onlineDeviceCount > 0
          ? `当前 ${facts.onlineDeviceCount} 台设备在线：它们会在下一次重启/SmartConfig 上线时被这条链带走。`
          : '当前没有设备在线：它仍会作用于未来每一次开机自检。',
        '上传本身可撤销（删除该文件），但已经被设备刷入的版本不可撤销。',
      ];
    }
    case 'delete_firmware': {
      const ver = (context && context.firmware && context.firmware.version)
        ? context.firmware.version : '';
      const fw = (context && context.firmware) || null;
      return [
        `删除 ${fw ? fw.filename : '这个固件文件'} 会从 data/bin/ 里**物理移除**它。`,
        '固件文件**信息永失**：服务器上不留副本、不进回收站，重新得到它只能重新下载或重新编译。',
        '如果它是在线设备回滚的唯一可用版本，删除之后没有任何按钮能把它拿回来。',
        ver
          ? `输入固件版本号 ${ver} 才能确认（这一步是故意的摩擦：它不可逆且不是正路运维）。`
          : '输入固件版本号才能确认（这一步是故意的摩擦：它不可逆且不是正路运维）。',
      ];
    }
    case 'reboot_device': {
      if (level === LEVEL.DANGER) {
        const target = facts.upgradeVersion || '更高';
        return [
          `设备 ${facts.deviceId || ''}（当前 v${facts.deviceVersion || '?'}）重启后会自动检查 OTA。`,
          `固件库里有更高版本 v${target}：这次重启就是**触发升级的扳机**，设备会把 v${target} 刷进去。`,
          '刷入完成之前没有回滚按钮——设备变砖的话要拆机接串口。',
        ];
      }
      return [
        `设备 ${facts.deviceId || ''} 会断连约 10-30 秒，之后自动重连——这是可恢复的打断，不需要人工干预。`,
      ];
    }
    case 'smartconfig': {
      return [
        '会在局域网里广播 Wi-Fi 凭据（约 30 秒）：物理世界有副作用，但重新广播一次即可覆盖。',
      ];
    }
    case 'restart_server': {
      return [
        '所有设备连接会断开，约 10-30 秒后自愈（launchctl 拉起）——不需要人工干预。',
      ];
    }
    default:
      // 常规级：零确认、无后果清单（§6.2）。
      return [];
  }
}

/** 危险级确认按钮上的文字（§6.3「单击带后果文字的确认按钮」）。
 *
 * 「确认」两个字不承载后果；这条要求按钮自己说出会发生什么。
 */
export function confirmButtonText(operation, context) {
  const facts = descriptiveFacts(operation, context);
  switch (operation) {
    case 'delete_firmware': {
      const ver = (context && context.firmware && context.firmware.version) || '';
      return ver ? `🗑 彻底删除 v${ver}` : '🗑 彻底删除';
    }
    case 'upload_firmware': {
      const ver = facts.targetVersion || '';
      return ver ? `⛔ 放进固件库，武装开机自检（v${ver}）`
        : '⛔ 放进固件库，武装开机自检';
    }
    case 'reboot_device':
      if (facts.upgradeVersion) {
        return `⛔ 重启并升级到 v${facts.upgradeVersion}`;
      }
      return '⟳ 重启设备';
    case 'restart_server':
      return '⚠ 重启服务';
    case 'smartconfig':
      return '⚠ 开始广播';
    default:
      return '确定';
  }
}
