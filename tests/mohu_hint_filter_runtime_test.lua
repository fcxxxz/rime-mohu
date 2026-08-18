package.path = "lua/?.lua;" .. package.path

local reverse_lookup_creations = 0
local reverse_lookup_calls = 0
local quick_code_hint = false
local aux_hint = false
local aux_table_loads = 0
local yielded = {}

ReverseLookup = function(dictionary)
    assert(dictionary == "test_fixed")
    reverse_lookup_creations = reverse_lookup_creations + 1
    return {
        lookup = function(_, text)
            reverse_lookup_calls = reverse_lookup_calls + 1
            if text == "三心二意" then return "sxey" end
            if text == "如果" then return "rg rugo" end
            return ""
        end,
    }
end

yield = function(candidate)
    table.insert(yielded, candidate)
end

ShadowCandidate = function(candidate, candidate_type, text, comment)
    return {
        type = candidate_type,
        text = text,
        comment = comment,
        preedit = candidate.preedit,
        get_genuine = function()
            return candidate:get_genuine()
        end,
    }
end

local mohu = require("mohu")
mohu.load_zrmdb = function()
    aux_table_loads = aux_table_loads + 1
    return {
        [utf8.codepoint("啊")] = " dt",
        [utf8.codepoint("如")] = "bd",
        [utf8.codepoint("果")] = "qe",
    }
end

local config = {
    get_bool = function(_, key)
        if key == "mohu/quick_code_hint_skip_chars" then return false end
        if key == "mohu/inject_fixed_words" then return true end
        return false
    end,
    get_string = function(_, key)
        if key == "mohu/quick_code_hint_dictionary" then return "test_fixed" end
        if key == "mohu/quick_code_hint_indicator" then return "⚡" end
        if key == "mohu/aux_priority_indicator" then return "↓" end
        return nil
    end,
}

local env = {
    name_space = "",
    engine = {
        schema = { config = config },
        context = {
            get_option = function(_, name)
                if name == "quick_code_hint" then return quick_code_hint end
                if name == "aux_hint" then return aux_hint end
                error("unexpected option: " .. tostring(name))
            end,
        },
    },
}

local function candidate()
    local result = {
        text = "三心二意",
        type = "phrase",
        comment = "",
        preedit = "sj xn er yi",
    }
    result.get_genuine = function(self) return self end
    return result
end

local function translation(item)
    return {
        iter = function()
            local done = false
            return function()
                if done then return nil end
                done = true
                return item
            end
        end,
    }
end

local filter = require("mohu_hint_filter")
filter.init(env)
assert(reverse_lookup_creations == 0)

local first = candidate()
filter.func(translation(first), env)
assert(first.comment == "")
assert(reverse_lookup_creations == 0)
assert(reverse_lookup_calls == 0)
assert(aux_table_loads == 0)

quick_code_hint = true
yielded = {}
local second = candidate()
filter.func(translation(second), env)
assert(second.comment == "⚡sxey")
assert(reverse_lookup_creations == 1)
assert(reverse_lookup_calls == 1)

yielded = {}
local third = candidate()
filter.func(translation(third), env)
assert(third.comment == "⚡sxey")
assert(reverse_lookup_creations == 1)
assert(reverse_lookup_calls == 2)

quick_code_hint = false
yielded = {}
local fourth = candidate()
filter.func(translation(fourth), env)
assert(fourth.comment == "")
assert(reverse_lookup_creations == 1)
assert(reverse_lookup_calls == 2)

aux_hint = true
yielded = {}
local aux_char = candidate()
aux_char.text = "啊"
aux_char.preedit = "a"
filter.func(translation(aux_char), env)
assert(yielded[1].comment == "dt")
assert(aux_table_loads == 1)

yielded = {}
filter.func(translation(aux_char), env)
assert(aux_table_loads == 1)

aux_hint = false
env.is_auxfilter = true
yielded = {}
local hidden_match = candidate()
hidden_match.text = "连接"
hidden_match.comment = "y↓"
filter.func(translation(hidden_match), env)
assert(yielded[1].comment == "")
assert(yielded[1]:get_genuine().comment == "y↓")

quick_code_hint = true
yielded = {}
local quick_without_aux = candidate()
quick_without_aux.text = "如果"
quick_without_aux.preedit = "ru go"
quick_without_aux.comment = "y"
filter.func(translation(quick_without_aux), env)
assert(yielded[1].comment == "⚡rg")
assert(yielded[1]:get_genuine().comment == "y")

quick_code_hint = true
aux_hint = true
yielded = {}
env.is_auxfilter = true
local combined = candidate()
combined.text = "如果"
combined.preedit = "ru go"
filter.func(translation(combined), env)
assert(combined.comment == "bd qe ¦ ⚡rg")

filter.fini(env)

print("runtime quick-code hint tests passed")
