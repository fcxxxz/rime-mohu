-- tiger_user_words.lua
-- Runtime candidate management for the Tiger family schemas.
--
-- Shortcuts:
--   target code + \\ + Space enters word capture
--   Shift+Delete or Ctrl+Delete hides the selected candidate
--   Return           confirm word capture
--   Ctrl+arrows      move current candidate in the visible candidate page
--   Ctrl+Option+arrows is the macOS-friendly alternative
--   Ctrl+Home/End or Ctrl+Option+Home/End move current candidate to page edge
--   Ctrl+minus / Ctrl+equal  same as arrows up/down — 与魔虎调序同键位

local add_trigger = require("tiger_add_trigger")
local capture_input = require("tiger_capture_input")

local kRejected = 0
local kAccepted = 1
local kNoop = 2

local USER_WORDS_MARKER = "USER_WORDS_MARKER"
local GENERATED_START = "# " .. USER_WORDS_MARKER .. " generated-start"
local GENERATED_END = "# " .. USER_WORDS_MARKER .. " generated-end"

local PROFILES = {
    tiger = {
        schema_id = "tiger",
        extended_dict = "tiger.user.dict.yaml",
        legacy_migration_sources = {},
        source_dicts = {
            "tiger.user.dict.yaml",
            "tiger.extended.dict.yaml",
            "tiger.common.dict.yaml",
            "tiger.dict.yaml",
        },
        text_code_weight_dicts = {
            "tiger.user.dict.yaml",
            "tiger.extended.dict.yaml",
            "tiger.common.dict.yaml",
            "tiger.dict.yaml",
        },
        db_name = "tiger_user_words_tiger",
        weight_base = 100000000000,
        weight_step = 1000,
    },
    tigress = {
        schema_id = "tigress",
        extended_dict = "tigress.user.dict.yaml",
        legacy_migration_sources = {
            "tigress.extended.dict.yaml",
        },
        source_dicts = {
            "tigress.user.dict.yaml",
            "tigress.extended.dict.yaml",
            "tigress.common.dict.yaml",
            "tigress_ci.common.dict.yaml",
            "tigress_simp_ci.common.dict.yaml",
            "tigress.dict.yaml",
            "tigress_ci.dict.yaml",
            "tigress_simp_ci.dict.yaml",
        },
        text_code_weight_dicts = {
            "tigress.user.dict.yaml",
            "tigress.extended.dict.yaml",
        },
        db_name = "tiger_user_words_tigress",
        weight_base = 100000000000,
        weight_step = 1000,
    },
}

local DB_SEPARATOR = " \t"
local DB_META_KEY = "__tiger_user_words_meta__"
local DB_VERSION = 1
local LIBRIME_METADATA_KEYS = {
    ["\1/db_name"] = true,
    ["\1/db_type"] = true,
    ["\1/rime_version"] = true,
    ["\1/user_id"] = true,
}
local db_pools = {}

local function parse_db_record(value)
    if type(value) ~= "string" then
        return nil
    end
    local fields = {}
    for key, field_value in value:gmatch("(%a+)=([%-]?%d+)") do
        fields[key] = tonumber(field_value)
    end
    if fields.v ~= DB_VERSION then
        return nil
    end
    local legacy_ordered = fields.o == nil and fields.a ~= 1 and fields.h ~= 1
        and fields.w ~= nil and fields.w ~= 0
    return {
        added = fields.a == 1,
        hidden = fields.h == 1,
        weight = fields.w,
        updated_at = fields.t or 0,
        source = fields.s == 1 and "shortcut" or nil,
        ordered = fields.o == 1 or legacy_ordered,
    }
end

local function encode_db_record(record)
    return string.format(
        "v=%d a=%d h=%d w=%d t=%d s=%d o=%d",
        DB_VERSION,
        record.added and 1 or 0,
        record.hidden and 1 or 0,
        record.weight or 0,
        record.updated_at or 0,
        record.source == "shortcut" and 1 or 0,
        record.ordered and 1 or 0
    )
end

local function copy_db_record(record)
    if not record then
        return {
            added = false,
            hidden = false,
            weight = nil,
            updated_at = 0,
            source = nil,
            ordered = false,
        }
    end
    return {
        added = record.added,
        hidden = record.hidden,
        weight = record.weight,
        updated_at = record.updated_at,
        source = record.source,
        ordered = record.ordered,
    }
end

local Store = {}
Store.__index = Store

local function acquire_db(name)
    if type(LevelDb) ~= "function" then
        return nil
    end
    local pool = db_pools[name]
    if pool then
        return setmetatable({ pool = pool }, Store)
    end
    local ok, db = pcall(LevelDb, name)
    if not ok or not db then
        return nil
    end
    local opened = pcall(function()
        if not db:loaded() then
            db:open()
        end
    end)
    local loaded_ok, loaded = pcall(function()
        return db:loaded()
    end)
    if not opened or not loaded_ok or not loaded then
        pcall(function()
            if db:loaded() then
                db:close()
            end
        end)
        return nil
    end
    pool = { db = db }
    db_pools[name] = pool
    return setmetatable({ pool = pool }, Store)
end

function Store:query(code)
    local records = {}
    local prefix = code .. DB_SEPARATOR
    local ok, available = pcall(function()
        local accessor = self.pool.db:query(prefix)
        if not accessor then
            return false
        end
        for key, value in accessor:iter() do
            local record = parse_db_record(value)
            if record and key:sub(1, #prefix) == prefix then
                records[key:sub(1 + #prefix)] = record
            end
        end
        return true
    end)
    return ok and available and records or nil
end

function Store:list_all()
    local records = {}
    local ok, available = pcall(function()
        local accessor = self.pool.db:query("")
        if not accessor then
            return false
        end
        for key, value in accessor:iter() do
            local split = key:find(DB_SEPARATOR, 1, true)
            if not split and LIBRIME_METADATA_KEYS[key] then
                -- LevelDb stores its own database metadata beside user records.
            elseif not split then
                return false
            else
                local record = parse_db_record(value)
                if not record then
                    return false
                end
                local code = key:sub(1, split - 1)
                local text = key:sub(split + #DB_SEPARATOR)
                if code ~= DB_META_KEY then
                    if code == "" or text == "" then
                        return false
                    end
                    table.insert(records, {
                        code = code,
                        text = text,
                        record = record,
                    })
                end
            end
        end
        return true
    end)
    return ok and available and records or nil
end

function Store:write(code, text, record)
    local ok, result = pcall(function()
        return self.pool.db:update(code .. DB_SEPARATOR .. text, encode_db_record(record))
    end)
    return ok and result ~= false
end

function Store:is_migrated()
    local ok, found = pcall(function()
        local accessor = self.pool.db:query(DB_META_KEY .. DB_SEPARATOR)
        if not accessor then
            return false
        end
        for key, value in accessor:iter() do
            if key == DB_META_KEY .. DB_SEPARATOR .. "version" then
                return parse_db_record(value) ~= nil
            end
        end
        return false
    end)
    return ok and found or false
end

function Store:mark_migrated()
    return self:write(DB_META_KEY, "version", {
        added = false,
        hidden = false,
        weight = DB_VERSION,
        updated_at = os.time(),
    })
end

local shared_states = {}
local capture_sessions = {}
local restore_hidden
local reset_code
local capture_token_counter = 0
local capture_token_prefix = "tiger_user_words:" .. tostring(capture_sessions) .. ":"
local capture_token_property = "tiger_user_words_capture_token"
local management_code_property = "tiger_user_words_management_code"

local function capture_token(engine, create)
    local context = engine.context
    local token = context:get_property(capture_token_property) or ""
    if token == "" and create then
        capture_token_counter = capture_token_counter + 1
        token = capture_token_prefix .. capture_token_counter
        context:set_property(capture_token_property, token)
    end
    return token
end

local function set_capture(engine, capture)
    capture_sessions[capture_token(engine, true)] = capture
end

local function get_capture(engine)
    local token = capture_token(engine, false)
    return token ~= "" and capture_sessions[token] or nil
end

local function clear_engine_capture(engine)
    local token = capture_token(engine, false)
    if token ~= "" then
        capture_sessions[token] = nil
        engine.context:set_property(capture_token_property, "")
    end
end

local KEY = {
    BACKSPACE = 0xff08,
    RETURN = 0xff0d,
    KP_RETURN = 0xff8d,
    ESCAPE = 0xff1b,
    HOME = 0xff50,
    LEFT = 0xff51,
    UP = 0xff52,
    RIGHT = 0xff53,
    DOWN = 0xff54,
    PAGE_UP = 0xff55,
    PAGE_DOWN = 0xff56,
    END = 0xff57,
    SPACE = 0x20,
    BACKSLASH = 0x5c,
    APOSTROPHE = 0x27,
    PLUS = 0x2b,
    MINUS = 0x2d,
    EQUAL = 0x3d,
    SEMICOLON = 0x3b,
    DELETE = 0xffff,
}

local function pathsep()
    return (package.config or "\\"):sub(1, 1)
end

local function data_path(filename)
    return rime_api.get_user_data_dir() .. pathsep() .. filename
end

local function split_tab(line)
    local fields = {}
    for field in (line .. "\t"):gmatch("(.-)\t") do
        table.insert(fields, field)
    end
    return fields
end

local function trim_cr(line)
    return (line:gsub("\r$", ""))
end

local function read_lines(filename)
    local path = data_path(filename)
    local f = io.open(path, "r")
    if not f then
        return {}
    end
    local lines = {}
    for line in f:lines() do
        table.insert(lines, trim_cr(line))
    end
    f:close()
    return lines
end

local function ensure_code(state, code)
    state.added[code] = state.added[code] or {}
    state.blocked[code] = state.blocked[code] or {}
    state.weights[code] = state.weights[code] or {}
    state.ordered[code] = state.ordered[code] or {}
end

local function set_added(state, code, text, weight)
    if not code or code == "" or not text or text == "" then
        return
    end
    ensure_code(state, code)
    state.added[code][text] = true
    state.blocked[code][text] = nil
    state.weights[code][text] = weight or state.weights[code][text] or state.config.weight_base
end

local function set_blocked(state, code, text)
    if not code or code == "" or not text or text == "" then
        return
    end
    ensure_code(state, code)
    state.blocked[code][text] = true
    state.added[code][text] = nil
    state.weights[code][text] = nil
end

local function is_user_layer_dict(profile, filename)
    if filename == profile.extended_dict then
        return true
    end
    for _, legacy in ipairs(profile.legacy_migration_sources) do
        if filename == legacy then
            return true
        end
    end
    return false
end

local function uses_text_code_weight(profile, filename)
    for _, configured in ipairs(profile.text_code_weight_dicts) do
        if filename == configured then
            return true
        end
    end
    return false
end

local function parse_entry(profile, filename, line)
    if line:match("^%s*$") or line:match("^%s*#") then
        return nil
    end
    local fields = split_tab(line)
    local text = fields[1]
    local code
    local weight
    if uses_text_code_weight(profile, filename) then
        code = fields[2]
        weight = tonumber(fields[3])
    else
        weight = tonumber(fields[2])
        code = fields[3]
    end
    if text and code and text ~= "" and code ~= "" then
        return { text = text, code = code, weight = weight }
    end
    if is_user_layer_dict(profile, filename) and text and text ~= "" and not code then
        return { text = text, code = nil, weight = nil }
    end
    return nil
end

local function parse_marker(line)
    local op, code, text, value = line:match("^#%s*" .. USER_WORDS_MARKER .. "\t([a-z]+)\t([^\t]+)\t([^\t]+)\t?([^\t]*)")
    if op and code and text then
        return {
            op = op,
            code = code,
            text = text,
            value = value,
        }
    end
    return nil
end

local function migrate_to_db(profile, store)
    if not store or store:is_migrated() then
        return
    end

    local function current_record(code, text)
        local records = store:query(code)
        return records and records[text] or nil
    end

    local function write_record(code, text, changes)
        local record = copy_db_record(current_record(code, text))
        for key, value in pairs(changes) do
            record[key] = value
        end
        store:write(code, text, record)
    end

    for _, filename in ipairs(profile.source_dicts) do
        local in_body = false
        for _, line in ipairs(read_lines(filename)) do
            if line == "..." then
                in_body = true
            elseif in_body then
                local marker = parse_marker(line)
                if marker and (marker.op == "disabled" or marker.op == "enabled") then
                    write_record(marker.code, marker.text, {
                        added = marker.op == "enabled",
                        hidden = marker.op == "disabled",
                        weight = tonumber(marker.value) or profile.weight_base,
                        updated_at = os.time(),
                    })
                elseif is_user_layer_dict(profile, filename) then
                    local entry = parse_entry(profile, filename, line)
                    if entry and entry.code then
                        write_record(entry.code, entry.text, {
                            added = true,
                            weight = entry.weight or profile.weight_base,
                            updated_at = os.time(),
                        })
                    end
                end
            end
        end
    end
    store:mark_migrated()
end

local function load_state(profile)
    local store = acquire_db(profile.db_name)
    migrate_to_db(profile, store)

    local state = {
        config = profile,
        store = store,
        added = {},
        blocked = {},
        weights = {},
        ordered = {},
        generated_loaded = false,
    }

    local database_records = {}
    if store then
        for _, item in ipairs(store:list_all() or {}) do
            database_records[item.code] = database_records[item.code] or {}
            database_records[item.code][item.text] = true
            if item.record.added then
                set_added(state, item.code, item.text, item.record.weight or profile.weight_base)
            end
            if item.record.weight and item.record.weight ~= 0 then
                ensure_code(state, item.code)
                state.weights[item.code][item.text] = item.record.weight
            end
            if item.record.hidden then
                set_blocked(state, item.code, item.text)
            end
            if item.record.ordered then
                ensure_code(state, item.code)
                state.ordered[item.code][item.text] = true
            end
        end
    end

    for _, filename in ipairs(profile.source_dicts) do
        local in_generated = false
        for _, line in ipairs(read_lines(filename)) do
            if line == GENERATED_START then
                in_generated = true
                if filename == profile.extended_dict then
                    state.generated_loaded = true
                end
            elseif line == GENERATED_END then
                in_generated = false
            else
                local marker = parse_marker(line)
                local marker_in_database = marker and database_records[marker.code]
                    and database_records[marker.code][marker.text]
                if marker and not marker_in_database then
                    if marker.op == "disabled" then
                        set_blocked(state, marker.code, marker.text)
                    elseif marker.op == "enabled" then
                        set_added(state, marker.code, marker.text, tonumber(marker.value) or profile.weight_base)
                    end
                elseif in_generated then
                    local entry = parse_entry(profile, filename, line)
                    local entry_in_database = entry and entry.code and database_records[entry.code]
                        and database_records[entry.code][entry.text]
                    if entry and not entry_in_database then
                        set_added(state, entry.code, entry.text, entry.weight)
                    end
                end
            end
        end
    end

    return state
end

local function get_state(profile)
    if not shared_states[profile.schema_id] then
        shared_states[profile.schema_id] = load_state(profile)
    end
    return shared_states[profile.schema_id]
end

local function profile_for_env(env)
    local schema = env and env.engine and env.engine.schema
    local schema_id = schema and schema.schema_id
    local profile = PROFILES[schema_id]
    if not profile then
        log.error("tiger_user_words: unsupported schema: " .. tostring(schema_id))
    end
    return profile
end

local function persist_disable(state, code, text)
    if not state.store then
        return false
    end
    local records = state.store:query(code)
    if not records then
        return false
    end
    local record = copy_db_record(records[text])
    record.hidden = true
    record.updated_at = os.time()
    if not state.store:write(code, text, record) then
        return false
    end
    set_blocked(state, code, text)
    return true
end

local function persist_weight(state, code, text, weight, options)
    if not state.store then
        return false
    end
    local records = state.store:query(code)
    if not records then
        return false
    end
    local record = copy_db_record(records[text])
    options = options or {}
    if options.added ~= nil then
        record.added = options.added
    end
    record.hidden = false
    record.weight = weight
    record.updated_at = os.time()
    if options.source ~= nil then
        record.source = options.source
    end
    if options.ordered ~= nil then
        record.ordered = options.ordered
    end
    if not state.store:write(code, text, record) then
        return false
    end
    if record.added then
        set_added(state, code, text, weight)
    else
        ensure_code(state, code)
        state.weights[code][text] = weight
        state.blocked[code][text] = nil
    end
    return true
end

local function current_segment(env)
    local ctx = env.engine.context
    local comp = ctx.composition
    if comp:empty() then
        return nil
    end
    return comp:back()
end

local function current_code(env)
    local ctx = env.engine.context
    local seg = current_segment(env)
    if seg then
        return ctx.input:sub(seg._start + 1, seg._end)
    end
    return ctx.input or ""
end

local function genuine_text(cand)
    if not cand then
        return ""
    end
    if cand.get_genuine then
        local ok, genuine = pcall(function()
            return cand:get_genuine()
        end)
        if ok and genuine and genuine.text then
            return genuine.text
        end
    end
    return cand.text or ""
end

local function is_status_candidate(cand)
    return cand and cand.type == "tiger_user_status"
end

local function selected_candidate(env)
    return env.engine.context:get_selected_candidate()
end

local function current_selected_text(env)
    return genuine_text(selected_candidate(env))
end

local function remove_last_utf8_char(text)
    local pos = utf8.offset(text, -1)
    if not pos then
        return ""
    end
    return text:sub(1, pos - 1)
end

local function capture_status_text(capture)
    local text = capture.text
    if not text or text == "" then
        text = "未取字"
    end
    local label = capture.operation == "disable" and "减词" or "加词"
    return label .. " " .. capture.code .. "：" .. text
end

local function capture_status_comment(capture)
    if capture.message and capture.message ~= "" then
        return capture.message
    end
    return ""
end

local function update_prompt(env)
    local capture = get_capture(env.engine)
    if not capture then
        return
    end
    local seg = current_segment(env)
    if seg then
        local comment = capture_status_comment(capture)
        local suffix = comment ~= "" and "｜" .. comment or ""
        seg.prompt = "〔" .. capture_status_text(capture) .. suffix .. "〕"
    end
end

local function composition_segments(ctx)
    local comp = ctx.composition
    if not comp or comp:empty() then
        return {}
    end
    if comp.toSegmentation then
        local ok, segmentation = pcall(function()
            return comp:toSegmentation()
        end)
        if ok and segmentation and segmentation.get_segments then
            local segments_ok, segments = pcall(function()
                return segmentation:get_segments()
            end)
            if segments_ok and segments then
                return segments
            end
        end
    end
    return { comp:back() }
end

local function segment_start(seg)
    return seg and (seg._start or seg.start) or 0
end

local function segment_end(seg)
    return seg and (seg._end or seg["end"]) or segment_start(seg)
end

local function is_selected_segment(seg)
    return seg and (seg.status == "kSelected" or seg.status == "kConfirmed")
end

local function segment_selected_candidate(seg)
    if not seg then
        return nil
    end
    if seg.get_selected_candidate then
        local ok, cand = pcall(function()
            return seg:get_selected_candidate()
        end)
        if ok then
            return cand
        end
    end
    if seg.menu then
        return seg.menu:get_candidate_at(seg.selected_index or 0)
    end
    return nil
end

local function sync_native_capture(env)
    local capture = get_capture(env.engine)
    if not capture or not capture.native_started then
        return false
    end

    local ctx = env.engine.context
    local input_length = #(ctx.input or "")
    if input_length < (capture.native_input_length or 0) then
        capture.native_confirmed_end = 0
    end
    capture.native_input_length = input_length
    local confirmed_end = capture.native_confirmed_end or 0
    local query = ""
    local changed = false
    for _, seg in ipairs(composition_segments(ctx)) do
        local start_pos = segment_start(seg)
        local end_pos = segment_end(seg)
        if is_selected_segment(seg) and end_pos > confirmed_end then
            local cand = segment_selected_candidate(seg)
            local text = genuine_text(cand)
            if text ~= "" and not is_status_candidate(cand) then
                capture.text = capture.text .. text
                capture.native_confirmed_end = end_pos
                confirmed_end = end_pos
                changed = true
            end
        elseif not is_selected_segment(seg) and end_pos > start_pos then
            query = (ctx.input or ""):sub(start_pos + 1, end_pos)
        end
    end
    capture.query = query
    update_prompt(env)
    return changed
end

local function refresh_context(ctx)
    if ctx.refresh_non_confirmed_composition then
        ctx:refresh_non_confirmed_composition()
    end
end

local function show_capture_context(env)
    local capture = get_capture(env.engine)
    if not capture then
        return
    end
    local ctx = env.engine.context
    local query = capture.query or ""
    capture.native_started = false
    capture.native_confirmed_end = 0
    capture.native_input_length = 0
    ctx:clear()
    ctx.input = query ~= "" and query or capture.code
    capture.query = query
    capture.native_started = query ~= ""
    refresh_context(ctx)
    update_prompt(env)
end

local function clear_capture(env)
    local capture = get_capture(env.engine)
    if capture then
        local context = env.engine.context
        if context:get_option("ascii_mode") ~= capture.original_ascii_mode then
            context:set_option("ascii_mode", capture.original_ascii_mode)
        end
        if capture.original_auto_commit ~= nil
            and context:get_option("_auto_commit") ~= capture.original_auto_commit then
            context:set_option("_auto_commit", capture.original_auto_commit)
        end
    end
    clear_engine_capture(env.engine)
end

local function enter_capture(env, operation, default_text, target_code)
    local code = target_code or current_code(env)
    if code == "" then
        return false
    end
    set_capture(env.engine, {
        code = code,
        text = default_text or "",
        query = "",
        operation = operation or "add",
        message = "",
        original_ascii_mode = env.engine.context:get_option("ascii_mode"),
        original_auto_commit = env.engine.context:get_option("_auto_commit"),
        shift_key = nil,
        shift_used = false,
        native_started = false,
        native_confirmed_end = 0,
        native_input_length = 0,
    })
    if get_capture(env.engine).original_auto_commit then
        env.engine.context:set_option("_auto_commit", false)
    end
    show_capture_context(env)
    return true
end

local function finish_capture(env)
    local capture = get_capture(env.engine)
    if not capture then
        return false
    end
    if capture.text == "" then
        capture.message = "请先选择或输入要加入的词"
        refresh_context(env.engine.context)
        update_prompt(env)
        return false
    end
    local code = capture.code
    local text = capture.text
    local call_ok, saved = pcall(function()
        if capture.operation == "disable" then
            return persist_disable(env.state, code, text)
        end
        return persist_weight(env.state, code, text, env.state.config.weight_base, {
            added = true,
            source = "shortcut",
            ordered = false,
        })
    end)
    if not call_ok or not saved then
        capture.message = "保存失败，请重试"
        refresh_context(env.engine.context)
        update_prompt(env)
        return false
    end
    clear_capture(env)
    local ctx = env.engine.context
    ctx:clear()
    ctx.input = code
    refresh_context(ctx)
    return true
end

local function append_capture_text(env, text)
    local capture = get_capture(env.engine)
    if not capture or text == "" then
        return false
    end
    capture.text = capture.text .. text
    capture.query = ""
    capture.message = ""
    show_capture_context(env)
    return true
end

local function append_capture_input(env, ch)
    local capture = get_capture(env.engine)
    if not capture or not ch or ch == "" then
        return false
    end
    capture.query = (capture.query or "") .. ch
    capture.message = ""
    show_capture_context(env)
    return true
end

local function capture_backspace(env)
    local capture = get_capture(env.engine)
    if capture.query and capture.query ~= "" then
        capture.query = capture.query:sub(1, -2)
    else
        capture.text = remove_last_utf8_char(capture.text)
    end
    capture.message = ""
    show_capture_context(env)
end

local function candidate_at(env, index)
    local seg = current_segment(env)
    if not seg or not seg.menu then
        return nil
    end
    seg.menu:prepare(index + 1)
    return seg.menu:get_candidate_at(index)
end

local function capture_selection(env, keycode)
    local capture = get_capture(env.engine)
    if not capture or not capture.query or capture.query == "" then
        return false
    end
    local seg = current_segment(env)
    if not seg then
        return false
    end
    local index = seg.selected_index or 0
    if keycode ~= KEY.SPACE then
        index = capture_input.selection_index(
            index,
            keycode,
            env.engine.schema.page_size or 5,
            env.select_keys
        )
        if index == nil then
            return false
        end
    end
    local cand = candidate_at(env, index)
    while is_status_candidate(cand) do
        index = index + 1
        cand = candidate_at(env, index)
    end
    return append_capture_text(env, genuine_text(cand))
end

local function visible_page_candidates(env)
    local seg = current_segment(env)
    if not seg or not seg.menu then
        return nil
    end
    local page_size = env.engine.schema.page_size or 5
    local selected = seg.selected_index or 0
    local page_start = math.floor(selected / page_size) * page_size
    local items = {}
    for i = 0, page_size - 1 do
        local absolute = page_start + i
        local cand = candidate_at(env, absolute)
        if cand then
            table.insert(items, {
                index = absolute,
                text = genuine_text(cand),
            })
        end
    end
    return items, selected, page_start
end

local function apply_visible_order(env, items, code)
    local profile = env.state.config
    local user_added = env.state.added[code] or {}
    if not env.state.store then
        return false
    end
    local records = env.state.store:query(code)
    if not records then
        return false
    end
    for i, item in ipairs(items) do
        local weight = profile.weight_base - (i - 1) * profile.weight_step
        local record = copy_db_record(records[item.text])
        record.added = user_added[item.text] == true
        record.hidden = false
        record.weight = weight
        record.updated_at = os.time()
        record.ordered = true
        if not env.state.store:write(code, item.text, record) then
            return false
        end
        env.state.weights[code] = env.state.weights[code] or {}
        env.state.weights[code][item.text] = weight
        env.state.ordered[code] = env.state.ordered[code] or {}
        env.state.ordered[code][item.text] = true
    end
    return true
end

local function select_moved_candidate(env, moved, fallback_index)
    local seg = current_segment(env)
    if not seg or not seg.menu then
        return false
    end
    local page_size = env.engine.schema.page_size or 5
    local page_start = math.floor(fallback_index / page_size) * page_size
    for offset = 0, page_size - 1 do
        local index = page_start + offset
        local cand = candidate_at(env, index)
        if cand and genuine_text(cand) == moved.text then
            seg.selected_index = index
            return true
        end
    end
    seg.selected_index = fallback_index
    return false
end

local function move_selected(env, direction)
    local code = current_code(env)
    if code == "" then
        return false
    end
    local items, selected = visible_page_candidates(env)
    if not items or #items == 0 then
        return false
    end

    local rel = nil
    for i, item in ipairs(items) do
        if item.index == selected then
            rel = i
            break
        end
    end
    if not rel then
        return false
    end

    local target = rel
    if direction == "front" then
        target = 1
    elseif direction == "back" then
        target = #items
    elseif direction == "prev" then
        target = math.max(1, rel - 1)
    elseif direction == "next" then
        target = math.min(#items, rel + 1)
    end
    if target == rel then
        return true
    end

    local moved = table.remove(items, rel)
    table.insert(items, target, moved)
    if not apply_visible_order(env, items, code) then
        return false
    end
    env.engine.context:refresh_non_confirmed_composition()
    select_moved_candidate(env, moved, items[target].index)
    return true
end

local function disable_selected(env)
    local code = current_code(env)
    local cand = selected_candidate(env)
    local text = genuine_text(cand)
    local candidate_type = cand and cand.type or ""
    if cand and cand.get_genuine then
        local ok, genuine = pcall(function()
            return cand:get_genuine()
        end)
        if ok and genuine and genuine.type then
            candidate_type = genuine.type
        end
    end
    if code == "" or text == "" then
        return false
    end
    local records = env.state.store and env.state.store:query(code)
    local existing = records and records[text]
    if existing and existing.source == "shortcut" and existing.added then
        local record = copy_db_record(existing)
        record.added = false
        record.source = nil
        record.ordered = false
        record.weight = 0
        record.hidden = candidate_type ~= "tiger_user_word"
        record.updated_at = os.time()
        if not env.state.store:write(code, text, record) then
            return false
        end
        ensure_code(env.state, code)
        env.state.added[code][text] = nil
        env.state.weights[code][text] = nil
        env.state.ordered[code][text] = nil
        env.state.blocked[code][text] = record.hidden and true or nil
    elseif not persist_disable(env.state, code, text) then
        return false
    end
    env.engine.context:refresh_non_confirmed_composition()
    local seg = current_segment(env)
    if seg then
        seg.prompt = "〔已删除「" .. text .. "」；Ctrl+Shift+M 可管理和恢复〕"
    end
    return true
end

local function management_code(env)
    return env.engine.context:get_property(management_code_property) or ""
end

local function set_management_code(env, code)
    local next_code = code or ""
    if management_code(env) ~= next_code then
        env.engine.context:set_property(management_code_property, next_code)
    end
end

local function is_management_shortcut(key_event)
    local keycode = key_event.keycode
    return key_event:ctrl() and key_event:shift()
        and not key_event:alt() and not key_event:super() and not key_event:release()
        and (keycode == 0x4d or keycode == 0x6d)
end

local function is_delete_shortcut(key_event)
    if key_event.keycode ~= KEY.DELETE or key_event:release() or key_event:alt() or key_event:super() then
        return false
    end
    return (key_event:shift() and not key_event:ctrl())
        or (key_event:ctrl() and not key_event:shift())
end

local function is_reorder_shortcut(key_event)
    local ctrl_only = key_event:ctrl() and not key_event:alt() and not key_event:shift() and not key_event:release()
    local ctrl_option = key_event:ctrl() and key_event:alt() and not key_event:shift() and not key_event:release()
    return ctrl_only or ctrl_option
end

local function handle_reorder_shortcut(keycode, env)
    if keycode == KEY.UP or keycode == KEY.LEFT then
        return move_selected(env, "prev") and kAccepted or kNoop
    elseif keycode == KEY.DOWN or keycode == KEY.RIGHT then
        return move_selected(env, "next") and kAccepted or kNoop
    elseif keycode == KEY.HOME then
        return move_selected(env, "front") and kAccepted or kNoop
    elseif keycode == KEY.END then
        return move_selected(env, "back") and kAccepted or kNoop
    -- 与魔虎调序同键位：Ctrl+减号上移、Ctrl+等号/加号下移（魔虎 pin/
    -- override 两层共用这对键，虎码方案保持同一肌肉记忆；1.16 方向键
    -- 兼容性不稳时这也是稳妥替代）。
    elseif keycode == KEY.MINUS then
        return move_selected(env, "prev") and kAccepted or kNoop
    elseif keycode == KEY.EQUAL or keycode == KEY.PLUS then
        return move_selected(env, "next") and kAccepted or kNoop
    end
    return kNoop
end

local processor = {}

function processor.init(env)
    local profile = profile_for_env(env)
    env.state = profile and get_state(profile) or nil
    if not env.state then
        return
    end
    local config = env.engine.schema.config
    env.select_keys = config and config.get_string
        and config:get_string("menu/alternative_select_keys") or "1234567890"
    if Component and Component.Processor then
        local ok, speller = pcall(Component.Processor, env.engine, "", "speller")
        if ok then
            env.capture_speller = speller
        end
    end
    env.update_notifier = env.engine.context.update_notifier:connect(function()
        sync_native_capture(env)
        update_prompt(env)
    end)
    env.select_notifier = env.engine.context.select_notifier:connect(function()
        sync_native_capture(env)
        update_prompt(env)
    end)
    if env.engine.context.commit_notifier then
        env.commit_notifier = env.engine.context.commit_notifier:connect(function()
            set_management_code(env, "")
        end)
    end
end

function processor.fini(env)
    clear_capture(env)
    set_management_code(env, "")
    if env.update_notifier then
        env.update_notifier:disconnect()
    end
    if env.select_notifier then
        env.select_notifier:disconnect()
    end
    if env.commit_notifier then
        env.commit_notifier:disconnect()
    end
end

function processor.func(key_event, env)
    if not env.state then
        return kNoop
    end
    local keycode = key_event.keycode

    local capture = get_capture(env.engine)
    if capture then
        local ctx = env.engine.context
        local has_shortcut_modifier = key_event:ctrl() or key_event:alt() or key_event:super()
        if capture_input.is_shift_key(keycode) then
            if has_shortcut_modifier then
                return kAccepted
            end
            if key_event:release() then
                if capture.shift_key == keycode then
                    if not capture.shift_used then
                        ctx:set_option("ascii_mode", not ctx:get_option("ascii_mode"))
                        refresh_context(ctx)
                        update_prompt(env)
                    end
                    capture.shift_key = nil
                    capture.shift_used = false
                end
            else
                if capture.shift_key == nil then
                    capture.shift_key = keycode
                    capture.shift_used = false
                elseif capture.shift_key ~= keycode then
                    capture.shift_used = true
                end
            end
            return kAccepted
        elseif key_event:release() then
            return kAccepted
        end

        if capture.shift_key then
            capture.shift_used = true
        end

        local is_edit_key = keycode == KEY.RETURN or keycode == KEY.KP_RETURN
            or keycode == KEY.ESCAPE or keycode == KEY.BACKSPACE
        if is_edit_key and (has_shortcut_modifier or key_event:shift()) then
            return kAccepted
        elseif keycode == KEY.RETURN or keycode == KEY.KP_RETURN then
            finish_capture(env)
            return kAccepted
        elseif keycode == KEY.ESCAPE then
            clear_capture(env)
            ctx:clear()
            return kAccepted
        elseif keycode == KEY.BACKSPACE then
            capture_backspace(env)
            return kAccepted
        elseif has_shortcut_modifier or capture_input.is_modifier_key(keycode) then
            return kAccepted
        end

        local action, ch = capture_input.classify(
            keycode,
            ctx:get_option("ascii_mode"),
            capture.query ~= "",
            env.select_keys,
            key_event:shift()
        )
        if action == "select" then
            capture_selection(env, keycode)
            return kAccepted
        elseif not ctx:get_option("ascii_mode") and not env.capture_speller
            and not key_event:shift() and capture_input.is_printable_keysym(keycode) then
            capture.message = "当前环境不支持原生取词"
            refresh_context(ctx)
            update_prompt(env)
            return kAccepted
        elseif not ctx:get_option("ascii_mode") and env.capture_speller
            and not key_event:shift() and capture_input.is_printable_keysym(keycode) then
            local started_here = not capture.native_started
            if started_here then
                capture.native_confirmed_end = 0
                capture.query = ""
                ctx:clear()
                capture.native_started = true
            end
            local result = env.capture_speller:process_key_event(key_event)
            if result == kAccepted then
                capture.message = ""
                sync_native_capture(env)
                return kAccepted
            end
            if started_here then
                show_capture_context(env)
            end
            if action == "query" then
                return kAccepted
            end
        elseif action == "query" then
            append_capture_input(env, ch)
            return kAccepted
        end
        if action == "literal" then
            append_capture_text(env, capture_input.literal_char(
                capture.text,
                ch,
                ctx:get_option("ascii_mode"),
                ctx:get_option("ascii_punct"),
                ctx:get_option("full_shape")
            ))
            return kAccepted
        elseif action == "consume" then
            return kAccepted
        end
        update_prompt(env)
        if not key_event:shift() and (
            keycode == KEY.LEFT or keycode == KEY.RIGHT or keycode == KEY.UP or keycode == KEY.DOWN
            or keycode == KEY.HOME or keycode == KEY.END
            or keycode == KEY.PAGE_UP or keycode == KEY.PAGE_DOWN
        ) then
            return kNoop
        end
        return kAccepted
    end

    if is_management_shortcut(key_event) then
        local code = current_code(env)
        if code == "" then
            return kNoop
        end
        if management_code(env) == code then
            set_management_code(env, "")
        else
            set_management_code(env, code)
        end
        refresh_context(env.engine.context)
        return kAccepted
    end

    if not key_event:ctrl() and not key_event:alt() and not key_event:shift() and not key_event:release() then
        local ctx = env.engine.context
        if keycode == KEY.BACKSLASH and add_trigger.can_append(ctx.input) then
            ctx:push_input("\\")
            return kAccepted
        elseif keycode == KEY.SPACE then
            local code = add_trigger.target_code(ctx.input)
            if code then
                return enter_capture(env, "add", nil, code) and kAccepted or kNoop
            end
        end
    end

    local managed_code = management_code(env)
    if managed_code ~= "" and managed_code ~= current_code(env) then
        set_management_code(env, "")
        managed_code = ""
    end
    if managed_code ~= "" and keycode == KEY.ESCAPE and not key_event:ctrl()
        and not key_event:alt() and not key_event:shift() and not key_event:super()
        and not key_event:release() then
        set_management_code(env, "")
        return kNoop
    end
    if managed_code ~= "" and is_delete_shortcut(key_event) then
        local text = current_selected_text(env)
        if restore_hidden(env.state.config.schema_id, managed_code, text) then
            refresh_context(env.engine.context)
            return kAccepted
        end
        local seg = current_segment(env)
        if seg then
            seg.prompt = "〔恢复失败：无法写入用户数据〕"
        end
        return kAccepted
    end

    if managed_code ~= "" and key_event:ctrl() and not key_event:shift()
        and not key_event:alt() and not key_event:super() and not key_event:release()
        and keycode == 0x30 then
        local reset, status = reset_code(env.state.config.schema_id, managed_code)
        if reset and status == "changed" then
            refresh_context(env.engine.context)
        elseif not reset and status == "partial" then
            refresh_context(env.engine.context)
        end
        local seg = current_segment(env)
        if seg then
            if reset and status == "changed" then
                seg.prompt = "〔已重置当前编码〕"
            elseif reset and status == "unchanged" then
                seg.prompt = "〔当前编码无需重置〕"
            elseif status == "partial" then
                seg.prompt = "〔重置未完全完成，已重新加载用户数据〕"
            else
                seg.prompt = "〔重置失败：无法写入用户数据〕"
            end
        end
        return kAccepted
    end

    if is_delete_shortcut(key_event) then
        return disable_selected(env) and kAccepted or kNoop
    end

    if is_reorder_shortcut(key_event) then
        return handle_reorder_shortcut(keycode, env)
    end

    return kNoop
end

local filter = {}

function filter.init(env)
    local profile = profile_for_env(env)
    env.state = profile and get_state(profile) or nil
end

local function capture_status_candidate(env, capture)
    local seg = current_segment(env)
    local start = seg and seg._start or 0
    local finish = seg and seg._end or start
    return Candidate(
        "tiger_user_status",
        start,
        finish,
        capture_status_text(capture),
        capture_status_comment(capture)
    )
end

local function add_trigger_status_candidate(env, code)
    local seg = current_segment(env)
    local start = seg and seg._start or 0
    local finish = seg and seg._end or #env.engine.context.input
    return Candidate("tiger_user_status", start, finish, "加词 " .. code, "空格进入加词")
end

local function candidate_sort_key(state, code, text, fallback)
    local weight = state.weights[code] and state.weights[code][text]
    if weight and weight ~= 0 then
        return weight
    end
    return fallback
end

function filter.func(input, env)
    if not env.state then
        for cand in input:iter() do
            yield(cand)
        end
        return
    end

    local capture = get_capture(env.engine)
    local native_query_pending = capture and capture.native_started
        and (capture.native_confirmed_end or 0) < #(env.engine.context.input or "")
    if capture and not native_query_pending and (not capture.query or capture.query == "") then
        yield(capture_status_candidate(env, capture))
        return
    end

    local trigger_code = add_trigger.target_code(env.engine.context.input)
    if trigger_code then
        yield(add_trigger_status_candidate(env, trigger_code))
        return
    end

    local code = current_code(env)
    if code == "" then
        for cand in input:iter() do
            yield(cand)
        end
        return
    end

    local state = env.state
    local managed = management_code(env) == code
    local blocked = state.blocked[code] or {}
    local weights = state.weights[code] or {}
    local ordered = state.ordered[code] or {}
    local added = state.added[code] or {}
    local has_added_or_weight = next(added) ~= nil or next(weights) ~= nil

    if not has_added_or_weight and not managed then
        for cand in input:iter() do
            local text = genuine_text(cand)
            if not is_status_candidate(cand) and not blocked[text] then
                yield(cand)
            end
        end
        return
    end

    local seen = {}
    local rows = {}
    local index = 0

    for cand in input:iter() do
        local text = genuine_text(cand)
        if not is_status_candidate(cand) and (not blocked[text] or managed) then
            index = index + 1
            seen[text] = true
            local output = cand
            if managed and (blocked[text] or ordered[text]) then
                local comments = {}
                if blocked[text] then
                    table.insert(comments, "已隐藏 · Delete 恢复")
                end
                if ordered[text] then
                    table.insert(comments, "手动调序")
                end
                local comment = table.concat(comments, " · ")
                if ShadowCandidate then
                    local genuine = cand
                    if cand.get_genuine then
                        local ok, resolved = pcall(function()
                            return cand:get_genuine()
                        end)
                        if ok and resolved then
                            genuine = resolved
                        end
                    end
                    output = ShadowCandidate(genuine, "tiger_user_management", cand.text, comment)
                else
                    output = Candidate("tiger_user_management", cand.start or 0, cand._end or #code, cand.text, comment)
                end
            end
            table.insert(rows, {
                cand = output,
                text = text,
                index = index,
                score = candidate_sort_key(state, code, text, 0 - index),
            })
        end
    end

    for text, _ in pairs(added) do
        if not seen[text] and not blocked[text] then
            index = index + 1
            local seg = current_segment(env)
            local start = seg and seg._start or 0
            local finish = seg and seg._end or #code
            local cand = Candidate("tiger_user_word", start, finish, text, "")
            cand.quality = candidate_sort_key(state, code, text, 0 - index)
            table.insert(rows, {
                cand = cand,
                text = text,
                index = index,
                score = cand.quality,
            })
        end
    end

    if managed then
        for text, _ in pairs(blocked) do
            if not seen[text] then
                index = index + 1
                local seg = current_segment(env)
                local start = seg and seg._start or 0
                local finish = seg and seg._end or #code
                local cand = Candidate(
                    "tiger_user_management",
                    start,
                    finish,
                    text,
                    "已隐藏 · Delete 恢复"
                )
                table.insert(rows, {
                    cand = cand,
                    text = text,
                    index = index,
                    score = candidate_sort_key(state, code, text, 0 - index),
                })
            end
        end
    end

    table.sort(rows, function(a, b)
        if a.score ~= b.score then
            return a.score > b.score
        end
        return a.index < b.index
    end)

    for _, row in ipairs(rows) do
        yield(row.cand)
    end
end

local function export_snapshot(schema_id)
    local profile = PROFILES[schema_id]
    if not profile then
        return nil
    end

    local db = acquire_db(profile.db_name)
    if not db then
        return nil, "用户词数据库不可用"
    end

    local rows = db:list_all()
    if not rows then
        return nil, "用户词数据库不可用"
    end

    local snapshot = {}
    for _, row in ipairs(rows) do
        table.insert(snapshot, {
            code = row.code,
            text = row.text,
            added = row.record.added,
            hidden = row.record.hidden,
            weight = row.record.weight,
            source = row.record.source,
            ordered = row.record.ordered,
        })
    end
    return snapshot
end

local function manager_snapshot(schema_id)
    local profile = PROFILES[schema_id]
    if not profile then
        return nil
    end
    local db = acquire_db(profile.db_name)
    if not db then
        return nil, "用户词数据库不可用"
    end
    local rows = db:list_all()
    if not rows then
        return nil, "用户词数据库不可用"
    end
    local snapshot = {}
    for _, row in ipairs(rows) do
        table.insert(snapshot, {
            code = row.code,
            text = row.text,
            added = row.record.added,
            hidden = row.record.hidden,
            weight = row.record.weight,
            source = row.record.source,
            ordered = row.record.ordered,
            updated_at = row.record.updated_at,
        })
    end
    return snapshot
end

local function state_for_schema(schema_id)
    local profile = PROFILES[schema_id]
    if not profile then
        return nil
    end
    return get_state(profile)
end

local function mutate_record(schema_id, code, text, mutate)
    local state = state_for_schema(schema_id)
    if not state or not state.store or code == "" or text == "" then
        return false
    end
    local records = state.store:query(code)
    if not records or not records[text] then
        return false
    end
    local record = copy_db_record(records[text])
    if mutate(record) == false then
        return false
    end
    record.updated_at = os.time()
    if not state.store:write(code, text, record) then
        return false
    end
    ensure_code(state, code)
    state.added[code][text] = record.added and true or nil
    state.blocked[code][text] = record.hidden and true or nil
    state.weights[code][text] = record.weight and record.weight ~= 0 and record.weight or nil
    state.ordered[code][text] = record.ordered and true or nil
    return true
end

restore_hidden = function(schema_id, code, text)
    return mutate_record(schema_id, code, text, function(record)
        if not record.hidden then
            return false
        end
        record.hidden = false
    end)
end

local function clear_order(schema_id, code, text)
    return mutate_record(schema_id, code, text, function(record)
        if not record.ordered then
            return false
        end
        record.ordered = false
        record.weight = record.added and PROFILES[schema_id].weight_base or 0
    end)
end

local function remove_shortcut(schema_id, code, text)
    return mutate_record(schema_id, code, text, function(record)
        if record.source ~= "shortcut" or not record.added then
            return false
        end
        record.added = false
        record.source = nil
        record.ordered = false
        record.weight = 0
    end)
end

reset_code = function(schema_id, code)
    local state = state_for_schema(schema_id)
    if not state or not state.store or code == "" then
        return false, "failed"
    end
    local records = state.store:query(code)
    if not records then
        return false, "failed"
    end
    local changes = {}
    for text, original in pairs(records) do
        if original.hidden or original.ordered then
            local record = copy_db_record(original)
            record.hidden = false
            record.ordered = false
            record.weight = record.added and state.config.weight_base or 0
            record.updated_at = os.time()
            table.insert(changes, {
                text = text,
                original = copy_db_record(original),
                updated = record,
            })
        end
    end
    if #changes == 0 then
        return true, "unchanged"
    end
    table.sort(changes, function(a, b)
        return a.text < b.text
    end)

    local applied = {}
    for _, change in ipairs(changes) do
        if not state.store:write(code, change.text, change.updated) then
            local rollback_failed = false
            for index = #applied, 1, -1 do
                local rollback = applied[index]
                if not state.store:write(code, rollback.text, rollback.original) then
                    rollback_failed = true
                    log.error("tiger_user_words: failed to roll back reset for " .. code .. "/" .. rollback.text)
                end
            end
            if rollback_failed then
                local reloaded = load_state(state.config)
                state.store = reloaded.store
                state.added = reloaded.added
                state.blocked = reloaded.blocked
                state.weights = reloaded.weights
                state.ordered = reloaded.ordered
                state.generated_loaded = reloaded.generated_loaded
                return false, "partial"
            end
            return false, "failed"
        end
        table.insert(applied, change)
    end

    ensure_code(state, code)
    for _, change in ipairs(changes) do
        state.blocked[code][change.text] = nil
        state.weights[code][change.text] = change.updated.added and change.updated.weight or nil
        state.ordered[code][change.text] = nil
    end
    return true, "changed"
end

return {
    processor = processor,
    filter = filter,
    export_snapshot = export_snapshot,
    manager_snapshot = manager_snapshot,
    restore_hidden = restore_hidden,
    clear_order = clear_order,
    remove_shortcut = remove_shortcut,
    reset_code = reset_code,
    _test = {
        get_capture = get_capture,
    },
}
