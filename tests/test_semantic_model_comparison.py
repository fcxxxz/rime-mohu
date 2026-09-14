from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "research" / "semantic_student"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from compare_production_models import apply_shared_policy, evaluate_dataset


def candidate(text: str, score: float, comment: str = "") -> dict:
    return {
        "text": text,
        "comment": comment,
        "native_score": score,
        "has_score": True,
    }


class SharedProductionPolicyTest(unittest.TestCase):
    def test_protected_menu_head_remains_top1(self) -> None:
        production = [
            candidate("甲", 0.0),
            candidate("乙乙", -0.1),
            candidate("丙丙", -0.2),
        ]

        outcome = apply_shared_policy(
            production,
            model_scores=[-100.0, 0.0, 100.0],
            gate_margin=3.0,
            flip_margin=0.15,
        )

        self.assertTrue(outcome.gate_open)
        self.assertEqual(0, outcome.top_index)
        self.assertEqual("protected_head", outcome.reason)

    def test_closed_v5_gate_preserves_production_head(self) -> None:
        production = [
            candidate("甲乙", 10.0),
            candidate("丙丁", 0.0),
            candidate("戊己", -1.0),
        ]

        outcome = apply_shared_policy(
            production,
            model_scores=[-100.0, 100.0, 0.0],
            gate_margin=0.5,
            flip_margin=0.15,
        )

        self.assertFalse(outcome.gate_open)
        self.assertEqual(0, outcome.top_index)
        self.assertEqual("v5_confident", outcome.reason)

    def test_equal_model_scores_keep_original_order(self) -> None:
        production = [
            candidate("甲乙", 0.0),
            candidate("丙丁", -0.1),
            candidate("戊己", -0.2),
        ]

        outcome = apply_shared_policy(
            production,
            model_scores=[1.0, 1.0, 1.0],
            gate_margin=3.0,
            flip_margin=0.15,
        )

        self.assertTrue(outcome.gate_open)
        self.assertEqual(0, outcome.top_index)
        self.assertEqual("flat_model_scores", outcome.reason)

    def test_missing_native_score_fails_open(self) -> None:
        production = [candidate("甲乙", 0.0), candidate("丙丁", -0.1)]
        production[1]["has_score"] = False

        outcome = apply_shared_policy(
            production,
            model_scores=[-1.0, 1.0],
            gate_margin=3.0,
            flip_margin=0.15,
        )

        self.assertEqual(0, outcome.top_index)
        self.assertEqual("missing_native_score", outcome.reason)

    def test_fusion_policy_blends_model_and_native_scores(self) -> None:
        production = [
            candidate("甲乙", 3.0),
            candidate("丙丁", -3.0),
        ]

        model_only = apply_shared_policy(
            production,
            model_scores=[-1.0, 1.0],
            gate_margin=3.0,
            flip_margin=0.0,
            fusion_weight=1.0,
        )
        self.assertEqual(1, model_only.top_index)
        self.assertEqual("reranked", model_only.reason)

        # With two candidates both sides z-score to +/-1, so the 0.5 blend is
        # an exact tie; stable tie-breaking must keep the production head.
        balanced_tie = apply_shared_policy(
            production,
            model_scores=[-3.0, 3.0],
            gate_margin=3.0,
            flip_margin=0.0,
            fusion_weight=0.5,
        )
        self.assertEqual(0, balanced_tie.top_index)
        self.assertEqual("kept_head", balanced_tie.reason)

        model_dominant_blend = apply_shared_policy(
            production,
            model_scores=[-3.0, 3.0],
            gate_margin=3.0,
            flip_margin=0.0,
            fusion_weight=0.75,
        )
        self.assertEqual(1, model_dominant_blend.top_index)
        self.assertEqual("reranked", model_dominant_blend.reason)

        native_dominant = apply_shared_policy(
            production,
            model_scores=[-3.0, 3.0],
            gate_margin=3.0,
            flip_margin=0.0,
            fusion_weight=0.25,
        )
        self.assertEqual(0, native_dominant.top_index)

    def test_limit_counts_exactly_requested_menus(self) -> None:
        class FixedScorer:
            name = "fixed"

            def score(self, context: str, production: list[dict]) -> list[float]:
                return [0.0] * len(production)

            def metadata(self) -> dict:
                return {}

        records = []
        for index in range(3):
            records.append(
                {
                    "case_id": f"case-{index}",
                    "context_ok": True,
                    "context_committed": "上下文",
                    "target_text": "甲乙",
                    "production_oracle_rank": 1,
                    "production": [
                        {"menu_index": 0, **candidate("甲乙", 0.0)},
                        {"menu_index": 1, **candidate("丙丁", -0.1)},
                    ],
                }
            )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pairs.jsonl"
            path.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                encoding="utf-8",
            )
            result = evaluate_dataset(
                path,
                [FixedScorer()],
                window=10,
                gate_margin=3.0,
                flip_margin=0.15,
                bootstrap_samples=10,
                seed=7,
                limit=2,
            )

        self.assertEqual(2, result["dataset"]["evaluable_unique_menus"])
        self.assertEqual(2, result["models"]["fixed"]["menus"])


if __name__ == "__main__":
    unittest.main()
