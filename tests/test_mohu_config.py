import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SCHEMAS = [
    "mohu_zrm",
    "mohu_flypy",
    "mohu_flypy_fixed",
    "mohu_flypy_sentence",
    "mohu_flypy_aux",
    "mohu_llm_zrm",
    "mohu_llm_flypy",
    "tiger",
]

DOUBLE_PINYIN_SCHEMAS = EXPECTED_SCHEMAS[:5]
STANDARD_SCHEMAS = [schema for schema in EXPECTED_SCHEMAS if not schema.startswith("mohu_llm_")]
COMPILE_ONLY_SCHEMAS = [
    "mohu_zrm_fixed",
    "mohu_zrm_fixed_legacy",
    "mohu_zrm_sentence",
    "mohu_flypy_fixed_legacy",
]
CONTEXTUAL_SCHEMAS = [
    "mohu_zrm",
    "mohu_flypy",
    "mohu_flypy_sentence",
    "mohu_flypy_aux",
]
REMOVED_COMPONENTS = (
    "mohu_english",
    "mohu_japanese",
    "reverse_universal",
    "reverse_stroke",
    "reverse_cangjie5",
    "reverse_zrlf",
    "reverse_bopomofo",
    "std_t2s",
    "std_t2tw",
    "std_t2hk",
    "std_t2jp",
    "std_t2dzing",
)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class MohuConfigTest(unittest.TestCase):
    def test_default_schema_order(self) -> None:
        default = read("default.yaml")
        schemas = re.findall(r"^\s*- schema: (\S+)\s*$", default, re.MULTILINE)
        self.assertEqual(EXPECTED_SCHEMAS, schemas)
        for removed in ("zh_hant", "zh_hans", "simplification"):
            self.assertNotIn(removed, default)

    def test_double_pinyin_schemas_exist_and_are_minimal(self) -> None:
        for schema_id in DOUBLE_PINYIN_SCHEMAS:
            with self.subTest(schema=schema_id):
                text = read(f"{schema_id}.schema.yaml")
                self.assertIn(f"schema_id: {schema_id}", text)
                self.assertIn("states: [ 常用字, 全字集 ]", text)
                self.assertIn("reverse_lookup_translator@reverse_tiger", text)
                self.assertIn(
                    "reverse_lookup_translator@reverse_tiger_backtick", text
                )
                self.assertEqual(
                    2,
                    len(re.findall(r"reverse_lookup_translator@", text)),
                )
                self.assertIn("tips: 〔虎〕", text)
                self.assertNotIn("〔虎码〕", text)
                self.assertNotRegex(text, r"(?m)^\s+reverse_lookup:")
                for removed in REMOVED_COMPONENTS:
                    self.assertNotIn(removed, text)
                self.assertNotRegex(text, r"states:\s*\[\s*简,\s*通")

    def test_auxiliary_schema_display_names_use_filtering_term(self) -> None:
        self.assertIn(
            "  name: 辅筛·魔虎·小鹤\n",
            read("mohu_flypy_aux.schema.yaml"),
        )

    def test_runtime_lua_does_not_reference_removed_variant_options(self) -> None:
        processor = read("lua/mohu_processor.lua")
        self.assertNotRegex(processor, r"std_(?:s|t)")

    def test_all_schema_lua_modules_exist(self) -> None:
        missing = []
        for schema_id in DOUBLE_PINYIN_SCHEMAS:
            text = "\n".join(
                line
                for line in read(f"{schema_id}.schema.yaml").splitlines()
                if not line.lstrip().startswith("#")
            )
            modules = re.findall(
                r"lua_(?:processor|translator|filter)@\*([^*@\s]+)",
                text,
            )
            for module in modules:
                if not (ROOT / "lua" / f"{module}.lua").is_file():
                    missing.append((schema_id, module))
        self.assertEqual([], missing)

    def test_tiger_uses_full_pinyin_reverse_lookup(self) -> None:
        text = read("tiger.schema.yaml")
        self.assertIn("- mohu_pinyin", text)
        self.assertIn("dictionary: mohu_pinyin", text)
        self.assertIn('prefix: "`"', text)
        self.assertIn("〔全拼反查〕", text)
        dictionary = read("mohu_pinyin.dict.yaml")
        self.assertIn("\n火\thuo\t1180390\n", dictionary)

    def test_runtime_menu_switches(self) -> None:
        default = read("default.yaml")
        self.assertIn("    - contextual_order\n", default)
        self.assertIn("    - quick_code_hint\n", default)
        self.assertIn("    - aux_hint\n", default)
        self.assertIn("    - multi_short_code\n", default)
        self.assertIn("    - mohu_llm_model_rerank\n", default)
        self.assertNotIn("mohu_tiger_sentence_early_commit", default)

        for schema_id in DOUBLE_PINYIN_SCHEMAS:
            with self.subTest(schema=schema_id):
                text = read(f"{schema_id}.schema.yaml")
                self.assertIn("  - name: multi_short_code\n", text)
                self.assertIn(
                    "    states: [ 多重简字, 唯一简字 ]\n",
                    text,
                )
                self.assertNotIn("  - name: multi_short_code\n    reset", text)
                self.assertIn("  - name: quick_code_hint\n", text)
                self.assertIn(
                    "    states: [ 简码提示关, 简码提示 ]\n",
                    text,
                )
                self.assertNotIn("  - name: quick_code_hint\n    reset", text)
                self.assertIn("  - name: aux_hint\n", text)
                self.assertIn(
                    "    states: [ 辅助码提示关, 辅助码提示 ]\n",
                    text,
                )
                self.assertNotIn("  - name: aux_hint\n    reset", text)
                self.assertIn("lua_processor@*option_sync\n", text)
                self.assertIn("    - lua_filter@*mohu_hint_filter", text)
                self.assertNotIn("mohu/enable_quick_code_hint", text)
                self.assertIn("  inject_fixed_words: true", text)
                if schema_id.endswith("_fixed"):
                    self.assertIn(
                        "lua_translator@*mohu_contextual_translator*fixed_selector",
                        text,
                    )
                    self.assertNotIn(
                        "lua_translator@*mohu_contextual_translator@fixed_selector",
                        text,
                    )
                if schema_id in {
                    "mohu_zrm",
                    "mohu_flypy",
                    "mohu_flypy_fixed",
                    "mohu_flypy_aux",
                }:
                    scheme = "flypy" if schema_id.startswith("mohu_flypy") else "zrm"
                    self.assertIn(
                        f"  dictionary: mohu_{scheme}_fixed_legacy\n",
                        text,
                    )
                    self.assertIn("translator_legacy:\n", text)
                if schema_id in CONTEXTUAL_SCHEMAS:
                    self.assertIn("  - name: contextual_order\n", text)
                    self.assertIn(
                        "    states: [ 单次候选调频, 跨候选调频 ]\n",
                        text,
                    )
                    self.assertNotIn(
                        "  - name: contextual_order\n    reset", text
                    )
                    self.assertIn("  contextual_suggestions: true\n", text)
                    self.assertNotIn("  contextual_suggestions: false\n", text)
                else:
                    self.assertNotIn("  - name: contextual_order\n", text)

        self.assertIn(
            "quick_code_hint_dictionary: mohu_zrm_fixed",
            read("mohu_zrm_sentence.schema.yaml"),
        )
        self.assertIn(
            "quick_code_hint_dictionary: mohu_flypy_fixed",
            read("mohu_flypy_sentence.schema.yaml"),
        )

    def test_option_sync_setup(self) -> None:
        # 菜单开关依赖 save_options + option_sync 跨会话同步，不能再用 reset 压制。
        self.assertTrue((ROOT / "lua" / "option_sync.lua").is_file())
        self.assertTrue((ROOT / "lua" / "option_state.lua").is_file())
        self.assertIn("lua_processor@*option_sync\n", read("tiger.schema.yaml"))
        tiger_sentence = read("mohu_llm_zrm.schema.yaml")
        for name in (
            "mohu_llm_model_rerank",
            "contextual_order",
            "quick_code_hint",
            "aux_hint",
            "multi_short_code",
        ):
            self.assertNotIn(f"  - name: {name}\n    reset", tiger_sentence)
        self.assertIn("lua_processor@*option_sync\n", tiger_sentence)
        self.assertIn(
            "    - mohu_llm_model_rerank\n", read("default.yaml")
        )
        self.assertNotIn("mohu_tiger_sentence_early_commit", tiger_sentence)

    def test_native_schema_is_named_for_llm_and_keeps_octagram_on_mohu_zrm(self) -> None:
        native = read("mohu_llm_zrm.schema.yaml")
        self.assertIn("  schema_id: mohu_llm_zrm\n", native)
        self.assertIn("  name: 魔虎大模型·自然码\n", native)
        self.assertIn(
            "    states: [ 模型重排关, 模型重排开 ]\n",
            native,
        )
        self.assertNotIn("__include: mohu:/octagram/enable_for_sentence", native)
        self.assertIn(
            "__include: mohu:/octagram/enable_for_sentence\n",
            read("mohu_zrm.schema.yaml"),
        )

    def test_native_dist_contains_complete_scorer_runtime(self) -> None:
        makefile = read("Makefile")
        for target in ("mohu-llm-runtime-dist", "mohu-llm-zrm-dist", "mohu-llm-flypy-dist"):
            self.assertIn(f"{target}:", makefile)
        self.assertNotIn("mohu_tiger_sentence.schema.yaml", makefile)
        self.assertNotIn("install_mohu_llm.command", makefile)

    def test_standard_dist_excludes_native_and_qwen_assets(self) -> None:
        makefile = read("Makefile")
        recipe = makefile.split("dist: quick", 1)[1].split(
            "# Native Tiger sentence assets", 1
        )[0]
        for forbidden in (
            "tiger_sentence_native",
            "libtigerengine.dylib",
            "qwen35_scorer.py",
            "sentence-ngram-mobile.bin",
            "safetensors",
            "/models/",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, recipe)

    def test_llm_dist_requires_ngram_and_excludes_model_weights(self) -> None:
        makefile = read("Makefile")
        self.assertIn(
            "TIGER_NGRAM ?= tiger_sentence_native/sentence-ngram-mobile.bin",
            makefile,
        )
        self.assertIn("mohu-llm-runtime-dist:", makefile)
        self.assertIn("mohu-llm-zrm-dist:", makefile)
        self.assertIn("mohu-llm-flypy-dist:", makefile)
        self.assertIn("MOHU_LLM_ZRM_DESTDIR", makefile)
        self.assertIn('"$(MOHU_LLM_ZRM_DESTDIR)/runtime"', makefile)
        self.assertIn("mohu_llm_zrm.schema.yaml", makefile)
        self.assertIn("mohu_llm_flypy.schema.yaml", makefile)
        self.assertIn("model_manifests", read("tiger_sentence_native/mohu_llm_zrm.package.json"))
        self.assertNotIn("mohu_tiger_sentence.schema.yaml", makefile)
        self.assertNotIn("$(LLM_DESTDIR)/tiger", makefile)

    def test_llm_installer_merges_user_schema_patch_safely(self) -> None:
        self.assertFalse((ROOT / "tiger_sentence_native/install_mohu_llm.command").exists())
        for scheme in ("zrm", "flypy"):
            installer_path = ROOT / "tiger_sentence_native/install_mohu_llm_scheme.command"
            installer = installer_path.read_text(encoding="utf-8")
            self.assertTrue(os.access(installer_path, os.X_OK))
            self.assertIn("MOHU_LLM_SCHEME", installer)
            self.assertNotIn("mohu_tiger_sentence.schema.yaml", installer)
            self.assertNotIn("$rime_dir/tiger", installer)
        self.assertIn("default.custom.yaml", installer)
        self.assertIn("schema_list/+", installer)
        self.assertIn("MOHU_RIME_DIR", installer)
        self.assertIn("--reload", installer)
        self.assertIn("mohu_llm/runtime", installer)

    def test_qwen_manifests_match_model_registry(self) -> None:
        catalog = read("tiger_sentence_native/mohu_tiger_model_catalog.lua")
        supervisor = read("tiger_sentence_native/scorer_models.zsh")
        expected = {
            "qwen35-0.8b": {
                "registry_path": "mlx-community/Qwen3.5-0.8B-MLX-4bit",
                "path": "mohu_llm/models/Qwen3.5-0.8B-MLX-4bit",
                "model_type": "qwen3_5",
                "size_bytes": 652034038,
                "size_mib": 621.83,
                "sha256": "8b1fc914a940d611e13ba1880ffdae553deb4504a0a6299256ac19470fc591b8",
                "manifest": "tiger_sentence_native/models/qwen35-0.8b.manifest",
            },
            "qwen3-0.6b": {
                "registry_path": "mlx-community/Qwen3-0.6B-4bit",
                "path": "mohu_llm/models/Qwen3-0.6B-4bit",
                "model_type": "qwen3",
                "size_bytes": 351388968,
                "size_mib": 335.11,
                "sha256": "2de6c7d42ac12c447715e06bfab6497bdd49707bec990ae3cddce3a8c4ba0548",
                "manifest": "tiger_sentence_native/models/qwen3-0.6b.manifest",
            },
        }
        for model_id, fields in expected.items():
            with self.subTest(model=model_id):
                manifest = json.loads(read(fields["manifest"]))
                self.assertEqual(model_id, manifest["id"])
                self.assertEqual(fields["registry_path"], manifest["registry_path"])
                self.assertEqual(fields["path"], manifest["path"])
                self.assertEqual(fields["model_type"], manifest["model_type"])
                self.assertEqual(4, manifest["quantization_bits"])
                self.assertEqual(fields["size_bytes"], manifest["size_bytes"])
                self.assertAlmostEqual(fields["size_mib"], manifest["size_mib"], places=2)
                self.assertEqual(fields["sha256"], manifest["sha256"])
                self.assertIsNotNone(
                    re.search(
                        rf'id = "{re.escape(model_id)}".*?model_type = "{re.escape(fields["model_type"])}"',
                        catalog,
                        re.DOTALL,
                    )
                )
                self.assertIn(fields["sha256"], catalog)
                self.assertIn(fields["sha256"], supervisor)

    def test_github_workflow_builds_and_releases_llm_addon(self) -> None:
        workflow = read(".github/workflows/build.yml")
        self.assertIn("runs-on: macos-14", workflow)
        self.assertIn("mohu-llm-zrm-dist", workflow)
        self.assertIn("sentence-ngram-mobile.bin", workflow)
        self.assertIn("mohu-llm-zrm-latest.zip", workflow)
        self.assertIn("mohu-llm-flypy-latest.zip", workflow)
        self.assertIn("build-llm", workflow)
        self.assertIn("needs: [build, build-llm]", workflow)

    def test_classics_dictionary_is_imported_only_by_smart_tables(self) -> None:
        self.assertIn(
            "  - mohu_zrm.classics   # 经审核的古诗文与经典文本\n",
            read("mohu_zrm.extended.dict.yaml"),
        )
        self.assertIn(
            "  - mohu_flypy.classics   # 经审核的古诗文与经典文本\n",
            read("mohu_flypy.extended.dict.yaml"),
        )
        for path in ROOT.glob("mohu_*_fixed*.dict.yaml"):
            with self.subTest(path=path.name):
                self.assertNotIn("classics", path.read_text(encoding="utf-8"))

    def test_legacy_dictionaries_have_compile_only_dependencies(self) -> None:
        for scheme in ("zrm", "flypy"):
            with self.subTest(scheme=scheme):
                schema_id = f"mohu_{scheme}_fixed_legacy"
                helper = read(f"{schema_id}.schema.yaml")
                self.assertIn(f"  schema_id: {schema_id}\n", helper)
                self.assertIn(f"  dictionary: {schema_id}\n", helper)
                self.assertIn(
                    f"    - {schema_id}\n",
                    read(f"mohu_{scheme}_fixed.schema.yaml"),
                )
                self.assertNotIn(f"  - schema: {schema_id}\n", read("default.yaml"))

    def test_fixed_schemas_merge_static_table_before_learning_table(self) -> None:
        static_selector = (
            "    - lua_translator@*mohu_contextual_translator*fixed_static_selector\n"
        )
        learning_selector = (
            "    - lua_translator@*mohu_contextual_translator*fixed_selector\n"
        )
        for scheme in ("zrm", "flypy"):
            with self.subTest(scheme=scheme):
                text = read(f"mohu_{scheme}_fixed.schema.yaml")
                self.assertIn(static_selector, text)
                self.assertLess(text.index(static_selector), text.index(learning_selector))

    def test_quality_cannot_override_fixed_positions(self) -> None:
        for schema_id in DOUBLE_PINYIN_SCHEMAS:
            with self.subTest(schema=schema_id):
                self.assertNotIn(
                    "four_code_two_char_first_choice_quality",
                    read(f"{schema_id}.schema.yaml"),
                )


class MohuCharsetTest(unittest.TestCase):
    def test_common_charset_matches_tiger_core2022_snapshot(self) -> None:
        dictionary = read("mohu_charset.dict.yaml")
        self.assertIn(
            "# Source: rime-tiger/core2022.dict.yaml (version 2026.03.01)",
            dictionary,
        )

        entries = {
            fields[0]: fields[1]
            for line in dictionary.splitlines()
            if not line.startswith("#") and len(fields := line.split("\t")) == 2
        }
        self.assertEqual(9767, len(entries))
        self.assertTrue(all(marker == "t" for marker in entries.values()))
        for char in "的一是我你":
            self.assertIn(char, entries)
        for char in "寗㲰㩶":
            self.assertNotIn(char, entries)


class MohuNamingTest(unittest.TestCase):
    def test_no_legacy_runtime_files(self) -> None:
        legacy = [
            path.name
            for path in ROOT.iterdir()
            if path.is_file()
            and path.name.startswith("moran")
            and path.suffix in {".yaml", ".txt", ".gram"}
        ]
        legacy.extend(path.name for path in (ROOT / "lua").glob("moran*.lua"))
        self.assertEqual([], sorted(legacy))

    def test_distribution_contains_only_current_runtime_schemas(self) -> None:
        dist = ROOT / "dist"
        if not dist.is_dir():
            self.skipTest("distribution has not been built")
        schemas = sorted(path.name for path in dist.glob("*.schema.yaml"))
        expected = sorted(
            [f"{schema_id}.schema.yaml" for schema_id in STANDARD_SCHEMAS]
            + [f"{schema_id}.schema.yaml" for schema_id in COMPILE_ONLY_SCHEMAS]
            + ["mohu_charset.schema.yaml", "mohu_pinyin.schema.yaml"]
        )
        self.assertEqual(expected, schemas)
        legacy = sorted(path.name for path in dist.iterdir() if path.name.startswith("moran"))
        self.assertEqual([], legacy)


if __name__ == "__main__":
    unittest.main()
