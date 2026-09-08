-- Mohu Student Gate Filter (experimental)
-- Copyright (c) 2026 ksqsf
--
-- Ver: 0.0.1
--
-- This file is part of Project Mohu
-- Licensed under GPLv3
--
-- 门控异步学生重排：引擎拿得定的菜单直接放行（零额外开销），只有引擎
-- top-2 归一化分差小于阈值（tiger/gate_margin，默认 0.5）的「歧义菜单」
-- 才请求本地 ONNX 学生服务重排前 K 个候选（tiger/gate_candidates，默认
-- 5）。实验依据（docs/reports/2026-09-07-tiny-char-scorer.md 融合一节）：
-- 仲裁形态在 curated 保持 95.65% 不丢、random/top 拿走大部分融合收益，
-- 且只有 4–22% 的菜单需要调用模型。
--
-- 打分协议（student_scorer.py，tab 分隔行协议）：
--   请求 "SCORE\t上文\t候选1\t候选2...\n" → 响应 "OK\ts1\ts2...\n"
-- 两侧分数各自在候选内 z 归一后比较；引擎分来自 acquire_char_scorer
-- （与 word_order char 信号同源，0.012ms/批）。
--
-- 可用性：服务不在/超时/返回异常 → 逐字节直通，不阻塞按键（超时预算
-- tiger/gate_timeout_ms，默认 30ms；学生 5 候选实测 ~7ms）。请求结果按
-- (上文+候选) 缓存 64 条，上文变化即清空。
--
-- 稳定边界与挂接位置同 mohu_word_order_filter：punct/pinned/native/单字/
-- ⚡️📌 不参与；不删候选、不改文本、只重排顺序。默认关闭，schema 显式
-- 开启：tiger/student_gate: true + tiger/student_gate_socket: <路径>。

local ok_tiger, tiger = pcall(require, "mohu_sentence")
if not ok_tiger or type(tiger) ~= "table" then tiger = nil end

local F = {}
-- 注意：yield 是 librime-lua 运行时注入的全局，不能在模块加载期提为
-- upvalue（会捕获 nil）；保持函数内直接引用（与 mohu_reorder_filter 一致）。

local native_sentence_types = {
  mohu_zrm = true,
  mohu_flypy = true,
  mohu_zrm_personal = true,
  mohu_flypy_personal = true,
}

local CACHE_LIMIT = 64
local MAX_LINE_BYTES = 65536

-- ---------------------------------------------------------------- path

-- 嵌入式 Rime Lua 的 package.path 不含 rocks 树；把用户目录与
-- /opt/homebrew 的 Lua 5.4 路径补进来，luasocket 装在任一处即可用。
local function inject_lua_module_paths()
  if not package then return end
  local version = type(_VERSION) == "string" and
    (_VERSION:match("Lua (%d+%.%d+)") or "5.4") or "5.4"
  local home = ""
  if os and type(os.getenv) == "function" then
    local ok, value = pcall(os.getenv, "HOME")
    if ok and type(value) == "string" then home = value end
  end
  local user_dir = ""
  if rime_api and type(rime_api.get_user_data_dir) == "function" then
    local ok, value = pcall(rime_api.get_user_data_dir)
    if ok and type(value) == "string" then user_dir = value end
  end
  local lua_paths = {
    user_dir ~= "" and user_dir .. "/lua/?.lua" or nil,
    user_dir ~= "" and user_dir .. "/lua/rocks/share/lua/" .. version .. "/?.lua" or nil,
    home ~= "" and home .. "/.luarocks/share/lua/" .. version .. "/?.lua" or nil,
    "/opt/homebrew/share/lua/" .. version .. "/?.lua",
  }
  local c_paths = {
    user_dir ~= "" and user_dir .. "/lua/rocks/lib/lua/" .. version .. "/?.so" or nil,
    home ~= "" and home .. "/.luarocks/lib/lua/" .. version .. "/?.so" or nil,
    "/opt/homebrew/lib/lua/" .. version .. "/?.so",
  }
  local function append(field, values)
    local current = package[field] or ""
    for _, value in ipairs(values) do
      if value and not current:find(value, 1, true) then
        current = current .. ";" .. value
      end
    end
    package[field] = current
  end
  append("path", lua_paths)
  append("cpath", c_paths)
end

inject_lua_module_paths()

-- ---------------------------------------------------------------- socket

local socket_module = false  -- false=未探测, nil=不可用
local socket_clock = false
local clients = {}

local function load_socket_module()
  if socket_module ~= false then return socket_module end
  socket_module = nil
  -- socket.unix 模块本身就是构造器；完整 socket 模块的构造器在 .unix。
  local ok, unix = pcall(require, "socket.unix")
  if ok then
    if type(unix) == "function" then
      socket_module = unix
      return unix
    end
    if type(unix) == "table" and type(unix.stream) == "function" then
      socket_module = unix.stream
      return unix.stream
    end
    if type(unix) == "table" and type(unix.tcp) == "function" then
      socket_module = unix.tcp
      return unix.tcp
    end
  end
  local ok2, sock = pcall(require, "socket")
  if ok2 and type(sock) == "table" and type(sock.unix) == "function" then
    socket_module = sock.unix
    return sock.unix
  end
  return nil
end

-- 统一返回毫秒时钟；不可用时退 nil（调用方即退化为单一超时档）。
local function load_clock()
  if socket_clock ~= false then return socket_clock end
  socket_clock = nil
  if rime_api and type(rime_api.get_time_ms) == "function" then
    local ok, value = pcall(rime_api.get_time_ms)
    if ok and type(value) == "number" then
      socket_clock = function()
        local fine, now = pcall(rime_api.get_time_ms)
        return fine and type(now) == "number" and now or nil
      end
      return socket_clock
    end
  end
  local ok, sock = pcall(require, "socket")
  if ok and type(sock.gettime) == "function" then
    socket_clock = function()
      local fine, now = pcall(sock.gettime)
      return fine and type(now) == "number" and now * 1000 or nil
    end
  end
  return socket_clock
end

local function client_connect(path, timeout_seconds)
  local module = load_socket_module()
  if not module then return nil end
  local ok, client = pcall(module)
  -- luasocket 的构造器返回 userdata（5.4 编译版），不是 table。
  if not ok or (type(client) ~= "table" and type(client) ~= "userdata") then
    return nil
  end
  local ok_set = pcall(client.settimeout, client, timeout_seconds)
  if not ok_set then return nil end
  local ok_conn = pcall(client.connect, client, path)
  if not ok_conn then return nil end
  clients[path] = client
  return client
end

local function client_close(path)
  local client = clients[path]
  clients[path] = nil
  if type(client) == "table" and type(client.close) == "function" then
    pcall(client.close, client)
  end
end

-- 单次行请求：deadline 控制下 send+逐字节 receive；失败关闭连接。
-- 返回响应行（不含换行）的 tab 分割字段表，或 nil。
local function socket_request(path, payload, timeout_ms)
  local timeout = (tonumber(timeout_ms) or 30) / 1000
  local clock = load_clock()
  local started = clock and clock() or nil
  local deadline_ms = started and (started + (tonumber(timeout_ms) or 30)) or nil
  local message = payload .. "\n"
  for _ = 1, 2 do
    local client = clients[path] or client_connect(path, timeout)
    if client then
      local failed = false
      local ok_send, sent = pcall(client.send, client, message)
      if not ok_send or sent ~= #message then
        failed = true
      end
      local parts, total = {}, 0
      while not failed do
        if deadline_ms and clock then
          local remaining_ms = deadline_ms - clock()
          if remaining_ms <= 0 then failed = true break end
          pcall(client.settimeout, client, remaining_ms / 1000)
        else
          pcall(client.settimeout, client, timeout)
        end
        local ok_recv, chunk = pcall(client.receive, client, 1)
        if not ok_recv or type(chunk) ~= "string" or #chunk == 0 then
          failed = true break
        end
        if chunk == "\n" then
          local line = table.concat(parts)
          if line:sub(-1) == "\r" then line = line:sub(1, -2) end
          local fields = {}
          local rest, pos = line, 1
          while true do
            local tab = rest:find("\t", pos, true)
            if not tab then
              fields[#fields + 1] = rest:sub(pos)
              break
            end
            fields[#fields + 1] = rest:sub(pos, tab - 1)
            pos = tab + 1
          end
          return fields
        end
        total = total + 1
        if total > MAX_LINE_BYTES then failed = true break end
        parts[#parts + 1] = chunk
      end
      if failed then client_close(path) end
    end
  end
  return nil
end

-- ---------------------------------------------------------------- helpers

local function passthrough(input)
  for cand in input:iter() do yield(cand) end
end

local function read_history(env)
  local context = env.engine and env.engine.context
  if not (context and context.get_option and
          context:get_option("contextual_order")) then
    return nil
  end
  local hist = context.commit_history
  if not hist then return nil end
  local text
  if type(hist.latest_text) == "function" then
    local ok, value = pcall(hist.latest_text, hist)
    if ok then text = value end
  elseif type(hist.latest_text) == "string" then
    text = hist.latest_text
  end
  if type(text) == "string" and text ~= "" and
      text:find("[\228-\233]") ~= nil then
    return text
  end
  return nil
end

local function config_flag(cfg, key, default)
  local ok, value = pcall(cfg.get_string, cfg, key)
  if ok and (value == "true" or value == "1") then return true end
  if ok and (value == "false" or value == "0") then return false end
  local ok_int, n = pcall(cfg.get_int, cfg, key)
  if ok_int and type(n) == "number" then return n ~= 0 end
  return default
end

local function config_number(cfg, key, default, lo, hi)
  -- 浮点参数（如 gate_margin=0.5）必须先走字符串路径：librime 的 get_int
  -- 会把 0.5 截断成 0，导致阈值失真。
  local value
  local ok_str, s = pcall(cfg.get_string, cfg, key)
  if ok_str and type(s) == "string" then value = tonumber(s) end
  if type(value) ~= "number" or value ~= value then
    local ok_int, n = pcall(cfg.get_int, cfg, key)
    if ok_int and type(n) == "number" then value = n end
  end
  if type(value) ~= "number" or value ~= value then return default end
  return math.min(hi, math.max(lo, value))
end

local function config_string(cfg, key, default)
  local ok, value = pcall(cfg.get_string, cfg, key)
  if ok and type(value) == "string" and value ~= "" then return value end
  return default
end

local function reorderable(env, cand)
  local g = cand.get_genuine and cand:get_genuine() or cand
  local t = g.type
  if t == "punct" or t == "pinned" then return false end
  -- native 句子候选参与重排：引擎解码器有自己的上下文评分，但当
  -- 门控判定引擎拿不准时，学生模型的判断应能覆盖它。「家给了」vs
  -- 「嫁给了」正是这种场景——引擎不确定选哪个，学生有完整语境。
  local text = g.text
  if type(text) ~= "string" or text == "" then return false end
  local len = utf8.len(text)
  if not len or len < 2 then return false end
  local comment = g.comment
  if type(comment) == "string" and comment ~= "" then
    if env._sg_quick ~= "" and comment:sub(1, #env._sg_quick) == env._sg_quick then
      return false
    end
    if env._sg_pin ~= "" and comment:sub(1, #env._sg_pin) == env._sg_pin then
      return false
    end
  end
  return true
end

local function znorm(values)
  local n = #values
  if n < 2 then return nil end
  local mean = 0
  for i = 1, n do mean = mean + values[i] end
  mean = mean / n
  local var = 0
  for i = 1, n do var = var + (values[i] - mean) ^ 2 end
  local std = math.sqrt(var / n)
  if not (std > 0) then return nil end
  local out = {}
  for i = 1, n do out[i] = (values[i] - mean) / std end
  return out
end

-- 缓存：key -> 学生分表；LRU 淘汰；上文变化清空。
local cache, cache_order = {}, {}

local function cache_remember(key, scores)
  cache[key] = scores
  cache_order[#cache_order + 1] = key
  while #cache_order > CACHE_LIMIT do
    cache[cache_order[1]] = nil
    table.remove(cache_order, 1)
  end
end

function F.init(env)
  local cfg = env.engine.schema.config
  env._sg_enabled = config_flag(cfg, "tiger/student_gate", false)
  env._sg_socket = config_string(cfg, "tiger/student_gate_socket", "")
  env._sg_margin = config_number(cfg, "tiger/gate_margin", 0.5, 0.0, 100.0)
  env._sg_timeout = math.floor(config_number(cfg, "tiger/gate_timeout_ms", 30, 1, 1000))
  env._sg_k = math.floor(config_number(cfg, "tiger/gate_candidates", 5, 2, 20))
  env._sg_limit = math.floor(config_number(cfg, "tiger/student_gate_candidates", 20, 2, 50))
  env._sg_quick = ""
  env._sg_pin = ""
  pcall(function()
    env._sg_quick = cfg:get_string("mohu/quick_code_indicator") or "⚡️"
    env._sg_pin = cfg:get_string("mohu/pin/indicator") or "📌"
  end)
  env._sg_last_context = nil
end

function F.fini(env)
end

function F.func(input, env)
  if not env._sg_enabled or env._sg_socket == "" then
    return passthrough(input)
  end
  if not tiger or type(tiger.acquire_char_scorer) ~= "function" then
    return passthrough(input)
  end
  local history = read_history(env)
  if not history then return passthrough(input) end
  if history ~= env._sg_last_context then
    env._sg_last_context = history
    cache, cache_order = {}, {}
  end
  local score_fn, handle = tiger.acquire_char_scorer(env)
  if not (score_fn and handle) then return passthrough(input) end

  local advance, state = input:iter()
  local prefix, block = {}, {}
  local seen_first = false
  while #block < env._sg_limit do
    local cand = advance(state)
    if cand == nil then break end
    if not seen_first and reorderable(env, cand) then seen_first = true end
    if seen_first then
      block[#block + 1] = cand
    else
      prefix[#prefix + 1] = cand
    end
  end
  local function drain()
    for cand in function() return advance(state) end do yield(cand) end
  end
  local function yield_prefix()
    for _, c in ipairs(prefix) do yield(c) end
  end

  local slots, texts = {}, {}
  for i = 1, #block do
    if reorderable(env, block[i]) then
      slots[#slots + 1] = i
      texts[#texts + 1] = block[i].text
    end
  end
  if #slots < 2 then
    yield_prefix()
    for _, c in ipairs(block) do yield(c) end
    drain()
    return
  end

  local ok, scores = pcall(score_fn, handle, history, texts)
  if not ok or type(scores) ~= "table" or #scores ~= #texts then
    -- 引擎评分失败：直通（评分器与 word_order 共用，不再另设停用标记）。
    yield_prefix()
    for _, c in ipairs(block) do yield(c) end
    drain()
    return
  end
  -- z 归一仅取前 K 个候选（冷僻候选会拉大 std 压扁 top-2 差距，
  -- 导致门控永远不触发；只看真正有竞争关系的头部）。
  local topk = math.min(#scores, env._sg_k)
  local ez = znorm({table.unpack(scores, 1, topk)})
  if not ez then
    yield_prefix()
    for _, c in ipairs(block) do yield(c) end
    drain()
    return
  end
  local first, second = -math.huge, -math.huge
  for i = 1, #ez do
    if ez[i] > first then
      second = first
      first = ez[i]
    elseif ez[i] > second then
      second = ez[i]
    end
  end
  -- 引擎拿得定：直接采用引擎排序，零额外开销（绝大多数菜单走这里）。
  if (first - second) >= env._sg_margin then
    yield_prefix()
    for _, c in ipairs(block) do yield(c) end
    drain()
    return
  end

  -- 歧义菜单：请求学生重排前 K 个参与候选。
  local k = math.min(env._sg_k, #slots)
  local shortlist = {}
  for i = 1, k do shortlist[i] = texts[i] end
  local key = history .. "\0" .. table.concat(shortlist, "\1")
  local student = cache[key]
  if not student then
    local payload = "SCORE\t" .. history
    for i = 1, k do payload = payload .. "\t" .. shortlist[i] end
    local fields = socket_request(env._sg_socket, payload, env._sg_timeout)
    if fields and fields[1] == "OK" and #fields == k + 1 then
      student = {}
      for i = 1, k do student[i] = tonumber(fields[i + 1]) end
      for i = 1, k do
        if type(student[i]) ~= "number" or student[i] ~= student[i] then
          student = nil break
        end
      end
      if student then cache_remember(key, student) end
    end
  end
  if not student then
    -- 学生不可用：保持引擎排序（fail-open）。
    yield_prefix()
    for _, c in ipairs(block) do yield(c) end
    drain()
    return
  end
  local sz = znorm(student)
  if not sz then
    yield_prefix()
    for _, c in ipairs(block) do yield(c) end
    drain()
    return
  end

  -- 前 K 个槽位按学生 z 分稳定重排（平分保持引擎原序）；其余槽位不动。
  local active = {}
  for i = 1, k do active[i] = { pos = i, s = sz[i] } end
  table.sort(active, function(a, b)
    if a.s ~= b.s then return a.s > b.s end
    return a.pos < b.pos
  end)
  local ordered = {}
  for i = 1, #block do ordered[i] = block[i] end
  for i = 1, k do
    ordered[slots[active[i].pos]] = block[slots[i]]
  end

  yield_prefix()
  for _, c in ipairs(ordered) do yield(c) end
  drain()
end

return F

-- Local Variables:
-- lua-indent-level: 4
-- End:
