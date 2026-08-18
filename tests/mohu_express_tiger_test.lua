package.path = "lua/?.lua;" .. package.path

log = {
    error = function(message)
        error(message)
    end,
}

rime_api = {
    get_user_data_dir = function()
        return "."
    end,
}

local mohu = require("mohu")
local translator = require("mohu_express_translator")

local tiger_rank = mohu.load_tiger_rank()
assert(type(tiger_rank) == "table")
assert(type(tiger_rank[utf8.codepoint("和")]) == "number")

local candidates = {
    { text = "昭" },
    { text = "照" },
    { text = "照明" },
    { text = "晁" },
}

local ordered = translator.order_exact_four_candidates(
    candidates,
    function(candidate)
        return candidate.text == "照"
    end,
    1
)

local expected = { "昭", "照", "照明", "晁" }
for index, text in ipairs(expected) do
    assert(ordered[index].text == text, index .. ": " .. ordered[index].text)
end
for index = 1, #ordered do
    assert(ordered[index].quality == nil)
end

assert(translator.order_four_code_word_and_fixed_chars == nil)
assert(translator.normalize_four_code_two_char_first_choice_quality == nil)

local word_filter_candidate = { comment = "nf" }
translator.apply_word_filter_hint(word_filter_candidate, false, nil)
assert(word_filter_candidate.comment == "")

word_filter_candidate.comment = "nf"
translator.apply_word_filter_hint(word_filter_candidate, true, nil)
assert(word_filter_candidate.comment == "nf")

word_filter_candidate.comment = "nf"
translator.apply_word_filter_hint(word_filter_candidate, true, "🎯")
assert(word_filter_candidate.comment == "🎯")

print("Mohu express IJRQ ordering tests passed")
