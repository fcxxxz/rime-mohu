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
    local config = read_bounded(root .. "/" .. item.relative_path .. "/config.json", MAX_CONFIG_BYTES)
    result[#result + 1] = model_with_path(item, root, type(config) == "string" and #config > 0)
  end
  return result
end

function M.get(id, options)
  local item = model_for(id)
  if not item then return nil end
  local opts = options or {}
  local root = opts.user_data_dir or default_user_data_dir()
  root = tostring(root):gsub("/+$", "")
  local config = read_bounded(root .. "/" .. item.relative_path .. "/config.json", MAX_CONFIG_BYTES)
  return model_with_path(item, root, type(config) == "string" and #config > 0)
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
  local available = type(config) == "string" and #config > 0
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
