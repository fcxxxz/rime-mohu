package.path = "./lua/?.lua;" .. package.path

local manager = require("mohu_candidate_manager")
local subject = manager._test

local home = subject.parse_route("\\gl", "\\gl")
assert(home ~= nil and home.category == nil and home.query == "")
local hidden = subject.parse_route("\\glhab", "\\gl")
assert(hidden.category == "h" and hidden.query == "ab")
assert(subject.parse_route("abc", "\\gl") == nil)
assert(subject.parse_route("\\glx", "\\gl") == nil)
assert(subject.code_from_comment("lm jx · 用户自造词 · Shift+Delete 删除") == "lm jx")

local overrides = {
    { code = "bb", text = "Beta", hidden = false, rank = 1, tick = 10 },
    { code = "aa", text = "Alpha", hidden = true, rank = -1, tick = 30 },
    { code = "cc", text = "Both", hidden = true, rank = 0, tick = 20 },
}
local hidden_records = subject.override_records(overrides, "h", "")
assert(#hidden_records == 2)
assert(hidden_records[1].text == "Alpha")
assert(hidden_records[2].text == "Both")
local ordered_records = subject.override_records(overrides, "o", "b")
assert(#ordered_records == 2)
assert(ordered_records[1].text == "Both")
assert(ordered_records[2].text == "Beta")

local pins = {
    { code = "aa", phrase = "Manual", source = "pin", commits = 2, timestamp = 3 },
    { code = "aa", phrase = "Made", source = "panacea", commits = 1, timestamp = 4 },
    { code = "bb", phrase = "Free", source = "freestyle", commits = 0, timestamp = 5 },
    { code = "cc", phrase = "Old", source = "legacy", commits = 0, timestamp = 6 },
}
assert(#subject.pin_records(pins, "p", "") == 1)
assert(#subject.pin_records(pins, "w", "") == 2)
assert(#subject.pin_records(pins, "l", "") == 1)
assert(subject.pin_records(pins, "p", "man")[1].phrase == "Manual")
local pin_comment = subject.record_comment("p", pins[1])
assert(pin_comment == "aa · Shift+Delete 取消置顶", pin_comment)

local memory = {
    user_entries = {
        { text = "Built", custom_code = "aa", commit_count = 5 },
        { text = "Made", custom_code = "b b", commit_count = 2 },
        { text = "Another", custom_code = "cc", commit_count = 1 },
        { text = "Deleted", custom_code = "dd", commit_count = 1 },
    },
    dict_entries = {
        aa = { { text = "Built" } },
        bb = { { text = "Other" } },
        cc = {},
        dd = {},
    },
}
function memory:user_lookup(code)
    self.lookup_code = code
    return true
end
function memory:iter_user()
    local entries = self.user_entries
    if self.lookup_code ~= nil and self.lookup_code ~= "" then
        entries = {}
        for _, entry in ipairs(self.user_entries) do
            if entry.custom_code == self.lookup_code and entry.text ~= "Deleted" then
                table.insert(entries, entry)
            end
        end
    end
    local index = 0
    return function()
        index = index + 1
        return entries[index]
    end
end
function memory:dictiter_lookup(code)
    local entries = self.dict_entries[code] or {}
    return {
        iter = function()
            local index = 0
            return function()
                index = index + 1
                return entries[index]
            end
        end,
    }
end

local users = subject.user_created_records(memory, "")
assert(#users == 2)
assert(users[1].text == "Made" and users[1].code == "b b")
assert(users[2].text == "Another" and users[2].code == "cc")
assert(subject.user_created_records(memory, "cc")[1].text == "Another")
local made = subject.find_user_created_record(memory, "bb", "Made")
assert(made ~= nil and made.entry.text == "Made")
assert(subject.find_user_created_record(memory, "aa", "Built") == nil)
assert(subject.find_user_created_record(memory, "dd", "Deleted") == nil)
assert(subject.record_comment("h", { code = "bb", user_created = true })
    == "bb · 用户自造词 · Shift+Delete 永久删除")

local calls = {}
local deps = {
    override_store = {
        set_hidden = function(_, code, text, value)
            table.insert(calls, { "hidden", code, text, value })
            return true
        end,
        clear_rank = function(_, code, text)
            table.insert(calls, { "rank", code, text })
            return true
        end,
    },
    pin_store = {
        remove = function(code, text)
            table.insert(calls, { "pin", code, text })
            return true
        end,
    },
    memory = memory,
}
function memory:update_userdict(entry, commits, prefix)
    table.insert(calls, { "user", entry.text, entry.custom_code, commits, prefix })
    return true
end

assert(subject.perform_action("h", "aa", "Alpha", deps) == true)
assert(subject.perform_action("o", "bb", "Beta", deps) == true)
assert(subject.perform_action("p", "aa", "Manual", deps) == true)
assert(subject.perform_action("w", "aa", "Made", deps) == true)
assert(subject.perform_action("l", "cc", "Old", deps) == true)
assert(subject.perform_action("u", "bb", "Made", deps) == true)
assert(calls[1][1] == "hidden" and calls[1][4] == false)
assert(calls[2][1] == "rank")
assert(calls[3][1] == "pin" and calls[5][1] == "pin")
assert(calls[6][1] == "user" and calls[6][4] == -1)

local permanent_calls = {}
local permanent_deps = {
    override_store = {
        set_hidden = function(_, code, text, value)
            table.insert(permanent_calls, { "hidden", code, text, value })
            return true
        end,
    },
    memory = memory,
}
function permanent_deps.memory:update_userdict(entry, commits, prefix)
    table.insert(permanent_calls, { "user", entry.text, entry.custom_code, commits, prefix })
    return true
end
assert(subject.perform_action("h", "cc", "Another", permanent_deps) == true)
assert(permanent_calls[1][1] == "hidden" and permanent_calls[1][4] == false)
assert(permanent_calls[2][1] == "user" and permanent_calls[2][4] == -1)

local counts = subject.category_counts(overrides, pins, users)
assert(counts.h == 2 and counts.o == 2)
assert(counts.p == 1 and counts.w == 2 and counts.l == 1 and counts.u == 2)

local context = {
    input = "",
    caret_pos = 0,
    refresh_count = 0,
    push_input_count = 0,
}
context.composition = {
    empty = function()
        return context.input == ""
    end,
    back = function()
        return nil
    end,
}
function context:push_input(value)
    self.push_input_count = self.push_input_count + 1
    self.input = self.input .. value
    self.caret_pos = #self.input
    return true
end
function context:refresh_non_confirmed_composition()
    self.refresh_count = self.refresh_count + 1
end

local shortcut = {
    keycode = 0x4d,
    ctrl = function() return true end,
    shift = function() return true end,
    alt = function() return false end,
    release = function() return false end,
}
local processor_env = {
    manager_enabled = true,
    manager_prefix = "\\gl",
    engine = { context = context },
}
assert(manager.manager_processor.func(shortcut, processor_env) == 1)
assert(context.input == "\\gl", context.input)
assert(context.caret_pos == 3, context.caret_pos)
assert(context.refresh_count == 1, context.refresh_count)
assert(context.push_input_count == 1, context.push_input_count)

local nav_context = {
    input = "\\gl",
    caret_pos = 3,
    refresh_count = 0,
    push_input_count = 0,
    pop_input_count = 0,
    clear_count = 0,
    properties = {},
}
local nav_segment = {
    selected_index = 0,
    menu = {},
    get_selected_candidate = function() return nil end,
}
nav_context.composition = {
    empty = function() return false end,
    back = function() return nav_segment end,
}
function nav_context:push_input(value)
    self.push_input_count = self.push_input_count + 1
    self.input = self.input .. value
    self.caret_pos = #self.input
    return true
end
function nav_context:pop_input(count)
    self.pop_input_count = self.pop_input_count + 1
    self.input = self.input:sub(1, #self.input - count)
    self.caret_pos = #self.input
    return true
end
function nav_context:refresh_non_confirmed_composition()
    self.refresh_count = self.refresh_count + 1
end
function nav_context:clear()
    self.clear_count = self.clear_count + 1
    self.input = ""
    self.caret_pos = 0
end
function nav_context:get_property(name)
    return self.properties[name] or ""
end
function nav_context:set_property(name, value)
    self.properties[name] = value
end

KeyEvent = function(repr)
    return { repr = repr }
end
local forwarded_keys = {}
local nav_env = {
    manager_enabled = true,
    manager_prefix = "\\gl",
    engine = {
        context = nav_context,
        process_key = function(_, event)
            table.insert(forwarded_keys, event.repr)
            nav_context.input = nav_context.input .. event.repr
            nav_context.caret_pos = #nav_context.input
            return true
        end,
    },
}
local space = {
    keycode = 0x20,
    ctrl = function() return false end,
    shift = function() return false end,
    alt = function() return false end,
    release = function() return false end,
}
assert(manager.manager_processor.func(space, nav_env) == 1)
assert(nav_context.input == "\\glh", nav_context.input)
assert(#forwarded_keys == 1 and forwarded_keys[1] == "h")
assert(nav_context.refresh_count == 0, nav_context.refresh_count)
assert(subject.category_preedit("\\gl", "h") == "\\gl 隐藏的内置词")

local backspace = {
    keycode = 0xff08,
    ctrl = function() return false end,
    shift = function() return false end,
    alt = function() return false end,
    release = function() return false end,
}
assert(manager.manager_processor.func(backspace, nav_env) == 2)
assert(nav_context.input == "\\glh", nav_context.input)
assert(nav_context.refresh_count == 0, nav_context.refresh_count)
assert(nav_context.pop_input_count == 0, nav_context.pop_input_count)

nav_segment.get_selected_candidate = function()
    return {
        type = "mohu_manager_record_h",
        text = "寜",
        comment = "aa · Shift+Delete 恢复显示",
    }
end
assert(manager.manager_processor.func(space, nav_env) == 1)
assert(nav_segment.prompt == "〔管理记录：使用 Shift+Delete 执行注释中的操作〕")

local override_module = require("mohu_candidate_override")
local original_acquire_store = override_module.acquire_store
local management_actions = {}
local action_store = {
    release = function() end,
    refresh = function() return true end,
    set_hidden = function(_, code, text, hidden_value)
        table.insert(management_actions, { code, text, hidden_value })
        return true
    end,
}
override_module.acquire_store = function()
    return action_store
end
nav_env.manager_override_name = "mohu_candidate_manager_test"
nav_env.manager_override_store = action_store
local shifted_backspace = {
    keycode = 0xff08,
    ctrl = function() return false end,
    shift = function() return true end,
    alt = function() return false end,
    release = function() return false end,
}
assert(manager.manager_processor.func(shifted_backspace, nav_env) == 1)
assert(#management_actions == 1)
assert(management_actions[1][1] == "aa")
assert(management_actions[1][2] == "寜")
assert(management_actions[1][3] == false)
override_module.acquire_store = original_acquire_store

local modifier_release = {
    keycode = 0xffe1,
    ctrl = function() return true end,
    shift = function() return false end,
    alt = function() return false end,
    release = function() return true end,
}
assert(manager.manager_processor.func(modifier_release, processor_env) == 2)
assert(context.input == "\\gl", context.input)
assert(context.caret_pos == 3, context.caret_pos)
assert(context.refresh_count == 1, context.refresh_count)
assert(context.push_input_count == 1, context.push_input_count)

local next_key = {
    keycode = 0xff1b,
    ctrl = function() return false end,
    shift = function() return false end,
    alt = function() return false end,
    release = function() return false end,
}
context.input = ""
context.caret_pos = 0
assert(manager.manager_processor.func(next_key, processor_env) == 2)
assert(context.input == "", context.input)

local option_shortcut = {
    keycode = 0x4d,
    ctrl = function() return true end,
    shift = function() return false end,
    alt = function() return true end,
    release = function() return false end,
}
assert(manager.manager_processor.func(option_shortcut, processor_env) == 1)
assert(context.input == "\\gl", context.input)
assert(context.push_input_count == 2, context.push_input_count)
assert(manager.manager_processor.func(modifier_release, processor_env) == 2)

print("candidate manager logic: ok")
