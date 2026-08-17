(function () {
'use strict';

const appCore = window.RimeSkinCore || null;
const {
  copySkinToPlatform,
  createSkinPackage,
  createBackupManifest,
  createRollbackPlan,
  detectBrowserCapabilities,
  detectPreferredPlatform,
  deleteSquirrelSkinConfig,
  deleteWeaselSkinConfig,
  formatBackupFolderName,
  generateSkinId,
  parseGlobalCustomFiles,
  parseUserSelectedSchema,
  parseYaml,
  parseSquirrelConfig,
  parseWeaselConfig,
  parseSkinPackage,
  rgbaToRimeHex,
  updateGlobalCustomConfig,
  updateActiveSkinConfig,
  updateSquirrelConfig,
  updateWeaselConfig,
} = appCore || {};
const previewModel = window.RimeSkinPreviewModel || {};
const {
  DEFAULT_PREVIEW_CANDIDATES = [],
  DEFAULT_PREVIEW_CODE = 'u',
  DEFAULT_PREVIEW_CANDIDATES_TEXT = '',
  applyAlpha,
  markerBehavior,
  previewCandidateItems: parsePreviewCandidateItems,
  resolvePreviewComment,
} = previewModel;
const schemaSettingsApi = window.RimeSchemaSettings || {};
const { buildSchemaCatalog, customFilesForSchema, discoverSchemaSettings, updateSchemaSetting } = schemaSettingsApi;

const BACKUP_ROOT = 'Rime皮肤编辑器备份';
const LOCALHOST_NAMES = new Set(['127.0.0.1', 'localhost', '[::1]']);
const PLATFORM_FILES = {
  squirrel: 'squirrel.custom.yaml',
  weasel: 'weasel.custom.yaml',
};
const PLATFORM_LABELS = {
  squirrel: '鼠须管',
  weasel: '小狼毫',
};
const COMMON_FONTS = [
  'LXGW WenKai GB Screen',
  'PingFang SC',
  'Microsoft YaHei',
  'Segoe UI',
  'Consolas',
  'SimSun',
  'Apple Color Emoji',
  'Noto Color Emoji',
];
const COLOR_CONTROLS = [
  ['back', '背景'],
  ['border', '边框'],
  ['text', '编码文字'],
  ['preeditBack', '编码背景'],
  ['candidateText', '候选文字'],
  ['candidateBack', '候选背景'],
  ['commentText', '注释文字'],
  ['label', '编号'],
  ['hilitedBack', '编码高亮背景'],
  ['hilitedText', '编码高亮文字'],
  ['hilitedCandidateBack', '高亮背景'],
  ['hilitedCandidateText', '高亮文字'],
  ['hilitedLabel', '高亮编号备用'],
  ['hilitedCandidateLabel', '高亮编号'],
  ['hilitedCommentText', '高亮注释'],
  ['hilitedMark', '候选标记'],
  ['shadow', '阴影'],
  ['candidateShadow', '候选文字阴影'],
  ['hilitedCandidateShadow', '高亮文字阴影'],
];
const DEFAULT_COLORS = {
  back: { r: 255, g: 255, b: 255, a: 255 },
  border: { r: 230, g: 230, b: 230, a: 255 },
  text: { r: 51, g: 51, b: 51, a: 255 },
  preeditBack: { r: 255, g: 255, b: 255, a: 0 },
  candidateText: { r: 34, g: 34, b: 34, a: 255 },
  candidateBack: { r: 255, g: 255, b: 255, a: 0 },
  commentText: { r: 102, g: 102, b: 102, a: 255 },
  label: { r: 120, g: 120, b: 120, a: 255 },
  hilitedBack: { r: 240, g: 240, b: 240, a: 255 },
  hilitedText: { r: 0, g: 0, b: 0, a: 255 },
  hilitedCandidateBack: { r: 240, g: 240, b: 240, a: 255 },
  hilitedCandidateText: { r: 0, g: 0, b: 0, a: 255 },
  hilitedLabel: { r: 0, g: 0, b: 0, a: 255 },
  hilitedCandidateLabel: { r: 0, g: 0, b: 0, a: 255 },
  hilitedCommentText: { r: 80, g: 80, b: 80, a: 255 },
  hilitedMark: { r: 0, g: 0, b: 0, a: 255 },
  shadow: { r: 0, g: 0, b: 0, a: 32 },
  candidateShadow: { r: 0, g: 0, b: 0, a: 0 },
  hilitedCandidateShadow: { r: 0, g: 0, b: 0, a: 0 },
};
const NUMBER_STYLE_LABELS = {
  dot: '%s.',
  plain: '%s',
  paren: '%s)',
  bracket: '[%s]',
};
const NUMBER_LABEL_PRESETS = {
  circledIdeograph: {
    label: '㊀ ㊁ ㊂',
    values: ['㊀', '㊁', '㊂', '㊃', '㊄', '㊅', '㊆', '㊇', '㊈', '㊉'],
  },
  circledNumber: {
    label: '① ② ③',
    values: ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩'],
  },
  filledCircledNumber: {
    label: '❶ ❷ ❸',
    values: ['❶', '❷', '❸', '❹', '❺', '❻', '❼', '❽', '❾', '❿'],
  },
  doubleCircledNumber: {
    label: '⓵ ⓶ ⓷',
    values: ['⓵', '⓶', '⓷', '⓸', '⓹', '⓺', '⓻', '⓼', '⓽', '⓾'],
  },
  parenthesizedIdeograph: {
    label: '㈠ ㈡ ㈢',
    values: ['㈠', '㈡', '㈢', '㈣', '㈤', '㈥', '㈦', '㈧', '㈨', '㈩'],
  },
  mahjongNumber: {
    label: '🀇 🀈 🀉',
    values: ['🀇', '🀈', '🀉', '🀊', '🀋', '🀌', '🀍', '🀎', '🀏', '🀄'],
  },
  romanUpper: {
    label: 'Ⅰ Ⅱ Ⅲ',
    values: ['Ⅰ', 'Ⅱ', 'Ⅲ', 'Ⅳ', 'Ⅴ', 'Ⅵ', 'Ⅶ', 'Ⅷ', 'Ⅸ', 'Ⅹ'],
  },
  romanLower: {
    label: 'ⅰ ⅱ ⅲ',
    values: ['ⅰ', 'ⅱ', 'ⅲ', 'ⅳ', 'ⅴ', 'ⅵ', 'ⅶ', 'ⅷ', 'ⅸ', 'ⅹ'],
  },
  alphaUpper: {
    label: 'Ⓐ Ⓑ Ⓒ',
    values: ['Ⓐ', 'Ⓑ', 'Ⓒ', 'Ⓓ', 'Ⓔ', 'Ⓕ', 'Ⓖ', 'Ⓗ', 'Ⓘ', 'Ⓙ'],
  },
  alphaLower: {
    label: 'ⓐ ⓑ ⓒ',
    values: ['ⓐ', 'ⓑ', 'ⓒ', 'ⓓ', 'ⓔ', 'ⓕ', 'ⓖ', 'ⓗ', 'ⓘ', 'ⓙ'],
  },
};
const CUSTOM_NUMBER_STYLE = 'customLabels';
const CUSTOM_SEPARATOR = '__custom_separator__';
const CUSTOM_MARKER = '__custom_marker__';
const NUMBER_SEPARATOR_LABELS = {
  tight: '',
  normal: ' ',
  wide: '  ',
};
const NUMBER_SEPARATOR_GAPS = {
  tight: 2,
  normal: 4,
  wide: 8,
};
const PREVIEW_CANDIDATES = [
  { label: '1', text: '你好', comment: '常用', active: true },
  { label: '2', text: '你', comment: '单字' },
  { label: '3', text: '年', comment: '' },
  { label: '4', text: '候选文本比较长', comment: '长词' },
  { label: '5', text: '候選', comment: '异体' },
];
const PREVIEW_SETTINGS_STORAGE_KEY = 'rimeSkinEditor.previewSettings.v2';

const state = {
  capability: null,
  storageMode: 'browser',
  localApiToken: '',
  preferredPlatform: 'unknown',
  dirHandle: null,
  folderName: '',
  configs: {
    squirrel: null,
    weasel: null,
  },
  fileExists: {
    squirrel: false,
    weasel: false,
    default: false,
  },
  rawFiles: {
    squirrel: '',
    weasel: '',
    default: '',
    user: '',
  },
  customFiles: [],
  yamlFiles: [],
  schemas: [],
  currentSchemaId: '',
  selectedSchemaId: '',
  schemaSettings: [],
  deploySupported: false,
  globalLayout: {},
  globalSources: {},
  hasAnySchemaFile: false,
  selectedPlatform: 'squirrel',
  selectedSkinId: '',
  originalSkinId: '',
  draftSkin: null,
  previewActiveCandidateIndex: 0,
  previewScale: 100,
  previewCode: DEFAULT_PREVIEW_CODE,
  previewCandidatesText: DEFAULT_PREVIEW_CANDIDATES_TEXT,
  backups: [],
  heartbeatTimer: null,
  closeRequested: false,
};

const dom = {};

document.addEventListener('DOMContentLoaded', async () => {
  bindDom();
  try {
    await initialize();
  } catch (error) {
    renderStartupError(error);
    console.error('Rime skin editor startup failed', error);
  }
});

function bindDom() {
  for (const id of [
    'supportStatus',
    'chooseFolderButton',
    'folderName',
    'blockedPanel',
    'blockedReason',
    'workspace',
    'squirrelTab',
    'weaselTab',
    'platformHint',
    'skinList',
    'newSkinButton',
    'duplicateSkinButton',
    'deleteSkinButton',
    'importSkinButton',
    'exportSkinButton',
    'skinImportFile',
    'previewPlatform',
    'previewScale',
    'previewScaleValue',
    'previewCode',
    'previewCandidates',
    'resetPreviewSettingsButton',
    'previewStage',
    'previewPreedit',
    'candidatePreview',
    'colorControls',
    'layoutMode',
    'fontFace',
    'labelFontFace',
    'commentFontFace',
    'fontHint',
    'numberStyle',
    'numberSeparator',
    'candidateMarkerField',
    'candidateMarker',
    'preeditVisibility',
    'fontPoint',
    'fontPointNumber',
    'fontPointValue',
    'labelFontPoint',
    'labelFontPointNumber',
    'labelFontPointValue',
    'commentFontPoint',
    'commentFontPointNumber',
    'commentFontPointValue',
    'cornerRadius',
    'cornerRadiusNumber',
    'cornerRadiusValue',
    'hilitedCornerRadius',
    'hilitedCornerRadiusNumber',
    'hilitedCornerRadiusValue',
    'candidateSpacing',
    'candidateSpacingNumber',
    'candidateSpacingValue',
    'hiliteSpacing',
    'hiliteSpacingNumber',
    'hiliteSpacingValue',
    'lineSpacing',
    'lineSpacingNumber',
    'lineSpacingValue',
    'marginX',
    'marginXNumber',
    'marginXValue',
    'marginY',
    'marginYNumber',
    'marginYValue',
    'borderWidth',
    'borderWidthNumber',
    'borderWidthValue',
    'borderHeight',
    'borderHeightNumber',
    'borderHeightValue',
    'hilitePadding',
    'hilitePaddingNumber',
    'hilitePaddingValue',
    'hilitePaddingX',
    'hilitePaddingXNumber',
    'hilitePaddingXValue',
    'hilitePaddingY',
    'hilitePaddingYNumber',
    'hilitePaddingYValue',
    'minWidth',
    'minWidthNumber',
    'minWidthValue',
    'minHeight',
    'minHeightNumber',
    'minHeightValue',
    'shadowSize',
    'shadowSizeNumber',
    'shadowSizeValue',
    'shadowOffsetX',
    'shadowOffsetXNumber',
    'shadowOffsetXValue',
    'shadowOffsetY',
    'shadowOffsetYNumber',
    'shadowOffsetYValue',
    'displayName',
    'author',
    'skinId',
    'setActiveButton',
    'saveButton',
    'copyButton',
    'reloadBackupsButton',
    'backupList',
    'messageLog',
    'schemaSettingsPanel',
    'schemaSelect',
    'refreshSchemasButton',
    'schemaSettingsControls',
    'saveSchemaSettingsButton',
    'deployHint',
  ]) {
    dom[id] = document.getElementById(id);
  }
}

async function initialize() {
  if (!appCore) {
    throw new Error('核心脚本加载失败，请确认编辑器文件完整，并从 index.html 打开。');
  }
  const localSession = detectLocalLauncherSession(window.location);
  state.capability = detectBrowserCapabilities(window);
  state.preferredPlatform = detectPreferredPlatform(navigator);
  state.selectedPlatform = state.preferredPlatform === 'weasel' ? 'weasel' : 'squirrel';
  state.configs.squirrel = emptyConfig('squirrel');
  state.configs.weasel = emptyConfig('weasel');
  loadPreviewSettings();
  populateFontOptions();
  bindEvents();
  if (localSession) {
    await initializeLocalLauncher(localSession);
    return;
  }
  renderCapability();
  renderAll();
}

function bindEvents() {
  dom.chooseFolderButton.addEventListener('click', chooseFolder);
  dom.squirrelTab.addEventListener('click', () => selectPlatform('squirrel'));
  dom.weaselTab.addEventListener('click', () => selectPlatform('weasel'));
  dom.newSkinButton.addEventListener('click', createNewSkin);
  dom.duplicateSkinButton.addEventListener('click', duplicateCurrentSkin);
  dom.deleteSkinButton.addEventListener('click', deleteCurrentSkin);
  dom.importSkinButton.addEventListener('click', () => dom.skinImportFile.click());
  dom.exportSkinButton.addEventListener('click', exportCurrentSkin);
  dom.skinImportFile.addEventListener('change', importSkinFromFile);
  dom.previewScale.addEventListener('input', updatePreviewScale);
  dom.previewCode.addEventListener('input', updatePreviewCode);
  dom.previewCandidates.addEventListener('input', updatePreviewCandidates);
  dom.resetPreviewSettingsButton.addEventListener('click', resetPreviewSettings);
  dom.setActiveButton.addEventListener('click', setActiveSkin);
  dom.saveButton.addEventListener('click', saveCurrentSkin);
  dom.copyButton.addEventListener('click', copyCurrentSkin);
  dom.reloadBackupsButton.addEventListener('click', refreshBackups);
  dom.schemaSelect.addEventListener('change', () => {
    state.selectedSchemaId = dom.schemaSelect.value;
    renderSchemaSettings();
  });
  dom.refreshSchemasButton.addEventListener('click', refreshSchemaFiles);
  dom.saveSchemaSettingsButton.addEventListener('click', saveSchemaSettings);

  dom.displayName.addEventListener('input', () => updateDraftText('displayName', dom.displayName.value));
  dom.author.addEventListener('input', () => updateDraftText('author', dom.author.value));
  dom.skinId.addEventListener('change', () => updateDraftId(dom.skinId.value));
  dom.layoutMode.addEventListener('change', updateLayoutMode);
  dom.fontFace.addEventListener('change', updateFontFace);
  dom.labelFontFace.addEventListener('change', () => updateFontSelect('labelFontFace', dom.labelFontFace));
  dom.commentFontFace.addEventListener('change', () => updateFontSelect('commentFontFace', dom.commentFontFace));
  dom.numberStyle.addEventListener('change', () => updateNumberFormat('style'));
  dom.numberSeparator.addEventListener('change', () => updateNumberFormat('separator'));
  dom.candidateMarker.addEventListener('change', updateCandidateMarker);
  dom.preeditVisibility.addEventListener('change', updatePreeditVisibility);
  bindRange(dom.fontPoint, dom.fontPointNumber, dom.fontPointValue, 'fontPoint');
  bindRange(dom.labelFontPoint, dom.labelFontPointNumber, dom.labelFontPointValue, 'labelFontPoint');
  bindRange(dom.commentFontPoint, dom.commentFontPointNumber, dom.commentFontPointValue, 'commentFontPoint');
  bindRange(dom.cornerRadius, dom.cornerRadiusNumber, dom.cornerRadiusValue, 'cornerRadius');
  bindRange(dom.hilitedCornerRadius, dom.hilitedCornerRadiusNumber, dom.hilitedCornerRadiusValue, 'hilitedCornerRadius');
  bindRange(dom.candidateSpacing, dom.candidateSpacingNumber, dom.candidateSpacingValue, 'candidateSpacing');
  bindRange(dom.hiliteSpacing, dom.hiliteSpacingNumber, dom.hiliteSpacingValue, 'hiliteSpacing');
  bindRange(dom.lineSpacing, dom.lineSpacingNumber, dom.lineSpacingValue, 'lineSpacing');
  bindRange(dom.marginX, dom.marginXNumber, dom.marginXValue, 'marginX');
  bindRange(dom.marginY, dom.marginYNumber, dom.marginYValue, 'marginY');
  bindRange(dom.borderWidth, dom.borderWidthNumber, dom.borderWidthValue, 'borderWidth');
  bindRange(dom.borderHeight, dom.borderHeightNumber, dom.borderHeightValue, 'borderHeight');
  bindRange(dom.hilitePadding, dom.hilitePaddingNumber, dom.hilitePaddingValue, 'hilitePadding');
  bindRange(dom.hilitePaddingX, dom.hilitePaddingXNumber, dom.hilitePaddingXValue, 'hilitePaddingX');
  bindRange(dom.hilitePaddingY, dom.hilitePaddingYNumber, dom.hilitePaddingYValue, 'hilitePaddingY');
  bindRange(dom.minWidth, dom.minWidthNumber, dom.minWidthValue, 'minWidth');
  bindRange(dom.minHeight, dom.minHeightNumber, dom.minHeightValue, 'minHeight');
  bindRange(dom.shadowSize, dom.shadowSizeNumber, dom.shadowSizeValue, 'shadowSize');
  bindRange(dom.shadowOffsetX, dom.shadowOffsetXNumber, dom.shadowOffsetXValue, 'shadowOffsetX');
  bindRange(dom.shadowOffsetY, dom.shadowOffsetYNumber, dom.shadowOffsetYValue, 'shadowOffsetY');
}

function bindRange(rangeInput, numberInput, output, field) {
  const sync = (source) => {
    const value = clampNumber(source.value, Number(rangeInput.min), Number(rangeInput.max));
    rangeInput.value = String(value);
    numberInput.value = String(value);
    output.value = String(value);
    updateDraftLayout(field, value);
  };
  rangeInput.addEventListener('input', () => sync(rangeInput));
  numberInput.addEventListener('input', () => sync(numberInput));
}

function renderCapability() {
  if (state.capability.canEditLocalRime) {
    dom.supportStatus.textContent = '浏览器支持本地配置文件夹读写。';
    dom.blockedPanel.classList.add('hidden');
    return;
  }

  dom.supportStatus.textContent = '当前浏览器不支持直接编辑。';
  dom.blockedReason.textContent = state.capability.reason;
  dom.blockedPanel.classList.remove('hidden');
  dom.chooseFolderButton.disabled = true;
}

async function initializeLocalLauncher(session) {
  state.storageMode = 'local';
  state.localApiToken = session.token;
  dom.chooseFolderButton.disabled = true;
  dom.chooseFolderButton.textContent = '已连接本地启动器';
  dom.supportStatus.textContent = '正在读取本地启动器配置...';
  const loaded = await loadConfigsFromLocalApi();
  if (!looksLikeRimeFolder(loaded)) {
    dom.supportStatus.textContent = '本地启动器已连接，但当前目录不像 Rime 配置目录。';
    dom.blockedReason.textContent = '请把 Rime皮肤编辑器 文件夹放在 Rime 配置目录里，再双击启动器。';
    dom.blockedPanel.classList.remove('hidden');
    return;
  }
  applyLoadedConfigs(null, loaded);
  dom.folderName.textContent = state.folderName;
  chooseInitialPlatform();
  await refreshBackups();
  dom.workspace.classList.remove('hidden');
  dom.supportStatus.textContent = '本地启动器已连接，可直接编辑当前 Rime 配置目录。';
  startLocalLauncherHeartbeat();
  logMessage('已通过本地启动器读取当前配置目录。');
}

function renderStartupError(error) {
  const message = error?.message || '未知启动错误。';
  if (dom.supportStatus) dom.supportStatus.textContent = `编辑器启动失败：${message}`;
  if (dom.blockedReason) dom.blockedReason.textContent = message;
  if (dom.blockedPanel) dom.blockedPanel.classList.remove('hidden');
  if (dom.chooseFolderButton) dom.chooseFolderButton.disabled = true;
}

async function chooseFolder() {
  try {
    const dirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
    const loaded = await loadConfigsFromHandle(dirHandle);
    if (!looksLikeRimeFolder(loaded)) {
      dom.workspace.classList.add('hidden');
      logMessage('选择的文件夹不像 Rime 配置文件夹：未找到前端配置、方案配置或 default.custom.yaml。');
      return;
    }
    applyLoadedConfigs(dirHandle, loaded);
    dom.folderName.textContent = state.folderName;
    chooseInitialPlatform();
    await refreshBackups();
    dom.workspace.classList.remove('hidden');
    logMessage('已读取配置文件夹。');
  } catch (error) {
    if (error?.name === 'AbortError') return;
    dom.workspace.classList.add('hidden');
    logMessage(`选择文件夹失败：${error.message}`);
  }
}

async function loadConfigs() {
  const loaded = state.storageMode === 'local'
    ? await loadConfigsFromLocalApi()
    : await loadConfigsFromHandle(state.dirHandle);
  applyLoadedConfigs(state.dirHandle, loaded);
}

async function loadConfigsFromLocalApi() {
  const snapshot = await localApi('config');
  const customFiles = snapshot.customFiles || [];
  return {
    folderName: snapshot.folderName || '本地 Rime 配置目录',
    rootPath: snapshot.rootPath || '',
    configs: {
      squirrel: snapshot.rawFiles?.squirrel ? parseSquirrelConfig(snapshot.rawFiles.squirrel) : emptyConfig('squirrel'),
      weasel: snapshot.rawFiles?.weasel ? parseWeaselConfig(snapshot.rawFiles.weasel) : emptyConfig('weasel'),
    },
    rawFiles: {
      squirrel: snapshot.rawFiles?.squirrel || '',
      weasel: snapshot.rawFiles?.weasel || '',
      default: snapshot.rawFiles?.default || '',
      user: snapshot.rawFiles?.user || '',
    },
    customFiles,
    yamlFiles: snapshot.yamlFiles || [],
    deploySupported: Boolean(snapshot.deploySupported),
    currentSchemaId: parseUserSelectedSchema(snapshot.rawFiles?.user || ''),
    fileExists: {
      squirrel: Boolean(snapshot.fileExists?.squirrel),
      weasel: Boolean(snapshot.fileExists?.weasel),
      default: Boolean(snapshot.fileExists?.default),
    },
    hasAnySchemaFile: Boolean(snapshot.hasAnySchemaFile),
  };
}

async function loadConfigsFromHandle(dirHandle) {
  const loaded = {
    folderName: dirHandle.name || '已选择的 Rime 配置文件夹',
    configs: {},
    rawFiles: {},
    fileExists: {},
    customFiles: [],
    yamlFiles: [],
    deploySupported: false,
    globalLayout: {},
    globalSources: {},
    currentSchemaId: '',
    hasAnySchemaFile: false,
  };
  for (const platform of Object.keys(PLATFORM_FILES)) {
    const filename = PLATFORM_FILES[platform];
    const entry = await readOptionalFileEntry(filename, dirHandle);
    const text = entry.text;
    loaded.fileExists[platform] = entry.exists;
    loaded.rawFiles[platform] = text;
    loaded.configs[platform] = text ? parseConfig(platform, text) : emptyConfig(platform);
  }
  const defaultEntry = await readOptionalFileEntry('default.custom.yaml', dirHandle);
  loaded.fileExists.default = defaultEntry.exists;
  loaded.rawFiles.default = defaultEntry.text;
  const userEntry = await readOptionalFileEntry('user.yaml', dirHandle);
  loaded.rawFiles.user = userEntry.text;
  loaded.currentSchemaId = parseUserSelectedSchema(userEntry.text);
  loaded.customFiles = await readRootCustomFiles(dirHandle);
  loaded.yamlFiles = await readRootYamlFiles(dirHandle);
  loaded.hasAnySchemaFile = await directoryHasSchemaFile(dirHandle);
  return loaded;
}

function applyLoadedConfigs(dirHandle, loaded) {
  state.dirHandle = dirHandle;
  state.folderName = loaded.rootPath || loaded.folderName;
  state.configs.squirrel = loaded.configs.squirrel;
  state.configs.weasel = loaded.configs.weasel;
  state.rawFiles.squirrel = loaded.rawFiles.squirrel;
  state.rawFiles.weasel = loaded.rawFiles.weasel;
  state.rawFiles.default = loaded.rawFiles.default;
  state.rawFiles.user = loaded.rawFiles.user || '';
  state.customFiles = loaded.customFiles || [];
  state.yamlFiles = loaded.yamlFiles || [];
  state.currentSchemaId = loaded.currentSchemaId || '';
  if (state.currentSchemaId) state.selectedSchemaId = state.currentSchemaId;
  state.deploySupported = Boolean(loaded.deploySupported);
  state.globalLayout = loaded.globalLayout || {};
  state.globalSources = loaded.globalSources || {};
  state.fileExists.squirrel = loaded.fileExists.squirrel;
  state.fileExists.weasel = loaded.fileExists.weasel;
  state.fileExists.default = loaded.fileExists.default;
  state.hasAnySchemaFile = loaded.hasAnySchemaFile;
  refreshSchemaCatalog();
}

async function readRootYamlFiles(dirHandle) {
  const files = [];
  for await (const [name, handle] of dirHandle.entries()) {
    if (handle.kind !== 'file' || !name.endsWith('.yaml') || name.endsWith('.dict.yaml') || name.endsWith('.custom.yaml')) continue;
    if (name === 'installation.yaml' || name === 'user.yaml') continue;
    const file = await handle.getFile();
    files.push({ name, text: await file.text() });
  }
  return files.sort((left, right) => left.name.localeCompare(right.name));
}

function refreshSchemaCatalog() {
  if (typeof buildSchemaCatalog !== 'function') return;
  state.schemas = buildSchemaCatalog(state.yamlFiles, state.rawFiles.default);
  const enabled = state.schemas.filter((schema) => schema.enabled);
  if (!state.schemas.some((schema) => schema.id === state.currentSchemaId)) {
    state.currentSchemaId = enabled[0]?.id || state.schemas[0]?.id || '';
  }
  const capable = state.schemas.filter((schema) => discoverSchemaSettings(schema, state.yamlFiles).length);
  if (!capable.some((schema) => schema.id === state.selectedSchemaId)) {
    state.selectedSchemaId = capable.some((schema) => schema.id === state.currentSchemaId)
      ? state.currentSchemaId
      : capable[0]?.id || '';
  }
  applyCurrentSchemaLayout();
  renderSchemaSettings();
}

function currentSchemaCustomFiles() {
  if (typeof customFilesForSchema !== 'function') return [];
  return customFilesForSchema(state.currentSchemaId, state.customFiles);
}

function applyCurrentSchemaLayout() {
  const scoped = parseGlobalCustomFiles(currentSchemaCustomFiles());
  state.globalLayout = scoped.layout;
  state.globalSources = scoped.sources;
  for (const platform of Object.keys(PLATFORM_FILES)) {
    const base = state.rawFiles[platform] ? parseConfig(platform, state.rawFiles[platform]) : emptyConfig(platform);
    state.configs[platform] = mergeGlobalLayoutIntoConfig(base, state.globalLayout);
  }
}

function currentSchemaSettingValue(settingId) {
  const schema = state.schemas.find((item) => item.id === state.currentSchemaId);
  const setting = discoverSchemaSettings(schema, state.yamlFiles).find((item) => item.id === settingId);
  if (!setting) return '';
  for (const file of currentSchemaCustomFiles()) {
    const patch = parseYaml(file.text).patch || {};
    if (Object.prototype.hasOwnProperty.call(patch, setting.path)) return String(patch[setting.path] ?? '');
  }
  return setting.value;
}

async function refreshSchemaFiles() {
  try {
    const loaded = state.storageMode === 'local' ? await loadConfigsFromLocalApi() : await loadConfigsFromHandle(state.dirHandle);
    applyLoadedConfigs(state.dirHandle, loaded);
    renderSchemaSettings();
    logMessage('已重新扫描输入方案。');
  } catch (error) {
    logMessage(`扫描方案失败：${error.message}`);
  }
}

function renderSchemaSettings() {
  if (!dom.schemaSettingsPanel || typeof discoverSchemaSettings !== 'function') return;
  dom.schemaSelect.replaceChildren();
  const capable = state.schemas.filter((item) => discoverSchemaSettings(item, state.yamlFiles).length);
  for (const schema of capable) {
    const option = document.createElement('option');
    option.value = schema.id;
    option.textContent = `${schema.name}${schema.enabled ? ' · 已启用' : ''}`;
    dom.schemaSelect.append(option);
  }
  dom.schemaSelect.value = state.selectedSchemaId;
  const schema = state.schemas.find((item) => item.id === state.selectedSchemaId);
  state.schemaSettings = discoverSchemaSettings(schema, state.yamlFiles);
  const custom = state.customFiles.find((file) => file.name === `${state.selectedSchemaId}.custom.yaml`);
  const patch = custom ? (parseYaml(custom.text).patch || {}) : {};
  dom.schemaSettingsControls.replaceChildren();
  for (const setting of state.schemaSettings) {
    const label = document.createElement('label');
    const title = document.createElement('span');
    title.textContent = setting.label;
    const input = document.createElement('input');
    input.type = 'text';
    input.value = Object.prototype.hasOwnProperty.call(patch, setting.path) ? String(patch[setting.path]) : setting.value;
    input.dataset.settingPath = setting.path;
    label.append(title, input);
    dom.schemaSettingsControls.append(label);
  }
  const visible = Boolean(capable.length && state.schemaSettings.length);
  dom.schemaSettingsPanel.hidden = !visible;
}

async function saveSchemaSettings() {
  const schema = state.schemas.find((item) => item.id === state.selectedSchemaId);
  if (!schema || !state.schemaSettings.length || !hasWritableStorage()) return;
  const filename = `${schema.id}.custom.yaml`;
  try {
    const current = await readOptionalFileEntry(filename);
    let output = current.exists ? current.text : 'patch:\n';
    for (const input of dom.schemaSettingsControls.querySelectorAll('[data-setting-path]')) {
      output = updateSchemaSetting(output, input.dataset.settingPath, input.value);
    }
    await backupFiles(`保存方案设置-${schema.id}`, [filename], {
      operation: 'save', sourcePlatform: '', targetPlatform: '', skinIdBefore: schema.id, skinIdAfter: schema.id,
      createdFiles: current.exists ? [] : [filename],
    }, { [filename]: current });
    await writeFile(filename, output);
    upsertCustomFileState(filename, output);
    refreshSchemaCatalog();
    await refreshBackups();
    renderSchemaSettings();
    await deployAfterWrite(`已保存「${schema.name}」方案设置`);
  } catch (error) {
    logMessage(`保存方案设置失败：${error.message}`);
  }
}

async function readRootCustomFiles(dirHandle) {
  const files = [];
  try {
    for await (const [name, handle] of dirHandle.entries()) {
      if (
        handle.kind !== 'file' ||
        !name.endsWith('.custom.yaml') ||
        Object.values(PLATFORM_FILES).includes(name) ||
        name === 'default.custom.yaml'
      ) {
        continue;
      }
      const file = await handle.getFile();
      files.push({ name, text: await file.text() });
    }
  } catch (error) {
    logMessage(`扫描全局 custom 配置失败：${error.message}`);
  }
  return files.sort((a, b) => a.name.localeCompare(b.name));
}

function mergeGlobalLayoutIntoConfig(config, globalLayout = {}) {
  const labels = globalLayout.alternativeSelectLabels;
  if (!Array.isArray(labels) || !labels.length) return config;
  return {
    ...config,
    skins: config.skins.map((skin) => ({
      ...skin,
      layout: {
        alternativeSelectLabels: labels,
        ...(skin.layout || {}),
      },
    })),
  };
}

function chooseInitialPlatform() {
  const hasSquirrel = state.fileExists.squirrel;
  const hasWeasel = state.fileExists.weasel;
  if (hasSquirrel && !hasWeasel) state.selectedPlatform = 'squirrel';
  else if (hasWeasel && !hasSquirrel) state.selectedPlatform = 'weasel';
  else state.selectedPlatform = state.preferredPlatform === 'weasel' ? 'weasel' : 'squirrel';

  const active = state.configs[state.selectedPlatform].activeSkinId;
  const first = state.configs[state.selectedPlatform].skins[0]?.id || '';
  selectSkin(active || first);
}

async function readOptionalFile(filename, dirHandle = state.dirHandle) {
  return (await readOptionalFileEntry(filename, dirHandle)).text;
}

async function readOptionalFileEntry(filename, dirHandle = state.dirHandle) {
  if (state.storageMode === 'local') {
    try {
      const result = await localApi(`file?path=${encodeURIComponent(filename)}`);
      return { exists: Boolean(result.exists), text: result.text || '' };
    } catch (error) {
      if (error.status === 404) return { exists: false, text: '' };
      throw error;
    }
  }
  try {
    const handle = await dirHandle.getFileHandle(filename);
    const file = await handle.getFile();
    return { exists: true, text: await file.text() };
  } catch (error) {
    if (error?.name === 'NotFoundError') return { exists: false, text: '' };
    throw error;
  }
}

async function directoryHasSchemaFile(dirHandle = state.dirHandle) {
  if (state.storageMode === 'local') return state.hasAnySchemaFile;
  try {
    for await (const [name, handle] of dirHandle.entries()) {
      if (handle.kind === 'file' && name.endsWith('.schema.yaml')) return true;
    }
  } catch (error) {
    logMessage(`扫描方案文件失败：${error.message}`);
  }
  return false;
}

function parseConfig(platform, text) {
  return platform === 'squirrel' ? parseSquirrelConfig(text) : parseWeaselConfig(text);
}

async function readAndValidateFrontendFile(platform) {
  const filename = PLATFORM_FILES[platform];
  const entry = await readOptionalFileEntry(filename);
  if (!entry.exists) return { exists: false, text: '', config: emptyConfig(platform) };
  const config = mergeGlobalLayoutIntoConfig(parseConfig(platform, entry.text), state.globalLayout);
  return { exists: true, text: entry.text, config };
}

function emptyConfig(platform) {
  return {
    platform,
    document: { patch: platform === 'squirrel' ? { preset_color_schemes: {}, style: {} } : {} },
    skins: [],
    activeSkinId: '',
    darkSkinId: '',
  };
}

function looksLikeRimeFolder(snapshot = state) {
  return Boolean(
    snapshot.fileExists.squirrel ||
      snapshot.fileExists.weasel ||
      snapshot.fileExists.default ||
      snapshot.hasAnySchemaFile,
  );
}

function selectPlatform(platform) {
  state.selectedPlatform = platform;
  const active = state.configs[platform].activeSkinId;
  const first = state.configs[platform].skins[0]?.id || '';
  selectSkin(active || first);
}

function selectSkin(id) {
  const config = state.configs[state.selectedPlatform];
  const skin = config.skins.find((item) => item.id === id) || config.skins[0] || null;
  state.selectedSkinId = skin?.id || '';
  state.originalSkinId = skin?.id || '';
  state.draftSkin = skin ? cloneSkin(skin) : null;
  state.previewActiveCandidateIndex = 0;
  rememberGlobalLayoutSources();
  rememberOriginalLayout();
  renderAll();
}

function createNewSkin() {
  const config = state.configs[state.selectedPlatform];
  const id = generateSkinId('新皮肤', config.skins.map((skin) => skin.id));
  state.draftSkin = {
    platform: state.selectedPlatform,
    id,
    displayName: '新皮肤',
    author: '',
    colors: cloneJson(DEFAULT_COLORS),
    layout: defaultLayoutForPlatform(state.selectedPlatform),
    unsupportedFields: {},
  };
  rememberGlobalLayoutSources();
  state.selectedSkinId = id;
  state.originalSkinId = '';
  state.previewActiveCandidateIndex = 0;
  renderAll();
}

function duplicateCurrentSkin() {
  if (!state.draftSkin) return;
  const config = state.configs[state.selectedPlatform];
  const id = generateSkinId(`${state.draftSkin.id}_copy`, config.skins.map((skin) => skin.id));
  state.draftSkin = {
    ...cloneSkin(state.draftSkin),
    id,
    displayName: `${state.draftSkin.displayName || state.draftSkin.id} 副本`,
  };
  state.selectedSkinId = id;
  state.originalSkinId = '';
  state.previewActiveCandidateIndex = 0;
  rememberGlobalLayoutSources();
  renderAll();
}

function exportCurrentSkin() {
  if (!state.draftSkin) return;
  try {
    const packageText = createSkinPackage(state.draftSkin, {
      sourcePlatform: state.selectedPlatform,
    });
    const filename = `${state.draftSkin.id || 'rime-skin'}.rime-skin.json`;
    downloadTextFile(filename, packageText);
    logMessage(`已导出皮肤包：${filename}`);
  } catch (error) {
    logMessage(`导出失败：${error.message}`);
  }
}

async function importSkinFromFile() {
  const file = dom.skinImportFile.files?.[0];
  dom.skinImportFile.value = '';
  if (!file) return;
  try {
    const packageText = await file.text();
    const skinPackage = parseSkinPackage(packageText);
    const sourceSkin = skinPackage.skin;
    const imported = sourceSkin.platform && sourceSkin.platform !== state.selectedPlatform
      ? copySkinToPlatform(sourceSkin, state.selectedPlatform)
      : cloneSkin({ ...sourceSkin, platform: state.selectedPlatform });
    const config = state.configs[state.selectedPlatform];
    imported.id = generateSkinId(imported.id || imported.displayName, config.skins.map((skin) => skin.id));
    imported.displayName ||= imported.id;
    imported.unsupportedFields ||= {};
    state.draftSkin = imported;
    state.selectedSkinId = imported.id;
    state.originalSkinId = '';
    state.previewActiveCandidateIndex = 0;
    rememberGlobalLayoutSources();
    rememberOriginalLayout();
    renderAll();
    logMessage(`已导入皮肤包：${imported.displayName}。预览确认后可保存。`);
  } catch (error) {
    logMessage(`导入失败：${error.message}`);
  }
}

async function deleteCurrentSkin() {
  if (!state.draftSkin || !hasWritableStorage()) return;
  const config = state.configs[state.selectedPlatform];
  if (state.originalSkinId && state.draftSkin.id !== state.originalSkinId) {
    logMessage('当前皮肤 ID 已改动。请先保存为新皮肤，或重新选择原皮肤后再删除。');
    return;
  }
  const deleteId = state.originalSkinId || state.draftSkin.id;
  const persisted = config.skins.some((skin) => skin.id === deleteId);
  if (!persisted) {
    state.draftSkin = null;
    state.selectedSkinId = '';
    state.originalSkinId = '';
    selectSkin(config.activeSkinId || config.skins[0]?.id || '');
    logMessage('已丢弃未保存的皮肤草稿。');
    return;
  }
  const confirmed = window.confirm(`确认删除皮肤「${state.draftSkin.displayName}」？保存前会先备份当前配置。`);
  if (!confirmed) return;

  try {
    const platform = state.selectedPlatform;
    const filename = PLATFORM_FILES[platform];
    const current = await readAndValidateFrontendFile(platform);
    if (!current.exists) throw new Error(`${filename} 不存在，无法删除已保存皮肤。`);
    const operation = `删除${PLATFORM_LABELS[platform]}-${deleteId}`;
    await backupFiles(operation, [filename], {
      operation: 'delete',
      sourcePlatform: platform,
      targetPlatform: '',
      skinIdBefore: deleteId,
      skinIdAfter: '',
    }, { [filename]: current });
    const output = platform === 'squirrel'
      ? deleteSquirrelSkinConfig(current.text, deleteId)
      : deleteWeaselSkinConfig(current.text, deleteId);
    await writeFile(filename, output);
    const written = await readOptionalFileEntry(filename);
    if (!written.exists) throw new Error(`删除后无法重新读取 ${filename}`);
    state.rawFiles[platform] = written.text;
    state.configs[platform] = mergeGlobalLayoutIntoConfig(parseConfig(platform, written.text), state.globalLayout);
    const next = state.configs[platform].activeSkinId || state.configs[platform].skins[0]?.id || '';
    await refreshBackups();
    selectSkin(next);
    await deployAfterWrite('已删除并备份');
  } catch (error) {
    logMessage(`删除失败：${error.message}`);
  }
}

function renderAll() {
  renderPlatform();
  renderSkinList();
  renderEditor();
  renderPreview();
  renderBackupList();
  renderSchemaSettings();
}

function renderPlatform() {
  dom.squirrelTab.classList.toggle('active', state.selectedPlatform === 'squirrel');
  dom.weaselTab.classList.toggle('active', state.selectedPlatform === 'weasel');
  const detected = state.preferredPlatform === 'squirrel' ? '检测到 macOS，默认鼠须管。'
    : state.preferredPlatform === 'weasel' ? '检测到 Windows，默认小狼毫。'
      : '未识别当前 Rime 前端，可手动选择平台。';
  dom.platformHint.textContent = detected;
  const canDeploy = state.storageMode === 'local' && state.deploySupported;
  dom.setActiveButton.textContent = canDeploy ? '保存、设为当前并应用' : '保存并设为当前';
  dom.deployHint.textContent = canDeploy
    ? '保存和回退后可由本地启动器重新部署 Rime。'
    : '当前模式不能自动部署，保存后请手动重新部署 Rime。';
}

function renderSkinList() {
  const config = state.configs[state.selectedPlatform];
  dom.skinList.replaceChildren();
  if (!config.skins.length && !state.draftSkin) {
    const empty = document.createElement('p');
    empty.className = 'hint';
    empty.textContent = '还没有皮肤，点击“新建”。';
    dom.skinList.append(empty);
    return;
  }

  for (const skin of config.skins) {
    const button = document.createElement('button');
    button.className = `skin-item ${skin.id === state.selectedSkinId ? 'active' : ''}`;
    button.innerHTML = `<span class="skin-title">${escapeHtml(skin.displayName)}</span><span class="skin-meta">${escapeHtml(skin.id)}${skin.id === config.activeSkinId ? ' · 当前' : ''}${skin.id === config.darkSkinId ? ' · 暗色' : ''}</span>`;
    button.addEventListener('click', () => selectSkin(skin.id));
    dom.skinList.append(button);
  }

  if (state.draftSkin && !config.skins.some((skin) => skin.id === state.draftSkin.id)) {
    const button = document.createElement('button');
    button.className = 'skin-item active';
    button.innerHTML = `<span class="skin-title">${escapeHtml(state.draftSkin.displayName)}</span><span class="skin-meta">${escapeHtml(state.draftSkin.id)} · 未保存</span>`;
    dom.skinList.prepend(button);
  }
}

function renderEditor() {
  const skin = state.draftSkin;
  const disabled = !skin;
  const canDelete = !disabled;
  for (const element of [
    dom.displayName,
    dom.author,
    dom.skinId,
    dom.layoutMode,
    dom.fontFace,
    dom.labelFontFace,
    dom.commentFontFace,
    dom.numberStyle,
    dom.numberSeparator,
    dom.candidateMarker,
    dom.preeditVisibility,
    dom.fontPoint,
    dom.fontPointNumber,
    dom.labelFontPoint,
    dom.labelFontPointNumber,
    dom.commentFontPoint,
    dom.commentFontPointNumber,
    dom.cornerRadius,
    dom.cornerRadiusNumber,
    dom.hilitedCornerRadius,
    dom.hilitedCornerRadiusNumber,
    dom.candidateSpacing,
    dom.candidateSpacingNumber,
    dom.hiliteSpacing,
    dom.hiliteSpacingNumber,
    dom.lineSpacing,
    dom.lineSpacingNumber,
    dom.marginX,
    dom.marginXNumber,
    dom.marginY,
    dom.marginYNumber,
    dom.borderWidth,
    dom.borderWidthNumber,
    dom.borderHeight,
    dom.borderHeightNumber,
    dom.hilitePadding,
    dom.hilitePaddingNumber,
    dom.hilitePaddingX,
    dom.hilitePaddingXNumber,
    dom.hilitePaddingY,
    dom.hilitePaddingYNumber,
    dom.minWidth,
    dom.minWidthNumber,
    dom.minHeight,
    dom.minHeightNumber,
    dom.shadowSize,
    dom.shadowSizeNumber,
    dom.shadowOffsetX,
    dom.shadowOffsetXNumber,
    dom.shadowOffsetY,
    dom.shadowOffsetYNumber,
    dom.setActiveButton,
    dom.saveButton,
    dom.copyButton,
    dom.duplicateSkinButton,
    dom.exportSkinButton,
  ]) {
    element.disabled = disabled;
  }
  dom.deleteSkinButton.disabled = !canDelete;
  dom.importSkinButton.disabled = false;
  const target = state.selectedPlatform === 'squirrel' ? 'weasel' : 'squirrel';
  dom.copyButton.textContent = `复制到${PLATFORM_LABELS[target]}`;
  dom.colorControls.replaceChildren();
  if (!skin) return;

  dom.displayName.value = skin.displayName || '';
  dom.author.value = skin.author || '';
  dom.skinId.value = skin.id || '';
  dom.previewPlatform.textContent = PLATFORM_LABELS[state.selectedPlatform];

  const layout = normalizedLayout(skin);
  dom.layoutMode.value = layout.mode;
  syncSelect(dom.fontFace, layout.fontFace);
  syncSelect(dom.labelFontFace, layout.labelFontFace);
  syncSelect(dom.commentFontFace, layout.commentFontFace);
  updateFontHint();
  syncSelect(dom.numberStyle, layout.numberStyle, numberStyleOptionLabel(layout));
  syncSelect(dom.numberSeparator, layout.numberSeparator, numberSeparatorOptionLabel(layout));
  syncSelect(dom.candidateMarker, layout.markText);
  syncCandidateMarkerControl();
  dom.preeditVisibility.value = layout.preeditVisibility;
  setRange(dom.fontPoint, dom.fontPointNumber, dom.fontPointValue, layout.fontPoint);
  setRange(dom.labelFontPoint, dom.labelFontPointNumber, dom.labelFontPointValue, layout.labelFontPoint);
  setRange(dom.commentFontPoint, dom.commentFontPointNumber, dom.commentFontPointValue, layout.commentFontPoint);
  setRange(dom.cornerRadius, dom.cornerRadiusNumber, dom.cornerRadiusValue, layout.cornerRadius);
  setRange(dom.hilitedCornerRadius, dom.hilitedCornerRadiusNumber, dom.hilitedCornerRadiusValue, layout.hilitedCornerRadius);
  setRange(dom.candidateSpacing, dom.candidateSpacingNumber, dom.candidateSpacingValue, layout.candidateSpacing);
  setRange(dom.hiliteSpacing, dom.hiliteSpacingNumber, dom.hiliteSpacingValue, layout.hiliteSpacing);
  setRange(dom.lineSpacing, dom.lineSpacingNumber, dom.lineSpacingValue, layout.lineSpacing);
  setRange(dom.marginX, dom.marginXNumber, dom.marginXValue, layout.marginX);
  setRange(dom.marginY, dom.marginYNumber, dom.marginYValue, layout.marginY);
  setRange(dom.borderWidth, dom.borderWidthNumber, dom.borderWidthValue, layout.borderWidth);
  setRange(dom.borderHeight, dom.borderHeightNumber, dom.borderHeightValue, layout.borderHeight);
  setRange(dom.hilitePadding, dom.hilitePaddingNumber, dom.hilitePaddingValue, layout.hilitePadding);
  setRange(dom.hilitePaddingX, dom.hilitePaddingXNumber, dom.hilitePaddingXValue, layout.hilitePaddingX);
  setRange(dom.hilitePaddingY, dom.hilitePaddingYNumber, dom.hilitePaddingYValue, layout.hilitePaddingY);
  setRange(dom.minWidth, dom.minWidthNumber, dom.minWidthValue, layout.minWidth);
  setRange(dom.minHeight, dom.minHeightNumber, dom.minHeightValue, layout.minHeight);
  setRange(dom.shadowSize, dom.shadowSizeNumber, dom.shadowSizeValue, layout.shadowSize);
  setRange(dom.shadowOffsetX, dom.shadowOffsetXNumber, dom.shadowOffsetXValue, layout.shadowOffsetX);
  setRange(dom.shadowOffsetY, dom.shadowOffsetYNumber, dom.shadowOffsetYValue, layout.shadowOffsetY);

  for (const [role, label] of COLOR_CONTROLS) {
    if (role === 'hilitedMark' && state.selectedPlatform !== 'weasel') continue;
    const wrapper = document.createElement('label');
    const color = skin.colors?.[role] || DEFAULT_COLORS[role] || DEFAULT_COLORS.back;
    wrapper.className = 'color-control';
    wrapper.innerHTML = `<span>${label}</span>`;
    const input = document.createElement('input');
    input.type = 'color';
    input.value = rgbaToCssHex(color);
    input.addEventListener('input', () => updateDraftColor(role, cssHexToRgba(input.value)));
    const alphaRow = document.createElement('div');
    alphaRow.className = 'alpha-row';
    const alpha = document.createElement('input');
    alpha.type = 'range';
    alpha.min = '0';
    alpha.max = '255';
    alpha.step = '1';
    alpha.value = String(color.a ?? 255);
    const alphaValue = document.createElement('output');
    alphaValue.value = String(color.a ?? 255);
    alpha.addEventListener('input', () => {
      alphaValue.value = alpha.value;
      updateDraftAlpha(role, Number(alpha.value));
    });
    alphaRow.append(alpha, alphaValue);
    wrapper.append(input, alphaRow);
    dom.colorControls.append(wrapper);
  }
}

function renderPreview() {
  const skin = state.draftSkin;
  if (!skin) return;
  const colors = colorsForPreview(skin.colors || {});
  const layout = normalizedLayout(skin);
  const preview = dom.candidatePreview;
  const preedit = dom.previewPreedit;
  const row = preview.querySelector('.candidate-row') || document.createElement('div');
  if (!row.parentElement && !preview.children?.includes?.(row)) preview.append(row);

  dom.previewStage.style.fontFamily = layout.fontFace;
  dom.previewStage.style.fontSize = `${layout.fontPoint}px`;
  dom.previewStage.style.setProperty('--preview-scale', String(state.previewScale / 100));
  if (dom.previewScale) dom.previewScale.value = String(state.previewScale);
  if (dom.previewScaleValue) dom.previewScaleValue.textContent = `${state.previewScale}%`;
  if (dom.previewCode && dom.previewCode.value !== state.previewCode) dom.previewCode.value = state.previewCode;
  if (dom.previewCandidates && dom.previewCandidates.value !== state.previewCandidatesText) {
    dom.previewCandidates.value = state.previewCandidatesText;
  }

  preedit.textContent = preeditTextForLayout(layout);
  preedit.hidden = !shouldShowPreedit(layout);
  preedit.style.color = rgbaToCss(colorRole(colors, 'text'));
  preedit.style.backgroundColor = rgbaToCss(colorRole(colors, 'preeditBack'));
  preedit.style.fontFamily = layout.fontFace;
  preedit.style.fontSize = `${layout.fontPoint}px`;
  preedit.style.textDecorationColor = rgbaToCss(colorRole(colors, 'text'));

  preview.style.backgroundColor = rgbaToCss(colors.back);
  preview.style.borderColor = rgbaToCss(colors.border || colors.back);
  preview.style.borderStyle = 'solid';
  preview.style.borderWidth = '0';
  preview.style.borderRadius = `${layout.cornerRadius}px`;
  preview.style.fontFamily = layout.fontFace;
  preview.style.fontSize = `${layout.fontPoint}px`;
  preview.style.padding = `${layout.marginY}px ${layout.marginX}px`;
  preview.style.minWidth = `${layout.minWidth}px`;
  preview.style.minHeight = `${layout.minHeight}px`;
  preview.style.boxShadow = previewBoxShadow(layout, colors);
  row.classList.toggle('stacked', layout.mode === 'stacked');
  row.classList.toggle('linear', layout.mode === 'linear');
  row.style.display = layout.mode === 'stacked' ? 'grid' : 'flex';
  row.style.gridTemplateColumns = layout.mode === 'stacked' ? '1fr' : '';
  row.style.columnGap = `${layout.candidateSpacing}px`;
  row.style.rowGap = `${layout.lineSpacing}px`;
  row.replaceChildren(...previewNodes(layout, colors));
}

function renderBackupList() {
  dom.backupList.replaceChildren();
  if (!state.backups.length) {
    const empty = document.createElement('p');
    empty.className = 'hint';
    empty.textContent = '暂无备份。';
    dom.backupList.append(empty);
    return;
  }
  for (const backup of state.backups) {
    const item = document.createElement('div');
    item.className = 'backup-item';
    const summary = manifestSummary(backup.manifest);
    const backupFiles = [
      ...(backup.manifest?.files || []),
      ...(backup.manifest?.createdFiles || []).map((file) => `${file}（新建）`),
    ];
    const files = backupFiles.join(', ') || '未知文件';
    const body = document.createElement('button');
    body.className = 'backup-restore';
    body.innerHTML = `<span class="skin-title">${escapeHtml(backup.name)}</span><span class="backup-meta">${escapeHtml(summary)} · ${escapeHtml(files)}</span>`;
    body.addEventListener('click', () => rollbackBackup(backup));
    const deleteButton = document.createElement('button');
    deleteButton.className = 'backup-delete';
    deleteButton.type = 'button';
    deleteButton.textContent = '删除';
    deleteButton.addEventListener('click', async (event) => {
      event.stopPropagation?.();
      await deleteBackupEntry(backup);
    });
    item.append(body, deleteButton);
    dom.backupList.append(item);
  }
}

function syncCandidateMarkerControl() {
  if (!dom.candidateMarker) return;
  const isWeasel = state.selectedPlatform === 'weasel';
  dom.candidateMarker.disabled = !isWeasel;
  dom.candidateMarkerField.hidden = !isWeasel;
  dom.candidateMarker.title = isWeasel
    ? ''
    : '鼠须管不支持小狼毫的 style/mark_text 候选标记。';
  dom.candidateMarkerField?.classList.toggle('disabled-control', !isWeasel);
}

function updateDraftText(field, value) {
  if (!state.draftSkin) return;
  state.draftSkin[field] = value;
  if (field === 'displayName') renderSkinList();
  renderPreview();
}

function updateDraftId(value) {
  if (!state.draftSkin) return;
  const safe = generateSkinId(value || state.draftSkin.displayName, []);
  state.draftSkin.id = safe;
  dom.skinId.value = safe;
  state.selectedSkinId = safe;
  renderSkinList();
}

function updatePreviewScale() {
  state.previewScale = clampNumber(dom.previewScale.value, Number(dom.previewScale.min), Number(dom.previewScale.max));
  savePreviewSettings();
  renderPreview();
}

function updatePreviewCode() {
  state.previewCode = dom.previewCode.value;
  savePreviewSettings();
  renderPreview();
}

function updatePreviewCandidates() {
  state.previewCandidatesText = dom.previewCandidates.value;
  state.previewActiveCandidateIndex = Math.min(
    state.previewActiveCandidateIndex,
    Math.max(0, previewCandidateItems().length - 1),
  );
  savePreviewSettings();
  renderPreview();
}

function loadPreviewSettings() {
  try {
    const raw = localStorage.getItem(PREVIEW_SETTINGS_STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (Number.isFinite(Number(saved.scale))) state.previewScale = clampNumber(Number(saved.scale), 75, 500);
    if (typeof saved.code === 'string') state.previewCode = saved.code;
    if (typeof saved.candidates === 'string') state.previewCandidatesText = saved.candidates;
  } catch (error) {
    console.warn('Failed to load preview settings', error);
  }
}

function savePreviewSettings() {
  try {
    localStorage.setItem(PREVIEW_SETTINGS_STORAGE_KEY, JSON.stringify({
      scale: state.previewScale,
      code: state.previewCode,
      candidates: state.previewCandidatesText,
    }));
  } catch (error) {
    console.warn('Failed to save preview settings', error);
  }
}

function resetPreviewSettings() {
  state.previewScale = 100;
  state.previewCode = DEFAULT_PREVIEW_CODE;
  state.previewCandidatesText = DEFAULT_PREVIEW_CANDIDATES_TEXT;
  state.previewActiveCandidateIndex = 0;
  savePreviewSettings();
  renderPreview();
}

function updateLayoutMode() {
  if (!state.draftSkin) return;
  const mode = dom.layoutMode.value;
  if (state.selectedPlatform === 'squirrel') {
    state.draftSkin.layout.candidateListLayout = mode;
  } else {
    state.draftSkin.layout.horizontal = mode === 'linear';
  }
  renderPreview();
}

function updateNumberFormat(source = 'all') {
  if (!state.draftSkin) return;
  state.draftSkin.layout ||= {};
  let numberStyle = dom.numberStyle.value;
  let separatorStyle = dom.numberSeparator.value;
  if (numberStyle === CUSTOM_NUMBER_STYLE) {
    if (source === 'style' || !normalizeLabelList(state.draftSkin.layout.alternativeSelectLabels).length) {
      const current = normalizeLabelList(state.draftSkin.layout.alternativeSelectLabels);
      const value = window.prompt('输入候选编号，用空格、逗号或顿号分隔。', current.join(' '));
      if (value === null || value === undefined || value === '') {
        syncSelect(dom.numberStyle, numberStyleFromLayout(state.draftSkin.layout), numberStyleOptionLabel(normalizedLayout(state.draftSkin)));
        return;
      }
      const labels = parseCustomList(value);
      if (!labels.length) {
        syncSelect(dom.numberStyle, numberStyleFromLayout(state.draftSkin.layout), numberStyleOptionLabel(normalizedLayout(state.draftSkin)));
        return;
      }
      state.draftSkin.layout.alternativeSelectLabels = labels;
    }
  } else if (NUMBER_LABEL_PRESETS[numberStyle]) {
    state.draftSkin.layout.alternativeSelectLabels = [...NUMBER_LABEL_PRESETS[numberStyle].values];
  } else {
    delete state.draftSkin.layout.alternativeSelectLabels;
  }

  if (separatorStyle === CUSTOM_SEPARATOR && (source === 'separator' || state.draftSkin.layout.customNumberSeparator === undefined)) {
    const current = separatorFromLayout(state.draftSkin.layout);
    const value = window.prompt('输入编号和候选之间的间隔，可以直接输入空格或符号。', current);
    if (value === null || value === undefined) {
      syncSelect(dom.numberSeparator, candidateSeparatorFromLayout(state.draftSkin.layout), numberSeparatorOptionLabel(normalizedLayout(state.draftSkin)));
      return;
    }
    state.draftSkin.layout.customNumberSeparator = value;
  } else {
    delete state.draftSkin.layout.customNumberSeparator;
  }

  const labelFormat = NUMBER_STYLE_LABELS[numberStyle] || NUMBER_STYLE_LABELS.dot;
  const separator = separatorStyle === CUSTOM_SEPARATOR
    ? state.draftSkin.layout.customNumberSeparator || ''
    : NUMBER_SEPARATOR_LABELS[separatorStyle] ?? NUMBER_SEPARATOR_LABELS.normal;
  state.draftSkin.layout.numberSeparator = separatorStyle;
  if (state.selectedPlatform === 'squirrel') {
    state.draftSkin.layout.candidateFormat = `${labelFormat.replace(/%s/g, '%c')}${separator}%@`;
    delete state.draftSkin.layout.labelFormat;
  } else {
    state.draftSkin.layout.labelFormat = `${labelFormat}${separator}`;
  }
  if (numberStyle === CUSTOM_NUMBER_STYLE) {
    syncSelect(dom.numberStyle, CUSTOM_NUMBER_STYLE, numberStyleOptionLabel(normalizedLayout(state.draftSkin)));
  }
  if (separatorStyle === CUSTOM_SEPARATOR) {
    syncSelect(dom.numberSeparator, CUSTOM_SEPARATOR, numberSeparatorOptionLabel(normalizedLayout(state.draftSkin)));
  }
  renderPreview();
}

function updateCandidateMarker() {
  if (!state.draftSkin) return;
  if (state.selectedPlatform !== 'weasel') {
    syncCandidateMarkerControl();
    return;
  }
  state.draftSkin.layout ||= {};
  if (dom.candidateMarker.value === CUSTOM_MARKER) {
    const current = state.draftSkin.layout.markText || '';
    const value = window.prompt('输入候选标记。', current);
    if (value === null || value === undefined) {
      syncSelect(dom.candidateMarker, current);
      return;
    }
    state.draftSkin.layout.markText = value;
    syncSelect(dom.candidateMarker, value || '');
  } else {
    state.draftSkin.layout.markText = dom.candidateMarker.value;
  }
  renderPreview();
}

function updatePreeditVisibility() {
  if (!state.draftSkin) return;
  state.draftSkin.layout ||= {};
  const value = dom.preeditVisibility.value;
  if (value === 'auto') {
    delete state.draftSkin.layout.previewPreedit;
    restoreOriginalLayoutField('inlinePreedit');
  } else {
    state.draftSkin.layout.previewPreedit = value;
    state.draftSkin.layout.inlinePreedit = value === 'hide';
  }
  renderPreview();
}

function updateDraftLayout(field, value) {
  if (!state.draftSkin) return;
  state.draftSkin.layout ||= {};
  if (field === 'shadowSize') {
    state.draftSkin.layout.shadowRadius = value;
    delete state.draftSkin.layout.shadowSize;
  } else if (field === 'hilitePadding') {
    state.draftSkin.layout.hilitePadding = value;
    state.draftSkin.layout.hilitePaddingX = value;
    state.draftSkin.layout.hilitePaddingY = value;
  } else if (field === 'candidateSpacing' && state.selectedPlatform === 'squirrel') {
    state.draftSkin.layout.candidateSpacing = value;
    state.draftSkin.layout.spacing = value;
  } else {
    state.draftSkin.layout[field] = value;
  }
  renderPreview();
}

function rememberOriginalLayout() {
  if (!state.draftSkin) return;
  state.draftSkin.originalLayout = cloneJson(state.draftSkin.layout || {});
}

function rememberGlobalLayoutSources() {
  if (!state.draftSkin) return;
  state.draftSkin.globalSources = cloneJson(state.globalSources || {});
  state.draftSkin.originalGlobalLayout = cloneJson(state.globalLayout || {});
}

function restoreOriginalLayoutField(field) {
  const original = state.draftSkin?.originalLayout || {};
  if (Object.prototype.hasOwnProperty.call(original, field)) {
    state.draftSkin.layout[field] = original[field];
  } else {
    delete state.draftSkin.layout[field];
  }
}

function updateDraftColor(role, color) {
  if (!state.draftSkin) return;
  state.draftSkin.colors ||= {};
  const hasExplicitColor = Boolean(state.draftSkin.colors[role]);
  const previousAlpha = state.draftSkin.colors[role]?.a;
  const defaultAlpha = DEFAULT_COLORS[role]?.a;
  state.draftSkin.colors[role] = {
    ...color,
    a: hasExplicitColor ? previousAlpha : defaultAlpha === 0 ? 255 : (defaultAlpha ?? color.a ?? 255),
  };
  renderPreview();
}

function updateDraftAlpha(role, alpha) {
  if (!state.draftSkin) return;
  state.draftSkin.colors ||= {};
  const color = state.draftSkin.colors[role] || DEFAULT_COLORS[role] || DEFAULT_COLORS.back;
  state.draftSkin.colors[role] = typeof applyAlpha === 'function'
    ? applyAlpha(color, alpha)
    : { ...color, a: clampNumber(alpha, 0, 255) };
  renderPreview();
}

function resolveSkinIdConflict(platform, id, actionLabel) {
  const result = window.prompt(
    `${PLATFORM_LABELS[platform]}中已存在 ID「${id}」。输入 1 覆盖，输入 2 另存为新 ID，留空取消。`,
    '2',
  );
  if (result === '1') return 'overwrite';
  if (result === '2') return 'new';
  logMessage(`${actionLabel}已取消，未覆盖同名皮肤。`);
  return 'cancel';
}

function manifestSummary(manifest) {
  if (!manifest) return '缺少备份清单';
  const operation = {
    save: '保存',
    copy: '复制',
    delete: '删除',
    'rollback-before': '回退前备份',
  }[manifest.operation] || manifest.operation || '操作';
  const source = manifest.sourcePlatform ? PLATFORM_LABELS[manifest.sourcePlatform] || manifest.sourcePlatform : '';
  const target = manifest.targetPlatform ? `到${PLATFORM_LABELS[manifest.targetPlatform] || manifest.targetPlatform}` : '';
  const skin = manifest.skinIdAfter || manifest.skinIdBefore || '';
  return [operation, `${source}${target}`, skin].filter(Boolean).join(' ');
}

async function setActiveSkin() {
  if (!state.draftSkin) return;
  logMessage('正在保存并切换当前皮肤...');
  await saveCurrentSkin({ activate: true });
}

async function deployAfterWrite(successMessage) {
  if (state.storageMode === 'local' && state.deploySupported) {
    try {
      await localApi('deploy', { method: 'POST' });
      logMessage(`${successMessage}并重新部署 Rime。`);
    } catch (error) {
      logMessage(`${successMessage}，但自动部署失败：${error.message}`);
    }
  } else {
    logMessage(`${successMessage}；请手动重新部署 Rime。`);
  }
}

async function saveCurrentSkin(options = {}) {
  if (!state.draftSkin || !hasWritableStorage()) return;
  try {
    const platform = state.selectedPlatform;
    const filename = PLATFORM_FILES[platform];
    const current = await readAndValidateFrontendFile(platform);
    const existingText = current.exists ? current.text : minimalConfigText(platform);
    const config = current.exists ? current.config : state.configs[platform];
    let overwriteConfirmed = false;
    const isRename = state.originalSkinId && state.originalSkinId !== state.draftSkin.id;
    if (isRename) {
      const saveAsNew = window.confirm('已保存皮肤的 ID 不能直接改名。是否另存为一个新皮肤？原皮肤会保留。');
      if (!saveAsNew) return;
      if (config.skins.some((skin) => skin.id === state.draftSkin.id)) {
        const resolution = resolveSkinIdConflict(platform, state.draftSkin.id, '另存');
        if (resolution === 'cancel') return;
        if (resolution === 'new') state.draftSkin.id = generateSkinId(state.draftSkin.id, config.skins.map((skin) => skin.id));
        if (resolution === 'overwrite') overwriteConfirmed = true;
      }
      state.originalSkinId = '';
    }
    const existingSkin = config.skins.find((skin) => skin.id === state.draftSkin.id);
    if (!overwriteConfirmed && existingSkin && existingSkin.id !== state.originalSkinId) {
      const resolution = resolveSkinIdConflict(platform, state.draftSkin.id, '保存');
      if (resolution === 'cancel') return;
      if (resolution === 'new') state.draftSkin.id = generateSkinId(state.draftSkin.id, config.skins.map((skin) => skin.id));
    }
    const userRequestedActive = options.activate === true;
    const makeActive = Boolean(
      userRequestedActive ||
        (!state.originalSkinId && !config.activeSkinId) ||
        config.activeSkinId === state.draftSkin.id ||
        (state.originalSkinId && config.activeSkinId === state.originalSkinId),
    );
    const makeDark = platform === 'squirrel' && Boolean(
      userRequestedActive ||
        config.darkSkinId === state.draftSkin.id ||
        (state.originalSkinId && config.darkSkinId === state.originalSkinId),
    );
    const operation = `保存${PLATFORM_LABELS[platform]}-${state.draftSkin.id}`;
    const fileExists = current.exists;
    if (!fileExists) {
      const createFile = window.confirm(`未找到 ${filename}。是否创建这个前端配置文件并保存皮肤？`);
      if (!createFile) return;
    }
    const frontendLabels = labelsFromCustomPatchText(filename, existingText);
    const shouldSyncFrontendLabels = frontendLabels.length > 0;
    const currentLabels = normalizeLabelList(state.draftSkin.layout?.alternativeSelectLabels);
    const globalWrite = shouldSyncFrontendLabels && currentLabels.length
      ? null
      : await prepareGlobalLayoutWrite(state.draftSkin);
    const activeOnly = shouldSaveActiveOnly({
      platform,
      config,
      current,
      globalWrite,
      userRequestedActive,
    });
    if (activeOnly) {
      await backupFiles(`切换${PLATFORM_LABELS[platform]}-${state.draftSkin.id}`, [filename], {
        operation: 'save', sourcePlatform: platform, targetPlatform: '',
        skinIdBefore: config.activeSkinId, skinIdAfter: state.draftSkin.id, createdFiles: [],
      }, { [filename]: current });
      const output = updateActiveSkinConfig(existingText, platform, state.draftSkin.id, {
        makeDark: platform === 'squirrel',
      });
      await writeFile(filename, output);
      const written = await readOptionalFileEntry(filename);
      if (!written.exists) throw new Error(`保存后无法重新读取 ${filename}`);
      state.fileExists[platform] = true;
      state.rawFiles[platform] = written.text;
      state.configs[platform] = mergeGlobalLayoutIntoConfig(parseConfig(platform, written.text), state.globalLayout);
      state.selectedSkinId = state.draftSkin.id;
      selectSkin(state.selectedSkinId);
      await deployAfterWrite('已切换当前皮肤');
      return true;
    }
    const filesToBackup = [filename, ...(globalWrite ? [globalWrite.filename] : [])];
    const backupSnapshots = { [filename]: current };
    if (globalWrite) backupSnapshots[globalWrite.filename] = globalWrite.current;

    await backupFiles(operation, filesToBackup, {
      operation: 'save',
      sourcePlatform: platform,
      targetPlatform: '',
      skinIdBefore: state.originalSkinId || config.activeSkinId,
      skinIdAfter: state.draftSkin.id,
      createdFiles: [
        ...(fileExists ? [] : [filename]),
        ...(globalWrite && !globalWrite.current.exists ? [globalWrite.filename] : []),
      ],
    }, backupSnapshots);

    const frontendWriteOptions = shouldSyncFrontendLabels
      ? { alternativeSelectLabels: currentLabels }
      : {};
    const output = platform === 'squirrel'
      ? updateSquirrelConfig(existingText, state.draftSkin, { makeActive, makeDark, ...frontendWriteOptions })
      : updateWeaselConfig(existingText, state.draftSkin, { makeActive, ...frontendWriteOptions });
    await writeFile(filename, output);
    if (globalWrite) await writeFile(globalWrite.filename, globalWrite.output);
    const written = await readOptionalFileEntry(filename);
    if (!written.exists) throw new Error(`保存后无法重新读取 ${filename}`);
    state.fileExists[platform] = true;
    state.rawFiles[platform] = written.text;
    if (globalWrite) {
      const savedGlobal = await readOptionalFileEntry(globalWrite.filename);
      if (globalWrite.filename === 'default.custom.yaml') {
        state.rawFiles.default = savedGlobal.text;
        state.fileExists.default = true;
      } else {
        upsertCustomFileState(globalWrite.filename, savedGlobal.text);
      }
      applyCurrentSchemaLayout();
    }
    state.configs[platform] = mergeGlobalLayoutIntoConfig(parseConfig(platform, written.text), state.globalLayout);
    state.selectedSkinId = state.draftSkin.id;
    await refreshBackups();
    selectSkin(state.selectedSkinId);
    if (userRequestedActive) {
      await deployAfterWrite('已保存并设为当前皮肤');
    } else {
      await deployAfterWrite('已保存并备份');
    }
    return true;
  } catch (error) {
    logMessage(`保存失败：${error.message}`);
    return false;
  }
}

function shouldSaveActiveOnly({ platform, config, current, globalWrite, userRequestedActive }) {
  if (!userRequestedActive || !current.exists || globalWrite || !state.originalSkinId) return false;
  if (!state.draftSkin || state.draftSkin.id !== state.originalSkinId) return false;
  const persisted = config.skins.find((skin) => skin.id === state.originalSkinId);
  if (!persisted) return false;
  const expectedActive = state.draftSkin.id;
  if (config.activeSkinId === expectedActive && (platform !== 'squirrel' || config.darkSkinId === expectedActive)) {
    return false;
  }
  const cleanDraft = comparableSkin(state.draftSkin);
  const cleanPersisted = comparableSkin(persisted);
  return JSON.stringify(cleanDraft) === JSON.stringify(cleanPersisted);
}

function comparableSkin(skin) {
  const copy = cloneSkin(skin);
  delete copy.originalLayout;
  delete copy.originalGlobalLayout;
  delete copy.globalSources;
  return copy;
}

async function copyCurrentSkin() {
  if (!state.draftSkin || !hasWritableStorage()) return;
  const target = state.selectedPlatform === 'squirrel' ? 'weasel' : 'squirrel';
  try {
    const targetFile = PLATFORM_FILES[target];
    const current = await readAndValidateFrontendFile(target);
    const targetText = current.exists ? current.text : minimalConfigText(target);
    const targetConfig = current.exists ? current.config : state.configs[target];
    const copied = copySkinToPlatform(state.draftSkin, target);
    const confirmed = window.confirm(`将当前皮肤复制到${PLATFORM_LABELS[target]}。颜色和布局会自动映射，少数平台专属设置可能需要复制后检查。继续？`);
    if (!confirmed) return;
    const targetExists = targetConfig.skins.some((skin) => skin.id === copied.id);
    if (targetExists) {
      const resolution = resolveSkinIdConflict(target, copied.id, '复制');
      if (resolution === 'cancel') return;
      if (resolution === 'new') copied.id = generateSkinId(copied.id, targetConfig.skins.map((skin) => skin.id));
    }
    const operation = `复制${PLATFORM_LABELS[state.selectedPlatform]}到${PLATFORM_LABELS[target]}-${copied.id}`;
    const fileExists = current.exists;
    if (!fileExists) {
      const createFile = window.confirm(`未找到 ${targetFile}。是否创建这个前端配置文件并写入复制的皮肤？`);
      if (!createFile) return;
    }
    await backupFiles(operation, [targetFile], {
      operation: 'copy',
      sourcePlatform: state.selectedPlatform,
      targetPlatform: target,
      skinIdBefore: targetConfig.activeSkinId,
      skinIdAfter: copied.id,
      createdFiles: fileExists ? [] : [targetFile],
    }, { [targetFile]: current });
    const output = target === 'squirrel'
      ? updateSquirrelConfig(targetText, copied, { makeActive: false })
      : updateWeaselConfig(targetText, copied, { makeActive: false });
    await writeFile(targetFile, output);
    const written = await readOptionalFileEntry(targetFile);
    if (!written.exists) throw new Error(`复制后无法重新读取 ${targetFile}`);
    state.fileExists[target] = true;
    state.rawFiles[target] = written.text;
    state.configs[target] = mergeGlobalLayoutIntoConfig(parseConfig(target, written.text), state.globalLayout);
    await refreshBackups();
    logMessage(`已复制到${PLATFORM_LABELS[target]}，请重新部署 Rime。`);
    renderAll();
  } catch (error) {
    logMessage(`复制失败：${error.message}`);
  }
}

async function prepareGlobalLayoutWrite(skin) {
  const layout = skin.layout || {};
  const original = skin.originalGlobalLayout || {};
  const currentLabels = normalizeLabelList(layout.alternativeSelectLabels);
  const originalLabels = normalizeLabelList(original.alternativeSelectLabels);
  if (labelListsEqual(currentLabels, originalLabels)) return null;

  const filename = skin.globalSources?.alternativeSelectLabels ||
    state.globalSources.alternativeSelectLabels ||
    (state.currentSchemaId ? `${state.currentSchemaId}.custom.yaml` : 'default.custom.yaml');
  const current = await readOptionalFileEntry(filename);
  const output = updateGlobalCustomConfig(current.exists ? current.text : 'patch:\n', {
    alternativeSelectLabels: currentLabels,
  });
  return { filename, current, output };
}

function labelsFromCustomPatchText(filename, text) {
  try {
    const parsed = parseGlobalCustomFiles([{ name: filename, text }]);
    return normalizeLabelList(parsed.layout.alternativeSelectLabels);
  } catch {
    return [];
  }
}

function upsertCustomFileState(filename, text) {
  if (Object.values(PLATFORM_FILES).includes(filename) || filename === 'default.custom.yaml') return;
  const existing = state.customFiles.find((file) => file.name === filename);
  if (existing) {
    existing.text = text;
  } else {
    state.customFiles.push({ name: filename, text });
    state.customFiles.sort((a, b) => a.name.localeCompare(b.name));
  }
}

function normalizeLabelList(value) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item ?? '').trim()).filter(Boolean);
}

function labelListsEqual(left, right) {
  if (left.length !== right.length) return false;
  return left.every((value, index) => value === right[index]);
}

function matchingNumberLabelPreset(labels) {
  const normalized = normalizeLabelList(labels);
  if (!normalized.length) return '';
  for (const [key, preset] of Object.entries(NUMBER_LABEL_PRESETS)) {
    const presetValues = preset.values;
    if (
      normalized.length <= presetValues.length &&
      normalized.every((value, index) => value === presetValues[index])
    ) {
      return key;
    }
  }
  return '';
}

async function backupFiles(operationSummary, filenames, manifestInput, snapshots = {}) {
  const existingNames = state.storageMode === 'local'
    ? new Set(state.backups.map((backup) => backup.name))
    : await listDirectoryNames(await state.dirHandle.getDirectoryHandle(BACKUP_ROOT, { create: true }));
  const folderName = formatBackupFolderName(new Date(), operationSummary, -new Date().getTimezoneOffset(), existingNames);
  const backupDir = state.storageMode === 'local'
    ? { name: folderName }
    : await (await state.dirHandle.getDirectoryHandle(BACKUP_ROOT, { create: true })).getDirectoryHandle(folderName, { create: true });
  const backedUp = [];

  for (const filename of filenames) {
    const entry = snapshots[filename] || await readOptionalFileEntry(filename);
    if (!entry.exists) continue;
    await writeFileToDirectory(backupDir, filename, entry.text);
    backedUp.push(filename);
  }

  const manifest = createBackupManifest({
    createdAt: new Date().toISOString(),
    ...manifestInput,
    files: backedUp,
    createdFiles: manifestInput.createdFiles || [],
    browser: navigator.userAgent,
    rimeFolder: state.folderName,
  });
  await writeFileToDirectory(backupDir, 'manifest.json', JSON.stringify(manifest, null, 2));
  return folderName;
}

async function listDirectoryNames(directoryHandle) {
  const names = new Set();
  for await (const [name, handle] of directoryHandle.entries()) {
    if (handle.kind === 'directory') names.add(name);
  }
  return names;
}

async function refreshBackups() {
  state.backups = [];
  if (state.storageMode === 'local') {
    try {
      const result = await localApi('backups');
      state.backups = (result.backups || []).map((backup) => ({
        ...backup,
        handle: { name: backup.name },
        availableFiles: new Set(backup.availableFiles || []),
      }));
    } catch (error) {
      logMessage(`读取备份失败：${error.message}`);
    }
    renderBackupList();
    return;
  }
  if (!state.dirHandle) {
    renderBackupList();
    return;
  }
  try {
    const root = await state.dirHandle.getDirectoryHandle(BACKUP_ROOT);
    for await (const [name, handle] of root.entries()) {
      if (handle.kind !== 'directory') continue;
      const manifestText = await readFileFromDirectory(handle, 'manifest.json').catch(() => '');
      const manifest = manifestText ? JSON.parse(manifestText) : null;
      const availableFiles = new Set();
      for await (const [fileName, fileHandle] of handle.entries()) {
        if (fileHandle.kind === 'file') availableFiles.add(fileName);
      }
      state.backups.push({ name, handle, manifest, availableFiles });
    }
    state.backups.sort((a, b) => b.name.localeCompare(a.name));
  } catch (error) {
    if (error?.name !== 'NotFoundError') logMessage(`读取备份失败：${error.message}`);
  }
  renderBackupList();
}

async function rollbackBackup(backup) {
  if (!backup?.manifest) {
    logMessage('这个备份没有 manifest.json，暂不自动回退。');
    return;
  }
  const currentFiles = currentFrontendFiles();
  let plan;
  try {
    plan = createRollbackPlan(backup, currentFiles);
  } catch (error) {
    logMessage(`无法回退：${error.message}`);
    return;
  }
  const restoreText = plan.filesToRestore.length ? `将恢复：${plan.filesToRestore.join(', ')}` : '';
  const deleteText = plan.filesToDelete.length ? `将删除：${plan.filesToDelete.join(', ')}` : '';
  const confirmed = window.confirm(`确认回退这个备份？\n${backup.name}\n${manifestSummary(backup.manifest)}\n${[restoreText, deleteText].filter(Boolean).join('\n')}`);
  if (!confirmed) return;

  try {
    const filesToBackup = [...new Set([...plan.filesToRestore, ...plan.filesToDelete])];
    await backupFiles(`回退前备份-${backup.name}`, filesToBackup, {
      operation: 'rollback-before',
      sourcePlatform: backup.manifest.sourcePlatform,
      targetPlatform: backup.manifest.targetPlatform,
      skinIdBefore: backup.manifest.skinIdAfter,
      skinIdAfter: backup.manifest.skinIdBefore,
    });
    for (const filename of plan.filesToRestore) {
      const text = await readFileFromDirectory(backup.handle, filename);
      await writeFile(filename, text);
    }
    for (const filename of plan.filesToDelete) {
      if (state.storageMode === 'local') {
        await deleteFile(filename);
      } else {
        await state.dirHandle.removeEntry(filename).catch((error) => {
          if (error?.name !== 'NotFoundError') throw error;
        });
      }
    }
    await loadConfigs();
    chooseInitialPlatform();
    await refreshBackups();
    await deployAfterWrite('已回退到所选备份');
  } catch (error) {
    logMessage(`回退失败：${error.message}`);
  }
}

async function deleteBackupEntry(backup) {
  if (!backup?.name || !hasWritableStorage()) return;
  const confirmed = window.confirm(`确认删除这个备份？\n${backup.name}`);
  if (!confirmed) return;
  try {
    if (state.storageMode === 'local') {
      await localApi(`backup?name=${encodeURIComponent(backup.name)}`, { method: 'DELETE' });
    } else {
      const root = await state.dirHandle.getDirectoryHandle(BACKUP_ROOT);
      await root.removeEntry(backup.name, { recursive: true });
    }
    await refreshBackups();
    logMessage('已删除备份。');
  } catch (error) {
    logMessage(`删除备份失败：${error.message}`);
  }
}

async function writeFile(filename, text) {
  if (state.storageMode === 'local') {
    await localApi('file', {
      method: 'PUT',
      body: { path: filename, text },
    });
    return;
  }
  const handle = await state.dirHandle.getFileHandle(filename, { create: true });
  const writable = await handle.createWritable();
  try {
    await writable.write(text);
    await writable.close();
  } catch (error) {
    await writable.abort?.();
    throw error;
  }
}

async function deleteFile(filename) {
  if (state.storageMode === 'local') {
    await localApi(`file?path=${encodeURIComponent(filename)}`, { method: 'DELETE' });
    return;
  }
  await state.dirHandle.removeEntry(filename);
}

async function writeFileToDirectory(directoryHandle, filename, text) {
  if (state.storageMode === 'local') {
    await localApi('mkdir', {
      method: 'POST',
      body: { path: `${BACKUP_ROOT}/${directoryHandle.name}` },
    });
    await localApi('file', {
      method: 'PUT',
      body: { path: `${BACKUP_ROOT}/${directoryHandle.name}/${filename}`, text },
    });
    return;
  }
  const handle = await directoryHandle.getFileHandle(filename, { create: true });
  const writable = await handle.createWritable();
  try {
    await writable.write(text);
    await writable.close();
  } catch (error) {
    await writable.abort?.();
    throw error;
  }
}

async function readFileFromDirectory(directoryHandle, filename) {
  if (state.storageMode === 'local') {
    const result = await localApi(`file?path=${encodeURIComponent(`${BACKUP_ROOT}/${directoryHandle.name}/${filename}`)}`);
    return result.text || '';
  }
  const handle = await directoryHandle.getFileHandle(filename);
  const file = await handle.getFile();
  return await file.text();
}

function minimalConfigText(platform) {
  if (platform === 'squirrel') return 'patch:\n  preset_color_schemes:\n  style:\n';
  return 'patch:\n';
}

function populateFontOptions() {
  for (const select of [dom.fontFace, dom.labelFontFace, dom.commentFontFace]) {
    populateOneFontSelect(select);
  }
}

function fontFamiliesFromValue(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim().replace(/^["']|["']$/g, ''))
    .filter(Boolean);
}

function isFontAvailable(value) {
  const families = fontFamiliesFromValue(value);
  if (!families.length || value === '__custom_font__') return true;
  if (typeof document.fonts?.check !== 'function') return true;
  try {
    return families.some((family) => document.fonts.check(`16px "${family}"`));
  } catch {
    return true;
  }
}

function markMissingFontOptions(select) {
  for (const option of select.options) {
    if (option.value === '__custom_font__') continue;
    if (!option.dataset.baseLabel) option.dataset.baseLabel = option.textContent;
    option.textContent = isFontAvailable(option.value)
      ? option.dataset.baseLabel
      : `${option.dataset.baseLabel}（未安装）`;
  }
}

function updateFontHint() {
  if (!dom.fontHint) return;
  const missing = [];
  for (const select of [dom.fontFace, dom.labelFontFace, dom.commentFontFace]) {
    markMissingFontOptions(select);
    const value = select.value;
    if (value && value !== '__custom_font__' && !isFontAvailable(value) && !missing.includes(value)) {
      missing.push(value);
    }
  }
  dom.fontHint.hidden = !missing.length;
  if (missing.length) {
    dom.fontHint.textContent = `「${missing.join('」「')}」这台电脑没有安装：预览会回退到系统默认字体，切换后看不到变化是正常现象，不是皮肤问题。安装字体后预览和输入法才会显示该字体。`;
  }
}

function populateOneFontSelect(select) {
  select.replaceChildren();
  for (const font of COMMON_FONTS) {
    const option = document.createElement('option');
    option.value = font;
    option.textContent = font;
    select.append(option);
  }
  const custom = document.createElement('option');
  custom.value = '__custom_font__';
  custom.textContent = '自定义字体...';
  select.append(custom);
}

function syncSelect(select, value, label = value) {
  if (value && ![...select.options].some((option) => option.value === value)) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label || value;
    select.prepend(option);
  }
  select.value = value || select.options[0]?.value || '';
}

function setRange(rangeInput, numberInput, output, value) {
  rangeInput.value = String(value);
  numberInput.value = String(value);
  output.value = String(value);
}

function clampNumber(value, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  return Math.max(min, Math.min(max, Math.round(number)));
}

function updateFontFace() {
  updateFontSelect('fontFace', dom.fontFace);
}

function updateFontSelect(field, select) {
  if (!state.draftSkin) return;
  if (select.value !== '__custom_font__') {
    updateDraftLayout(field, select.value);
    updateFontHint();
    return;
  }
  const current = state.draftSkin.layout?.[field] || state.draftSkin.layout?.fontFace || '';
  const value = window.prompt('输入字体名称，多个字体可用逗号分隔。', current);
  if (!value) {
    syncSelect(select, current);
    updateFontHint();
    return;
  }
  const trimmed = value.trim();
  syncSelect(select, trimmed);
  updateDraftLayout(field, trimmed);
  updateFontHint();
}

function parseCustomList(value) {
  return String(value || '')
    .split(/[\s,，、]+/u)
    .map((item) => item.trim())
    .filter(Boolean);
}

function currentFrontendFiles() {
  const files = new Set();
  for (const [platform, filename] of Object.entries(PLATFORM_FILES)) {
    if (state.fileExists[platform]) files.add(filename);
  }
  if (state.fileExists.default) files.add('default.custom.yaml');
  for (const filename of state.customFiles.map((file) => file.name)) files.add(filename);
  return files;
}

function hasWritableStorage() {
  return state.storageMode === 'local' || Boolean(state.dirHandle);
}

function detectLocalLauncherSession(locationValue) {
  const locationObject = locationValue || window.location;
  if (!LOCALHOST_NAMES.has(locationObject.hostname)) return null;
  const token = new URLSearchParams(locationObject.search || '').get('token') || '';
  if (!token) return null;
  return { token };
}

function startLocalLauncherHeartbeat() {
  if (state.heartbeatTimer) return;
  const ping = () => {
    Promise.resolve()
      .then(() => fetch('/api/ping', {
        method: 'POST',
        headers: { 'x-rime-editor-token': state.localApiToken },
      }))
      .catch(() => {});
  };
  ping();
  if (typeof setInterval === 'function') {
    state.heartbeatTimer = setInterval(ping, 10000);
  }
  if (typeof window.addEventListener === 'function') {
    window.addEventListener('pagehide', requestLocalLauncherClose);
    window.addEventListener('beforeunload', requestLocalLauncherClose);
  }
}

function requestLocalLauncherClose() {
  if (state.storageMode !== 'local' || state.closeRequested) return;
  state.closeRequested = true;
  if (state.heartbeatTimer) {
    clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = null;
  }
  fetch(`/api/close?token=${encodeURIComponent(state.localApiToken)}`, {
    method: 'POST',
    keepalive: true,
  }).catch(() => {});
}

async function localApi(path, options = {}) {
  const response = await fetch(`/api/${path}`, {
    method: options.method || 'GET',
    headers: {
      'content-type': 'application/json',
      'x-rime-editor-token': state.localApiToken,
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `本地启动器请求失败：${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function downloadTextFile(filename, text) {
  const blob = new Blob([text], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body?.append?.(link);
  link.click();
  link.remove?.();
  URL.revokeObjectURL(url);
}

function previewNodes(layout, colors) {
  return previewCandidateItems().map((item, index) => createCandidateNode(item, layout, colors, index));
}

function previewCandidateItems() {
  if (typeof parsePreviewCandidateItems === 'function') {
    return parsePreviewCandidateItems(state.previewCandidatesText, DEFAULT_PREVIEW_CANDIDATES.length ? DEFAULT_PREVIEW_CANDIDATES : PREVIEW_CANDIDATES);
  }
  return PREVIEW_CANDIDATES;
}

function colorsForPreview(skinColors = {}) {
  return {
    ...DEFAULT_COLORS,
    __explicitHilitedMark: Object.prototype.hasOwnProperty.call(skinColors, 'hilitedMark'),
    hilitedCandidateBack: skinColors.hilitedCandidateBack || skinColors.hilitedBack || DEFAULT_COLORS.hilitedCandidateBack,
    hilitedCandidateText: skinColors.hilitedCandidateText || skinColors.hilitedText || DEFAULT_COLORS.hilitedCandidateText,
    hilitedCandidateLabel: skinColors.hilitedCandidateLabel || skinColors.hilitedLabel || DEFAULT_COLORS.hilitedCandidateLabel,
    hilitedCommentText: skinColors.hilitedCommentText || skinColors.hilitedCandidateText || skinColors.hilitedText || DEFAULT_COLORS.hilitedCommentText,
    hilitedMark: skinColors.hilitedMark || skinColors.hilitedCandidateLabel || skinColors.hilitedLabel || DEFAULT_COLORS.hilitedMark,
    ...skinColors,
  };
}

function createCandidateNode(item, layout, colors, index) {
  const active = index === state.previewActiveCandidateIndex;
  const previewComment = typeof resolvePreviewComment === 'function'
    ? resolvePreviewComment(item.comment, { quickCodeIndicator: currentSchemaSettingValue('quickCodeIndicator') })
    : item.comment;
  const candidate = document.createElement('span');
  const classes = ['candidate'];
  if (active) classes.push('active');
  if (!previewComment) classes.push('no-comment');
  if (String(item.text || '').length > 6) classes.push('long');
  candidate.className = classes.join(' ');
  const textMarker = String(layout.markText || '');
  const hilitedMarkColor = colorRole(colors, 'hilitedMark', 'hilitedCandidateLabel', 'hilitedLabel', 'label');
  const marker = typeof markerBehavior === 'function'
    ? markerBehavior(state.selectedPlatform, layout, { hilitedMark: hilitedMarkColor }, active)
    : {
        type: !textMarker && colorIsVisible(hilitedMarkColor) ? 'win11' : textMarker ? 'text' : 'none',
        hasSlot: Boolean((!textMarker && colorIsVisible(hilitedMarkColor)) || textMarker),
        visible: active,
        text: textMarker,
      };
  const showMarkerSlot = marker.hasSlot && (active || layout.mode === 'stacked');
  candidate.style.display = layout.mode === 'stacked' ? 'grid' : 'inline-grid';
  candidate.style.gridTemplateColumns = candidateGridColumns(showMarkerSlot);
  candidate.style.width = layout.mode === 'stacked' ? '100%' : '';
  candidate.style.boxSizing = 'border-box';
  candidate.style.alignItems = 'baseline';
  candidate.style.fontFamily = layout.fontFace;
  candidate.style.fontSize = `${layout.fontPoint}px`;
  candidate.style.backgroundColor = rgbaToCss(active
    ? colorRole(colors, 'hilitedCandidateBack', 'hilitedBack')
    : colorRole(colors, 'candidateBack'));
  candidate.style.color = rgbaToCss(active
    ? colorRole(colors, 'hilitedCandidateText', 'hilitedText', 'candidateText')
    : colorRole(colors, 'candidateText'));
  candidate.style.borderRadius = `${active ? layout.hilitedCornerRadius : Math.min(layout.hilitedCornerRadius, layout.cornerRadius)}px`;
  const blockPadding = layout.hilitePaddingY;
  const inlinePadding = layout.hilitePaddingX;
  candidate.style.padding = `${cssLength(blockPadding)} ${cssLength(inlinePadding)}`;
  candidate.style.paddingLeft = cssLength(inlinePadding);
  candidate.style.paddingRight = cssLength(inlinePadding);
  candidate.style.columnGap = `${candidateInnerGap(layout)}px`;
  candidate.style.textShadow = textShadowForColor(active
    ? colorRole(colors, 'hilitedCandidateShadow')
    : colorRole(colors, 'candidateShadow'));
  candidate.style.cursor = 'pointer';
  candidate.addEventListener('click', () => {
    state.previewActiveCandidateIndex = index;
    renderPreview();
  });

  const mark = document.createElement('span');
  mark.className = 'candidate-mark';
  if (showMarkerSlot && marker.type === 'win11') {
    mark.className = 'candidate-mark win11-mark';
    mark.textContent = '';
    mark.style.visibility = marker.visible ? 'visible' : 'hidden';
    mark.style.backgroundColor = rgbaToCss(hilitedMarkColor);
    mark.style.borderRadius = `${layout.hilitedCornerRadius}px`;
    mark.style.display = 'inline-block';
    mark.style.alignSelf = 'center';
    mark.style.minWidth = markerMinWidth(layout);
    mark.style.width = markerMinWidth(layout);
    mark.style.height = win11MarkerHeight(layout);
    mark.style.padding = '0';
  } else {
    mark.textContent = marker.text || textMarker;
    mark.style.visibility = marker.visible ? 'visible' : 'hidden';
    mark.style.color = rgbaToCss(active
      ? hilitedMarkColor
      : colorRole(colors, 'label'));
    mark.style.backgroundColor = '';
    mark.style.borderRadius = '';
    mark.style.display = 'inline';
    mark.style.alignItems = '';
    mark.style.justifyContent = '';
    mark.style.alignSelf = '';
    mark.style.minWidth = '';
    mark.style.padding = '';
  }

  const label = document.createElement('span');
  label.className = 'candidate-label';
  label.textContent = formatCandidateLabel(labelForCandidate(item, layout, index), layout);
  label.style.color = rgbaToCss(active
    ? colorRole(colors, 'hilitedCandidateLabel', 'hilitedLabel', 'hilitedCandidateText', 'hilitedText')
    : colorRole(colors, 'label'));
  label.style.fontFamily = layout.labelFontFace || layout.fontFace;
  label.style.fontSize = `${layout.labelFontPoint}px`;

  const text = document.createElement('span');
  text.className = 'candidate-text';
  text.textContent = item.text;
  text.style.color = rgbaToCss(active
    ? colorRole(colors, 'hilitedCandidateText', 'hilitedText', 'candidateText')
    : colorRole(colors, 'candidateText'));

  const comment = document.createElement('span');
  comment.className = 'candidate-comment';
  comment.textContent = previewComment;
  comment.style.color = rgbaToCss(active
    ? colorRole(colors, 'hilitedCommentText', 'hilitedCandidateText', 'hilitedText')
    : colorRole(colors, 'commentText'));
  comment.style.fontFamily = layout.commentFontFace || layout.fontFace;
  comment.style.fontSize = `${layout.commentFontPoint}px`;

  if (showMarkerSlot) candidate.append(mark);
  candidate.append(label, text, comment);
  return candidate;
}

function candidateGridColumns(showMarkerSlot) {
  return showMarkerSlot
    ? 'max-content max-content minmax(0, max-content) max-content'
    : 'max-content minmax(0, max-content) max-content';
}

function candidateInnerGap(layout) {
  return Math.max(layout.labelGap, layout.hiliteSpacing);
}

function markerMinWidth(layout) {
  return `${Math.max(4, Math.round(layout.fontPoint * 0.28))}px`;
}

function win11MarkerHeight(layout) {
  return `${Math.max(10, Math.round(layout.fontPoint * 0.78))}px`;
}

function cssLength(value) {
  const numeric = Number(value) || 0;
  return numeric === 0 ? '0' : `${numeric}px`;
}

function colorIsVisible(color) {
  return Boolean(color && (color.a ?? 255) > 0);
}

function colorRole(colors, ...roles) {
  for (const role of roles) {
    if (colors[role]) return colors[role];
  }
  return DEFAULT_COLORS[roles[0]] || DEFAULT_COLORS.text;
}

function textShadowForColor(color) {
  if (!color || (color.a ?? 0) === 0) return 'none';
  return `0 1px 1px ${rgbaToCss(color)}`;
}

function shouldShowPreedit(layout) {
  if (layout.previewPreedit === 'show') return true;
  if (layout.previewPreedit === 'hide') return false;
  return layout.inlinePreedit === false;
}

function preeditTextForLayout(layout) {
  const items = previewCandidateItems();
  if (layout.preeditType === 'preview') return items[0]?.text || '';
  if (layout.preeditType === 'preview_all') {
    return items.map((item) => item.text).join(' ');
  }
  return state.previewCode || DEFAULT_PREVIEW_CODE;
}

function labelForCandidate(item, layout, index) {
  const labels = normalizeLabelList(layout.alternativeSelectLabels);
  return labels[index] || item.label || String(index + 1);
}

function formatCandidateLabel(label, layout) {
  return labelFormatFromLayout(layout).replace(/%s/g, label).trimEnd();
}

function labelFormatFromLayout(layout) {
  if (layout.candidateFormat) {
    const beforeCandidate = layout.candidateFormat.includes('%@')
      ? layout.candidateFormat.split('%@')[0]
      : layout.candidateFormat;
    return beforeCandidate.replace(/%c/g, '%s').trimEnd();
  }
  if (layout.labelFormat) return layout.labelFormat;
  return '%s.';
}

function candidateSeparatorFromLayout(layout) {
  if (layout.numberSeparator) return layout.numberSeparator;
  if (layout.customNumberSeparator !== undefined) return CUSTOM_SEPARATOR;
  const separator = labelSeparatorFromLayout(layout);
  if (separator === null) return 'normal';
  if (separator === '  ') return 'wide';
  if (separator === ' ') return 'normal';
  if (separator === '') return 'tight';
  return CUSTOM_SEPARATOR;
}

function numberStyleFromLayout(layout) {
  const labelPreset = matchingNumberLabelPreset(layout.alternativeSelectLabels);
  if (labelPreset) return labelPreset;
  if (normalizeLabelList(layout.alternativeSelectLabels).length) return CUSTOM_NUMBER_STYLE;
  const format = labelFormatCoreFromLayout(layout);
  if (format === '%s') return 'plain';
  if (format === '%s)') return 'paren';
  if (format === '[%s]') return 'bracket';
  if (format === '%s.') return 'dot';
  return 'dot';
}

function separatorFromLayout(layout) {
  if (layout.customNumberSeparator !== undefined) return layout.customNumberSeparator;
  const separator = labelSeparatorFromLayout(layout);
  if (separator !== null && !['', ' ', '  '].includes(separator)) return separator;
  const separatorStyle = candidateSeparatorFromLayout(layout);
  if (separatorStyle === CUSTOM_SEPARATOR) return layout.customNumberSeparator || '';
  return NUMBER_SEPARATOR_LABELS[separatorStyle] ?? NUMBER_SEPARATOR_LABELS.normal;
}

function labelFormatCoreFromLayout(layout) {
  const format = labelFormatFromLayout(layout);
  for (const styleFormat of Object.values(NUMBER_STYLE_LABELS)) {
    if (format === styleFormat || format.startsWith(styleFormat)) return styleFormat;
  }
  return format.trimEnd();
}

function labelSeparatorFromLayout(layout) {
  if (layout.labelFormat) {
    return separatorAfterKnownFormat(String(layout.labelFormat), '%s');
  }
  if (!layout.candidateFormat || !layout.candidateFormat.includes('%@')) return null;
  const beforeCandidate = layout.candidateFormat.split('%@')[0];
  return separatorAfterKnownFormat(beforeCandidate.replace(/%c/g, '%s'), '%s');
}

function separatorAfterKnownFormat(format, placeholder) {
  const candidates = Object.values(NUMBER_STYLE_LABELS)
    .map((value) => value.replace(/%s/g, placeholder))
    .sort((left, right) => right.length - left.length);
  for (const candidate of candidates) {
    if (format.startsWith(candidate)) return format.slice(candidate.length);
  }
  const placeholderIndex = format.indexOf(placeholder);
  if (placeholderIndex < 0) return '';
  return format.slice(placeholderIndex + placeholder.length);
}

function gapForSeparator(layout) {
  const separatorStyle = candidateSeparatorFromLayout(layout);
  if (separatorStyle !== CUSTOM_SEPARATOR) {
    return NUMBER_SEPARATOR_GAPS[separatorStyle] ?? NUMBER_SEPARATOR_GAPS.normal;
  }
  const separator = separatorFromLayout(layout);
  return Math.max(2, Math.min(24, separator.length * 4));
}

function numberStyleOptionLabel(layout) {
  const labels = normalizeLabelList(layout.alternativeSelectLabels);
  if (!labels.length) return '';
  const preset = matchingNumberLabelPreset(labels);
  if (preset) return NUMBER_LABEL_PRESETS[preset].label;
  return `自定义：${labels.slice(0, 3).join(' ')}`;
}

function previewBoxShadow(layout, colors) {
  const shadows = [];
  const borderColor = rgbaToCss(colors.border || colors.back);
  const horizontal = Number(layout.borderWidth) || 0;
  const vertical = Number(layout.borderHeight) || 0;
  if (horizontal > 0) {
    shadows.push(`inset ${horizontal}px 0 0 ${borderColor}`);
    shadows.push(`inset -${horizontal}px 0 0 ${borderColor}`);
  }
  if (vertical > 0) {
    shadows.push(`inset 0 ${vertical}px 0 ${borderColor}`);
    shadows.push(`inset 0 -${vertical}px 0 ${borderColor}`);
  }
  if (layout.shadowSize) {
    shadows.push(`${layout.shadowOffsetX}px ${layout.shadowOffsetY}px ${layout.shadowSize}px ${rgbaToCss(colors.shadow)}`);
  }
  return shadows.length ? shadows.join(', ') : 'none';
}

function numberSeparatorOptionLabel(layout) {
  if (layout.numberSeparator !== CUSTOM_SEPARATOR && layout.customNumberSeparator === undefined) return '';
  const separator = separatorFromLayout(layout);
  return separator ? `自定义：${separator.replace(/ /g, '空格')}` : '自定义：无间隔';
}

function normalizedLayout(skin) {
  const layout = skin.layout || {};
  const fontPoint = layout.fontPoint || 16;
  return {
    mode: state.selectedPlatform === 'squirrel'
      ? layout.candidateListLayout || 'stacked'
      : layout.horizontal ? 'linear' : 'stacked',
    fontFace: layout.fontFace || COMMON_FONTS[0],
    fontPoint,
    labelFontFace: layout.labelFontFace || layout.fontFace || COMMON_FONTS[0],
    labelFontPoint: layout.labelFontPoint || fontPoint,
    commentFontFace: layout.commentFontFace || layout.fontFace || COMMON_FONTS[0],
    commentFontPoint: layout.commentFontPoint || fontPoint,
    labelFormat: layout.labelFormat || '',
    candidateFormat: layout.candidateFormat || '',
    alternativeSelectLabels: normalizeLabelList(layout.alternativeSelectLabels),
    customNumberSeparator: layout.customNumberSeparator,
    numberStyle: numberStyleFromLayout(layout),
    numberSeparator: candidateSeparatorFromLayout(layout),
    labelGap: gapForSeparator(layout),
    markText: layout.markText || '',
    inlinePreedit: layout.inlinePreedit,
    preeditType: layout.preeditType || '',
    previewPreedit: layout.previewPreedit || 'auto',
    preeditVisibility: layout.previewPreedit || 'auto',
    cornerRadius: layout.cornerRadius ?? 6,
    hilitedCornerRadius: layout.hilitedCornerRadius ?? layout.cornerRadius ?? 6,
    marginX: layout.marginX ?? 5,
    marginY: layout.marginY ?? 5,
    borderWidth: layout.borderWidth ?? 1,
    borderHeight: layout.borderHeight ?? 1,
    candidateSpacing: layout.candidateSpacing ?? layout.spacing ?? 8,
    hiliteSpacing: layout.hiliteSpacing ?? 4,
    lineSpacing: layout.lineSpacing ?? layout.candidateSpacing ?? 8,
    hilitePadding: layout.hilitePadding ?? 2,
    hilitePaddingX: layout.hilitePaddingX ?? layout.hilitePadding ?? 2,
    hilitePaddingY: layout.hilitePaddingY ?? layout.hilitePadding ?? 2,
    minWidth: layout.minWidth ?? 96,
    minHeight: layout.minHeight ?? 0,
    shadowSize: layout.shadowRadius ?? layout.shadowSize ?? 8,
    shadowOffsetX: layout.shadowOffsetX ?? 0,
    shadowOffsetY: layout.shadowOffsetY ?? 4,
  };
}

function defaultLayoutForPlatform(platform) {
  return platform === 'squirrel'
    ? {
        fontFace: COMMON_FONTS[0],
        fontPoint: 16,
        labelFontPoint: 16,
        commentFontPoint: 16,
        candidateListLayout: 'stacked',
        candidateFormat: '%c. %@',
        cornerRadius: 6,
        hilitedCornerRadius: 6,
        borderWidth: 1,
        borderHeight: 1,
        spacing: 8,
        hiliteSpacing: 4,
        lineSpacing: 8,
        minWidth: 96,
      }
    : {
        fontFace: COMMON_FONTS[0],
        fontPoint: 16,
        labelFontPoint: 16,
        commentFontPoint: 16,
        horizontal: false,
        labelFormat: '%s.',
        markText: '',
        cornerRadius: 6,
        hilitedCornerRadius: 6,
        marginX: 5,
        marginY: 5,
        borderWidth: 1,
        borderHeight: 1,
        candidateSpacing: 8,
        hiliteSpacing: 4,
        lineSpacing: 8,
        hilitePadding: 2,
        hilitePaddingX: 2,
        hilitePaddingY: 2,
        minWidth: 96,
        minHeight: 0,
        shadowRadius: 8,
        shadowOffsetX: 0,
        shadowOffsetY: 4,
      };
}

function cloneSkin(skin) {
  return cloneJson(skin);
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function rgbaToCssHex(color) {
  return `#${toHex(color.r)}${toHex(color.g)}${toHex(color.b)}`;
}

function cssHexToRgba(value) {
  const raw = value.replace('#', '');
  return {
    r: parseInt(raw.slice(0, 2), 16),
    g: parseInt(raw.slice(2, 4), 16),
    b: parseInt(raw.slice(4, 6), 16),
    a: 255,
  };
}

function rgbaToCss(color) {
  return `rgba(${color.r}, ${color.g}, ${color.b}, ${(color.a ?? 255) / 255})`;
}

function toHex(value) {
  return Math.max(0, Math.min(255, Math.round(value))).toString(16).padStart(2, '0');
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function logMessage(message) {
  dom.messageLog.textContent = `${new Date().toLocaleTimeString()} ${message}\n${dom.messageLog.textContent || ''}`.trim();
}
})();
