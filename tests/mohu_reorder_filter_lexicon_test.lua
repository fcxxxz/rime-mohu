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

-- 协程驱动的边界回归：补全流必须在首个 yield 前只预取有界数量，且
-- 超预算时保留同一个 iterator、候选身份和展示字段。
local function run_coroutine(candidates, config)
  config = config or {}
  local yielded = {}
  local tracker = { advance_count = 0, first_yield_advances = nil }
  local old_yield = _G.yield
  local input = {
    iter = function()
      local index = 0
      return function()
        tracker.advance_count = tracker.advance_count + 1
        index = index + 1
        local value = candidates[index]
        if value == "__ITERATOR_ERROR__" then error("iterator failed") end
        return value
      end
    end,
  }
  local env = {
    reorder_threshold = 50,
    engine = { schema = { config = {
      get_string = function(_, key)
        if key == "mohu/quick_code_indicator" then return "⚡️" end
        if key == "mohu/pin/indicator" then return "📌" end
        local value = config[key]
        return value == nil and nil or tostring(value)
      end,
      get_int = function(_, key)
        local value = config[key]
        return type(value) == "number" and value or nil
      end,
    } } },
  }
  filter.init(env)
  _G.yield = function(value)
    if tracker.first_yield_advances == nil then
      tracker.first_yield_advances = tracker.advance_count
    end
    yielded[#yielded + 1] = value
    return coroutine.yield(value)
  end
  local co = coroutine.create(function() filter.func(input, env) end)
  while coroutine.status(co) ~= "dead" do
    local ok, err = coroutine.resume(co)
    assert(ok, err)
  end
  _G.yield = old_yield
  return yielded, tracker, env
end

local function contains_identity(list, wanted)
  for _, value in ipairs(list) do
    if value == wanted then return true end
  end
  return false
end

local function count_identity(list, wanted)
  local count = 0
  for _, value in ipairs(list) do
    if value == wanted then count = count + 1 end
  end
  return count
end

do
  local fixed = candidate("table", "固定", "mohu uh", "`F")
  local stream = { fixed }
  for index = 1, 100 do
    stream[#stream + 1] = candidate("sentence", "候选" .. index, "mohu uh ke")
  end
  local out, tracker = run_coroutine(stream, {
    ["mohu/reorder_scan_budget"] = 3,
    ["mohu/reorder_time_budget_ms"] = 100000,
  })
  assert(tracker.first_yield_advances <= 4,
    "oversized streams must yield after budget + sentinel pulls")
  assert(#out > 0 and out[1] == fixed and out[1].comment == "⚡️",
    "fallback preserves identity and only normalizes the internal fixed marker")
end

do
  local pinned_one = candidate("pinned", "甲", "a")
  local pinned_two = candidate("pinned", "乙", "b")
  local delayed_one = candidate("sentence", "甲", "a")
  local delayed_two = candidate("sentence", "乙", "b")
  local trigger = candidate("sentence", "丙", "c")
  local out = run_coroutine({ pinned_one, delayed_one, pinned_two,
                              delayed_two, trigger }, {
    ["mohu/reorder_scan_budget"] = 64,
    ["mohu/reorder_time_budget_ms"] = 100000,
  })
  assert(count_identity(out, delayed_one) == 1 and count_identity(out, delayed_two) == 1,
    "delayed smart candidates must not be emitted twice")
end

do
  local fixed = candidate("table", "固定", "mohu uh", "`F")
  local native = candidate("mohu_zrm", "甲乙丙", "mohu uh ke")
  local early_smart = candidate("sentence", "固定", "mohu uh")
  local later_smart = candidate("sentence", "甲乙丙", "mohu uh ke")
  local out = run_coroutine({ fixed, native, early_smart, later_smart }, {
    ["mohu/reorder_scan_budget"] = 64,
    ["mohu/reorder_time_budget_ms"] = 100000,
  })
  assert(contains_identity(out, native),
    "finite streams must retain native candidates covered by later smart text")
end

do
  local function boundary(size)
    local fixed = candidate("table", "固定", "mohu uh", "`F")
    local stream = { fixed }
    for index = 1, size do
      stream[#stream + 1] = candidate("sentence", "候选" .. index, "mohu uh ke")
    end
    local out, tracker = run_coroutine(stream, {
      ["mohu/reorder_scan_budget"] = 3,
      ["mohu/reorder_time_budget_ms"] = 100000,
    })
    return out, tracker
  end
  local _, below = boundary(1)
  local _, exact = boundary(2)
  local _, above = boundary(3)
  assert(below.first_yield_advances == 3,
    "budget-1 finite stream must consume its EOF sentinel")
  assert(exact.first_yield_advances == 4,
    "budget-sized finite stream must consume one sentinel")
  assert(above.first_yield_advances == 4,
    "budget+1 stream must yield after one sentinel candidate")
end

do
  local old_api, old_clock, old_log = _G.rime_api, os.clock, _G.log
  local errors = 0
  _G.rime_api = { get_time_ms = function() error("clock unavailable") end }
  os.clock = function() error("clock unavailable") end
  _G.log = { error = function() errors = errors + 1 end }
  local fixed = candidate("table", "固定", "mohu uh", "`F")
  local out, tracker = run_coroutine({ fixed, "__ITERATOR_ERROR__", candidate("sentence", "后续") }, {
    ["mohu/reorder_scan_budget"] = 8,
    ["mohu/reorder_time_budget_ms"] = 4,
  })
  _G.rime_api, os.clock, _G.log = old_api, old_clock, old_log
  assert(#out == 1 and out[1] == fixed and out[1].comment == "⚡️",
    "iterator errors emit the consumed buffer and preserve fallback identity")
  assert(errors == 1 and tracker.first_yield_advances == 2,
    "iterator errors and invalid returns are bounded and logged once")
end

do
  local old_api, old_clock = _G.rime_api, os.clock
  local ticks = 0
  _G.rime_api = { get_time_ms = function()
    ticks = ticks + 1
    return ticks < 5 and 0 or 100
  end }
  os.clock = function() return 0 end
  local fixed = candidate("table", "固定", "mohu uh", "`F")
  local smart = candidate("sentence", "固定", "mohu uh")
  local out, tracker = run_coroutine({ fixed, smart }, {
    ["mohu/reorder_scan_budget"] = 8,
    ["mohu/reorder_time_budget_ms"] = 4,
  })
  _G.rime_api, os.clock = old_api, old_clock
  assert(#out == 2 and out[1] == fixed and out[2] == smart,
    "EOF crossing the deadline must fail open in source order")
  assert(tracker.first_yield_advances == 3,
    "EOF deadline fallback must not restart or over-read the iterator")
end

print("Mohu reorder lexicon-first tests passed")
