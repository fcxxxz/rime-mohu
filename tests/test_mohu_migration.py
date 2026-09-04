import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.migrate_moran_to_mohu import apply_migration, plan_migration


class MohuMigrationTest(unittest.TestCase):
    def test_migrated_schema_menu_adds_only_public_flypy_schema(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "default.custom.yaml"
            source.write_text(
                "patch:\n"
                "  schema_list:\n"
                "    - {schema: moran}\n"
                "    - {schema: moran_fixed}\n"
                "    - {schema: moran_sentence}\n"
                "    - {schema: moran_aux}\n"
                "    - {schema: tiger}\n",
                encoding="utf-8",
            )

            migrated = plan_migration(root).text_edits[source]

            expected_order = [
                "mohu_zrm",
                "mohu_flypy",
                "tiger",
            ]
            for schema in expected_order:
                self.assertIn(f"schema: {schema}", migrated)
            positions = [migrated.index(f"schema: {schema}") for schema in expected_order]
            self.assertEqual(positions, sorted(positions))
            for removed in (
                "mohu_zrm_fixed",
                "mohu_zrm_sentence",
                "mohu_zrm_aux",
                "mohu_flypy_fixed",
                "mohu_flypy_sentence",
                "mohu_flypy_aux",
            ):
                self.assertNotIn(removed, migrated)

    def test_migration_drops_previously_published_internal_schema_entries(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "default.custom.yaml"
            source.write_text(
                "schema_list:\n"
                "  - schema: moran\n"
                "  - schema: mohu_zrm_core\n"
                "  - schema: mohu_zrm_sentence_core\n"
                "  - schema: mohu_llm_zrm\n"
                "  - schema: mohu_flypy\n"
                "  - schema: mohu_flypy_fixed\n"
                "  - schema: mohu_flypy_fixed_legacy\n"
                "  - schema: mohu_flypy_sentence_core\n"
                "  - schema: mohu_flypy_aux\n"
                "  - schema: mohu_flypy_core\n"
                "  - schema: mohu_llm_flypy\n",
                encoding="utf-8",
            )

            migrated = plan_migration(root).text_edits[source]

            self.assertIn("schema: mohu_zrm", migrated)
            self.assertIn("schema: mohu_flypy", migrated)
            for removed in (
                "mohu_zrm_core",
                "mohu_zrm_sentence_core",
                "mohu_llm_zrm",
                "mohu_flypy_fixed",
                "mohu_flypy_fixed_legacy",
                "mohu_flypy_sentence_core",
                "mohu_flypy_aux",
                "mohu_flypy_core",
                "mohu_llm_flypy",
            ):
                self.assertNotIn(removed, migrated)

    def test_plan_is_read_only_and_maps_old_schemas_to_zrm(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "default.custom.yaml"
            original = "schema_list:\n  - schema: moran\n  - schema: moran_fixed\n"
            source.write_text(original, encoding="utf-8")

            plan = plan_migration(root)

            self.assertEqual(original, source.read_text(encoding="utf-8"))
            self.assertFalse(plan.unknown_references)
            self.assertIn("mohu_zrm", plan.text_edits[source])
            self.assertNotIn("moran_fixed", plan.text_edits[source])
            self.assertNotIn("mohu_zrm_fixed", plan.text_edits[source])

    def test_apply_backs_up_and_renames_config_and_userdb(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / "moran.custom.yaml"
            custom.write_text(
                "patch:\n  mohu/algebra/user_force_top: {}\n  schema: moran\n",
                encoding="utf-8",
            )
            userdb = root / "moran_tiger_prefix2.userdb"
            userdb.mkdir()
            (userdb / "data.bin").write_bytes(b"learning")

            plan = plan_migration(root)
            backup = apply_migration(root, plan, timestamp="20260815-120000")

            self.assertEqual(root / "mohu-migration-backup-20260815-120000", backup)
            self.assertTrue((backup / "moran.custom.yaml").exists())
            self.assertTrue((backup / "moran_tiger_prefix2.userdb" / "data.bin").exists())
            migrated = root / "mohu_zrm.custom.yaml"
            self.assertTrue(migrated.exists())
            self.assertIn("schema: mohu_zrm", migrated.read_text(encoding="utf-8"))
            self.assertTrue((root / "mohu_zrm_tiger_prefix2.userdb" / "data.bin").exists())
            self.assertFalse(custom.exists())
            self.assertFalse(userdb.exists())

    def test_unknown_legacy_reference_blocks_all_writes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "user.yaml"
            original = "patch:\n  custom: moran_unknown_database\n"
            source.write_text(original, encoding="utf-8")
            plan = plan_migration(root)

            self.assertTrue(plan.unknown_references)
            with self.assertRaisesRegex(ValueError, "unknown legacy references"):
                apply_migration(root, plan, timestamp="20260815-120000")
            self.assertEqual(original, source.read_text(encoding="utf-8"))
            self.assertFalse((root / "mohu-migration-backup-20260815-120000").exists())


if __name__ == "__main__":
    unittest.main()
