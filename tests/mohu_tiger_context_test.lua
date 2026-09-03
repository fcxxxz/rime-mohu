package.path = "./tiger_sentence_native/?.lua;./lua/?.lua;" .. package.path

-- 跨候选左上文测试：contextual_order 开关喂/清引擎上下文（整段最近上屏
-- 文本 + 可配窗口）、4 键词码在有上文时由 native 接管、旧 ABI dylib 降级。

local root = "/tmp/mohu-tiger-context-test"
os.execute("rm -rf " .. root)
os.execute("mkdir -p " .. root)

rime_api = { get_user_data_dir = function() return root end }
log = { error = function() end }

local yielded = {}
Candidate = function(kind, start_pos, end_pos, text, comment)
  return { type = kind, start = start_pos, _end = end_pos, text = text, comment = comment }
end
yield = function(candidate) yielded[#yielded + 1] = candidate end

local decode_output = table.concat({
  "0 0 0 0 2 0",
  "原生句\tab cd ef\t0\t0\t1\t3:2,6:4",
  "原生候选\tab cd ef\t-1\t-1\t1\t3:2,6:4",
  "",
}, "\n")

local function notifier()
  local self = { connections = {} }
  function self:connect(callback)
    local connection = { callback = callback, disconnected = false }
    function connection:disconnect() self.disconnected = true end
    self.connections[#self.connections + 1] = connection
    return connection
  end
  return self
end

local function make_env(config, history_text, option_value)
  local ctx = {
    input = "",
    properties = {},
    options = { contextual_order = option_value },
    commit_notifier = notifier(),
    update_notifier = notifier(),
    commit_history = {
      latest_text = function() return history_text or "" end,
    },
  }
  function ctx:get_property(name) return self.properties[name] end
  function ctx:set_property(name, state) self.properties[name] = state end
  function ctx:get_option(name) return self.options[name] end
  function ctx:set_option(name, state) self.options[name] = state end
  function ctx:is_composing() return self.input ~= "" end
  function ctx:has_menu() return self.input ~= "" end
  local engine = {
    context = ctx,
    schema = {
      config = {
        get_string = function(_, key) return config[key] end,
        get_int = function(_, key)
          local v = config[key]
          return type(v) == "number" and v or nil
        end,
      },
    },
  }
  return { engine = engine }, ctx
end

local segment = {
  start = 0,
  _end = 6,
  has_tag = function(_, tag) return tag == "abc" end,
}

local function fresh(with_context_abi)
  local calls = { contexts = {}, decode_raws = {} }
  package.preload["mohu_tiger_reranker"] = function()
    return { init = function() end, fini = function() end, rerank = function() return nil end }
  end
  package.loadlib = function()
    return function()
      local module = {
        create = function() return 7 end,
        free = function() end,
        -- 记录每次 decode 的输入（ensure_engine 的 canary 探活用 "a"，
        -- 不应计入查询计数）。
        decode = function(_, raw)
          calls.decode_raws[#calls.decode_raws + 1] = raw
          return decode_output, 0.1
        end,
      }
      if with_context_abi then
        module.set_decode_context = function(handle, text, window)
          assert(handle == 7, "context must target the live engine handle")
          calls.contexts[#calls.contexts + 1] = { text = text, window = window }
          return 1
        end
      end
      return module
    end
  end
  return dofile("tiger_sentence_native/mohu_tiger_sentence.lua"), calls
end

local function run(with_abi, history, option, input, config)
  yielded = {}
  local mod, calls = fresh(with_abi)
  local env = make_env(config or {}, history, option)
  mod.translator.init(env)
  local before = #calls.decode_raws
  mod.translator.func(input, segment, env)
  local queried = #calls.decode_raws - before
  mod.translator.fini(env)
  return calls, queried, yielded
end

local failures = 0
local function check(name, ok)
  print((ok and "pass: " or "fail: ") .. name)
  if not ok then failures = failures + 1 end
end
local function last_context(calls)
  local c = calls.contexts[#calls.contexts]
  return c and c.text or nil, c and c.window or nil
end

-- 1) 开关开 + 上文「他向来」：整段历史喂引擎；默认 takeover 关闭，
--    4 键词码仍早退（smart 已学词频保持权威）。
do
  local calls, queried, out = run(true, "他向来", true, "ubji")
  local text, window = last_context(calls)
  check("whole latest_text fed", text == "他向来" and window == nil)
  check("4-key early return by default (no takeover)",
        queried == 0 and #out == 0)
end

-- 1b) takeover 配置开启：4 键词码由 native 接管。
do
  local calls, queried, out = run(true, "他向来", true, "ubji",
                                  { ["tiger/decode_context_takeover"] = "true" })
  check("4-key handled with takeover on", queried >= 1 and #out > 0)
  check("context fed with takeover on", last_context(calls) == "他向来")
end

-- 2) 开关关：传空串清空，4 键早退（保持既有行为）。
do
  local calls, queried, out = run(true, "他向来", false, "ubji")
  check("cleared when switch off", last_context(calls) == "")
  check("4-key early return without context", queried == 0 and #out == 0)
end

-- 3) 上文为空：清空，4 键早退。
do
  local calls, queried, out = run(true, "", true, "ubji")
  check("empty history clears", last_context(calls) == "")
  check("4-key early return on empty history", queried == 0 and #out == 0)
end

-- 4) 无汉字历史：lua 判定无上下文（4 键早退），但仍把原文传给引擎清空。
do
  local calls, queried, out = run(true, "abc123", true, "ubji")
  check("cjk-free history passed through for clearing", last_context(calls) == "abc123")
  check("4-key early return on cjk-free history", queried == 0 and #out == 0)
end

-- 5) 带辅码输入（≥5 键）保持既有 native 路径，上下文照喂。
do
  local calls, queried, out = run(true, "他向来", true, "ubwjim")
  check("aux input still native", queried >= 1 and #out > 0)
  check("context fed for aux input", last_context(calls) == "他向来")
end

-- 6) 开关关时 5 键输入照常（旧行为），上下文清空。
do
  local calls, queried, out = run(true, "他向来", false, "ubwjim")
  check("aux input native with switch off", queried >= 1 and #out > 0)
  check("context cleared for aux input", last_context(calls) == "")
end

-- 7) 配置窗口 decode_context_chars=1：窗口随调用传入。
do
  local calls, queried = run(true, "他向来", true, "ubji",
                    { ["tiger/decode_context_chars"] = 1 })
  local text, window = last_context(calls)
  check("config window forwarded", text == "他向来" and window == 1)
end

-- 8) 旧 ABI dylib（无 set_decode_context）：静默降级，不崩；4 键早退，
--    辅码路径不受影响。
do
  local calls, queried, out = run(false, "他向来", true, "ubji")
  check("old dylib degrades silently", #calls.contexts == 0 and queried == 0)
  local calls2, queried2, out2 = run(false, "他向来", true, "ubwjim")
  check("old dylib keeps aux path", queried2 >= 1 and #out2 > 0)
end

-- 9) 整段长历史原样透传（尾部截取是引擎内部的事）。
do
  local calls, queried = run(true, "第3批。他说他向来", true, "ubji")
  check("long history passed whole", last_context(calls) == "第3批。他说他向来")
end

if failures > 0 then
  print(string.format("%d failures", failures))
  os.exit(1)
end
print("all context tests passed")
