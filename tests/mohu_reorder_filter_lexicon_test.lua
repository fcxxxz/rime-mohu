package.path = "lua/?.lua;" .. package.path

local filter = require("mohu_reorder_filter")

local function candidate(kind, text, preedit, comment)
  local value = {
    type = kind,
    text = text,
    preedit = preedit or "mohu uh ke",
    comment = comment or "",
  }
  function value:get_genuine() return self end
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
  candidate("sentence", "魔虎尚可"),
})
assert(#output == 1 and output[1].text == "魔虎尚可",
  "native-only hallucinated segmentation must be removed when the dictionary has a candidate")

local native_only = run({
  candidate("mohu_zrm", "模型候选"),
})
assert(#native_only == 1 and native_only[1].text == "模型候选",
  "native candidates remain available when no dictionary candidate exists")

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
