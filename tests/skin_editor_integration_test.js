const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const indexHtml = fs.readFileSync(path.join(root, 'Rime皮肤编辑器/index.html'), 'utf8');
const appJs = fs.readFileSync(path.join(root, 'Rime皮肤编辑器/src/app.js'), 'utf8');

function test(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`not ok - ${name}`);
    throw error;
  }
}

test('preview model script loads before app script', () => {
  const modelIndex = indexHtml.indexOf('./src/preview-model.js');
  const appIndex = indexHtml.indexOf('./src/app.js');
  assert.ok(modelIndex >= 0, 'index.html should load preview-model.js');
  assert.ok(appIndex >= 0, 'index.html should load app.js');
  assert.ok(modelIndex < appIndex, 'preview-model.js must load before app.js');
});

test('schema settings script loads before app script', () => {
  const settingsIndex = indexHtml.indexOf('./src/schema-settings.js');
  const appIndex = indexHtml.indexOf('./src/app.js');
  assert.ok(settingsIndex >= 0, 'index.html should load schema-settings.js');
  assert.ok(settingsIndex < appIndex, 'schema-settings.js must load before app.js');
});

test('app delegates marker semantics to preview model with selected platform', () => {
  assert.ok(appJs.includes('RimeSkinPreviewModel'), 'app.js should read the preview model helper');
  assert.ok(appJs.includes('markerBehavior'), 'app.js should use markerBehavior');
  assert.ok(appJs.includes('state.selectedPlatform'), 'marker behavior must be platform-specific');
});

test('app hides weasel-only marker controls for squirrel', () => {
  assert.ok(appJs.includes("if (role === 'hilitedMark' && state.selectedPlatform !== 'weasel') continue"), 'hilited mark color should be omitted on squirrel');
  assert.ok(appJs.includes('dom.candidateMarkerField.hidden = !isWeasel'), 'candidate marker should be hidden on squirrel');
});

test('local launcher deployment is wired into skin and schema saves', () => {
  assert.ok(appJs.includes("localApi('deploy', { method: 'POST' })"), 'app should call the local deploy endpoint');
  assert.ok(appJs.includes('deploySupported'), 'app should respect launcher deployment capability');
  assert.ok(appJs.includes("await deployAfterWrite('已保存并备份')"), 'ordinary skin saves should trigger deployment');
  assert.ok(appJs.includes("await deployAfterWrite('已回退到所选备份')"), 'rollback should trigger deployment');
});

test('app lists built-in skins from platform yaml files', () => {
  assert.ok(appJs.includes('parseBuiltinConfig'), 'app should parse built-in skins from platform yaml');
  assert.ok(appJs.includes('refreshBuiltinConfigs'), 'app should refresh built-ins whenever config files load');
  assert.ok(appJs.includes('activateBuiltinSkin'), 'built-in skins should be activatable without saving a copy');
  assert.ok(appJs.includes('skin-list-header'), 'skin list should render a built-in section header');
});

test('built-in skins render read-only with a hint', () => {
  assert.ok(indexHtml.includes('builtinSkinHint'), 'index.html should include the built-in skin hint element');
  assert.ok(appJs.includes("state.selectedSkinOrigin === 'builtin'"), 'app should track the built-in selection origin');
  assert.ok(appJs.includes('复制为自定义'), 'duplicate button should offer copying built-ins into custom skins');
});
