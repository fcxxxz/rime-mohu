-- Bounded registry for the locally supported Qwen checkpoints.
-- Availability checks only read a small config.json prefix; model weights are
-- intentionally never hashed from the keypress thread.
local M = {}

local DEFAULT_ID = "qwen35-0.8b"
local MAX_SELECTION_BYTES = 128
local MAX_CONFIG_BYTES = 4096

local registry = {
  {
    id = "qwen35-0.8b",
    selection_id = "qwen35-0.8b",
    display_label = "Qwen3.5-0.8B-MLX-4bit",
    label = "Qwen3.5-0.8B-MLX-4bit",
    relative_path = "tiger/models/Qwen3.5-0.8B-MLX-4bit",
    path = "tiger/models/Qwen3.5-0.8B-MLX-4bit",
    model_sha256 = "8b1fc914a940d611e13ba1880ffdae553deb4504a0a6299256ac19470fc591b8",
    expected_sha256 = "8b1fc914a940d611e13ba1880ffdae553deb4504a0a6299256ac19470fc591b8",
    model_type = "qwen3_5",
  },
  {
    id = "qwen3-0.6b",
    selection_id = "qwen3-0.6b",
    display_label = "Qwen3-0.6B-4bit",
    label = "Qwen3-0.6B-4bit",
    relative_path = "tiger/models/Qwen3-0.6B-4bit",
    path = "tiger/models/Qwen3-0.6B-4bit",
    model_sha256 = "2de6c7d42ac12c447715e06bfab6497bdd49707bec990ae3cddce3a8c4ba0548",
    expected_sha256 = "2de6c7d42ac12c447715e06bfab6497bdd49707bec990ae3cddce3a8c4ba0548",
    model_type = "qwen3",
  },
}

local function copy(item)
  local result = {}
  for key, value in pairs(item or {}) do result[key] = value end
  return result
end

local function default_user_data_dir()
  local api = rawget(_G, "rime_api")
  if api and type(api.get_user_data_dir) == "function" then
    local ok, value = pcall(api.get_user_data_dir)
    if ok and type(value) == "string" and value ~= "" then
      return (value:gsub("/+$", ""))
    end
  end
  return "."
end

local function trim(value)
  return (value:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function read_bounded(path, limit)
  local file, err = io.open(path, "r")
  if not file then return nil, err end
  local value = file:read(limit)
  file:close()
  if type(value) ~= "string" then return nil, "read failed" end
  return value
end

local function parse_config(text, expected_type)
  if type(text) ~= "string" or #text < 2 or text:match("^%s*{%s*}%s*$") then
    return false
  end
  local normalized = trim(text)
  if normalized:sub(1, 1) ~= "{" or normalized:sub(-1) ~= "}" then
    return false
  end
  local depth, quoted, escaped, root_closed = 0, false, false, false
  for index = 1, #text do
    local char = text:sub(index, index)
    if quoted then
      if escaped then escaped = false
      elseif char == "\\" then escaped = true
      elseif char == '"' then quoted = false end
    elseif root_closed then
      if not char:match("%s") then return false end
    elseif char == '"' then quoted = true
    elseif char == "{" then depth = depth + 1
    elseif char == "}" then
      depth = depth - 1
      if depth < 0 then return false end
      if depth == 0 then root_closed = true end
    end
  end
  if quoted or escaped or depth ~= 0 or not root_closed then return false end
  local model_type = text:match('"model_type"%s*:%s*"([^"]+)"')
  local bits = tonumber(text:match('"bits"%s*:%s*(%d+)'))
  return model_type == expected_type and bits == 4
end

local function has_readable_asset(root, relative_path)
  local names = { "tokenizer.json", "tokenizer_config.json", "model.safetensors", "model.safetensors.index.json" }
  for _, name in ipairs(names) do
    local value = read_bounded(root .. "/" .. relative_path .. "/" .. name, 1)
    if type(value) == "string" and #value > 0 then return true end
  end
  return false
end

local function is_available(item, root)
  local config = read_bounded(root .. "/" .. item.relative_path .. "/config.json", MAX_CONFIG_BYTES)
  return parse_config(config, item.model_type) and has_readable_asset(root, item.relative_path)
end

local function model_for(id)
  for _, item in ipairs(registry) do
    if item.id == id then return item end
  end
  return nil
end

local function model_with_path(item, root, available)
  local result = copy(item)
  result.model_path = root .. "/" .. item.relative_path
  result.available = available == true
  return result
end

function M.list(options)
  local opts = options or {}
  local root = tostring(opts.user_data_dir or default_user_data_dir()):gsub("/+$", "")
  local result = {}
  for _, item in ipairs(registry) do
    result[#result + 1] = model_with_path(item, root, is_available(item, root))
  end
  return result
end

function M.get(id, options)
  local item = model_for(id)
  if not item then return nil end
  local opts = options or {}
  local root = opts.user_data_dir or default_user_data_dir()
  root = tostring(root):gsub("/+$", "")
  return model_with_path(item, root, is_available(item, root))
end

function M.read_selection(options)
  local opts = options or {}
  local root = tostring(opts.user_data_dir or default_user_data_dir()):gsub("/+$", "")
  local path = opts.selection_path or (root .. "/tiger/model-selection")
  local value, err = read_bounded(path, MAX_SELECTION_BYTES)
  if not value then
    if err and tostring(err):match("No such file") then
      return DEFAULT_ID, "default"
    end
    return nil, "no-model", err
  end
  value = trim(value)
  if value == "" then return nil, "no-model" end
  return value, "explicit"
end

function M.status(options)
  local opts = options or {}
  local root = tostring(opts.user_data_dir or default_user_data_dir()):gsub("/+$", "")
  local id, source, read_error = M.read_selection(opts)
  if not id then
    return { status = "no-model", selection_id = nil, source = source, error = read_error }
  end
  local item = model_for(id)
  if not item then
    return { status = "unknown-selection", selection_id = id, source = source }
  end
  local config_path = root .. "/" .. item.relative_path .. "/config.json"
  local config, config_error = read_bounded(config_path, MAX_CONFIG_BYTES)
  local available = parse_config(config, item.model_type) and has_readable_asset(root, item.relative_path)
  local model = model_with_path(item, root, available)
  return {
    status = available and "available" or "unavailable",
    selection_id = id,
    source = source,
    model = model,
    config_path = config_path,
    error = available and nil or config_error,
  }
end

function M.active(options)
  return M.status(options)
end

M.default_id = DEFAULT_ID

return M
