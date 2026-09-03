package.path = "./tiger_sentence_native/?.lua;./lua/?.lua;" .. package.path

-- 两字候选放行测试：带辅码的两音节输入（>4 键）应输出原生两字候选，
-- 裸双拼四键输入仍完全交给 smart 通道（不输出任何候选）；
-- native 候选上屏时按 preedit 分段码重建裸双拼写入用户词库。

local root = "/tmp/mohu-tiger-two-char-test"
os.execute("rm -rf " .. root)
os.execute("mkdir -p " .. root .. "/mohu_llm/config")

rime_api = { get_user_data_dir = function() return root end }
log = { error = function() end }

local yielded = {}
Candidate = function(kind, start_pos, end_pos, text, comment)
  return { type = kind, start = start_pos, _end = end_pos, text = text, comment = comment }
end
yield = function(candidate) yielded[#yielded + 1] = candidate end

-- 杨娇/样娇/杨姣（两字）+ 一条单字与一条三字句；单字仍须被过滤。
local decode_output = table.concat({
  "0 0 0 0 4 0",
  "杨娇\tyhea jcbt\t-13.37\t-13.37\t1\t0:0,3:4,6:8",
  "杨姣\tyhea jcbt\t-20.00\t-20.00\t3\t0:0,3:4,6:8",
  "样娇\tyhea jcbt\t-20.71\t-20.71\t3\t0:0,3:4,6:8",
  "杨\tyheao\t-21.00\t-21.00\t1\t0:0,3:5",
  "",
}, "\n")

local function notifier()
  return {
    connections = {},
    connect = function(self, callback)
      local connection = { callback = callback, disconnected = false }
      function connection:disconnect() self.disconnected = true end
      self.connections[#self.connections + 1] = connection
      return connection
    end,
  }
end

-- Memory/DictEntry/ReverseLookup 桩：记录 update_userdict 写入，扫描路径安静跳过。
local userdb_writes = {}
Memory = function()
  return {
    update_userdict = function(_, entry, commits, prefix)
      userdb_writes[#userdb_writes + 1] =
        { text = entry.text, code = entry.custom_code, commits = commits, prefix = prefix }
      return true
    end,
    user_lookup = function() return false end,
    dictiter_lookup = function() return nil end,
    disconnect = function() end,
  }
end
DictEntry = function()
  return { text = "", custom_code = "" }
end
ReverseLookup = function()
  return {
    lookup = function(_, text)
      return ({ ["杨"] = "yh;ea", ["娇"] = "jc;bt", ["样"] = "yh;eg",
                ["姣"] = "jc;bt" })[text] or ""
    end,
  }
end

local function make_env(config)
  local ctx = {
    input = "",
    properties = {},
    options = {},
    commit_notifier = notifier(),
    update_notifier = notifier(),
    _commit_text = "",
    composition = nil,
  }
  function ctx:get_commit_text() return self._commit_text end
  local engine = {
    context = ctx,
    schema = {
      config = {
        get_string = function(_, key) return config[key] end,
        get_int = function() return nil end,
      },
    },
  }
  return { engine = engine }, ctx
end

local function fresh()
  package.preload["mohu_tiger_reranker"] = function()
    return { init = function() end, fini = function() end, rerank = function() return nil end }
  end
  package.loadlib = function()
    return function()
      return {
        create = function() return 7 end,
        free = function() end,
        decode = function() return decode_output, 0.1 end,
      }
    end
  end
  return dofile("tiger_sentence_native/mohu_tiger_sentence.lua")
end

local native = fresh()
local env, ctx = make_env({})
native.translator.init(env)

local function translate(raw)
  ctx.input = raw
  yielded = {}
  local segment = {
    start = 0,
    _end = #raw,
    has_tag = function(_, tag) return tag == "abc" end,
  }
  native.translator.func(raw, segment, env)
  return yielded
end

-- 带辅码的两音节输入：两字候选按原生顺序输出，杨娇居首；单字仍被过滤。
local candidates = translate("yheajcbt")
assert(#candidates == 3, "two-character finals must be yielded, single chars filtered")
assert(candidates[1].text == "杨娇" and candidates[2].text == "杨姣" and
  candidates[3].text == "样娇", "two-character candidates keep native order")
assert(candidates[1].quality == 50, "native candidates keep initial_quality")

-- 同样带辅码（首字一位辅码）的输入同样放行。
candidates = translate("yhejcb")
assert(#candidates == 3 and candidates[1].text == "杨娇",
  "partial-aux two-syllable input must also yield two-character candidates")

-- 裸双拼四键：不参与，交给 smart 通道。
candidates = translate("yhjc")
assert(#candidates == 0, "bare four-key input must stay with the smart translator")

-- native 候选上屏：按 preedit 每字一段重建裸双拼写入用户词库。
local committed = {
  type = "mohu_llm_zrm",
  text = "杨娇",
  preedit = "yh jcbt",
}
ctx.composition = {
  toSegmentation = function()
    return {
      get_segments = function()
        return {
          { get_selected_candidate = function() return committed end },
        }
      end,
    }
  end,
}
ctx._commit_text = "杨娇"
for _, connection in ipairs(ctx.commit_notifier.connections) do
  if not connection.disconnected then connection.callback(ctx) end
end
assert(#userdb_writes == 1, "the native commit must write exactly one userdb entry")
assert(userdb_writes[1].text == "杨娇" and userdb_writes[1].code == "yh;ea jc;bt ",
  "the userdb key must use trailing-space syllabary codes from reverse lookup")
assert(userdb_writes[1].commits == 1 and userdb_writes[1].prefix == "",
  "automatic learning uses one commit and no new-entry prefix")

-- 非 native 候选与超长句（音节数超上限）不写库。
userdb_writes = {}
committed = { type = "sentence", text = "样娇", preedit = "yh jcbt" }
for _, connection in ipairs(ctx.commit_notifier.connections) do
  if not connection.disconnected then connection.callback(ctx) end
end
committed = {
  type = "mohu_llm_zrm",
  text = "一二三四五六七八九十一",
  preedit = "aa bb cc dd ee ff gg hh ii jj kk",
}
for _, connection in ipairs(ctx.commit_notifier.connections) do
  if not connection.disconnected then connection.callback(ctx) end
end
assert(#userdb_writes == 0,
  "foreign candidates and over-limit sentences must not touch the userdb")

native.translator.fini(env)
print("Mohu two-char candidate tests passed")
