local schemas = {
    "mohu_zrm.schema.yaml",
    "mohu_zrm_fixed.schema.yaml",
    "mohu_zrm_sentence.schema.yaml",
    "mohu_flypy.schema.yaml",
    "mohu_flypy_aux.schema.yaml",
    "mohu_flypy_sentence.schema.yaml",
    "mohu_flypy_fixed.schema.yaml",
}

local function read(path)
    local file = assert(io.open(path, "r"))
    local text = file:read("*a")
    file:close()
    return text
end

for _, path in ipairs(schemas) do
    local text = read(path)
    assert(text:find("lua_processor@*mohu_candidate_manager*manager_processor", 1, true), path)
    assert(text:find("lua_translator@*mohu_candidate_manager*manager_translator", 1, true), path)
    assert(text:find([[candidate_manager: "^==[hopwlu]?[a-z]*$"]], 1, true), path)
    local alphabet = assert(text:match("alphabet:%s*([^\n]+)"), path)
    assert(alphabet:find("=", 1, true), path .. " alphabet")
end

local shared = read("mohu.yaml")
assert(shared:find("candidate_manager:", 1, true))
assert(shared:find([[prefix: "=="]], 1, true))

local manager = read("lua/mohu_candidate_manager.lua")
assert(not manager:find("空格查看", 1, true))

local makefile = read("Makefile")
assert(makefile:find("lua tests/mohu_candidate_manager_test.lua", 1, true))
assert(makefile:find("lua tests/mohu_pin_store_test.lua", 1, true))

print("candidate manager config: ok")
