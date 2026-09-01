-- A selected Rime segment remains in Context.input while the translator gets
-- only the active segment's input.  Native candidates must not re-display the
-- selected segment's raw code in their preedit.
package.path = "./tiger_sentence_native/?.lua;./lua/?.lua;" .. package.path

local original_loadlib = package.loadlib
local yielded = {}
local decode_inputs = {}

rime_api = {
  get_user_data_dir = function() return "/tmp/mohu-selected-segment" end,
}

local context = {
  input = "zl jyuf",
  properties = {
  },
  options = {
    mohu_llm_model_rerank = false,
  },
}
local selected_segment = {
  start = 0,
  _end = 2,
  status = "kSelected",
  get_selected_candidate = function()
    return { text = "在" }
  end,
}
context.composition = {
  toSegmentation = function()
    return {
      get_segments = function()
        return { selected_segment }
      end,
    }
  end,
}
function context:get_property(name) return self.properties[name] end
function context:set_property(name, value) self.properties[name] = value end
function context:get_option(name) return self.options[name] or false end

local config = {
  get_string = function(_, key)
    if key == "tiger/initial_quality" then return "50" end
    return nil
  end,
  get_int = function() return nil end,
}
local engine = { context = context, schema = { config = config } }
local env = { engine = engine }

package.preload["mohu_tiger_reranker"] = function()
  return {
    init = function() end,
    fini = function() end,
    clear_cache = function() end,
    neural_enabled = function() return false end,
    rerank = function() return nil end,
  }
end

Candidate = function(kind, start_pos, end_pos, text, comment)
  return {
    type = kind,
    start = start_pos,
    _end = end_pos,
    text = text,
    comment = comment,
  }
end
yield = function(candidate) yielded[#yielded + 1] = candidate end

package.loadlib = function()
  return function()
    return {
      create = function() return 1 end,
      free = function() end,
      decode = function(_, raw)
        decode_inputs[#decode_inputs + 1] = raw
        return table.concat({
          "0 0 0 0 1 0",
          "在精神\tzl jy uf\t0\t0\t1\t3:2,6:4,9:6",
          "",
        }, "\n"), 0.1
      end,
    }
  end
end

local native = dofile("tiger_sentence_native/mohu_tiger_sentence.lua")
native.translator.init(env)
local segment = {
  start = 3,
  _end = 7,
  has_tag = function(_, tag) return tag == "abc" end,
}

native.translator.func("jyuf", segment, env)

assert(decode_inputs[1] == "a",
  "engine init must probe the lua runtime with the canary decode first")
assert(decode_inputs[2] == "zljyuf",
  "native decode must retain the selected segment in the language-model context")
assert(#yielded == 1, "selected-segment translation must yield one candidate")
assert(yielded[1].text == "精神",
  "selected candidate text must begin after the already selected prefix")
assert(yielded[1].preedit == "jy uf",
  "selected candidate preedit must exclude the selected segment raw code")

native.translator.fini(env)
package.loadlib = original_loadlib
print("Mohu selected-segment translation test passed")
