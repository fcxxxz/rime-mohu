package.path = "lua/?.lua;" .. package.path
local translator = require("mohu_express_translator")
local yielded = {}
yield = function(cand) yielded[#yielded + 1] = cand end
ShadowCandidate = function(original, kind, text, comment)
    local cand = { type = kind, text = text, comment = comment }
    function cand:get_genuine() return original end
    return cand
end
local function candidate(text)
    local cand = { text = text, type = "table", comment = "", preedit = "yjlm" }
    function cand:get_genuine() return self end
    return cand
end
local function run(texts, options)
    yielded = {}
    local rows = {}
    for _, text in ipairs(texts) do rows[#rows + 1] = candidate(text) end
    local env = {
        code_table = {}, quick_code_indicator = "`F", inject_table_words = true,
        rfixed = function()
            local map = options and options.reverse_codes
            return { lookup = function(_, char) return (map and map[char]) or "" end }
        end,
    }
    for key, value in pairs(options or {}) do env[key] = value end
    translator.output_begin(env)
    local collided = translator.output_four_code_collision(env, rows, false, options and options.input or "yjlm")
    return collided, yielded
end
-- Mixed slots (competing char + two-character word rows): words take smart
-- ranking; row order only places chars. Case B (char rows first): the char
-- keeps its curated top slot and nothing else is frozen.
local collided, result = run({"芫", "演练"})
assert(not collided and #result == 1 and result[1].text == "芫",
    "a char leading the word rows must not freeze the word order")
collided, result = run({"演练", "芫"})
assert(not collided and #result == 0,
    "mixed slots keep smart ranking regardless of master row order")
-- Case B (char rows before the first word row): the char keeps its curated
-- top slot; trailing chars queue behind the first output.
collided, result = run({"卟", "意志", "已"},
    { input = "ybui", reverse_codes = { ["已"] = "yiv yivi" } })
assert(not collided and #result == 1 and result[1].text == "卟",
    "a char leading the word rows must keep its curated top slot")
-- Pure character slots (no two-character word rows) retain the row-order block.
collided, result = run({"卟", "芫"})
assert(collided and result[1].text == "卟" and result[2].text == "芫",
    "pure character slots must retain master row order")
collided, result = run({"游手好闲", "玉石混淆", "鹆", "鱼水和谐"})
assert(result[1].text == "鹆" and result[2].text == "游手好闲" and
    result[3].text == "玉石混淆" and result[4].text == "鱼水和谐",
    "the secondary block must preserve its master row order")
collided, result = run({"几乎", "几虎"})
assert(not collided and #result == 0,
    "without an exact fixed character, ordinary two-character words retain smart ranking")
collided, result = run({"游手好闲", "鹆"}, { inject_table_words = false })
assert(collided and #result == 1 and result[1].text == "鹆",
    "disabling long table-word injection must still work")

-- A character yielded by 出简让全 (its short code prefixes the input) no
-- longer counts as competing: the slot keeps smart ranking.
collided, result = run({"意志", "抑制", "一致", "已"},
    { input = "yivi", reverse_codes = { ["已"] = "yiv yivi" } })
assert(not collided and #result == 0,
    "a short-code-yielded fallback char must not trigger the collision block")
-- A competing char after the word rows (被顶字) is delegated to the
-- post-first-choice injection block instead of freezing the words.
collided, result = run({"意志", "卟"},
    { input = "ybui", reverse_codes = { ["卟"] = "budy" } })
assert(not collided and #result == 0,
    "a displaced char must not freeze the word rows")
-- The gate must not be fooled by shorter codes that are not input prefixes.
collided, result = run({"厂", "暗笑"},
    { input = "anxc", reverse_codes = { ["厂"] = "ih ihs" } })
assert(not collided and #result == 1 and result[1].text == "厂",
    "a non-prefix short code keeps the char competing and leading")

-- Exercise the production branch, including lazy Translation replay and
-- query counts. The ordering helper alone cannot prove pipeline wiring.
local function translation(texts)
    local index = 0
    return { iter = function()
        return function()
            index = index + 1
            if texts[index] then return candidate(texts[index]) end
        end
    end }
end
local function query_run(code, fixed, smart, reverse_codes)
    yielded = {}
    local fixed_queries, smart_queries = 0, 0
    local table_stub = { query = function()
        fixed_queries = fixed_queries + 1
        return translation(fixed)
    end }
    local smart_stub = { query = function()
        smart_queries = smart_queries + 1
        return translation(smart)
    end }
    local env = {
        engine = { context = { input = code, get_option = function() return false end } },
        code_table = table_stub, contextual_translator = smart_stub,
        quick_code_indicator = "`F", inject_table_words = true, inject_table_chars = true,
        rfixed = function()
            return { lookup = function(_, char) return (reverse_codes and reverse_codes[char]) or "" end }
        end,
    }
    translator.func(code, {start = 0, _end = 4}, env)
    return yielded, fixed_queries, smart_queries
end
local fixed_queries, smart_queries
-- Post-10-13 master order on yjlm is 演练 before 芫 (displaced char second):
-- words rank by smart, 芫 stays secondary via the injection block.
result, fixed_queries, smart_queries = query_run("yjlm", {"演练", "芫"}, {"演练", "眼帘"})
assert(result[1].text == "演练" and result[2].text == "芫" and result[3].text == "眼帘" and
    fixed_queries == 2 and smart_queries == 1,
    "mixed slots keep smart ranking with the displaced char injected after the first choice")
result, fixed_queries, smart_queries = query_run("johu", {"几乎"}, {"几虎", "几"})
assert(result[1].text == "几乎" and result[2].text == "几虎" and smart_queries == 1,
    "maintained two-character aliases must survive without querying smart twice")
result = query_run("niho", {"拟好", "你好"}, {"你好", "拟好", "你"})
assert(result[1].text == "你好" and result[2].text == "拟好",
    "noncolliding ordinary words must retain smart order rather than table order")
result, fixed_queries, smart_queries = query_run("yivi",
    {"意志", "抑制", "一致", "已"},
    {"一直", "一只", "一致", "一支", "意志", "抑制", "已"},
    { ["已"] = "yiv yivi" })
assert(result[1].text == "一直" and result[3].text == "一只" and result[4].text == "一致" and
    result[6].text == "意志" and result[7].text == "抑制" and
    fixed_queries == 2 and smart_queries == 1,
    "a short-code-yielded fallback char must let the slot keep smart ranking")
result, fixed_queries, smart_queries = query_run("bixu",
    {"必须", "庳", "变成现实"},
    {"必须", "必需", "碧虚", "比"},
    { ["庳"] = "" })
assert(result[1].text == "必须" and result[2].text == "庳" and result[3].text == "变成现实" and
    result[4].text == "必需" and result[3].type == "mohu_secondary_word",
    "mixed collision slots keep smart word ranking with the char and quick word injected after the first choice")
result, fixed_queries, smart_queries = query_run("anxc",
    {"厂", "暗笑"},
    {"暗笑", "安享"},
    { ["厂"] = "ih ihs" })
assert(result[1].text == "厂" and result[2].text == "暗笑",
    "a char leading the word rows keeps its curated top slot above smart words")
print("four-code master table ordering: ok")
