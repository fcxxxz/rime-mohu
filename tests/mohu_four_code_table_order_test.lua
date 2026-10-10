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
    local env = { code_table = {}, quick_code_indicator = "`F", inject_table_words = true }
    for key, value in pairs(options or {}) do env[key] = value end
    translator.output_begin(env)
    local collided = translator.output_four_code_collision(env, rows, false)
    return collided, yielded
end
local collided, result = run({"芫", "演练"})
assert(collided and result[1].text == "芫" and result[2].text == "演练",
    "four-key character-before-word rows must retain master order")
collided, result = run({"演练", "芫"})
assert(collided and result[1].text == "演练" and result[2].text == "芫",
    "reversing master rows must reverse the character-word priority")
collided, result = run({"机难轻失", "进", "进去"})
assert(collided and result[1].text == "进" and result[2].text == "机难轻失" and
    result[3].text == "进去" and result[2].type == "mohu_secondary_word",
    "long four-key abbreviations must remain secondary and retain the IJRQ marker")
collided, result = run({"机难轻失", "进去", "禁区", "进"})
assert(collided and result[1].text == "进去" and result[2].text == "机难轻失" and
    result[3].text == "禁区" and result[4].text == "进",
    "abbreviations follow the first primary candidate rather than the whole primary block")
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
local function query_run(code, fixed, smart)
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
    }
    translator.func(code, {start = 0, _end = 4}, env)
    return yielded, fixed_queries, smart_queries
end
local fixed_queries, smart_queries
result, fixed_queries, smart_queries = query_run("yjlm", {"芫", "演练"}, {"演练", "眼帘"})
assert(result[1].text == "芫" and result[2].text == "演练" and
    fixed_queries == 1 and smart_queries == 1,
    "the normal four-key production branch must query each translator once and honor master order")
result, fixed_queries, smart_queries = query_run("johu", {"几乎"}, {"几虎", "几"})
assert(result[1].text == "几乎" and result[2].text == "几虎" and smart_queries == 1,
    "maintained two-character aliases must survive without querying smart twice")
result = query_run("niho", {"拟好", "你好"}, {"你好", "拟好", "你"})
assert(result[1].text == "你好" and result[2].text == "拟好",
    "noncolliding ordinary words must retain smart order rather than table order")
print("four-code master table ordering: ok")
