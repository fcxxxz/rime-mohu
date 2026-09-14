from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "research" / "semantic_student"
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

try:
    import torch
except ModuleNotFoundError:  # torch is an optional dev extra in this repo
    torch = None

if torch is not None:
    from model import SharedContextRanker


@unittest.skipIf(torch is None, "torch not installed")
class ProductionRankPriorTest(unittest.TestCase):
    def test_zero_residual_head_preserves_production_order_exactly(self) -> None:
        model = SharedContextRanker(
            vocab_size=32,
            d_model=64,
            context_layers=1,
            candidate_layers=1,
            cross_layers=1,
            heads=8,
            dropout=0.0,
            production_rank_prior=1.0,
            zero_residual_head=True,
        ).eval()
        context_ids = torch.tensor([[4, 5, 6, 0]], dtype=torch.long)
        candidate_ids = torch.tensor(
            [[[7, 8, 0], [9, 10, 0], [11, 12, 0], [13, 14, 0]]],
            dtype=torch.long,
        )
        candidate_mask = torch.ones(1, 4, dtype=torch.bool)
        native_rank = torch.tensor([[1, 2, 3, 4]], dtype=torch.long)
        native_score = torch.zeros(1, 4)
        has_score = torch.zeros(1, 4)
        syllable_count = torch.ones(1, 4)
        char_len = torch.full((1, 4), 2.0)

        with torch.no_grad():
            logits = model.score_menu(
                context_ids,
                candidate_ids,
                candidate_mask,
                native_rank,
                native_score,
                syllable_count,
                char_len,
                has_score,
            )

        self.assertTrue(torch.isfinite(logits).all())
        self.assertEqual([0, 1, 2, 3], torch.argsort(logits[0], descending=True).tolist())
        self.assertEqual([0.0, -1.0, -2.0, -3.0], logits[0].tolist())

    def test_missing_score_uses_explicit_presence_channel(self) -> None:
        model = SharedContextRanker(
            vocab_size=32,
            d_model=64,
            context_layers=1,
            candidate_layers=1,
            cross_layers=1,
            heads=8,
            dropout=0.0,
        ).eval()
        with torch.no_grad():
            model.structured.score_presence.weight[0].fill_(-2.0)
            model.structured.score_presence.weight[1].fill_(3.0)
        common = {
            "native_rank": torch.tensor([[1, 1]], dtype=torch.long),
            "native_score": torch.zeros(1, 2),
            "syllable_count": torch.ones(1, 2),
            "char_len": torch.ones(1, 2),
        }
        features = model.structured(
            common["native_rank"],
            common["native_score"],
            common["syllable_count"],
            common["char_len"],
            torch.tensor([[0.0, 1.0]]),
        )
        self.assertFalse(torch.equal(features[:, 0], features[:, 1]))


if __name__ == "__main__":
    unittest.main()
