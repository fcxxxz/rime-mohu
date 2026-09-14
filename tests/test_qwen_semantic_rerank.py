from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.qwen_semantic_menu_observation import (
    FORMAT_VERSION as OBSERVATION_FORMAT_VERSION,
    SemanticMenuObservationError,
    load_observations,
    parse_observation_lines,
)
from tools.qwen_semantic_rerank import (
    FORMAT_VERSION,
    SemanticMenuValidationError,
    apply_semantic_response,
    canonical_menu_bytes,
    eligible_menu_indices,
    make_scoring_request,
    menu_checksum,
    validate_menu_record,
    validate_scoring_response,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "evaluate_qwen_semantic_reranker.py"
OBSERVATION_FIXTURE = ROOT / "tests" / "fixtures" / "qwen_semantic_menu_observation_v1.tsv"


def phrase_span(text: str, start: int, end: int, source: str = "base") -> dict[str, object]:
    return {"text": text, "start_char": start, "end_char": end, "source": source}


def candidate(
    menu_index: int,
    text: str,
    *,
    protected: bool = False,
    consumes_current_input: bool = True,
    end: int = 4,
    native_score: float | None = -10.0,
    source_flags: list[str] | None = None,
    known_phrase_spans: list[dict[str, object]] | None = None,
    auxiliary_constraints: list[str] | None = None,
    reading_constraints: list[str] | None = None,
) -> dict[str, object]:
    return {
        "menu_index": menu_index,
        "text": text,
        "type": "mohu_zrm",
        "start": 0,
        "end": end,
        "consumes_current_input": consumes_current_input,
        "protected": protected,
        "preedit": "ni hao",
        "native_rank": menu_index + 1,
        "native_score": native_score,
        "native_score_kind": "static_or_personalized_v5",
        "source_flags": source_flags or ["full_input", "native"],
        "dictionary_evidence": {
            "known_phrase_spans": known_phrase_spans or [],
            "personal_phrase_spans": [],
            "fixed_phrase_spans": [],
        },
        "code_evidence": {
            "syllables": ["ni", "hao"],
            "auxiliary_constraints": auxiliary_constraints or [],
            "reading_constraints": reading_constraints or [],
        },
    }


def menu_record() -> dict[str, object]:
    return {
        "format_version": FORMAT_VERSION,
        "case_id": "fixture-001",
        "source": "checked-in-fixture",
        "schema": "mohu_zrm",
        "raw_input": "niho",
        "preedit": "ni hao",
        "committed_context": "这是",
        "target_text": "你好",
        "target_visible": True,
        "oracle_rank": 2,
        "engine_capabilities": None,
        "candidates": [
            candidate(0, "拟好"),
            candidate(
                1,
                "你好",
                known_phrase_spans=[phrase_span("你好", 0, 2)],
                auxiliary_constraints=["h:hao"],
            ),
            candidate(2, "你号", protected=True),
        ],
    }


def eval_row(
    menu: dict[str, object],
    *,
    response: dict[str, object] | None,
    gate_open: bool = True,
    fail_open: bool = False,
    protected_veto: bool = False,
) -> dict[str, object]:
    native_indices = [
        candidate["menu_index"] for candidate in menu["candidates"]  # type: ignore[index]
    ]
    preserve_native_order = not gate_open or fail_open or protected_veto
    final_menu_indices = (
        native_indices
        if preserve_native_order or response is None
        else apply_semantic_response(menu, response, model_profile="student-v1")
    )
    return {
        "format_version": "qwen-semantic-rerank-eval/v1",
        "menu": menu,
        "model_profile": "student-v1",
        "semantic_response": response,
        "final_menu_indices": final_menu_indices,
        "gate": {
            "open": gate_open,
            "fail_open": fail_open,
            "protected_veto": protected_veto,
        },
    }


class SemanticMenuObservationTest(unittest.TestCase):
    def test_public_rime_observation_preserves_duplicate_text_by_index(self) -> None:
        observations = load_observations(OBSERVATION_FIXTURE)

        self.assertEqual(OBSERVATION_FORMAT_VERSION, "qwen-semantic-menu-observation/v1")
        self.assertEqual(len(observations), 1)
        observation = observations[0]
        self.assertEqual(observation.case_id, "duplicate-menu")
        self.assertEqual(observation.raw_input, "niho")
        self.assertEqual(observation.composition_preedit, "ni hao")
        self.assertEqual([item.menu_index for item in observation.candidates], [0, 1, 2])
        self.assertEqual([item.text for item in observation.candidates], ["拟好", "拟好", "你好"])
        self.assertEqual(observation.candidates[1].comment, "重复")

    def test_observation_rejects_incomplete_or_out_of_order_menus(self) -> None:
        valid = OBSERVATION_FIXTURE.read_text(encoding="utf-8").splitlines()
        cases = (
            (
                "wrong index",
                [line.replace("C\tduplicate-menu\t1", "C\tduplicate-menu\t3") for line in valid],
                "non-contiguous menu_index",
            ),
            ("missing end", valid[:-1], "missing end row"),
            (
                "bad hex",
                [line.replace("E68B9FE5A5BD", "zz", 1) for line in valid],
                "not valid UTF-8 hex",
            ),
            (
                "truncated menu",
                [line.replace("E\tduplicate-menu\t3\t0", "E\tduplicate-menu\t3\t1") for line in valid],
                "truncated menus",
            ),
            ("blank row", [valid[0], "", *valid[1:]], "blank observation row"),
            ("wrong header", ["H\twrong-version", *valid[1:]], "invalid observation header"),
        )
        for label, lines, message in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(SemanticMenuObservationError, message):
                    parse_observation_lines(lines, source=label)


class SemanticMenuValidationTest(unittest.TestCase):
    def test_canonicalization_normalizes_unicode_and_unordered_evidence(self) -> None:
        first = menu_record()
        first["committed_context"] = "Cafe\u0301"
        first["candidates"][1]["source_flags"] = ["native", "full_input"]  # type: ignore[index]
        second = copy.deepcopy(first)
        second["committed_context"] = "Caf\u00e9"
        second["candidates"][1]["source_flags"] = ["full_input", "native"]  # type: ignore[index]

        first_bytes = canonical_menu_bytes(first)
        second_bytes = canonical_menu_bytes(second)

        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(menu_checksum(first), menu_checksum(second))
        canonical = validate_menu_record(first)
        self.assertEqual(canonical["committed_context"], "Caf\u00e9")
        self.assertEqual(canonical["candidates"][1]["source_flags"], ["full_input", "native"])
        first_text = first_bytes.decode("utf-8")
        self.assertIn('"committed_context":"Café"', first_text)
        self.assertNotIn(": ", first_text)

    def test_duplicate_display_text_is_valid_when_menu_indexes_are_unique(self) -> None:
        record = menu_record()
        record["candidates"][1]["text"] = "拟好"  # type: ignore[index]
        record["candidates"][1]["dictionary_evidence"]["known_phrase_spans"] = []  # type: ignore[index]
        record["target_text"] = "拟好"

        validated = validate_menu_record(record)

        self.assertEqual(
            [item["text"] for item in validated["candidates"]], ["拟好", "拟好", "你号"]
        )
        self.assertEqual(eligible_menu_indices(validated), [0, 1])

    def test_invalid_menu_index_span_or_target_rank_is_rejected(self) -> None:
        cases = [
            ("duplicate index", lambda record: record["candidates"][1].update(menu_index=0)),
            ("invalid span", lambda record: record["candidates"][0].update(end=5)),
            ("oracle rank", lambda record: record.update(oracle_rank=1)),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                record = menu_record()
                mutate(record)
                with self.assertRaises(SemanticMenuValidationError):
                    validate_menu_record(record)

    def test_incomplete_and_protected_slots_are_excluded_from_scoring(self) -> None:
        record = menu_record()
        record["candidates"][0]["consumes_current_input"] = False  # type: ignore[index]
        record["candidates"][0]["end"] = 3  # type: ignore[index]

        validated = validate_menu_record(record)

        self.assertEqual(eligible_menu_indices(validated), [1])

    def test_ordered_syllables_preserve_repetition(self) -> None:
        record = menu_record()
        record["candidates"][1]["code_evidence"]["syllables"] = ["ni", "ni", "hao"]  # type: ignore[index]

        validated = validate_menu_record(record)

        self.assertEqual(
            validated["candidates"][1]["code_evidence"]["syllables"], ["ni", "ni", "hao"]
        )

    def test_native_score_null_requires_capability_manifest(self) -> None:
        record = menu_record()
        record["candidates"][0]["native_score"] = None  # type: ignore[index]
        with self.assertRaisesRegex(SemanticMenuValidationError, "engine_capabilities"):
            validate_menu_record(record)

        record["engine_capabilities"] = {
            "engine_id": "tigerengine",
            "engine_version": "5.0.0",
            "capabilities": ["candidate_spans"],
        }
        validated = validate_menu_record(record)
        self.assertEqual(validated["engine_capabilities"]["engine_id"], "tigerengine")

        record["engine_capabilities"]["unexpected"] = True  # type: ignore[index]
        with self.assertRaisesRegex(SemanticMenuValidationError, "unknown fields"):
            validate_menu_record(record)

    def test_unknown_fields_are_rejected_at_each_protocol_level(self) -> None:
        record = menu_record()
        record["candidates"][1]["dictionary_evidence"]["known_phrase_spans"] = [  # type: ignore[index]
            phrase_span("你好", 0, 2)
        ]
        mutations = (
            ("menu", lambda value: value.__setitem__("top_level", True)),
            ("candidate", lambda value: value.__setitem__("candidate_extra", True)),
            (
                "dictionary evidence",
                lambda value: value.__setitem__("dictionary_extra", True),
            ),
            ("code evidence", lambda value: value.__setitem__("code_extra", True)),
            ("phrase span", lambda value: value.__setitem__("span_extra", True)),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                isolated = copy.deepcopy(record)
                if label == "menu":
                    target = isolated
                elif label == "candidate":
                    target = isolated["candidates"][0]
                elif label == "dictionary evidence":
                    target = isolated["candidates"][0]["dictionary_evidence"]
                elif label == "code evidence":
                    target = isolated["candidates"][0]["code_evidence"]
                else:
                    target = isolated["candidates"][1]["dictionary_evidence"]["known_phrase_spans"][0]
                mutate(target)
                with self.assertRaisesRegex(SemanticMenuValidationError, "unknown fields"):
                    validate_menu_record(isolated)

        request = make_scoring_request(record, model_profile="student-v1")
        response = {
            "format_version": FORMAT_VERSION,
            "protocol_checksum": request["protocol_checksum"],
            "model_profile": "student-v1",
            "scores": [
                {"menu_index": 0, "score": 1.0},
                {"menu_index": 1, "score": 2.0},
            ],
        }
        with self.assertRaisesRegex(SemanticMenuValidationError, "unknown fields"):
            validate_scoring_response(response, {**request, "request_extra": True})
        with self.assertRaisesRegex(SemanticMenuValidationError, "unknown fields"):
            validate_scoring_response({**response, "response_extra": True}, request)
        with self.assertRaisesRegex(SemanticMenuValidationError, "unknown fields"):
            validate_scoring_response(
                {
                    **response,
                    "scores": [
                        {**response["scores"][0], "score_extra": True},
                        response["scores"][1],
                    ],
                },
                request,
            )

    def test_request_and_score_response_use_indexes_and_reject_partial_outputs(self) -> None:
        record = menu_record()
        request = make_scoring_request(record, model_profile="student-v1")
        valid_response = {
            "format_version": FORMAT_VERSION,
            "protocol_checksum": request["protocol_checksum"],
            "model_profile": "student-v1",
            "scores": [
                {"menu_index": 0, "score": 1.0},
                {"menu_index": 1, "score": 2.0},
            ],
        }

        validated = validate_scoring_response(valid_response, request)

        self.assertEqual(validated["scores"][1]["menu_index"], 1)
        with self.assertRaisesRegex(SemanticMenuValidationError, "full permutation"):
            validate_scoring_response(
                {**valid_response, "scores": valid_response["scores"][:1]}, request
            )
        with self.assertRaisesRegex(SemanticMenuValidationError, "protected or ineligible"):
            validate_scoring_response(
                {
                    **valid_response,
                    "scores": [
                        {"menu_index": 0, "score": 1.0},
                        {"menu_index": 2, "score": 2.0},
                    ],
                },
                request,
            )

    def test_request_checksum_profile_and_utf8_are_strictly_bound(self) -> None:
        record = menu_record()
        request = make_scoring_request(record, model_profile="student-v1")
        response = {
            "format_version": FORMAT_VERSION,
            "protocol_checksum": request["protocol_checksum"],
            "model_profile": "student-v1",
            "permutation": [1, 0],
        }

        altered_request = {**request, "protocol_checksum": "0" * 64}
        with self.assertRaisesRegex(SemanticMenuValidationError, "does not match menu"):
            validate_scoring_response(response, altered_request)
        with self.assertRaisesRegex(SemanticMenuValidationError, "model profile mismatch"):
            apply_semantic_response(record, {**response, "model_profile": "wrong-profile"}, model_profile="student-v1")
        invalid_unicode = menu_record()
        invalid_unicode["committed_context"] = "bad\ud800"
        with self.assertRaisesRegex(SemanticMenuValidationError, "Unicode scalar"):
            canonical_menu_bytes(invalid_unicode)

    def test_response_ties_are_stable_and_protected_slots_stay_in_place(self) -> None:
        record = menu_record()
        request = make_scoring_request(record, model_profile="student-v1")
        response = {
            "format_version": FORMAT_VERSION,
            "protocol_checksum": request["protocol_checksum"],
            "model_profile": "student-v1",
            "scores": [
                {"menu_index": 0, "score": 5.0},
                {"menu_index": 1, "score": 5.0},
            ],
        }

        self.assertEqual(
            apply_semantic_response(record, response, model_profile="student-v1"), [0, 1, 2]
        )
        response["scores"] = [  # type: ignore[index]
            {"menu_index": 0, "score": 1.0},
            {"menu_index": 1, "score": 2.0},
        ]
        self.assertEqual(
            apply_semantic_response(record, response, model_profile="student-v1"), [1, 0, 2]
        )

    def test_protected_veto_preserves_native_order_after_valid_response(self) -> None:
        from tools.evaluate_qwen_semantic_reranker import evaluate_rows

        menu = menu_record()
        request = make_scoring_request(menu, model_profile="student-v1")
        response = {
            "format_version": FORMAT_VERSION,
            "protocol_checksum": request["protocol_checksum"],
            "model_profile": "student-v1",
            "permutation": [1, 0],
        }
        row = eval_row(menu, response=response, protected_veto=True)
        row["final_menu_indices"] = [0, 1, 2]

        metrics = evaluate_rows([row], model_profile="student-v1")

        self.assertEqual(metrics["reranked_correct"], 0)
        self.assertEqual(metrics["protected_veto_rate"], 1.0)


class SemanticMenuEvaluatorTest(unittest.TestCase):
    def test_cli_reports_index_metrics_and_gate_rates(self) -> None:
        menu = menu_record()
        request = make_scoring_request(menu, model_profile="student-v1")
        response = {
            "format_version": FORMAT_VERSION,
            "protocol_checksum": request["protocol_checksum"],
            "model_profile": "student-v1",
            "permutation": [1, 0],
        }
        rows = [eval_row(menu, response=response)]
        with tempfile.TemporaryDirectory() as directory:
            dump = Path(directory) / "semantic.jsonl"
            output = Path(directory) / "metrics.json"
            dump.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "python",
                    str(SCRIPT),
                    str(dump),
                    "--model-profile",
                    "student-v1",
                    "--bootstrap-samples",
                    "50",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            metrics = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(metrics["baseline_correct"], 0)
            self.assertEqual(metrics["reranked_correct"], 1)
            self.assertEqual(metrics["candidate_membership_failures"], 0)
            self.assertEqual(metrics["gate_open_rate"], 1.0)
            self.assertEqual(metrics["slices"]["candidate_count"]["3"]["total"], 1)

    def test_evaluator_rejects_changed_membership_and_accepts_diagnostic_responses(self) -> None:
        from tools.evaluate_qwen_semantic_reranker import SemanticEvaluationError, evaluate_rows

        menu = menu_record()
        request = make_scoring_request(menu, model_profile="student-v1")
        response = {
            "format_version": FORMAT_VERSION,
            "protocol_checksum": request["protocol_checksum"],
            "model_profile": "student-v1",
            "permutation": [1, 0],
        }
        changed = eval_row(menu, response=response)
        changed["final_menu_indices"] = [1, 1, 2]
        with self.assertRaisesRegex(SemanticEvaluationError, "full permutation"):
            evaluate_rows([changed], model_profile="student-v1")

        for label, gate_args in (
            ("closed", {"gate_open": False}),
            ("fail-open", {"fail_open": True}),
            ("protection veto", {"protected_veto": True}),
        ):
            with self.subTest(label=label):
                diagnostic = eval_row(menu, response=response, **gate_args)
                metrics = evaluate_rows([diagnostic], model_profile="student-v1")
                self.assertEqual(metrics["reranked_correct"], 0)
                self.assertEqual(metrics["reversal_rate"], 0.0)

        invalid_diagnostic = eval_row(menu, response=response, gate_open=False)
        invalid_diagnostic["final_menu_indices"] = [1, 0, 2]
        with self.assertRaisesRegex(SemanticEvaluationError, "preserve native menu order"):
            evaluate_rows([invalid_diagnostic], model_profile="student-v1")

        mismatched_profile = eval_row(menu, response=response)
        with self.assertRaisesRegex(SemanticEvaluationError, "trusted evaluation profile"):
            evaluate_rows([mismatched_profile], model_profile="student-v2")

    def test_evaluator_rejects_unknown_row_and_gate_fields(self) -> None:
        from tools.evaluate_qwen_semantic_reranker import SemanticEvaluationError, evaluate_rows

        menu = menu_record()
        row = eval_row(menu, response=None, gate_open=False)
        with self.assertRaisesRegex(SemanticEvaluationError, "unknown fields"):
            evaluate_rows([{**row, "extra": True}], model_profile="student-v1")
        row["gate"]["extra"] = True  # type: ignore[index]
        with self.assertRaisesRegex(SemanticEvaluationError, "unknown fields"):
            evaluate_rows([row], model_profile="student-v1")


if __name__ == "__main__":
    unittest.main()
