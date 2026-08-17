-- Mohu candidate override support.
-- Stores per-code ordering and soft-deletion preferences outside source dictionaries.

local M = {}

local kAccepted = 1
local kNoop = 2
local sep = " \t"
local pools = {}
local deleted_user_phrases = {}

local function normalize_code(code)
    return (code or ""):gsub(";[^%s]+", ""):gsub("%s+", "")
end

local function genuine_candidate(cand)
    if cand.get_genuine then
        return cand:get_genuine()
    end
    return cand
end

local function genuine_text(cand)
    return genuine_candidate(cand).text
end

local function record_applies(cand)
    local cand_type = genuine_candidate(cand).type
    return cand_type ~= "mohu_capture_status"
end

local function parse_record(value)
    if type(value) ~= "string" then
        return nil
    end

    local fields = {}
    for key, field_value in value:gmatch("(%a+)=(%-?%d+)") do
        fields[key] = tonumber(field_value)
    end
    if fields.v ~= 1 or (fields.h ~= 0 and fields.h ~= 1) then
        return nil
    end
    if fields.r == nil or fields.t == nil then
        return nil
    end
    return {
        hidden = fields.h == 1,
        rank = fields.r,
        origin = fields.o or -1,
        tick = fields.t,
        deleted_user_commit = fields.d or -1,
    }
end

local function encode_record(record)
    local value = string.format(
        "v=1 h=%d r=%d o=%d t=%d",
        record.hidden and 1 or 0,
        record.rank,
        record.origin,
        record.tick
    )
    if type(record.deleted_user_commit) == "number" and record.deleted_user_commit >= 0 then
        value = value .. " d=" .. tostring(record.deleted_user_commit)
    end
    return value
end

local function is_pinned(cand, pin_indicator)
    return cand.type == "pinned" or (pin_indicator ~= nil and cand.comment == pin_indicator)
end

local function order_entries(candidates, records, management, pin_indicator)
    local status = {}
    local pinned = {}
    local ranked = {}
    local ordinary = {}
    local seen = {}

    for index, cand in ipairs(candidates) do
        local text = genuine_text(cand)
        if not seen[text] then
            seen[text] = true
            local record = record_applies(cand) and records[text] or nil
            local entry = {
                cand = cand,
                hidden = record ~= nil and record.hidden or false,
                reordered = record ~= nil and record.rank >= 0 or false,
                upstream_index = index,
                rank = record ~= nil and record.rank or -1,
                tick = record ~= nil and record.tick or 0,
            }

            if not entry.hidden or management then
                if genuine_candidate(cand).type == "mohu_capture_status" then
                    table.insert(status, entry)
                elseif is_pinned(cand, pin_indicator) then
                    table.insert(pinned, entry)
                elseif entry.reordered then
                    table.insert(ranked, entry)
                else
                    table.insert(ordinary, entry)
                end
            end
        end
    end

    table.sort(ranked, function(a, b)
        if a.rank ~= b.rank then
            return a.rank < b.rank
        end
        if a.tick ~= b.tick then
            return a.tick > b.tick
        end
        return a.upstream_index < b.upstream_index
    end)

    local result = {}
    for _, group in ipairs({ status, pinned, ranked, ordinary }) do
        for _, entry in ipairs(group) do
            table.insert(result, entry)
        end
    end
    return result
end

local function sorted_ranked_texts(records, upstream_texts)
    local ranked = {}
    local upstream_index = {}
    for index, text in ipairs(upstream_texts or {}) do
        if upstream_index[text] == nil then
            upstream_index[text] = index
        end
    end
    for text, record in pairs(records) do
        if record.rank >= 0 then
            table.insert(ranked, { text = text, rank = record.rank, tick = record.tick })
        end
    end
    table.sort(ranked, function(a, b)
        if a.rank ~= b.rank then
            return a.rank < b.rank
        end
        if a.tick ~= b.tick then
            return a.tick > b.tick
        end
        local a_index = upstream_index[a.text] or math.huge
        local b_index = upstream_index[b.text] or math.huge
        if a_index ~= b_index then
            return a_index < b_index
        end
        return a.text < b.text
    end)
    return ranked
end

local function find_text(texts, wanted)
    for index, text in ipairs(texts) do
        if text == wanted then
            return index
        end
    end
    return nil
end

local function merge_visible_order(visible_texts, records)
    local result = {}
    local present = {}
    for _, item in ipairs(sorted_ranked_texts(records, visible_texts)) do
        if not present[item.text] then
            present[item.text] = true
            table.insert(result, item.text)
        end
    end

    local previous = nil
    for visible_index, text in ipairs(visible_texts) do
        if not present[text] then
            local insert_at = nil
            if previous ~= nil then
                insert_at = find_text(result, previous) + 1
            else
                for next_index = visible_index + 1, #visible_texts do
                    local next_position = find_text(result, visible_texts[next_index])
                    if next_position ~= nil then
                        insert_at = next_position
                        break
                    end
                end
            end
            table.insert(result, insert_at or (#result + 1), text)
            present[text] = true
        end
        previous = text
    end
    return result
end

local function swap_texts(texts, left, right)
    local left_index = find_text(texts, left)
    local right_index = find_text(texts, right)
    if left_index == nil or right_index == nil then
        return false
    end
    texts[left_index], texts[right_index] = texts[right_index], texts[left_index]
    return true
end

local Store = {}
Store.__index = Store

local function close_pool(pool)
    collectgarbage()
    pcall(function()
        if pool.db:loaded() then
            pool.db:close()
        end
    end)
end

local function acquire_store(name)
    local pool = pools[name]
    if pool == nil then
        local ok, db = pcall(LevelDb, name)
        if not ok or db == nil then
            return nil
        end
        ok = pcall(function()
            if not db:loaded() then
                db:open()
            end
        end)
        local loaded_ok, loaded = pcall(function()
            return db:loaded()
        end)
        if not ok or not loaded_ok or not loaded then
            close_pool({ db = db })
            return nil
        end
        pool = { db = db, refs = 0, failed = false }
        pools[name] = pool
    elseif pool.failed then
        return nil
    end
    pool.refs = pool.refs + 1
    return setmetatable({ name = name, pool = pool }, Store)
end

function Store:release()
    local pool = self.pool
    if pool == nil then
        return
    end
    pool.refs = pool.refs - 1
    if pool.refs <= 0 then
        close_pool(pool)
        pools[self.name] = nil
    end
    self.pool = nil
end

function Store:available()
    return self.pool ~= nil and not self.pool.failed
end

function Store:refresh()
    if not self:available() then
        return false
    end
    close_pool(self.pool)
    local ok = pcall(function() self.pool.db:open() end)
    local loaded_ok, loaded = pcall(function() return self.pool.db:loaded() end)
    if not ok or not loaded_ok or not loaded then
        self:fail()
        return false
    end
    return true
end

function Store:fail()
    if self.pool ~= nil then
        self.pool.failed = true
    end
end

function Store:query(code)
    local records = {}
    if not self:available() then
        return nil
    end
    local prefix = code .. sep
    local ok = pcall(function()
        local accessor = self.pool.db:query(prefix)
        if accessor == nil then
            return
        end
        for key, value in accessor:iter() do
            local record = parse_record(value)
            if record ~= nil and key:sub(1, #prefix) == prefix then
                records[key:sub(#prefix + 1)] = record
            end
        end
    end)
    if not ok then
        self:fail()
        return nil
    end
    return records
end

function Store:list_all()
    local records = {}
    if not self:available() then
        return nil
    end
    local ok = pcall(function()
        local accessor = self.pool.db:query("")
        if accessor == nil then
            return
        end
        for key, value in accessor:iter() do
            local split = key:find(sep, 1, true)
            local record = parse_record(value)
            if split ~= nil and record ~= nil and (record.hidden or record.rank >= 0) then
                record.code = key:sub(1, split - 1)
                record.text = key:sub(split + #sep)
                table.insert(records, record)
            end
        end
    end)
    if not ok then
        self:fail()
        return nil
    end
    table.sort(records, function(a, b)
        if a.code ~= b.code then
            return a.code < b.code
        end
        return a.text < b.text
    end)
    return records
end

local function copy_record(record)
    if record == nil then
        return { hidden = false, rank = -1, origin = -1, tick = 0 }
    end
    return {
        hidden = record.hidden,
        rank = record.rank,
        origin = record.origin or -1,
        tick = record.tick,
        deleted_user_commit = record.deleted_user_commit or -1,
    }
end

function Store:apply_updates(code, updates, originals)
    if not self:available() then
        return false
    end
    local applied = {}
    for _, update in ipairs(updates) do
        local ok, result = pcall(function()
            return self.pool.db:update(code .. sep .. update.text, encode_record(update.record))
        end)
        if not ok or result == false then
            for index = #applied, 1, -1 do
                local text = applied[index]
                local original = copy_record(originals[text])
                pcall(function()
                    self.pool.db:update(code .. sep .. text, encode_record(original))
                end)
            end
            self:fail()
            return false
        end
        table.insert(applied, update.text)
    end
    return true
end

function Store:set_hidden(code, text, hidden)
    local records = self:query(code)
    if records == nil then
        return false
    end
    local record = copy_record(records[text])
    record.hidden = hidden
    record.tick = os.time()
    return self:apply_updates(code, { { text = text, record = record } }, records)
end

function Store:write_order(code, texts, baseline_texts)
    local records = self:query(code)
    if records == nil then
        return false
    end
    local baseline_positions = {}
    for index, text in ipairs(baseline_texts or texts) do
        baseline_positions[text] = index - 1
    end
    local origins = {}
    local restored = true
    local previous_origin = -1
    for index, text in ipairs(texts) do
        local record = records[text]
        local origin = record ~= nil and record.origin or -1
        if origin == nil or origin < 0 then
            origin = baseline_positions[text] or (index - 1)
        end
        origins[text] = origin
        if origin <= previous_origin then
            restored = false
        end
        previous_origin = origin
    end
    local tick = os.time()
    local updates = {}
    for index, text in ipairs(texts) do
        local record = copy_record(records[text])
        record.rank = restored and -1 or (index - 1)
        record.origin = restored and -1 or origins[text]
        record.tick = tick
        table.insert(updates, { text = text, record = record })
    end
    return self:apply_updates(code, updates, records)
end

function Store:clear_rank(code, text)
    local records = self:query(code)
    if records == nil then
        return false
    end
    local record = records[text]
    if record == nil or record.rank < 0 then
        return true
    end
    local cleared = copy_record(record)
    cleared.rank = -1
    cleared.origin = -1
    cleared.tick = os.time()
    return self:apply_updates(code, { { text = text, record = cleared } }, records)
end

function Store:clear_code(code)
    local records = self:query(code)
    if records == nil then
        return false
    end
    local tick = os.time()
    local updates = {}
    for text, record in pairs(records) do
        if record.hidden or record.rank >= 0 then
            local cleared = copy_record(record)
            cleared.hidden = false
            cleared.rank = -1
            cleared.origin = -1
            cleared.tick = tick
            table.insert(updates, { text = text, record = cleared })
        end
    end
    return self:apply_updates(code, updates, records)
end

function Store:set_user_deleted(code, text, commit_count)
    code = normalize_code(code)
    local records = self:query(code)
    if records == nil then
        return false
    end
    local record = copy_record(records[text])
    record.hidden = false
    record.rank = -1
    record.origin = -1
    record.deleted_user_commit = tonumber(commit_count) or -1
    record.tick = os.time()
    return self:apply_updates(code, { { text = text, record = record } }, records)
end

function Store:is_user_deleted(code, text, commit_count)
    code = normalize_code(code)
    local records = self:query(code)
    if records == nil then
        return false
    end
    local record = records[text]
    if record == nil or (record.deleted_user_commit or -1) < 0 then
        return false
    end
    return type(commit_count) ~= "number" or commit_count <= record.deleted_user_commit
end

local lexical_types = {
    completion = true,
    mohu_reordered = true,
    phrase = true,
    sentence = true,
    table = true,
    user_phrase = true,
}

local function is_orderable(cand, env)
    if is_pinned(cand, env.override_pin_indicator) then
        return false
    end
    return record_applies(cand) and lexical_types[genuine_candidate(cand).type] == true
end

local function is_soft_deletable(cand)
    if cand.type == "pinned" then
        return true
    end
    local cand_type = genuine_candidate(cand).type
    return lexical_types[cand_type] == true
end

local function entry_code(entry)
    if type(entry.custom_code) ~= "string" then
        return ""
    end
    return (entry.custom_code:gsub("%s+$", ""))
end

local function deleted_user_key(code, text)
    return normalize_code(code) .. "\t" .. text
end

local function mark_user_deleted(code, text, commit_count)
    local key = deleted_user_key(code, text)
    deleted_user_phrases[key] = tonumber(commit_count) or 0
end

local function is_user_deleted(code, text, commit_count)
    local key = deleted_user_key(code, text)
    local deleted_at = deleted_user_phrases[key]
    if deleted_at == nil then
        return false
    end
    if type(commit_count) == "number" and commit_count > deleted_at then
        deleted_user_phrases[key] = nil
        return false
    end
    return true
end

local deleted_user_property = "mohu_user_deleted_phrases"

local function deleted_user_id(code, text)
    return (deleted_user_key(code, text):gsub(".", function(char)
        return string.format("%02x", string.byte(char))
    end))
end

local function deleted_user_option(code, text, commit_count)
    return "mohu_user_deleted_"
        .. deleted_user_id(code, text)
        .. "_"
        .. tostring(tonumber(commit_count) or 0)
end

local function context_deleted_users(context)
    local result = {}
    local ok, value = pcall(function()
        return context:get_property(deleted_user_property)
    end)
    if not ok or type(value) ~= "string" then
        return result
    end
    for id, commit_count in value:gmatch("([0-9a-f]+)=(%-?%d+)") do
        result[id] = tonumber(commit_count)
    end
    return result
end

local function write_context_deleted_users(context, records)
    local rows = {}
    for id, commit_count in pairs(records) do
        table.insert(rows, id .. "=" .. tostring(commit_count))
    end
    table.sort(rows)
    context:set_property(deleted_user_property, table.concat(rows, "\n"))
end

local function mark_context_user_deleted(context, code, text, commit_count)
    if context == nil then
        return
    end
    if context.set_option ~= nil then
        pcall(function()
            context:set_option(deleted_user_option(code, text, commit_count), true)
        end)
    end
    if context.set_property == nil then
        return
    end
    pcall(function()
        local records = context_deleted_users(context)
        records[deleted_user_id(code, text)] = tonumber(commit_count) or 0
        write_context_deleted_users(context, records)
    end)
end

local function is_context_user_deleted(context, code, text, commit_count)
    if context == nil then
        return false
    end
    if context.get_option ~= nil then
        local ok, deleted = pcall(function()
            return context:get_option(deleted_user_option(code, text, commit_count))
        end)
        if ok and deleted then
            return true
        end
    end
    if context.get_property == nil then
        return false
    end
    local records = context_deleted_users(context)
    local id = deleted_user_id(code, text)
    local deleted_at = records[id]
    if deleted_at == nil then
        return false
    end
    if type(commit_count) == "number" and commit_count > deleted_at then
        records[id] = nil
        pcall(function() write_context_deleted_users(context, records) end)
        return false
    end
    return true
end

local function is_builtin(memory, code, text)
    local function lookup(candidate_code)
        local ok, iterator = pcall(function()
            return memory:dictiter_lookup(candidate_code, false, 0)
        end)
        if not ok or iterator == nil then
            return false
        end
        for entry in iterator:iter() do
            if entry.text == text then
                return true
            end
        end
        return false
    end
    if lookup(code) then
        return true
    end
    local normalized = normalize_code(code)
    return normalized ~= code and lookup(normalized)
end

local function user_created_entry(memory, cand, code)
    local genuine = genuine_candidate(cand)
    if memory == nil or genuine.type ~= "user_phrase" then
        return nil
    end
    local normalized = normalize_code(code)
    local text = genuine_text(cand)
    local entry_ok, selected_entry = pcall(function()
        return genuine.entry
    end)
    if entry_ok and selected_entry ~= nil then
        local stored_code = entry_code(selected_entry)
        if normalize_code(stored_code) == normalized and selected_entry.text == text then
            return not is_builtin(memory, stored_code, text) and selected_entry or nil
        end
    end
    local ok, found = pcall(function()
        return memory:user_lookup("", true)
    end)
    if not ok or not found then
        return nil
    end
    local created = nil
    ok = pcall(function()
        for entry in memory:iter_user() do
            local stored_code = entry_code(entry)
            if normalize_code(stored_code) == normalized and entry.text == text then
                created = not is_builtin(memory, stored_code, text) and entry or nil
                return
            end
        end
    end)
    return ok and created or nil
end

local function is_user_created(memory, cand, code)
    return user_created_entry(memory, cand, code) ~= nil
end

local function learned_builtin_entry(memory, cand, code)
    local genuine = genuine_candidate(cand)
    if memory == nil then
        return nil
    end
    local text = genuine_text(cand)
    local selected_code = ""
    local entry_ok, selected_entry = pcall(function()
        return genuine.entry
    end)
    if entry_ok and selected_entry ~= nil and selected_entry.text == text then
        selected_code = normalize_code(entry_code(selected_entry))
    end
    local input_code = normalize_code(code)
    local ok, found = pcall(function()
        return memory:user_lookup("", true)
    end)
    if not ok or not found then
        return nil
    end
    local learned = nil
    ok = pcall(function()
        for entry in memory:iter_user() do
            local stored_code = entry_code(entry)
            local normalized = normalize_code(stored_code)
            local code_matches = selected_code ~= "" and normalized == selected_code
                or selected_code == "" and input_code ~= "" and normalized:sub(1, #input_code) == input_code
            local active = type(entry.commit_count) ~= "number" or entry.commit_count > 0
            if code_matches and active and entry.text == text and is_builtin(memory, stored_code, text) then
                learned = entry
                return
            end
        end
    end)
    return ok and learned or nil
end

local function current_segment(context)
    local composition = context.composition
    if composition:empty() then
        return nil, nil
    end
    local segment = composition:back()
    if segment == nil then
        return nil, nil
    end
    return segment, context.input:sub(segment._start + 1, segment._end)
end

local function set_prompt(context, message)
    local segment = current_segment(context)
    if segment ~= nil then
        segment.prompt = message
    end
end

local function refresh(context)
    context:refresh_non_confirmed_composition()
end

local function highlight_text(context, text, limit)
    local segment = current_segment(context)
    if segment == nil or segment.menu == nil then
        return
    end
    local count = segment.menu:prepare(limit)
    for index = 0, count - 1 do
        local cand = segment.menu:get_candidate_at(index)
        if cand ~= nil and genuine_text(cand) == text then
            segment.selected_index = index
            return
        end
    end
end

local function get_bool(config, path, default)
    local value = config:get_bool(path)
    if value == nil then
        return default
    end
    return value
end

local function configure(env)
    local config = env.engine.schema.config
    env.override_enable = get_bool(config, "mohu/candidate_override/enable", true)
    env.override_max_candidates = config:get_int("mohu/candidate_override/max_candidates") or 50
    env.override_max_candidates = math.max(1, env.override_max_candidates)
    env.override_management_option = "candidate_override_management"
    env.override_hidden_comment = config:get_string("mohu/candidate_override/hidden_comment") or "🗑 Shift+Delete 恢复"
    env.override_reordered_indicator = config:get_string("mohu/candidate_override/reordered_indicator") or "↕"
    env.override_pin_indicator = config:get_string("mohu/pin/indicator") or "📌"
    if env.override_enable then
        local db_name = config:get_string("mohu/candidate_override/db_name") or "mohu_candidate_override"
        env.override_store = acquire_store(db_name)
        env.override_enable = env.override_store ~= nil
    end
end

local function release(env)
    if env.override_store ~= nil then
        env.override_store:release()
        env.override_store = nil
    end
end

local processor = {}

local function refresh_override_memory(env)
    if env.override_memory ~= nil then
        pcall(function() env.override_memory:disconnect() end)
        env.override_memory = nil
    end
    if Memory == nil or env.override_memory_namespace == nil then
        return nil
    end
    local ok, memory = pcall(
        Memory,
        env.engine,
        env.engine.schema,
        env.override_memory_namespace
    )
    if ok then
        env.override_memory = memory
    end
    return env.override_memory
end

function processor.init(env)
    configure(env)
    if not env.override_enable then
        return
    end
    if Memory ~= nil then
        local config = env.engine.schema.config
        env.override_memory_namespace = config:get_string("mohu/candidate_manager/memory_namespace") or "translator"
        local ok, memory = pcall(Memory, env.engine, env.engine.schema, env.override_memory_namespace)
        if ok then
            env.override_memory = memory
        end
    end
    env.override_management_armed = false
    local function reset_management(context)
        env.override_management_armed = false
        if context:get_option(env.override_management_option) then
            context:set_option(env.override_management_option, false)
        end
    end
    env.override_commit_notifier = env.engine.context.commit_notifier:connect(reset_management)
    env.override_update_notifier = env.engine.context.update_notifier:connect(function(context)
        if context.composition:empty() then
            if not env.override_management_armed then
                reset_management(context)
            end
        else
            env.override_management_armed = false
        end
    end)
end

function processor.fini(env)
    if env.override_commit_notifier ~= nil then
        env.override_commit_notifier:disconnect()
    end
    if env.override_update_notifier ~= nil then
        env.override_update_notifier:disconnect()
    end
    if env.override_memory ~= nil then
        pcall(function() env.override_memory:disconnect() end)
        env.override_memory = nil
    end
    release(env)
end

local function toggle_management(context, env)
    local enabled = not context:get_option(env.override_management_option)
    local segment = current_segment(context)
    env.override_management_armed = enabled and segment == nil
    context:set_option(env.override_management_option, enabled)
    if segment ~= nil then
        refresh(context)
    end
    if enabled and segment ~= nil then
        set_prompt(context, "〔候选管理：选择已隐藏词，按 Shift+Delete 恢复〕")
    end
end

local function reorder_direction(key_event)
    if not key_event:ctrl() then
        return nil
    end
    local keycode = key_event.keycode
    if keycode == 0x2d or keycode == 0xffad then
        return -1
    end
    if keycode == 0x3d or keycode == 0x2b or keycode == 0xffab then
        return 1
    end
    local ok, repr = pcall(function()
        return key_event:repr()
    end)
    if not ok then
        return nil
    end
    if repr == "Control+minus" or repr == "Control+KP_Subtract" then
        return -1
    end
    if repr == "Control+equal" or repr == "Control+plus" or repr == "Control+KP_Add" then
        return 1
    end
    return nil
end

local function collect_ordinary(segment, existing_length, env)
    local menu = segment.menu
    local prepare_count = math.max(segment.selected_index + 2, existing_length + 8)
    prepare_count = math.min(prepare_count, env.override_max_candidates + 8)
    local count = menu:prepare(prepare_count)
    local entries = {}
    local selected_position = nil
    local selected_too_far = false

    for menu_index = 0, count - 1 do
        local cand = menu:get_candidate_at(menu_index)
        if cand ~= nil and is_orderable(cand, env) then
            if #entries < env.override_max_candidates then
                table.insert(entries, {
                    text = genuine_text(cand),
                    menu_index = menu_index,
                })
                if menu_index == segment.selected_index then
                    selected_position = #entries
                end
            elseif menu_index == segment.selected_index then
                selected_too_far = true
            end
        end
    end
    return entries, selected_position, selected_too_far
end

local function move_candidate(context, segment, code, direction, env)
    local selected = segment:get_selected_candidate()
    if selected == nil or is_pinned(selected, env.override_pin_indicator) or not is_orderable(selected, env) then
        return kNoop
    end

    local records = env.override_store:query(code)
    if records == nil then
        return kNoop
    end
    local existing_count = 0
    for _, record in pairs(records) do
        if record.rank >= 0 then
            existing_count = existing_count + 1
        end
    end
    local entries, selected_position, selected_too_far = collect_ordinary(segment, existing_count, env)
    if selected_too_far or selected_position == nil then
        set_prompt(context, "〔调序失败：超出管理范围〕")
        return kAccepted
    end
    local destination = selected_position + direction
    if destination < 1 or destination > #entries then
        return kAccepted
    end

    local prefix_length = math.max(selected_position, destination)
    prefix_length = math.min(prefix_length, #entries, env.override_max_candidates)
    local visible_texts = {}
    for index = 1, prefix_length do
        table.insert(visible_texts, entries[index].text)
    end
    local texts = merge_visible_order(visible_texts, records)
    local baseline_texts = {}
    for index, text in ipairs(texts) do
        baseline_texts[index] = text
    end
    if not swap_texts(texts, entries[selected_position].text, entries[destination].text) then
        return kNoop
    end
    if not env.override_store:write_order(code, texts, baseline_texts) then
        set_prompt(context, "〔调序失败：无法写入用户资料〕")
        return kAccepted
    end

    local selected_text = genuine_text(selected)
    refresh(context)
    highlight_text(context, selected_text, prefix_length + 8)
    if direction < 0 then
        set_prompt(context, "〔已上移「" .. selected_text .. "」〕")
    else
        set_prompt(context, "〔已下移「" .. selected_text .. "」〕")
    end
    return kAccepted
end

local function reset_learned_weight(context, segment, code, env)
    local selected = segment:get_selected_candidate()
    if selected == nil then
        return kNoop
    end
    local text = genuine_text(selected)
    local entry = learned_builtin_entry(env.override_memory, selected, code)
    if entry == nil then
        set_prompt(context, "〔当前候选没有可清除的学习权重〕")
        return kAccepted
    end
    local ok, reset = pcall(function()
        return context:delete_current_selection()
    end)
    if not ok or reset == false then
        set_prompt(context, "〔学习权重重置失败〕")
        return kAccepted
    end
    refresh(context)
    set_prompt(context, "〔已重置「" .. text .. "」的学习权重〕")
    return kAccepted
end

local function delete_or_restore(context, segment, code, env)
    local selected = segment:get_selected_candidate()
    if selected == nil then
        return kNoop
    end
    local text = genuine_text(selected)
    local records = env.override_store:query(code)
    if records == nil then
        return kNoop
    end
    local record = records[text]
    local management = context:get_option(env.override_management_option)

    local created_entry = user_created_entry(env.override_memory, selected, code)
    if created_entry ~= nil then
        local was_hidden = record ~= nil and record.hidden
        if was_hidden and not env.override_store:set_hidden(code, text, false) then
            set_prompt(context, "〔永久删除失败：无法清理隐藏记录〕")
            return kAccepted
        end
        local stored_code = normalize_code(entry_code(created_entry))
        if not env.override_store:set_user_deleted(stored_code, text, created_entry.commit_count) then
            if was_hidden then
                pcall(function() env.override_store:set_hidden(code, text, true) end)
            end
            set_prompt(context, "〔永久删除失败：无法记录删除状态〕")
            return kAccepted
        end
        local update_ok, updated = pcall(function()
            return env.override_memory:update_userdict(created_entry, -1, "")
        end)
        if not update_ok or updated == false then
            pcall(function() env.override_store:set_user_deleted(stored_code, text, -1) end)
            if was_hidden then
                pcall(function() env.override_store:set_hidden(code, text, true) end)
            end
            set_prompt(context, "〔永久删除失败：无法写入用户词典〕")
            return kAccepted
        end
        mark_user_deleted(entry_code(created_entry), text, created_entry.commit_count)
        mark_context_user_deleted(context, entry_code(created_entry), text, created_entry.commit_count)
        local ok, deleted = pcall(function()
            return context:delete_current_selection()
        end)
        if not ok or deleted == false then
            if was_hidden then
                pcall(function() env.override_store:set_hidden(code, text, true) end)
            end
            set_prompt(context, "〔永久删除失败：无法写入用户词典〕")
            return kAccepted
        end
        refresh_override_memory(env)
        refresh(context)
        set_prompt(context, "〔已永久删除「" .. text .. "」〕")
        return kAccepted
    end

    if management and record ~= nil and record.hidden then
        if env.override_store:set_hidden(code, text, false) then
            local prepare_count = math.min(segment.selected_index + 9, env.override_max_candidates + 8)
            refresh(context)
            highlight_text(context, text, prepare_count)
            set_prompt(context, "〔已恢复「" .. text .. "」〕")
        else
            set_prompt(context, "〔恢复失败：无法写入用户资料〕")
        end
        return kAccepted
    end
    if not is_soft_deletable(selected) then
        return kNoop
    end
    if env.override_store:set_hidden(code, text, true) then
        refresh(context)
        set_prompt(context, "〔已隐藏「" .. text .. "」；Ctrl+Shift+M 可管理和恢复〕")
    else
        set_prompt(context, "〔隐藏失败：无法写入用户资料〕")
    end
    return kAccepted
end

function processor.func(key_event, env)
    if not env.override_enable or not env.override_store:available() or key_event:release() then
        return kNoop
    end

    local context = env.engine.context
    local keycode = key_event.keycode
    if key_event:ctrl() and key_event:shift() and (keycode == 0x4d or keycode == 0x6d) then
        toggle_management(context, env)
        return kAccepted
    end

    local segment, code = current_segment(context)
    if segment == nil or segment.menu == nil then
        return kNoop
    end
    if key_event:ctrl() and keycode == 0x30 then
        if context:get_option(env.override_management_option) then
            if env.override_store:clear_code(code) then
                refresh(context)
                set_prompt(context, "〔已重置当前编码〕")
            else
                set_prompt(context, "〔重置失败：无法写入用户资料〕")
            end
            return kAccepted
        end
        return reset_learned_weight(context, segment, code, env)
    end
    if (key_event:shift() or key_event:ctrl()) and keycode == 0xffff then
        return delete_or_restore(context, segment, code, env)
    end
    local direction = reorder_direction(key_event)
    if direction ~= nil then
        return move_candidate(context, segment, code, direction, env)
    end
    return kNoop
end

local filter = {}
local order_filter = {}

function filter.init(env)
    configure(env)
end

function filter.fini(env)
    release(env)
end

local function decorate_entry(entry, management, env)
    if not management then
        return entry.cand
    end
    if entry.hidden then
        if is_pinned(entry.cand, env.override_pin_indicator) then
            local comment = entry.cand.comment .. " " .. env.override_hidden_comment
            return ShadowCandidate(entry.cand, "pinned", entry.cand.text, comment, false)
        end
        return ShadowCandidate(entry.cand, "mohu_hidden", entry.cand.text, env.override_hidden_comment, false)
    end
    if is_pinned(entry.cand, env.override_pin_indicator) or not record_applies(entry.cand) then
        return entry.cand
    end
    if entry.reordered then
        local comment = env.override_reordered_indicator .. entry.cand.comment
        return ShadowCandidate(entry.cand, "mohu_reordered", entry.cand.text, comment, false)
    end
    return entry.cand
end

function filter.func(t_input, env)
    if not env.override_enable then
        for cand in t_input:iter() do
            yield(cand)
        end
        return
    end

    local context = env.engine.context
    local segment, code = current_segment(context)
    if segment == nil then
        for cand in t_input:iter() do
            yield(cand)
        end
        return
    end

    local records = env.override_store:query(code)
    if records == nil then
        for cand in t_input:iter() do
            yield(cand)
        end
        return
    end
    local management = context:get_option(env.override_management_option)
    if management then
        segment.prompt = "〔候选管理：选择已隐藏词，按 Shift+Delete 恢复〕"
    end

    for cand in t_input:iter() do
        local record = record_applies(cand) and records[genuine_text(cand)] or nil
        if record == nil or not record.hidden or management then
            local entry = {
                cand = cand,
                hidden = record ~= nil and record.hidden or false,
                reordered = record ~= nil and record.rank >= 0 or false,
            }
            yield(decorate_entry(entry, management, env))
        end
    end
end

function order_filter.init(env)
    configure(env)
end

function order_filter.fini(env)
    release(env)
end

function order_filter.func(t_input, env)
    if not env.override_enable then
        for cand in t_input:iter() do
            yield(cand)
        end
        return
    end

    local context = env.engine.context
    local segment, code = current_segment(context)
    if segment == nil then
        for cand in t_input:iter() do
            yield(cand)
        end
        return
    end

    local records = env.override_store:query(code)
    if records == nil then
        for cand in t_input:iter() do
            yield(cand)
        end
        return
    end
    local ranked_needed = 0
    for _, record in pairs(records) do
        if record.rank >= 0 then
            ranked_needed = ranked_needed + 1
        end
    end
    if ranked_needed == 0 then
        for cand in t_input:iter() do
            yield(cand)
        end
        return
    end

    local advance, state = t_input:iter()
    local buffer = {}
    local seen = {}
    local ordinary_count = 0
    local seen_ranked = 0
    local raw_limit = env.override_max_candidates + 8
    while #buffer < raw_limit and ordinary_count < env.override_max_candidates and seen_ranked < ranked_needed do
        local cand = advance(state)
        if cand == nil then
            break
        end
        table.insert(buffer, cand)
        local text = genuine_text(cand)
        if is_orderable(cand, env) and not seen[text] then
            seen[text] = true
            ordinary_count = ordinary_count + 1
            local record = records[text]
            if record ~= nil and record.rank >= 0 then
                seen_ranked = seen_ranked + 1
            end
        end
    end
    for _, entry in ipairs(order_entries(buffer, records, true, env.override_pin_indicator)) do
        yield(entry.cand)
    end
    while true do
        local cand = advance(state)
        if cand == nil then
            break
        end
        yield(cand)
    end
end

M.override_processor = processor
M.override_order_filter = order_filter
M.override_filter = filter
M.acquire_store = acquire_store
M.is_context_user_deleted = is_context_user_deleted
M.is_user_deleted = is_user_deleted
M.mark_context_user_deleted = mark_context_user_deleted
M.mark_user_deleted = mark_user_deleted
M._test = {
    acquire_store = acquire_store,
    delete_or_restore = delete_or_restore,
    is_user_created = is_user_created,
    reset_learned_weight = reset_learned_weight,
    user_created_entry = user_created_entry,
    merge_visible_order = merge_visible_order,
    parse_record = parse_record,
    order_entries = order_entries,
    record_applies = record_applies,
    reorder_direction = reorder_direction,
    swap_texts = swap_texts,
}

return M
