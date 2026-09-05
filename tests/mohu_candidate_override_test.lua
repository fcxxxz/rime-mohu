package.path = "./lua/?.lua;" .. package.path

local override = require("mohu_candidate_override")
local subject = override._test

local function candidate(text, cand_type)
    local cand = { text = text, type = cand_type or "phrase", comment = "" }
    function cand:get_genuine()
        return self
    end
    return cand
end

local function texts(entries)
    local result = {}
    for _, entry in ipairs(entries) do
        table.insert(result, entry.cand.text)
    end
    return table.concat(result, ",")
end

local records = {
    A = { hidden = false, rank = 1, tick = 10 },
    B = { hidden = false, rank = 0, tick = 11 },
    C = { hidden = true, rank = -1, tick = 12 },
}
local candidates = {
    candidate("PIN", "pinned"),
    candidate("A"),
    candidate("B"),
    candidate("C"),
    candidate("D"),
}

local normal = subject.order_entries(candidates, records, false)
assert(texts(normal) == "PIN,B,A,D", texts(normal))
assert(normal[2].reordered == true)
assert(normal[4].reordered == false)

local management = subject.order_entries(candidates, records, true)
assert(texts(management) == "PIN,B,A,C,D", texts(management))
assert(management[4].hidden == true)

local capture = subject.order_entries(
    { candidate("加词 zzqa：链接", "mohu_capture_status"), candidate("PIN", "pinned"), candidate("A") },
    records,
    false
)
assert(texts(capture) == "加词 zzqa：链接,PIN,A", texts(capture))

local hidden_pin = subject.order_entries(
    { candidate("PIN", "pinned"), candidate("A") },
    { PIN = { hidden = true, rank = -1, tick = 13 } },
    false
)
assert(texts(hidden_pin) == "A", texts(hidden_pin))

local duplicates = subject.order_entries(
    { candidate("A", "phrase"), candidate("A", "table"), candidate("B") },
    {
        A = { hidden = false, rank = 1, tick = 14 },
        B = { hidden = false, rank = 0, tick = 15 },
    },
    false
)
assert(texts(duplicates) == "B,A", texts(duplicates))

local parsed = subject.parse_record("v=1 h=1 r=3 t=42")
assert(parsed.hidden == true)
assert(parsed.rank == 3)
assert(parsed.tick == 42)
assert(parsed.origin == -1)
local parsed_with_origin = subject.parse_record("v=1 h=0 r=1 o=0 t=43")
assert(parsed_with_origin.origin == 0)
assert(subject.parse_record("v=2 h=1 r=3 t=42") == nil)

local dormant_records = {
    A = { hidden = false, rank = 0, tick = 10 },
    H = { hidden = true, rank = 1, tick = 11 },
    Z = { hidden = false, rank = 2, tick = 12 },
}
local merged = subject.merge_visible_order({ "A", "B" }, dormant_records)
assert(table.concat(merged, ",") == "A,B,H,Z", table.concat(merged, ","))
subject.swap_texts(merged, "A", "B")
assert(table.concat(merged, ",") == "B,A,H,Z", table.concat(merged, ","))

local hidden_first = subject.merge_visible_order(
    { "B", "C" },
    {
        A = { hidden = true, rank = 0, tick = 10 },
        B = { hidden = false, rank = 1, tick = 11 },
    }
)
subject.swap_texts(hidden_first, "B", "C")
assert(table.concat(hidden_first, ",") == "A,C,B", table.concat(hidden_first, ","))

local equal_rank = subject.merge_visible_order(
    { "B", "A", "C" },
    {
        A = { hidden = false, rank = 0, tick = 1 },
        B = { hidden = false, rank = 0, tick = 1 },
    }
)
assert(table.concat(equal_rank, ",") == "B,A,C", table.concat(equal_rank, ","))

assert(subject.record_applies(candidate("A", "phrase")) == true)
assert(subject.record_applies(candidate("A", "user_phrase")) == true)
assert(subject.record_applies(candidate("加词 zzqa：链接", "mohu_capture_status")) == false)
override.mark_user_deleted("x x", "Transient", 2)
assert(override.is_user_deleted("xx", "Transient", 2))
assert(not override.is_user_deleted("xx", "Transient", 3))
local deleted_properties = {}
local deleted_options = {}
local property_context = {
    get_property = function(_, name) return deleted_properties[name] or "" end,
    set_property = function(_, name, value) deleted_properties[name] = value end,
    get_option = function(_, name) return deleted_options[name] or false end,
    set_option = function(_, name, value) deleted_options[name] = value end,
}
override.mark_context_user_deleted(property_context, "y y", "Shared", 4)
assert(override.is_context_user_deleted(property_context, "yy", "Shared", 4))
deleted_properties = {}
assert(override.is_context_user_deleted(property_context, "yy", "Shared", 4))
assert(not override.is_context_user_deleted(property_context, "yy", "Shared", 5))

local phrase_memory = {
    user_entries = {
        { text = "Built", custom_code = "a a", commit_count = 2 },
        { text = "Made", custom_code = "b b", commit_count = 1 },
        { text = "AuxMade", custom_code = "bb;xx cc;yy", commit_count = 1 },
    },
    dict_entries = {
        ["a a"] = { { text = "Built" } },
        ["b b"] = {},
        ["bb;xx cc;yy"] = {},
    },
}
function phrase_memory:user_lookup()
    return true
end
function phrase_memory:iter_user()
    local index = 0
    return function()
        index = index + 1
        return self.user_entries[index]
    end
end
function phrase_memory:dictiter_lookup(code)
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
phrase_memory.update_calls = {}
function phrase_memory:update_userdict(entry, commits, prefix)
    table.insert(self.update_calls, { entry.text, entry.custom_code, commits, prefix })
    return true
end

assert(subject.is_user_created(phrase_memory, candidate("Made", "user_phrase"), "bb"))
assert(subject.is_user_created(phrase_memory, candidate("AuxMade", "user_phrase"), "bbcc"))
assert(not subject.is_user_created(phrase_memory, candidate("Built", "user_phrase"), "aa"))
assert(not subject.is_user_created(phrase_memory, candidate("Made", "phrase"), "bb"))

local fresh_user_phrase = candidate("Fresh", "user_phrase")
fresh_user_phrase.entry = { text = "Fresh", custom_code = "c c" }
local pending_memory = {
    user_lookup = function() return false end,
    dictiter_lookup = function()
        return { iter = function() return function() return nil end end }
    end,
}
assert(subject.is_user_created(pending_memory, fresh_user_phrase, "cc"))

local function deletion_context(selected)
    local segment = {
        _start = 0,
        _end = 2,
        selected_index = 0,
        menu = {},
        get_selected_candidate = function()
            return selected
        end,
    }
    local context = {
        input = "bb",
        delete_count = 0,
        refresh_count = 0,
    }
    context.composition = {
        empty = function() return false end,
        back = function() return segment end,
    }
    function context:get_option()
        return false
    end
    function context:delete_current_selection()
        self.delete_count = self.delete_count + 1
        return true
    end
    function context:refresh_non_confirmed_composition()
        self.refresh_count = self.refresh_count + 1
    end
    return context, segment
end

local permanent_hidden_calls = {}
local permanent_context, permanent_segment = deletion_context(candidate("Made", "user_phrase"))
local permanent_result = subject.delete_or_restore(permanent_context, permanent_segment, "bb", {
    override_store = {
        query = function()
            return { Made = { hidden = true, rank = -1, tick = 1 } }
        end,
        set_hidden = function(_, code, text, value)
            table.insert(permanent_hidden_calls, { code, text, value })
            return true
        end,
        set_user_deleted = function(_, code, text, commit_count)
            table.insert(permanent_hidden_calls, { code, text, commit_count })
            return true
        end,
    },
    override_memory = phrase_memory,
    override_management_option = "candidate_override_management",
    override_max_candidates = 50,
})
assert(permanent_result == 1)
assert(permanent_context.delete_count == 1)
assert(#permanent_hidden_calls == 2 and permanent_hidden_calls[1][3] == false)
assert(permanent_hidden_calls[2][1] == "bb" and permanent_hidden_calls[2][3] == 1)
assert(#phrase_memory.update_calls == 1)
assert(phrase_memory.update_calls[1][1] == "Made" and phrase_memory.update_calls[1][3] == -1)

local builtin_hidden_calls = {}
local builtin_context, builtin_segment = deletion_context(candidate("Built", "user_phrase"))
local builtin_result = subject.delete_or_restore(builtin_context, builtin_segment, "aa", {
    override_store = {
        query = function()
            return {}
        end,
        set_hidden = function(_, code, text, value)
            table.insert(builtin_hidden_calls, { code, text, value })
            return true
        end,
    },
    override_memory = phrase_memory,
    override_management_option = "candidate_override_management",
    override_max_candidates = 50,
})
assert(builtin_result == 1)
assert(builtin_context.delete_count == 0)
assert(#builtin_hidden_calls == 1 and builtin_hidden_calls[1][3] == true)

local learned_builtin = candidate("Built", "phrase")
local reset_context, reset_segment = deletion_context(learned_builtin)
local update_count_before_reset = #phrase_memory.update_calls
local reset_result = subject.reset_learned_weight(reset_context, reset_segment, "a", {
    override_memory = phrase_memory,
})
assert(reset_result == 1)
assert(reset_context.delete_count == 1)
assert(reset_context.refresh_count == 1, reset_context.refresh_count)
assert(#phrase_memory.update_calls == update_count_before_reset)

local created_context, created_segment = deletion_context(candidate("Made", "user_phrase"))
local created_result = subject.reset_learned_weight(created_context, created_segment, "b", {
    override_memory = phrase_memory,
})
assert(created_result == 1)
assert(created_context.delete_count == 0)
assert(created_context.refresh_count == 0)
assert(#phrase_memory.update_calls == update_count_before_reset)

local query_db = { is_loaded = false }
function query_db:loaded()
    return self.is_loaded
end
function query_db:open()
    self.is_loaded = true
end
function query_db:query()
    return {
        iter = function()
            error("query failed")
        end,
    }
end
function query_db:close()
    self.is_loaded = false
end

local original_level_db = LevelDb
LevelDb = function()
    return query_db
end
local failing_store = subject.acquire_store("mohu_candidate_override_test_query_failure")
assert(failing_store:query("lmjx") == nil)
assert(failing_store:available() == false)
failing_store:release()
LevelDb = original_level_db

local update_db = {
    data = {
        ["lmjx \tA"] = "v=1 h=0 r=0 t=1",
    },
    is_loaded = false,
    update_count = 0,
}
function update_db:loaded()
    return self.is_loaded
end
function update_db:open()
    self.is_loaded = true
end
function update_db:query(prefix)
    local entries = {}
    for key, value in pairs(self.data) do
        if key:sub(1, #prefix) == prefix then
            table.insert(entries, { key, value })
        end
    end
    return {
        iter = function()
            local index = 0
            return function()
                index = index + 1
                local entry = entries[index]
                if entry == nil then return nil end
                return entry[1], entry[2]
            end
        end,
    }
end
function update_db:update(key, value)
    self.update_count = self.update_count + 1
    if self.update_count == 2 then
        return false
    end
    self.data[key] = value
    return true
end
function update_db:close()
    self.is_loaded = false
end

LevelDb = function()
    return update_db
end
local rollback_store = subject.acquire_store("mohu_candidate_override_test_update_failure")
assert(rollback_store:write_order("lmjx", { "B", "A" }) == false)
assert(update_db.data["lmjx \tB"] == "v=1 h=0 r=-1 o=-1 t=0")
assert(update_db.data["lmjx \tA"] == "v=1 h=0 r=0 t=1")
assert(rollback_store:available() == false)
rollback_store:release()
LevelDb = original_level_db

local all_db = {
    data = {
        ["aa \tAlpha"] = "v=1 h=1 r=-1 t=11",
        ["bb \tBeta"] = "v=1 h=0 r=0 t=12",
        ["cc \tCleared"] = "v=1 h=0 r=-1 t=13",
        ["dd \tBroken"] = "not-a-record",
    },
    is_loaded = false,
}
function all_db:loaded()
    return self.is_loaded
end
function all_db:open()
    self.is_loaded = true
end
function all_db:query(prefix)
    local entries = {}
    for key, value in pairs(self.data) do
        if key:sub(1, #prefix) == prefix then
            table.insert(entries, { key, value })
        end
    end
    table.sort(entries, function(a, b) return a[1] < b[1] end)
    return {
        iter = function()
            local index = 0
            return function()
                index = index + 1
                local entry = entries[index]
                if entry == nil then return nil end
                return entry[1], entry[2]
            end
        end,
    }
end
function all_db:update(key, value)
    self.data[key] = value
    return true
end
function all_db:close()
    self.is_loaded = false
end

LevelDb = function()
    return all_db
end
local all_store = subject.acquire_store("mohu_candidate_override_test_all")
local all_records = all_store:list_all()
assert(#all_records == 2, #all_records)
assert(all_records[1].code == "aa" and all_records[1].text == "Alpha")
assert(all_records[2].code == "bb" and all_records[2].rank == 0)
assert(all_store:clear_rank("bb", "Beta") == true)
assert(all_db.data["bb \tBeta"]:find("h=0 r=-1", 1, true))
assert(all_store:set_hidden("aa", "Alpha", false) == true)
assert(all_db.data["aa \tAlpha"]:find("h=0 r=-1", 1, true))
assert(all_store:set_user_deleted("ff", "Transient", 2) == true)
assert(all_store:is_user_deleted("ff", "Transient", 2))
assert(not all_store:is_user_deleted("ff", "Transient", 3))

assert(all_store:write_order("ee", { "B", "A" }, { "A", "B" }) == true)
assert(all_db.data["ee \tA"]:find("r=1 o=0", 1, true))
assert(all_db.data["ee \tB"]:find("r=0 o=1", 1, true))
assert(all_store:write_order("ee", { "A", "B" }, { "B", "A" }) == true)
assert(all_db.data["ee \tA"]:find("r=-1 o=-1", 1, true))
assert(all_db.data["ee \tB"]:find("r=-1 o=-1", 1, true))
all_store:release()
LevelDb = original_level_db

local function key(repr, keycode, shifted)
    return {
        keycode = keycode or 0,
        repr = function() return repr end,
        ctrl = function() return true end,
        shift = function() return shifted or false end,
    }
end

assert(subject.reorder_direction(key("Control+minus", 0x2d)) == -1)
assert(subject.reorder_direction(key("Control+equal", 0x3d)) == 1)
assert(subject.reorder_direction(key("Control+plus", 0x2b)) == 1)
assert(subject.reorder_direction(key("Shift+Control+plus", 0x2b, true)) == 1)
assert(subject.reorder_direction(key("Control+KP_Subtract", 0xffad)) == -1)
assert(subject.reorder_direction(key("Control+KP_Add", 0xffab)) == 1)
assert(subject.reorder_direction(key("Control+Up", 0xff52)) == nil)
assert(subject.reorder_direction(key("Control+Left", 0xff51)) == nil)
assert(subject.reorder_direction(key("Control+Down", 0xff54)) == nil)
assert(subject.reorder_direction(key("Control+Right", 0xff53)) == nil)
assert(subject.reorder_direction(key("Control+Alt+Up", 0xff52)) == nil)

-- Candidate override Memory must be lazy: ordinary candidate management should not
-- open the user dictionary, while actions that need user-dictionary inspection do.
local processor = override.override_processor

local function override_config(db_name)
    return {
        get_bool = function(_, path)
            if path == "mohu/candidate_override/enable" then
                return true
            end
            return nil
        end,
        get_int = function(_, path)
            if path == "mohu/candidate_override/max_candidates" then
                return 50
            end
            return nil
        end,
        get_string = function(_, path)
            if path == "mohu/candidate_override/db_name" then
                return db_name
            end
            if path == "mohu/candidate_manager/memory_namespace" then
                return "translator"
            end
            return nil
        end,
    }
end

local function override_db()
    local db = { is_loaded = false, data = {} }
    function db:loaded()
        return self.is_loaded
    end
    function db:open()
        self.is_loaded = true
    end
    function db:close()
        self.is_loaded = false
    end
    function db:query(prefix)
        local rows = {}
        for key, value in pairs(self.data) do
            if key:sub(1, #prefix) == prefix then
                table.insert(rows, { key, value })
            end
        end
        return {
            iter = function()
                local index = 0
                return function()
                    index = index + 1
                    local row = rows[index]
                    if row == nil then
                        return nil
                    end
                    return row[1], row[2]
                end
            end,
        }
    end
    function db:update(key, value)
        self.data[key] = value
        return true
    end
    return db
end

local function notifier()
    return {
        connect = function()
            return { disconnect = function() end }
        end,
    }
end

local function processor_env(db_name)
    local context = {
        composition = {
            empty = function() return true end,
        },
    }
    return {
        engine = {
            schema = { config = override_config(db_name) },
            context = {
                commit_notifier = notifier(),
                update_notifier = notifier(),
            },
        },
        context = context,
    }
end

local function action_context(selected, input, management)
    local segment = {
        _start = 0,
        _end = #input,
        selected_index = 0,
        menu = {
            prepare = function() return 0 end,
            get_candidate_at = function() return nil end,
        },
        get_selected_candidate = function()
            return selected
        end,
    }
    local context = {
        input = input,
        delete_count = 0,
        refresh_count = 0,
        options = { candidate_override_management = management or false },
    }
    context.composition = {
        empty = function() return false end,
        back = function() return segment end,
    }
    function context:get_option(name)
        return self.options[name] or false
    end
    function context:set_option(name, value)
        self.options[name] = value
    end
    function context:delete_current_selection()
        self.delete_count = self.delete_count + 1
        return true
    end
    function context:refresh_non_confirmed_composition()
        self.refresh_count = self.refresh_count + 1
    end
    return context, segment
end

local function action_env(store, memory, namespace)
    return {
        override_enable = true,
        override_store = store,
        override_memory = memory,
        override_memory_namespace = namespace or "translator",
        override_memory_attempted = memory ~= nil,
        override_management_option = "candidate_override_management",
        override_max_candidates = 50,
        override_pin_indicator = "📌",
    }
end

local old_memory = Memory
local old_level_db = LevelDb
local memory_create_count = 0
local memory_disconnect_count = 0
local memory_update_count = 0
local lazy_memory = {
    user_entries = {},
    dict_entries = {},
}
function lazy_memory:user_lookup()
    return true
end
function lazy_memory:iter_user()
    local index = 0
    return function()
        index = index + 1
        return self.user_entries[index]
    end
end
function lazy_memory:dictiter_lookup(code)
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
function lazy_memory:update_userdict(entry, commits, prefix)
    memory_update_count = memory_update_count + 1
    entry.commit_count = commits
    return true
end
function lazy_memory:disconnect()
    memory_disconnect_count = memory_disconnect_count + 1
end

local lazy_db = override_db()
LevelDb = function()
    return lazy_db
end
Memory = function()
    memory_create_count = memory_create_count + 1
    return lazy_memory
end

local init_env = processor_env("mohu_candidate_override_lazy_memory")
processor.init(init_env)
assert(memory_create_count == 0, "processor.init must not construct Memory")
assert(init_env.override_memory == nil)
assert(init_env.override_memory_attempted == false)
assert(init_env.override_memory_namespace == "translator")

local ordinary_store = {
    query = function() return {} end,
    set_hidden_calls = {},
    set_hidden = function(self, code, text, hidden)
        table.insert(self.set_hidden_calls, { code, text, hidden })
        return true
    end,
    available = function() return true end,
}
local ordinary_context, ordinary_segment = action_context(candidate("Ordinary", "phrase"), "oo", false)
local ordinary_result = subject.delete_or_restore(ordinary_context, ordinary_segment, "oo", action_env(ordinary_store))
assert(ordinary_result == 1)
assert(memory_create_count == 0, "ordinary hide must not construct Memory")
assert(#ordinary_store.set_hidden_calls == 1 and ordinary_store.set_hidden_calls[1][3] == true)

local move_a = candidate("MoveA", "phrase")
local move_b = candidate("MoveB", "phrase")
local move_context, move_segment = action_context(move_b, "mm", false)
move_segment.selected_index = 1
move_segment.menu = {
    prepare = function() return 2 end,
    get_candidate_at = function(_, index)
        return index == 0 and move_a or move_b
    end,
}
local move_store = {
    available = function() return true end,
    query = function() return {} end,
    write_order = function(_, texts) return #texts == 2 and texts[1] == "MoveB" end,
}
local move_before = memory_create_count
local move_env = action_env(move_store)
move_env.engine = { context = move_context }
local move_key = {
    keycode = 0x2d,
    ctrl = function() return true end,
    shift = function() return false end,
    release = function() return false end,
}
local move_result = processor.func(move_key, move_env)
assert(move_result == 1)
assert(memory_create_count == move_before, "candidate move must not construct Memory")

local builtin_store = {
    query = function()
        return { Builtin = { hidden = true, rank = -1, tick = 1 } }
    end,
    set_hidden_calls = {},
    set_hidden = function(self, code, text, hidden)
        table.insert(self.set_hidden_calls, { code, text, hidden })
        return true
    end,
}
local builtin_context, builtin_segment = action_context(candidate("Builtin", "phrase"), "bb", true)
local builtin_result = subject.delete_or_restore(builtin_context, builtin_segment, "bb", action_env(builtin_store))
assert(builtin_result == 1)
assert(memory_create_count == 0, "builtin hidden restore must not construct Memory")
assert(#builtin_store.set_hidden_calls == 1 and builtin_store.set_hidden_calls[1][3] == false)

lazy_memory.user_entries = {
    { text = "UserMade", custom_code = "u u", commit_count = 3 },
}
lazy_memory.dict_entries = { ["u u"] = {} }
local user_store = {
    query = function()
        return { UserMade = { hidden = true, rank = -1, tick = 1 } }
    end,
    set_hidden_calls = {},
    set_user_deleted_calls = {},
    set_hidden = function(self, code, text, hidden)
        table.insert(self.set_hidden_calls, { code, text, hidden })
        return true
    end,
    set_user_deleted = function(self, code, text, commits)
        table.insert(self.set_user_deleted_calls, { code, text, commits })
        return true
    end,
}
local user_candidate = candidate("UserMade", "user_phrase")
user_candidate.entry = { text = "UserMade", custom_code = "u u", commit_count = 3 }
local user_context, user_segment = action_context(user_candidate, "uu", false)
local user_env = action_env(user_store)
local user_before = memory_create_count
local user_result = subject.delete_or_restore(user_context, user_segment, "uu", user_env)
assert(user_result == 1)
assert(memory_create_count == user_before + 2, "user phrase refresh must retry Memory construction")
assert(#user_store.set_user_deleted_calls == 1)
assert(memory_update_count == 1)
assert(user_env.override_memory == lazy_memory)
assert(user_env.override_memory_attempted == true)
assert(user_env.override_memory_namespace == "translator")
assert(memory_disconnect_count == 1)

local learned_context, learned_segment = action_context(candidate("Builtin", "phrase"), "bb", false)
local learned_store = { query = function() return {} end }
local learned_env = action_env(learned_store)
local learned_before = memory_create_count
local learned_result = subject.reset_learned_weight(learned_context, learned_segment, "bb", learned_env)
assert(learned_result == 1)
assert(memory_create_count == learned_before + 1, "normal Ctrl+0 must create Memory")
local learned_again = subject.reset_learned_weight(learned_context, learned_segment, "bb", learned_env)
assert(learned_again == 1)
assert(memory_create_count == learned_before + 1, "normal Ctrl+0 must reuse Memory")

local management_clear_calls = 0
local management_context, management_segment = action_context(candidate("Builtin", "phrase"), "bb", true)
local management_env = action_env({
    available = function() return true end,
    clear_code = function() management_clear_calls = management_clear_calls + 1; return true end,
})
local management_engine = {
    context = management_context,
}
local management_key = {
    keycode = 0x30,
    ctrl = function() return true end,
    shift = function() return false end,
    release = function() return false end,
}
local management_before = memory_create_count
local old_engine = management_env.engine
management_env.engine = management_engine
local management_result = processor.func(management_key, management_env)
assert(management_result == 1)
assert(management_clear_calls == 1)
assert(memory_create_count == management_before, "management Ctrl+0 must not construct Memory")

local failed_store = {
    query = function() return {} end,
    set_hidden_calls = 0,
    set_user_deleted_calls = 0,
    set_hidden = function(self) self.set_hidden_calls = self.set_hidden_calls + 1; return true end,
    set_user_deleted = function(self) self.set_user_deleted_calls = self.set_user_deleted_calls + 1; return true end,
    release = function() end,
}
local failing_candidate = candidate("CannotConnect", "user_phrase")
failing_candidate.entry = { text = "CannotConnect", custom_code = "c c", commit_count = 1 }
local failed_context, failed_segment = action_context(failing_candidate, "cc", false)
local failed_env = action_env(failed_store)
local failed_old_memory = Memory
Memory = function()
    memory_create_count = memory_create_count + 1
    error("user dictionary unavailable")
end
local failed_result = subject.delete_or_restore(failed_context, failed_segment, "cc", failed_env)
assert(failed_result == 1)
assert(failed_segment.prompt == "〔无法连接用户词典〕", failed_segment.prompt)
assert(failed_store.set_hidden_calls == 0)
assert(failed_store.set_user_deleted_calls == 0)
Memory = failed_old_memory
local failed_before_retry = memory_create_count
subject.refresh_override_memory(failed_env)
assert(memory_create_count == failed_before_retry + 1, "refresh must retry a failed Memory construction")
assert(failed_env.override_memory ~= nil)
assert(failed_env.override_memory_attempted == true)
processor.fini(failed_env)

processor.fini(init_env)
assert(init_env.override_memory == nil)
assert(init_env.override_memory_attempted == false)
assert(init_env.override_memory_namespace == nil)

LevelDb = old_level_db
Memory = old_memory

print("candidate override logic: ok")
