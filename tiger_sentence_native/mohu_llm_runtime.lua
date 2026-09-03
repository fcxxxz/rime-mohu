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
  local root = join(base, "mohu_llm")
  local runtime = join(root, "runtime")
  local data = join(root, "data")
  local models = join(root, "models")
  local config = join(root, "config")
  return {
    root = root,
    runtime = runtime,
    data = data,
    models = models,
    config = config,
    engine = engine_library(runtime),
    ngram = join(data, "sentence-ngram-mobile.bin"),
    lexicon = join(data, "zrm/mohu_llm_zrm.lexicon.txt"),
    lexicons = {
      zrm = join(data, "zrm/mohu_llm_zrm.lexicon.txt"),
      flypy = join(data, "flypy/mohu_llm_flypy.lexicon.txt"),
    },
    socket = join(runtime, "qwen35-reranker.sock"),
    selection = join(config, "model-selection"),
  }
end

function M.lexicon(scheme, options)
  local paths = M.paths(options)
  local key = scheme == "flypy" and "flypy" or "zrm"
  return paths.lexicons[key]
end

M.resolve = M.paths

return M
