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

for _, path in ipairs({
    "mohu_zrm.schema.yaml",
    "mohu_zrm_aux.schema.yaml",
    "mohu_zrm_fixed.schema.yaml",
    "mohu_zrm_sentence.schema.yaml",
    "mohu_flypy.schema.yaml",
    "mohu_flypy_aux.schema.yaml",
    "mohu_flypy_fixed.schema.yaml",
    "mohu_flypy_sentence.schema.yaml",
}) do
    assert_contains(path, "  pin:\n    __include: mohu:/pin")
    assert_before(
        path,
        "    - lua_processor@*mohu_candidate_override*override_processor",
        "    - lua_processor@*mohu_pin*pin_processor"
    )
    assert_before(
        path,
        "    - lua_processor@*mohu_pin*pin_processor",
        "    - key_binder"
    )
    assert_before(
        path,
        "    - lua_processor@*mohu_pin*pin_processor",
        "    - ascii_composer"
    )
end

for _, prefix in ipairs({ "mohu_zrm", "mohu_flypy" }) do
    local fixed = prefix .. "_fixed.schema.yaml"
    local sentence = prefix .. "_sentence.schema.yaml"
    assert_contains(fixed, [[  alphabet: 'abcdefghijklmnopqrstuvwxyz/=;']])
    assert_contains(fixed, "  auto_select_pattern: ^;(\\w|;)+")
    assert_contains(fixed, [[    panacea: "^[a-z]*/{1,2}[a-z']*$"]])
    assert_contains(sentence, "    - lua_processor@*mohu_pin*pin_processor")
    assert_contains(sentence, "    - lua_translator@*mohu_pin*panacea_translator")
    assert_contains(sentence, "    - lua_filter@*mohu_pin*pin_filter")
    assert_contains(sentence, [[    panacea: "^[a-z]*/{1,2}[a-z']*$"]])
    assert_before(
        sentence,
        "    - lua_processor@*mohu_candidate_override*override_processor",
        "    - lua_processor@*mohu_pin*pin_processor"
    )
    assert_before(
        sentence,
        "    - lua_filter@*mohu_pin*pin_filter",
        "    - lua_filter@*mohu_candidate_override*override_order_filter"
    )
end

print("guided word creation schema configuration tests passed")
