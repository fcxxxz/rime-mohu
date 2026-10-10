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

local function mock_candidate(text, dynamic)
    local value = { text = text, type = "phrase" }
    function value:get_genuine() return self end
    function value:get_dynamic_type() return dynamic or "Phrase" end
    return value
end

local step = 0
local merged = translator.chain_candidates({ "a", "b" }, function()
    step = step + 1
    return "s" .. step
end)
assert(merged() == "a" and merged() == "b" and merged() == "s1" and merged() == "s2",
    "chained iterator must replay the buffer before resuming the stream")

-- 语义谱系：fixed/smart 候选必须在查询边界绑定不可变来源事实
local semantic_meta = require("mohu_semantic_meta")

local function fake_translation(cands)
    local index = 0
    return {
        iter = function()
            return function()
                index = index + 1
                return cands[index]
            end
        end,
    }
end

local smart_stub = {}
local smart_env = { contextual_translator = smart_stub }
function smart_stub.query(_, input, seg)
    assert(input == "niho" and seg.start == 0)
    local function lexical(text, cand_type)
        local cand = { text = text, type = cand_type, preedit = "ni hao", comment = "" }
        function cand:get_genuine() return self end
        return cand
    end
    return fake_translation({ lexical("你好", "phrase"), lexical("拟好", "user_phrase") })
end
for bound in translator.raw_query_smart(smart_env, "niho", { start = 0, _end = 4 }, false) do
    local provenance = semantic_meta.resolve(bound)
    assert(type(provenance) == "table" and
        provenance.provenance_version == "mohu-lexical/v1" and
        provenance.source == "smart" and
        provenance.lexical_translator == "smart" and
        provenance.candidate_type == bound.type and
        provenance.genuine_type == bound.type and
        provenance.native_score_kind == "unavailable_rime_lexical" and
        provenance.query_preedit == "ni hao",
        "smart candidates must retain lexical provenance at the query boundary")
end

local fixed_yielded = {}
local original_yield = yield
yield = function(candidate)
    table.insert(fixed_yielded, candidate)
end
local fixed_char = { text = "佳", type = "table", preedit = "jwrg", comment = "" }
function fixed_char:get_genuine() return self end
local fixed_env = {
    engine = { context = { get_option = function() return true end } },
    code_table = {},
    quick_code_indicator = "`F",
}
translator.output_begin(fixed_env)
translator.output_fixed_chars_first(
    fixed_env,
    fake_translation({ fixed_char }),
    false,
    true
)
yield = original_yield
assert(#fixed_yielded == 1)
local fixed_provenance = semantic_meta.resolve(fixed_yielded[1])
assert(type(fixed_provenance) == "table" and
    fixed_provenance.provenance_version == "mohu-lexical/v1" and
    fixed_provenance.source == "fixed" and
    fixed_provenance.lexical_translator == "code_table" and
    fixed_provenance.candidate_type == "table" and
    fixed_provenance.native_score_kind == "unavailable_rime_lexical",
    "fixed candidates must retain lexical provenance at the query boundary")

-- Short-code rows are the authority for both characters and words. Do not
-- silently promote a character ahead of a word placed first by the maintainer.
local table_rows = {}
yield = function(candidate) table_rows[#table_rows + 1] = candidate end
local order_env = { code_table = {}, quick_code_indicator = "⚡" }
translator.output_begin(order_env)
translator.output_table_order(order_env, fake_translation({ mock_candidate("哪里"), mock_candidate("𦰡") }), false)
assert(table_rows[1].text == "哪里" and table_rows[2].text == "𦰡", "table row order must include words")
table_rows = {}
translator.output_begin(order_env)
translator.output_table_order(order_env, fake_translation({ mock_candidate("哪里"), mock_candidate("𦰡") }), true)
assert(#table_rows == 1 and table_rows[1].text == "𦰡", "partial sentence selection must retain its char-only constraint")
yield = original_yield

local saved_shadow = ShadowCandidate
ShadowCandidate = function(original, kind, text, comment)
    local value = { type = kind, text = text, comment = comment, preedit = original.preedit }
    function value:get_genuine() return original end
    return value
end
local abbreviated = mock_candidate("机难轻失")
abbreviated.type, abbreviated.preedit, abbreviated.comment = "table", "jnqu", "`F"
local marked = translator.secondary_word(abbreviated)
assert(marked.type == "mohu_secondary_word" and marked:get_genuine() == abbreviated,
    "secondary word marker must survive Rime while retaining the original candidate")
assert(marked.comment == "`F" and abbreviated.type == "table",
    "secondary marker must not change fixed-candidate provenance or reordering")
ShadowCandidate = saved_shadow

print("Mohu express IJRQ ordering tests passed")

local missing_fixed = translator.collect_missing_fixed_words(
    {{text = "几乎"}, {text = "噗嗤"}, {text = "三心二意"}},
    {{text = "几虎"}, {text = "噗嗤"}, {text = "普通词"}})
assert(#missing_fixed == 2 and missing_fixed[1].text == "几乎" and
    missing_fixed[2].text == "三心二意",
    "four-key fixed words missing from smart candidates must be recoverable")
print("four-key fixed word fallback: ok")
