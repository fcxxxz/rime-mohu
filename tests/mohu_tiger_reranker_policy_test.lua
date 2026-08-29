package.path = "./tiger_sentence_native/?.lua;" .. package.path

local PROFILE_HASH = string.rep("a", 64)

package.preload["mohu_tiger_reranker_profile"] = function()
  return {
    schema = 1,
    model_id = "Qwen3.5-0.8B-MLX-4bit",
    model_path = "/tmp/qwen35",
    model_sha256 = PROFILE_HASH,
    normalization = "sum_token_logp",
    alpha = 1,
    top_k = 5,
    max_top_k = 20,
    adaptive_margin = 0.5,
    min_raw_len = 1,
    max_conf_gap = 10,
  }
end

local reranker = dofile("tiger_sentence_native/mohu_tiger_reranker.lua")

local function profile()
  return assert(reranker._test.validate_profile({
    schema = 1,
    model_id = "Qwen3.5-0.8B-MLX-4bit",
    model_path = "/tmp/qwen35",
    model_sha256 = PROFILE_HASH,
    normalization = "sum_token_logp",
    alpha = 1,
    top_k = 5,
    max_top_k = 20,
    adaptive_margin = 0.5,
    min_raw_len = 1,
    max_conf_gap = 10,
  }))
end

local function candidate(index, score, confidence)
  return {
    text = "candidate-" .. index,
    score = score,
    confidence = confidence,
  }
end

local items = {
  candidate(1, 100.00, 50.00),
  candidate(2, 99.90, 49.90),
  candidate(3, 99.80, 49.80),
  candidate(4, 99.70, 49.70),
  candidate(5, 99.60, 49.60),
  candidate(6, 99.55, 49.55),
  candidate(7, 99.39, 49.39),
  candidate(8, 98.00, 48.00),
}

assert(type(reranker._test.choose_shortlist_k) == "function",
  "adaptive shortlist policy must be exposed to focused tests")
assert(reranker._test.choose_shortlist_k(items, profile()) == 6,
  "one-shot shortlist must include candidates within the native leader margin")

local full_pool_profile = profile()
full_pool_profile.shortlist_confidence_margin = 2
full_pool_profile.shortlist_score_margin = 2
full_pool_profile.diversity_threshold = 0.5
local diverse_items = {}
for index, item in ipairs(items) do
  diverse_items[index] = candidate(index, item.score, item.confidence)
  diverse_items[index].text = string.char(64 + index) .. " phrase " .. index
end
assert(reranker._test.choose_shortlist_k(diverse_items, full_pool_profile) == #items,
  "an ambiguous and diverse native pool must be scored in one full request")
assert(reranker._test.common_candidate_prefix({
  { text = "短" }, { text = "短长" },
}, 2) == "",
  "a candidate that equals the shared prefix must not become scoring context")
assert(reranker._test.common_candidate_prefix({
  { text = "共同甲" }, { text = "共同乙" },
}, 2) == "共同",
  "a strict shared prefix should remain available as scoring context")

-- The policy helper is intentionally tolerant of a raw (not yet validated)
-- profile.  This keeps aliases usable during reloads and makes the base
-- budget unambiguous when legacy ``top_k`` is also present.
local raw_alias_profile = {
  base_top_k = 5,
  top_k = 8,
  max_top_k = 20,
  uncertainty_margin = 0.5,
  diversity_threshold = 0,
}
assert(reranker._test.choose_shortlist_k(items, raw_alias_profile) == 6,
  "raw profile aliases must select one adaptive shortlist from base_top_k")

local unbounded_profile = {
  base_top_k = 5,
  max_top_k = 20,
  adaptive = true,
  shortlist_confidence_margin = math.huge,
  shortlist_score_margin = math.huge,
  diversity_threshold = 0,
}
assert(reranker._test.choose_shortlist_k(items, unbounded_profile) == #items,
  "infinite uncertainty thresholds must remain unbounded")

local score_rescue = {}
for index, item in ipairs(items) do
  score_rescue[index] = candidate(index, item.score, item.confidence)
end
score_rescue[6].confidence = 40
assert(reranker._test.choose_shortlist_k(score_rescue, profile()) == 6,
  "a close native score must keep a candidate when confidence aggregation differs")

assert(type(reranker._test.rank_z_normalize) == "function",
  "rank/z normalization must be exposed to focused tests")
local first = assert(reranker._test.rank_z_normalize({ 100, 90, 80, 70, 60 }))
local shifted_scaled = assert(reranker._test.rank_z_normalize({ 21, 19, 17, 15, 13 }))
for index = 1, #first do
  assert(math.abs(first[index] - shifted_scaled[index]) < 1e-12,
    "rank/z normalization must be invariant to positive affine score scaling")
end
local tied = assert(reranker._test.rank_z_normalize({ 4, 4, 4 }))
assert(tied[1] == 0 and tied[2] == 0 and tied[3] == 0,
  "equal evidence must not manufacture an ordering preference")

local tail_a = candidate(7, 94, 44)
local tail_b = candidate(8, 93, 43)
local blend_items = {
  candidate(1, 100, 50),
  candidate(2, 99, 49),
  candidate(3, 98, 48),
  candidate(4, 97, 47),
  candidate(5, 96, 46),
  candidate(6, 95, 45),
  tail_a,
  tail_b,
}
local neural = { -9, -1, -5, -6, -7, -8 }
local blended = assert(reranker._test.blend_and_stable_sort(blend_items, neural, 1))
assert(blended[1].text == "candidate-2",
  "normalized neural evidence must be able to reorder a close native shortlist")
assert(blended[7] == tail_a and blended[8] == tail_b,
  "unscored tail candidates must preserve identity and native order")

local key_five = reranker._test.cache_key("raw", { "a", "b" }, profile(), "model", "", 5)
local key_six = reranker._test.cache_key("raw", { "a", "b" }, profile(), "model", "", 6)
assert(key_five ~= key_six, "cache identity must include the selected shortlist size")

local context = {
  input = "abcdefgh",
  options = { mohu_llm_model_rerank = true },
}
function context:get_option(name) return self.options[name] or false end

local values = {
  ["tiger/rerank_socket"] = "/tmp/mohu-adaptive-policy.sock",
  ["tiger/rerank_timeout_ms"] = 20,
}
local config = {}
function config:get_string(key) return values[key] end
function config:get_int(key) return tonumber(values[key]) end
function config:get_double(key) return tonumber(values[key]) end
local env = { engine = { context = context, schema = { config = config } } }

local calls = 0
reranker._test.set_transport(function(request)
  calls = calls + 1
  assert(#request.candidates == 6,
    "adaptive policy must issue one request with the preselected K")
  assert(request.context == "candidate-",
    "empty context should use the common candidate prefix for conditional scoring")
  local scores = {}
  for index = 1, #request.candidates do
    scores[index] = { sum_logp = -index, predicted_tokens = 1 }
  end
  return {
    ok = true,
    version = 1,
    status = "ok",
    request_id = request.request_id,
    scores = scores,
  }
end)
reranker._test.clear_cache()
local ranked = assert(reranker.rerank(items, "abcdefgh", context, env))
assert(calls == 1, "adaptive shortlist must never iterate or issue follow-up requests")
assert(ranked[7] == items[7] and ranked[8] == items[8],
  "runtime reranking must leave the unscored tail untouched")
assert(reranker.rerank(items, "abcdefgh", context, env) ~= nil and calls == 1,
  "the selected K request must be reusable from cache")

reranker._test.set_transport(function(request)
  calls = calls + 1
  local scores = {}
  for index = 1, #request.candidates - 1 do
    scores[index] = { sum_logp = -index, predicted_tokens = 1 }
  end
  return {
    ok = true,
    version = 1,
    status = "ok",
    request_id = request.request_id,
    scores = scores,
  }
end)
reranker._test.clear_cache()
assert(reranker.rerank(items, "abcdefgh", context, env) == nil,
  "a response count different from the selected K must fail open")

print("Mohu tiger adaptive reranker policy tests passed")
