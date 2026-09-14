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

-- 四码字词避让成对表
local pairs_zrm = mohu.load_four_code_yield_pairs("zrm")
assert(type(pairs_zrm) == "table", "zrm yield pairs must load")
assert(type(pairs_zrm["汽车"]) == "table", "汽车 must be a zrm yield word")
assert(pairs_zrm["汽车"]["缉"] == true, "汽车 must yield 缉 in zrm")
assert(pairs_zrm["职工"]["址"] == true, "职工 must yield 址 in zrm")
assert(pairs_zrm["一点"]["嗌"] == true, "一点 must yield 嗌 in zrm")

local pairs_flypy = mohu.load_four_code_yield_pairs("flypy")
assert(type(pairs_flypy) == "table", "flypy yield pairs must load")
assert(pairs_flypy["一定"]["嗌"] == true, "一定 must yield 嗌 in flypy")
assert(pairs_zrm["一定"] == nil, "scheme tables must stay separate")

assert(mohu.load_four_code_yield_pairs("zrm") == pairs_zrm, "loader must cache")

-- 四码让位候选资格：仅完整二字真词
local function mock_candidate(text, dynamic)
    local value = { text = text, type = "phrase" }
    function value:get_genuine() return self end
    function value:get_dynamic_type() return dynamic or "Phrase" end
    return value
end

assert(translator.is_four_code_yield_word_candidate(mock_candidate("汽车")) == true)
assert(translator.is_four_code_yield_word_candidate(mock_candidate("英特尔")) == false,
    "three-character candidates must not compete")
assert(translator.is_four_code_yield_word_candidate(mock_candidate("影")) == false,
    "single characters must not compete")
assert(translator.is_four_code_yield_word_candidate(mock_candidate("应哦", "Sentence")) == false,
    "dynamic sentences must not compete")

local shadow = { text = "影哦", type = "phrase" }
function shadow:get_genuine() return mock_candidate("汽车") end
function shadow:get_dynamic_type() return "Shadow" end
assert(translator.is_four_code_yield_word_candidate(shadow) == true,
    "wrapped candidates must be judged by their genuine candidate")

-- 流扫描：跳过不合格候选，在首个表内词处停下并保留已拉取的候选
local function list_iter(items)
    local index = 0
    return function()
        index = index + 1
        return items[index]
    end
end

local fake_pairs = {
    ["试词"] = { ["字"] = true },
}

local buffer, word = translator.find_four_code_yield_word(list_iter({
    mock_candidate("字"),
    mock_candidate("伪句", "Sentence"),
    mock_candidate("试词"),
    mock_candidate("尾词"),
}), fake_pairs, 20)
assert(word ~= nil and word.text == "试词", "scan must stop at the first table word")
assert(#buffer == 3, "buffer must keep every pulled candidate")

local buffer2, word2 = translator.find_four_code_yield_word(list_iter({
    mock_candidate("甲乙"),
    mock_candidate("丙丁"),
}), fake_pairs, 20)
assert(word2 == nil and #buffer2 == 2, "no table word means no yield")

local limited, limited_word = translator.find_four_code_yield_word(list_iter({
    mock_candidate("甲"), mock_candidate("乙"), mock_candidate("丙"),
    mock_candidate("丁"), mock_candidate("戊"),
}), fake_pairs, 3)
assert(limited_word == nil and #limited == 3, "scan must respect the limit")

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
    runtime_primary = {},
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
    fixed_provenance.lexical_translator == "fixed_primary" and
    fixed_provenance.candidate_type == "table" and
    fixed_provenance.native_score_kind == "unavailable_rime_lexical",
    "fixed candidates must retain lexical provenance at the query boundary")

print("Mohu express IJRQ ordering tests passed")
