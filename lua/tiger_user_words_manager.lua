local user_words = require("tiger_user_words")

local M = {}
local kAccepted = 1
local kNoop = 2
local PREFIX = "\\gl"

local categories = {
    { key = "h", label = "已隐藏" },
    { key = "o", label = "手动调序" },
    { key = "u", label = "快捷加词" },
}

local function parse_route(input)
    if input == PREFIX then
        return { category = nil, query = "" }
    end
    if input:sub(1, #PREFIX) ~= PREFIX then
        return nil
    end
    local category, query = input:sub(#PREFIX + 1):match("^([hou])([a-z;']*)$")
    if not category then
        return nil
    end
    return { category = category, query = query }
end

local function matches(record, query)
    return query == ""
        or record.code:lower():find(query, 1, true) ~= nil
        or record.text:lower():find(query, 1, true) ~= nil
end

local function category_records(records, category, query)
    local result = {}
    for _, record in ipairs(records or {}) do
        local included = (category == "h" and record.hidden)
            or (category == "o" and record.ordered)
            or (category == "u" and record.added and record.source == "shortcut")
        if included and matches(record, query) then
            table.insert(result, record)
        end
    end
    table.sort(result, function(a, b)
        if a.updated_at ~= b.updated_at then
            return a.updated_at > b.updated_at
        end
        if a.code ~= b.code then
            return a.code < b.code
        end
        return a.text < b.text
    end)
    return result
end

local function record_comment(category, record)
    if category == "h" then
        return record.code .. " · 已隐藏 · Delete 恢复"
    elseif category == "o" then
        return record.code .. " · 手动调序 · Delete 清除调序"
    end
    return record.code .. " · 快捷加词 · Delete 删除"
end

local function manager_candidate(candidate)
    if not candidate then
        return nil
    end
    if candidate.get_genuine then
        candidate = candidate:get_genuine()
    end
    return candidate.type and candidate.type:sub(1, 14) == "tiger_manager_" and candidate or nil
end

local function selected_code(candidate)
    return candidate.comment and candidate.comment:match("^(.-)%s+·") or nil
end

local function replace_input(context, input)
    local current = context.input or ""
    if input:sub(1, #current) == current and context.push_input then
        context:push_input(input:sub(#current + 1))
    elseif current:sub(1, #input) == input and context.pop_input then
        context:pop_input(#current - #input)
    else
        context.input = input
        context.caret_pos = #input
        if context.refresh_non_confirmed_composition then
            context:refresh_non_confirmed_composition()
        end
    end
end

local function current_segment(context)
    if not context.composition or context.composition:empty() then
        return nil
    end
    return context.composition:back()
end

local function selected_for_key(context, keycode)
    local segment = current_segment(context)
    if keycode >= 0x31 and keycode <= 0x39 and segment and segment.menu then
        local index = keycode - 0x31
        segment.menu:prepare(index + 1)
        return segment.menu:get_candidate_at(index)
    end
    return context:get_selected_candidate()
end

local function set_prompt(context, message)
    local segment = current_segment(context)
    if segment then
        segment.prompt = message
    end
end

local function is_entry_shortcut(key_event)
    local keycode = key_event.keycode
    return key_event:ctrl() and key_event:shift()
        and not key_event:alt() and not key_event:super() and not key_event:release()
        and (keycode == 0x4d or keycode == 0x6d)
end

local function is_delete_shortcut(key_event)
    if key_event.keycode ~= 0xffff or key_event:release() or key_event:alt() or key_event:super() then
        return false
    end
    return (key_event:shift() and not key_event:ctrl())
        or (key_event:ctrl() and not key_event:shift())
end

local function perform_action(schema_id, category, code, text)
    if category == "h" then
        return user_words.restore_hidden(schema_id, code, text)
    elseif category == "o" then
        return user_words.clear_order(schema_id, code, text)
    elseif category == "u" then
        return user_words.remove_shortcut(schema_id, code, text)
    end
    return false
end

local processor = {}

function processor.init(env)
    env.schema_id = env.engine.schema.schema_id
end

function processor.func(key_event, env)
    if key_event:release() then
        return kNoop
    end
    local context = env.engine.context
    if is_entry_shortcut(key_event) and context.composition:empty() then
        context:push_input(PREFIX)
        return kAccepted
    end

    local route = parse_route(context.input or "")
    if not route then
        return kNoop
    end
    if key_event.keycode == 0xff1b then
        context:clear()
        return kAccepted
    end
    if route.category and context.input == PREFIX .. route.category and key_event.keycode == 0xff08 then
        replace_input(context, PREFIX)
        return kAccepted
    end

    local selected = manager_candidate(selected_for_key(context, key_event.keycode))
    local commit_key = key_event.keycode == 0x20 or key_event.keycode == 0xff0d
        or (key_event.keycode >= 0x31 and key_event.keycode <= 0x39)
    if commit_key and not route.category then
        local category = selected and selected.type:match("^tiger_manager_nav_([hou])$")
        if category then
            replace_input(context, PREFIX .. category)
        end
        return kAccepted
    end
    if is_delete_shortcut(key_event) and route.category and selected then
        local code = selected_code(selected)
        local ok = code and perform_action(env.schema_id, route.category, code, selected.text)
        if ok then
            if context.refresh_non_confirmed_composition then
                context:refresh_non_confirmed_composition()
            end
            set_prompt(context, "〔已更新「" .. selected.text .. "」〕")
        else
            set_prompt(context, "〔更新失败：无法写入用户数据〕")
        end
        return kAccepted
    end
    if commit_key and selected then
        set_prompt(context, "〔请使用 Shift+Delete 或 Ctrl+Delete 执行操作〕")
        return kAccepted
    end
    return kNoop
end

local translator = {}

function translator.init(env)
    env.schema_id = env.engine.schema.schema_id
end

local function emit(candidate_type, segment, text, comment, index)
    local candidate = Candidate(candidate_type, segment.start, segment._end, text, comment)
    candidate.quality = 1000000 - (index or 0)
    yield(candidate)
end

function translator.func(input, segment, env)
    local route = parse_route(input)
    if not route then
        return
    end
    local records, error_message = user_words.manager_snapshot(env.schema_id)
    if not records then
        emit("tiger_manager_empty", segment, "管理数据不可用", error_message or "无法读取用户数据库", 1)
        return
    end
    if not route.category then
        for index, category in ipairs(categories) do
            local count = #category_records(records, category.key, "")
            emit("tiger_manager_nav_" .. category.key, segment, category.label, count .. " 条", index)
        end
        return
    end
    local filtered = category_records(records, route.category, route.query)
    if #filtered == 0 then
        emit("tiger_manager_empty", segment, "没有记录", "可继续输入编码筛选", 1)
        return
    end
    for index, record in ipairs(filtered) do
        emit("tiger_manager_record_" .. route.category, segment, record.text, record_comment(route.category, record), index)
    end
end

M.processor = processor
M.translator = translator
M._test = {
    category_records = category_records,
    parse_route = parse_route,
    perform_action = perform_action,
    record_comment = record_comment,
}

return M
