#!/usr/bin/env python3
"""Export TinyCharLM weights to a flat binary file for C++ inference."""
import json
import struct
import sys

import torch


def main():
    if len(sys.argv) < 3:
        print("Usage: export_neural_weights.py <checkpoint.pt> <out.bin>")
        sys.exit(1)

    ckpt_path = sys.argv[1]
    out_path = sys.argv[2]

    state = torch.load(ckpt_path, map_location="cpu", weights_only=True)

    d = state["blocks.0.ln1.weight"].shape[0]
    n_layers = max(int(k.split(".")[1]) for k in state if k.startswith("blocks.")) + 1
    vocab_size = state["embed.weight"].shape[0]
    max_len = state["pos.weight"].shape[0]
    n_heads = 8
    ffn_dim = state["blocks.0.mlp.0.weight"].shape[0]

    print(f"d={d} L={n_layers} H={n_heads} V={vocab_size} max_len={max_len} ffn={ffn_dim}")

    header = struct.pack(
        "<8sIIIIIII28s",
        b"MOHU_NLM", 1, d, n_layers, n_heads, vocab_size, max_len, ffn_dim,
        b"\0" * 28,
    )

    parts = [header]
    total = 64

    def add(name, tensor):
        nonlocal total
        data = tensor.detach().cpu().float().contiguous().numpy().tobytes()
        parts.append(data)
        total += len(data)

    add("embed", state["embed.weight"])
    add("pos", state["pos.weight"])

    for li in range(n_layers):
        p = f"blocks.{li}."
        add(f"L{li}.ln1_g", state[p + "ln1.weight"])
        add(f"L{li}.ln1_b", state[p + "ln1.bias"])
        add(f"L{li}.qkv_w", state[p + "attn.in_proj_weight"])
        add(f"L{li}.qkv_b", state[p + "attn.in_proj_bias"])
        add(f"L{li}.ao_w", state[p + "attn.out_proj.weight"])
        add(f"L{li}.ao_b", state[p + "attn.out_proj.bias"])
        add(f"L{li}.ln2_g", state[p + "ln2.weight"])
        add(f"L{li}.ln2_b", state[p + "ln2.bias"])
        add(f"L{li}.ff1_w", state[p + "mlp.0.weight"])
        add(f"L{li}.ff1_b", state[p + "mlp.0.bias"])
        add(f"L{li}.ff2_w", state[p + "mlp.2.weight"])
        add(f"L{li}.ff2_b", state[p + "mlp.2.bias"])

    add("lnf_g", state["ln_f.weight"])
    add("lnf_b", state["ln_f.bias"])
    add("head_w", state["head.weight"])

    blob = b"".join(parts)
    with open(out_path, "wb") as f:
        f.write(blob)
    print(f"Wrote {out_path}: {total / 2**20:.1f} MiB, {len(blob)} bytes")


if __name__ == "__main__":
    main()
