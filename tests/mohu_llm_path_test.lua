package.path = "./tiger_sentence_native/?.lua;" .. package.path

local root = os.tmpname()
os.remove(root)
assert(os.execute("mkdir -p " .. root) == true)
rime_api = { get_user_data_dir = function() return root end }

local runtime = dofile("tiger_sentence_native/mohu_llm_runtime.lua")
local paths = runtime.paths()
assert(paths.root == root .. "/mohu_llm", "runtime root must be named mohu_llm")
assert(paths.runtime == root .. "/mohu_llm/runtime")
assert(paths.data == root .. "/mohu_llm/data")
assert(paths.models == root .. "/mohu_llm/models")
assert(paths.config == root .. "/mohu_llm/config")
assert(paths.socket == root .. "/mohu_llm/runtime/qwen35-reranker.sock")
assert(paths.selection == root .. "/mohu_llm/config/model-selection")
assert(paths.ngram == root .. "/mohu_llm/data/sentence-ngram-mobile.bin")
assert(paths.engine == root .. "/mohu_llm/runtime/libtigerengine.dylib")
assert(paths.lexicons.zrm == root .. "/mohu_llm/data/zrm/mohu_llm_zrm.lexicon.txt")
assert(paths.lexicons.flypy == root .. "/mohu_llm/data/flypy/mohu_llm_flypy.lexicon.txt")
assert(runtime.lexicon("flypy") == paths.lexicons.flypy)
assert(runtime.lexicon("zrm") == paths.lexicons.zrm)

local custom = runtime.paths({ user_data_dir = "/tmp/example-rime/" })
assert(custom.root == "/tmp/example-rime/mohu_llm")
assert(custom.models == "/tmp/example-rime/mohu_llm/models")
assert(custom.selection == "/tmp/example-rime/mohu_llm/config/model-selection")

for _, name in ipairs({
  "mohu_llm_runtime.lua",
  "mohu_tiger_sentence.lua",
  "mohu_tiger_reranker.lua",
  "mohu_tiger_model_catalog.lua",
  "mohu_tiger_model_menu.lua",
  "run_qwen35_scorer.command",
  "scorer_models.zsh",
}) do
  local file = assert(io.open("tiger_sentence_native/" .. name, "r"))
  local source = file:read("*a")
  file:close()
  assert(not source:find("/tiger/", 1, true), name .. " retains a legacy tiger path")
end

print("Mohu LLM runtime path tests passed")
