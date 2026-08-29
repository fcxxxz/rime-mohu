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
  candidate("mohu_llm_zrm", "万户上课"),
  candidate("sentence", "魔虎尚可"),
})
assert(#output == 1 and output[1].text == "魔虎尚可",
  "native-only hallucinated segmentation must be removed when the dictionary has a candidate")

local native_only = run({
  candidate("mohu_llm_zrm", "模型候选"),
})
assert(#native_only == 1 and native_only[1].text == "模型候选",
  "native candidates remain available when no dictionary candidate exists")

local long_sentence = run({
  candidate("mohu_llm_zrm", "你不要再精神内耗了"),
  candidate("sentence", "你不要在精神内耗了"),
})
assert(long_sentence[1] and long_sentence[1].text == "你不要再精神内耗了",
  "long native sentences must not require an exact dictionary duplicate")

print("Mohu reorder lexicon-first tests passed")
