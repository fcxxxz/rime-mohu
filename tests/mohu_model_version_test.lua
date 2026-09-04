package.path = "./tiger_sentence_native/?.lua;" .. package.path

local runtime = dofile("tiger_sentence_native/mohu_runtime.lua")

local root = os.tmpname()
os.remove(root)
assert(os.execute("mkdir -p " .. root) == true)

local function touch(name)
  local file = assert(io.open(root .. "/" .. name, "w"))
  file:write("model")
  file:close()
end

touch("mohu-sentence-ngram-v5.bin")
touch("mohu-sentence-ngram-v5.2.bin")
touch("mohu-sentence-ngram-v5.10.bin")
touch("mohu-sentence-ngram-v6.bin")
touch("mohu-sentence-ngram-v6.0.1.bin")
touch("mohu-sentence-ngram-v7-preview.bin")
touch("mohu-sentence-ngram-vx.bin")
touch("other.bin")

local selected = runtime.resolve_model({ model_dir = root })
assert(selected == root .. "/mohu-sentence-ngram-v6.0.1.bin",
  "highest numeric version must win: " .. tostring(selected))

os.remove(root .. "/mohu-sentence-ngram-v6.0.1.bin")
selected = runtime.resolve_model({ model_dir = root })
assert(selected == root .. "/mohu-sentence-ngram-v6.bin",
  "integer v6 must beat decimal v5.10: " .. tostring(selected))

os.remove(root .. "/mohu-sentence-ngram-v6.bin")
selected = runtime.resolve_model({ model_dir = root })
assert(selected == root .. "/mohu-sentence-ngram-v5.10.bin",
  "v5.10 must beat v5.2 numerically: " .. tostring(selected))

os.remove(root .. "/mohu-sentence-ngram-v5.bin")
os.remove(root .. "/mohu-sentence-ngram-v5.2.bin")
os.remove(root .. "/mohu-sentence-ngram-v5.10.bin")
assert(runtime.resolve_model({ model_dir = root }) == nil,
  "no valid model must return nil")

os.execute("rm -rf " .. root)
print("Mohu model version selection tests passed")
