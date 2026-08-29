-- Dynamic model picker for the native tiger sentence schema.
-- Candidates carry a stable model id; the processor consumes selection keys so
-- the visible model label is never committed as user text.
local catalog = require("mohu_tiger_model_catalog")
local runtime = require("mohu_llm_runtime")
local ok_reranker, reranker = pcall(require, "mohu_tiger_reranker")
if not ok_reranker or type(reranker) ~= "table" then reranker = {} end

local M = {}
local ACCEPTED = 1
local NOOP = 2
local PREFIX = "/model"

local function current_segment(context)
  if not context or not context.composition or context.composition:empty() then
    return nil
  end
  return context.composition:back()
end

local function is_commit_key(keycode)
  return keycode == 0x20 or keycode == 0xff0d or keycode == 0xff8d or keycode == 0x0d or
    (keycode >= 0x31 and keycode <= 0x39)
end

local function selected_for_key(context, keycode)
  local segment = current_segment(context)
  if not segment then return nil end
  if keycode >= 0x31 and keycode <= 0x39 and segment.menu then
    segment.menu:prepare(keycode - 0x30)
    return segment.menu:get_candidate_at(keycode - 0x31)
  end
  if segment.get_selected_candidate then
    return segment:get_selected_candidate()
  end
  return nil
end

local function write_selection(user_data_dir, model_id)
  local paths = runtime.paths({ user_data_dir = user_data_dir })
  local directory = paths.config
  -- The deployment creates this directory, but creating it here also makes
  -- the command work on a fresh user data directory.
  local shell_directory = directory:gsub("'", "'\\''")
  pcall(os.execute, "mkdir -p '" .. shell_directory .. "'")
  local path = paths.selection
  local temporary = path .. ".tmp-" .. tostring(os.time()) .. "-" .. tostring(math.random(1000000))
  local file = io.open(temporary, "w")
  if not file then return false end
  local ok = file:write(model_id .. "\n")
  if ok and file.flush then ok = file:flush() end
  file:close()
  if not ok then
    os.remove(temporary)
    return false
  end
  local renamed = os.rename(temporary, path)
  if not renamed then os.remove(temporary) end
  return renamed == true
end

local function user_data_dir()
  local api = rawget(_G, "rime_api")
  if api and type(api.get_user_data_dir) == "function" then
    local ok, value = pcall(api.get_user_data_dir)
    if ok and type(value) == "string" and value ~= "" then return value end
  end
  return "."
end

local function status_candidate(seg, text, comment, index)
  local candidate = Candidate("mohu_tiger_model_status", seg.start, seg._end, text, comment)
  candidate.quality = 1000000 - (index or 0)
  candidate.mohu_tiger_model_status = true
  return candidate
end

local function model_candidate(seg, model, current, index)
  local comment = current and "当前模型" or "可用模型"
  local model_id = model.id or model.selection_id
  local candidate = Candidate("mohu_tiger_model:" .. tostring(model_id), seg.start, seg._end,
    model.display_label or model.label or model.id, comment)
  candidate.quality = 1000000 - (index or 0)
  pcall(function() candidate.mohu_tiger_model_id = model_id end)
  pcall(function() candidate.model_id = model_id end)
  return candidate
end

local function status_message(selection)
  if not selection or selection.status == "no-model" then
    return "当前未选择可用模型", "请安装并部署模型文件"
  end
  if selection.status == "unknown-selection" then
    return "当前模型选择未知", "请选择下方可用模型"
  end
  if selection.status == "unavailable" then
    local model = selection.model
    local label = model and (model.display_label or model.label or model.id) or "所选模型"
    return "当前模型不可用：" .. label, "请检查模型配置和资源文件"
  end
  return nil
end

local translator = {}
function translator.func(input, seg, env)
  if input ~= PREFIX then return end
  local models = catalog.list() or {}
  local selection = catalog.status() or {}
  local available = {}
  for _, model in ipairs(models) do
    if model.available == true then available[#available + 1] = model end
  end
  local index = 1
  local status_text, status_comment = status_message(selection)
  if status_text then
    yield(status_candidate(seg, status_text, status_comment, index))
    index = index + 1
  end
  if #available == 0 then
    if not status_text then
      yield(status_candidate(seg, "没有可用模型", "请安装并部署模型文件", index))
    end
    return
  end
  for _, model in ipairs(available) do
    local id = model.id or model.selection_id
    local current = selection.status == "available" and selection.selection_id == id
    yield(model_candidate(seg, model, current, index))
    index = index + 1
  end
end

local processor = {}
function processor.func(key_event, env)
  if not key_event or key_event:release() then return NOOP end
  local context = env and env.engine and env.engine.context
  if not context or context.input ~= PREFIX or not is_commit_key(key_event.keycode) then
    return NOOP
  end
  local candidate = selected_for_key(context, key_event.keycode)
  if not candidate or (candidate.type ~= "mohu_tiger_model_status" and
      not tostring(candidate.type or ""):match("^mohu_tiger_model:")) then
    return NOOP
  end
  local model_id = candidate.mohu_tiger_model_id or candidate.model_id
  if not model_id then
    model_id = tostring(candidate.type or ""):match("^mohu_tiger_model:(.+)$")
  end
  if model_id then
    if not write_selection(user_data_dir(), model_id) then
      return ACCEPTED
    end
    if context.clear then
      context:clear()
    else
      context.input = ""
      if context.refresh_non_confirmed_composition then
        context:refresh_non_confirmed_composition()
      end
    end
    local reload = reranker.reload_profile or reranker.reload
    if type(reload) == "function" then pcall(reload) end
  elseif context.clear then
    context:clear()
  else
    context.input = ""
    if context.refresh_non_confirmed_composition then
      context:refresh_non_confirmed_composition()
    end
  end
  return ACCEPTED
end

M.translator = translator
M.processor = processor
M._test = {
  write_selection = write_selection,
  status_message = status_message,
  selected_for_key = selected_for_key,
}
return M
