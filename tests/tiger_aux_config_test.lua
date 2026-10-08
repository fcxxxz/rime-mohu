local function read(path)
    local f = assert(io.open(path, "r")); local s = f:read("*a"); f:close(); return s
end
for _, scheme in ipairs({"zrm", "flypy"}) do
    local s = read("mohu_" .. scheme .. ".schema.yaml")
    assert(s:find("dictionary: mohu_" .. scheme .. "\n", 1, true))
    assert(not s:find("multi_short_code", 1, true))
    assert(not s:find("fixed_legacy", 1, true))
    local shim = read("mohu_" .. scheme .. "_sentence_core.schema.yaml")
    assert(shim:find("script_translator", 1, true))
    assert(s:find("dictionary: tiger", 1, true))
end
print("Unified dictionary schema configuration tests passed")
