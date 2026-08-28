package.path = "./tiger_sentence_native/?.lua;" .. package.path

local catalog = dofile("tiger_sentence_native/mohu_tiger_model_catalog.lua")

local root = os.tmpname()
os.remove(root)
assert(os.execute("mkdir -p " .. root .. "/tiger/models/Qwen3.5-0.8B-MLX-4bit") == true)
assert(os.execute("mkdir -p " .. root .. "/tiger/models/Qwen3-0.6B-4bit") == true)

local function write(path, value)
  local file = assert(io.open(path, "w"))
  file:write(value)
  file:close()
end

local models = catalog.list()
assert(#models == 2, "catalog must contain exactly two supported models")
assert(models[1].id == "qwen35-0.8b")
assert(models[1].display_label == "Qwen3.5-0.8B-MLX-4bit")
assert(models[1].relative_path == "tiger/models/Qwen3.5-0.8B-MLX-4bit")
assert(models[2].id == "qwen3-0.6b")
assert(models[2].display_label == "Qwen3-0.6B-4bit")

local missing = catalog.status({ user_data_dir = root })
assert(missing.selection_id == "qwen35-0.8b", "default selection must be qwen35-0.8b")
assert(missing.status == "unavailable", "missing config must report unavailable")
assert(missing.model and missing.model.id == "qwen35-0.8b")

write(root .. "/tiger/model-selection", "qwen3-0.6b\n")
local unavailable = catalog.status({ user_data_dir = root })
assert(unavailable.selection_id == "qwen3-0.6b")
assert(unavailable.status == "unavailable")

write(root .. "/tiger/models/Qwen3-0.6B-4bit/config.json", "{}\n")
local empty_config = catalog.status({ user_data_dir = root })
assert(empty_config.status == "unavailable", "empty config is not a valid model")
write(root .. "/tiger/models/Qwen3-0.6B-4bit/config.json", "{\n")
local malformed_config = catalog.status({ user_data_dir = root })
assert(malformed_config.status == "unavailable", "malformed config is unavailable")
write(root .. "/tiger/models/Qwen3-0.6B-4bit/config.json",
  '{"model_type":"qwen3","quantization":{"bits":4}}\n')
local missing_weights = catalog.status({ user_data_dir = root })
assert(missing_weights.status == "unavailable", "model assets are required")
write(root .. "/tiger/models/Qwen3-0.6B-4bit/tokenizer.json", "{}\n")
local available = catalog.status({ user_data_dir = root })
assert(available.status == "available")
assert(available.model.relative_path == "tiger/models/Qwen3-0.6B-4bit")
assert(available.model.model_path:sub(-#available.model.relative_path) == available.model.relative_path)

write(root .. "/tiger/models/Qwen3-0.6B-4bit/config.json",
  '{"model_type":"unsupported","quantization":{"bits":4}}\n')
local unsupported = catalog.status({ user_data_dir = root })
assert(unsupported.status == "unavailable", "unsupported model type is unavailable")

write(root .. "/tiger/model-selection", "made-up-model\n")
local unknown = catalog.status({ user_data_dir = root })
assert(unknown.status == "unknown-selection")
assert(unknown.model == nil, "unknown selection must not auto-fallback")

os.remove(root .. "/tiger/model-selection")
os.execute("rm -rf " .. root)
print("Mohu tiger model catalog tests passed")
