local function read(path)
  local file = assert(io.open(path, "r"), "missing " .. path)
  local text = file:read("*a")
  file:close()
  return text
end

local function assert_contains(text, needle, message)
  assert(text:find(needle, 1, true), message or ("missing: " .. needle))
end

local function assert_not_contains(text, needle, message)
  assert(not text:find(needle, 1, true), message or ("unexpected: " .. needle))
end

local schemas = {
  {
    path = "mohu_llm_zrm.schema.yaml",
    id = "mohu_llm_zrm",
    name = "魔虎大模型·自然码",
    dependency = "mohu_zrm_fixed",
    lexicon = "mohu_llm/data/zrm/mohu_llm_zrm.lexicon.txt",
    scheme = "zrm",
  },
  {
    path = "mohu_llm_flypy.schema.yaml",
    id = "mohu_llm_flypy",
    name = "魔虎大模型·小鹤",
    dependency = "mohu_flypy_fixed",
    lexicon = "mohu_llm/data/flypy/mohu_llm_flypy.lexicon.txt",
    scheme = "flypy",
  },
}

for _, item in ipairs(schemas) do
  local text = read(item.path)
  assert_contains(text, "schema_id: " .. item.id)
  assert_contains(text, "name: " .. item.name)
  assert_contains(text, "- " .. item.dependency)
  assert_contains(text, "scheme: " .. item.scheme)
  assert_contains(text, "lexicon: " .. item.lexicon)
  assert_contains(text, "lua_translator@*mohu_sentence*translator")
  assert_not_contains(text, "lua_translator@*mohu_tiger_sentence*translator")
  assert_contains(text, "lua_filter@*mohu_reorder_filter")
  assert_contains(text, "smart_static:")
  assert_contains(text, "personal_lexicon_namespace: smart")
  assert_contains(text, "personal_lexicon_max_rows: 4096")
  local static_start = text:find("smart_static:", 1, true)
  local fixed_start = text:find("\nfixed:", static_start, true)
  local static = text:sub(static_start, fixed_start or #text)
  assert_contains(static, 'user_dict: ""')
  assert_contains(static, "enable_user_dict: false")
  assert_not_contains(text, "mohu_tiger_sentence.schema")
  assert_not_contains(text, "octagram")
  assert_not_contains(text, "early_commit")
end

assert(not io.open("tiger_sentence_native/mohu_tiger_sentence.schema.yaml", "r"),
  "legacy native schema must be removed")

print("mohu llm schema split: ok")
