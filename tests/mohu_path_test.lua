local original_config = package.config

package.path = "./tiger_sentence_native/?.lua;" .. package.path

local root = os.tmpname()
os.remove(root)
assert(os.execute("mkdir -p " .. root) == true)
rime_api = { get_user_data_dir = function() return root end }

local runtime = dofile("tiger_sentence_native/mohu_runtime.lua")
local paths = runtime.paths()
assert(paths.root == root .. "/mohu", "runtime root must be named mohu")
assert(paths.runtime == root .. "/mohu/runtime")
assert(paths.data == root .. "/mohu/data")
assert(paths.model == root .. "/mohu/model")
assert(paths.ngram == root .. "/mohu/model/mohu-sentence-ngram-v5.bin")
assert(paths.engine == root .. "/mohu/runtime/libtigerengine.dylib")
assert(paths.lexicons.zrm == root .. "/mohu/data/zrm/mohu_zrm.lexicon.txt")
assert(paths.lexicons.flypy == root .. "/mohu/data/flypy/mohu_flypy.lexicon.txt")
assert(runtime.lexicon("flypy") == paths.lexicons.flypy)
assert(runtime.lexicon("zrm") == paths.lexicons.zrm)

local custom = runtime.paths({ user_data_dir = "/tmp/example-rime/" })
assert(custom.root == "/tmp/example-rime/mohu")
assert(custom.model == "/tmp/example-rime/mohu/model")

-- Cross-platform packages contain both engines.  A POSIX host must keep using
-- the dylib even when the Windows DLL is present.
os.execute("mkdir -p " .. root .. "/mohu/runtime")
local dll = io.open(root .. "/mohu/runtime/libtigerengine.dll", "w")
dll:write("stub")
dll:close()
assert(runtime.paths().engine == root .. "/mohu/runtime/libtigerengine.dylib")

-- Weasel exposes the Windows path separator through package.config.
package.config = "\\" .. original_config:sub(2)
assert(runtime.paths().engine == root .. "/mohu/runtime/libtigerengine.dll")
package.config = original_config

for _, name in ipairs({
  "mohu_runtime.lua",
  "mohu_tiger_sentence.lua",
}) do
  local file = assert(io.open("tiger_sentence_native/" .. name, "r"))
  local source = file:read("*a")
  file:close()
  assert(not source:find("/tiger/", 1, true), name .. " retains a legacy tiger path")
end

print("Mohu runtime path tests passed")
