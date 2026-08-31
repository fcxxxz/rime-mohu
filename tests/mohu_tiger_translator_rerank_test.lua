package.path = "./tiger_sentence_native/?.lua;./lua/?.lua;" .. package.path

local original_loadlib = package.loadlib
local captured_request
local decoded_raw
local yielded = {}

rime_api = {
  get_user_data_dir = function() return "/tmp/mohu-translator-rerank" end,
}

package.preload["mohu_tiger_reranker_profile"] = function()
  return {
    schema = 1,
    model_id = "Qwen3.5-0.8B-MLX-4bit",
    model_path = "/tmp/qwen35",
    model_sha256 = string.rep("a", 64),
    normalization = "sum_token_logp",
    alpha = 0.01,
    top_k = 5,
    min_raw_len = 1,
    max_conf_gap = math.huge,
  }
end

local reranker = require("mohu_tiger_reranker")
reranker._test.set_transport(function(request)
  captured_request = request
  local scores = {}
  for index = 1, #request.candidates do
    scores[index] = { sum_logp = -index, predicted_tokens = 1 }
  end
  return {
    ok = true,
    version = 1,
    status = "ok",
    request_id = request.request_id,
    model = { sha256 = string.rep("a", 64) },
    scores = scores,
  }
end)

Candidate = function(cand_type, start_pos, end_pos, text, comment)
  return {
    type = cand_type,
    start = start_pos,
    _end = end_pos,
    text = text,
    comment = comment,
  }
end
yield = function(candidate) yielded[#yielded + 1] = candidate end

local output = table.concat({
  "0 0 0 0 5 0",
  "完整候选甲\tna jq\t10\t0\t1\t",
  "完整候选乙\tna jq\t9\t0\t1\t",
  "完整候选丙\tna jq\t8\t0\t1\t",
  "完整候选四\tna jq\t7\t0\t1\t",
  "完整候选五\tna jq\t6\t0\t1\t",
  "",
}, "\n")

package.loadlib = function()
  return function()
    return {
      create = function() return 1 end,
      decode = function(_, raw)
        decoded_raw = raw
        return output, 0.1
      end,
    }
  end
end

local context = {
  input = "najqmzufmekeybyudele",
  options = { mohu_llm_model_rerank = true },
  properties = {},
}
function context:get_option(name) return self.options[name] or false end
function context:get_property(name) return self.properties[name] end
function context:set_property(name, value) self.properties[name] = value end

local config = {
  get_string = function(_, key)
    if key == "tiger/rerank_timeout_ms" then return "1000" end
    return nil
  end,
  get_int = function() return nil end,
  get_double = function() return nil end,
}
local env = { engine = { context = context, schema = { config = config } } }
local segment = {
  start = 0,
  _end = #context.input,
  has_tag = function(_, tag) return tag == "abc" end,
}

local native = dofile("tiger_sentence_native/mohu_tiger_sentence.lua")
native.translator.init(env)
-- Rime can pass only the current segment in `input`; context.input is the
-- complete composition and must be the string sent to the native decoder.
native.translator.func("najq", segment, env)

package.loadlib = original_loadlib
assert(decoded_raw == context.input, "translator must decode the complete composition")
assert(captured_request and captured_request.candidates[1] == "完整候选甲",
  "scorer must receive complete candidate text")
assert(#yielded == 5)
print("Mohu translator rerank composition test passed")
