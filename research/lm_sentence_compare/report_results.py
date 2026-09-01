#!/usr/bin/env python3
"""Aggregate candidate dumps into auditable accuracy tables."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Mapping, Sequence

from .metrics import (
    edit_distance,
    evaluate_cases,
    paired_bootstrap_delta,
    parse_rime_dump,
    parse_tiger_dump,
)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MODES = ("pure", "sparse", "word1", "char1")
MODE_LABELS = {
    "pure": "纯双拼",
    "sparse": "少量辅助码（每四词首字一辅）",
    "word1": "每词一辅",
    "char1": "每字一辅",
}
MODEL_LABELS = {"bgw": "八股文 bgw", "wx": "万象 LTS", "tiger": "虎码 TCSKNM"}
CANONICAL_CASE_COUNT = 20_000
CANONICAL_SOURCE_COUNTS = {"news": 10_000, "daily": 10_000}
CANONICAL_CASES_SHA256 = "da0887f98bb72d66536e82f5438fe03cd5659dbfc77fbf9aa8bfe421825c7205"
RAW_RESULT_FILES = tuple(
    [f"rime_{model}_{mode}.tsv" for model in ("bgw", "wx") for mode in MODES]
    + [f"tiger_{mode}.tsv" for mode in MODES]
    + [f"tiger_{mode}.latency.tsv" for mode in MODES]
)


def load_cases(
    path: str | Path, *, require_canonical: bool = True
) -> list[dict[str, object]]:
    source_path = Path(path)
    cases: list[dict[str, object]] = []
    ids: set[str] = set()
    raws_by_mode: dict[str, set[str]] = {mode: set() for mode in MODES}
    with source_path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            case_id = str(row.get("id", ""))
            if not case_id or case_id in ids:
                raise ValueError(f"invalid or duplicate case id at line {line_number}")
            modes = row.get("modes")
            if not isinstance(modes, Mapping) or any(not modes.get(mode) for mode in MODES):
                raise ValueError(f"case {case_id} lacks all input modes")
            for mode in MODES:
                raw = modes[mode]
                if not isinstance(raw, str) or not raw:
                    raise ValueError(f"case {case_id} has invalid raw for mode {mode}")
                if raw in raws_by_mode[mode]:
                    raise ValueError(
                        f"duplicate raw for mode {mode} at line {line_number}"
                    )
                raws_by_mode[mode].add(raw)
            ids.add(case_id)
            cases.append(row)
    if not cases:
        raise ValueError(f"no cases in {path}")
    if require_canonical:
        if len(cases) != CANONICAL_CASE_COUNT:
            raise ValueError(
                f"canonical benchmark requires {CANONICAL_CASE_COUNT} cases, "
                f"got {len(cases)}"
            )
        source_counts = Counter(str(case.get("source", "")) for case in cases)
        if source_counts != CANONICAL_SOURCE_COUNTS:
            raise ValueError(
                "canonical benchmark source quotas mismatch: "
                f"expected {CANONICAL_SOURCE_COUNTS}, got {dict(source_counts)}"
            )
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if digest != CANONICAL_CASES_SHA256:
            raise ValueError(
                "canonical benchmark cases SHA-256 mismatch: "
                f"expected {CANONICAL_CASES_SHA256}, got {digest}"
            )
    return cases


def validate_artifact_hashes(
    results_dir: str | Path,
    manifest_path: str | Path,
    *,
    required_files: Sequence[str] = RAW_RESULT_FILES,
) -> None:
    root = validate_results_root(Path(results_dir))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    try:
        files = manifest["outputs"]["external_raw"]["files"]
    except (KeyError, TypeError) as exc:
        raise ValueError("artifact manifest lacks external raw file entries") from exc
    for name in required_files:
        metadata = files.get(name)
        if not isinstance(metadata, Mapping):
            raise ValueError(f"artifact manifest lacks {name}")
        path = (root / name).resolve()
        if root not in path.parents:
            raise ValueError(f"artifact path escapes results directory: {name}")
        if not path.is_file():
            raise FileNotFoundError(path)
        actual_size = path.stat().st_size
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_size != int(metadata.get("bytes", -1)):
            raise ValueError(
                f"artifact size mismatch for {name}: "
                f"expected {metadata.get('bytes')}, got {actual_size}"
            )
        if actual_hash != str(metadata.get("sha256", "")):
            raise ValueError(f"artifact SHA-256 mismatch for {name}")


def validate_results_root(path: Path) -> Path:
    lexical = path.expanduser().absolute()
    allowed_system_aliases = {
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var"),
        Path("/var/tmp"),
    }
    current = lexical
    while True:
        if current.is_symlink() and current not in allowed_system_aliases:
            raise ValueError(f"refusing unsafe results root: symlinked ancestor {current}")
        if current == current.parent:
            break
        current = current.parent
    root = lexical.resolve()
    forbidden = {
        Path("/").resolve(),
        Path.home().resolve(),
        Path("/tmp").resolve(),
        Path("/private/tmp").resolve(),
        Path("/var/tmp").resolve(),
        REPO.resolve(),
    }
    live_rime = (Path.home() / "Library" / "Rime").resolve()
    if lexical.is_symlink() or root in forbidden or root == live_rime or live_rime in root.parents:
        raise ValueError(f"refusing unsafe results root: {root}")
    if root.exists() and not root.is_dir():
        raise ValueError(f"results root is not a directory: {root}")
    return root


def validate_output_file(path: Path, root: Path) -> Path:
    """Reject report targets that could follow a pre-existing symlink."""

    lexical = path.expanduser().absolute()
    if lexical.is_symlink():
        raise ValueError(f"refusing symlinked report file: {lexical}")
    if lexical.resolve().parent != root:
        raise ValueError(f"refusing unsafe report file: {lexical}")
    if lexical.exists() and not lexical.is_file():
        raise ValueError(f"report target is not a file: {lexical}")
    return lexical


def load_results(results_dir: str | Path, *, models: Sequence[str] = ("bgw", "wx", "tiger")) -> dict[str, dict[str, dict[str, object]]]:
    root = validate_results_root(Path(results_dir))
    output: dict[str, dict[str, dict[str, object]]] = {}
    for model in models:
        if model not in MODEL_LABELS:
            raise ValueError(f"unknown model {model!r}")
        output[model] = {}
        for mode in MODES:
            if model == "tiger":
                path = root / f"tiger_{mode}.tsv"
                latency = root / f"tiger_{mode}.latency.tsv"
                if not path.is_file():
                    raise FileNotFoundError(path)
                if not latency.is_file():
                    raise FileNotFoundError(f"missing Tiger latency file: {latency}")
                output[model][mode] = parse_tiger_dump(path, latency_path=latency)
            else:
                path = root / f"rime_{model}_{mode}.tsv"
                if not path.is_file():
                    raise FileNotFoundError(path)
                output[model][mode] = parse_rime_dump(path)
    if not output:
        raise ValueError(f"no result dumps in {root}")
    return output


def _slice_rows(rows: Sequence[Mapping[str, object]], *, source: str | None = None, label: str | None = None, length_band: tuple[int, int] | None = None) -> list[Mapping[str, object]]:
    result = []
    for row in rows:
        if source is not None and row["source"] != source:
            continue
        if label is not None and row["label"] != label:
            continue
        if length_band is not None and not (length_band[0] <= len(str(row["text"])) <= length_band[1]):
            continue
        result.append(row)
    return result


def _row_stat(rows: Sequence[Mapping[str, object]]) -> dict[str, float | int]:
    n = len(rows)
    if n == 0:
        return {"n": 0, "top1": 0, "top5": 0, "top10": 0, "top20": 0, "coverage": 0, "char_accuracy": 0.0, "mrr": 0.0}
    return {
        "n": n,
        "top1": sum(row["rank"] == 1 for row in rows),
        "top5": sum(0 < int(row["rank"]) <= 5 for row in rows),
        "top10": sum(0 < int(row["rank"]) <= 10 for row in rows),
        "top20": sum(0 < int(row["rank"]) <= 20 for row in rows),
        "coverage": sum(int(row["rank"]) > 0 for row in rows),
        "mrr": sum(1.0 / int(row["rank"]) if int(row["rank"]) > 0 else 0.0 for row in rows) / n,
        "char_accuracy": sum(
            1.0 - edit_distance(str(row["top1"]), str(row["text"]))
            / max(len(str(row["top1"])), len(str(row["text"])), 1)
            for row in rows
        ) / n,
    }


def _pct(value: int | float, denominator: int) -> str:
    return f"{100 * value / denominator:.2f}%" if denominator else "-"


def _validate_result_matrix(
    cases: Sequence[Mapping[str, object]],
    results: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> None:
    required_models = set(MODEL_LABELS)
    actual_models = set(results)
    missing_models = required_models - actual_models
    unexpected_models = actual_models - required_models
    if missing_models:
        raise ValueError(f"missing models: {', '.join(sorted(missing_models))}")
    if unexpected_models:
        raise ValueError(f"unexpected models: {', '.join(sorted(unexpected_models))}")
    expected_raws = {
        mode: {str(case["modes"][mode]) for case in cases}
        for mode in MODES
    }
    for model in MODEL_LABELS:
        mode_maps = results[model]
        missing_modes = set(MODES) - set(mode_maps)
        unexpected_modes = set(mode_maps) - set(MODES)
        if missing_modes:
            raise ValueError(
                f"missing modes for {model}: {', '.join(sorted(missing_modes))}"
            )
        if unexpected_modes:
            raise ValueError(
                f"unexpected modes for {model}: {', '.join(sorted(unexpected_modes))}"
            )
        for mode in MODES:
            actual_raws = set(mode_maps[mode])
            if actual_raws != expected_raws[mode]:
                raise ValueError(
                    f"raw set mismatch for {model}/{mode}: "
                    f"expected {len(expected_raws[mode])}, got {len(actual_raws)}"
                )
            for raw, value in mode_maps[mode].items():
                if isinstance(value, Mapping):
                    candidates = value.get("candidates", ())
                    status = value.get("status", "ok" if candidates else "empty")
                    error = value.get("error")
                else:
                    candidates = getattr(value, "candidates", ())
                    status = getattr(value, "status", "ok" if candidates else "empty")
                    error = getattr(value, "error", None)
                if status not in {"ok", "empty"} or (status == "ok" and error) or (
                    status == "ok" and not candidates
                ) or (status == "empty" and candidates):
                    raise ValueError(
                        f"invalid result status/candidate mismatch for {model}/{mode}/{raw}: "
                        f"status={status!r}, error={error!r}"
                    )


def render_report(cases: Sequence[dict[str, object]], results: Mapping[str, Mapping[str, Mapping[str, object]]], out_dir: str | Path) -> dict[str, object]:
    _validate_result_matrix(cases, results)
    rows, summary = evaluate_cases(cases, results)
    out = validate_results_root(Path(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    output_paths = {
        name: validate_output_file(out / name, out)
        for name in ("results.jsonl", "summary.json", "summary.csv", "summary.md")
    }
    with output_paths["results.jsonl"].open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    # Add stable source/domain/length slices using the already joined rows.
    slices: dict[str, dict[str, dict[str, dict[str, float | int]]]] = defaultdict(dict)
    for model in results:
        for mode in results[model]:
            selected = [row for row in rows if row["model"] == model and row["mode"] == mode]
            slices[model][mode] = {
                "all": _row_stat(selected),
                "news": _row_stat(_slice_rows(selected, source="news")),
                "daily": _row_stat(_slice_rows(selected, source="daily")),
                "short_6_9": _row_stat(_slice_rows(selected, length_band=(6, 9))),
                "medium_10_14": _row_stat(_slice_rows(selected, length_band=(10, 14))),
                "long_15_24": _row_stat(_slice_rows(selected, length_band=(15, 24))),
            }
            labels = sorted({str(row["label"]) for row in selected if str(row["source"]) == "news"})
            for label in labels:
                slices[model][mode][f"label:{label}"] = _row_stat(_slice_rows(selected, label=label))

    bootstrap: dict[str, dict[str, object]] = {}
    common_coverage_bootstrap: dict[str, dict[str, object]] = {}
    if "bgw" in results:
        for challenger in results:
            if challenger in {"bgw"}:
                continue
            for mode in MODES:
                base_rows = {str(row["id"]): row for row in rows if row["model"] == "bgw" and row["mode"] == mode}
                new_rows = {str(row["id"]): row for row in rows if row["model"] == challenger and row["mode"] == mode}
                common = sorted(set(base_rows) & set(new_rows))
                if common:
                    bootstrap[f"{challenger}_vs_bgw/{mode}"] = paired_bootstrap_delta(
                        [int(base_rows[key]["rank"]) == 1 for key in common],
                        [int(new_rows[key]["rank"]) == 1 for key in common],
                    )
                    covered = [
                        key
                        for key in common
                        if int(base_rows[key]["rank"]) > 0
                        and int(new_rows[key]["rank"]) > 0
                    ]
                    if covered:
                        common_coverage_bootstrap[
                            f"{challenger}_vs_bgw/{mode}"
                        ] = paired_bootstrap_delta(
                            [int(base_rows[key]["rank"]) == 1 for key in covered],
                            [int(new_rows[key]["rank"]) == 1 for key in covered],
                        )
                        common_coverage_bootstrap[
                            f"{challenger}_vs_bgw/{mode}"
                        ]["baseline_top1"] = sum(
                            int(base_rows[key]["rank"]) == 1 for key in covered
                        ) / len(covered)
                        common_coverage_bootstrap[
                            f"{challenger}_vs_bgw/{mode}"
                        ]["challenger_top1"] = sum(
                            int(new_rows[key]["rank"]) == 1 for key in covered
                        ) / len(covered)

    input_profile: dict[str, dict[str, float | int]] = {}
    for mode in MODES:
        counts = [int(c.get("aux_counts", {}).get(mode, 0)) for c in cases if isinstance(c.get("aux_counts"), Mapping)]
        ratios = [float(c.get("aux_ratios", {}).get(mode, 0.0)) for c in cases if isinstance(c.get("aux_ratios"), Mapping)]
        input_profile[mode] = {
            "mean_aux_chars": mean(counts) if counts else 0.0,
            "mean_aux_ratio": mean(ratios) if ratios else 0.0,
        }

    report = {
        "summary": summary,
        "slices": dict(slices),
        "bootstrap": bootstrap,
        "bootstrap_common_coverage": common_coverage_bootstrap,
        "input_profile": input_profile,
    }
    output_paths["summary.json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    csv_fields = ["model", "mode", "n", "top1", "top1_ci_low", "top1_ci_high", "top5", "top10", "top20", "coverage", "mrr", "char_accuracy", "missing", "empty", "errors", "latency_mean_us", "latency_p50_us", "latency_p95_us", "latency_p99_us"]
    with output_paths["summary.csv"].open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=csv_fields)
        writer.writeheader()
        for model, mode_stats in summary.items():
            for mode, stat in mode_stats.items():
                writer.writerow({"model": model, "mode": mode, **stat})

    lines = [
        "# 三模型整句输入基准",
        "",
        f"样本数：{len(cases)}（新闻 {sum(str(c.get('source')) == 'news' for c in cases)}，日常 {sum(str(c.get('source')) == 'daily' for c in cases)}）",
        "",
        "主表中的 top-k 是目标句在最多 20 个候选中的命中；coverage 与排序准确率分开统计。",
        "",
        "延迟列只用于记录各自探针的运行成本：Rime 包含清空组合、遍历候选和写出，Tiger 只记录 native `tiger_decode`；两者不是同一测量边界，不能直接作用户端响应时间排名。",
        "",
        "| 模型 | 输入条件 | n | Top-1 (95% CI) | Top-5 | Top-10 | Top-20 | Coverage | MRR | 字符准确率 | 空候选 | 错误 | P50(us) | P95(us) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model, mode_stats in summary.items():
        for mode, stat in mode_stats.items():
            n = int(stat["n"])
            lines.append(
                f"| {MODEL_LABELS.get(model, model)} | {MODE_LABELS.get(mode, mode)} | {n} | "
                f"{_pct(int(stat['top1']), n)} [{100*float(stat['top1_ci_low']):.2f}%, {100*float(stat['top1_ci_high']):.2f}%] | {_pct(int(stat['top5']), n)} | "
                f"{_pct(int(stat['top10']), n)} | {_pct(int(stat['top20']), n)} | "
                f"{_pct(int(stat['coverage']), n)} | {100*float(stat['mrr']):.2f}% | {100*float(stat['char_accuracy']):.2f}% | "
                f"{stat['empty']} | {stat['errors']} | "
                f"{_fmt_num(stat['latency_p50_us'])} | {_fmt_num(stat['latency_p95_us'])} |"
            )
    lines += ["", "## 来源切片（Top-1）", "", "| 模型 | 模式 | 新闻 | 日常 |", "|---|---|---:|---:|"]
    for model, mode_stats in slices.items():
        for mode, stat in mode_stats.items():
            lines.append(
                f"| {MODEL_LABELS.get(model, model)} | {MODE_LABELS.get(mode, mode)} | "
                f"{_pct(int(stat['news']['top1']), int(stat['news']['n']))} ({stat['news']['n']}) | "
                f"{_pct(int(stat['daily']['top1']), int(stat['daily']['n']))} ({stat['daily']['n']}) |"
            )
    lines += ["", "## 新闻领域切片（Top-1）", ""]
    news_labels = sorted({str(c.get("label")) for c in cases if str(c.get("source")) == "news"})
    for mode in MODES:
        lines += [f"### {MODE_LABELS[mode]}", "", "| 领域 | 八股文 | 万象 | Tiger |", "|---|---:|---:|---:|"]
        for label in news_labels:
            cells = []
            for model in ("bgw", "wx", "tiger"):
                picked = [row for row in rows if row["model"] == model and row["mode"] == mode and row["label"] == label]
                stat = _row_stat(picked)
                cells.append(f"{_pct(int(stat['top1']), int(stat['n']))} ({stat['n']})")
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        lines.append("")

    lines += ["## 句长切片（Top-1）", "", "| 模式 | 6–9 | 10–14 | 15–24 |", "|---|---:|---:|---:|"]
    length_bands = ((6, 9), (10, 14), (15, 24))
    for model in ("bgw", "wx", "tiger"):
        for mode in MODES:
            cells = []
            for band in length_bands:
                picked = [row for row in rows if row["model"] == model and row["mode"] == mode and band[0] <= len(str(row["text"])) <= band[1]]
                stat = _row_stat(picked)
                cells.append(f"{_pct(int(stat['top1']), int(stat['n']))} ({stat['n']})")
            lines.append(f"| {MODEL_LABELS.get(model, model)} / {MODE_LABELS[mode]} | " + " | ".join(cells) + " |")

    lines += ["", "## 辅助码密度", "", "| 模式 | 平均辅助字符数 | 平均占比 |", "|---|---:|---:|"]
    for mode in MODES:
        profile = input_profile[mode]
        lines.append(f"| {MODE_LABELS[mode]} | {profile['mean_aux_chars']:.2f} | {100*float(profile['mean_aux_ratio']):.2f}% |")

    lines += ["", "## 错误样例", "", "| 模型 | 模式 | 目标 | 首选 | 目标排名 | 状态 |", "|---|---|---|---|---:|---|"]
    for model in results:
        for mode in results[model]:
            wrong = [row for row in rows if row["model"] == model and row["mode"] == mode and row["rank"] != 1][:3]
            for row in wrong:
                lines.append(f"| {MODEL_LABELS.get(model, model)} | {MODE_LABELS.get(mode, mode)} | {row['text']} | {row['top1'] or '（空）'} | {row['rank']} | {row['status']} |")

    lines += ["", "## 配对差异（相对八股文 bgw）", "", "| 对比 | 观察差 | 95% bootstrap 区间 |", "|---|---:|---:|"]
    for key, value in bootstrap.items():
        lines.append(f"| {key} | {100*float(value['observed_delta']):+.2f}pp | [{100*float(value['ci_low']):+.2f}, {100*float(value['ci_high']):+.2f}]pp |")
    lines += [
        "",
        "## 共同覆盖子集的配对 Top-1 差异",
        "",
        "仅保留两个模型都把目标句放入候选池的句子。这是带选择偏差的条件统计，不是把 coverage 影响无偏剥离；两边仍不是完全相同的候选晶格。",
        "",
        "| 对比 | 共同覆盖 n | BGW 条件 Top-1 | 对比模型条件 Top-1 | 差值 | 95% bootstrap 区间 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, value in common_coverage_bootstrap.items():
        lines.append(
            f"| {key} | {int(value['n'])} | "
            f"{100*float(value['baseline_top1']):.2f}% | "
            f"{100*float(value['challenger_top1']):.2f}% | "
            f"{100*float(value['observed_delta']):+.2f}pp | "
            f"[{100*float(value['ci_low']):+.2f}, {100*float(value['ci_high']):+.2f}]pp |"
        )
    output_paths["summary.md"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _fmt_num(value: object) -> str:
    if value is None:
        return "-"
    return f"{float(value):.0f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate sentence model result dumps")
    parser.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    parser.add_argument("--results", type=Path, default=HERE / "results")
    parser.add_argument("--out", type=Path, default=HERE / "results")
    parser.add_argument("--artifact-manifest", type=Path)
    parser.add_argument("--skip-artifact-hash-check", action="store_true")
    parser.add_argument("--allow-noncanonical-cases", action="store_true")
    args = parser.parse_args()
    cases = load_cases(
        args.cases, require_canonical=not args.allow_noncanonical_cases
    )
    if args.artifact_manifest is not None and not args.skip_artifact_hash_check:
        validate_artifact_hashes(args.results, args.artifact_manifest)
    results = load_results(args.results)
    report = render_report(cases, results, args.out)
    print(json.dumps({"cases": len(cases), "models": sorted(results), "out": str(args.out), "bootstrap": len(report["bootstrap"])}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
