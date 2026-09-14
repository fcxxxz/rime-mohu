"""Shared-context semantic candidate ranker (student).

Architecture per the training contract: the committed context is encoded
once per menu; each candidate (text + native segmentation evidence) is
encoded and interacts with the context through cross-attention; structured
IME features (native rank, z-scored native score, syllable count, length)
enter through embeddings/scalars. One score per candidate; training applies
a listwise softmax over the full menu.

This is a semantic reranker: it only scores candidates that already exist in
a native menu. It never generates text and never adds or removes candidates.
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

PAD, UNK, BOS, EOS = 0, 1, 2, 3
MAX_RANK_BUCKET = 64


class TransformerBlock(nn.Module):
    def __init__(self, d_model: int, heads: int, *, ffn_mult: int = 4, dropout: float = 0.1):
        super().__init__()
        self.attn = nn.MultiheadAttention(d_model, heads, dropout=dropout, batch_first=True)
        self.ffn = nn.Sequential(
            nn.Linear(d_model, d_model * ffn_mult),
            nn.GELU(),
            nn.Linear(d_model * ffn_mult, d_model),
        )
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, key_padding_mask: torch.Tensor | None = None) -> torch.Tensor:
        residual = x
        x = self.norm1(x)
        x, _ = self.attn(x, x, x, key_padding_mask=key_padding_mask, need_weights=False)
        x = residual + self.dropout(x)
        x = x + self.dropout(self.ffn(self.norm2(x)))
        return x


class CrossAttentionBlock(nn.Module):
    def __init__(self, d_model: int, heads: int, *, dropout: float = 0.1):
        super().__init__()
        self.attn = nn.MultiheadAttention(d_model, heads, dropout=dropout, batch_first=True)
        self.norm_q = nn.LayerNorm(d_model)
        self.norm_kv = nn.LayerNorm(d_model)
        self.ffn = nn.Sequential(
            nn.Linear(d_model, d_model * 4), nn.GELU(), nn.Linear(d_model * 4, d_model)
        )
        self.norm2 = nn.LayerNorm(d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, q: torch.Tensor, kv: torch.Tensor,
                kv_padding_mask: torch.Tensor | None = None) -> torch.Tensor:
        residual = q
        x, _ = self.attn(self.norm_q(q), self.norm_kv(kv), self.norm_kv(kv),
                         key_padding_mask=kv_padding_mask, need_weights=False)
        x = residual + self.dropout(x)
        x = x + self.dropout(self.ffn(self.norm2(x)))
        return x


class StructuredFeatures(nn.Module):
    """Native rank/score, syllable count and length evidence per candidate.

    Production menus mix native decode candidates (real score) with
    fixed/smart table candidates (no decode score). The score-presence flag
    routes between the score MLP and a learned unknown-score embedding, so
    missing evidence is an explicit signal, never a silent zero.
    """

    def __init__(self, d_model: int):
        super().__init__()
        self.rank_embedding = nn.Embedding(MAX_RANK_BUCKET + 1, d_model)
        self.score_mlp = nn.Sequential(nn.Linear(1, d_model // 2), nn.GELU(), nn.Linear(d_model // 2, d_model))
        self.score_presence = nn.Embedding(2, d_model)
        self.count_mlp = nn.Sequential(nn.Linear(2, d_model // 2), nn.GELU(), nn.Linear(d_model // 2, d_model))

    def forward(self, native_rank: torch.Tensor, native_score: torch.Tensor,
                syllable_count: torch.Tensor, char_len: torch.Tensor,
                has_score: torch.Tensor | None = None) -> torch.Tensor:
        rank = self.rank_embedding(native_rank.clamp(1, MAX_RANK_BUCKET))
        if has_score is None:
            has_score = torch.ones_like(native_score)
        presence = self.score_presence(has_score.long().clamp(0, 1))
        scored = self.score_mlp((native_score * has_score).unsqueeze(-1))
        counts = self.count_mlp(torch.stack([syllable_count.float(), char_len.float()], dim=-1))
        return rank + presence + scored + counts


class SharedContextRanker(nn.Module):
    def __init__(self, vocab_size: int, *, d_model: int = 512, context_layers: int = 6,
                 candidate_layers: int = 4, cross_layers: int = 2, heads: int = 8,
                 dropout: float = 0.1, score_scale: float = 8.0,
                 production_rank_prior: float = 0.0,
                 zero_residual_head: bool = False):
        super().__init__()
        self.d_model = d_model
        self.embedding = nn.Embedding(vocab_size, d_model, padding_idx=PAD)
        self.position = nn.Embedding(512, d_model)
        self.context_encoder = nn.ModuleList(
            TransformerBlock(d_model, heads, dropout=dropout) for _ in range(context_layers)
        )
        self.candidate_encoder = nn.ModuleList(
            TransformerBlock(d_model, heads, dropout=dropout) for _ in range(candidate_layers)
        )
        self.cross = nn.ModuleList(
            CrossAttentionBlock(d_model, heads, dropout=dropout) for _ in range(cross_layers)
        )
        self.structured = StructuredFeatures(d_model)
        self.head = nn.Sequential(
            nn.LayerNorm(d_model),
            nn.Linear(d_model, d_model),
            nn.GELU(),
            nn.Linear(d_model, 1),
        )
        self.score_scale = score_scale
        self.production_rank_prior = production_rank_prior
        nn.init.normal_(self.structured.rank_embedding.weight, std=0.02)
        nn.init.normal_(self.embedding.weight, std=0.02)
        with torch.no_grad():
            self.embedding.weight[PAD].zero_()
        if zero_residual_head:
            # Production-shaped training starts at exact native menu order:
            # residual logits are initially zero, so only learned evidence can
            # overcome the fixed rank prior.
            output = self.head[-1]
            nn.init.zeros_(output.weight)
            nn.init.zeros_(output.bias)

    def _embed(self, ids: torch.Tensor) -> torch.Tensor:
        positions = torch.arange(ids.shape[1], device=ids.device)
        x = self.embedding(ids) * math.sqrt(self.d_model) + self.position(positions)
        return x * (ids != PAD).unsqueeze(-1)

    def encode_context(self, context_ids: torch.Tensor) -> torch.Tensor:
        """context_ids: (B, T) padded on the right. Returns (B, T, D)."""
        x = self._embed(context_ids)
        padding = context_ids == PAD
        for layer in self.context_encoder:
            x = layer(x, key_padding_mask=padding)
        return x

    def score_menu(self, context_ids: torch.Tensor, candidate_ids: torch.Tensor,
                   candidate_mask: torch.Tensor, native_rank: torch.Tensor,
                   native_score: torch.Tensor, syllable_count: torch.Tensor,
                   char_len: torch.Tensor, has_score: torch.Tensor | None = None) -> torch.Tensor:
        """Score a full menu.

        context_ids: (B, T); candidate_ids: (B, C, L) right-padded;
        candidate_mask: (B, C) True for real candidates; features: (B, C);
        has_score: (B, C) 1 where native_score is a real decode score.
        Returns (B, C) scores with padding set to -inf.
        """
        batch, n_candidates, length = candidate_ids.shape
        context = self.encode_context(context_ids)
        context_padding = context_ids == PAD

        flat = self._embed(candidate_ids.reshape(batch * n_candidates, length))
        cand_padding = (candidate_ids.reshape(batch * n_candidates, length) == PAD)
        for layer in self.candidate_encoder:
            flat = layer(flat, key_padding_mask=cand_padding)
        # mean-pool real tokens as the candidate summary vector
        token_mask = (~cand_padding).float().unsqueeze(-1)
        summary = (flat * token_mask).sum(dim=1) / token_mask.sum(dim=1).clamp(min=1.0)
        summary = summary.reshape(batch, n_candidates, self.d_model)

        for layer in self.cross:
            summary = layer(summary, context, kv_padding_mask=context_padding)

        features = self.structured(native_rank, native_score, syllable_count, char_len, has_score)
        residual_logits = self.head(summary + features).squeeze(-1) * self.score_scale
        rank_prior = -(native_rank.float() - 1.0) * self.production_rank_prior
        logits = residual_logits + rank_prior
        return logits.masked_fill(~candidate_mask, float("-inf"))

    def forward(self, batch: dict) -> tuple[torch.Tensor, torch.Tensor]:
        """Return (logits, log_prob) over a menu with its target index."""

        logits = self.score_menu(
            batch["context_ids"], batch["candidate_ids"], batch["candidate_mask"],
            batch["native_rank"], batch["native_score"], batch["syllable_count"],
            batch["char_len"], batch.get("has_score"),
        )
        return logits, F.log_softmax(logits, dim=-1)


def count_parameters(model: nn.Module) -> int:
    return sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
