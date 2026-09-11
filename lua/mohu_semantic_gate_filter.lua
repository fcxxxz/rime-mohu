-- Mohu Semantic Gate Filter (experimental)
-- Copyright (c) 2026 ksqsf
--
-- Ver: 0.1.0
--
-- 语义学生门控重排（与大模型开关 neural_rerank 共用同一个开关）：
--   neural_rerank（大模型开）且 mohu/semantic_rerank/enable 为真时，
--   引擎 V5 上下文字符分 top-2 z 分差小于阈值（歧义菜单）的窗口交给
--   Squirrel 进程内的魔虎语义 C2 scorer（ONNX Runtime）重排。模型加载
--   或打分异常时逐字节直通，不阻塞按键。
--
-- 契约 §7「Client integration precedent and semantic path decision」：
--   1. 共享 neural_rerank 大模型开关（不单独设开关；这是唯一后端）；
--   2. 候选身份严格使用最终菜单 slots/menu_index；服务响应数量必须与
--      shortlist 一致，任何缺失/越界/非法分数整单 fail-open；
--   3. 首选翻转要求语义 z 分领先超过 mohu/semantic_rerank/semantic_margin；
--   4. 单字/简码（⚡️）/置顶（📌）候选为冻结槽位，永不参与重排。
-- 不删候选、不改文本、只重排窗口内顺序。

local ok_tiger, tiger = pcall(require, "mohu_sentence")
if not ok_tiger or type(tiger) ~= "table" then tiger = nil end

local F = {}
-- yield 是 librime-lua 运行时注入的全局，不能提为模块级 upvalue。
local native_sentence_types = {
  mohu_zrm = true,
  mohu_flypy = true,
  mohu_zrm_personal = true,
  mohu_flypy_personal = true,
}

local CACHE_LIMIT = 64

-- ---------------------------------------------------------------- helpers

local function passthrough(input)
  for cand in input:iter() do yield(cand) end
end

local function read_history(env)
  -- 语义路径与大模型开关共用 neural_rerank；上屏历史本身即门控条件，
  -- 不再额外要求 contextual_order（那是 V5 词序特性的开关）。
  local context = env.engine and env.engine.context
  if not (context and context.get_option and
          context:get_option("neural_rerank")) then
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

local function reorderable(env, cand)
  local g = cand.get_genuine and cand:get_genuine() or cand
  local t = g.type
  if t == "punct" or t == "pinned" then return false end
  local text = g.text
  if type(text) ~= "string" or text == "" then return false end
  local len = utf8.len(text)
  if not len or len < 2 then return false end
  local comment = g.comment
  if type(comment) == "string" and comment ~= "" then
    if env._se_quick ~= "" and comment:sub(1, #env._se_quick) == env._se_quick then
      return false
    end
    if env._se_pin ~= "" and comment:sub(1, #env._se_pin) == env._se_pin then
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
  env._se_enabled = config_flag(cfg, "mohu/semantic_rerank/enable", true)
  env._se_margin = config_number(cfg, "mohu/semantic_rerank/semantic_margin", 0.15, 0.0, 100.0)
  env._se_gate_margin = config_number(cfg, "mohu/semantic_rerank/gate_margin", 0.5, 0.0, 100.0)
  env._se_k = math.floor(config_number(cfg, "mohu/semantic_rerank/candidates", 5, 2, 20))
  env._se_limit = math.floor(config_number(cfg, "mohu/semantic_rerank/limit", 20, 2, 50))
  env._se_quick = ""
  env._se_pin = ""
  pcall(function()
    env._se_quick = cfg:get_string("mohu/quick_code_indicator") or "⚡️"
    env._se_pin = cfg:get_string("mohu/pin/indicator") or "📌"
  end)
  env._se_last_context = nil
end

function F.fini(env)
end

function F.func(input, env)
  if not env._se_enabled then
    return passthrough(input)
  end
  -- 与字级神经路径共用 neural_rerank（大模型）开关：开关关闭即直通。
  local context = env.engine and env.engine.context
  if not (context and context.get_option and
          context:get_option("neural_rerank")) then
    return passthrough(input)
  end
  if not (tiger and type(tiger.acquire_char_scorer) == "function") then
    return passthrough(input)
  end
  local history = read_history(env)
  if not history then return passthrough(input) end
  if history ~= env._se_last_context then
    env._se_last_context = history
    cache, cache_order = {}, {}
  end
  local score_fn, handle = tiger.acquire_char_scorer(env)
  if not (score_fn and handle) then return passthrough(input) end

  local advance, state = input:iter()
  local prefix, block = {}, {}
  local seen_first = false
  while #block < env._se_limit do
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
  local function keep_native()
    for _, c in ipairs(prefix) do yield(c) end
    for _, c in ipairs(block) do yield(c) end
    drain()
  end

  local slots, texts = {}, {}
  for i = 1, #block do
    if reorderable(env, block[i]) then
      slots[#slots + 1] = i
      texts[#texts + 1] = block[i].text
    end
  end
  if #slots < 2 then return keep_native() end

  -- 最终候选身份由当前菜单槽位（menu_index）决定。候选跨 librime filter
  -- 边界后 Lua userdata 包装不稳定，不能以对象身份关联上游元数据；V5 分
  -- 按同一 slots 顺序生成，服务响应必须与 shortlist 等长，否则 fail-open。
  local ok, scores = pcall(score_fn, handle, history, texts)
  if not ok or type(scores) ~= "table" or #scores ~= #texts then
    return keep_native()
  end
  local topk = math.min(#scores, env._se_k)
  local ez = znorm({ table.unpack(scores, 1, topk) })
  if not ez then return keep_native() end
  local first, second = -math.huge, -math.huge
  for i = 1, #ez do
    if ez[i] > first then
      second = first
      first = ez[i]
    elseif ez[i] > second then
      second = ez[i]
    end
  end
  if (first - second) >= env._se_gate_margin then
    return keep_native()
  end

  -- 歧义菜单：进程内 C2 scorer 重排前 K 个参与候选。V5 上下文字符分
  -- 与训练特征 score_kind=v5_context_char 同源，按同一 menu_index 顺序传入。
  local k = math.min(env._se_k, #slots)
  local shortlist, native_shortlist = {}, {}
  for i = 1, k do
    shortlist[i] = texts[i]
    native_shortlist[i] = scores[i]
  end
  local key = history .. "\0" .. table.concat(shortlist, "\1")
  local semantic = cache[key]
  if not semantic then
    semantic = tiger.semantic_score(env, history, shortlist, native_shortlist)
    if type(semantic) ~= "table" or #semantic ~= k then semantic = nil end
    if semantic then
      for i = 1, k do
        if type(semantic[i]) ~= "number" or semantic[i] ~= semantic[i] then
          semantic = nil break
        end
      end
      if semantic then cache_remember(key, semantic) end
    end
  end
  if not semantic then return keep_native() end
  local sz = znorm(semantic)
  if not sz then return keep_native() end

  -- 契约差异 3：首选翻转需要语义 z 分领先超过 margin。
  local semantic_first = 1
  for i = 2, k do
    if sz[i] > sz[semantic_first] then semantic_first = i end
  end
  local allow_first_flip = semantic_first ~= 1 and
    (sz[semantic_first] - sz[1]) >= env._se_margin

  -- 第 i 个输出槽位放第 i 名语义候选；平分保持引擎原序。
  -- 若首选翻转未被 margin 允许，钉住原首位只允许低位重排。
  local active = {}
  for i = 1, k do active[i] = { pos = i, s = sz[i] } end
  if not allow_first_flip then
    active[1].s = math.huge
  end
  table.sort(active, function(a, b)
    if a.s ~= b.s then return a.s > b.s end
    return a.pos < b.pos
  end)
  local ordered = {}
  for i = 1, #block do ordered[i] = block[i] end
  for i = 1, k do
    ordered[slots[i]] = block[slots[active[i].pos]]
  end

  for _, c in ipairs(prefix) do yield(c) end
  for _, c in ipairs(ordered) do yield(c) end
  drain()
end

return F

-- Local Variables:
-- lua-indent-level: 4
-- End:
