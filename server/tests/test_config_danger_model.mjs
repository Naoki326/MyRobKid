#!/usr/bin/env node
/* ============================================================================
 * test_config_danger_model.mjs — 危险分级缝（issue #30，父 spec §6）
 *
 * 被测对象：server/config/config_danger_model.js（**无 DOM 依赖**的纯模块）。
 *
 * 为什么单列一条缝：§6.1 的定级规则是「机械可算」的，而唯一需要「算」的
 * 动态项是**重启设备的升降级**（§6.2：固件库有更新版本时从警示升为危险）。
 * 这条升降级纯前端可判（两个列表对比版本），所以它必须是一个可测的纯函数
 * ——把它留在渲染代码里就等于放弃 §6 唯一一处真正的判定逻辑。
 *
 * 判别力（本文件最易糊弄处）：
 *   1. **重启设备在无更新版本时必须是 warning**，有更新版本时才是 danger。
 *      恒返回 danger 的实现在「无更新」那条上立刻红。
 *   2. **版本比较必须真比较**，不是字符串比较：``2.10.0`` 高于 ``2.9.9``、
 *      ``1.0`` 等于 ``v1.0.0``、``?`` / 空版本不算「更新」。把比较写成
 *      ``a > b`` 会让第一条静默地判反（字符串里 "2.10" < "2.9"）。
 *   3. **删除固件恒 danger、且带打字摩擦**（§6.3）；上传固件恒 danger、
 *      **不带**打字摩擦（§6.3 明写「不设打字摩擦」）。
 *   4. **上传固件的后果文案点名武装对象 = 未来所有开机自检**（ADR-0002 的
 *      正确读法），且带动态数字。写成「在线设备会自动升级」是反的。
 *
 * 不做的事：不测 DOM 呈现（父 spec 明确不引入浏览器测试基建）。确认层的
 * 外观、颜色、图标只钉**声明**（level → 视觉载体），渲染由人工走查。
 *
 * 运行：node --test server/tests/test_config_danger_model.mjs
 * ========================================================================== */

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CONFIRM_MODE,
  DANGER_LEVELS,
  LEVEL,
  LEVEL_VISUALS,
  OPERATION_LEVELS,
  compareVersions,
  descriptiveFacts,
  hasNewerFirmware,
  highestLevel,
  levelOf,
  needsVersionTyping,
  normalizeVersion,
  operationConsequences,
} from '../config/config_danger_model.js';

/* ---------------------------------------------------------------------------
 * 夹具：一份固件库 + 一台在线设备（设备的型号/版本来自在线设备列表的行）。
 * ------------------------------------------------------------------------ */
const FIRMWARES = [
  { filename: 'zhengchen-minicam_2.4.2.bin', model: 'zhengchen-minicam', version: '2.4.2' },
  { filename: 'other-board_3.0.0.bin', model: 'other-board', version: '3.0.0' },
];

/* ---------------------------------------------------------------------------
 * §6.1 定级规则：三级枚举 + 视觉三载体
 * ------------------------------------------------------------------------ */

test('三级枚举就是 §6.2 那三个字（不许发明内部代号）', () => {
  assert.deepEqual(DANGER_LEVELS, ['normal', 'warning', 'danger']);
  assert.equal(LEVEL.NORMAL, 'normal');
  assert.equal(LEVEL.WARNING, 'warning');
  assert.equal(LEVEL.DANGER, 'danger');
});

test('视觉三载体（颜色 + 图标 + 文案）各占一格，缺一不可', () => {
  // §6.4「三载体同上，不只靠颜色」——断言每条都同时给了颜色与图标，
  // 只靠颜色的实现（icon 为空串）在这里红。
  for (const lvl of DANGER_LEVELS) {
    const v = LEVEL_VISUALS[lvl];
    assert.ok(v && v.color, `${lvl} 必须有颜色载体`);
    assert.ok(typeof v.icon === 'string', `${lvl} 的图标载体必须显式声明（可为空）`);
    assert.ok(typeof v.buttonClass === 'string', `${lvl} 要有按钮的类名载体`);
    assert.ok(v.label, `${lvl} 要有中文级名（读者直觉，避免 L0/L1/L2）`);
  }
  // 常规不带图标（默认按钮就是它的载体），警示与危险各占一个不同的图标
  // ——「三载体缺一不可」约束的是警示与危险这两级（§6.4 明文：常规 = 默认按钮）。
  assert.equal(LEVEL_VISUALS.normal.icon, '');
  assert.ok(LEVEL_VISUALS.warning.icon && LEVEL_VISUALS.danger.icon,
    '警示与危险必须有图标载体——只靠颜色是不允许的');
  assert.equal(LEVEL_VISUALS.warning.icon, '⚠');
  assert.equal(LEVEL_VISUALS.danger.icon, '⛔');
  assert.notEqual(LEVEL_VISUALS.warning.color, LEVEL_VISUALS.danger.color);
  assert.notEqual(LEVEL_VISUALS.warning.buttonClass,
                  LEVEL_VISUALS.danger.buttonClass);
});

/* ---------------------------------------------------------------------------
 * §6.2 落位表：五个操作（含重启设备的动态项）
 * ------------------------------------------------------------------------ */

test('§6.2 落位表：试连 LLM / 覆盖密钥 / 保存普通修改归常规（零确认）', () => {
  for (const op of ['save', 'test_llm', 'overwrite_secret']) {
    assert.equal(levelOf(op), LEVEL.NORMAL, `${op} 是常规（§6.2 表）`);
    assert.equal(CONFIRM_MODE[LEVEL.NORMAL], 'none', '常规零确认');
  }
});

test('§6.2 落位表：重启服务 / SmartConfig 广播归警示（单击确认）', () => {
  for (const op of ['restart_server', 'smartconfig']) {
    assert.equal(levelOf(op), LEVEL.WARNING, `${op} 是警示（§6.2 表）`);
    assert.equal(CONFIRM_MODE[LEVEL.WARNING], 'click', '警示 = 单击确认');
  }
});

test('§6.2 落位表：删除固件 / 上传固件归危险（强确认）', () => {
  for (const op of ['delete_firmware', 'upload_firmware']) {
    assert.equal(levelOf(op), LEVEL.DANGER, `${op} 是危险（§6.2 表）`);
    assert.equal(CONFIRM_MODE[LEVEL.DANGER], 'strong', '危险 = 强确认');
  }
});

test('重启设备在被要求判定上下文时，缺上下文不许静默降级', () => {
  // 有在线设备必须先算版本对比才算得出级别；不给上下文就 throw，
  // 不让「忘了算」表现成一个看起来正常的警示级。
  assert.throws(() => levelOf('reboot_device'), /上下文|context/i);
  assert.equal(levelOf('reboot_device', { firmwares: FIRMWARES, device: { model: 'zhengchen-minicam', version: '2.4.2' } }),
    LEVEL.WARNING);
});

test('未知操作归常规，但必须显式登记（fallback 是声明，不是默认）', () => {
  assert.equal(levelOf('save'), LEVEL.NORMAL);
  assert.equal(OPERATION_LEVELS.save, LEVEL.NORMAL,
    '每个操作的级别都要在落位表里显式登记，不许靠 fallback 兜');
  assert.throws(() => levelOf('launch_nukes'), /未知操作/,
    '没登记的操作要炸：静默归常规等于悄悄放走一个危险动作');
});

/* ---------------------------------------------------------------------------
 * 版本比较（重启设备升降级的判据）
 * ------------------------------------------------------------------------ */

test('normalizeVersion 认 v 前缀、去空白、按段存数字', () => {
  assert.deepEqual(normalizeVersion('v1.2.3'), [1, 2, 3]);
  assert.deepEqual(normalizeVersion(' 2.4 '), [2, 4]);
  assert.deepEqual(normalizeVersion('1.0.0-rc1'), [1, 0, 0, 1]);
  assert.equal(normalizeVersion('?'), null);
  assert.equal(normalizeVersion(''), null);
  assert.equal(normalizeVersion(null), null);
  assert.equal(normalizeVersion('abc'), null);
});

test('compareVersions 按数字段比较，不是字符串比较（2.10 > 2.9）', () => {
  assert.equal(compareVersions('2.10.0', '2.9.9'), 1,
    '字符串比较会给出 -1，这条就是钉住它的');
  assert.equal(compareVersions('2.9.9', '2.10.0'), -1);
  assert.equal(compareVersions('1.0', 'v1.0.0'), 0, '缺段按 0 补（同一版本）');
  assert.equal(compareVersions('1.0.1', '1.0'), 1, '多出的段比 0 大');
  assert.equal(compareVersions('?', '1.0'), null, '认不出的版本不回谎值');
  assert.equal(compareVersions('1.0', null), null);
});

test('hasNewerFirmware 只比**同型号**的固件（别的型号的版本无关）', () => {
  assert.equal(hasNewerFirmware('zhengchen-minicam', '2.4.1', FIRMWARES), true);
  assert.equal(hasNewerFirmware('zhengchen-minicam', '2.4.2', FIRMWARES), false,
    '版本相等不算「有更新版本」（相等升级不触发）');
  assert.equal(hasNewerFirmware('zhengchen-minicam', '2.5.0', FIRMWARES), false,
    '设备比库里新 = 没有更新版本');
  // 库里最高的同型号版本是基准，不是任意一条。
  assert.equal(hasNewerFirmware('other-board', '2.9.9', FIRMWARES), true);
  assert.equal(hasNewerFirmware('unknown-board', '0.1.0', FIRMWARES), false,
    '库里有别的型号的高版本，与本设备无关');
});

test('hasNewerFirmware：设备版本认不出时按「有更新」保守处理（宁升不降）', () => {
  // 现场表现：设备上报 version 为 '?' 或缺失。此时不能证明「库里没有更新的」，
  // 而误判成警示的代价是用户以为重启是自愈操作、实际触发了固件升级。
  assert.equal(hasNewerFirmware('zhengchen-minicam', '?', FIRMWARES), true);
  assert.equal(hasNewerFirmware('zhengchen-minicam', '', FIRMWARES), true);
  assert.equal(hasNewerFirmware('zhengchen-minicam', null, FIRMWARES), true);
  // 库里连这个型号都没有 → 不可能升级 → 仍是警示。
  assert.equal(hasNewerFirmware('unknown-board', '?', FIRMWARES), false);
});

/* ---------------------------------------------------------------------------
 * §6.2 动态升降级：重启设备
 * ------------------------------------------------------------------------ */

test('重启设备：固件库无更新版本时是警示（§6.2 表「可恢复的打断」）', () => {
  assert.equal(levelOf('reboot_device', {
    firmwares: FIRMWARES,
    device: { model: 'zhengchen-minicam', version: '2.4.2' },
  }), LEVEL.WARNING);
});

test('重启设备：固件库有更新版本时升为危险（它变成触发升级的扳机）', () => {
  assert.equal(levelOf('reboot_device', {
    firmwares: FIRMWARES,
    device: { model: 'zhengchen-minicam', version: '2.4.1' },
  }), LEVEL.DANGER);
  // 同型号更高版本，即使是第三位（2.4.2 → 2.4.3）。
  assert.equal(levelOf('reboot_device', {
    firmwares: [{ filename: 'zhengchen-minicam_2.4.3.bin', model: 'zhengchen-minicam', version: '2.4.3' }],
    device: { model: 'zhengchen-minicam', version: '2.4.2' },
  }), LEVEL.DANGER);
});

/* ---------------------------------------------------------------------------
 * AC3：与 api/devices 同形的载荷必须真的驱动升降级（issue #30）
 *
 * 背景：AC3「固件库放入更高版本后，重启设备的确认从警示升为危险」曾经
 * **在真实页面上不可达**——``api/devices`` 当时不返回 ``model``/``version``，
 * 前端拿到的设备行是 ``{device_id, client_ip}``，``modelOf(d)`` 得空串，
 * 库里找不到同型号行，恒返回警示。
 *
 * ⚠️ 职责边界（请勿误读）：本用例**只能**证明「给定带 model/version 的载荷，
 * 模型算出 danger」——它碰不到 Python，后端把字段删了它**不会**红（实测过）。
 * 「数据源真的接上了」的证据在 HTTP 契约缝：
 * ``test_config_danger_seam.py`` 的 ``test_devices_payload_carries_model_and_
 * version_keys`` / ``test_ota_self_check_is_the_source_of_model_and_version``
 * （那两条删字段就红）。本用例在此守护的是模型侧的升降级行为与其反向边界。
 * ------------------------------------------------------------------------ */

test('AC3：与 api/devices 同形的设备载荷，在库中有更高同型号版本时得 danger', () => {
  // 与 server/core/api/config_handler.py::handle_devices 逐字段同形。
  const device = {
    device_id: 'aa:bb:cc:dd:ee:ff',
    client_ip: '192.168.18.20',
    model: 'zhengchen-minicam',
    version: '2.4.1',
  };
  // 库里同型号有 2.4.2 → 比设备高 → 重启是触发升级的扳机 → 危险。
  assert.equal(
    levelOf('reboot_device', { firmwares: FIRMWARES, device }),
    LEVEL.DANGER,
    '后端给了 model/version，前端就该升为危险（AC3）');

  // 反向边界：把 model/version 拿掉（旧 bug 的形状）只能得警示——
  // 证明上面那条确实是这两个字段带来的，不是固件库单方面決定。
  const bare = { device_id: device.device_id, client_ip: device.client_ip };
  assert.equal(
    levelOf('reboot_device', { firmwares: FIRMWARES, device: bare }),
    LEVEL.WARNING,
    '没有 model/version 时只能停在警示——这正是修前的现象');

  // 库里没有该型号（型号真有值但不匹配）→ 也是警示（不是「一律危险」）。
  assert.equal(
    levelOf('reboot_device', {
      firmwares: FIRMWARES,
      device: { ...device, model: 'unknown-board' },
    }),
    LEVEL.WARNING);

  // version 与库里同型号最高版本相等 → 不触发升级 → 警示。
  assert.equal(
    levelOf('reboot_device', {
      firmwares: FIRMWARES,
      device: { ...device, version: '2.4.2' },
    }),
    LEVEL.WARNING, '版本相等不算有更新版本（相等不触发升级）');
});

test('重启服务的级别**不受**固件库影响（它是自愈操作，没有链条）', () => {
  // 判别力：把「重启」当成同一类动作（按固件库升降级）会让重启服务也变红。
  assert.equal(levelOf('restart_server', {
    firmwares: FIRMWARES,
    device: { model: 'zhengchen-minicam', version: '1.0.0' },
  }), LEVEL.WARNING);
});

/* ---------------------------------------------------------------------------
 * §6.3 两种确认形态
 * ------------------------------------------------------------------------ */

test('§6.3：只有删除固件带打字摩擦（输入固件版本号）', () => {
  assert.equal(needsVersionTyping('delete_firmware'), true);
  assert.equal(needsVersionTyping('upload_firmware'), false,
    '§6.3 明写上传固件「不设打字摩擦」——正路运维，摩擦太高会催生绕过');
  assert.equal(needsVersionTyping('reboot_device'), false);
  assert.equal(needsVersionTyping('restart_server'), false);
  assert.equal(needsVersionTyping('save'), false);
});

test('§6.3：删除固件的打字对象是固件版本号本身', () => {
  // 判别力：写成「输入文件名」比「输入版本号」松得多（文件名可以复制粘贴，
  // 版本号必须看列表才知道），而且与 §6.3 的措辞不符。
  assert.equal(needsVersionTyping('delete_firmware', { field: 'version' }), true);
  assert.equal(needsVersionTyping('delete_firmware', { field: 'filename' }), false);
});

test('危险级的后果清单带动态数字（§6.6 同源：两个既有列表）', () => {
  const facts = descriptiveFacts('upload_firmware', {
    firmwares: FIRMWARES,
    devices: [{ device_id: 'aa' }, { device_id: 'bb' }, { device_id: 'cc' }],
    pendingFirmware: { filename: 'zhengchen-minicam_2.4.3.bin', model: 'zhengchen-minicam', version: '2.4.3' },
  });
  assert.equal(facts.onlineDeviceCount, 3,
    '在线设备数来自 api/devices 的列表长度');
  assert.equal(facts.targetVersion, '2.4.3',
    '目标固件版本来自 api/firmware 的那条记录');
  assert.equal(facts.targetModel, 'zhengchen-minicam');
});

test('§6.3/ADR-0002：上传固件的后果文案点名「未来所有开机自检」，不是立即升级', () => {
  const facts = descriptiveFacts('upload_firmware', {
    firmwares: FIRMWARES,
    devices: [{ device_id: 'aa' }, { device_id: 'bb' }],
    pendingFirmware: { filename: 'zhengchen-minicam_2.4.3.bin', model: 'zhengchen-minicam', version: '2.4.3' },
  });
  const text = operationConsequences('upload_firmware', {
    firmwares: FIRMWARES,
    devices: [{ device_id: 'aa' }, { device_id: 'bb' }],
    pendingFirmware: { filename: 'zhengchen-minicam_2.4.3.bin', model: 'zhengchen-minicam', version: '2.4.3' },
  }).join('\n');
  assert.match(text, /开机自检/, '武装对象必须是「未来所有开机自检」（ADR-0002）');
  assert.match(text, /2\.4\.3/, '后果清单要带目标固件版本（动态数字）');
  assert.match(text, /2 台/, '后果清单要带在线设备数');
  assert.doesNotMatch(text, /会自动升级|立即自动升级|在线设备会立即/,
    '在线设备**不**立即升级（ADR-0002）——写成立即升级是反的');
  assert.match(text, /不\*\*立即升级|不立即升级/,
    'ADR-0002 的正确读法要说出来（在线设备不立即升级，重启才升）');
});

test('删除固件的后果文案说「信息永失」，且指出打字对象是版本号', () => {
  const text = operationConsequences('delete_firmware', {
    firmware: { filename: 'zhengchen-minicam_2.4.2.bin', model: 'zhengchen-minicam', version: '2.4.2' },
  }).join('\n');
  assert.match(text, /永失|不可恢复/);
  assert.match(text, /2\.4\.2/, '要指出输哪个版本号才解得开');
});

test('警示级的后果文案是**一句**（§6.4「一句后果 hint」，不是清单）', () => {
  const lines = operationConsequences('reboot_device', {
    firmwares: FIRMWARES,
    device: { model: 'zhengchen-minicam', version: '2.4.2' },
  });
  assert.equal(lines.length, 1, '警示 = 重述**一句**后果（§6.5）');
  assert.match(lines[0], /自愈|10-30|10–30/, '重启设备要说清「能自愈」这件事');
});

test('常规级没有确认层、也没有后果清单', () => {
  assert.equal(operationConsequences('save').length, 0);
  assert.equal(operationConsequences('test_llm').length, 0);
  assert.equal(levelOf('test_llm'), LEVEL.NORMAL,
    '试连 LLM 零确认（§6.2 明文），它的外呼副作用靠按钮旁的 hint 点名');
});

test('highestLevel：动态升降级取最高（固件库有更新 → 危险胜过设备的静态级）', () => {
  assert.equal(highestLevel(LEVEL.WARNING, LEVEL.DANGER), LEVEL.DANGER);
  assert.equal(highestLevel(LEVEL.NORMAL, LEVEL.WARNING), LEVEL.WARNING);
  assert.equal(highestLevel(LEVEL.DANGER, LEVEL.NORMAL), LEVEL.DANGER);
  assert.equal(highestLevel(), LEVEL.NORMAL);
});
