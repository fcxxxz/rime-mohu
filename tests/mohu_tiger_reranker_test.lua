package.path = "./tiger_sentence_native/?.lua;" .. package.path

local reranker = dofile("tiger_sentence_native/mohu_tiger_reranker.lua")

local function config(values)
  return {
    get_string = function(_, key)
      local value = values[key]
      return value == nil and nil or tostring(value)
    end,
    get_int = function(_, key)
      local value = values[key]
      return value == nil and nil or tonumber(value)
    end,
    get_double = function(_, key)
      local value = values[key]
      return value == nil and nil or tonumber(value)
    end,
    get_bool = function(_, key)
      return values[key]
    end,
  }
end

local context = {
  input = "abcdefghijkl",
  options = { mohu_llm_model_rerank = true },
  properties = {},
}
function context:get_option(name) return self.options[name] or false end
function context:get_property(name) return self.properties[name] end
function context:set_property(name, value) self.properties[name] = value end
function context:set_option(name, value) self.options[name] = value end

local env = {
  engine = {
    context = context,
    schema = { config = config({
      ["tiger/rerank_socket"] = "/tmp/mohu-qwen35-test.sock",
      ["tiger/rerank_timeout_ms"] = 20,
    }) },
  },
}

package.preload["mohu_tiger_reranker_profile"] = function()
  return {
    schema = 1,
    model_id = "Qwen3.5-0.8B-Base",
    model_path = "tiger/models/qwen3.5-0.8b-mlx",
    model_sha256 = string.rep("a", 64),
    normalization = "mean_token_logp",
    alpha = 2,
    top_k = 5,
    min_raw_len = 3,
    max_conf_gap = 10,
  }
end

local requests = 0
reranker._test.set_transport(function(request, timeout_ms)
  requests = requests + 1
  assert(timeout_ms == 20)
  assert(request.version == 1)
  assert(request.request_id ~= nil)
  assert(request.candidates[1] == "甲")
  assert(#request.candidates == 5)
  return {
    ok = true,
    version = 1,
    request_id = request.request_id,
    status = "ok",
    model = { sha256 = string.rep("a", 64) },
    scores = {
      { sum_logp = -9, predicted_tokens = 3 },
      { sum_logp = -3, predicted_tokens = 3 },
      { sum_logp = -6, predicted_tokens = 3 },
      { sum_logp = -7, predicted_tokens = 3 },
      { sum_logp = -8, predicted_tokens = 3 },
    },
  }
end)

local items = {
  { text = "甲", score = 10, confidence = 0 },
  { text = "乙", score = 9.9, confidence = 0 },
  { text = "丙", score = 9.8, confidence = 0 },
  { text = "丁", score = 9.7, confidence = 0 },
  { text = "戊", score = 9.6, confidence = 0 },
  { text = "己", score = 9.5, confidence = 0 },
}

local first = reranker.rerank(items, "abcdefghijkl", context, env)
assert(first ~= nil, "valid scorer response should rerank")
assert(first[1].text == "乙", "normalized neural score should move the best candidate")
assert(first[2].text == "丙")
assert(first[6].text == "己", "tail candidates must preserve order")
assert(requests == 1)

local second = reranker.rerank(items, "abcdefghijkl", context, env)
assert(second ~= nil and requests == 1, "identical input must hit the score cache")

local twenty_request_items = {}
for index = 1, 20 do
  twenty_request_items[index] = {
    text = "候选" .. index,
    score = 0,
    confidence = 0,
  }
end
reranker._test.state.profile.top_k = 20
reranker._test.state.profile.base_top_k = 20
reranker._test.state.profile.max_top_k = 20
reranker._test.set_transport(function(request)
  assert(#request.candidates == 20,
    "a top_k=20 profile must send the full bounded request")
  local scores = {}
  for index = 1, 20 do
    scores[index] = { sum_logp = index - 20, predicted_tokens = 1 }
  end
  return {
    ok = true,
    version = 1,
    request_id = request.request_id,
    status = "ok",
    model = { sha256 = string.rep("a", 64) },
    scores = scores,
  }
end)
reranker._test.clear_cache()
local twenty_request_ranked = reranker.rerank(
  twenty_request_items, "abcdefghijkl", context, env)
assert(twenty_request_ranked and twenty_request_ranked[1].text == "候选20",
  "all twenty returned scores must participate in reranking")
reranker._test.state.profile.top_k = 5
reranker._test.state.profile.base_top_k = 5
reranker._test.state.profile.max_top_k = 5
reranker._test.clear_cache()

context.input = "ab"
assert(reranker.rerank(items, "ab", context, env) == nil,
  "raw shorter than profile threshold must fail open")
assert(requests == 1)

context.input = "abcdefghijkl"
reranker._test.set_transport(function()
  error("transport must not be called for invalid scorer response")
end)
reranker._test.clear_cache()
local invalid = reranker._test.validate_profile({
  schema = 1,
  model_id = "Qwen3.5-0.8B-Base",
  model_path = "tiger/models/qwen3.5-0.8b-mlx",
  model_sha256 = "bad",
  normalization = "mean_token_logp",
  alpha = 2,
  top_k = 5,
  min_raw_len = 3,
  max_conf_gap = 10,
})
assert(invalid == nil, "invalid model hash must disable reranking")

local function profile_with(changes)
  local profile = {
    schema = 1,
    model_id = "Qwen3.5-0.8B-Base",
    model_path = "tiger/qwen3.5-0.8b.gguf",
    model_sha256 = string.rep("a", 64),
    normalization = "sum_token_logp",
    alpha = 0.8,
    top_k = 5,
    min_raw_len = 3,
    max_conf_gap = 10,
  }
  for key, value in pairs(changes or {}) do profile[key] = value end
  return profile
end

for _, normalization in ipairs({ "sum_logp", "mean_logp", "per_token" }) do
  assert(reranker._test.validate_profile(profile_with({
    normalization = normalization,
  })) == nil, "only calibrated normalization names are valid")
end
local shipped_profile = dofile("tiger_sentence_native/mohu_tiger_reranker_profile.lua")
assert(shipped_profile.top_k == 5,
  "the shipped Lua profile must keep the default budget at five")
assert(shipped_profile.base_top_k == 5 and shipped_profile.max_top_k == 20,
  "the shipped Lua profile must expose the bounded adaptive budget")
local default_budget = reranker._test.validate_profile(profile_with())
assert(default_budget and default_budget.top_k == 5,
  "the shipped profile budget must remain five")
for top_k = 2, 20 do
  local validated = reranker._test.validate_profile(profile_with({ top_k = top_k }))
  assert(validated and validated.top_k == top_k,
    "profile top_k must accept bounded adaptive budgets")
end
for _, top_k in ipairs({ 1, 21, 2.5 }) do
  assert(reranker._test.validate_profile(profile_with({ top_k = top_k })) == nil,
    "profile top_k must reject values outside the integer range 2..20")
end
assert(reranker._test.validate_profile(profile_with({ alpha = 1e100 })) == nil,
  "profile alpha must have a bounded safety limit")

local twenty_items = {}
local twenty_scores = {}
for index = 1, 20 do
  twenty_items[index] = { text = tostring(index), score = 0 }
  twenty_scores[index] = index == 20 and 1 or 0
end
local twenty_ranked = reranker._test.blend_and_stable_sort(
  twenty_items, twenty_scores, 1)
assert(twenty_ranked and twenty_ranked[1].text == "20",
  "a valid twenty-candidate budget must not be truncated to five")

local huge = reranker._test.blend_and_stable_sort(
  { { text = "甲", score = 1e308 }, { text = "乙", score = 0 } },
  { 1e308, 0 }, 1e308)
assert(huge and huge[1].text == "甲",
  "rank-normalized fusion must remain finite for large native scores")
local nonfinite = reranker._test.blend_and_stable_sort(
  { { text = "甲", score = math.huge }, { text = "乙", score = 0 } },
  { 1, 0 }, 1)
assert(nonfinite == nil, "non-finite native scores must fail open")

reranker._test.set_transport(function(request)
  return {
    version = 1,
    request_id = request.request_id,
    status = "ok",
    scores = {
      { sum_logp = -1, predicted_tokens = 1 },
      { sum_logp = -2, predicted_tokens = 1 },
      { sum_logp = -3, predicted_tokens = 1 },
      { sum_logp = -4, predicted_tokens = 1 },
      { sum_logp = -5, predicted_tokens = 1 },
    },
  }
end, true)
reranker._test.clear_cache()
assert(reranker.rerank(items, "abcdefghijkl", context, env) == nil,
  "production transport must reject a response without model hash")

reranker._test.set_transport(function(request)
  return {
    version = 1,
    request_id = request.request_id,
    model_sha256 = string.rep("a", 64),
    scores = {
      { sum_logp = -1, predicted_tokens = 1 },
      { sum_logp = -2, predicted_tokens = 1 },
      { sum_logp = -3, predicted_tokens = 1 },
      { sum_logp = -4, predicted_tokens = 1 },
      { sum_logp = -5, predicted_tokens = 1 },
    },
  }
end, true)
reranker._test.clear_cache()
assert(reranker.rerank(items, "abcdefghijkl", context, env) == nil,
  "production transport must reject a response without an ok/status marker")

reranker._test.set_transport(function(request)
  return {
    ok = true,
    version = 1,
    request_id = request.request_id,
    status = "ok",
    model = { sha256 = string.rep("a", 64) },
    -- Strict production responses identify the normalization used.
    scores = {
      { sum_logp = -1, predicted_tokens = 1 },
      { sum_logp = -2, predicted_tokens = 1 },
      { sum_logp = -3, predicted_tokens = 1 },
      { sum_logp = -4, predicted_tokens = 1 },
      { sum_logp = -5, predicted_tokens = 1 },
    },
  }
end, true)
reranker._test.clear_cache()
assert(reranker.rerank(items, "abcdefghijkl", context, env) == nil,
  "production transport must reject a response without normalization")

assert(type(reranker._test.deadline_remaining) == "function")
assert(reranker._test.deadline_remaining(100, 20, 105) == 15,
  "deadline budget must use monotonic wall time")

local function fake_line_socket(data)
  local socket = { data = data, position = 1, timeout = nil }
  function socket:settimeout(value) self.timeout = value end
  function socket:receive(size)
    assert(size == 1)
    if self.position > #self.data then return nil, "closed" end
    local value = self.data:sub(self.position, self.position)
    self.position = self.position + 1
    return value
  end
  return socket
end
local bounded_short = reranker._test.receive_line_bounded(
  fake_line_socket("ok\n"), 1000)
assert(bounded_short == "ok", "bounded socket reader must accept a short line")
local bounded_long = reranker._test.receive_line_bounded(
  fake_line_socket(string.rep("x", 65537) .. "\n"), 1000)
assert(bounded_long == nil, "bounded socket reader must reject oversized lines")
local no_timeout_socket = fake_line_socket("ok\n")
no_timeout_socket.settimeout = nil
assert(reranker._test.receive_line_bounded(no_timeout_socket, 1000) == nil,
  "socket readers without timeout support must fail open")

local http_payload
reranker._test.set_transport(nil)
reranker._test.set_http_request(function(_, payload)
  http_payload = payload
  return {
    ok = true,
    version = 1,
    request_id = payload.request_id,
    status = "ok",
    normalize = "mean_logp",
    model_sha256 = string.rep("a", 64),
    scores = {
      { sum_logp = -1, predicted_tokens = 1 },
      { sum_logp = -2, predicted_tokens = 1 },
      { sum_logp = -3, predicted_tokens = 1 },
      { sum_logp = -4, predicted_tokens = 1 },
      { sum_logp = -5, predicted_tokens = 1 },
    },
  }
end)
local http_env = {
  engine = { context = context, schema = { config = config({
    ["tiger/rerank_socket"] = "",
    ["tiger/rerank_http_endpoint"] = "http://127.0.0.1:9/score",
    ["tiger/rerank_timeout_ms"] = 20,
  }) } },
}
reranker._test.clear_cache()
assert(reranker.rerank(items, "abcdefghijkl", context, http_env) ~= nil)
assert(http_payload and not http_payload.messages and http_payload.candidates,
  "HTTP fallback must use the direct score protocol, never a prompt")

reranker._test.set_http_request(function(_, payload)
  return {
    ok = true,
    version = 1,
    request_id = payload.request_id,
    status = "ok",
    scores = {
      { sum_logp = -1, predicted_tokens = 1 },
      { sum_logp = -2, predicted_tokens = 1 },
      { sum_logp = -3, predicted_tokens = 1 },
      { sum_logp = -4, predicted_tokens = 1 },
      { sum_logp = -5, predicted_tokens = 1 },
    },
  }
end)
reranker._test.clear_cache()
assert(reranker.rerank(items, "abcdefghijkl", context, http_env) == nil,
  "direct HTTP responses without the calibrated model hash must fail open")

local utf_items = {
  { text = "甲", score = 1, confidence = 0 },
  { text = "乙", score = 0, confidence = 0 },
}
assert(reranker._test.utf8_length("你好") == 2)
assert(reranker._test.blend_and_stable_sort(utf_items, { 0, 1 }, 2)[1].text == "乙")

reranker._test.set_transport(function(request)
  assert(request.context == "已提交")
  assert(request.candidate_mode == "complete")
  assert(request.candidates[1] == "甲", "scorer must receive full decoded text")
  return {
    version = 1,
    request_id = request.request_id,
    status = "ok",
    model = { sha256 = string.rep("a", 64) },
    scores = {
      { sum_logp = -3, predicted_tokens = 3 },
      { sum_logp = -6, predicted_tokens = 3 },
      { sum_logp = -7, predicted_tokens = 3 },
      { sum_logp = -8, predicted_tokens = 3 },
      { sum_logp = -9, predicted_tokens = 3 },
    },
  }
end)
reranker._test.clear_cache()
assert(reranker.rerank(items, "abcdefghijkl", context, env, "已提交") ~= nil)

local bad_confidence_items = {}
for index, item in ipairs(items) do
  bad_confidence_items[index] = {
    text = item.text,
    score = item.score,
  }
  if index ~= 1 then bad_confidence_items[index].confidence = item.confidence end
end
assert(reranker.rerank(bad_confidence_items, "abcdefghijkl", context, env, "已提交") == nil,
  "malformed native confidence must fail open")

local bad_later_confidence_items = {}
for index, item in ipairs(items) do
  bad_later_confidence_items[index] = { text = item.text, score = item.score, confidence = item.confidence }
end
bad_later_confidence_items[3].confidence = nil
assert(reranker.rerank(bad_later_confidence_items, "abcdefghijkl", context, env, "已提交") == nil,
  "every scored native confidence must be finite")

reranker._test.clear_cache()
local cache_profile = reranker._test.state.profile
for index = 1, 70 do
  local key = reranker._test.cache_key(
    "raw" .. index, { "甲", "乙" }, cache_profile, "configured-model", "")
  assert(type(key) == "string" and key ~= "")
  reranker._test.cache_put(key, {
    { sum_logp = -1, predicted_tokens = 1 },
    { sum_logp = -2, predicted_tokens = 1 },
  })
end
assert(reranker._test.cache_size() == 64, "score cache must be bounded to 64 entries")

local env_two = {
  engine = { context = context, schema = env.engine.schema },
}
reranker.init(env)
reranker.init(env_two)
assert(reranker._test.state.references >= 2)
reranker.fini(env)
assert(reranker._test.state.references >= 1,
  "one component finishing must not tear down another component's transport")
reranker.fini(env_two)
assert(reranker._test.state.references == 0)

package.preload["mohu_tiger_reranker_profile"] = function()
  return {
    schema = 1,
    model_id = "Qwen3.5-0.8B-MLX-4bit-reloaded",
    model_path = "tiger/models/reloaded",
    model_sha256 = string.rep("b", 64),
    normalization = "sum_token_logp",
    alpha = 0.5,
    top_k = 5,
    min_raw_len = 3,
    max_conf_gap = math.huge,
  }
end
local env_three = {
  engine = { context = context, schema = env.engine.schema },
}
reranker.init(env_three)
assert(reranker._test.state.profile.model_sha256 == string.rep("b", 64),
  "final fini must allow a replacement profile to load")
reranker.fini(env_three)

-- Adaptive policy regressions: one request chooses one budget, and the
-- scale-independent fusion path uses only generic synthetic candidates.
package.preload["mohu_tiger_reranker_profile"] = function()
  return {
    schema = 1,
    model_id = "adaptive-test-model",
    model_path = "tiger/models/adaptive-test",
    model_sha256 = string.rep("c", 64),
    normalization = "mean_token_logp",
    alpha = 1,
    top_k = 5,
    adaptive = true,
    base_top_k = 5,
    max_top_k = 8,
    uncertainty_margin = 0.25,
    diversity_threshold = 0.75,
    fusion = "rank",
    min_raw_len = 1,
    max_conf_gap = math.huge,
  }
end
local adaptive_context = {
  input = "adaptive-input",
  options = { mohu_llm_model_rerank = true },
  properties = {},
}
function adaptive_context:get_option(name) return self.options[name] or false end
function adaptive_context:get_property(name) return self.properties[name] end
function adaptive_context:set_property(name, value) self.properties[name] = value end
local adaptive_env = {
  engine = {
    context = adaptive_context,
    schema = { config = config({
      ["tiger/rerank_socket"] = "/tmp/mohu-qwen35-adaptive.sock",
      ["tiger/rerank_timeout_ms"] = 20,
      ["tiger/rerank_full_timeout_ms"] = 80,
    }) },
  },
}
reranker.fini(env_three)
reranker.init(adaptive_env)
local adaptive_requests = 0
local adaptive_request_sizes = {}
local adaptive_request_timeouts = {}
reranker._test.set_transport(function(request, timeout_ms)
  adaptive_requests = adaptive_requests + 1
  adaptive_request_sizes[#adaptive_request_sizes + 1] = #request.candidates
  adaptive_request_timeouts[#adaptive_request_timeouts + 1] = timeout_ms
  local scores = {}
  for index = 1, #request.candidates do
    scores[index] = { sum_logp = -index, predicted_tokens = 1 }
  end
  return {
    ok = true,
    version = 1,
    request_id = request.request_id,
    status = "ok",
    model = { sha256 = string.rep("c", 64) },
    normalize = request.normalize,
    scores = scores,
  }
end)
local adaptive_items = {}
for index = 1, 20 do
  adaptive_items[index] = {
    text = "共同前缀" .. index,
    score = 1000000 - index,
    confidence = index == 1 and 10 or (index == 2 and 8 or 7),
  }
end
local adaptive_ranked = reranker.rerank(
  adaptive_items, "adaptive-input", adaptive_context, adaptive_env)
assert(adaptive_ranked ~= nil)
assert(adaptive_request_sizes[1] == 5,
  "a decisive native margin must use the base budget")
assert(adaptive_request_timeouts[1] == 20,
  "the normal shortlist must use the short scorer deadline")
assert(adaptive_ranked[6].text == adaptive_items[6].text,
  "unscored native tail must preserve its original order")
assert(reranker.rerank(
  adaptive_items, "adaptive-input", adaptive_context, adaptive_env) ~= nil)
assert(adaptive_requests == 1,
  "repeating the same composition must reuse one cached score request")

for index = 1, 20 do
  adaptive_items[index].text = string.char(64 + ((index - 1) % 20) + 1) .. index
  adaptive_items[index].confidence = 10 - index * 0.01
end
local ambiguous_ranked = reranker.rerank(
  adaptive_items, "adaptive-input", adaptive_context, adaptive_env)
assert(ambiguous_ranked ~= nil)
assert(adaptive_request_sizes[2] == 8,
  "a close and diverse native set must use the larger budget")
assert(adaptive_request_timeouts[2] == 80,
  "an expanded shortlist must use the measured full-pool deadline")

-- The scorer switches to its twenty-row kernel shape for any request above
-- five candidates, even when a custom profile calls that budget "base".
reranker._test.state.profile.top_k = 8
reranker._test.state.profile.base_top_k = 8
reranker._test.state.profile.max_top_k = 8
reranker._test.clear_cache()
local custom_base_ranked = reranker.rerank(
  adaptive_items, "custom-base-input", adaptive_context, adaptive_env)
assert(custom_base_ranked ~= nil)
assert(adaptive_request_sizes[3] == 8)
assert(adaptive_request_timeouts[3] == 80,
  "an eight-row base budget must use the twenty-row scorer deadline")
assert(adaptive_requests == 3,
  "a changed selected budget must not reuse the smaller-budget cache entry")

local key_base = reranker._test.cache_key(
  "raw", { "甲", "乙" }, reranker._test.state.profile, "model", "", 5)
local key_large = reranker._test.cache_key(
  "raw", { "甲", "乙" }, reranker._test.state.profile, "model", "", 8)
assert(key_base ~= key_large, "cache identity must include the selected budget")

local normalized = reranker._test.rank_normalize_scores({ 1e12, 0, -1e12 })
assert(normalized and normalized[1] > normalized[2] and normalized[2] > normalized[3],
  "rank normalization must preserve ordering without raw-score scale")

local reload_profile = reranker.reload_profile
assert(type(reload_profile) == "function", "reranker must expose a public reload hook")
reranker._test.clear_cache()
reranker._test.cache_put("reload-test", {
  { sum_logp = -1, predicted_tokens = 1 },
})
assert(reranker._test.cache_size() == 1)
reload_profile()
assert(reranker._test.cache_size() == 0,
  "profile reload must clear score cache")
assert(reranker._test.state.profile_loaded == false,
  "profile reload must force a fresh profile load")

print("Mohu tiger reranker tests passed")
