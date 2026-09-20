package.path = "./lua/?.lua;" .. package.path

Candidate = function(candidate_type, start, finish, text, comment)
    return {
        type = candidate_type,
        start = start,
        _end = finish,
        text = text,
        comment = comment,
    }
end

local yielded = {}
yield = function(candidate)
    yielded[#yielded + 1] = candidate
end

local hint = require("mohu_symbol_hint")
local function translate(input)
    yielded = {}
    hint(input, { start = 0, _end = #input }, {})
    return yielded
end

local all = translate("/")
assert(#all > 10)
local all_text = {}
for _, candidate in ipairs(all) do
    all_text[candidate.text] = true
end
assert(all_text["/bd 标点符号"])
assert(all_text["/date 日期"])
assert(all_text["/rq 日期"])
assert(all_text["/nl 农历"])
assert(all_text["/sj 时间"])
assert(all_text["/xq 星期"])
assert(all_text["/jq 节气"])
assert(all_text["/rqfh 日期符号"])
assert(all_text["/sjfh 时间符号"])
assert(all_text["/xqfh 象棋符号"])
assert(all_text["/jqfh 节气符号"])
assert(all_text["/gl 候选管理"])
for _, code in ipairs({ "riqi", "nongli", "shijian", "xingqi", "jieqi" }) do
    assert(not all_text["/" .. code .. " 日期"])
    assert(not all_text["/" .. code .. " 农历"])
    assert(not all_text["/" .. code .. " 时间"])
    assert(not all_text["/" .. code .. " 星期"])
    assert(not all_text["/" .. code .. " 节气"])
end
assert(not all_text["/dcck 导出词库"])
assert(not all_text["/djs 倒计时"])
assert(not all_text["/chol 火星文"])
assert(not all_text["/baidu 百度"])
for _, code in ipairs({ "frq", "orzh", "fnl", "wtxs", "fsj", "fuj", "okao", "fxq", "olzh", "lzvq" }) do
    assert(not all_text["/" .. code .. " 日期"])
    assert(not all_text["/" .. code .. " 农历"])
    assert(not all_text["/" .. code .. " 时间"])
    assert(not all_text["/" .. code .. " 星期"])
    assert(not all_text["/" .. code .. " 节气"])
end

local prefix = translate("/p")
for _, candidate in ipairs(prefix) do
    assert(candidate.text:sub(1, 2) == "/p")
end

assert(#translate("/date") == 0)
assert(#translate("\\bd") == 0)

local shijian = require("mohu_shijian")
local date_route = assert(shijian._test and shijian._test.date_route)
assert(date_route("/date") == "date")
assert(date_route("/rq") == "date")
assert(date_route("/cdate") == "cdate")
assert(date_route("/nl") == "cdate")
assert(date_route("/time") == "time")
assert(date_route("/sj") == "time")
assert(date_route("/week") == "week")
assert(date_route("/xq") == "week")
assert(date_route("/fjq") == "jieqi")
assert(date_route("/jq") == "jieqi")
assert(date_route("odate") == "date")
assert(date_route("ojq") == "jieqi")
assert(date_route("/djs") == nil)
for _, input in ipairs({ "/riqi", "/nongli", "/shijian", "/xingqi", "/jieqi" }) do
    assert(date_route(input) == nil, input)
end
for _, input in ipairs({ "/frq", "/orzh", "/fnl", "/wtxs", "/fsj", "/fuj", "/okao", "/fxq", "/olzh", "/lzvq" }) do
    assert(date_route(input) == nil, input)
end

for _, input in ipairs({
    "/date", "/rq", "/cdate", "/nl", "/time", "/sj",
    "/week", "/xq", "/fjq", "/jq",
}) do
    yielded = {}
    shijian.func(input, { start = 0, _end = #input }, {})
    assert(#yielded > 0, input)
end

-- 语义谱系：symbol_hint 与 temporal 候选必须携带受保护的合成来源记录
local semantic_meta = require("mohu_semantic_meta")
local hint_candidates = translate("/")
local hint_provenance = semantic_meta.resolve(hint_candidates[1])
assert(type(hint_provenance) == "table" and
    hint_provenance.provenance_version == "mohu-symbol-hint/v1" and
    hint_provenance.source == "symbol_hint" and
    hint_provenance.candidate_type == "symbol_hint" and
    hint_provenance.protected == true and
    hint_provenance.synthetic == true and
    hint_provenance.native_score_kind == "unavailable" and
    type(hint_provenance.symbol_code) == "string",
    "symbol_hint candidates must retain producer provenance")
local hint_shadow = {
    type = "mohu_wrapped",
    get_genuine = function() return hint_candidates[1] end,
}
assert(semantic_meta.resolve(hint_shadow) == hint_provenance,
    "symbol_hint provenance must resolve through a wrapper")

yielded = {}
shijian.func("/date", { start = 0, _end = 5 }, {})
assert(#yielded > 0)
local temporal_provenance = semantic_meta.resolve(yielded[1])
assert(type(temporal_provenance) == "table" and
    temporal_provenance.provenance_version == "mohu-temporal/v1" and
    temporal_provenance.source == "temporal" and
    temporal_provenance.candidate_type == "date" and
    temporal_provenance.protected == true and
    temporal_provenance.synthetic == true and
    temporal_provenance.native_score_kind == "unavailable",
    "temporal candidates must retain producer provenance")

print("symbol command tests passed")
