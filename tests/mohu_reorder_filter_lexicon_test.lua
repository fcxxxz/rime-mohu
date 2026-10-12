package.path = "lua/?.lua;" .. package.path

local filter = require("mohu_reorder_filter")
local evidence = false
package.loaded["mohu_sentence"] = {
  native_sentence_evidence = function(_, native_text, smart_text)
    return evidence and native_text == "第四扇门" and smart_text == "第四山门"
  end,
}

local function candidate(kind, text, preedit, comment, start, finish)
  local value = {
    type = kind,
    text = text,
    preedit = preedit or "mohu uh ke",
    comment = comment or "",
    start = start,
    _end = finish,
  }
  function value:get_genuine() return self end
  function value:get_dynamic_type()
    return kind == "sentence" and "Sentence" or "Phrase"
  end
  return value
end

local function run(candidates)
  local yielded = {}
  _G.yield = function(value) yielded[#yielded + 1] = value end
  local input = {
    iter = function()
      local index = 0
      return function()
        index = index + 1
        return candidates[index]
      end
    end,
  }
  local env = {
    reorder_threshold = 50,
    engine = { schema = { config = {
      get_string = function(_, key)
        if key == "mohu/quick_code_indicator" then return "⚡️" end
        if key == "mohu/pin/indicator" then return "📌" end
        return nil
      end,
    } } },
  }
  filter.init(env)
  filter.func(input, env)
  return yielded
end

local output = run({
  candidate("mohu_zrm", "万户上课"),
  candidate("phrase", "魔虎尚可"),
})
assert(#output == 1 and output[1].text == "魔虎尚可",
  "native-only hallucinated segmentation must be removed when the dictionary has a candidate")

local native_only = run({
  candidate("mohu_zrm", "模型候选"),
})
assert(#native_only == 1 and native_only[1].text == "模型候选",
  "native candidates remain available when no dictionary candidate exists")

local full_native = candidate("mohu_zrm", "第一扇门", "di yi uj mf", "", 0, 8)
local composed = candidate("sentence", "地一扇门", "di yi uj mf", "", 0, 8)
local partial_word = candidate("phrase", "第一扇", "di yi uj", "", 0, 6)
local partial_prefix = candidate("phrase", "第一", "di yi", "", 0, 4)
local composed_menu = run({full_native, composed, partial_word, partial_prefix})
assert(composed_menu[1] == full_native,
  "a dynamic smart sentence and prefix words must not veto a full native sentence")
local full_lexical = candidate("phrase", "完整词条", "di yi uj mf", "", 0, 8)
local lexical_menu = run({full_native, composed, partial_word, full_lexical})
assert(lexical_menu[1] ~= full_native,
  "a real lexical entry covering the same span retains the short-native lexicon guard")
local matched_menu = run({full_native,
  candidate("phrase", "第一扇门", "di yi uj mf", "", 0, 8), full_lexical})
assert(matched_menu[1] == full_native,
  "a native candidate independently present in the lexicon remains allowed")
local suffix = candidate("phrase", "扇门", "uj mf", "", 4, 8)
assert(run({full_native, composed, suffix, partial_word})[1] == full_native,
  "a lexical suffix cannot veto completion of the independently preferred prefix")
local completion = candidate("completion", "第一扇门后", "di yi uj mf", "", 0, 8)
assert(run({full_native, composed, completion, partial_word})[1] == full_native,
  "a longer completion is not a lexical entry for the entered sentence")
local wrapped = candidate("phrase", "地一扇门", "di yi uj mf", "", 0, 8)
function wrapped:get_genuine() return composed end
assert(run({full_native, wrapped, partial_word})[1] == full_native,
  "a wrapped dynamic sentence must still be distinguished from a real phrase")
local renamed_phrase = candidate("sentence", "完整词条", "di yi uj mf", "", 0, 8)
function renamed_phrase:get_dynamic_type() return "Phrase" end
assert(run({full_native, renamed_phrase})[1] ~= full_native,
  "the underlying Phrase class, rather than a mutable label, establishes word identity")
local consistent = candidate("sentence", "第一扇们", "di yi uj mf", "", 0, 8)
assert(run({full_native, consistent, partial_word})[1] == consistent,
  "do not override smart's suffix when its sentence already preserves the preferred prefix")
local unmatched = candidate("mohu_zrm", "滴一扇门", "di yi uj mf", "", 0, 8)
assert(run({unmatched, composed, partial_word})[1] == composed,
  "a native sentence inconsistent with the preferred prefix must remain guarded")
assert(run({full_native, composed})[1] == composed,
  "without an independent lexical prefix, retain the existing guard")
local fourth = candidate("mohu_zrm", "第四扇门", "di si uj mf", "", 0, 8)
local mountain = candidate("sentence", "第四山门", "di si uj mf", "", 0, 8)
local fourth_prefix = candidate("phrase", "第四", "di si", "", 0, 4)
evidence = true
assert(run({fourth, mountain, fourth_prefix})[1] == fourth,
  "strong full-sentence evidence must permit a native suffix correction")
assert(run({fourth, mountain, fourth_prefix,
    candidate("phrase", "第四山门", "di si uj mf", "", 0, 8)})[1] ~= fourth,
  "strong model evidence must not bypass a real full-span dictionary entry")
evidence = false
assert(run({fourth, mountain, fourth_prefix})[1] == mountain,
  "weak or unavailable full-sentence evidence must retain smart ordering")

local long_sentence = run({
  candidate("mohu_zrm", "你不要再精神内耗了"),
  candidate("sentence", "你不要在精神内耗了"),
})
assert(long_sentence[1] and long_sentence[1].text == "你不要再精神内耗了",
  "long native sentences must not require an exact dictionary duplicate")

-- 两字终态只可能来自两音节带辅码的输入，独立输出，不要求词库同文本。
local two_char = run({
  candidate("mohu_zrm", "杨娇", "yh jcbt"),
  candidate("sentence", "样娇", "yh jc"),
})
assert(two_char[1] and two_char[1].text == "杨娇",
  "two-character native candidates must stay independent of the lexicon set")
assert(#two_char == 2 and two_char[2].text == "样娇",
  "dictionary candidates keep flowing after the native pair")

-- 置顶后 native 个人词副本不得再输出第二条同文候选（2026-09-15 回归）：
-- pin 替换位输出的文本优先，native 侧（含 _personal 豁免路径）同文跳过。
local pin_dup = run({
  candidate("pinned", "李火旺", "lihowh", "📌"),
  candidate("mohu_zrm_personal", "李火旺", "lihowh"),
  candidate("sentence", "李火旺", "li ho wh"),
  candidate("sentence", "离火网", "li ho wh"),
})
local pin_copies = 0
for _, c in ipairs(pin_dup) do
  if c.text == "李火旺" then pin_copies = pin_copies + 1 end
end
assert(pin_copies == 1,
  "pinned text must not be duplicated by its native personal copy")
assert(pin_dup[1].text == "李火旺" and pin_dup[1].comment == "📌",
  "the pin-position copy keeps the pin indicator")
assert(pin_dup[#pin_dup].text == "离火网",
  "unrelated dictionary candidates keep flowing")

-- pin 没有可替换的 smart 副本时（用户词库尚无该词），pin 本体直接输出，
-- native 个人词副本同样不得重复。
local pin_no_smart = run({
  candidate("pinned", "李火旺", "lihowh", "📌"),
  candidate("mohu_zrm_personal", "李火旺", "lihowh"),
  candidate("sentence", "离火网", "li ho wh"),
})
local pin_no_smart_copies = 0
for _, c in ipairs(pin_no_smart) do
  if c.text == "李火旺" then pin_no_smart_copies = pin_no_smart_copies + 1 end
end
assert(pin_no_smart_copies == 1 and pin_no_smart[1].text == "李火旺",
  "unmatched pinned candidate yields once and native personal copy is skipped")

print("Mohu reorder lexicon-first tests passed")
