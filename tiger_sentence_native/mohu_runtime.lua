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

local VERSIONED_MODEL = "^mohu%-sentence%-ngram%-v([0-9.]+)%.bin$"

local function valid_version(name)
  local version = name:match(VERSIONED_MODEL)
  if not version or version:match("^%.") or version:match("%.$") or
      version:find("..", 1, true) then
    return nil
  end
  local count = 0
  for component in version:gmatch("[^.]+") do
    if not component:match("^%d+$") then return nil end
    count = count + 1
  end
  return count > 0 and version or nil
end

local function version_parts(version)
  local parts = {}
  for component in version:gmatch("%d+") do
    parts[#parts + 1] = tonumber(component)
  end
  return parts
end

local function version_greater(left, right)
  local a, b = version_parts(left), version_parts(right)
  local length = math.max(#a, #b)
  for index = 1, length do
    local av, bv = a[index] or 0, b[index] or 0
    if av ~= bv then return av > bv end
  end
  return left > right
end

local function shell_quote(value)
  if package.config:sub(1, 1) == "\\" then
    return '"' .. tostring(value):gsub('"', '""') .. '"'
  end
  return "'" .. tostring(value):gsub("'", "'\\''") .. "'"
end

local function model_entries(model_dir)
  local command
  if package.config:sub(1, 1) == "\\" then
    command = "dir /b /a-d " .. shell_quote(model_dir) .. " 2>nul"
  else
    command = "find " .. shell_quote(model_dir) .. " -maxdepth 1 -type f -print 2>/dev/null"
  end
  local pipe = io.popen(command, "r")
  if not pipe then return function() end end
  local iterator = pipe:lines()
  return function()
    local value = iterator()
    if value == nil then pipe:close() end
    return value
  end
end

function M.resolve_model(options)
  local opts = options or {}
  local model_dir = opts.model_dir or M.paths(opts).model
  local best_name, best_version
  for entry in model_entries(model_dir) do
    local name = entry:gsub("^.*[/\\]", "")
    local version = valid_version(name)
    if version and (not best_version or version_greater(version, best_version)) then
      best_name, best_version = name, version
    end
  end
  return best_name and join(model_dir, best_name) or nil
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
    ngram = join(model, "mohu-sentence-ngram-v5.bin"),
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
