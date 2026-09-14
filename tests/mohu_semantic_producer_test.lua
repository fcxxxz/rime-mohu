package.path = "./lua/?.lua;" .. package.path

-- 语义谱系 ABI 测试（unicode / number 生产者）：
-- 受保护的合成候选必须在构造点携带不可变来源记录，并能穿透包装解析。

local meta = require("mohu_semantic_meta")

local yielded = {}
Candidate = function(candidate_type, start, finish, text, comment)
    return {
        type = candidate_type,
        start = start,
        _end = finish,
        text = text,
        comment = comment,
    }
end
yield = function(candidate)
    yielded[#yielded + 1] = candidate
end

local unicode = require("mohu_unicode")
local config = {
    get_string = function(_, path)
        assert(path == "recognizer/patterns/unicode")
        return "^U([0-9a-fA-F]+)$"
    end,
}
local unicode_env = { engine = { schema = { config = config } } }
local unicode_seg = {
    start = 0,
    _end = 5,
    has_tag = function(_, tag) return tag == "unicode" end,
}

yielded = {}
unicode("U62fc", unicode_seg, unicode_env)
assert(#yielded >= 2, "unicode input must yield the primary and near candidates")
local unicode_provenance = meta.resolve(yielded[1])
assert(type(unicode_provenance) == "table" and
    unicode_provenance.provenance_version == "mohu-unicode/v1" and
    unicode_provenance.source == "unicode" and
    unicode_provenance.candidate_type == "unicode" and
    unicode_provenance.protected == true and
    unicode_provenance.synthetic == true and
    unicode_provenance.native_score_kind == "unavailable" and
    unicode_provenance.code_point == 0x62fc,
    "unicode candidates must retain producer provenance")
local near_provenance = meta.resolve(yielded[2])
assert(type(near_provenance) == "table" and
    near_provenance.provenance_version == "mohu-unicode/v1" and
    near_provenance.code_point == 0x62fc * 16,
    "near unicode candidates must retain their own code point")

yielded = {}
unicode("U110000", {
    start = 0,
    _end = 7,
    has_tag = function() return true end,
}, unicode_env)
assert(#yielded == 1, "overflow input must yield exactly the overflow candidate")
local overflow_provenance = meta.resolve(yielded[1])
assert(type(overflow_provenance) == "table" and
    overflow_provenance.provenance_version == "mohu-unicode/v1" and
    overflow_provenance.code_point == nil,
    "overflow candidates must carry provenance without a code point")

local unicode_shadow = {
    type = "mohu_wrapped",
    get_genuine = function() return yielded[1] end,
}
assert(meta.resolve(unicode_shadow) == overflow_provenance,
    "unicode provenance must resolve through a wrapper")

local number = require("mohu_number")
yielded = {}
number("S123", { start = 0, _end = 4 })
assert(#yielded == 7, "number input must yield all conversions")
local number_provenance = meta.resolve(yielded[1])
assert(type(number_provenance) == "table" and
    number_provenance.provenance_version == "mohu-number/v1" and
    number_provenance.source == "number" and
    number_provenance.candidate_type == "S123" and
    number_provenance.protected == true and
    number_provenance.synthetic == true and
    number_provenance.native_score_kind == "unavailable" and
    number_provenance.conversion_kind == "〔常规〕",
    "number candidates must retain producer provenance")

print("semantic producer tests passed")
