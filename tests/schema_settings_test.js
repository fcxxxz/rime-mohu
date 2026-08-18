const assert = require('assert');

function test(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`not ok - ${name}`);
    throw error;
  }
}

const settings = require('../Rime皮肤编辑器/src/schema-settings.js');

const schemaFiles = [
  {
    name: 'mohu_zrm.schema.yaml',
    text: [
      'schema:',
      '  schema_id: mohu_zrm',
      '  name: 魔虎·自然码',
      'mohu:',
      '  quick_code_indicator: "⚡"',
      '  pin:',
      '    __include: mohu:/pin',
    ].join('\n'),
  },
  {
    name: 'mohu_flypy.schema.yaml',
    text: [
      'schema:',
      '  schema_id: mohu_flypy',
      '  name: 魔虎·小鹤',
      'mohu:',
      '  quick_code_indicator: "⚡"',
      '  pin:',
      '    __include: mohu:/pin',
    ].join('\n'),
  },
  {
    name: 'mohu.yaml',
    text: [
      'pin:',
      '  indicator: "📌"',
    ].join('\n'),
  },
];

test('catalog only marks schemas listed under schema_list as enabled', () => {
  const custom = [
    '# schema: commented_out',
    'patch:',
    '  schema_list:',
    '    - {schema: mohu_zrm}',
    '  unrelated:',
    '    schema: mohu_flypy',
  ].join('\n');
  const catalog = settings.buildSchemaCatalog(schemaFiles, custom);
  assert.deepStrictEqual(
    catalog.filter((item) => item.enabled).map((item) => item.id),
    ['mohu_zrm'],
  );
});

test('catalog accepts block-style schema list entries and preserves their order', () => {
  const custom = [
    'patch:',
    '  schema_list:',
    '    - schema: mohu_flypy',
    '    - schema: mohu_zrm',
  ].join('\n');
  const catalog = settings.buildSchemaCatalog(schemaFiles, custom);
  assert.deepStrictEqual(catalog.slice(0, 2).map((item) => item.id), ['mohu_flypy', 'mohu_zrm']);
});

test('settings are discovered through schema inheritance', () => {
  const schema = settings.buildSchemaCatalog(schemaFiles).find((item) => item.id === 'mohu_zrm');
  const discovered = settings.discoverSchemaSettings(schema, schemaFiles);
  assert.deepStrictEqual(
    discovered.map(({ id, value, source }) => ({ id, value, source })),
    [
      { id: 'quickCodeIndicator', value: '⚡', source: 'mohu_zrm.schema.yaml' },
      { id: 'pinIndicator', value: '📌', source: 'mohu.yaml' },
    ],
  );
});

test('schema custom inheritance stays scoped to the selected schema', () => {
  const files = [
    { name: 'mohu_zrm.custom.yaml', text: 'patch:\n  __include: shared_mohu.custom:/' },
    { name: 'shared_mohu.custom.yaml', text: 'patch:\n  menu/alternative_select_labels: [一, 二]' },
    { name: 'mohu_flypy.custom.yaml', text: 'patch:\n  menu/alternative_select_labels: [壹, 贰]' },
  ];
  assert.deepStrictEqual(
    settings.customFilesForSchema('mohu_zrm', files).map((item) => item.name),
    ['mohu_zrm.custom.yaml', 'shared_mohu.custom.yaml'],
  );
});

test('schema setting writer stores a scalar patch path', () => {
  const updated = settings.updateSchemaSetting('patch:\n', 'translator/quick_code_indicator', '⚡');
  assert.ok(updated.includes('translator/quick_code_indicator:'), updated);
  assert.ok(updated.includes('⚡'), updated);
});
