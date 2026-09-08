package.path = "./lua/?.lua;./tiger_sentence_native/?.lua;" .. package.path

-- 门控学生重排测试（lua/mohu_student_gate_filter.lua）：
--   A) 引擎高分差（拿得定）→ 直通，socket 不被调用；
--   B) 引擎低分差（歧义）→ 请求学生，前 K 槽按学生分重排、其余不动；
--   C) 学生超时/不可用 → 直通 fail-open；
--   D) 无上屏历史 / 参与候选 <2 → 直通；
--   E) 相同请求命中缓存，不重复打 socket；
--   F) 学生分平坦 → 稳定保持引擎原序。

local failures = 0
local function check(name, ok, detail)
  if ok then
    print("pass: " .. name)
  else
    print("fail: " .. name .. (detail and (" [" .. detail .. "]") or ""))
    failures = failures + 1
  end
end

-- 假 mohu_sentence：char 评分函数返回 char_scores 预设。
local char_state = { fn = nil, invoked = 0 }
package.preload["mohu_sentence"] = function()
  return {
    acquire_char_scorer = function(env)
      if not char_state.fn then return nil end
      return char_state.fn, 9
    end,
  }
end

-- 假 socket 模块：记录请求，按 script 应答。
local socket_state = {
  requests = 0,
  last_payload = nil,
  mode = "ok",          -- ok | timeout | down
  response_scores = { -2.0, -1.0, -3.0, -4.0, -5.0 },
}
local fake_client = {}
package.preload["socket.unix"] = function() error("not in test") end
package.preload["socket"] = function()
  return {
    gettime = function() return os.clock() end,
    unix = function()
      local client = {}
      function client:settimeout() return true end
      function client:connect(path)
        socket_state.path = path
        return socket_state.mode ~= "down"
      end
      function client:send(data)
        if socket_state.mode == "timeout" then return 0 end
        socket_state.requests = socket_state.requests + 1
        socket_state.last_payload = data
        -- 按请求实际候选数生成等长响应。
        local n = 0
        for _ in data:gmatch("\t") do n = n + 1 end
        n = n - 1  -- 首个 tab 之后是 context
        local parts = { "OK" }
        for i = 1, n do parts[#parts + 1] =
          string.format("%.4f", socket_state.response_scores[i]) end
        client._response = table.concat(parts, "\t") .. "\n"
        client._offset = 1
        return #data
      end
      function client:receive(n)
        if socket_state.mode == "timeout" then return nil, "timeout" end
        if not client._response or client._offset > #client._response then
          return nil, "closed"
        end
        local chunk = client._response:sub(client._offset, client._offset)
        client._offset = client._offset + 1
        return chunk
      end
      function client:close() client._response = nil end
      return client
    end,
  }
end

local filter = require("mohu_student_gate_filter")
print("MODULE SOURCE:", debug.getinfo(filter.func).source)

local yielded = {}
yield = function(candidate) yielded[#yielded + 1] = candidate end

local function candidate(kind, text, comment)
  return {
    type = kind,
    text = text,
    comment = comment or "",
    get_genuine = function(self) return self end,
  }
end

local function make_env(config, history)
  local cfg = {
    _s = config,
    get_string = function(self, key)
      local v = self._s[key]
      if type(v) == "string" then return v end
      if v == nil then return nil end
      return tostring(v)
    end,
    get_int = function(self, key)
      local v = self._s[key]
      return type(v) == "number" and math.floor(v) or nil
    end,
  }
  local context = {
    get_option = function(_, name)
      if name == "contextual_order" then return true end
      return false
    end,
    commit_history = history and { latest_text = history } or nil,
  }
  return {
    engine = { schema = { config = cfg }, context = context },
    _sg_last_context = nil,
  }
end

local function make_input(cands)
  local state = { i = 0 }
  local function advance()
    state.i = state.i + 1
    return cands[state.i]
  end
  return { iter = function() return advance, state end }
end

local function texts_of(list)
  local out = {}
  for i, c in ipairs(list) do out[i] = c.text end
  return out
end

local BASE_CONFIG = {
  ["tiger/student_gate"] = "true",
  ["tiger/student_gate_socket"] = "/tmp/test-student.sock",
  ["tiger/gate_margin"] = 0.5,
  ["tiger/gate_timeout_ms"] = 30,
}

local HISTORY = "外婆年轻的时候一直住在乡下的小镇"
local CANDS = {
  candidate("table", "家给了"),
  candidate("table", "嫁给了"),
  candidate("table", "夹给了"),
  candidate("punct", "，"),
  candidate("table", "稼给了"),
}

local function reset(config, history)
  yielded = {}
  char_state.invoked = 0
  socket_state.requests = 0
  socket_state.last_payload = nil
  socket_state.mode = "ok"
  local env = make_env(config or BASE_CONFIG, history)
  filter.init(env)
  return env
end

-- A) 引擎拿得定（分差 3σ）：直通且 socket 零调用。
do
  local env = reset(BASE_CONFIG, HISTORY)
  char_state.fn = function(_, _, texts)
    -- 单一明确赢家：z 空间分差 ≈ 2.3σ ≥ 0.5。
    local s = {}
    for i = 1, #texts do s[i] = i == 1 and -1.0 or -3.0 end
    return s
  end
  filter.func(make_input(CANDS), env)
  check("A high margin passthrough",
        texts_of(yielded)[1] == "家给了" and socket_state.requests == 0)
end

-- B) 引擎歧义（top-2 z 分差 <0.5）：socket 被调，前 K 槽按学生分重排。
-- 学生响应 [-2,-1,-3,-4,-5] → z 分对第 2 位最高 → 「嫁给了」升到第一。
do
  local env = reset(BASE_CONFIG, HISTORY)
  char_state.fn = function(_, _, texts)
    -- 前 2 名几乎并列、其余远离：z 空间 top-2 分差 ≈ 0.004 < 0.5。
    local s = {}
    for i = 1, #texts do
      s[i] = (i <= 2) and (-10.0 - 0.001 * i) or (-12.0 - i)
    end
    return s
  end
  filter.func(make_input(CANDS), env)
  local got = texts_of(yielded)
  local asked = socket_state.last_payload or ""
  check("B gate asks student",
        socket_state.requests == 1 and asked:find("SCORE\t") == 1 and
        asked:find(HISTORY, 1, true) ~= nil,
        string.format("req=%d path=%s payload=%s", socket_state.requests,
                      tostring(socket_state.path),
                      tostring(socket_state.last_payload)))
  check("B student rerank top-K",
        got[1] == "嫁给了" and got[2] == "家给了" and got[3] == "夹给了",
        table.concat(got, ","))
  -- punct 槽位保持原位（第 4 位仍是「，」）。
  check("B punct stays", got[4] == "，")
  -- 未参与槽（第 5 位 K 之外）不动。
  check("B beyond-K untouched", got[5] == "稼给了")
end

-- C) 学生超时：fail-open 直通。
do
  local env = reset(BASE_CONFIG, HISTORY)
  char_state.fn = function(_, _, texts)
    local s = {}
    for i = 1, #texts do s[i] = -10.0 - 0.01 * i end
    return s
  end
  socket_state.mode = "timeout"
  filter.func(make_input(CANDS), env)
  check("C timeout fail-open",
        texts_of(yielded)[1] == "家给了" and socket_state.last_payload == nil)
end

-- C2) 服务不在（connect 失败）：同样直通。
do
  local env = reset(BASE_CONFIG, HISTORY)
  char_state.fn = function() return { -10.0, -10.01, -10.02, -10.03, -10.04 } end
  socket_state.mode = "down"
  filter.func(make_input(CANDS), env)
  check("C2 service down fail-open", texts_of(yielded)[1] == "家给了")
end

-- D) 无历史 → 直通。
do
  local env = reset(BASE_CONFIG, nil)
  char_state.fn = function() error("must not be called") end
  filter.func(make_input(CANDS), env)
  check("D no history passthrough",
        #yielded == #CANDS and socket_state.requests == 0)
end

-- E) 缓存：相同 (上文+候选) 第二次不触发 socket。
do
  local env = reset(BASE_CONFIG, HISTORY)
  local flat = function(_, _, texts)
    local s = {}
    for i = 1, #texts do
      s[i] = (i <= 2) and -10.0 or (-12.0 - i)
    end
    return s
  end
  char_state.fn = flat
  filter.func(make_input(CANDS), env)
  local first = texts_of(yielded)
  yielded = {}
  filter.func(make_input(CANDS), env)
  check("E cache hit", socket_state.requests == 1 and
        table.concat(texts_of(yielded), ",") == table.concat(first, ","),
        string.format("req=%d got=%s first=%s", socket_state.requests,
                      table.concat(texts_of(yielded), ","),
                      table.concat(first, ",")))
end

-- F) 学生分平坦（znorm nil → 保持引擎序）。
do
  local env = reset(BASE_CONFIG, HISTORY)
  char_state.fn = function(_, _, texts)
    -- 引擎歧义（top-2 并列）触发门控，学生返回全同分数 → znorm nil。
    local s = {}
    for i = 1, #texts do
      s[i] = (i <= 2) and -10.0 or (-12.0 - i)
    end
    return s
  end
  socket_state.response_scores = { -5.0, -5.0, -5.0, -5.0, -5.0 }
  filter.func(make_input(CANDS), env)
  check("F flat student keeps engine order",
        texts_of(yielded)[1] == "家给了" and socket_state.requests == 1,
        table.concat(texts_of(yielded), ","))
  socket_state.response_scores = { -2.0, -1.0, -3.0, -4.0, -5.0 }
end

-- G) 参与候选不足 2：直通。
do
  local env = reset(BASE_CONFIG, HISTORY)
  local single = { candidate("table", "嫁给了"), candidate("punct", "，") }
  char_state.fn = function() error("must not be called") end
  filter.func(make_input(single), env)
  check("G too few reorderable", #yielded == 2 and socket_state.requests == 0)
end

os.exit(failures == 0 and 0 or 1)
