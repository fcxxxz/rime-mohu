package.path = "./tiger_sentence_native/?.lua;./lua/?.lua;" .. package.path

Candidate = function(candidate_type, start, finish, text, comment)
  return { type = candidate_type, start = start, _end = finish, text = text, comment = comment }
end

local yielded = {}
yield = function(candidate) yielded[#yielded + 1] = candidate end

local root = os.tmpname()
os.remove(root)
assert(os.execute("mkdir -p " .. root .. "/tiger") == true)
rime_api = { get_user_data_dir = function() return root end }

local models = {
  { id = "qwen35-0.8b", display_label = "Qwen3.5-0.8B-MLX-4bit", available = true },
  { id = "qwen3-0.6b", display_label = "Qwen3-0.6B-4bit", available = true },
}
local status = { status = "available", selection_id = "qwen35-0.8b", model = models[1] }
package.preload["mohu_tiger_model_catalog"] = function()
  return {
    list = function() return models end,
    status = function() return status end,
    default_id = "qwen35-0.8b",
  }
end
local reload_count = 0
package.preload["mohu_tiger_reranker"] = function()
  return { reload_profile = function() reload_count = reload_count + 1 end }
end

local menu = dofile("tiger_sentence_native/mohu_tiger_model_menu.lua")
local segment = { start = 0, _end = 6, selected_index = 0 }
local context = {
  input = "/model",
  composition = {
    empty = function() return false end,
    back = function() return segment end,
  },
  clear_count = 0,
  clear = function(self) self.clear_count = self.clear_count + 1; self.input = "" end,
}
local env = { engine = { context = context } }
local engine_keys = {}
env.engine.process_key = function(_, event) engine_keys[#engine_keys + 1] = event end

local function translate()
  yielded = {}
  menu.translator.func("/model", segment, env)
  return yielded
end

local available = translate()
assert(#available == 2, "only available models should be listed")
assert(available[1].text:find("Qwen3.5", 1, true))
assert(available[1].comment:find("当前", 1, true), "current model must be marked")

local function event(keycode)
  return {
    keycode = keycode,
    release = function() return false end,
    shift = function() return false end,
    ctrl = function() return false end,
    alt = function() return false end,
  }
end
segment.menu = {
  prepare = function(_, index) segment.selected_index = index - 1 end,
  get_candidate_at = function(_, index) return available[index + 1] end,
}
local result = menu.processor.func(event(0x32), env)
assert(result == 1, "numeric model selection must be accepted")
local file = assert(io.open(root .. "/tiger/model-selection", "r"))
local selected = file:read("*a"); file:close()
assert(selected == "qwen3-0.6b\n", "selection must persist stable model id")
assert(context.clear_count == 1, "selection must clear composition")
assert(reload_count == 1, "selection must reload reranker profile")

-- Space/Enter use the highlighted candidate and must be consumed as commands,
-- never committed as the visible model label.
context.input = "/model"
segment.get_selected_candidate = function() return available[1] end
local before_reload = reload_count
local space_result = menu.processor.func(event(0x20), env)
assert(space_result == 1, "space selection must be accepted")
assert(context.clear_count == 2, "space selection must clear composition")
assert(reload_count == before_reload + 1, "space selection must reload profile")

context.input = "/model"
local before_keypad_reload = reload_count
local keypad_result = menu.processor.func(event(0xff8d), env)
assert(keypad_result == 1, "keypad Enter selection must be accepted")
assert(context.clear_count == 3, "keypad Enter selection must clear composition")
assert(reload_count == before_keypad_reload + 1,
  "keypad Enter selection must reload profile")

models = { models[1] }
status = { status = "unknown-selection", selection_id = "made-up-model" }
context.input = "/model"
segment.selected_index = 0
yielded = {}
menu.translator.func("/model", segment, env)
assert(#yielded == 2, "unknown selection should include a friendly status and available models")
assert(yielded[1].comment:find("未知", 1, true) or yielded[1].text:find("未知", 1, true))

models = {}
status = { status = "no-model", selection_id = nil }
yielded = {}
menu.translator.func("/model", segment, env)
assert(#yielded == 1, "empty catalog should emit one friendly status candidate")
assert(yielded[1].text:find("模型", 1, true))

print("Mohu tiger model menu tests passed")
