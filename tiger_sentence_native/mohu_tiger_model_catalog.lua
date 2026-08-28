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
  if type(text) ~= "string" or #text < 2 then return false end

  local null_value = {}
  local position = 1

  local function skip_space()
    while position <= #text and text:sub(position, position):match("%s") do
      position = position + 1
    end
  end

  local function parse_string()
    if text:sub(position, position) ~= '"' then return nil end
    position = position + 1
    local parts = {}
    while position <= #text do
      local char = text:sub(position, position)
      position = position + 1
      if char == '"' then return table.concat(parts) end
      if char == "\\" then
        if position > #text then return nil end
        local escaped = text:sub(position, position)
        position = position + 1
        local simple = { ['"'] = '"', ['\\'] = "\\", ['/'] = "/",
          b = "\b", f = "\f", n = "\n", r = "\r", t = "\t" }
        if simple[escaped] then
          parts[#parts + 1] = simple[escaped]
        elseif escaped == "u" then
          local hex = text:sub(position, position + 3)
          if not hex:match("^%x%x%x%x$") then return nil end
          position = position + 4
          parts[#parts + 1] = "?"
        else
          return nil
        end
      elseif char:byte() < 0x20 then
        return nil
      else
        parts[#parts + 1] = char
      end
    end
    return nil
  end

  local parse_value
  local function parse_array()
    if text:sub(position, position) ~= "[" then return nil end
    position = position + 1
    local result = {}
    skip_space()
    if text:sub(position, position) == "]" then
      position = position + 1
      return result
    end
    while position <= #text do
      local value = parse_value()
      if value == nil then return nil end
      result[#result + 1] = value
      skip_space()
      local delimiter = text:sub(position, position)
      position = position + 1
      if delimiter == "]" then return result end
      if delimiter ~= "," then return nil end
      skip_space()
    end
    return nil
  end

  local function parse_object()
    if text:sub(position, position) ~= "{" then return nil end
    position = position + 1
    local result = {}
    skip_space()
    if text:sub(position, position) == "}" then
      position = position + 1
      return result
    end
    while position <= #text do
      local key = parse_string()
      if key == nil then return nil end
      skip_space()
      if text:sub(position, position) ~= ":" then return nil end
      position = position + 1
      local value = parse_value()
      if value == nil then return nil end
      result[key] = value
      skip_space()
      local delimiter = text:sub(position, position)
      position = position + 1
      if delimiter == "}" then return result end
      if delimiter ~= "," then return nil end
      skip_space()
    end
    return nil
  end

  local function parse_number()
    local start = position
    local value = text:sub(position):match("^-?%d+%.?%d*[eE]?[+-]?%d*")
    if not value or value == "" or not tonumber(value) then return nil end
    if value:match("^-?0%d") or value:match("%.$") or
        value:match("[eE][+-]?$") then
      return nil
    end
    position = start + #value
    local next_char = text:sub(position, position)
    if next_char ~= "" and not next_char:match("[%s,%]}]") then return nil end
    return tonumber(value)
  end

  function parse_value()
    skip_space()
    local prefix = text:sub(position, position)
    if prefix == '"' then return parse_string() end
    if prefix == "{" then return parse_object() end
    if prefix == "[" then return parse_array() end
    if text:sub(position, position + 3) == "true" then
      position = position + 4
      return true
    end
    if text:sub(position, position + 4) == "false" then
      position = position + 5
      return false
    end
    if text:sub(position, position + 3) == "null" then
      position = position + 4
      return null_value
    end
    return parse_number()
  end

  local value = parse_value()
  skip_space()
  if type(value) ~= "table" or position <= #text then return false end
  local model_type = value.model_type
  local quantization = value.quantization or value.quantization_config
  local bits = type(quantization) == "table" and quantization.bits or nil
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
