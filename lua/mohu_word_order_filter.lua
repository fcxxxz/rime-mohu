-- Mohu Word Order Filter
-- Copyright (c) 2026 ksqsf
--
-- Ver: 0.1.0
--
-- This file is part of Project Mohu
-- Licensed under GPLv3
--
-- 跨候选调频·词级重排：contextual_order 开启且上屏历史含汉字时，用引擎
-- 对菜单前 N 个 smart 候选批量取上下文续写分，按「模型分 − rank_penalty ×
-- (名次−1)」融合后稳定重排——词频权威保留，模型只在上下文条件下提升
-- 续写概率更高的候选（重排，不顶替；−7.3pp 接管实验的教训）。
--
-- 评分信号（tiger/word_order_signal）：
--   char（默认）＝ 字符续写裸分 Σ logP(候选字|上文末 2 字)，octagram 同型
--     机制；用主字符模型，无词层依赖、无 OOV 概念，延迟与内存零增量。
--     离线实测修好率约为 word 信号的 3 倍（40.3% vs 13.2% @修反 ≤1%）。
--   word ＝ 词级分 logP(词|上文末 2 词)（需容器词层或显式
--     word_scorer_model；OOV −20 无信号不参与重排）。
--
-- 稳定边界：punct / pinned / native 引擎候选（已带上下文，避免双重计分）
-- / 简码与固顶标记（⚡️/📌）/ 单字候选一律不参与重排，保持原位。只重排
-- 顺序：不删候选、不改文本、不新建候选。
--
-- 可用性：旧 dylib、引擎未就绪、评分出错 → 逐字节直通；评分出错后本
-- env 内不再重试。配置：tiger/word_order（默认开）、
-- tiger/word_order_candidates（默认 20）、tiger/word_order_rank_penalty
-- （默认 1.0，离线网格：0.95 时修好 45.7%/修反 1.5%，1.4 时 40.3%/1.0%）、
-- tiger/word_order_scan_budget（收集阶段总拉取上限，默认
-- word_order_candidates×3：block 只数可重排候选，流前部全是单字时
-- block 永不凑齐、循环会抽干整条流，实测补全流下单键近万候选、
-- 数百毫秒；预算耗尽即以已收集的 block 继续重排，其余流式直通）。
--
-- 挂接：mohu_*.schema.yaml filters 列表，mohu_reorder_filter 之后、
-- candidate_override 之前（用户显式覆盖优先于模型重排）。

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

local function passthrough(input)
  for cand in input:iter() do yield(cand) end
end

-- 上屏历史：contextual_order 开启且 commit_history 尾部含汉字才重排。
-- 无闭包版本：热路径上少一次闭包分配与整段 pcall。
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
  local value
  local ok_int, n = pcall(cfg.get_int, cfg, key)
  if ok_int and type(n) == "number" then value = n end
  if value == nil then
    local ok_str, s = pcall(cfg.get_string, cfg, key)
    if ok_str then value = tonumber(s) end
  end
  if type(value) ~= "number" or value ~= value then return default end
  return math.min(hi, math.max(lo, value))
end

function F.init(env)
  local cfg = env.engine.schema.config
  env._wo_enabled = config_flag(cfg, "tiger/word_order", true)
  env._wo_limit = math.floor(config_number(cfg, "tiger/word_order_candidates", 20, 2, 50))
  env._wo_penalty = config_number(cfg, "tiger/word_order_rank_penalty", 1.0, 0.0, 100.0)
  env._wo_scan_budget = math.floor(config_number(cfg, "tiger/word_order_scan_budget",
    env._wo_limit * 3, env._wo_limit, 1000))
  -- 评分信号：char = 字符续写分（octagram 同型，默认；用主字符模型，
  -- 无词层/无 OOV 概念，实测修好率约为词信号 3 倍）；word = 词级分
  -- （需容器词层或显式 word_scorer_model，OOV −20 无信号不参与）。
  env._wo_signal = "char"
  pcall(function()
    local s = cfg:get_string("tiger/word_order_signal") or ""
    if s == "word" then env._wo_signal = "word" end
  end)
  env._wo_quick = ""
  env._wo_pin = ""
  pcall(function()
    env._wo_quick = cfg:get_string("mohu/quick_code_indicator") or "⚡️"
    env._wo_pin = cfg:get_string("mohu/pin/indicator") or "📌"
  end)
end

function F.fini(env)
end

-- 候选是否参与重排：多字、非 punct/pinned/native、无简码/固顶标记。
local function reorderable(env, cand)
  local g = cand.get_genuine and cand:get_genuine() or cand
  local t = g.type
  if t == "punct" or t == "pinned" then return false end
  if native_sentence_types[t] then return false end
  local text = g.text
  if type(text) ~= "string" or text == "" then return false end
  local len = utf8.len(text)
  if not len or len < 2 then return false end
  local comment = g.comment
  if type(comment) == "string" and comment ~= "" then
    if env._wo_quick ~= "" and comment:sub(1, #env._wo_quick) == env._wo_quick then
      return false
    end
    if env._wo_pin ~= "" and comment:sub(1, #env._wo_pin) == env._wo_pin then
      return false
    end
  end
  return true
end

function F.func(input, env)
  if env._wo_dead then return passthrough(input) end
  if not env._wo_enabled then return passthrough(input) end
  if not tiger or type(tiger.acquire_word_scorer) ~= "function" then
    return passthrough(input)
  end
  local history = read_history(env)
  if not history then return passthrough(input) end
  if not tiger or type(tiger.acquire_word_scorer) ~= "function" then
    return passthrough(input)
  end
  local score_fn, handle
  if env._wo_signal == "word" then
    score_fn, handle = tiger.acquire_word_scorer(env)
  else
    if type(tiger.acquire_char_scorer) ~= "function" then
      return passthrough(input)
    end
    score_fn, handle = tiger.acquire_char_scorer(env)
  end
  if not (score_fn and handle) then return passthrough(input) end

  local advance, state = input:iter()
  local prefix, block = {}, {}
  local seen_first = false
  -- 只收集到限额为止，其余流式直通（不占内存）。
  -- 扫描预算封顶"为一口气凑 block 而连续拉取"的总量：单字等不可重排
  -- 候选不数入 block，流前部若全是这类候选，block 永不凑齐，循环会把
  -- 整条流抽干（补全流下即逐键数百毫秒卡顿的形态）。预算耗尽后以已
  -- 收集的 block 照常重排（不足 2 个走直通），其余交给 drain() 流式输出。
  local scans = 0
  while #block < env._wo_limit do
    if scans >= env._wo_scan_budget then break end
    scans = scans + 1
    local cand = advance(state)
    if cand == nil then break end
    if not seen_first and reorderable(env, cand) then seen_first = true end
    if seen_first then
      block[#block + 1] = cand
    else
      prefix[#prefix + 1] = cand
    end
  end
  -- 收集块之后的剩余候选：流式直通。
  local function drain()
    for cand in function() return advance(state) end do yield(cand) end
  end
  -- 前缀（首个可重排候选之前的稳定候选）始终原样输出。
  local function yield_prefix()
    for _, c in ipairs(prefix) do yield(c) end
  end

  -- 找出块内参与重排的槽位与文本。
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
    -- 评分失败：直通并停用（可用性：不每键重试报错）。
    env._wo_dead = true
    yield_prefix()
    for _, c in ipairs(block) do yield(c) end
    drain()
    return
  end

  -- 融合：F_k = 模型分 − rank_penalty × (原名次−1)。候选要越过前面的
  -- 候选，模型分优势必须超过 rank_penalty × 距离——这就是修反保护阈
  -- 值。word 信号的 OOV（-20 无信号）不参与、保持原位；char 信号是
  -- 续写累加分（可为任意负值），无 OOV，全员参与。
  local penalty = env._wo_penalty
  local word_signal = env._wo_signal == "word"
  local active = {}  -- 参与重排的槽位（slots 内序号，升序）＋融合分
  for k = 1, #slots do
    local s = tonumber(scores[k])
    if type(s) == "number" and s == s and (not word_signal or s > -19.9) then
      active[#active + 1] = { pos = k, s = s - penalty * (k - 1) }
    end
  end
  if #active >= 2 then
    local ranked = {}
    for k = 1, #active do ranked[k] = active[k] end
    table.sort(ranked, function(a, b)
      if a.s ~= b.s then return a.s > b.s end
      return a.pos < b.pos  -- 平分稳定：保持词频原序
    end)
    -- 第 k 名写回第 k 个参与槽位（active 升序即这些槽位的新占用序）；
    -- OOV 等未参与槽位保持原候选，不删除、不复制。
    local ordered = {}
    for i = 1, #block do ordered[i] = block[i] end
    for k = 1, #ranked do
      ordered[slots[active[k].pos]] = block[slots[ranked[k].pos]]
    end
    block = ordered
  end

  yield_prefix()
  for _, c in ipairs(block) do yield(c) end
  drain()
end

return F

-- Local Variables:
-- lua-indent-level: 4
-- End:
