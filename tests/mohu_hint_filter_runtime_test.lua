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
local semantic_meta = require("mohu_semantic_meta")
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
        if key == "mohu/inject_table_words" then return true end
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
assert(semantic_meta.bind(hidden_match, { origin = "native" }))
filter.func(translation(hidden_match), env)
assert(yielded[1].comment == "")
assert(yielded[1]:get_genuine().comment == "y↓")
assert(semantic_meta.resolve(yielded[1]).origin == "native",
  "hint ShadowCandidate must preserve producer provenance")

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

local pin_phrase={text="如果",type="pinned",get_dynamic_type=function() return "Phrase" end}
local hinted=require("mohu_hint_filter").get_auxcode_hint({is_auxfilter=true,
 aux_table={[utf8.codepoint("如")]="bd",[utf8.codepoint("果")]="qe"}},pin_phrase,pin_phrase,true)
assert(hinted=="bd qe","pinned learning Phrase retains its word auxiliary hints")
print("pinned Phrase word hints: ok")

-- A fixed-table ordering marker is not evidence of an abbreviated word.
-- Preserve the genuine Phrase and a nonvisual identity for later filters,
-- while hiding the bare icon when the whole double-pinyin word was entered.
env.is_auxfilter = false
quick_code_hint, aux_hint = false, false
filter.init(env)
yielded = {}
local full_word = candidate()
full_word.text, full_word.preedit, full_word.comment = "金丹", "jn dj", "⚡️"
filter.func(translation(full_word), env)
assert(yielded[1].comment == "", "jndj is 金丹's full spelling, not a quick code")
assert(yielded[1]:get_genuine() == full_word and full_word.comment == "" and
    yielded[1].comment == "" and yielded[1].type == "mohu_table_full_word" and
    full_word.type == "phrase",
    "hiding the icon must retain the learning Phrase and fixed-order protection")
quick_code_hint = true
yielded = {}
full_word.comment = "⚡️"
filter.func(translation(full_word), env)
assert(yielded[1].comment == "", "enabling quick-code hints must not invent an abbreviation")
quick_code_hint = false
for _, item in ipairs({{"哪里", "nal"}, {"机难轻失", "jnqu"}}) do
    yielded = {}
    local short_word = candidate()
    short_word.text, short_word.preedit, short_word.comment = item[1], item[2], "⚡️"
    filter.func(translation(short_word), env)
    assert(yielded[1].comment == "⚡️", "genuine shortened word codes retain their icon")
end
filter.fini(env)
print("full word versus abbreviated word display: ok")
local ordinary = candidate()
ordinary.text, ordinary.preedit, ordinary.comment = "金丹", "jn dj", ""
assert(filter.display_full_word({quick_code_indicator = ""}, ordinary) == ordinary,
    "an empty indicator must not classify every ordinary word as a protected table entry")
