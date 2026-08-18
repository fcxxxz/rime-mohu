package.path = "./lua/?.lua;" .. package.path

local subject = require("mohu_candidate_override")._test

local function candidate(text, cand_type)
    local cand = { text = text, type = cand_type, comment = "" }
    function cand:get_genuine()
        return self
    end
    return cand
end

local memory = {
    user_entries = {
        { text = "Built", custom_code = "aa;xx", commit_count = 3 },
        { text = "Made", custom_code = "b b", commit_count = 2 },
    },
    dict_entries = {
        ["aa"] = { { text = "Built" } },
        ["b b"] = {},
    },
    update_calls = {},
}

function memory:user_lookup()
    return true
end

function memory:iter_user()
    local index = 0
    return function()
        index = index + 1
        return self.user_entries[index]
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

function memory:update_userdict(entry, commits, prefix)
    table.insert(self.update_calls, { entry.text, entry.custom_code, commits, prefix })
    return true
end

local function reset_context(selected, input)
    local segment = {
        _start = 0,
        _end = #input,
        menu = {},
        get_selected_candidate = function()
            return selected
        end,
    }
    local context = { input = input, delete_count = 0, refresh_count = 0 }
    context.composition = {
        empty = function() return false end,
        back = function() return segment end,
    }
    function context:refresh_non_confirmed_composition()
        self.refresh_count = self.refresh_count + 1
    end
    function context:delete_current_selection()
        self.delete_count = self.delete_count + 1
        return true
    end
    return context, segment
end

local builtin_context, builtin_segment = reset_context(candidate("Built", "phrase"), "a")
local builtin_result = subject.reset_learned_weight(builtin_context, builtin_segment, "a", {
    override_memory = memory,
})
assert(builtin_result == 1)
assert(builtin_context.delete_count == 1)
assert(builtin_context.refresh_count == 1)
assert(#memory.update_calls == 0)

local created_context, created_segment = reset_context(candidate("Made", "user_phrase"), "b")
local created_result = subject.reset_learned_weight(created_context, created_segment, "b", {
    override_memory = memory,
})
assert(created_result == 1)
assert(created_context.delete_count == 0)
assert(created_context.refresh_count == 0)
assert(#memory.update_calls == 0)

print("candidate learned-weight reset: ok")
