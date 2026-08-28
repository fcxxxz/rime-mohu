-- Local opt-in profile for the pinned Qwen3-0.6B 4-bit checkpoint.
-- Same layout as mohu_tiger_reranker_profile.lua; only the model identity
-- differs.  Fusion parameters are inherited from the Qwen3.5 profile and
-- have not been recalibrated for this checkpoint yet.
local function user_data_dir()
  local api = rawget(_G, "rime_api")
  if api and type(api.get_user_data_dir) == "function" then
    local ok, value = pcall(api.get_user_data_dir)
    if ok and type(value) == "string" and value ~= "" then
      return (value:gsub("/+$", ""))
    end
  end
  return "."
end

local model_root = user_data_dir() .. "/tiger/models/Qwen3-0.6B-4bit"

return {
  schema = 1,
  model_id = "Qwen3-0.6B-4bit",
  model_path = model_root,
  model_sha256 = "2de6c7d42ac12c447715e06bfab6497bdd49707bec990ae3cddce3a8c4ba0548",
  -- Sum likelihood is intentional: the target regression differs by one
  -- tokenizer piece.  Scores are fused as centered ranks below, so the neural
  -- weight must be strong enough to overturn a near-tied native branch.
  normalization = "sum_token_logp",
  -- ``top_k`` is the normal five-row budget.  Ambiguous native margins may
  -- expand one time, but never beyond this explicit upper bound.
  -- Local opt-in heuristic; no independent labelled corpus has calibrated
  -- this value yet.  Keep it explicit so future evaluation can replace it.
  alpha = 4.0,
  -- Canonical adaptive-budget name; ``top_k`` remains for older profiles.
  base_top_k = 5,
  top_k = 5,
  adaptive = true,
  min_top_k = 2,
  max_top_k = 20,
  -- A close native leader plus materially different continuations warrants
  -- one full top-20 request; decisive inputs stay on the five-row fast path.
  shortlist_confidence_margin = 2.0,
  shortlist_score_margin = 2.0,
  diversity_threshold = 0.45,
  -- Native and neural scores have unrelated units; centered rank keeps the
  -- blend bounded and stable across candidate-set scale changes.
  fusion_normalization = "rank",
  min_raw_len = 8,
  max_conf_gap = 2.0,
}
