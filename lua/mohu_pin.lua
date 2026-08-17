-- mohu_pin.lua
-- version: 0.1.5
-- author: kuroame
-- license: GPLv3
-- You may copy, distribute and modify the software as long as you track
-- changes/dates in source files. Any modifications to or software including
-- (via compiler) GPL-licensed code must also be made available under the GPL
-- along with build & install instructions.

-- changelog
-- 0.1.5: query only the current segment instead of the whole input
-- 0.1.4: make commit counter always start from 0
-- 0.1.3: use C-- and C-= (C-+) to reorder candidates
-- 0.1.2: add freestyle mode, add switch to enable/disable pin
-- 0.1.1: simple configuration
-- 0.1.0: init

local mohu = require("mohu")

local function remove_last_utf8_char(text)
    if text == nil or text == "" then
        return ""
    end
    local start = #text
    while start > 1 do
        local byte = text:byte(start)
        if byte < 0x80 or byte > 0xbf then
            break
        end
        start = start - 1
    end
    return text:sub(1, start - 1)
end

local function new_capture(code)
    return {
        code = code,
        text = "",
        query = "",
        mode = "lookup",
        message = "",
        shift_key = nil,
        shift_used = false,
    }
end

local function append_query(capture, text)
    capture.query = capture.query .. text
    capture.message = ""
end

local function append_text(capture, text)
    capture.text = capture.text .. text
    capture.query = ""
    capture.message = ""
end

local function capture_backspace(capture)
    if capture.query ~= "" then
        capture.query = remove_last_utf8_char(capture.query)
    else
        capture.text = remove_last_utf8_char(capture.text)
    end
    capture.message = ""
end

-- userdb
-- 将用户的pin记录存储在userdb中
local user_db = {}
local sep_t = " \t"
-- epoch : 2024/11/11 00:00 in min
local epoch = 28854240
local ref_count = 0
local pin_db = nil
function user_db.release()
    if ref_count <= 0 then
        return
    end
    ref_count = ref_count - 1
    if ref_count == 0 and pin_db ~= nil then
        collectgarbage()
        if pin_db ~= nil then
            pcall(function()
                if pin_db:loaded() then
                    pin_db:close()
                end
            end)
        end
        pin_db = nil
    end
end

function user_db.acquire()
    if ref_count == 0 then
        local create_ok, db = pcall(LevelDb, "mohu_pin")
        if not create_ok or db == nil then
            pin_db = nil
            return false
        end
        pin_db = db
        local open_ok, loaded = pcall(function()
            if not pin_db:loaded() then
                pin_db:open()
            end
            return pin_db:loaded()
        end)
        if not open_ok or not loaded then
            pcall(function()
                if pin_db:loaded() then
                    pin_db:close()
                end
            end)
            pin_db = nil
            return false
        end
    end
    ref_count = ref_count + 1
    return true
end

---@param input string
---@return function iterator
function user_db.query_and_unpack(input)
    local res = pin_db:query(input .. sep_t)
    local function iter()
        if not res then return nil end
        local next_func, self = res:iter()
        return function()
            while true do
                local key, value = next_func(self)
                if key == nil then
                    return nil
                end
                local entry = user_db.unpack_entry(key, value)
                if entry ~= nil then
                    return entry
                end
            end
        end
    end
    return iter()
end

function user_db.query_and_unpack_as_list(input)
    local res = user_db.query_and_unpack(input)
    if res == nil then
        return {}
    end
    local ret = {}
    for entry in res do
        table.insert(ret, entry)
    end
    return ret
end

function user_db.timestamp_now()
    return math.floor((os.time()) / 60) - epoch
end

---@param n string weight/output commits
---@param m string timestamp in min from epoch
---@return str encoded commits
function user_db.encode(n, m)
    local n_prime = n + 8 -- move the range to [0, 15]
    if n >= 0 then
        return m * 16 + n_prime
    else
        return -(m * 16 + n_prime)
    end
end

---@param x string encoded commits
---@return n string weight/output commits
---@return m string timestamp in min from epoch
function user_db.decode(x)
    local n, m
    if x >= 0 then
        m = math.floor(x / 16)
        n = (x % 16) - 8
    else
        local x_abs = -x
        m = math.floor(x_abs / 16)
        n = (x_abs % 16) - 8
    end
    return n, m
end

---@param key string
---@param value string
---@return table|nil
function user_db.unpack_entry(key, value)
    local result = {}

    local code, phrase = key:match("^(.-)%s+(.+)$")
    if code and phrase then
        result.code = code
        result.phrase = phrase
    else
        return nil
    end

    local commits, dee, tick, source = 0, 0.0, 0, "legacy"
    for k, v in value:gmatch("(%a+)=(%S+)") do
        if k == "c" then
            commits = tonumber(v) or 0
        elseif k == "d" then
            dee = math.min(10000.0, tonumber(v) or 0.0)
        elseif k == "t" then
            tick = tonumber(v) or 0
        elseif k == "s" and (v == "pin" or v == "panacea" or v == "freestyle") then
            source = v
        end
    end
    local output_commits, timestamp = user_db.decode(commits)

    -- just neglect tombstoned entries
    if output_commits < 0 then
        return nil
    end

    result.raw_commits = commits
    result.commits = output_commits
    result.timestamp = timestamp
    result.dee = dee
    result.tick = tick
    result.source = source

    return result
end

---@param input string
---@param cand_text string
function user_db.toggle_pin_status(input, cand_text, source)
    source = source or "pin"
    local pinned_res = pin_db:query(input .. sep_t)
    if pinned_res ~= nil then
        local key = input .. sep_t .. cand_text
        local max_commits = -1
        for k, v in pinned_res:iter() do
            local unpacked = user_db.unpack_entry(k, v)
            if unpacked then
                -- found existing entry here
                if key == k then
                    -- if it's an active one, set its commit counter to -1 to tombstone it
                    if unpacked.commits >= 0 then
                        user_db.tombstone(key, unpacked.source)
                        -- good to leave now
                        return
                    end
                end
                max_commits = math.max(max_commits, unpacked.commits)
            end
        end

        -- commit counter ranges from 0 to 7 (minus one is considered as tombstoned)
        if max_commits >= 7 then
            -- whoops, maximum reached, we need to rearrange the commit counter from 0 to 7
            -- if there's no vacancy, we need to tombstone the one with a minimum commit counter (most unimportant one) to make room for the new one
            user_db.rearrange(input, cand_text, source)
        else
            -- nothing much to worry, upsert the new entry here
            user_db.upsert(key, max_commits + 1, source)
        end
    end
end

---@param input string
---@param cand_text string
---@param source string|nil
---@return boolean ok
---@return boolean inserted
function user_db.ensure_pin(input, cand_text, source)
    source = source or "panacea"
    if pin_db == nil then
        return false, false
    end

    local ok, pinned_res = pcall(function()
        return pin_db:query(input .. sep_t)
    end)
    if not ok then
        return false, false
    end

    local max_commits = -1
    if pinned_res ~= nil then
        local iter_ok, iter_result = pcall(function()
            for key, value in pinned_res:iter() do
                local unpacked = user_db.unpack_entry(key, value)
                if unpacked ~= nil then
                    if unpacked.phrase == cand_text then
                        return { exists = true }
                    end
                    max_commits = math.max(max_commits, unpacked.commits)
                end
            end
            return { exists = false }
        end)
        if not iter_ok then
            return false, false
        end
        if iter_result.exists then
            return true, false
        end
    end

    local key = input .. sep_t .. cand_text
    if max_commits >= 7 then
        local write_ok = user_db.rearrange(input, cand_text, source)
        return write_ok, write_ok
    end
    local write_ok = user_db.upsert(key, max_commits + 1, source)
    return write_ok, write_ok
end

function user_db.rearrange(input, cand_text, source)
    source = source or "pin"
    local pinned_res = pin_db:query(input .. sep_t)
    local key = input .. sep_t .. cand_text
    local entries = {}
    local original_values = {}
    local active_entries = {}
    local max_commits = -1
    local min_commits = math.huge
    local min_key = nil

    -- traverse all entries to find the one with the minimum commit counter
    for k, v in pinned_res:iter() do
        original_values[k] = v
        local unpacked = user_db.unpack_entry(k, v)
        if unpacked then
            entries[k] = unpacked
            if unpacked.commits >= 0 then
                table.insert(active_entries, { key = k, entry = unpacked })
                if unpacked.commits > max_commits then
                    max_commits = unpacked.commits
                end
                if unpacked.commits < min_commits then
                    min_commits = unpacked.commits
                    min_key = k
                end
            end
        end
    end

    local updates = {}

    -- check if we need to delete the one with the minimum commit counter
    if #active_entries >= 8 then
        if not entries[key] or entries[key].commits < 0 then
            table.insert(updates, { key = min_key, commits = -1, source = entries[min_key].source })
            entries[min_key].commits = -1
            for i, item in ipairs(active_entries) do
                if item.key == min_key then
                    table.remove(active_entries, i)
                    break
                end
            end
        else
            -- the key already exists, hence no need to tombstone any entry
        end
    end

    local new_commits = 0
    for _, item in ipairs(active_entries) do
        local k = item.key
        local entry = item.entry
        if k ~= key then
            table.insert(updates, { key = k, commits = new_commits, source = entry.source })
            new_commits = new_commits + 1
        end
    end

    -- upsert the new entry
    table.insert(updates, { key = key, commits = new_commits, source = source })
    return user_db.apply_updates(updates, original_values)
end

function user_db.dump_raw()
    local res = pin_db:query("")
    local function iter()
        local next_func, self = res:iter()
        return function()
            while true do
                local key, value = next_func(self)
                if key == nil then
                    return nil
                end
                return key, value
            end
        end
    end
    return iter()
end

function user_db.list_all()
    local entries = {}
    local raw = user_db.dump_raw()
    if raw == nil then
        return entries
    end
    for key, value in raw do
        local entry = user_db.unpack_entry(key, value)
        if entry ~= nil then
            table.insert(entries, entry)
        end
    end
    table.sort(entries, function(a, b)
        if a.code ~= b.code then
            return a.code < b.code
        end
        if a.commits ~= b.commits then
            return a.commits > b.commits
        end
        return a.phrase < b.phrase
    end)
    return entries
end

function user_db.upsert(key, output_commits, source)
    local encoded_commit = user_db.encode(output_commits, user_db.timestamp_now())
    local source_field = source and (" s=" .. source) or ""
    local ok, result = pcall(function()
        return pin_db:update(key, "c=" .. encoded_commit .. " d=0 t=1" .. source_field)
    end)
    return ok and result ~= false
end

function user_db.apply_updates(updates, original_values)
    local applied = {}
    for _, update in ipairs(updates) do
        if not user_db.upsert(update.key, update.commits, update.source) then
            for index = #applied, 1, -1 do
                local applied_key = applied[index]
                local original = original_values[applied_key]
                pcall(function()
                    if original ~= nil then
                        pin_db:update(applied_key, original)
                    elseif pin_db.erase ~= nil then
                        pin_db:erase(applied_key)
                    end
                end)
            end
            return false
        end
        table.insert(applied, update.key)
    end
    return true
end

function user_db.upsert_many(entries)
    local max_commit = #entries
    for i = 1, max_commit do
        local key = entries[i].code .. sep_t .. entries[i].phrase
        user_db.upsert(key, max_commit - i, entries[i].source)
    end
end

function user_db.tombstone(key, source)
    return user_db.upsert(key, -1, source)
end

function user_db.remove(input, cand_text)
    local key = input .. sep_t .. cand_text
    for entry in user_db.query_and_unpack(input) do
        if entry.code == input and entry.phrase == cand_text then
            return user_db.tombstone(key, entry.source)
        end
    end
    return false
end

-- | returns the index to the first element in @entries whose text is equal to @cand_text.
function user_db.find(entries, input, cand_text)
    for i = 1, #entries do
        if entries[i].phrase == cand_text then
            return i
        end
    end
    return nil
end

-- | returns the new position of the moved pinned candidate, or nil if no pinned candidate is found
function user_db.move_pin_up(input, cand_text)
    local entries = user_db.query_and_unpack_as_list(input)
    table.sort(entries, function(a, b) return a.commits > b.commits end)
    local j = user_db.find(entries, input, cand_text)
    if j == nil or j == 1 then
        return nil
    elseif j == 1 then
        return j
    end
    entries[j-1], entries[j] = entries[j], entries[j-1]
    user_db.upsert_many(entries)
    return j-1
end

-- | returns the new position of the moved pinned candidate, or nil if no pinned candidate is found
function user_db.move_pin_down(input, cand_text)
    local entries = user_db.query_and_unpack_as_list(input)
    table.sort(entries, function(a, b) return a.commits > b.commits end)
    local j = user_db.find(entries, input, cand_text)
    if j == nil then
        return nil
    elseif j == #entries then
        return j
    end
    entries[j], entries[j+1] = entries[j+1], entries[j]
    user_db.upsert_many(entries)
    return j+1
end

-- pin_processor
-- 处理置顶快捷键和引导式加词
local kAccepted = 1
local kNoop = 2
local KEY = {
    BACKSPACE = 0xff08,
    RETURN = 0xff0d,
    KP_RETURN = 0xff8d,
    ESCAPE = 0xff1b,
    SPACE = 0x20,
    APOSTROPHE = 0x27,
    SEMICOLON = 0x3b,
}

local keypad_chars = {
    [0xffaa] = "*",
    [0xffab] = "+",
    [0xffac] = ",",
    [0xffad] = "-",
    [0xffae] = ".",
    [0xffaf] = "/",
    [0xffbd] = "=",
}

local function keycode_to_char(keycode)
    if keycode >= 0x20 and keycode <= 0x7e then
        return string.char(keycode)
    end
    if keycode >= 0xffb0 and keycode <= 0xffb9 then
        return string.char(0x30 + keycode - 0xffb0)
    end
    return keypad_chars[keycode]
end

local function is_printable_keysym(keycode)
    return (keycode >= 0x20 and keycode <= 0x7e)
        or (keycode >= 0xa0 and keycode <= 0xfdff)
        or (keycode >= 0x01000100 and keycode <= 0x0110ffff)
end

local function is_modifier_key(keycode)
    return keycode >= 0xffe1 and keycode <= 0xffee
end

local function is_shift_key(keycode)
    return keycode == 0xffe1 or keycode == 0xffe2
end

local function is_selection_char(char, select_keys)
    if char == " " or char == ";" or char == "'" then
        return true
    end
    return select_keys:find(char, 1, true) ~= nil
end

local function selection_index(selected_index, keycode, page_size, select_keys)
    local page_start = math.floor(selected_index / page_size) * page_size
    local position = nil
    local char = keycode_to_char(keycode)
    if char == ";" then
        position = 2
    elseif char == "'" then
        position = 3
    elseif char ~= nil then
        position = select_keys:find(char, 1, true)
    end
    if position == nil or position > page_size then
        return nil
    end
    return page_start + position - 1
end

local function capture_key_action(capture, keycode, select_keys, shifted)
    local char = keycode_to_char(keycode)
    if char == nil then
        if is_printable_keysym(keycode) then
            return "consume", nil
        end
        return nil, nil
    end
    if shifted and char:match("^[a-z]$") then
        char = char:upper()
    end
    if capture.query ~= "" then
        if capture.query:sub(1, 1) == ";" then
            if char:match("^[a-z;]$") then
                return "quick_query", char
            end
            return "consume", char
        end
        if is_selection_char(char, select_keys) then
            return "select", char
        end
        if char:match("^[a-z]$") then
            return "query", char
        end
        return "consume", char
    end
    if char == ";" then
        return "quick_query", char
    end
    if capture.mode == "literal" then
        return "literal", char
    end
    if char:match("^[a-z]$") then
        return "query", char
    end
    return "literal", char
end

local capture_sessions = {}
local capture_token_counter = 0
local capture_token_prefix = "mohu_pin:" .. tostring(capture_sessions) .. ":"
local capture_token_property = "mohu_pin_capture_token"

local function capture_token(engine, create)
    local context = engine.context
    local token = context:get_property(capture_token_property)
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

local function clear_capture(engine)
    local token = capture_token(engine, false)
    if token ~= "" then
        capture_sessions[token] = nil
        engine.context:set_property(capture_token_property, "")
    end
end

local function genuine_candidate(cand)
    if cand == nil then
        return nil
    end
    local ok, genuine = pcall(function()
        return cand:get_genuine()
    end)
    if ok and genuine ~= nil then
        return genuine
    end
    return cand
end

local function genuine_text(cand)
    local genuine = genuine_candidate(cand)
    return genuine and genuine.text or ""
end

local function current_segment(env)
    local composition = env.engine.context.composition
    if composition == nil or composition:empty() then
        return nil
    end
    return composition:back()
end

local function refresh_context(context)
    if context.refresh_non_confirmed_composition then
        context:refresh_non_confirmed_composition()
    end
end

local function capture_status_text(capture)
    local text = capture.text ~= "" and capture.text or "未取词"
    return "加词 " .. capture.code .. "：" .. text
end

local function capture_status_comment(capture)
    if capture.message ~= "" then
        return capture.message
    end
    local mode = capture.mode == "literal" and "文本" or "选词"
    return "[" .. mode .. "]"
end

local function capture_shows_status_candidate(capture)
    return capture.query == ""
end

local function show_capture(env)
    local capture = get_capture(env.engine)
    if capture == nil then
        return
    end
    local context = env.engine.context
    context:clear()
    context.input = capture.query ~= "" and capture.query or capture.code
    refresh_context(context)
end

local function enter_capture(env, code)
    if code == nil or code == "" then
        return false
    end
    set_capture(env.engine, new_capture(code))
    show_capture(env)
    return true
end

local function finish_capture(env)
    local capture = get_capture(env.engine)
    if capture == nil then
        return false
    end
    if capture.text == "" then
        capture.message = "请先选取要加入的词"
        refresh_context(env.engine.context)
        return false
    end

    local call_ok, ok = pcall(user_db.ensure_pin, capture.code, capture.text, "freestyle")
    if not call_ok or not ok then
        capture.message = "保存失败，请重试"
        refresh_context(env.engine.context)
        return false
    end

    local code = capture.code
    clear_capture(env.engine)
    local context = env.engine.context
    context:clear()
    context.input = code
    refresh_context(context)
    return true
end

local function candidate_at(env, index)
    local segment = current_segment(env)
    if segment == nil or segment.menu == nil then
        return nil
    end
    segment.menu:prepare(index + 1)
    return segment.menu:get_candidate_at(index)
end

local function capture_selection(env, keycode)
    local capture = get_capture(env.engine)
    if capture == nil or capture.query == "" then
        return false
    end
    local segment = current_segment(env)
    if segment == nil then
        return false
    end

    local selected_index = segment.selected_index or 0
    local index = selected_index
    local page_size = env.engine.schema.page_size or 5
    if keycode ~= KEY.SPACE then
        index = selection_index(selected_index, keycode, page_size, env.select_keys)
        if index == nil then
            return false
        end
    end

    local cand = candidate_at(env, index)
    while cand ~= nil and cand.type == "mohu_capture_status" do
        index = index + 1
        cand = candidate_at(env, index)
    end
    local text = genuine_text(cand)
    if text == "" then
        capture.message = "没有可选候选"
        refresh_context(env.engine.context)
        return false
    end
    append_text(capture, text)
    show_capture(env)
    return true
end

local function append_capture_input(env, char)
    append_query(get_capture(env.engine), char)
    show_capture(env)
    return true
end

local chinese_half_shape_punctuation = {
    [","] = "，",
    ["."] = "。",
    ["<"] = "《",
    [">"] = "》",
    ["/"] = "、",
    ["?"] = "？",
    ["!"] = "！",
    [":"] = "：",
    ["\\"] = "、",
    ["|"] = "·",
    ["$"] = "￥",
    ["^"] = "……",
    ["("] = "（",
    [")"] = "）",
    ["_"] = "——",
    ["["] = "「",
    ["]"] = "」",
    ["{"] = "『",
    ["}"] = "』",
}

local chinese_full_shape_punctuation = {
    [" "] = "　",
    [","] = "，",
    ["."] = "。",
    ["<"] = "《",
    [">"] = "》",
    ["/"] = "／",
    ["?"] = "？",
    ["!"] = "！",
    [":"] = "：",
    ["\\"] = "、",
    ["|"] = "·",
    ["`"] = "｀",
    ["~"] = "～",
    ["@"] = "＠",
    ["#"] = "＃",
    ["%"] = "％",
    ["$"] = "￥",
    ["^"] = "……",
    ["&"] = "＆",
    ["*"] = "＊",
    ["("] = "（",
    [")"] = "）",
    ["-"] = "－",
    ["_"] = "——",
    ["+"] = "＋",
    ["["] = "「",
    ["]"] = "」",
    ["{"] = "『",
    ["}"] = "』",
}

local punctuation_pairs = {
    ["'"] = { "‘", "’" },
    ['"'] = { "“", "”" },
}

local function count_plain(text, needle)
    local count = 0
    local start = 1
    while true do
        local found = text:find(needle, start, true)
        if found == nil then
            return count
        end
        count = count + 1
        start = found + #needle
    end
end

local function capture_literal_char(capture, env, char)
    if env.engine.context:get_option("ascii_punct") then
        return char
    end
    local pair = punctuation_pairs[char]
    if pair ~= nil then
        local pair_count = count_plain(capture.text, pair[1]) + count_plain(capture.text, pair[2])
        return pair[pair_count % 2 + 1]
    end
    local punctuation = env.engine.context:get_option("full_shape")
        and chinese_full_shape_punctuation or chinese_half_shape_punctuation
    return punctuation[char] or char
end

local function toggle_capture_mode(env)
    local capture = get_capture(env.engine)
    if capture == nil then
        return false
    end
    capture.mode = capture.mode == "literal" and "lookup" or "literal"
    capture.query = ""
    capture.message = ""
    show_capture(env)
    return true
end

local function is_plain_key(key_event)
    return not key_event:ctrl() and not key_event:alt() and not key_event:super()
        and not key_event:release()
end

local pin_processor = {}

function pin_processor.init(env)
    env.pin_enable = env.engine.schema.config:get_bool("mohu/pin/enable") or false
    if not env.pin_enable then
        return
    end
    env.infix = env.engine.schema.config:get_string("mohu/pin/panacea/infix") or "//"
    env.freestyle = env.engine.schema.config:get_bool("mohu/pin/panacea/freestyle") or false
    env.select_keys = env.engine.schema.config:get_string("menu/alternative_select_keys") or "1234567890"
    env.pin_acquired = user_db.acquire()
    if not env.pin_acquired then
        env.pin_enable = false
    end
end

function pin_processor.fini(env)
    if not env.pin_enable then
        return
    end
    clear_capture(env.engine)
    if env.pin_acquired then
        user_db.release()
    end
end

-- Highlight the candidate at position @index.
function pin_processor.highlight_index(env, index)
    local comp = env.engine.context.composition
    comp:back().selected_index = index
end

function pin_processor.func(key_event, env)
    if not env.pin_enable then
        return kNoop
    end

    local keycode = key_event.keycode
    local capture = get_capture(env.engine)
    if capture ~= nil then
        if is_shift_key(keycode) then
            local has_shortcut_modifier = key_event:ctrl() or key_event:alt() or key_event:super()
            if has_shortcut_modifier then
                return kAccepted
            end
            if key_event:release() then
                if capture.shift_key == keycode then
                    if not capture.shift_used then
                        toggle_capture_mode(env)
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

        if capture.shift_key ~= nil then
            capture.shift_used = true
        end

        if key_event:ctrl() and not key_event:alt() and not key_event:shift()
            and not key_event:super() and keycode == KEY.SEMICOLON then
            toggle_capture_mode(env)
            return kAccepted
        end

        local has_shortcut_modifier = key_event:ctrl() or key_event:alt() or key_event:super()
        local is_edit_key = keycode == KEY.RETURN or keycode == KEY.KP_RETURN
            or keycode == KEY.ESCAPE or keycode == KEY.BACKSPACE
        if is_edit_key and (has_shortcut_modifier or key_event:shift()) then
            return kAccepted
        elseif keycode == KEY.RETURN or keycode == KEY.KP_RETURN then
            finish_capture(env)
            return kAccepted
        elseif keycode == KEY.ESCAPE then
            clear_capture(env.engine)
            env.engine.context:clear()
            return kAccepted
        elseif keycode == KEY.BACKSPACE then
            capture_backspace(capture)
            show_capture(env)
            return kAccepted
        elseif has_shortcut_modifier then
            return kAccepted
        elseif is_modifier_key(keycode) then
            return kAccepted
        end

        local action, char = capture_key_action(capture, keycode, env.select_keys, key_event:shift())
        if action == "select" then
            capture_selection(env, keycode)
            return kAccepted
        elseif action == "query" or action == "quick_query" then
            append_capture_input(env, char)
            local current = get_capture(env.engine)
            if action == "quick_query" and #current.query > 1 then
                capture_selection(env, KEY.SPACE)
            end
            return kAccepted
        elseif action == "literal" then
            append_text(capture, capture_literal_char(capture, env, char))
            show_capture(env)
            return kAccepted
        elseif action == "consume" then
            return kAccepted
        end
        return kNoop
    end

    local context = env.engine.context
    local input = context.input

    if env.freestyle and key_event:ctrl() and not key_event:alt() and not key_event:shift() and
        not key_event:release() and keycode == KEY.SEMICOLON then
        return enter_capture(env, input) and kAccepted or kNoop
    end

    if env.freestyle and is_plain_key(key_event) and keycode == KEY.SPACE and
        #input > #env.infix and input:sub(-#env.infix) == env.infix then
        local cand = genuine_candidate(context:get_selected_candidate())
        if cand ~= nil and cand.type == "pin_tip" then
            local code = input:sub(1, #input - #env.infix)
            return enter_capture(env, code) and kAccepted or kNoop
        end
    end

    -- ctrl + x to trigger
    if not key_event:ctrl() or key_event:release() then
        return kNoop
    end

    local cand = context:get_selected_candidate()
    if cand == nil then
        return kNoop
    end
    local text = cand.text
    -- 1) Special-case pure Chinese candidates: the text could be
    -- output from OpenCC, so pin the genuine candidate instead to
    -- preserve word frequency.
    --
    -- 2) If we know for sure this is a pinned candidate, always
    -- retrieve the genuine candidate to correctly delete it.
    if cand.type == 'pinned' or mohu.str_is_chinese(text) then
        text = genuine_text(cand)
    end

    -- + t
    if keycode == 0x74 then
        user_db.toggle_pin_status(input, text, "pin")
        context:refresh_non_confirmed_composition()
        -- + -, prioritize the current pinned candidate
    elseif keycode == 0x2d then
        -- If text is not from a pinned candidate, do nothing.
        local idx = user_db.move_pin_up(input, text)
        context:refresh_non_confirmed_composition()
        if idx ~= nil then
            pin_processor.highlight_index(env, idx-1)
        end
        -- + =, + +, deprioritize the current pinned candidate
    elseif keycode == 0x3d or keycode == 0x2b then
        -- If text is not from a pinned candidate, do nothing.
        local idx = user_db.move_pin_down(input, text)
        context:refresh_non_confirmed_composition()
        if idx ~= nil then
            pin_processor.highlight_index(env, idx-1)
        end
        -- + a
    elseif keycode == 0x61 then
        -- todo: add quick code
        return kNoop
    else
        return kNoop
    end
    return kAccepted
end

-- pin_filter
-- 从pin记录中读取候选项，并将其插入到候选列表的最前面
local pin_filter = {}

function pin_filter.init(env)
    env.pin_enable = env.engine.schema.config:get_bool("mohu/pin/enable") or false
    if not env.pin_enable then
        return
    end
    env.indicator = env.engine.schema.config:get_string("mohu/pin/indicator") or "📌"
    env.prompt = env.engine.schema.config:get_string("mohu/pin/panacea/prompt") or "〔加词〕"
    env.pin_acquired = user_db.acquire()
    if not env.pin_acquired then
        env.pin_enable = false
    end
end

function pin_filter.fini(env)
    if not env.pin_enable then
        return
    end
    if env.pin_acquired then
        user_db.release()
    end
end

function pin_filter.func(t_input, env)
    if env.pin_enable then
        local context = env.engine.context
        local composition = context.composition
        local segment = composition:back()
        local capture = get_capture(env.engine)
        if capture ~= nil then
            segment.prompt = env.prompt .. " | " .. capture_status_text(capture)
            if capture_shows_status_candidate(capture) then
                local status = Candidate(
                    "mohu_capture_status",
                    segment._start,
                    segment._end,
                    capture_status_text(capture),
                    capture_status_comment(capture)
                )
                status.preedit = capture.code
                status.quality = math.huge
                yield(status)
                return
            end
        end
        local input = context.input:sub(segment._start + 1, segment._end)
        local commits = {}
        local entries = user_db.query_and_unpack(input)
        if entries then
            for unpacked in entries do
                table.insert(commits, unpacked)
            end
        end
        -- descending sort
        table.sort(commits, function(a, b)
                       return a.commits > b.commits
        end)
        for _, unpacked in ipairs(commits) do
            local cand = Candidate("pinned", segment._start, segment._end, unpacked.phrase, env.indicator)
            cand.preedit = input
            yield(cand)
        end
    end
    for cand in t_input:iter() do
        yield(cand)
    end
end

-- panacea_translator
-- 基于pin功能 以 编码[infix]词 的形式触发，灵活造词
local panacea_translator = {}

function panacea_translator.init(env)
    env.pin_enable = env.engine.schema.config:get_bool("mohu/pin/enable") or false
    if not env.pin_enable then
        return
    end
    env.infix = env.engine.schema.config:get_string("mohu/pin/panacea/infix") or '//'
    env.escaped_infix = string.gsub(env.infix, "([%^%$%(%)%%%.%[%]%*%+%-%?])", "%%%1")
    env.prompt = env.engine.schema.config:get_string("mohu/pin/panacea/prompt") or "〔加词〕"
    env.indicator = env.engine.schema.config:get_string("mohu/pin/indicator") or "📌"
    env.pin_acquired = user_db.acquire()
    if not env.pin_acquired then
        env.pin_enable = false
        return
    end
    local pattern = string.format("(.+)%s(.+)", env.escaped_infix)
    local function on_commit(ctx)
        local commit_text = ctx:get_commit_text()
        if mohu.str_is_chinese(commit_text) then
            local segmentation = ctx.composition:toSegmentation()
            local segs = segmentation:get_segments()
            local genuine_text = ""
            local ok = true
            for _, seg in pairs(segs) do
                local c = seg:get_selected_candidate()
                if c == nil then
                    ok = false
                    break
                end
                local g = c:get_genuine()
                genuine_text = genuine_text .. g.text
            end
            if ok then
                commit_text = genuine_text
            end
        end

        local code, original_code = ctx.input:match(pattern)
        if original_code and original_code ~= "" and
            code and code ~= "" and
            commit_text and commit_text ~= "" then
            user_db.ensure_pin(code, commit_text, "panacea")
        end
    end

    env.commit_notifier = env.engine.context.commit_notifier:connect(on_commit)
end

function panacea_translator.fini(env)
    if not env.pin_enable then
        return
    end
    env.commit_notifier:disconnect()
    if env.pin_acquired then
        user_db.release()
    end
end

function panacea_translator.func(input, seg, env)
    if not env.pin_enable then
        return
    end

    local commits = {}
    local entries = user_db.query_and_unpack(input)
    if entries then
        for unpacked in entries do
            table.insert(commits, unpacked)
        end
    end
    table.sort(commits, function(a, b)
        return a.commits > b.commits
    end)
    for _, unpacked in ipairs(commits) do
        local cand = Candidate("pinned", seg.start, seg._end, unpacked.phrase, env.indicator)
        cand.preedit = input
        cand.quality = math.huge
        yield(cand)
    end

    local pattern = "[a-zA-Z]+" .. env.escaped_infix
    local match = input:match(pattern)

    if match then
        local comment = "开始加词➕" .. env.indicator
        local tip_cand = Candidate("pin_tip", 0, #match, "", comment)
        tip_cand.quality = math.huge
        yield(tip_cand)
    end
end

return {
    pin_filter = pin_filter,
    pin_processor = pin_processor,
    panacea_translator = panacea_translator,
    pin_store = user_db,
    _test = {
        user_db = user_db,
        new_capture = new_capture,
        append_query = append_query,
        append_text = append_text,
        backspace = capture_backspace,
        acquire = user_db.acquire,
        release = user_db.release,
        ensure_pin = user_db.ensure_pin,
        query = user_db.query_and_unpack_as_list,
        set_capture = set_capture,
        get_capture = get_capture,
        clear_capture = clear_capture,
        capture_status_text = capture_status_text,
        capture_status_comment = capture_status_comment,
        capture_shows_status_candidate = capture_shows_status_candidate,
        selection_index = selection_index,
        keycode_to_char = keycode_to_char,
        is_printable_keysym = is_printable_keysym,
        capture_key_action = capture_key_action,
    },
}

-- Local Variables:
-- lua-indent-level: 4
-- End:
