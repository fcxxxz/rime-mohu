local function read_file(path)
    local file = assert(io.open(path, "r"))
    local contents = file:read("*a")
    file:close()
    return contents
end

local function assert_contains(path, needle)
    local contents = read_file(path)
    assert(contents:find(needle, 1, true), path .. " is missing: " .. needle)
end

local function assert_not_contains(path, needle)
    local contents = read_file(path)
    assert(not contents:find(needle, 1, true), path .. " unexpectedly contains: " .. needle)
end

local schemas = {
    { "mohu_zrm.schema.yaml", "mohu_zrm", "魔虎·自然码", "mohu_zrm_tiger_prefix2", "mohu_zrm_custom_phrases" },
    { "mohu_zrm_fixed.schema.yaml", "mohu_zrm_fixed", "字词·魔虎·自然码", "mohu_zrm_fixed_tiger_prefix2", "mohu_zrm_custom_phrases" },
    { "mohu_zrm_sentence.schema.yaml", "mohu_zrm_sentence", "整句·魔虎·自然码", "mohu_zrm_sentence_tiger_prefix2", "mohu_zrm_custom_phrases" },
    { "mohu_flypy.schema.yaml", "mohu_flypy", "魔虎·小鹤", "mohu_flypy_tiger_prefix2", "mohu_flypy_custom_phrases" },
    { "mohu_flypy_aux.schema.yaml", "mohu_flypy_aux", "辅筛·魔虎·小鹤", "mohu_flypy_aux_tiger_prefix2", "mohu_flypy_custom_phrases" },
    { "mohu_flypy_sentence.schema.yaml", "mohu_flypy_sentence", "整句·魔虎·小鹤", "mohu_flypy_sentence_tiger_prefix2", "mohu_flypy_custom_phrases" },
    { "mohu_flypy_fixed.schema.yaml", "mohu_flypy_fixed", "字词·魔虎·小鹤", "mohu_flypy_fixed_tiger_prefix2", "mohu_flypy_custom_phrases" },
}

for _, schema in ipairs(schemas) do
    local path, schema_id, name, user_dict, custom_phrases = table.unpack(schema)
    assert_contains(path, "  schema_id: " .. schema_id .. "\n")
    assert_contains(path, "  name: " .. name .. "\n")
    assert_contains(path, '  version: "20260814"\n')
    assert_contains(path, "虎码最长主码前两码")
    assert_contains(path, "  user_dict: " .. user_dict .. "\n")
    assert_contains(path, "  user_dict: " .. custom_phrases .. "\n")
    assert_contains(path, "    states: [ 常用字, 全字集 ]\n")
end

for _, schema in ipairs(schemas) do
    local path = schema[1]
    assert_contains(path, "  dictionary: tiger\n")
    assert_contains(path, '  prefix: "ohm"\n')
end

assert_contains("mohu_zrm_fixed.dict.yaml", "#----------生成单字----------#\n")
assert_contains("mohu_flypy_fixed.dict.yaml", "#----------生成单字----------#\n")
assert_not_contains("mohu_zrm_fixed.dict.yaml", "  - mohu_zrm_tiger_fixed\n")
assert_not_contains("mohu_flypy_fixed.dict.yaml", "  - mohu_flypy_tiger_fixed\n")
assert_contains("Makefile", "lua/tiger_rank.txt")

print("Tiger auxiliary schema configuration tests passed")
