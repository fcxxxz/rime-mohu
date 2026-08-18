const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const corePath = path.resolve(__dirname, '../Rime皮肤编辑器/src/core.js');
const source = fs.readFileSync(corePath, 'utf8');
const sandbox = { console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: corePath });

function test(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`not ok - ${name}`);
    throw error;
  }
}

const core = sandbox.RimeSkinCore;

test('squirrel config writer does not emit weasel-only mark_text', () => {
  const updated = core.updateSquirrelConfig('patch:\n', {
    id: 'test_skin',
    displayName: 'Test Skin',
    colors: {},
    layout: {
      candidateListLayout: 'stacked',
      candidateFormat: '%c. %@',
      markText: '▌',
    },
  });

  assert.ok(!updated.includes('mark_text'), updated);
});

test('weasel config writer emits mark_text', () => {
  const updated = core.updateWeaselConfig('patch:\n', {
    id: 'test_skin',
    displayName: 'Test Skin',
    colors: {},
    layout: {
      horizontal: false,
      labelFormat: '%s.',
      markText: '▌',
    },
  });

  assert.ok(updated.includes('mark_text: "▌"') || updated.includes('mark_text: ▌'), updated);
});

test('parseBuiltinConfig lists preset skins with active ids', () => {
  const config = core.parseBuiltinConfig('squirrel', [
    'style:',
    '  color_scheme: wechat',
    '  color_scheme_dark: wechat_dark',
    'preset_color_schemes:',
    '  wechat:',
    '    name: 微信',
    '    author: 例子',
    '    back_color: 0xFFFFFF',
    '    candidate_text_color: 0x000000',
    '  wechat_dark:',
    '    name: 微信深色',
    '    back_color: 0x222222',
  ].join('\n'));

  assert.strictEqual(config.available, true);
  assert.strictEqual(config.sourceFile, 'squirrel.yaml');
  assert.strictEqual(config.skins.map((skin) => skin.id).join(','), 'wechat,wechat_dark');
  assert.strictEqual(config.skins[0].displayName, '微信');
  assert.strictEqual(config.skins[0].source.builtin, true);
  assert.strictEqual(config.skins[0].colors.back.r, 255);
  assert.strictEqual(config.activeSkinId, 'wechat');
  assert.strictEqual(config.darkSkinId, 'wechat_dark');
  assert.strictEqual(config.skipped.length, 0);
});

test('parseBuiltinConfig skips malformed schemes but keeps the rest', () => {
  const config = core.parseBuiltinConfig('weasel', [
    'preset_color_schemes:',
    '  good:',
    '    name: 好',
    '    back_color: 0xFFFFFF',
    '  bad:',
    '    name: 坏',
    '    back_color: not-a-color',
  ].join('\n'));

  assert.strictEqual(config.skins.map((skin) => skin.id).join(','), 'good');
  assert.strictEqual(config.skipped.join(','), 'bad');
});

test('updateActiveSkinConfig clearDark removes the dark override', () => {
  const base = 'patch:\n  style:\n    color_scheme: old\n    color_scheme_dark: old_dark\n';
  const updated = core.updateActiveSkinConfig(base, 'squirrel', 'wechat', { clearDark: true });

  assert.ok(updated.includes('color_scheme: wechat'), updated);
  assert.ok(!updated.includes('color_scheme_dark'), updated);
});
