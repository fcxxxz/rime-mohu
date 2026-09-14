package.path = "./lua/?.lua;" .. package.path

-- 语义门控 filter 测试：fail-open 全路径 + 窗口重排 + provenance 边界。
-- 桩：socket.unix（可编程响应）、mohu_sentence.acquire_char_scorer（V5 分）、
-- mohu_semantic_meta（可选禁用）。

local checks = { passed = 0, failed = 0 }

-- librime-lua 在运行时注入 yield；测试内用 coroutine 版本模拟。
yield = function(cand) coroutine.yield(cand) end

local function check(name, ok)
  if ok then
    checks.passed = checks.passed + 1
    print("pass: " .. name)
  else
    checks.failed = checks.failed + 1
    print("fail: " .. name)
  end
end

local semantic_meta = require("mohu_semantic_meta")

-- ---------------------------------------------------------------- stubs

-- 桩：mohu_sentence.semantic_score（进程内 C2 scorer 的 Lua 侧入口）。
local model_calls = { requests = 0, response = nil, fail = false }

package.preload["mohu_sentence"] = function()
  return {
    acquire_char_scorer = function(_env)
      return function(_handle, _history, texts)
        local scores = {}
        for i = 1, #texts do scores[i] = -10 - i * 0.01 end
        -- top-2 近平（z 差 < 0.5 阈值）→ 判定为歧义菜单
        scores[1] = -10.0
        scores[2] = -10.001
        return scores
      end, 42
    end,
    semantic_score = function(_env, history, texts, native_scores)
      model_calls.requests = model_calls.requests + 1
      model_calls.last_history = history
      model_calls.last_texts = texts
      model_calls.last_native_scores = native_scores
      if model_calls.fail then return nil end
      return model_calls.response or { 1, 2, 3 }
    end,
  }
end

local tiger = require("mohu_sentence")
local filter = require("mohu_semantic_gate_filter")

-- ---------------------------------------------------------------- helpers

local function candidate(text, cand_type, provenance)
  local cand = {
    type = cand_type or "table",
    text = text,
    comment = "",
    get_genuine = function(self) return self end,
  }
  if provenance ~= false then
    local record = {
      provenance_version = "mohu-native-decode/v1",
      source = "native",
    }
    assert(semantic_meta.bind(cand, record))
  end
  return cand
end

local function run(cands, env_over)
  local env = {
    _se_enabled = true,
            _se_margin = 0.15,
    _se_gate_margin = 0.5,
    _se_k = 3,
    _se_limit = 20,
    _se_quick = "⚡️",
    _se_pin = "📌",
    _se_last_context = nil,
    engine = {
      schema = { config = {} },
      context = {
        get_option = function(_, name) return name == "neural_rerank" end,
        commit_history = { latest_text = function() return "今天的会议" end },
        input = "niha",
      },
    },
  }
  if env_over then
    for k, v in pairs(env_over) do env[k] = v end
  end
  filter.init(env)
  -- init() 从（空的）config 桩读取默认值会覆盖测试预设；init 后再恢复。
  env._se_enabled = true
  env._se_socket = "/tmp/test-semantic.sock"
  env._se_timeout = 30
  env._se_margin = 0.15
  env._se_gate_margin = 0.5
  env._se_k = 3
  env._se_limit = 20
  if env_over then
    for k, v in pairs(env_over) do env[k] = v end
  end
  local out = {}
  local iter_state = { index = 0, items = cands }
  local translation = {
    iter = function()
      return function(state)
        state.index = state.index + 1
        return state.items[state.index]
      end, iter_state
    end,
  }
  local co = coroutine.wrap(function()
    filter.func(translation, env)
  end)
  for cand in co do
    out[#out + 1] = cand
  end
  return out
end

-- ---------------------------------------------------------------- cases

-- 1. 默认关闭：直通且不触碰 socket。
model_calls.requests = 0
local out = run({ candidate("你好"), candidate("拟好") }, { _se_enabled = false })
check("disabled option passes through without socket",
      #out == 2 and model_calls.requests == 0)

-- 2. 大模型开关（neural_rerank）关闭：直通。
model_calls.requests = 0
out = run({ candidate("你好"), candidate("拟好") }, {
  engine = {
    schema = { config = {} },
    context = {
      get_option = function() return false end,
      commit_history = { latest_text = function() return "今天的会议" end },
    },
  },
})
check("neural_rerank off passes through", #out == 2 and model_calls.requests == 0)

-- 3. 最终菜单候选以 menu_index 为身份；跨 filter 后 userdata 包装会变化，
-- 不依赖上游弱表 provenance 绑定，未绑定候选也可按最终槽位参与。
model_calls.requests = 0
model_calls.response = { 1.0, 5.0, 3.0 }
out = run({
  candidate("甲词", "table", false),
  candidate("乙词", "table", false),
  candidate("丙词", "table", false),
})
check("final menu index identifies unbound candidates",
      #out == 3 and out[1].text == "乙词" and model_calls.requests == 1)

-- 4. 歧义菜单 + socket 可用 + 语义分明确翻转 → 窗口内重排，保护位不动。
model_calls.requests = 0
model_calls.fail = false
model_calls.response = { 1.0, 5.0, 3.0 }  -- 第 2 个候选语义最强
out = run({
  candidate("甲词"),
  candidate("乙词"),
  candidate("丙词"),
  { type = "punct", text = "，", comment = "", get_genuine = function(self) return self end },
})
check("semantic reorder applies within window",
      #out == 4 and out[1].text == "乙词" and out[2].text == "丙词" and
      out[3].text == "甲词" and out[4].text == "，")

-- 5. 语义 margin 不足 → 首选保持引擎原序（低位仍可有限重排）。
-- 第 2 名 z 分仅领先 ~0.05（< 0.15），首选翻转被 margin 拒绝。
model_calls.response = { 1.0, 1.001, 0.5 }
out = run({ candidate("甲词"), candidate("乙词"), candidate("丙词") })
check("weak semantic margin keeps native first choice",
      out[1].text == "甲词")

-- 6. socket 失败 → fail-open。
model_calls.fail = true
model_calls.requests = 0
out = run({ candidate("甲词"), candidate("乙词"), candidate("丙词") })
check("socket failure fails open to native order",
      out[1].text == "甲词" and out[2].text == "乙词")
model_calls.fail = false

-- 7. 响应格式错误 → fail-open。
model_calls.response = { "bad" }
out = run({ candidate("甲词"), candidate("乙词"), candidate("丙词") })
check("malformed response fails open", out[1].text == "甲词")

-- 8. 缓存命中：同一 env 连续两次相同 shortlist 不再发请求。
model_calls.response = { 1.0, 5.0, 3.0 }
model_calls.requests = 0
local cache_cands = { candidate("丁词"), candidate("戊词"), candidate("己词") }
local cache_env
local function cache_run()
  if not cache_env then
    cache_env = {
      _se_enabled = true,
                  _se_margin = 0.15,
      _se_gate_margin = 0.5,
      _se_k = 3,
      _se_limit = 20,
      _se_quick = "⚡️",
      _se_pin = "📌",
      _se_last_context = nil,
      engine = {
        schema = { config = {} },
        context = {
          get_option = function(_, name) return name == "neural_rerank" end,
          commit_history = { latest_text = function() return "今天的会议" end },
          input = "niha",
        },
      },
    }
    filter.init(cache_env)
    cache_env._se_enabled = true
            cache_env._se_margin = 0.15
    cache_env._se_gate_margin = 0.5
    cache_env._se_k = 3
    cache_env._se_limit = 20
  end
  local out = {}
  local iter_state = { index = 0, items = cache_cands }
  local translation = {
    iter = function()
      return function(state)
        state.index = state.index + 1
        return state.items[state.index]
      end, iter_state
    end,
  }
  local co = coroutine.wrap(function() filter.func(translation, cache_env) end)
  for cand in co do out[#out + 1] = cand end
  return out
end
cache_run()
local first_count = model_calls.requests
cache_run()
check("cache prevents repeat socket calls",
      first_count >= 1 and model_calls.requests == first_count)

print(string.format("%d passed, %d failed", checks.passed, checks.failed))
if checks.failed > 0 then os.exit(1) end
