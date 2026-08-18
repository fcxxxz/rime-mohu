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
