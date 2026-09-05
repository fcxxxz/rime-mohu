package.path = "./lua/?.lua;./tiger_sentence_native/?.lua;" .. package.path

-- 词级上下文候选重排测试：
--   A) lua/mohu_word_order_filter.lua 单元测试（假 mohu_sentence 模块）：
--      重排生效、各降级直通、评分出错停用、稳定边界（pinned/native/
--      单字/⚡️/punct 原位）、OOV 不动、rank_penalty 边界、N 上限流式直通。
--   B) tiger_sentence_native/mohu_tiger_sentence.lua 的 acquire_word_scorer
--      集成（mock dylib）：容器词层/显式加载可用性判定、旧 ABI 降级。

local failures = 0
local function check(name, ok, detail)
  if ok then
    print("pass: " .. name)
  else
    print("fail: " .. name .. (detail and (" [" .. detail .. "]") or ""))
    failures = failures + 1
  end
end

-- ======================================================================
-- A) filter 单元测试
-- ======================================================================

-- 假 mohu_sentence：acquire_word_scorer / acquire_char_scorer 分别按
-- scorer_state / char_state 返回预设评分函数（word 信号 / char 信号）。
local scorer_state = {
  available = true,
  acquire_error = false,
  fn = nil,
  acquired = 0,
  invoked = 0,
  last_handle = nil,
  last_history = nil,
  last_texts = nil,
}
local char_state = {
  available = true,
  acquire_error = false,
  fn = nil,
  acquired = 0,
  invoked = 0,
  last_handle = nil,
  last_history = nil,
  last_texts = nil,
}
package.preload["mohu_sentence"] = function()
  return {
    acquire_word_scorer = function(env)
      scorer_state.acquired = scorer_state.acquired + 1
      if scorer_state.acquire_error then error("scorer accessor failed") end
      if not scorer_state.available then return nil end
      return scorer_state.fn, 7
    end,
    acquire_char_scorer = function(env)
      char_state.acquired = char_state.acquired + 1
      if char_state.acquire_error then error("scorer accessor failed") end
      if not char_state.available then return nil end
      return char_state.fn, 9
    end,
  }
end

local filter = require("mohu_word_order_filter")

local yielded = {}
yield = function(candidate) yielded[#yielded + 1] = candidate end

local function candidate(kind, text, comment)
  local value = {
    type = kind,
    text = text,
    preedit = "mohu",
    comment = comment or "",
  }
  function value:get_genuine() return self end
  return value
end

local function make_env(options)
  options = options or {}
  local config = options.config or {}
  local ctx = {
    options = { contextual_order = options.contextual_order ~= false },
    commit_history = {
      latest_text = function() return options.history or "" end,
    },
  }
  function ctx:get_option(name) return self.options[name] end
  local engine = {
    context = ctx,
    schema = {
      config = {
        get_string = function(_, key)
          if key == "mohu/quick_code_indicator" then return "⚡️" end
          if key == "mohu/pin/indicator" then return "📌" end
          return config[key]
        end,
        get_int = function(_, key)
          local value = config[key]
          return type(value) == "number" and value or nil
        end,
      },
    },
  }
  return { engine = engine }
end

local function run_filter(env, candidates)
  yielded = {}
  local input = {
    iter = function()
      local index = 0
      return function()
        index = index + 1
        return candidates[index]
      end
    end,
  }
  filter.func(input, env)
  return yielded
end

local function texts_of(list)
  local texts = {}
  for index = 1, #list do texts[index] = list[index].text end
  return texts
end

local function same_texts(a, b)
  if #a ~= #b then return false end
  for index = 1, #a do
    if a[index] ~= b[index] then return false end
  end
  return true
end

local function same_multiset(a, b)
  if #a ~= #b then return false end
  local count = {}
  for _, value in ipairs(a) do count[value] = (count[value] or 0) + 1 end
  for _, value in ipairs(b) do
    count[value] = (count[value] or 0) - 1
    if count[value] < 0 then return false end
  end
  return true
end

local function bind_fn(scores, state)
  return function(handle, history, texts)
    state.invoked = state.invoked + 1
    state.last_handle = handle
    state.last_history = history
    state.last_texts = texts
    if type(scores) == "table" then
      -- 评分函数契约：返回个数必须与候选文本数一致。
      local out = {}
      for index = 1, #texts do out[index] = scores[index] end
      return out
    end
    return error(scores)  -- 字符串：让 pcall 失败
  end
end

local function reset_scorer(scores)
  for _, state in ipairs({ scorer_state, char_state }) do
    state.available = true
    state.acquire_error = false
    state.acquired = 0
    state.invoked = 0
    state.last_handle = nil
    state.last_history = nil
    state.last_texts = nil
    state.fn = bind_fn(scores, state)
  end
end

local function run_filter_stream(env, candidates)
  yielded = {}
  local tracker = { advances = 0, first_yield_advances = nil }
  local old_yield = _G.yield
  local input = {
    iter = function()
      local index = 0
      return function()
        tracker.advances = tracker.advances + 1
        index = index + 1
        local value = candidates[index]
        if value == "__ITERATOR_ERROR__" then error("iterator failed") end
        return value
      end
    end,
  }
  _G.yield = function(value)
    if tracker.first_yield_advances == nil then
      tracker.first_yield_advances = tracker.advances
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
  return yielded, tracker
end

-- 1) 上下文重排生效：原名次 2 的候选以足够优势胜出。
do
  reset_scorer({ -10, -8, -12, -13, -14 })
  local env = make_env({ history = "我想吃" })
  filter.init(env)
  local input = {
    candidate("table", "中心"),
    candidate("table", "目标"),
    candidate("table", "其他"),
    candidate("table", "另外"),
    candidate("table", "其余"),
  }
  local out = run_filter(env, input)
  local texts = texts_of(out)
  check("reorder promotes the contextually better candidate",
        texts[1] == "目标" and texts[2] == "中心")
  check("reorder keeps every candidate (no loss)",
        #out == 5 and same_multiset(texts, texts_of(input)))
  check("scorer received handle/history/texts",
        char_state.invoked == 1 and char_state.last_handle == 9 and
        char_state.last_history == "我想吃" and
        same_texts(char_state.last_texts, { "中心", "目标", "其他", "另外", "其余" }))
end

-- 2) 各降级路径直通：输出与输入逐候选相同，且不触发评分。
local passthrough_cases = {
  {
    name = "contextual_order off",
    options = { history = "我想吃", contextual_order = false },
  },
  {
    name = "empty history",
    options = { history = "", contextual_order = true },
  },
  {
    name = "cjk-free history",
    options = { history = "abc", contextual_order = true },
  },
  {
    name = "tiger/word_order disabled",
    options = { history = "我想吃", config = { ["tiger/word_order"] = "false" } },
  },
}
for _, case in ipairs(passthrough_cases) do
  reset_scorer({ -1, -2, -3 })
  local env = make_env(case.options)
  filter.init(env)
  local input = {
    candidate("table", "中心"),
    candidate("table", "目标"),
    candidate("table", "其他"),
  }
  local out = run_filter(env, input)
  check("passthrough: " .. case.name,
        same_texts(texts_of(out), texts_of(input)) and scorer_state.invoked == 0)
end

do
  reset_scorer({ -1, -2, -3 })
  scorer_state.available = false  -- 词层不可用
  char_state.available = false    -- 字符评分也不可用（acquire 返回 nil）
  local env = make_env({ history = "我想吃" })
  filter.init(env)
  local input = {
    candidate("table", "中心"),
    candidate("table", "目标"),
  }
  local out = run_filter(env, input)
  check("passthrough: scorer unavailable",
        same_texts(texts_of(out), texts_of(input)) and scorer_state.invoked == 0)
  scorer_state.available = true
  char_state.available = true
end

-- 2b) 信号路由：默认走字符评分（handle 9）；word_order_signal=word 走词
--     评分（handle 7）且字符评分不被调用；char 信号下字符评分不可用则
--     直通（即使词层可用）。
do
  reset_scorer({ -9, -5 })
  local env = make_env({ history = "我想吃" })
  filter.init(env)
  run_filter(env, { candidate("table", "中心"), candidate("table", "目标") })
  check("default signal uses the char scorer",
        char_state.invoked == 1 and char_state.last_handle == 9 and
        scorer_state.invoked == 0)

  reset_scorer({ -9, -5 })
  local env_word = make_env({
    history = "我想吃",
    config = { ["tiger/word_order_signal"] = "word" },
  })
  filter.init(env_word)
  run_filter(env_word, { candidate("table", "中心"), candidate("table", "目标") })
  check("word_order_signal=word routes to the word scorer",
        scorer_state.invoked == 1 and scorer_state.last_handle == 7 and
        char_state.invoked == 0)

  reset_scorer({ -9, -5 })
  char_state.available = false
  local out = run_filter(env, { candidate("table", "中心"), candidate("table", "目标") })
  check("char signal with char scorer unavailable passes through",
        same_texts(texts_of(out), { "中心", "目标" }) and scorer_state.invoked == 0)
  char_state.available = true
end

-- 3) 评分抛错 → 直通并停用（_wo_dead），此后不再重试。
do
  reset_scorer("boom")
  local env = make_env({ history = "我想吃" })
  filter.init(env)
  local input = {
    candidate("table", "中心"),
    candidate("table", "目标"),
    candidate("table", "其他"),
  }
  local out1 = run_filter(env, input)
  check("scorer error passes through unchanged",
        same_texts(texts_of(out1), texts_of(input)))
  check("scorer error marks the env dead", env._wo_dead == true)

  reset_scorer({ -1, -9, -5 })
  local out2 = run_filter(env, input)
  check("dead env never retries scoring",
        same_texts(texts_of(out2), texts_of(input)) and scorer_state.invoked == 0)
end

do
  reset_scorer({ -1, -2 })
  char_state.acquire_error = true
  local env = make_env({ history = "我想吃" })
  filter.init(env)
  local input = { candidate("table", "中心"), candidate("table", "目标") }
  local out1 = run_filter(env, input)
  check("scorer accessor error passes through",
        same_texts(texts_of(out1), texts_of(input)) and env._wo_dead == true)
  char_state.acquire_error = false
  reset_scorer({ -1, -2 })
  local out2 = run_filter(env, input)
  check("scorer accessor error is not retried",
        same_texts(texts_of(out2), texts_of(input)) and char_state.acquired == 0)
end

-- 4) 稳定边界：pinned/native/单字/⚡️ 注释候选在首个可重排候选之前原样
--    输出；块内 punct 候选位置不动，重排只发生在其余候选之间。
do
  reset_scorer({ -10, -6 })
  local env = make_env({ history = "我想吃" })
  filter.init(env)
  local input = {
    candidate("pinned", "固顶词"),
    candidate("mohu_zrm", "原生候选"),
    candidate("sentence", "字"),
    candidate("table", "简码词", "⚡️标记"),
    candidate("table", "中心"),
    candidate("punct", "，，"),
    candidate("table", "目标"),
  }
  local out = run_filter(env, input)
  local texts = texts_of(out)
  check("stable prefix stays first",
        same_texts({ texts[1], texts[2], texts[3], texts[4] },
                   { "固顶词", "原生候选", "字", "简码词" }))
  check("in-block punct keeps its position",
        texts[6] == "，，" and texts[5] == "目标" and texts[7] == "中心")
  check("stable boundary keeps the candidate set",
        #out == 7 and same_multiset(texts, texts_of(input)))
end

-- 5) word 信号：OOV（-20 无信号）不参与重排、保持原位。
do
  reset_scorer({ -20, -9, -5 })
  local env = make_env({
    history = "我想吃",
    config = { ["tiger/word_order_signal"] = "word" },
  })
  filter.init(env)
  local input = {
    candidate("table", "生僻词甲"),
    candidate("table", "高分乙"),
    candidate("table", "低分丙"),
  }
  local out = run_filter(env, input)
  local texts = texts_of(out)
  check("oov candidate is not displaced (word signal)",
        texts[1] == "生僻词甲",
        "got " .. table.concat(texts, "/"))
  check("oov run keeps every candidate exactly once",
        same_multiset(texts, texts_of(input)),
        "got " .. table.concat(texts, "/"))
  check("scored pair reorders behind the oov",
        texts[2] == "低分丙" and texts[3] == "高分乙",
        "got " .. table.concat(texts, "/"))
end

-- 5b) char 信号无 OOV 概念：极低的累加分也全员参与重排（落后即沉底）。
do
  reset_scorer({ -20, -9, -5 })
  local env = make_env({ history = "我想吃" })  -- 默认 char 信号
  filter.init(env)
  local input = {
    candidate("table", "生僻词甲"),
    candidate("table", "高分乙"),
    candidate("table", "低分丙"),
  }
  local out = run_filter(env, input)
  local texts = texts_of(out)
  check("char signal reorders every candidate (no oov floor)",
        same_texts(texts, { "低分丙", "高分乙", "生僻词甲" }) and
        same_multiset(texts, texts_of(input)),
        "got " .. table.concat(texts, "/"))
end

-- 6) rank_penalty 边界（默认 1.0）：F 值恰好相等 → 稳定保持原序；
--    略微胜出 → 换位。
do
  reset_scorer({ -6, -5.0 })  -- F1=-6, F2=-6.0：平分
  local env = make_env({ history = "我想吃" })
  filter.init(env)
  local input = { candidate("table", "中心"), candidate("table", "目标") }
  local out = run_filter(env, input)
  check("tied fused scores keep dictionary order",
        same_texts(texts_of(out), { "中心", "目标" }))

  reset_scorer({ -6, -4.9 })  -- F1=-6, F2=-5.9：恰好胜出
  local out2 = run_filter(env, input)
  check("a sufficient margin swaps the pair",
        same_texts(texts_of(out2), { "目标", "中心" }))
end

-- 7) N 上限：只收集前 N 个候选评分，其余流式直通、顺序保持。
--    （penalty=1.0 下 F：其一 -6、其二 -9、其三 -5.5 → 其三领先。）
do
  reset_scorer({ -6, -7, -3.5 })
  local env = make_env({
    history = "我想吃",
    config = { ["tiger/word_order_candidates"] = 3 },
  })
  filter.init(env)
  local input = {
    candidate("table", "其一"),
    candidate("table", "其二"),
    candidate("table", "其三"),
    candidate("table", "其四"),
    candidate("table", "其五"),
  }
  local out = run_filter(env, input)
  check("limit caps the scored batch at N",
        char_state.invoked == 1 and char_state.last_texts ~= nil and
        same_texts(char_state.last_texts, { "其一", "其二", "其三" }))
  check("tail streams through in order after the reordered head",
        same_texts(texts_of(out), { "其三", "其一", "其二", "其四", "其五" }))
end

-- 8) 预算先于 scorer：预算命中前未凑齐完整 block 时必须原序直通，
--    且过滤器不能为了评分提前触发引擎创建。
do
  reset_scorer({ -1, -2, -3 })
  local env = make_env({
    history = "我想吃",
    config = {
      ["tiger/word_order_candidates"] = 3,
      ["tiger/word_order_scan_budget"] = 4,
      ["tiger/word_order_time_budget_ms"] = 100000,
    },
  })
  filter.init(env)
  local input = {
    candidate("table", "甲"), candidate("table", "乙"),
    candidate("table", "丙"), candidate("table", "中心"),
    candidate("table", "目标"), candidate("table", "尾部"),
  }
  local out = run_filter_stream(env, input)
  check("budget before complete block passes source order",
        same_texts(texts_of(out), texts_of(input)))
  check("budget before complete block does not acquire scorer",
        char_state.acquired == 0 and char_state.invoked == 0)
end

-- 9) 第 budget 个候选刚好补齐 >=2 个槽位时，仍允许一次评分。
do
  reset_scorer({ -1, -5 })
  local env = make_env({
    history = "我想吃",
    config = {
      ["tiger/word_order_candidates"] = 2,
      ["tiger/word_order_scan_budget"] = 4,
      ["tiger/word_order_time_budget_ms"] = 100000,
    },
  })
  filter.init(env)
  run_filter_stream(env, {
    candidate("table", "甲"), candidate("table", "乙"),
    candidate("table", "中心"), candidate("table", "目标"),
    candidate("table", "尾部"),
  })
  check("budget-th complete block still scores",
        char_state.acquired == 1 and char_state.invoked == 1)
end

-- 10) EOF 在预算内且有两个槽位时仍评分；同一 iterator 的尾部只能出现一次。
do
  reset_scorer({ -1, -5 })
  local env = make_env({
    history = "我想吃",
    config = {
      ["tiger/word_order_candidates"] = 3,
      ["tiger/word_order_scan_budget"] = 10,
      ["tiger/word_order_time_budget_ms"] = 100000,
    },
  })
  filter.init(env)
  local input = { candidate("table", "中心"), candidate("table", "目标") }
  local out, tracker = run_filter_stream(env, input)
  check("EOF with two candidates still scores",
        char_state.acquired == 1 and char_state.invoked == 1)
  check("EOF preserves the single iterator",
        same_texts(texts_of(out), { "中心", "目标" }) and tracker.advances == 3)
end

-- 11) 迭代器异常只记录一次，已消费候选仍按原序输出，不能让 filter 崩出协程。
do
  reset_scorer({ -1, -2 })
  local errors = 0
  local old_log = _G.log
  _G.log = { error = function() errors = errors + 1 end }
  local env = make_env({
    history = "我想吃",
    config = { ["tiger/word_order_scan_budget"] = 10 },
  })
  filter.init(env)
  local out = run_filter_stream(env, {
    candidate("table", "中心"), "__ITERATOR_ERROR__",
  })
  _G.log = old_log
  check("iterator error keeps consumed candidates", #out == 1 and out[1].text == "中心")
  check("iterator error logs once", errors == 1)
end

-- 12) 合法过期 deadline fail-open；时钟抛错（初始或扫描中）只关闭时间预算，
--     仍受候选数上限约束。
do
  local old_api, old_clock = _G.rime_api, os.clock
  local ticks = 0
  _G.rime_api = { get_time_ms = function()
    ticks = ticks + 1
    return ticks == 1 and 0 or 100
  end }
  os.clock = function() return 0 end
  reset_scorer({ -1, -2 })
  local env = make_env({
    history = "我想吃",
    config = {
      ["tiger/word_order_candidates"] = 3,
      ["tiger/word_order_scan_budget"] = 10,
      ["tiger/word_order_time_budget_ms"] = 4,
    },
  })
  filter.init(env)
  local out, tracker = run_filter_stream(env, {
    candidate("table", "中心"), candidate("table", "目标"),
  })
  check("expired deadline fails open before scoring",
        #out == 2 and char_state.acquired == 0 and tracker.first_yield_advances == 1)
  _G.rime_api, os.clock = old_api, old_clock
end

do
  local old_api, old_clock = _G.rime_api, os.clock
  local ticks = 0
  _G.rime_api = { get_time_ms = function()
    ticks = ticks + 1
    return ticks < 5 and 0 or 100
  end }
  os.clock = function() return 0 end
  reset_scorer({ -1, -2 })
  local env = make_env({
    history = "我想吃",
    config = {
      ["tiger/word_order_candidates"] = 3,
      ["tiger/word_order_scan_budget"] = 10,
      ["tiger/word_order_time_budget_ms"] = 4,
    },
  })
  filter.init(env)
  local out = run_filter_stream(env, {
    candidate("table", "中心"), candidate("table", "目标")
  })
  check("EOF call crossing deadline fails open",
        #out == 2 and char_state.acquired == 0)
  _G.rime_api, os.clock = old_api, old_clock
end

do
  local old_api, old_clock = _G.rime_api, os.clock
  _G.rime_api = { get_time_ms = function() error("clock unavailable") end }
  os.clock = function() error("clock unavailable") end
  reset_scorer({ -1, -2 })
  local env = make_env({
    history = "我想吃",
    config = {
      ["tiger/word_order_candidates"] = 3,
      ["tiger/word_order_scan_budget"] = 3,
      ["tiger/word_order_time_budget_ms"] = 4,
    },
  })
  filter.init(env)
  local input = { candidate("table", "甲"), candidate("table", "乙"),
                  candidate("table", "丙"), candidate("table", "中心") }
  local out, tracker = run_filter_stream(env, input)
  check("initial clock failure falls back to count cap",
        same_texts(texts_of(out), texts_of(input)) and char_state.acquired == 0 and
        tracker.first_yield_advances == 3)
  _G.rime_api, os.clock = old_api, old_clock
end

do
  local old_api, old_clock = _G.rime_api, os.clock
  local ticks = 0
  _G.rime_api = { get_time_ms = function()
    ticks = ticks + 1
    if ticks == 1 then return 0 end
    error("clock unavailable")
  end }
  os.clock = function() error("clock unavailable") end
  reset_scorer({ -1, -2 })
  local env = make_env({
    history = "我想吃",
    config = {
      ["tiger/word_order_candidates"] = 3,
      ["tiger/word_order_scan_budget"] = 3,
      ["tiger/word_order_time_budget_ms"] = 4,
    },
  })
  filter.init(env)
  local input = { candidate("table", "甲"), candidate("table", "乙"),
                  candidate("table", "丙"), candidate("table", "中心") }
  local out, tracker = run_filter_stream(env, input)
  check("mid-scan clock failure keeps count bound",
        same_texts(texts_of(out), texts_of(input)) and char_state.acquired == 0 and
        tracker.first_yield_advances == 3)
  _G.rime_api, os.clock = old_api, old_clock
end

-- ======================================================================
-- B) acquire_word_scorer 集成（mock dylib）
-- ======================================================================

local path_separator = package.config:sub(1, 1)
local root = (os.getenv("TEMP") or os.getenv("TMPDIR") or ".") ..
  path_separator .. "mohu-word-order-filter-test"

rime_api = { get_user_data_dir = function() return root end }
log = { error = function() end }

local decode_output = table.concat({
  "0 0 0 0 0 0 0 0 0 0",
  "原生句\tab cd\t0\t0\t1\t3:2",
  "",
}, "\n")

local function make_tiger_env(config)
  local ctx = {
    input = "",
    options = {},
    commit_history = { latest_text = function() return "我想吃" end },
  }
  function ctx:get_option(name) return self.options[name] end
  local engine = {
    context = ctx,
    schema = {
      config = {
        get_string = function(_, key) return config[key] end,
        get_int = function(_, key)
          local value = config[key]
          return type(value) == "number" and value or nil
        end,
      },
    },
  }
  return { engine = engine }
end

local function status_line(state, vocab)
  return table.concat({
    "path=/model.bin", "format=TCSKNM01", "bytes=1", "codes=1", "beam=200",
    "user_tri=0", "user_weight=0.850",
    "word_scorer=" .. state, "word_vocab=" .. (vocab or 0),
  }, "\t")
end

-- 每个场景重新 dofile，重置模块级 engine/word_scorer_ready 状态。
local function fresh(status_text, options)
  options = options or {}
  local calls = {}
  package.preload["mohu_tiger_reranker"] = function()
    return { init = function() end, fini = function() end,
             rerank = function() return nil end }
  end
  package.loadlib = function(lib, symbol)
    assert(symbol == "luaopen_tigerengine", "unexpected loadlib symbol")
    return function()
      local module = {
        create = function()
          calls.creates = (calls.creates or 0) + 1
          return 7
        end,
        free = function() end,
        decode = function() return decode_output, 0.1 end,
        status = function(handle)
          assert(handle == 7, "status must target the live handle")
          return status_text
        end,
        set_decode_context = function() return 1 end,
      }
      if options.word_scores ~= false then
        module.context_word_scores = function(handle, context, candidates)
          calls.scored = { handle = handle, context = context,
                           candidates = candidates }
          return { -5, -6 }
        end
      end
      if options.char_scores ~= false then
        module.context_char_scores = function(handle, context, candidates)
          calls.char_scored = { handle = handle, context = context,
                                candidates = candidates }
          return { -5.5, -6.5 }
        end
      end
      if options.load_scorer then
        module.load_word_scorer = function(handle, path)
          calls.loads = calls.loads or {}
          calls.loads[#calls.loads + 1] = { handle = handle, path = path }
          if options.load_scorer_ok == false then return false, "corrupt" end
          return true
        end
      end
      calls.module = module
      return module
    end
  end
  return dofile("tiger_sentence_native/mohu_tiger_sentence.lua"), calls
end

local function acquire_with(status_text, options, config)
  local mod, calls = fresh(status_text, options)
  local env = make_tiger_env(config or {})
  mod.translator.init(env)
  local fn, handle = mod.acquire_word_scorer(env)
  mod.translator.fini(env)
  return fn, handle, calls, env
end

-- 1) 容器词层（word_scorer=packed）：acquire 返回引擎的评分函数与句柄。
do
  local fn, handle, calls = acquire_with(status_line("packed", 364090))
  check("packed layer exposes the scorer",
        fn ~= nil and handle == 7 and fn == calls.module.context_word_scores)
  local scores = fn(handle, "我想吃", { "自助", "自主" })
  check("packed scorer is callable",
        type(scores) == "table" and scores[1] == -5 and scores[2] == -6)
end

-- scorer accessor 本身不得创建引擎；translator.init 才拥有生命周期。
do
  local mod, calls = fresh(status_line("packed", 364090))
  local env = make_tiger_env({})
  local word_fn = mod.acquire_word_scorer(env)
  local char_fn = mod.acquire_char_scorer(env)
  check("scorer accessors do not create an engine",
        word_fn == nil and char_fn == nil and calls.creates == nil)
end

do
  local mod = fresh(status_line("off", 0))
  local translator_env = make_tiger_env({})
  mod.translator.init(translator_env)
  -- A real lua_filter owns a different env; the shared module handle must
  -- still be visible to its accessor.
  local filter_env = make_tiger_env({})
  local fn, handle = mod.acquire_char_scorer(filter_env)
  check("separate filter env can use translator-owned handle",
        fn ~= nil and handle == 7)
  mod.translator.fini(translator_env)
end

-- 2) 纯字符引擎（word_scorer=off）：acquire 返回 nil。
do
  local fn = acquire_with(status_line("off", 0))
  check("char-only engine has no scorer", fn == nil)
end

-- 3) 旧 ABI dylib（无 context_word_scores）：即使 status 声称有词层也降级。
do
  local fn = acquire_with(status_line("packed", 364090),
                          { word_scores = false })
  check("old dylib (no context_word_scores) degrades to nil", fn == nil)
end

-- 4a) tiger/word_scorer_model 显式加载成功：acquire 可用且路径按配置传入。
do
  local fn, handle, calls = acquire_with(status_line("explicit", 12345), {
    load_scorer = true,
  }, { ["tiger/word_scorer_model"] = "/tmp/research-word-model.bin" })
  check("explicit scorer load makes acquire usable", fn ~= nil and handle == 7)
  check("explicit load passes the configured path",
        calls.loads ~= nil and #calls.loads == 1 and
        calls.loads[1].handle == 7 and
        calls.loads[1].path == "/tmp/research-word-model.bin")
end

-- 4b) 显式加载失败：引擎照常创建，但词层不可用（status off）→ nil。
do
  local fn, handle, calls = acquire_with(status_line("off", 0), {
    load_scorer = true,
    load_scorer_ok = false,
  }, { ["tiger/word_scorer_model"] = "/tmp/research-word-model.bin" })
  check("failed explicit load leaves no scorer", fn == nil and handle == nil)
  check("failed load was still attempted once",
        calls.loads ~= nil and #calls.loads == 1)
end

-- 5) acquire_char_scorer：纯字符引擎（word_scorer=off）即可用——char
--    信号不依赖词层；旧 ABI（无 context_char_scores）降级为 nil。
do
  local mod, calls = fresh(status_line("off", 0))
  local env = make_tiger_env({})
  mod.translator.init(env)
  local fn, handle = mod.acquire_char_scorer(env)
  check("char scorer available with plain char engine",
        fn ~= nil and handle == 7 and fn == calls.module.context_char_scores)
  local scores = fn(handle, "我想吃", { "自助", "自主" })
  check("char scorer is callable",
        type(scores) == "table" and scores[1] == -5.5 and scores[2] == -6.5)
  mod.translator.fini(env)
end

do
  local mod = fresh(status_line("off", 0), { char_scores = false })
  local env = make_tiger_env({})
  mod.translator.init(env)
  local fn = mod.acquire_char_scorer(env)
  check("old dylib (no context_char_scores) degrades to nil", fn == nil)
  mod.translator.fini(env)
end

if failures > 0 then
  print(string.format("%d failures", failures))
  os.exit(1)
end
print("all word order filter tests passed")
