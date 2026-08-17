(function (root) {
'use strict';

const SETTING_RULES = [
  { id: 'quickCodeIndicator', label: '简码图标', pattern: /(^|\/)quick_code_indicator$/ },
  { id: 'pinIndicator', label: 'Pin 图标', pattern: /(^|\/)pin\/indicator$/ },
];
const coreApi = root.RimeSkinCore
  || (typeof module !== 'undefined' && module.exports ? require('./core.js') : {});

function stripComment(line) {
  let quote = '';
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if ((char === '"' || char === "'") && line[index - 1] !== '\\') quote = quote === char ? '' : quote || char;
    if (char === '#' && !quote) return line.slice(0, index);
  }
  return line;
}

function unquote(value) {
  const text = String(value || '').trim();
  if ((text.startsWith('"') && text.endsWith('"')) || (text.startsWith("'") && text.endsWith("'"))) {
    return text.slice(1, -1);
  }
  return text;
}

function scanScalars(text) {
  const stack = [];
  const entries = [];
  for (const rawLine of String(text || '').split(/\r?\n/)) {
    const line = stripComment(rawLine);
    if (!line.trim() || line.trimStart().startsWith('- ')) continue;
    const match = line.match(/^(\s*)(["']?)([^:"']+)\2:\s*(.*)$/);
    if (!match) continue;
    const indent = match[1].replace(/\t/g, '  ').length;
    const key = match[3].trim();
    const rawValue = match[4].trim();
    while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop();
    const path = [...stack.map((item) => item.key), key];
    if (rawValue) entries.push({ path, value: unquote(rawValue) });
    else stack.push({ indent, key });
  }
  return entries;
}

function schemaMetadata(file) {
  const entries = scanScalars(file.text);
  const id = entries.find((item) => item.path.join('/') === 'schema/schema_id')?.value;
  if (!id) return null;
  const name = entries.find((item) => item.path.join('/') === 'schema/name')?.value || id;
  return { id, name, fileName: file.name, text: file.text };
}

function enabledSchemaIds(defaultCustomText) {
  const result = [];
  let schemaListIndent = null;

  function addSchemaIds(fragment) {
    const pattern = /(?:^|[{,\s])schema\s*:\s*["']?([A-Za-z0-9_.-]+)/g;
    let match;
    while ((match = pattern.exec(fragment))) {
      if (!result.includes(match[1])) result.push(match[1]);
    }
  }

  for (const rawLine of String(defaultCustomText || '').split(/\r?\n/)) {
    const line = stripComment(rawLine);
    if (!line.trim()) continue;
    const indent = line.match(/^\s*/)?.[0].replace(/\t/g, '  ').length || 0;
    const trimmed = line.trim();
    const listStart = trimmed.match(/^["']?(?:patch\/)?schema_list["']?\s*:\s*(.*)$/);
    if (listStart) {
      schemaListIndent = indent;
      addSchemaIds(listStart[1]);
      continue;
    }
    if (schemaListIndent === null) continue;
    if (indent <= schemaListIndent) {
      schemaListIndent = null;
      continue;
    }
    if (/^-\s*(?:\{\s*)?schema\s*:/.test(trimmed)) addSchemaIds(trimmed);
  }
  return result;
}

function buildSchemaCatalog(files, defaultCustomText = '') {
  const enabled = enabledSchemaIds(defaultCustomText);
  const order = new Map(enabled.map((id, index) => [id, index]));
  return (files || [])
    .filter((file) => file.name.endsWith('.schema.yaml'))
    .map(schemaMetadata)
    .filter(Boolean)
    .map((schema) => ({ ...schema, enabled: order.has(schema.id) }))
    .sort((left, right) => {
      const leftOrder = order.has(left.id) ? order.get(left.id) : Number.MAX_SAFE_INTEGER;
      const rightOrder = order.has(right.id) ? order.get(right.id) : Number.MAX_SAFE_INTEGER;
      return leftOrder - rightOrder || left.name.localeCompare(right.name);
    });
}

function fileByConfigId(files, configId) {
  return (files || []).find((file) => file.name === `${configId}.yaml`)
    || (files || []).find((file) => file.name === `${configId}.schema.yaml`);
}

function customFilesForSchema(schemaId, files) {
  const byName = new Map((files || []).map((file) => [file.name, file]));
  const output = [];
  const visited = new Set();

  function visit(filename) {
    if (visited.has(filename)) return;
    visited.add(filename);
    const file = byName.get(filename);
    if (!file) return;
    output.push(file);
    for (const entry of scanScalars(file.text)) {
      if (entry.path[entry.path.length - 1] !== '__include') continue;
      const configId = String(entry.value || '').split(':', 1)[0];
      if (!configId) continue;
      const includedName = configId.endsWith('.yaml') ? configId : `${configId}.yaml`;
      visit(includedName);
    }
  }

  if (schemaId) visit(`${schemaId}.custom.yaml`);
  return output;
}

function entriesUnder(entries, prefix) {
  return entries.filter((entry) => prefix.every((part, index) => entry.path[index] === part));
}

function resolvedEntries(file, files, targetPrefix = [], outputPrefix = [], visited = new Set()) {
  const visitKey = `${file.name}:${targetPrefix.join('/')}`;
  if (visited.has(visitKey)) return [];
  const nextVisited = new Set(visited).add(visitKey);
  const entries = entriesUnder(scanScalars(file.text), targetPrefix);
  const inherited = [];
  const local = [];
  for (const entry of entries) {
    const relative = entry.path.slice(targetPrefix.length);
    if (!relative.length) continue;
    if (relative[relative.length - 1] !== '__include') {
      local.push({ path: [...outputPrefix, ...relative], value: entry.value, source: file.name });
      continue;
    }
    const parent = relative.slice(0, -1);
    const include = String(entry.value || '');
    let configId = file.name.replace(/\.schema\.yaml$|\.yaml$/, '');
    let includePath = include;
    if (include.includes(':')) {
      const parts = include.split(':', 2);
      configId = parts[0] || configId;
      includePath = parts[1];
    }
    const source = fileByConfigId(files, configId);
    if (!source) continue;
    const prefix = String(includePath || '').replace(/^\//, '').split('/').filter(Boolean);
    inherited.push(...resolvedEntries(source, files, prefix, [...outputPrefix, ...parent], nextVisited));
  }
  return [...inherited, ...local];
}

function discoverSchemaSettings(schema, files) {
  if (!schema) return [];
  const resolved = resolvedEntries({ name: schema.fileName, text: schema.text }, files);
  const found = [];
  for (const rule of SETTING_RULES) {
    let entry = null;
    for (let index = resolved.length - 1; index >= 0; index -= 1) {
      if (rule.pattern.test(resolved[index].path.join('/'))) {
        entry = resolved[index];
        break;
      }
    }
    if (entry) found.push({ id: rule.id, label: rule.label, path: entry.path.join('/'), value: entry.value, source: entry.source });
  }
  return found;
}

function updateSchemaSetting(customText, settingPath, value) {
  if (typeof coreApi.updateCustomScalar !== 'function') {
    throw new Error('缺少 YAML 配置写入器。');
  }
  return coreApi.updateCustomScalar(customText, settingPath, String(value ?? ''));
}

const api = { buildSchemaCatalog, customFilesForSchema, discoverSchemaSettings, updateSchemaSetting };
if (typeof module !== 'undefined' && module.exports) module.exports = api;
root.RimeSchemaSettings = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
