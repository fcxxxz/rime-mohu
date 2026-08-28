# Registered scorer checkpoints shared by the launcher and the switch tool.
# Keys are stable selection names; values are relative to this directory.
# The SHA-256 values are the content fingerprints computed by
# qwen35_scorer.compute_model_fingerprint and enforced at load time.
SCORER_DEFAULT_MODEL="qwen35-0.8b"
SCORER_SELECTION_FILE="model-selection"
typeset -A SCORER_MODEL_DIR SCORER_MODEL_SHA SCORER_MODEL_PROFILE
SCORER_MODEL_DIR=(
  [qwen35-0.8b]="models/Qwen3.5-0.8B-MLX-4bit"
  [qwen3-0.6b]="models/Qwen3-0.6B-4bit"
)
SCORER_MODEL_SHA=(
  [qwen35-0.8b]="8b1fc914a940d611e13ba1880ffdae553deb4504a0a6299256ac19470fc591b8"
  [qwen3-0.6b]="2de6c7d42ac12c447715e06bfab6497bdd49707bec990ae3cddce3a8c4ba0548"
)
SCORER_MODEL_PROFILE=(
  [qwen35-0.8b]="mohu_tiger_reranker_profile.lua"
  [qwen3-0.6b]="mohu_tiger_reranker_profile_qwen3_06b.lua"
)
