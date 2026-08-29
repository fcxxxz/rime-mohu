-- 魔虎整句（mohu_tiger_sentence）——Lua 薄壳
-- 解码与语言模型全部在 libtigerengine.dylib（C 引擎）中，
-- 本文件负责原生候选输出与可选神经重排；不主动改写或提交 Context.input。
-- 移植自 TigerClaw 虎整句 Rime 版 tiger_sentence.lua（MIT 未声明，自用）。
--
-- schema 引用：
--   lua_translator@*mohu_tiger_sentence*translator
-- 配置（schema 内 tiger/ 节，均可省略）：
--   tiger/engine_lib: 引擎 dylib 路径（默认 <用户目录>/mohu_llm/runtime/libtigerengine.dylib）
--   tiger/model:      模型路径（默认 <用户目录>/mohu_llm/data/sentence-ngram-mobile.bin）
--   tiger/scheme:      双拼方案标识（zrm 或 flypy）
--   tiger/candidate_type: native 候选类型（默认按 scheme 推导）
--   tiger/lexicon:    码表路径（默认由 runtime resolver 提供）
--   tiger/beam:       束宽（默认 200）
--   tiger/all_ranks:  >4 键时是否允许全部档位竞争（默认 true）
--   tiger/initial_quality: 原生候选质量（默认 50）
--   tiger/rerank_socket: 可选本地 JSONL Unix socket
--   tiger/rerank_http_endpoint: 仅供测试注入，生产路径禁用
--   tiger/rerank_timeout_ms: five-row scorer deadline（默认 45）
--   tiger/rerank_full_timeout_ms: adaptive >5-row deadline（默认 140）

local M = {}
local runtime = require("mohu_llm_runtime")

-- Neural reranking is optional.  Keep the native sentence decoder usable on
-- installations that do not deploy the companion Lua module/profile.
local reranker
do
  local ok, loaded = pcall(require, "mohu_tiger_reranker")
  if ok and type(loaded) == "table" then
    reranker = loaded
  else
    reranker = {
      init = function() end,
      fini = function() end,
      clear_cache = function() end,
      neural_enabled = function() return false end,
      rerank = function() return nil end,
    }
  end
end

local beam_width = 200
local candidate_limit = 20
local candidate_quality = 50

local function finite_number(value)
  return type(value) == "number" and value == value and
    value ~= math.huge and value ~= -math.huge
end

local function utf8_length(text)
  if type(text) ~= "string" then return nil end
  local count = 0
  local index = 1
  while index <= #text do
    local byte = text:byte(index)
    local width
    if byte < 0x80 then
      width = 1
    elseif byte >= 0xC2 and byte <= 0xDF then
      width = 2
    elseif byte >= 0xE0 and byte <= 0xEF then
      width = 3
    elseif byte >= 0xF0 and byte <= 0xF4 then
      width = 4
    else
      return nil
    end
    if index + width - 1 > #text then return nil end
    for offset = 1, width - 1 do
      local continuation = text:byte(index + offset)
      if continuation < 0x80 or continuation > 0xBF then return nil end
    end
    if width == 3 then
      local b2 = text:byte(index + 1)
      if byte == 0xE0 and b2 < 0xA0 then return nil end
      if byte == 0xED and b2 > 0x9F then return nil end
    elseif width == 4 then
      local b2 = text:byte(index + 1)
      if byte == 0xF0 and b2 < 0x90 then return nil end
      if byte == 0xF4 and b2 > 0x8F then return nil end
    end
    count = count + 1
    index = index + width
  end
  return count
end

-- ---------------------------------------------------------------- 引擎装载

local engine_handle = nil
local engine_error = nil
local engine_error_logged = false
local decode_output_error_logged = false
local tigerengine = nil
local decode_ms = 0
local engine_references = 0
local engine_signature = nil
local engine_config_error_logged = false

local function report_engine_error(message)
  engine_error = message
  if not engine_error_logged then
    engine_error_logged = true
    if log and log.error then
      log.error("mohu_tiger_sentence: " .. message)
    end
  end
end

local function report_decode_output_error(message)
  if decode_output_error_logged then return end
  decode_output_error_logged = true
  if log and log.error then
    pcall(log.error, "mohu_tiger_sentence: " .. message)
  end
end

local function config_string(cfg, key)
  if not cfg or type(cfg.get_string) ~= "function" then return nil end
  local ok, value = pcall(cfg.get_string, cfg, key)
  if ok and type(value) == "string" and value ~= "" then return value end
  return nil
end

local function resolve_runtime_path(value, paths)
  if type(value) ~= "string" or value == "" then return value end
  if value:sub(1, 1) == "/" then return value end
  local root = paths and paths.root
  if not root then return value end
  if value == "mohu_llm" or value:sub(1, #"mohu_llm/") == "mohu_llm/" then
    return root .. value:sub(#"mohu_llm" + 1)
  end
  return value
end

local function configure(env)
  local cfg = env and env.engine and env.engine.schema and env.engine.schema.config
  local scheme = config_string(cfg, "tiger/scheme") or "zrm"
  if scheme ~= "zrm" and scheme ~= "flypy" then scheme = "zrm" end
  local candidate_type = config_string(cfg, "tiger/candidate_type")
  if candidate_type ~= "mohu_llm_zrm" and candidate_type ~= "mohu_llm_flypy" then
    candidate_type = "mohu_llm_" .. scheme
  end
  if env then
    env._tiger_scheme = scheme
    env._tiger_candidate_type = candidate_type
  end
  if cfg then
    local quality
    local ok, value = pcall(cfg.get_int, cfg, "tiger/initial_quality")
    if ok then quality = value end
    if quality == nil then
      local string_ok, string_value = pcall(cfg.get_string, cfg, "tiger/initial_quality")
      if string_ok then quality = tonumber(string_value) end
    end
    if finite_number(quality) then candidate_quality = quality else candidate_quality = 50 end
  end
  return cfg
end

local function ensure_engine(env)
  local cfg = configure(env)
  local function conf(name)
    if cfg then
      local ok, value = pcall(cfg.get_string, cfg, "tiger/" .. name)
      if ok and value and value ~= "" then return value end
    end
    return nil
  end
  local paths = runtime.paths()
  local lib = resolve_runtime_path(conf("engine_lib"), paths) or paths.engine
  local model = resolve_runtime_path(conf("model"), paths) or paths.ngram
  local lexicon = resolve_runtime_path(conf("lexicon"), paths) or
    (runtime.lexicon and runtime.lexicon(env and env._tiger_scheme)) or paths.lexicons.zrm
  local beam = conf("beam")
  local all_ranks = conf("all_ranks")
  local beam_value = beam and tonumber(beam) or beam_width
  if not finite_number(beam_value) or beam_value ~= math.floor(beam_value) or
    beam_value < -2147483648 or beam_value > 2147483647 then
    report_engine_error("invalid native beam width")
    return nil
  end
  local all_ranks_value =
    (all_ranks == nil or all_ranks == "true" or all_ranks == "1") and 1 or 0
  local signature = table.concat({ lib, model, lexicon, beam_value, all_ranks_value }, "\28")

  if engine_handle ~= nil then
    if engine_signature == signature then return engine_handle end
    if not engine_config_error_logged then
      engine_config_error_logged = true
      if log and log.error then
        pcall(log.error, "mohu_tiger_sentence: native engine configuration changed; reload required")
      end
    end
    return nil
  end
  if engine_error then return nil end

  local load_ok, loader, err = pcall(package.loadlib, lib, "luaopen_tigerengine")
  if not load_ok then
    report_engine_error("loadlib failed: " .. tostring(loader))
    return nil
  end
  if not loader then
    report_engine_error("loadlib " .. lib .. ": " .. tostring(err))
    return nil
  end
  local ok, t = pcall(loader)
  if not ok then
    report_engine_error("luaopen failed: " .. tostring(t))
    return nil
  end
  if type(t) ~= "table" or type(t.create) ~= "function" then
    report_engine_error("luaopen returned an invalid engine module")
    return nil
  end
  tigerengine = t
  local create_ok, h, e = pcall(t.create, model, lexicon, beam_value, all_ranks_value)
  if not create_ok then
    report_engine_error("engine create failed: " .. tostring(h))
    return nil
  end
  if not h then
    report_engine_error("engine create: " .. tostring(e))
    return nil
  end
  engine_handle = h
  engine_signature = signature
  return h
end

local function retain_engine(env)
  local handle = ensure_engine(env)
  if handle ~= nil and env and not env._tiger_engine_reference then
    env._tiger_engine_reference = true
    engine_references = engine_references + 1
  end
  if env then env._tiger_engine_ready = handle ~= nil end
  return handle
end

local function release_engine(env)
  if env and env._tiger_engine_reference then
    env._tiger_engine_reference = false
    engine_references = math.max(0, engine_references - 1)
  end
  if env then env._tiger_engine_ready = false end
  if engine_references > 0 then return end

  if engine_handle ~= nil and tigerengine and type(tigerengine.free) == "function" then
    pcall(tigerengine.free, engine_handle)
  end
  engine_handle = nil
  tigerengine = nil
  engine_error = nil
  engine_error_logged = false
  decode_output_error_logged = false
  decode_ms = 0
  engine_signature = nil
  engine_config_error_logged = false
end

-- 解析 C 输出协议：
--   首行 flags: truncated early_truncated uses_incomplete prefers_incomplete n_final n_early
--        consensus_complete consensus_text_bytes consensus_raw_length visible_consensus
--   候选行: text \t segmented \t score \t confidence \t max_rank \t pathmap
local function parse_output(out)
  local result = { items = {}, early = {} }
  local first = true
  for line in out:gmatch("([^\n]*)\n") do
    if first then
      local t, et, ui, pi, n_final, n_early, cc, cb, cr, vc =
        line:match("^(%d+) (%d+) (%d+) (%d+) (%d+) (%d+) (%d+) (%d+) (%d+) (%d+)$")
      if not t then
        t, et, ui, pi, n_final, n_early, cc, cb, cr =
          line:match("^(%d+) (%d+) (%d+) (%d+) (%d+) (%d+) (%d+) (%d+) (%d+)$")
      end
      if not t then
        -- Accept the pre-summary six-field header from older test/deployed
        -- engines, but never treat it as a complete consensus proof.
        t, et, ui, pi, n_final, n_early =
          line:match("^(%d+) (%d+) (%d+) (%d+) (%d+) (%d+)$")
      end
      if not t or (t ~= "0" and t ~= "1") or (et ~= "0" and et ~= "1") or
        (ui ~= "0" and ui ~= "1") or (pi ~= "0" and pi ~= "1") then
        return nil, "invalid native output header"
      end
      if cc and (cc ~= "0" and cc ~= "1") then
        return nil, "invalid native consensus flag"
      end
      if vc and vc ~= "0" and vc ~= "1" then
        return nil, "invalid native visible-consensus flag"
      end
      local consensus_bytes = cc and tonumber(cb) or nil
      local consensus_raw = cc and tonumber(cr) or nil
      if cc and (not consensus_bytes or not consensus_raw or
          consensus_bytes < 0 or consensus_raw < 0 or
          consensus_bytes ~= math.floor(consensus_bytes) or
          consensus_raw ~= math.floor(consensus_raw)) then
        return nil, "invalid native consensus boundary"
      end
      if cc == "0" and (consensus_bytes ~= 0 or consensus_raw ~= 0) then
        return nil, "invalid native consensus omission"
      end
      result.truncated = t == "1"
      result.early_truncated = et == "1"
      result.uses_incomplete = ui == "1"
      result.prefers_incomplete = pi == "1"
      result.n_final = tonumber(n_final)
      result.n_early = tonumber(n_early)
      result.consensus_complete = cc == "1"
      result.consensus_text_bytes = consensus_bytes or 0
      result.consensus_raw_length = consensus_raw or 0
      result.visible_consensus = vc == "1"
      first = false
    else
      local text, segmented, score, conf, max_rank, pathmap =
        line:match("^([^\t]*)\t([^\t]*)\t([^\t]*)\t([^\t]*)\t([^\t]*)\t(.*)$")
      if not text or text == "" then return nil, "invalid native candidate line" end
      local score_number = tonumber(score)
      local confidence_number = tonumber(conf)
      local rank_number = tonumber(max_rank)
      if not finite_number(score_number) or not finite_number(confidence_number) or
        not finite_number(rank_number) or rank_number < 1 or
        rank_number ~= math.floor(rank_number) then
        return nil, "invalid native candidate score"
      end
      if utf8_length(text) == nil or utf8_length(segmented) == nil or
        text:find("[%z\1-\8\11\12\14-\31]") or
        segmented:find("[%z\1-\8\11\12\14-\31]") then
        return nil, "invalid native candidate UTF-8"
      end
      local item = {
        text = text,
        segmented = segmented,
        score = score_number,
        confidence = confidence_number,
        max_rank = rank_number,
        raw_lengths = {},
      }
      for tl, rl in (pathmap or ""):gmatch("(%d+):(%d+)") do
        item.raw_lengths[tonumber(tl)] = tonumber(rl)
      end
      result.items[#result.items + 1] = item
    end
  end
  if first or #result.items ~= result.n_final + result.n_early then
    return nil, "native output count mismatch"
  end
  return result
end

local function has_duplicate_candidate_text(candidates)
  if type(candidates) ~= "table" then return false end
  local seen = {}
  for index = 1, #candidates do
    local candidate = candidates[index]
    if type(candidate) == "table" and type(candidate.text) == "string" then
      if seen[candidate.text] then return true end
      seen[candidate.text] = true
    end
  end
  return false
end

-- 仅保留前 n_final 个终态候选；include_early 仅为兼容旧 ABI
local function decode(raw, include_early)
  local h = engine_handle
  if not h or not tigerengine or engine_error then return { items = {}, early = {} } end
  local ok, out, ms = pcall(tigerengine.decode, h, raw, include_early and true or false)
  if not ok or type(out) ~= "string" then
    local detail = ok and ms or out
    report_engine_error("decode failed: " .. tostring(detail))
    return { items = {}, early = {} }
  end
  decode_ms = tonumber(ms) or 0
  local parsed, parse_error = parse_output(out)
  if not parsed then
    report_decode_output_error("invalid decode output: " .. tostring(parse_error))
    return { items = {}, early = {} }
  end
  local n_final = parsed.n_final or #parsed.items
  if #parsed.items > n_final then
    for index = n_final + 1, #parsed.items do
      parsed.early[#parsed.early + 1] = parsed.items[index]
    end
    while #parsed.items > n_final do
      table.remove(parsed.items)
    end
  end
  -- A text can occur in both lists when one native path is complete and
  -- another reaches the same text through an unfinished tail.  That overlap
  -- is valid evidence and is merged later.  Repeated text within one list is
  -- still malformed because it would double-count a single frontier row.
  if has_duplicate_candidate_text(parsed.items) or
      has_duplicate_candidate_text(parsed.early) then
    report_decode_output_error("duplicate native candidate text")
    return { items = {}, early = {} }
  end
  if parsed.consensus_complete then
    local source = parsed.items[1] or parsed.early[1]
    local canonical_raw = type(raw) == "string" and
      raw:gsub("[ \t\r\n]", ""):lower() or ""
    if (parsed.consensus_text_bytes or 0) > 0 and
      (not source or parsed.consensus_text_bytes >= #source.text or
       (parsed.consensus_raw_length or 0) >= #canonical_raw) then
      report_decode_output_error("invalid native consensus summary")
      return { items = {}, early = {} }
    end
  end
  return parsed
end

local function normalize_raw(raw)
  if type(raw) ~= "string" then return "" end
  -- Rime's speller inserts spaces between syllables.  The native decoder
  -- treats them as separators, so evidence continuity must use the same
  -- canonical code stream rather than the display preedit bytes.
  raw = raw:gsub("[ \t\r\n]", "")
  return raw:lower()
end
local function utf_chars(text)
  local chars = {}
  local i = 1
  while i <= #text do
    local c = text:byte(i)
    local len = c < 0x80 and 1 or c < 0xE0 and 2 or c < 0xF0 and 3 or 4
    chars[#chars + 1] = text:sub(i, i + len - 1)
    i = i + len
  end
  return chars
end

local function trim_segmented_after_raw_prefix(segmented, raw_prefix_length)
  if not segmented or segmented == "" or raw_prefix_length <= 0 then
    return segmented or ""
  end
  local raw_count = 0
  local index = 1
  while index <= #segmented and raw_count < raw_prefix_length do
    if segmented:sub(index, index) ~= " " then
      raw_count = raw_count + 1
    end
    index = index + 1
  end
  while index <= #segmented and segmented:sub(index, index) == " " do
    index = index + 1
  end
  return index <= #segmented and segmented:sub(index) or ""
end

local function segment_start_position(segment)
  if type(segment) ~= "table" and type(segment) ~= "userdata" then return 0 end
  local access_ok, value = pcall(function()
    local start = segment.start
    return start == nil and segment._start or start
  end)
  if not access_ok then return 0 end
  value = tonumber(value)
  return finite_number(value) and math.max(0, math.floor(value)) or 0
end

local function selected_text_before(context, boundary)
  if not context or not finite_number(boundary) or boundary <= 0 then return "" end
  local composition_ok, composition = pcall(function() return context.composition end)
  if not composition_ok then return "" end
  if not composition or type(composition.toSegmentation) ~= "function" then return "" end
  local ok, segmentation = pcall(composition.toSegmentation, composition)
  if not ok or not segmentation or type(segmentation.get_segments) ~= "function" then
    return ""
  end
  local segments_ok, segments = pcall(segmentation.get_segments, segmentation)
  if not segments_ok or type(segments) ~= "table" then return "" end
  local selected = {}
  for _, previous in ipairs(segments) do
    local previous_ok, end_pos, status, getter = pcall(function()
      return tonumber(previous and previous._end), previous and previous.status,
        previous and previous.get_selected_candidate
    end)
    if previous_ok and finite_number(end_pos) and end_pos <= boundary and
        (status == "kSelected" or status == "kConfirmed") and
        type(getter) == "function" then
      local candidate_ok, candidate = pcall(getter, previous)
      local text_ok, text = pcall(function() return candidate and candidate.text end)
      if not text_ok then text = nil end
      if type(text) == "string" and text ~= "" and utf8_length(text) then
        selected[#selected + 1] = text
      end
    end
  end
  return table.concat(selected)
end

local function candidate_prefix_boundary(item, prefix_text, raw_boundary)
  if type(item) ~= "table" or type(item.text) ~= "string" or
      type(prefix_text) ~= "string" or not finite_number(raw_boundary) then
    return nil
  end
  local text_bytes = #prefix_text
  if text_bytes == 0 then
    return raw_boundary == 0 and 0 or nil
  end
  if item.text:sub(1, text_bytes) ~= prefix_text then return nil end
  local lengths = type(item.raw_lengths) == "table" and item.raw_lengths or nil
  local mapped = lengths and tonumber(lengths[text_bytes]) or nil
  if mapped ~= raw_boundary then return nil end
  return text_bytes
end

-- ---------------------------------------------------------------- 候选输出

M.translator = {}

function M.translator.init(env)
  retain_engine(env)
  pcall(reranker.init, env)
end

function M.translator.fini(env)
  pcall(reranker.fini, env)
  release_engine(env)
end

function M.translator.func(input, seg, env)
  if not seg:has_tag("abc") then return end
  if env and env._tiger_engine_ready == false then return end
  local context = env.engine.context
  local context_input_raw = context.input
  if type(context_input_raw) ~= "string" then
    context_input_raw = type(input) == "string" and input or ""
  end
  local context_input = normalize_raw(context_input_raw)
  local segment_input = normalize_raw(input or "")
  if context_input == "" then context_input = segment_input end
  -- A single two-syllable word belongs to the normal smart translator so its
  -- learned frequency remains authoritative.
  if #context_input <= 4 and not context_input:find("'") then return end

  local segment_start = segment_start_position(seg)
  local selected_text = selected_text_before(context, segment_start)
  -- Segment positions are measured against the original Context.input bytes;
  -- only remove separators after taking the prefix slice.
  local raw_prefix_in_context = normalize_raw(
    context_input_raw:sub(1, segment_start))
  local prefix_text = selected_text
  local prefix_raw_boundary = #raw_prefix_in_context
  -- Keep the complete raw input for native/LM scoring.  The candidate view is
  -- trimmed to the active segment below.
  local raw = context_input
  local decoded = decode(raw, false)
  local sentence_items = {}
  for index = 1, #decoded.items do
    local item = decoded.items[index]
    if type(item.text) == "string" and #utf_chars(item.text) > 2 then
      sentence_items[#sentence_items + 1] = item
    end
  end
  if #sentence_items == 0 then return end
  decoded.items = sentence_items

  -- Reranking remains optional and fail-open.  Candidate metadata is retained
  -- until after this call so the policy can validate native scores.
  local ok, ranked = pcall(reranker.rerank, decoded.items, raw, context, env,
    prefix_text)
  if ok and type(ranked) == "table" then decoded.items = ranked end

  local yielded = 0
  local prefix_bytes = #prefix_text
  for i = 1, #decoded.items do
    local item = decoded.items[i]
    local prefix_ok = prefix_bytes == 0 or
      item.text:sub(1, prefix_bytes) == prefix_text
    if segment_start > 0 then
      -- A full native path is safe for a later segment only when its text and
      -- raw boundary agree with the already selected preceding segments.
      prefix_ok = prefix_ok and
        candidate_prefix_boundary(item, prefix_text, prefix_raw_boundary) ~= nil
    end
    if prefix_ok then
      local text = prefix_bytes == 0 and item.text or
        item.text:sub(prefix_bytes + 1)
      local preedit = trim_segmented_after_raw_prefix(item.segmented,
        prefix_raw_boundary)
      if preedit == "" and segment_input ~= "" then preedit = segment_input end
      if text ~= "" then
        local candidate_type = env and env._tiger_candidate_type or "mohu_llm_zrm"
        local cand = Candidate(candidate_type, seg.start, seg._end, text, "")
        cand.preedit = preedit
        cand.quality = candidate_quality - yielded * 0.001
        yield(cand)
        yielded = yielded + 1
        if yielded >= candidate_limit then return end
      end
    end
  end
end

return M
