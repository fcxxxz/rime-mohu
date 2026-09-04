-- Sync common Rime options across engine contexts.

local option_state = require("option_state")

local M = {}
local kNoop = 2
local syncing = false
local SYNC_INTERVAL_MS = 250

local function now_ms()
  if rime_api and rime_api.get_time_ms then
    return rime_api.get_time_ms()
  end
  return math.floor(os.clock() * 1000)
end

-- 与 default.yaml 的 switcher/save_options 保持一致。
local COMMON_OPTIONS = {
  "ascii_punct",
  "full_shape",
  "extended_charset",
  "emoji",
  "inflexible",
  "unicode_comment",
  "contextual_order",
  "quick_code_hint",
  "aux_hint",
  "multi_short_code",
}

local SCHEMA_OPTIONS = {
  mohu_zrm = {
    "ascii_punct",
    "full_shape",
    "extended_charset",
    "emoji",
    "inflexible",
    "unicode_comment",
    "contextual_order",
    "quick_code_hint",
    "aux_hint",
    "multi_short_code",
  },
  mohu_flypy = {
    "ascii_punct",
    "full_shape",
    "extended_charset",
    "emoji",
    "inflexible",
    "unicode_comment",
    "contextual_order",
    "quick_code_hint",
    "aux_hint",
    "multi_short_code",
  },
}

-- 状态文件里还没有记录时使用的默认值。
local OPTION_DEFAULTS = {
  contextual_order = true,
}

local function get_context(env)
  return env and env.engine and env.engine.context or nil
end

local function option_names(env)
  local schema = env and env.engine and env.engine.schema
  local schema_id = schema and schema.schema_id
  return SCHEMA_OPTIONS[schema_id] or COMMON_OPTIONS
end

local function save_options(env)
  local ctx = get_context(env)
  if ctx and ctx.get_option then
    local values = {}
    for _, name in ipairs(option_names(env)) do
      values[name] = ctx:get_option(name) and true or false
    end
    option_state.set_many(values)
  end
end

local function sync_options(env, force)
  if syncing then
    return
  end
  syncing = true
  option_state.sync_many(env, option_names(env), force, OPTION_DEFAULTS)
  syncing = false
end

function M.init(env)
  local ctx = get_context(env)
  if ctx and ctx.option_update_notifier and ctx.option_update_notifier.connect then
    env.option_sync_notifier = ctx.option_update_notifier:connect(function()
      if not syncing then
        save_options(env)
      end
    end)
  end
  env.option_sync_last_ms = now_ms()
  sync_options(env, true)
end

function M.fini(env)
  if env and env.option_sync_notifier and env.option_sync_notifier.disconnect then
    env.option_sync_notifier:disconnect()
  end
end

function M.func(key_event, env)
  if key_event and key_event.release and key_event:release() then
    return kNoop
  end
  local current_ms = now_ms()
  local last_ms = env and env.option_sync_last_ms
  if not last_ms or current_ms < last_ms or current_ms - last_ms >= SYNC_INTERVAL_MS then
    sync_options(env, false)
    if env then
      env.option_sync_last_ms = current_ms
    end
  end
  return kNoop
end

function M._test_options()
  return COMMON_OPTIONS
end

function M._test_option_names(schema_id)
  return SCHEMA_OPTIONS[schema_id] or COMMON_OPTIONS
end

return M
