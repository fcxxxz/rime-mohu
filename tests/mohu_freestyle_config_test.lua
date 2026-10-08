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

local function assert_before(path, first, second)
    local contents = read_file(path)
    local first_position = assert(contents:find(first, 1, true), path .. " is missing: " .. first)
    local second_position = assert(contents:find(second, 1, true), path .. " is missing: " .. second)
    assert(first_position < second_position, path .. " must place " .. first .. " before " .. second)
end

assert_contains("mohu.yaml", "pin:\n  enable: true")
assert_contains("mohu.yaml", "    freestyle: true")
assert_contains("mohu.yaml", '    infix: "//"')

for _, path in ipairs({ "mohu_zrm.schema.yaml", "mohu_flypy.schema.yaml" }) do
    assert_contains(path, "  pin:\n    __include: mohu:/pin")
    assert_before(path, "    - lua_processor@*mohu_pin*pin_processor", "    - ascii_composer")
    assert_before(path, "    - ascii_composer", "    - lua_processor@*mohu_candidate_override*override_processor")
    assert_before(path, "    - lua_processor@*mohu_pin*pin_processor", "    - key_binder")
end
for _, prefix in ipairs({ "mohu_zrm", "mohu_flypy" }) do
    local sentence = prefix .. "_sentence_core.schema.yaml"
    assert_contains(sentence, "script_translator")
    assert(not read_file(sentence):find("pin_processor", 1, true))
    assert(not read_file(sentence):find("pin_filter", 1, true))
end

print("guided word creation schema configuration tests passed")
