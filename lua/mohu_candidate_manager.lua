-- Global manager for Mohu candidate overrides and user-created words.

local override = require("mohu_candidate_override")
local pin = require("mohu_pin")

local M = {}
local kAccepted = 1
local kNoop = 2
local kMemoryUnavailable = "〔无法连接用户词典〕"

local categories = {
    { key = "h", label = "隐藏的内置词" },
    { key = "o", label = "手动调序" },
    { key = "p", label = "手动 Pin" },
    { key = "w", label = "万灵药 / 自由加词" },
    { key = "l", label = "Pin / 万灵药（历史）" },
    { key = "u", label = "用户自造词" },
}

local function category_preedit(prefix, key)
    for _, category in ipairs(categories) do
        if category.key == key then
            return prefix .. " " .. category.label
        end
    end
    return prefix
end

local function parse_route(input, prefix)
    if input == prefix then
        return { category = nil, query = "" }
    end
    if input:sub(1, #prefix) ~= prefix then
        return nil
    end
    local suffix = input:sub(#prefix + 1)
    local category, query = suffix:match("^([hopwlu])([a-z]*)$")
    if category == nil then
        return nil
    end
    return { category = category, query = query }
end

local function matches(record, query)
    if query == nil or query == "" then
        return true
    end
    query = query:lower()
    local code = (record.code or ""):lower()
    local text = (record.text or record.phrase or ""):lower()
    return code:find(query, 1, true) ~= nil or text:find(query, 1, true) ~= nil
end

local function override_records(records, category, query)
    local result = {}
    for _, record in ipairs(records or {}) do
        local active = (category == "h" and record.hidden) or (category == "o" and record.rank >= 0)
        if active and matches(record, query) then
            table.insert(result, record)
        end
    end
    table.sort(result, function(a, b)
        if a.tick ~= b.tick then
            return a.tick > b.tick
        end
        if a.code ~= b.code then
            return a.code < b.code
        end
        if category == "o" and a.rank ~= b.rank then
            return a.rank < b.rank
        end
        return a.text < b.text
    end)
    return result
end

local function pin_in_category(record, category)
    if category == "p" then
        return record.source == "pin"
    elseif category == "w" then
        return record.source == "panacea" or record.source == "freestyle"
    elseif category == "l" then
        return record.source == "legacy"
    end
    return false
end

local function pin_records(records, category, query)
    local result = {}
    for _, record in ipairs(records or {}) do
        if pin_in_category(record, category) and matches(record, query) then
            table.insert(result, record)
        end
    end
    table.sort(result, function(a, b)
        if a.timestamp ~= b.timestamp then
            return a.timestamp > b.timestamp
        end
        if a.code ~= b.code then
            return a.code < b.code
        end
        if a.commits ~= b.commits then
            return a.commits > b.commits
        end
        return a.phrase < b.phrase
    end)
    return result
end

local function entry_code(entry)
    local code = entry.custom_code
    if type(code) ~= "string" then
        return ""
    end
    return (code:gsub("%s+$", ""))
end

local function normalize_code(code)
    return (code or ""):gsub(";[^%s]+", ""):gsub("%s+", "")
end

local function is_builtin(memory, code, text)
    if code == "" then
        return false
    end
    local ok, iterator = pcall(function()
        return memory:dictiter_lookup(code, false, 0)
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

local function is_active_user_record(memory, record)
    if type(record.entry.commit_count) == "number" and record.entry.commit_count <= 0 then
        return false
    end
    local ok, found = pcall(function()
        return memory:user_lookup(record.code, false)
    end)
    if not ok or not found then
        return false
    end
    local active = false
    ok = pcall(function()
        for entry in memory:iter_user() do
            if entry_code(entry) == record.code and entry.text == record.text then
                active = type(entry.commit_count) ~= "number" or entry.commit_count > 0
                return
            end
        end
    end)
    return ok and active
end

local function user_created_records(memory, query, context, override_store)
    local result = {}
    if memory == nil then
        return result
    end
    local ok, found = pcall(function()
        return memory:user_lookup("", true)
    end)
    if not ok or not found then
        return result
    end
    local all_records = {}
    ok = pcall(function()
        for entry in memory:iter_user() do
            local code = entry_code(entry)
            table.insert(all_records, { code = code, text = entry.text, entry = entry })
        end
    end)
    if not ok then
        return {}
    end
    local seen = {}
    for _, record in ipairs(all_records) do
        local code = record.code
        local key = code .. "\t" .. record.text
        local active = is_active_user_record(memory, record)
            and not override.is_user_deleted(code, record.text, record.entry.commit_count)
            and not override.is_context_user_deleted(context, code, record.text, record.entry.commit_count)
            and not (
                override_store ~= nil
                and override_store.is_user_deleted ~= nil
                and override_store:is_user_deleted(code, record.text, record.entry.commit_count)
            )
        if active and not seen[key] and not is_builtin(memory, code, record.text) and matches(record, query) then
            seen[key] = true
            table.insert(result, record)
        end
    end
    table.sort(result, function(a, b)
        if a.code ~= b.code then
            return a.code < b.code
        end
        return a.text < b.text
    end)
    return result
end

local function find_user_created_record(memory, code, text, context, override_store)
    local normalized = normalize_code(code)
    for _, record in ipairs(user_created_records(memory, "", context, override_store)) do
        if normalize_code(record.code) == normalized and record.text == text then
            return record
        end
    end
    return nil
end

local function category_counts(overrides, pins, users)
    return {
        h = #override_records(overrides, "h", ""),
        o = #override_records(overrides, "o", ""),
        p = #pin_records(pins, "p", ""),
        w = #pin_records(pins, "w", ""),
        l = #pin_records(pins, "l", ""),
        u = #(users or {}),
    }
end

local function record_comment(category, record)
    if category == "h" then
        if record.user_created then
            return record.code .. " · 用户自造词 · Shift+Delete 永久删除"
        end
        return record.code .. " · 已隐藏 · Shift+Delete 恢复"
    elseif category == "o" then
        return string.format("%s · 第 %d 位 · Shift+Delete 恢复默认", record.code, record.rank + 1)
    elseif category == "p" then
        return record.code .. " · Shift+Delete 取消置顶"
    elseif category == "w" then
        local source = record.source == "freestyle" and "自由加词" or "万灵药"
        return record.code .. " · " .. source .. " · Shift+Delete 删除"
    elseif category == "l" then
        return record.code .. " · 历史来源未知 · Shift+Delete 删除"
    end
    return record.code .. " · 用户自造词 · Shift+Delete 删除"
end

local function code_from_comment(comment)
    if type(comment) ~= "string" then
        return nil
    end
    return comment:match("^(.-)%s+·")
end

local function perform_action(category, code, text, deps)
    if category == "h" then
        local user_record = find_user_created_record(
            deps.memory,
            code,
            text,
            deps.context,
            deps.override_store
        )
        if user_record ~= nil then
            if deps.override_store == nil or not deps.override_store:set_hidden(code, text, false) then
                return false
            end
            local ok, result = pcall(function()
                return deps.memory:update_userdict(user_record.entry, -1, "")
            end)
            if not ok or result == false then
                pcall(function() deps.override_store:set_hidden(code, text, true) end)
                return false
            end
            override.mark_user_deleted(user_record.code, text, user_record.entry.commit_count)
            override.mark_context_user_deleted(deps.context, user_record.code, text, user_record.entry.commit_count)
            if deps.override_store.set_user_deleted ~= nil then
                deps.override_store:set_user_deleted(user_record.code, text, user_record.entry.commit_count)
            end
            return true
        end
        return deps.override_store ~= nil and deps.override_store:set_hidden(code, text, false) or false
    elseif category == "o" then
        return deps.override_store ~= nil and deps.override_store:clear_rank(code, text) or false
    elseif category == "p" or category == "w" or category == "l" then
        if deps.pin_store == nil then
            return false
        end
        local ok, result = pcall(deps.pin_store.remove, code, text)
        return ok and result ~= false
    elseif category == "u" and deps.memory ~= nil then
        for _, record in ipairs(user_created_records(
            deps.memory,
            "",
            deps.context,
            deps.override_store
        )) do
            if normalize_code(record.code) == normalize_code(code) and record.text == text then
                local ok, result = pcall(function()
                    return deps.memory:update_userdict(record.entry, -1, "")
                end)
                if ok and result ~= false then
                    override.mark_user_deleted(record.code, text, record.entry.commit_count)
                    override.mark_context_user_deleted(deps.context, record.code, text, record.entry.commit_count)
                    if deps.override_store.set_user_deleted ~= nil then
                        deps.override_store:set_user_deleted(record.code, text, record.entry.commit_count)
                    end
                end
                return ok and result ~= false
            end
        end
    end
    return false
end

local function get_bool(config, path, default)
    local value = config:get_bool(path)
    if value == nil then
        return default
    end
    return value
end

local function ensure_memory(env)
    if env.manager_memory_attempted then
        return env.manager_memory
    end
    env.manager_memory_attempted = true
    if Memory == nil or env.manager_memory_namespace == nil then
        return nil
    end
    local ok, memory = pcall(Memory, env.engine, env.engine.schema, env.manager_memory_namespace)
    if ok and memory ~= nil then
        env.manager_memory = memory
    end
    return env.manager_memory
end

local function refresh_memory(env)
    if env.manager_memory ~= nil then
        pcall(function() env.manager_memory:disconnect() end)
        env.manager_memory = nil
    end
    env.manager_memory_attempted = false
    return ensure_memory(env)
end

local function refresh_override_store(env)
    if env.manager_override_store ~= nil then
        env.manager_override_store:release()
        env.manager_override_store = nil
    end
    env.manager_override_store = override.acquire_store(env.manager_override_name)
    if env.manager_override_store ~= nil and env.manager_override_store.refresh ~= nil then
        env.manager_override_store:refresh()
    end
    return env.manager_override_store
end

local function configure(env)
    local config = env.engine.schema.config
    if env.manager_memory ~= nil then
        pcall(function() env.manager_memory:disconnect() end)
    end
    env.manager_memory = nil
    env.manager_memory_attempted = false
    env.manager_memory_namespace = config:get_string("mohu/candidate_manager/memory_namespace") or "translator"
    env.manager_enabled = get_bool(config, "mohu/candidate_manager/enable", true)
    env.manager_prefix = config:get_string("mohu/candidate_manager/prefix") or "=="
    env.manager_option = "candidate_override_management"
    if not env.manager_enabled then
        return
    end

    env.manager_override_name = config:get_string("mohu/candidate_override/db_name") or "mohu_candidate_override"
    env.manager_override_store = override.acquire_store(env.manager_override_name)
    local pin_ok, pin_acquired = pcall(pin.pin_store.acquire)
    if pin_ok and pin_acquired then
        env.manager_pin_store = pin.pin_store
    end
end

local function release(env)
    if env.manager_override_store ~= nil then
        env.manager_override_store:release()
        env.manager_override_store = nil
    end
    if env.manager_pin_store ~= nil then
        pcall(env.manager_pin_store.release)
        env.manager_pin_store = nil
    end
    if env.manager_memory ~= nil then
        pcall(function() env.manager_memory:disconnect() end)
        env.manager_memory = nil
    end
    env.manager_memory_attempted = false
    env.manager_memory_namespace = nil
end

local function current_segment(context)
    if context.composition:empty() then
        return nil
    end
    return context.composition:back()
end

local function set_prompt(context, message)
    local segment = current_segment(context)
    if segment ~= nil then
        segment.prompt = message
    end
end

local function manager_candidate(cand)
    if cand == nil then
        return nil
    end
    if cand.get_genuine then
        cand = cand:get_genuine()
    end
    if cand.type:sub(1, 13) ~= "mohu_manager_" then
        return nil
    end
    return cand
end

local function open_manager(context, prefix)
    context:push_input(prefix)
    context:refresh_non_confirmed_composition()
end

local function selected_for_key(context, keycode)
    local segment = current_segment(context)
    if segment == nil or segment.menu == nil then
        return nil
    end
    if keycode >= 0x31 and keycode <= 0x39 then
        segment.menu:prepare(keycode - 0x30)
        return segment.menu:get_candidate_at(keycode - 0x31)
    end
    return segment:get_selected_candidate()
end

local processor = {}

function processor.init(env)
    configure(env)
end

function processor.fini(env)
    release(env)
end

function processor.func(key_event, env)
    if not env.manager_enabled then
        return kNoop
    end
    local context = env.engine.context
    if key_event:release() then
        return kNoop
    end
    local keycode = key_event.keycode

    local manager_modifier = key_event:shift() or key_event:alt()
    if key_event:ctrl() and manager_modifier and (keycode == 0x4d or keycode == 0x6d) then
        if context.composition:empty() then
            open_manager(context, env.manager_prefix)
            return kAccepted
        end
        return kNoop
    end

    local route = parse_route(context.input, env.manager_prefix)
    if route == nil then
        return kNoop
    end

    local commit_key = keycode == 0x20 or keycode == 0xff0d or (keycode >= 0x31 and keycode <= 0x39)
    if commit_key and route.category == nil then
        local segment = current_segment(context)
        local index = segment and (segment.selected_index + 1) or 1
        if keycode >= 0x31 and keycode <= 0x39 then
            index = keycode - 0x30
        end
        local category = categories[index]
        if category ~= nil then
            env.engine:process_key(KeyEvent(category.key))
        end
        return kAccepted
    end

    local selected = manager_candidate(selected_for_key(context, keycode))
    if selected == nil then
        return kNoop
    end

    if commit_key then
        local category = selected.type:match("^mohu_manager_nav_([hopwlu])$")
        if category ~= nil then
            env.engine:process_key(KeyEvent(category))
        else
            set_prompt(context, "〔管理记录：使用 Shift+Delete 执行注释中的操作〕")
        end
        return kAccepted
    end

    local delete_action = keycode == 0xffff and (key_event:shift() or key_event:ctrl())
        or keycode == 0xff08 and key_event:shift()
    if delete_action and route.category ~= nil then
        local code = code_from_comment(selected.comment)
        if code == nil then
            return kAccepted
        end
        if route.category == "h" or route.category == "u" then
            if ensure_memory(env) == nil then
                set_prompt(context, kMemoryUnavailable)
                return kAccepted
            end
            refresh_override_store(env)
        end
        local ok = perform_action(route.category, code, selected.text, {
            override_store = env.manager_override_store,
            pin_store = env.manager_pin_store,
            memory = env.manager_memory,
            context = context,
        })
        if ok then
            context:refresh_non_confirmed_composition()
            set_prompt(context, "〔已更新「" .. selected.text .. "」〕")
        else
            set_prompt(context, "〔更新失败：无法写入用户资料〕")
        end
        return kAccepted
    end
    return kNoop
end

local translator = {}

function translator.init(env)
    configure(env)
end

function translator.fini(env)
    release(env)
end

local function emit(cand_type, seg, text, comment, index, preedit)
    local cand = Candidate(cand_type, seg.start, seg._end, text, comment)
    cand.quality = 1000000 - (index or 0)
    if preedit ~= nil then
        cand.preedit = preedit
    end
    yield(cand)
end

local function read_sources(env)
    local overrides = env.manager_override_store and env.manager_override_store:list_all() or {}
    local pins = {}
    if env.manager_pin_store ~= nil then
        local ok, result = pcall(env.manager_pin_store.list_all)
        if ok and result ~= nil then
            pins = result
        end
    end
    return overrides or {}, pins
end

function translator.func(input, seg, env)
    if not env.manager_enabled then
        return
    end
    local route = parse_route(input, env.manager_prefix)
    if route == nil then
        return
    end
    if route.category == "h" or route.category == "u" then
        if ensure_memory(env) == nil then
            set_prompt(env.engine.context, kMemoryUnavailable)
            return
        end
        refresh_override_store(env)
    end
    local overrides, pins = read_sources(env)
    if route.category == nil then
        local counts = category_counts(overrides, pins, nil)
        for index, category in ipairs(categories) do
            local comment
            if category.key == "u" then
                comment = "当前方案"
            else
                comment = string.format("%d 条", counts[category.key])
            end
            emit(
                "mohu_manager_nav_" .. category.key,
                seg,
                category.label,
                comment,
                index
            )
        end
        return
    end

    local records
    if route.category == "h" or route.category == "o" then
        records = override_records(overrides, route.category, route.query)
        if route.category == "h" then
            for _, record in ipairs(records) do
                record.user_created = find_user_created_record(
                    env.manager_memory,
                    record.code,
                    record.text,
                    env.engine.context,
                    env.manager_override_store
                ) ~= nil
            end
        end
    elseif route.category == "u" then
        records = user_created_records(
            env.manager_memory,
            route.query,
            env.engine.context,
            env.manager_override_store
        )
    else
        records = pin_records(pins, route.category, route.query)
    end
    local preedit = category_preedit(env.manager_prefix, route.category)
    if #records == 0 then
        emit("mohu_manager_empty", seg, "没有记录", "可继续输入编码筛选", 1, preedit)
        return
    end
    for index, record in ipairs(records) do
        local text = record.text or record.phrase
        local comment = record_comment(route.category, record)
        emit("mohu_manager_record_" .. route.category, seg, text, comment, index, preedit)
    end
end

M.manager_processor = processor
M.manager_translator = translator
M._test = {
    category_preedit = category_preedit,
    category_counts = category_counts,
    code_from_comment = code_from_comment,
    ensure_memory = ensure_memory,
    find_user_created_record = find_user_created_record,
    override_records = override_records,
    parse_route = parse_route,
    perform_action = perform_action,
    pin_records = pin_records,
    record_comment = record_comment,
    refresh_memory = refresh_memory,
    user_created_records = user_created_records,
}

return M
