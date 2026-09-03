from __future__ import annotations

import binascii
import json
import tempfile
import unittest
from pathlib import Path

from research.lm_sentence_compare.cross_candidate import (
    CrossCase,
    CrossRow,
    ProbeResult,
    SCHEME_SPECS,
    SCHEMES,
    _rank,
    _word_rank,
    aggregate_context,
    auxiliary_remediation,
    build_context_rows,
    build_report,
    build_rows,
    full_rankings,
    load_after,
    load_cross_cases,
    load_fresh,
    rank_metric,
    render_markdown,
    summarize_rows,
)


def hx(value: str) -> str:
    return binascii.hexlify(value.encode()).decode()


def write_stream(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def probe_row(
    case_id: str,
    mode: str,
    text: str,
    rank: int,
    *,
    scheme: str = "mohu_zrm",
    prefix_ok: bool | None = None,
    context_state: str = "not_applicable",
    candidate_state: str = "target_covered",
) -> CrossRow:
    candidates = (text,) if rank == 1 else (("别的", text) if rank == 2 else ("别的",))
    return CrossRow(
        case_id,
        scheme,
        mode,
        text,
        prefix_ok,
        ProbeResult(candidates, False, "ok"),
        candidates[0],
        rank,
        candidate_state,
        context_state,
    )


class CrossCandidateTest(unittest.TestCase):
    def test_scheme_specs_use_correct_raw_conditions_and_inputs(self) -> None:
        self.assertEqual(SCHEME_SPECS["moran"]["condition"], "moranmain2")
        self.assertEqual(
            SCHEME_SPECS["moran"]["fresh_glob"],
            "moranmain2-moran.fresh.*.tsv",
        )
        self.assertEqual(SCHEME_SPECS["moran"]["input_name"], "moran.fresh.tsv")
        self.assertEqual(
            SCHEMES,
            ("moran", "yeying", "wxpro", "mohu_zrm", "mohu_flypy"),
        )

    def test_moran_fresh_loader_matches_corrected_main_shards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_stream(
                root / "out/fresh/moranmain2-moran.fresh.00.tsv",
                ["E\ti0\tpure\t0\t0\t1"],
            )
            write_stream(
                root / "out/fresh/moran-moran.fresh.00.tsv",
                ["E\tstale\tpure\t0\t0\t1"],
            )
            results = load_fresh(root, "moran")
            self.assertEqual(set(results), {("i0", "pure")})

    def test_after_parser_keeps_prefix_failures_and_empty_menus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_stream(
                root / "out/afterA/demo/demo.afterA.00.tsv",
                [
                    "A\ti0\t0\t-1",
                    "E\ti0\tpure\t0\t0\t1",
                    "A\ti1\t1\t0",
                    "E\ti1\tpure\t0\t0\t2",
                    "E\ti1\thead\t0\t0\t3",
                ],
            )
            prefix, menus = load_after(root, "demo")
            self.assertEqual(prefix, {"i0": False, "i1": True})
            self.assertEqual(menus[("i0", "pure")].status, "empty")
            self.assertEqual(menus[("i1", "pure")].status, "empty")

    def test_after_parser_rejects_duplicate_prefix_and_bad_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "out/afterA/demo/demo.afterA.00.tsv"
            write_stream(path, ["A\ti0\t1\t0", "A\ti0\t1\t0"])
            with self.assertRaisesRegex(ValueError, "duplicate A row"):
                load_after(root, "demo")
            write_stream(path, ["A\ti0\t1\t0", "E\ti0\tpure\t0\t2\t1"])
            with self.assertRaisesRegex(ValueError, "truncation"):
                load_after(root, "demo")

    def test_load_cases_rejects_metadata_target_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_stream(
                root / "in/moran.fresh.tsv",
                ["B\ti0\tpure\thead\ttail\tboth\t" + hx("目标") + "\t0\t目标"],
            )
            root.joinpath("meta.json").write_text(
                json.dumps({"i0": {"word": "别词", "prefix": "前缀"}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "metadata target mismatch"):
                load_cross_cases(root, input_name="moran.fresh.tsv")

    def test_summary_distinguishes_states_and_reports_word_equal_top1(self) -> None:
        rows = [
            probe_row("a0", "pure", "甲词", 1),
            probe_row("a1", "pure", "甲词", 0, candidate_state="target_absent_exported_topN"),
            probe_row("b0", "pure", "乙词", 1),
        ]
        rows[1] = CrossRow(
            rows[1].case_id,
            rows[1].scheme,
            rows[1].mode,
            rows[1].text,
            rows[1].prefix_ok,
            ProbeResult(("别的",), True, "ok"),
            "别的",
            0,
            "target_absent_exported_topN",
            rows[1].context_state,
        )
        stat = summarize_rows(rows)
        self.assertEqual(stat["n"], 3)
        self.assertEqual(stat["top1"], 2)
        self.assertEqual(stat["target_absent_exported_topN"], 1)
        self.assertAlmostEqual(stat["top1_rate"], 2 / 3)
        self.assertAlmostEqual(stat["word_equal_top1_rate"], 0.75)

    def test_auxiliary_remediation_uses_same_case_ids_and_exclusive_buckets(self) -> None:
        def row(case_id: str, mode: str, rank: int, state: str = "target_covered"):
            return type(
                "Row",
                (),
                {"case_id": case_id, "mode": mode, "rank": rank, "candidate_state": state},
            )()

        rows = [
            row("a", "pure", 2), row("a", "head", 1), row("a", "tail", 2), row("a", "both", 1),
            row("b", "pure", 0, "target_absent_exported_topN"), row("b", "head", 2), row("b", "tail", 1), row("b", "both", 2),
            row("c", "pure", 1), row("c", "head", 1), row("c", "tail", 1), row("c", "both", 1),
        ]
        result = auxiliary_remediation(rows)
        self.assertEqual(result["modes"]["head"]["wrong_to_aux_top1"], 1)
        self.assertEqual(result["modes"]["tail"]["wrong_to_aux_top1"], 1)
        self.assertEqual(result["modes"]["both"]["wrong_to_aux_top1"], 1)
        self.assertEqual(result["exclusive_total"], 2)
        self.assertEqual(sum(result["exclusive_top1_rescue"].values()), 2)

    def test_context_aggregation_reports_case_and_word_equal_metrics(self) -> None:
        fresh = [
            probe_row("a0", "pure", "甲词", 2),
            probe_row("a1", "pure", "甲词", 2),
            probe_row("b0", "pure", "乙词", 2),
        ]
        context = [
            probe_row("a0", "pure", "甲词", 1, prefix_ok=True, context_state="available"),
            probe_row("a1", "pure", "甲词", 2, prefix_ok=True, context_state="available"),
            probe_row("b0", "pure", "乙词", 1, prefix_ok=True, context_state="available"),
        ]
        stat = aggregate_context(fresh, context)
        self.assertAlmostEqual(stat["fixed_rate"], 2 / 3)
        self.assertAlmostEqual(stat["word_equal_fixed_rate"], 0.75)
        self.assertAlmostEqual(stat["word_equal_context_top1_rate"], 0.75)

    def test_rankings_are_complete_and_ties_share_rank(self) -> None:
        ranked = rank_metric(
            {
                "moran": 0.5,
                "yeying": 0.5,
                "wxpro": 0.2,
                "mohu_zrm": 0.8,
                "mohu_flypy": 0.1,
            }
        )
        self.assertEqual([item["rank"] for item in ranked], [1, 2, 2, 4, 5])
        summary = {
            scheme: {
                mode: {
                    "prefix_available_rate": 0.5,
                    "candidate_nonempty_rate": 0.5,
                    "target_covered_rate": 0.5,
                    "top1_rate": 0.5,
                    "word_equal_top1_rate": 0.5,
                    "context": {
                        "context_top1": 1,
                        "n_available": 2,
                        "context_lift": 0.0,
                        "fixed_rate": 0.0,
                        "word_equal_context_top1_rate": 0.5,
                        "word_equal_fixed_rate": 0.0,
                        "broken_rate": 0.0,
                        "word_equal_broken_rate": 0.0,
                    },
                }
                for mode in ("pure", "head", "tail", "both")
            }
            for scheme in SCHEMES
        }
        rankings = full_rankings(summary)
        self.assertEqual(len(rankings), 48)
        self.assertTrue(all(len(items) == len(SCHEMES) for items in rankings.values()))
        self.assertIn("纯双拼 / 直接第一候选命中率", rankings)

        auxiliary = {
            scheme: {
                "base_mode": "pure",
                "modes": {
                    mode: {
                        "base_top1_error": 1,
                        "wrong_to_aux_top1_rate": 0.5,
                        "base_export_absent": 1,
                        "absent_to_aux_covered_rate": 0.5,
                        "absent_to_aux_top1_rate": 0.5,
                    }
                    for mode in ("head", "tail", "both")
                },
            }
            for scheme in SCHEMES
        }
        rankings_with_auxiliary = full_rankings(summary, auxiliary)
        self.assertEqual(len(rankings_with_auxiliary), 57)
        self.assertIn(
            "首末辅 / 纯双拼未进已导出池后辅码首选正确率",
            rankings_with_auxiliary,
        )

        auxiliary["wxpro"]["modes"]["head"]["base_export_absent"] = 0
        visibility_ranking = full_rankings(summary, auxiliary)[
            "首辅 / 纯双拼未进已导出池后辅码进入池率"
        ]
        self.assertEqual(visibility_ranking[-1]["scheme"], "wxpro")
        self.assertIsNone(visibility_ranking[-1]["value"])
        self.assertIsNone(visibility_ranking[-1]["rank"])

    def test_word_equal_broken_rate_ranks_lower_values_first(self) -> None:
        summary = {
            scheme: {
                mode: {
                    "prefix_available_rate": 1.0,
                    "candidate_nonempty_rate": 1.0,
                    "target_covered_rate": 1.0,
                    "top1_rate": 1.0,
                    "word_equal_top1_rate": 1.0,
                    "context": {
                        "context_top1": 1,
                        "n_available": 1,
                        "context_lift": 0.0,
                        "fixed_rate": 0.0,
                        "word_equal_context_top1_rate": 1.0,
                        "word_equal_fixed_rate": 0.0,
                        "broken_rate": 0.0,
                        "word_equal_broken_rate": 0.2 if scheme == "moran" else 0.1,
                    },
                }
                for mode in ("pure", "head", "tail", "both")
            }
            for scheme in SCHEMES
        }
        ranking = full_rankings(summary)["纯双拼 / 目标词等权上下文修坏率"]
        self.assertEqual(ranking[-1]["scheme"], "moran")
        self.assertEqual(ranking[-1]["rank"], 5)

    def test_markdown_renders_five_ranked_schemes_and_percentage_points(self) -> None:
        item = {
            "n": 1,
            "prefix_available": 1,
            "prefix_available_rate": 1.0,
            "candidate_nonempty": 1,
            "candidate_nonempty_rate": 1.0,
            "target_covered": 1,
            "target_covered_rate": 1.0,
            "top1": 1,
            "top1_rate": 0.5,
            "word_count": 1,
            "word_equal_top1_rate": 0.5,
            "empty_candidates": 0,
            "target_absent_exported_topN": 0,
            "missing_raw": 0,
            "truncated": 1,
            "context": {
                "n_available": 1,
                "fresh_top1": 1,
                "context_top1": 1,
                "context_lift": 0.125,
                "fixed": 1,
                "fixed_denominator": 1,
                "fixed_rate": 0.25,
                "broken": 0,
                "broken_denominator": 1,
                "broken_rate": 0.0,
                "context_unavailable": 0,
                "word_count_available": 1,
                "word_equal_context_top1_rate": 1.0,
                "word_equal_fixed_rate": 0.25,
                "word_fixed_count": 1,
                "word_equal_broken_rate": 0.0,
                "word_broken_count": 1,
            },
        }
        summary = {
            scheme: {mode: dict(item) for mode in ("pure", "head", "tail", "both")}
            for scheme in SCHEMES
        }
        auxiliary = {
            "base_mode": "pure",
            "aux_modes": ("head", "tail", "both"),
            "modes": {
                mode: {
                    "base_top1_error": 1,
                    "wrong_to_aux_top1": 1,
                    "wrong_to_aux_top1_rate": 1.0,
                    "base_export_absent": 0,
                    "absent_to_aux_covered": 0,
                    "absent_to_aux_covered_rate": 0.0,
                    "absent_to_aux_top1": 0,
                    "absent_to_aux_top1_rate": 0.0,
                }
                for mode in ("head", "tail", "both")
            },
            "exclusive_top1_rescue": {"head+tail+both": 1},
            "exclusive_total": 1,
            "exclusive_denominator": 1,
        }
        report = {
            "case_count": 1,
            "summary": summary,
            "schemes": {scheme: {"auxiliary_remediation": auxiliary} for scheme in SCHEMES},
            "rankings": full_rankings(
                summary,
                {scheme: auxiliary for scheme in SCHEMES},
            ),
        }
        markdown = render_markdown(report)
        self.assertIn("| 指标 | 1 | 2 | 3 | 4 | 5 |", markdown)
        self.assertIn("+12.50pp", markdown)
        self.assertIn("1/1（100.00%）", markdown)
        self.assertIn("不适用（0/0）", markdown)
        self.assertIn("辅码首选补救互斥校验", markdown)
        self.assertIn("1/1 |", markdown)
        self.assertIn("首辅 / 纯双拼首选错误后辅码首选正确率", markdown)
        self.assertIn("## 目标词等权审计", markdown)

    def test_core_context_zero_denominator_is_unranked(self) -> None:
        summary = {
            scheme: {
                mode: {
                    "prefix_available_rate": 1.0,
                    "candidate_nonempty_rate": 1.0,
                    "target_covered_rate": 1.0,
                    "top1_rate": 1.0,
                    "word_equal_top1_rate": 1.0,
                    "context": {
                        "context_top1": 0,
                        "n_available": 0 if scheme == "wxpro" else 1,
                        "context_lift": 0.0,
                        "fixed_rate": 0.0,
                        "fixed_denominator": 0 if scheme == "wxpro" else 1,
                        "word_count_available": 0 if scheme == "wxpro" else 1,
                        "word_equal_context_top1_rate": 0.0,
                        "word_equal_fixed_rate": 0.0,
                        "word_fixed_count": 0 if scheme == "wxpro" else 1,
                        "broken_rate": 0.0,
                        "broken_denominator": 0 if scheme == "wxpro" else 1,
                        "word_equal_broken_rate": 0.0,
                        "word_broken_count": 0 if scheme == "wxpro" else 1,
                    },
                }
                for mode in ("pure", "head", "tail", "both")
            }
            for scheme in SCHEMES
        }
        rankings = full_rankings(summary)
        for metric in (
            "纯双拼 / 上屏后第一候选命中率",
            "纯双拼 / 上下文提升",
            "纯双拼 / 上下文修好率",
            "纯双拼 / 上下文修坏率",
        ):
            self.assertEqual(rankings[metric][-1]["scheme"], "wxpro")
            self.assertIsNone(rankings[metric][-1]["value"])
            self.assertIsNone(rankings[metric][-1]["rank"])

    def test_build_report_intersects_prefixes_and_embeds_run_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (
                ("i0", "目标", "前缀一"),
                ("i1", "词语", "前缀二"),
            )
            root.joinpath("meta.json").write_text(
                json.dumps({
                    case_id: {"word": text, "prefix": prefix}
                    for case_id, text, prefix in cases
                }),
                encoding="utf-8",
            )
            run_manifest = {
                "jobs": [{} for _ in range(30)],
                "runtime": {"mohu_model": {"sha256": "model-hash"}},
                "validation": {
                    scheme: {
                        "cases": 2,
                        "direct_candidate_streams": 8,
                        "after_prefix_candidate_streams": 8,
                        "prefix_records": 2,
                        "prefix_success": 1 if scheme == "moran" else 2,
                    }
                    for scheme in SCHEMES
                },
            }
            root.joinpath("run-manifest.json").write_text(
                json.dumps(run_manifest), encoding="utf-8"
            )
            b_rows = [
                "B\t" + case_id + "\tpure\thead\ttail\tboth\t" + hx(text) + "\t0\t" + text
                for case_id, text, _ in cases
            ]
            for scheme in SCHEMES:
                spec = SCHEME_SPECS[scheme]
                write_stream(root / "in" / str(spec["input_name"]), b_rows)
                fresh_name = (
                    "moranmain2-moran.fresh.00.tsv"
                    if scheme == "moran"
                    else f"{scheme}-direct.00.tsv"
                )
                fresh_rows = []
                after_rows = []
                for case_id, text, _ in cases:
                    prefix_ok = not (scheme == "moran" and case_id == "i1")
                    after_rows.append(f"A\t{case_id}\t{int(prefix_ok)}\t0")
                    for mode in ("pure", "head", "tail", "both"):
                        fresh_rows += [
                            f"C\t{case_id}\t{mode}\t1\t{hx(text)}",
                            f"E\t{case_id}\t{mode}\t1\t0\t1",
                        ]
                        after_rows += [
                            f"C\t{case_id}\t{mode}\t1\t{hx(text)}",
                            f"E\t{case_id}\t{mode}\t1\t0\t1",
                        ]
                write_stream(root / "out/fresh" / fresh_name, fresh_rows)
                write_stream(
                    root / "out/afterA" / str(spec["condition"]) / "run.00.tsv",
                    after_rows,
                )

            report = build_report(root)
            self.assertEqual(report["run_manifest"], run_manifest)
            self.assertEqual(report["common_prefix_context"]["case_count"], 1)
            self.assertEqual(report["common_prefix_context"]["word_count"], 1)
            for scheme in SCHEMES:
                for mode in ("pure", "head", "tail", "both"):
                    self.assertEqual(
                        report["common_prefix_context"]["summary"][scheme][mode]["n_available"],
                        1,
                    )
            markdown = render_markdown(report)
            self.assertIn("## 运行完整性与隔离", markdown)
            self.assertIn("正式运行完成 30/30 个 shard", markdown)
            self.assertIn("## 五方案共同前缀成功子集", markdown)
            self.assertIn("五个方案都成功提交前缀的 1 个 case", markdown)

    def test_build_rows_keeps_all_cases_when_prefix_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = CrossCase(
                "i0",
                "目标",
                "前缀",
                {mode: mode for mode in ("pure", "head", "tail", "both")},
            )
            write_stream(
                root / "out/fresh/mohu_zrm-run.00.tsv",
                [
                    "C\ti0\tpure\t1\t" + hx("目标"),
                    "E\ti0\tpure\t1\t0\t1",
                    "E\ti0\thead\t0\t0\t1",
                    "E\ti0\ttail\t0\t0\t1",
                    "E\ti0\tboth\t0\t0\t1",
                ],
            )
            write_stream(
                root / "out/afterA/mohu_zrm/run.00.tsv",
                [
                    "A\ti0\t0\t-1",
                    "E\ti0\tpure\t0\t0\t1",
                    "E\ti0\thead\t0\t0\t1",
                    "E\ti0\ttail\t0\t0\t1",
                    "E\ti0\tboth\t0\t0\t1",
                ],
            )
            fresh = build_rows(root, [case], "mohu_zrm")
            context = build_context_rows(root, [case], "mohu_zrm")
            self.assertEqual(len(fresh), 4)
            self.assertEqual(len(context), 4)
            self.assertTrue(all(row.context_state == "prefix_failed" for row in context))
            self.assertEqual(aggregate_context(fresh, context)["context_unavailable"], 4)


    def test_word_rank_rule_ignores_single_char_candidates(self) -> None:
        candidates = ("攻", "目标")
        self.assertEqual(_rank(candidates, "目标"), 2)
        self.assertEqual(_word_rank(candidates, "目标"), 1)
        self.assertEqual(_word_rank(("攻", "别词"), "目标"), 0)
        self.assertEqual(_word_rank(("甲", "乙", "目标"), "目标"), 1)

    def test_build_rows_applies_ignore_single_char_rule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = CrossCase("i0", "目标", "前缀", {"tail1": "abcd"})
            write_stream(
                root / "out/fresh/mohu_zrm-run.00.tsv",
                [
                    "C\ti0\ttail1\t1\t" + hx("攻"),
                    "C\ti0\ttail1\t2\t" + hx("目标"),
                    "E\ti0\ttail1\t2\t0\t1",
                ],
            )
            fresh_raw = build_rows(root, [case], "mohu_zrm")
            fresh_rule = build_rows(
                root, [case], "mohu_zrm", first_candidate_rule="ignore_single_char"
            )
            self.assertEqual(fresh_raw[0].rank, 2)
            self.assertEqual(fresh_raw[0].raw_rank, 2)
            self.assertEqual(fresh_raw[0].candidate_state, "target_covered")
            self.assertEqual(fresh_rule[0].rank, 1)
            self.assertEqual(fresh_rule[0].raw_rank, 2)
            self.assertEqual(fresh_rule[0].candidate_state, "target_covered")

    def test_parse_generic_b_row_mode_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = (
                "B\ti0\ttail1=ab\ttail2=abc\ttail2o=abco\ttail2s=abc/\t"
                + hx("目标")
                + "\t0\t目标"
            )
            write_stream(root / "in/mohu_zrm.fresh.tsv", [row])
            cases = load_cross_cases(root, input_name="mohu_zrm.fresh.tsv")
            self.assertEqual(
                cases[0].modes,
                {"tail1": "ab", "tail2": "abc", "tail2o": "abco", "tail2s": "abc/"},
            )

    def test_parse_b_row_rejects_mixed_mode_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_stream(
                root / "in/mohu_zrm.fresh.tsv",
                ["B\ti0\tpure\thead\t" + hx("目标") + "\t0\t目标"],
            )
            with self.assertRaises(ValueError):
                load_cross_cases(root, input_name="mohu_zrm.fresh.tsv")


if __name__ == "__main__":
    unittest.main()
