(function (root) {
'use strict';

const DEFAULT_PREVIEW_CANDIDATES = [
  { label: '1', text: '是', comment: '{{quick_code}}' },
  { label: '2', text: '试', comment: '{{quick_code}}' },
  { label: '3', text: '说', comment: '' },
  { label: '4', text: '谁', comment: '' },
  { label: '5', text: '上', comment: '' },
];
const DEFAULT_PREVIEW_CODE = 'u';
const DEFAULT_PREVIEW_CANDIDATES_TEXT = '是\t{{quick_code}}\n试\t{{quick_code}}\n说\n谁\n上';

function colorIsVisible(color) {
  return Boolean(color && (color.a ?? 255) > 0);
}

function markerBehavior(platform, layout = {}, colors = {}, active = false) {
  if (platform !== 'weasel') {
    return {
      type: 'none',
      hasSlot: false,
      visible: false,
      text: '',
    };
  }

  const text = String(layout.markText || '');
  const hasVisibleColor = colorIsVisible(colors.hilitedMark);
  if (text) {
    return {
      type: hasVisibleColor ? 'text' : 'none',
      hasSlot: hasVisibleColor,
      visible: Boolean(active && hasVisibleColor),
      text: hasVisibleColor ? text : '',
    };
  }
  if (hasVisibleColor) {
    return {
      type: 'win11',
      hasSlot: true,
      visible: Boolean(active),
      text: '',
    };
  }
  return {
    type: 'none',
    hasSlot: false,
    visible: false,
    text: '',
  };
}

function parsePreviewCandidateLine(line) {
  const text = String(line || '').trim();
  if (!text) return { text: '', comment: '' };
  const tabParts = text.split('\t');
  if (tabParts.length >= 2) {
    return { text: tabParts[0].trim(), comment: tabParts.slice(1).join('\t').trim() };
  }
  const pipeIndex = text.indexOf('|');
  if (pipeIndex >= 0) {
    return { text: text.slice(0, pipeIndex).trim(), comment: text.slice(pipeIndex + 1).trim() };
  }
  const commaIndex = text.indexOf(',');
  if (commaIndex >= 0) {
    return { text: text.slice(0, commaIndex).trim(), comment: text.slice(commaIndex + 1).trim() };
  }
  const spaced = text.match(/^(.+?)\s{2,}(.+)$/);
  if (spaced) return { text: spaced[1].trim(), comment: spaced[2].trim() };
  return { text, comment: '' };
}

function previewCandidateItems(source, fallback = DEFAULT_PREVIEW_CANDIDATES) {
  const raw = String(source || '').trim();
  const lines = raw ? raw.split(/\r?\n/) : [];
  const items = lines
    .map((line) => parsePreviewCandidateLine(line))
    .filter((item) => item.text)
    .map((item, index) => ({ label: String(index + 1), ...item }));
  return items.length ? items : fallback;
}

function resolvePreviewComment(comment, indicators = {}) {
  return String(comment || '').replace(/\{\{quick_code\}\}/g, String(indicators.quickCodeIndicator || ''));
}

function applyAlpha(color, alpha) {
  return {
    ...(color || { r: 0, g: 0, b: 0 }),
    a: Math.max(0, Math.min(255, Math.round(Number(alpha) || 0))),
  };
}

const api = {
  DEFAULT_PREVIEW_CANDIDATES,
  DEFAULT_PREVIEW_CODE,
  DEFAULT_PREVIEW_CANDIDATES_TEXT,
  applyAlpha,
  colorIsVisible,
  markerBehavior,
  parsePreviewCandidateLine,
  previewCandidateItems,
  resolvePreviewComment,
};

if (typeof module !== 'undefined' && module.exports) {
  module.exports = api;
}
root.RimeSkinPreviewModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
