-- Shared option state for Lua features across Rime engine contexts.

local M = {}

local loaded = false
local values = {}
local last_load_ms = 0
local LOAD_INTERVAL_MS = 250

local function now_ms()
  if rime_api and rime_api.get_time_ms then
    return rime_api.get_time_ms()
  end
  return math.floor(os.clock() * 1000)
end

local function pathsep()
  return (package.config or "/"):sub(1, 1)
end

local function state_file()
  if rime_api and rime_api.get_user_data_dir then
    return rime_api.get_user_data_dir() .. pathsep() .. "lua" .. pathsep() .. "option_state_data.lua"
  end
  return nil
end

local function serialize()
  local lines = { "return {" }
  for name, value in pairs(values) do
    if type(name) == "string" and type(value) == "boolean" then
      lines[#lines + 1] = string.format("  [%q] = %s,", name, tostring(value))
    end
  end
  lines[#lines + 1] = "}"
  return table.concat(lines, "\n")
end

local function load(force)
  local current_ms = now_ms()
  if loaded and not force and current_ms >= last_load_ms and current_ms - last_load_ms < LOAD_INTERVAL_MS then
    return
  end
  loaded = true
  last_load_ms = current_ms
  local next_values = {}
  local path = state_file()
  if not path then
    values = next_values
    return
  end
  local ok, data = pcall(dofile, path)
  if ok and type(data) == "table" then
    for name, value in pairs(data) do
      if type(name) == "string" and type(value) == "boolean" then
        next_values[name] = value
      end
    end
  end
  values = next_values
end

local function sync_context(env)
  local context = env and env.engine and env.engine.context
  if
    context and
    context.get_option and
    context.set_option and
    context.option_update_notifier
  then
    return context
  end
  return nil
end

local function save()
  local path = state_file()
  if not path then
    return
  end
  local file = io.open(path, "w")
  if not file then
    return
  end
  file:write(serialize())
  file:write("\n")
  file:close()
end

function M.get(name, fallback, force)
  load(force)
  if values[name] == nil then
    return fallback and true or false
  end
  return values[name]
end

function M.set(name, value)
  load(true)
  values[name] = value and true or false
  save()
end

function M.set_many(next_values)
  load(true)
  for name, value in pairs(next_values or {}) do
    if type(name) == "string" and type(value) == "boolean" then
      values[name] = value
    end
  end
  save()
end

function M.can_sync(env)
  return sync_context(env) ~= nil
end

function M.sync(env, name, fallback, force)
  local context = sync_context(env)
  if not context then
    return fallback and true or false
  end

  load(force)
  local desired = values[name]
  if desired == nil then
    desired = fallback and true or false
    values[name] = desired
    save()
  end

  local current = context:get_option(name) and true or false
  if current ~= desired then
    context:set_option(name, desired)
  end
  return desired
end

function M.sync_many(env, names, force, defaults)
  local context = sync_context(env)
  if not context then
    return false
  end

  load(force)
  local should_save = false
  for _, name in ipairs(names or {}) do
    local current = context:get_option(name) and true or false
    local desired = values[name]
    if desired == nil then
      desired = defaults and defaults[name]
      if desired == nil then
        desired = current
      end
      values[name] = desired
      should_save = true
    end
    if current ~= desired then
      context:set_option(name, desired)
    end
  end
  if should_save then
    save()
  end
  return true
end

function M.save_many_from_context(env, names, force)
  local context = sync_context(env)
  if not context then
    return false
  end

  load(force)
  for _, name in ipairs(names or {}) do
    values[name] = context:get_option(name) and true or false
  end
  save()
  return true
end

function M._test_reset()
  loaded = true
  values = {}
  last_load_ms = 0
end

return M
