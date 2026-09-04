-- Shared paths for the MoHu LLM runtime.
-- Keep all user-data resolution in one place so schemas, Lua components and
-- the scorer supervisor agree on the same installation layout.
local M = {}

local function user_data_dir()
  local api = rawget(_G, "rime_api")
  if api and type(api.get_user_data_dir) == "function" then
    local ok, value = pcall(api.get_user_data_dir)
    if ok and type(value) == "string" and value ~= "" then
      return (value:gsub("/+$", ""))
    end
  end
  return "."
end

local function join(root, suffix)
  return root .. "/" .. suffix
end

local FIXED_MODEL_NAME = "mohu-sentence-ngram-v5.bin"

function M.resolve_model(options)
  local opts = options or {}
  local model_dir = opts.model_dir or M.paths(opts).model
  -- Keep model selection out of the input hot path. In particular, do not
  -- use io.popen here: on Windows it launches a visible cmd.exe and blocks
  -- the Rime engine thread while the shell enumerates the directory.
  return join(model_dir, FIXED_MODEL_NAME)
end

-- Cross-platform packages contain both engines.  Select by host platform;
-- probing by file existence would make macOS try the bundled Windows DLL.
local function engine_library(runtime)
  if package.config:sub(1, 1) == "\\" then
    return join(runtime, "libtigerengine.dll")
  end
  return join(runtime, "libtigerengine.dylib")
end

function M.user_data_dir()
  return user_data_dir()
end

function M.paths(options)
  local opts = options or {}
  local base = tostring(opts.user_data_dir or user_data_dir()):gsub("/+$", "")
  local root = join(base, "mohu")
  local runtime = join(root, "runtime")
  local data = join(root, "data")
  local model = join(root, "model")
  return {
    root = root,
    runtime = runtime,
    data = data,
    model = model,
    engine = engine_library(runtime),
    ngram = join(model, FIXED_MODEL_NAME),
    lexicon = join(data, "zrm/mohu_zrm.lexicon.txt"),
    lexicons = {
      zrm = join(data, "zrm/mohu_zrm.lexicon.txt"),
      flypy = join(data, "flypy/mohu_flypy.lexicon.txt"),
    },
  }
end

function M.lexicon(scheme, options)
  local paths = M.paths(options)
  local key = scheme == "flypy" and "flypy" or "zrm"
  return paths.lexicons[key]
end

M.resolve = M.paths

return M
