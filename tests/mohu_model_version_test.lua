package.path = "./tiger_sentence_native/?.lua;" .. package.path

local runtime = dofile("tiger_sentence_native/mohu_runtime.lua")

local root = os.tmpname()
os.remove(root)
if package.config:sub(1, 1) == "\\" then
  assert(os.execute('mkdir "' .. root .. '"') == true)
else
  assert(os.execute("mkdir -p " .. root) == true)
end

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

-- Runtime selection is intentionally fixed. Model discovery belongs to the
-- package/deployment step; the Lua hot path must never spawn a shell.
local original_popen = io.popen
io.popen = function()
  error("runtime model resolution must not call io.popen")
end

local selected = runtime.resolve_model({ model_dir = root })
assert(selected == root .. "/mohu-sentence-ngram-v5.bin",
  "fixed v5 model must be selected: " .. tostring(selected))

selected = runtime.resolve_model({ model_dir = root })
assert(selected == root .. "/mohu-sentence-ngram-v5.bin",
  "repeated resolution must remain fixed: " .. tostring(selected))

os.remove(root .. "/mohu-sentence-ngram-v5.bin")
assert(runtime.resolve_model({ model_dir = root }) == root .. "/mohu-sentence-ngram-v5.bin",
  "missing model still returns the fixed path for one-time engine error reporting")

io.popen = original_popen

if package.config:sub(1, 1) == "\\" then
  os.execute('rmdir /s /q "' .. root .. '"')
else
  os.execute("rm -rf " .. root)
end
print("Mohu fixed model path tests passed")
