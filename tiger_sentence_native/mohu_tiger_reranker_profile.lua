-- Profile fields stay calibrated and compatible with older callers.  Model
-- identity/path/hash are supplied by the bounded local catalog.
local catalog = require("mohu_tiger_model_catalog")
local selection = catalog.status()
local selected = selection.model
local model_root = selected and selected.model_path or ""

return {
  schema = 1,
  -- Keep model_id's historical display-name value for scorer/cache
  -- compatibility; expose the stable catalog key separately.
  model_id = selected and selected.display_label or "unknown-selection",
  model_selection_id = selected and selected.id or nil,
  model_label = selected and selected.display_label or "Unknown model selection",
  model_path = model_root,
  model_sha256 = selected and selected.model_sha256 or string.rep("0", 64),
  catalog_status = selection.status,
  model_available = selection.status == "available",
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
