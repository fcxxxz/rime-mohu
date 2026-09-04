-- 魔虎整句（mohu_tiger_sentence）——Lua 薄壳
-- 解码与语言模型全部在 libtigerengine.dylib（C 引擎）中，
-- 本文件负责原生候选输出与可选神经重排；不主动改写或提交 Context.input。
-- 移植自 TigerClaw 虎整句 Rime 版 tiger_sentence.lua（MIT 未声明，自用）。
--
-- schema 引用：
--   lua_translator@*mohu_tiger_sentence*translator
-- 配置（schema 内 tiger/ 节，均可省略）：
--   tiger/engine_lib: 引擎 dylib 路径（默认 <用户目录>/mohu/runtime/libtigerengine.dylib）
--   tiger/model:      模型目录或显式文件路径（默认 <用户目录>/mohu/model/）
--   tiger/scheme:      双拼方案标识（zrm 或 flypy）
--   tiger/candidate_type: native 候选类型（默认按 scheme 推导）
--   tiger/lexicon:    码表路径（默认由 runtime resolver 提供）
--   tiger/beam:       束宽（默认 200）
--   tiger/all_ranks:  >4 键时是否允许全部档位竞争（默认 true）
--   tiger/initial_quality: 原生候选质量（默认 50）
--   tiger/personal_lexicon_max_rows: 个人词快照可选行数上限（默认不限制）
--   tiger/personal_refresh_interval: 提交边界快照刷新防抖秒数（默认 30，0=每次提交刷新）
--   tiger/perf_log: 输出逐句 native/Lua 分层耗时日志（默认 false）

local M = {}
local runtime = require("mohu_runtime")
local personal_lexicon = require("mohu_personal_lexicon")

-- librime-lua builds differ in how they expose logging: most ship a `log`
-- table, but some Tiger weasel builds register `log` as a plain function.
-- Indexing a function value raises, so never touch fields before type checks.
local function log_error(message)
  if type(log) == "table" then
    if type(log.error) == "function" then
      pcall(log.error, message)
    end
  elseif type(log) == "function" then
    pcall(log, message)
  end
end

local beam_width = 200
local candidate_limit = 20
local candidate_quality = 50
local personal_refresh_interval = 30

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
local word_scorer_ready = nil       -- nil=未知 true=词层可用 false=不可用
local word_scorer_error_logged = false

local function report_engine_error(message)
  engine_error = message
  if not engine_error_logged then
    engine_error_logged = true
    log_error("mohu_tiger_sentence: " .. message)
  end
end

local function report_decode_output_error(message)
  if decode_output_error_logged then return end
  decode_output_error_logged = true
  log_error("mohu_tiger_sentence: " .. message)
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
  if value == "mohu" or value:sub(1, #"mohu/") == "mohu/" then
    return root .. value:sub(#"mohu" + 1)
  end
  return value
end

local function configure(env)
  local cfg = env and env.engine and env.engine.schema and env.engine.schema.config
  local scheme = config_string(cfg, "tiger/scheme") or "zrm"
  if scheme ~= "zrm" and scheme ~= "flypy" then scheme = "zrm" end
  local candidate_type = config_string(cfg, "tiger/candidate_type")
  if candidate_type ~= "mohu_zrm" and candidate_type ~= "mohu_flypy" then
    candidate_type = "mohu_" .. scheme
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
    local interval
    local interval_ok, interval_value = pcall(cfg.get_int, cfg,
      "tiger/personal_refresh_interval")
    if interval_ok then interval = interval_value end
    if interval == nil then
      local s_ok, s_value = pcall(cfg.get_string, cfg,
        "tiger/personal_refresh_interval")
      if s_ok then interval = tonumber(s_value) end
    end
    if finite_number(interval) and interval >= 0 then
      personal_refresh_interval = math.floor(interval)
    end
    if env then
      env._tiger_perf = false
      local perf_ok, perf_value = pcall(cfg.get_string, cfg, "tiger/perf_log")
      if perf_ok and (perf_value == "true" or perf_value == "1") then
        env._tiger_perf = true
      end
      if not env._tiger_perf then
        local perf_int_ok, perf_int = pcall(cfg.get_int, cfg, "tiger/perf_log")
        if perf_int_ok and perf_int == 1 then env._tiger_perf = true end
      end
    end
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
  local configured_model = resolve_runtime_path(conf("model"), paths)
  local model
  if configured_model == paths.model or configured_model == paths.ngram then
    model = runtime.resolve_model({ model_dir = paths.model })
  elseif configured_model then
    model = configured_model
  else
    model = runtime.resolve_model({ model_dir = paths.model })
  end
  model = model or paths.ngram
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
  local scorer_override = conf("word_scorer_model")
  local signature = table.concat(
    { lib, model, lexicon, beam_value, all_ranks_value, scorer_override or "" }, "\28")

  if engine_handle ~= nil then
    if engine_signature == signature then return engine_handle end
    if not engine_config_error_logged then
      engine_config_error_logged = true
      log_error("mohu_tiger_sentence: native engine configuration changed; reload required")
    end
    return nil
  end
  if engine_error then return nil end

  -- The Windows loader does not search the engine DLL's own directory for its
  -- dependencies.  Preload every runtime-side dependency by absolute path
  -- first; modules already mapped into the process satisfy imports by name.
  -- lua54.dll is always required; libwinpthread-1.dll only exists for mingw
  -- builds that link the pthread runtime dynamically, and its preload is
  -- silent no-op when the file is absent (fully static builds).
  if lib:sub(-4):lower() == ".dll" then
    pcall(package.loadlib, paths.runtime .. "/lua54.dll", "*")
    pcall(package.loadlib, paths.runtime .. "/libwinpthread-1.dll", "*")
  end
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
  -- The bundled lua54.dll must embed the same Lua version as the host
  -- weasel's rime.dll; a minor-version mismatch corrupts strings returned
  -- through the C API (their length and contents disagree, e.g. #s and
  -- s:byte disagree).  Probe once with a known input and reject the engine
  -- instead of yielding garbage that fails parsing on every keystroke.
  local canary_ok, canary = pcall(tigerengine.decode, h, "a", false)
  if not canary_ok or type(canary) ~= "string" or #canary == 0 or
      canary:byte(1) == nil or canary:byte(#canary) == nil or
      not canary:match("^(%d+) (%d+) (%d+) (%d+) ") then
    report_engine_error("lua runtime mismatch: the bundled lua54.dll and the " ..
      "weasel rime.dll must embed the same Lua 5.4.x version")
    pcall(tigerengine.free, h)
    return nil
  end
  -- 词级评分层：tiger/word_scorer_model 显式指定独立 MHKNM01 时加载
  -- （研究/覆盖用）；常规路径由容器模型（MHCTN01）自带词层。加载失败
  -- 只记一次日志，引擎本体不受影响。随后读一次 status 判定词层可用性；
  -- 旧 dylib（无该 ABI / status 无该字段）自动视为不可用。
  word_scorer_ready = false
  if scorer_override then
    local scorer_path = resolve_runtime_path(scorer_override, paths)
    if type(tigerengine.load_word_scorer) == "function" then
      local ok, loaded, why = pcall(tigerengine.load_word_scorer, h, scorer_path)
      if not ok or loaded ~= true then
        if not word_scorer_error_logged then
          word_scorer_error_logged = true
          log_error("mohu_tiger_sentence: word scorer load failed: " ..
            tostring(why or loaded))
        end
      end
    end
  end
  if type(tigerengine.context_word_scores) == "function" then
    local ok_status, status = pcall(tigerengine.status, h)
    if ok_status and type(status) == "string" and
        status:find("word_scorer=", 1, true) ~= nil and
        status:find("word_scorer=off", 1, true) == nil then
      word_scorer_ready = true
    end
  end
  engine_handle = h
  engine_signature = signature
  return h
end

-- 个人词快照的刷新时机（打字零影响的分片设计，三阶段）：
--   1. 提交只递增代数计数并标记 dirty，不做任何扫描工作；
--   2. 扫描以 ≤5ms CPU 预算的切片推进，只在输入组合为空时启动/推进；
--   3. 扫描完成后进入 native 事务喂入阶段：personal_append 按同一预算
--      分片喂入整行块，personal_commit 一次性原子切换（解析成本已随
--      append 摊销，commit 只剩哈希层比对与应用；无变化时 native 保留
--      解码缓存）。事务期间解码始终使用旧快照。
-- 旧 ABI（无事务函数的 dylib）自动回退整体 set_personal 路径。
-- 初始化时做一次一次性全量（方案装载期，打字尚未开始）。
-- tiger/personal_refresh_interval 秒数设为 0 可关闭时间防抖。
-- 设置 tiger/personal_lexicon_max_rows 时回退整体扫描路径（需全局排序）。
-- 例外：--sync 从其他设备合并进来的词不触发提交事件，将在下一次
-- 含中文的上屏之后被带入快照。
local personal_scan_budget = 0.005  -- 每片 CPU 秒预算（扫描与喂入共用）

local function personal_scan_options(env)
  -- 默认无上限走分片路径；显式设置上限则保留整体路径（排序取头部）。
  local value = env._mohu_personal_max_rows
  if type(value) == "number" and value >= 0 then
    return { limit = math.floor(value) }, true
  end
  return nil, false
end

local function personal_cycle_complete(env)
  env._mohu_personal_refresh_at = os.time()
  -- 扫描/喂入期间又有提交：保持脏，下一周期再来。
  env._mohu_personal_dirty =
    (env._mohu_personal_generation or 0) ~= env._mohu_personal_scan_generation
end

local function personal_txn_available()
  return tigerengine ~= nil and
    type(tigerengine.personal_begin) == "function" and
    type(tigerengine.personal_append) == "function" and
    type(tigerengine.personal_commit) == "function"
end

-- 整体回退路径（旧 ABI / 事务失败 / max_rows 限额）。
local function personal_apply_monolithic(env, payload)
  if payload ~= env._mohu_personal_last_payload then
    env._mohu_personal_last_payload = payload
    local ok, err = pcall(tigerengine.set_personal_lexicon, engine_handle, payload)
    if not ok then
      log_error("mohu_sentence: personal lexicon update failed: " .. tostring(err))
    end
  end
  personal_cycle_complete(env)
end

-- 事务喂入：每个空闲事件喂入预算内的整行块；全部喂完则 commit。
-- 返回 true 表示本轮事务链已结束（无论成败）。
local function personal_feed_tick(env)
  local feed = env._mohu_personal_feed
  if feed == nil then return true end
  local deadline = os.clock() + personal_scan_budget
  local fed = 0
  while feed.index <= #feed.parts do
    local part = feed.parts[feed.index]
    local ok, err = pcall(tigerengine.personal_append, engine_handle, part)
    if not ok then
      pcall(tigerengine.personal_abort, engine_handle)
      env._mohu_personal_feed = nil
      log_error("mohu_sentence: personal append failed: " .. tostring(err))
      personal_apply_monolithic(env, table.concat(feed.parts))
      return true
    end
    feed.index = feed.index + 1
    fed = fed + 1
    if fed >= 2048 then break end
    if fed % 64 == 0 and os.clock() >= deadline then break end
  end
  if feed.index > #feed.parts then
    env._mohu_personal_feed = nil
    local ok, err = pcall(tigerengine.personal_commit, engine_handle)
    if not ok then
      pcall(tigerengine.personal_abort, engine_handle)
      log_error("mohu_sentence: personal commit failed: " .. tostring(err))
      personal_apply_monolithic(env, table.concat(feed.parts))
      return true
    end
    personal_cycle_complete(env)
  end
  return env._mohu_personal_feed == nil
end

-- 扫描完成后的衔接：有事务 ABI 则进入喂入阶段，否则整体应用。
local function personal_start_apply(env, state)
  if personal_txn_available() then
    local ok = pcall(tigerengine.personal_begin, engine_handle)
    if ok then
      env._mohu_personal_feed = { parts = state.parts, index = 1 }
      -- 顺势喂入第一片。
      personal_feed_tick(env)
      return
    end
    pcall(tigerengine.personal_abort, engine_handle)
  end
  personal_apply_monolithic(env, personal_lexicon.scan_finish(state))
end

local function personal_scan_tick(env, ctx)
  -- 组合非空时绝不推进（打字避让）。
  if ctx then
    local ok, input = pcall(function() return ctx.input end)
    if ok and type(input) == "string" and #input > 0 then return end
  end
  -- 阶段 3：事务喂入进行中。
  if env._mohu_personal_feed ~= nil then
    personal_feed_tick(env)
    return
  end
  -- 阶段 2：扫描进行中。
  local state = env._mohu_personal_scan
  if state ~= nil then
    if not personal_lexicon.scan_step(state, personal_scan_budget) then return end
    env._mohu_personal_scan = nil
    personal_start_apply(env, state)
    return
  end
  -- 阶段 1：条件启动。
  if not env._mohu_personal_dirty then return end
  if personal_refresh_interval > 0 then
    local last = env._mohu_personal_refresh_at
    if last and (os.time() - last) < personal_refresh_interval then return end
  end
  local memory = env._mohu_personal_memory
  if not memory or not tigerengine or not engine_handle or
      type(tigerengine.set_personal_lexicon) ~= "function" then
    return
  end
  local _, monolithic = personal_scan_options(env)
  if monolithic then
    local options = personal_scan_options(env)
    local _, payload = personal_lexicon.snapshot(memory, options)
    personal_apply_monolithic(env, payload)
    return
  end
  local scan, status = personal_lexicon.scan_begin(memory)
  if scan == nil then
    env._mohu_personal_refresh_at = os.time()
    env._mohu_personal_dirty = false
    if status == "empty" and env._mohu_personal_last_payload ~= "" then
      -- userdb 被清空：必须立即重置 native 叠加层。
      env._mohu_personal_last_payload = ""
      pcall(tigerengine.set_personal_lexicon, engine_handle, "")
    end
    return
  end
  env._mohu_personal_scan = scan
  env._mohu_personal_scan_generation = env._mohu_personal_generation or 0
  if not personal_lexicon.scan_step(scan, personal_scan_budget) then return end
  env._mohu_personal_scan = nil
  personal_start_apply(env, scan)
end

local function refresh_personal_lexicon(env, force)
  local memory = env and env._mohu_personal_memory
  if not memory or not tigerengine or not engine_handle or
      type(tigerengine.set_personal_lexicon) ~= "function" then
    return
  end
  if not force and personal_refresh_interval > 0 then
    local last = env._mohu_personal_refresh_at
    if last and (os.time() - last) < personal_refresh_interval then return end
  end
  local options = personal_scan_options(env)
  local _, payload = personal_lexicon.snapshot(memory, options)
  personal_apply_monolithic(env, payload)
end

-- 只有含非 ASCII 字符的上屏才可能写入 userdb（词选择/造词）；
-- 纯英文与标点上屏不标脏。读取提交文本失败时保守视为有变化。
local function commit_could_touch_userdb(ctx)
  local ok, text = pcall(function() return ctx and ctx:get_commit_text() end)
  if not ok or type(text) ~= "string" then return true end
  if text == "" then return false end
  for index = 1, #text do
    if text:byte(index) >= 0x80 then return true end
  end
  return false
end

-- native 候选不是 script 翻译器产出的短语，librime 的 Memorize 认领不了它；
-- 且 >=5 键输入由 express 走不读 userdb 的 smart_static，整句上屏也不学习。
-- 提交时按候选自带的分段码（preedit，每字一段）确定基码与首辅码，再经
-- ReverseLookup 反查每个字的完整音节码（如 yh;ea），拼成 userdb 的键
-- （音节表字符串，与词库词的键同构），经 smart 命名空间的 Memory 写入，
-- 恢复「打过辅码的词，裸双拼也跟得上调频」。
local native_memorize_max_syllables = 10  -- 与 smart/max_word_length 对齐

local function native_reverse_lookup(env)
  if env._tiger_reverse ~= nil then return env._tiger_reverse end
  env._tiger_reverse = false
  if type(ReverseLookup) ~= "function" then return nil end
  local cfg = env.engine and env.engine.schema and env.engine.schema.config
  local dictionary = config_string(cfg, "smart/dictionary") or
    ("mohu_" .. (env._tiger_scheme or "zrm") .. ".extended")
  local ok, db = pcall(ReverseLookup, dictionary)
  if ok and db and type(db.lookup) == "function" then
    env._tiger_reverse = db
  end
  return env._tiger_reverse
end

-- token 是候选 preedit 里的单字分段码（裸双拼或含辅码/码形后缀）；
-- codes 是该字的全部完整码（空格分隔，如 "yh;ea yh;eb"）。
-- 返回与 token 基码一致、且首辅码一致（若 token 带辅码）的完整码。
local function pick_full_char_code(codes, token)
  local base = token:sub(1, 2)
  local aux_first = token:sub(3, 3)
  if aux_first == ";" or aux_first == "/" then aux_first = "" end
  local fallback = nil
  for code in codes:gmatch("%S+") do
    if code:sub(1, 2) == base then
      local semicolon = code:find(";", 3, true)
      local code_aux = semicolon and code:sub(semicolon + 1, semicolon + 1) or ""
      if aux_first == "" or code_aux == aux_first then
        return code
      end
      fallback = fallback or code
    end
  end
  return fallback
end

local function memorize_native_candidates(env, ctx)
  local memory = env._mohu_personal_memory
  if not memory or type(memory.update_userdict) ~= "function" then return end
  if type(DictEntry) ~= "function" then return end
  local reverse = native_reverse_lookup(env)
  if not reverse then return end
  local candidate_type = env._tiger_candidate_type
  if not candidate_type then return end
  local composition_ok, composition = pcall(function() return ctx.composition end)
  if not composition_ok or not composition or
    type(composition.toSegmentation) ~= "function" then return end
  local ok, segmentation = pcall(composition.toSegmentation, composition)
  if not ok or not segmentation or type(segmentation.get_segments) ~= "function" then
    return
  end
  local segments_ok, segments = pcall(segmentation.get_segments, segmentation)
  if not segments_ok or type(segments) ~= "table" then return end
  for _, segment in ipairs(segments) do
    local cand_ok, cand = pcall(function()
      return segment and segment.get_selected_candidate and
        segment:get_selected_candidate()
    end)
    if cand_ok and cand then
      local type_ok, cand_type = pcall(function() return cand.type end)
      local preedit_ok, preedit = pcall(function() return cand.preedit end)
      local text_ok, text = pcall(function() return cand.text end)
      if type_ok and cand_type == candidate_type and preedit_ok and text_ok and
        type(text) == "string" and text ~= "" then
        local chars = utf_chars(text)
        local tokens = {}
        for token in tostring(preedit):gmatch("%S+") do
          tokens[#tokens + 1] = token
        end
        if #chars == #tokens and #tokens > 1 and
          #tokens <= native_memorize_max_syllables then
          local full_codes = {}
          for index = 1, #chars do
            local lookup_ok, codes = pcall(reverse.lookup, reverse, chars[index])
            local full_code = lookup_ok and type(codes) == "string" and
              pick_full_char_code(codes, tokens[index]) or nil
            if not full_code then full_codes = nil break end
            full_codes[index] = full_code
          end
          if full_codes then
            local entry_ok, entry = pcall(DictEntry)
            if entry_ok and entry then
              local set_ok = pcall(function()
                entry.text = text
                -- userdb 的键是音节表串且每个音节后跟一个空格
                -- （TranslateCodeToString 的格式，查找侧按此前缀匹配）。
                entry.custom_code = table.concat(full_codes, " ") .. " "
              end)
              if set_ok then
                pcall(memory.update_userdict, memory, entry, 1, "")
              end
            end
          end
        end
      end
    end
  end
end

-- ── 用户调频层（V5 + 用户小模型查询融合）───────────────────────────
-- 上屏文本喂入 native 引擎的内存三元计数表；解码时每个 trigram 查询按
-- P = w·P_V5 + (1-w)·P_用户 概率域融合（w = tiger/user_model_weight，
-- 默认 0.85，V5 主导）。静态模型文件永不改写；计数经二进制快照跨会话
-- 持久化（默认 mohu/config/user-ngram.snapshot，安装器不触碰该
-- 目录，重装存活）。旧 ABI dylib（无 update_user_model）自动停用本层。
local user_model_weight_default = 0.85
local user_model_snapshot_interval_default = 64

local function user_model_available()
  return tigerengine ~= nil and engine_handle ~= nil and
    type(tigerengine.update_user_model) == "function" and
    type(tigerengine.user_model_export) == "function" and
    type(tigerengine.user_model_import) == "function" and
    type(tigerengine.set_user_model_weight) == "function"
end

local function config_flag(cfg, key, default)
  local value = config_string(cfg, key)
  if value == "true" or value == "1" then return true end
  if value == "false" or value == "0" then return false end
  return default
end

local function ensure_snapshot_directory(directory)
  -- 失败时后续 io.open 自然失败，快照被安全跳过。
  if package.config:sub(1, 1) == "\\" then
    pcall(os.execute, 'md "' .. directory .. '" 2>nul')
  else
    pcall(os.execute, "mkdir -p '" .. directory:gsub("'", "'\\''") .. "'")
  end
end

local function user_model_write_snapshot(env)
  if not (env and env._tiger_user_model_on) then return end
  if not user_model_available() then return end
  local ok, blob = pcall(tigerengine.user_model_export, engine_handle)
  if not ok or type(blob) ~= "string" then return end
  local path = env._tiger_user_model_path
  if not path then return end
  local directory = path:match("^(.*)[/\\][^/\\]+$")
  if directory then ensure_snapshot_directory(directory) end
  local temporary = path .. ".tmp-" .. tostring(os.time()) .. "-" ..
    tostring(math.random(1000000))
  local file = io.open(temporary, "wb")  -- blob 是二进制，可含 NUL
  if not file then return end
  local ok_write = file:write(blob)
  if ok_write and file.flush then ok_write = file:flush() end
  file:close()
  if not ok_write then
    os.remove(temporary)
    return
  end
  if os.rename(temporary, path) then
    env._tiger_user_model_dirty = false
  else
    os.remove(temporary)
  end
end

local function init_user_model(env)
  if not env or not env.engine then return end
  local cfg = env.engine.schema and env.engine.schema.config
  env._tiger_user_model_path = resolve_runtime_path(
    config_string(cfg, "tiger/user_model_snapshot") or
    "mohu/config/user-ngram.snapshot", runtime.paths())
  env._tiger_user_model_on = config_flag(cfg, "tiger/user_model", true)
  env._tiger_user_model_dirty = false
  env._tiger_user_model_commits = 0
  local interval = tonumber(config_string(cfg, "tiger/user_model_snapshot_interval")) or
    user_model_snapshot_interval_default
  if not finite_number(interval) or interval < 1 then
    interval = user_model_snapshot_interval_default
  end
  env._tiger_user_model_interval = math.floor(interval)
  local weight = tonumber(config_string(cfg, "tiger/user_model_weight")) or
    user_model_weight_default
  if not finite_number(weight) or weight <= 0 or weight > 1 then
    weight = user_model_weight_default
  end
  env._tiger_user_model_weight = weight
  if not env._tiger_user_model_on then return end
  -- 旧 ABI dylib：静默停用（非错误，升级 dylib 后自然恢复）。
  if not user_model_available() then
    env._tiger_user_model_on = false
    return
  end
  pcall(tigerengine.set_user_model_weight, engine_handle, weight)
  local file = io.open(env._tiger_user_model_path, "rb")
  if file then
    local blob = file:read("*a")
    file:close()
    if type(blob) == "string" and #blob > 0 then
      pcall(tigerengine.user_model_import, engine_handle, blob)
    end
  end
  local context = env.engine.context
  if context and context.commit_notifier then
    env._tiger_user_model_commit_notifier = context.commit_notifier:connect(function(ctx)
      if not env._tiger_user_model_on or not user_model_available() then return end
      local ok, text = pcall(function() return ctx and ctx:get_commit_text() end)
      if not ok or type(text) ~= "string" or text == "" then return end
      local has_cjk = false
      for index = 1, #text do
        if text:byte(index) >= 0x80 then has_cjk = true break end
      end
      if not has_cjk then return end
      pcall(tigerengine.update_user_model, engine_handle, text)
      env._tiger_user_model_dirty = true
      env._tiger_user_model_commits = env._tiger_user_model_commits + 1
      if env._tiger_user_model_commits % env._tiger_user_model_interval == 0 then
        user_model_write_snapshot(env)
      end
    end)
  end
end

local function fini_user_model(env)
  if not env then return end
  if env._tiger_user_model_commit_notifier ~= nil then
    pcall(function() env._tiger_user_model_commit_notifier:disconnect() end)
    env._tiger_user_model_commit_notifier = nil
  end
  if env._tiger_user_model_dirty then
    user_model_write_snapshot(env)
  end
  env._tiger_user_model_on = nil
  env._tiger_user_model_dirty = nil
  env._tiger_user_model_commits = nil
  env._tiger_user_model_path = nil
  env._tiger_user_model_interval = nil
  env._tiger_user_model_weight = nil
end

local function init_personal_lexicon(env)
  if Memory == nil or not env or not env.engine then return end
  local cfg = env.engine.schema and env.engine.schema.config
  local namespace = "translator"
  if cfg and type(cfg.get_string) == "function" then
    local ok, value = pcall(cfg.get_string, cfg, "tiger/personal_lexicon_namespace")
    if ok and type(value) == "string" and value ~= "" then namespace = value end
  end
  if cfg and type(cfg.get_int) == "function" then
    local ok, value = pcall(cfg.get_int, cfg, "tiger/personal_lexicon_max_rows")
    if ok and type(value) == "number" and value >= 0 then
      env._mohu_personal_max_rows = value
    end
  end
  local ok, memory = pcall(Memory, env.engine, env.engine.schema, namespace)
  if not ok or memory == nil then return end
  env._mohu_personal_memory = memory
  env._mohu_personal_generation = 0
  -- 方案装载期的一次性全量：此刻打字尚未开始，不会影响输入。
  refresh_personal_lexicon(env, true)
  local context = env.engine.context
  if context and context.commit_notifier then
    env._mohu_personal_commit_notifier = context.commit_notifier:connect(function(ctx)
      env._mohu_personal_generation = (env._mohu_personal_generation or 0) + 1
      memorize_native_candidates(env, ctx)
      if commit_could_touch_userdb(ctx) then
        env._mohu_personal_dirty = true
      end
      -- 上屏瞬间组合为空，是推进一个切片的天然时机。
      personal_scan_tick(env, ctx)
    end)
  end
  if context and context.update_notifier then
    env._mohu_personal_update_notifier = context.update_notifier:connect(function(ctx)
      -- 扫描进行中即使不脏也要推进；未开始时 tick 内部自会检查条件。
      personal_scan_tick(env, ctx)
    end)
  end
end

local function fini_personal_lexicon(env)
  if not env then return end
  if env._mohu_personal_update_notifier ~= nil then
    pcall(function() env._mohu_personal_update_notifier:disconnect() end)
    env._mohu_personal_update_notifier = nil
  end
  if env._mohu_personal_commit_notifier ~= nil then
    pcall(function() env._mohu_personal_commit_notifier:disconnect() end)
    env._mohu_personal_commit_notifier = nil
  end
  if env._mohu_personal_memory ~= nil then
    pcall(function() env._mohu_personal_memory:disconnect() end)
    env._mohu_personal_memory = nil
  end
  if env._mohu_personal_feed ~= nil then
    if tigerengine and engine_handle and type(tigerengine.personal_abort) == "function" then
      pcall(tigerengine.personal_abort, engine_handle)
    end
    env._mohu_personal_feed = nil
  end
  env._mohu_personal_scan = nil
  env._mohu_personal_scan_generation = nil
  env._mohu_personal_generation = nil
  env._mohu_personal_dirty = nil
  env._mohu_personal_last_payload = nil
  env._mohu_personal_refresh_at = nil
  env._mohu_personal_max_rows = nil
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
  word_scorer_ready = nil
  word_scorer_error_logged = false
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

-- tiger/perf_log 开启时按轮次输出 native/Lua 分层耗时，用于长句延迟归因；
-- 默认关闭。native 耗时来自引擎自报的 decode_ms，lua 耗时覆盖解析与候选构造。
local function perf_begin(env)
  if env and env._tiger_perf then env._tiger_perf_clock = os.clock() end
end

local function perf_end(env, raw, phase)
  if not (env and env._tiger_perf and env._tiger_perf_clock) then return end
  local started = env._tiger_perf_clock
  env._tiger_perf_clock = nil
  if not log or not log.info then return end
  pcall(log.info, string.format(
    "mohu_sentence perf len=%d native=%.2fms lua=%.2fms phase=%s",
    #raw, tonumber(decode_ms) or 0, (os.clock() - started) * 1000,
    tostring(phase)))
end

M.translator = {}

function M.translator.init(env)
  local handle = retain_engine(env)
  if handle ~= nil then
    init_personal_lexicon(env)
    init_user_model(env)
  end
end

function M.translator.fini(env)
  fini_personal_lexicon(env)
  fini_user_model(env)
  release_engine(env)
end

-- 供词级上下文重排 filter（lua/mohu_word_order_filter.lua）复用引擎句柄
-- 做批量候选评分。返回 score_fn, handle；词层不可用（旧 dylib、非容器
-- 模型、显式加载失败、引擎未就绪）返回 nil，调用方应直通。引擎生命周期
-- 由 translator 的引用计数管理，filter 不重复持有；打分不依赖码表，
-- 跨方案共享同一句柄也安全。
function M.acquire_word_scorer(env)
  if word_scorer_ready ~= true then return nil end
  if tigerengine == nil or type(tigerengine.context_word_scores) ~= "function" then
    return nil
  end
  local ok, handle = pcall(ensure_engine, env)
  if ok and handle then
    return tigerengine.context_word_scores, handle
  end
  return nil
end

-- 字符续写评分（octagram 同型机制）：字符级主模型即可，不要求词层。
-- 引擎未装载时返回 nil（与 ensure_decode_context 同触发条件，有 CJK
-- 历史的组合 translator 已装载引擎）。
function M.acquire_char_scorer(env)
  if tigerengine == nil or type(tigerengine.context_char_scores) ~= "function" then
    return nil
  end
  local ok, handle = pcall(ensure_engine, env)
  if ok and handle then
    return tigerengine.context_char_scores, handle
  end
  return nil
end

-- tiger/decode_context_chars 缓存读取（schema 变更前不会变化）。
local function decode_context_chars(env)
  if env and env._tiger_decode_context_chars == nil then
    local value
    local ok_cfg, cfg = pcall(function()
      return env.engine and env.engine.schema and env.engine.schema.config
    end)
    if ok_cfg and cfg and type(cfg.get_int) == "function" then
      local ok, n = pcall(cfg.get_int, cfg, "tiger/decode_context_chars")
      if ok and type(n) == "number" and n >= 1 then value = n end
    end
    if value == nil and ok_cfg and cfg and type(cfg.get_string) == "function" then
      local ok, s = pcall(cfg.get_string, cfg, "tiger/decode_context_chars")
      if ok then value = tonumber(s) end
    end
    env._tiger_decode_context_chars = value  -- nil = 默认 2
  end
  return env and env._tiger_decode_context_chars or nil
end

-- 把整段最近上屏文本喂给引擎作解码左上文——与 librime 的
-- GetPrecedingText/commit_history:latest_text() 同源同形（整段传递，
-- 尾部窗口由模型侧决定：octagram 是词级三元窗口，V5 是字符级窗口，
-- 窗口大小经 tiger/decode_context_chars 配置）。开关关闭（单次候选
-- 调频）或历史为空时传空串清空引擎上下文。旧 ABI dylib 无
-- set_decode_context 时静默降级。返回历史是否携带汉字（供 4 键早退
-- 判断；尾部截取是引擎内部的事）。
local function ensure_decode_context(env, context)
  local latest = ""
  if context and context.get_option and context:get_option("contextual_order") then
    local ok, text = pcall(function()
      local hist = context.commit_history
      if not hist then return "" end
      if type(hist.latest_text) == "function" then return hist:latest_text() end
      if type(hist.latest_text) == "string" then return hist.latest_text end
      return ""
    end)
    if ok and type(text) == "string" then latest = text end
  end
  -- 历史里没有任何汉字字节（E3-E9 覆盖 CJK 基本区首字节）则视为无上下文。
  local has_cjk = latest ~= "" and latest:find("[\228-\233]") ~= nil
  -- 引擎未装载且无上文时保持零成本：不为 4 键短码空载引擎。
  if tigerengine == nil then
    if not has_cjk then return false end
    if not pcall(ensure_engine, env) or tigerengine == nil then return false end
  end
  -- 旧 ABI dylib：静默降级（4 键继续走 smart，无上下文能力）。
  if not tigerengine.set_decode_context then return false end
  local ok_engine, handle = pcall(ensure_engine, env)
  if not (ok_engine and handle) then return false end
  local window = decode_context_chars(env)
  if window then
    pcall(tigerengine.set_decode_context, handle, latest, window)
  else
    pcall(tigerengine.set_decode_context, handle, latest)
  end
  return has_cjk
end

-- tiger/decode_context_takeover：存在上文时是否让 native 接管 4 键纯双拼
-- 词码（默认 false，保留 smart 已学词频权威；实测字符级模型裸排 4 键
-- 低于词库词频约 7pp，接管仅在明确需要时开启）。缓存读取。
local function decode_context_takeover(env)
  if env and env._tiger_decode_context_takeover == nil then
    local value = false
    local ok_cfg, cfg = pcall(function()
      return env.engine and env.engine.schema and env.engine.schema.config
    end)
    if ok_cfg and cfg and type(cfg.get_string) == "function" then
      local ok, s = pcall(cfg.get_string, cfg, "tiger/decode_context_takeover")
      if ok and (s == "true" or s == "1") then value = true end
    end
    env._tiger_decode_context_takeover = value
  end
  return env and env._tiger_decode_context_takeover or false
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
  -- 跨候选调频开启且上屏历史含汉字时，先把整段历史喂给引擎作左上文。
  local has_history_context = ensure_decode_context(env, context)
  -- A single two-syllable word belongs to the normal smart translator so its
  -- learned frequency remains authoritative.  With cross-commit context the
  -- native model MAY take over bare 4-key word codes when
  -- tiger/decode_context_takeover is enabled: P(首字|上文) outranks learned
  -- frequency.  Default off — the char-level model ranks bare 4-key codes
  -- below the dictionary's learned frequencies.
  if #context_input <= 4 and not context_input:find("'") and
      not (has_history_context and decode_context_takeover(env)) then
    return
  end

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
  perf_begin(env)
  local decoded = decode(raw, false)
  -- Two-character finals can only consume a two-syllable input; every key
  -- beyond the bare double pinyin is an auxiliary code the user typed to
  -- disambiguate the characters, so the native LM ordering (not raw char
  -- frequency) should lead there.  Bare four-key inputs never reach this
  -- point (early return above), keeping smart's learned frequency
  -- authoritative for them.
  local sentence_items = {}
  for index = 1, #decoded.items do
    local item = decoded.items[index]
    if type(item.text) == "string" and #utf_chars(item.text) > 1 then
      sentence_items[#sentence_items + 1] = item
    end
  end
  if #sentence_items == 0 then
    perf_end(env, raw, "empty")
    return
  end
  decoded.items = sentence_items

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
        local candidate_type = env and env._tiger_candidate_type or "mohu_zrm"
        local cand = Candidate(candidate_type, seg.start, seg._end, text, "")
        cand.preedit = preedit
        cand.quality = candidate_quality - yielded * 0.001
        yield(cand)
        yielded = yielded + 1
        if yielded >= candidate_limit then
          perf_end(env, raw, "limit")
          return
        end
      end
    end
  end
  perf_end(env, raw, "ok")
end

return M
