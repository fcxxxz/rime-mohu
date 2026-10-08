"""The editable character/word table must be the runtime source of truth."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from tools.check_code_table import check

ROOT = Path(__file__).resolve().parents[1]


def dictionary(path):
    header, body = path.read_text(encoding='utf-8').split('\n...\n', 1)
    return yaml.safe_load(header), [line.split('\t') for line in body.splitlines()
                                   if line and not line.startswith('#') and '\t' in line]


class UnifiedDictionaryTest(unittest.TestCase):
    def test_master_contains_characters_words_and_multiple_codes(self):
        meta, rows = dictionary(ROOT / 'mohu_zrm.dict.yaml')
        self.assertEqual(meta['name'], 'mohu_zrm')
        self.assertEqual(meta['sort'], 'original')
        self.assertEqual(meta['columns'], ['text', 'code'])
        pairs = {(r[0], r[1]) for r in rows}
        self.assertIn(('式', 'uipu'), pairs)
        self.assertNotIn(('式', 'uig'), pairs)
        self.assertIn(('哪里', 'nal'), pairs)
        self.assertIn(('方式', 'fhui'), pairs)
        self.assertIn(('？', ';w'), pairs)
        self.assertIn(('试', 'u'), pairs)
        self.assertIn(('试', 'uisp'), pairs)
        self.assertIn(('师', 'uipf'), pairs)
        self.assertEqual([r[0] for r in rows if r[1] == 'we'][:2], ['未', '万恶'])
        self.assertEqual([r[0] for r in rows if r[1] == 'wf'][:2], ['问', '文'])
        self.assertTrue(all(len(r) == 2 for r in rows))
        self.assertIs(meta["use_preset_vocabulary"], False)

    def test_flypy_output_has_only_text_and_code(self):
        meta, rows = dictionary(ROOT / 'mohu_flypy.dict.yaml')
        self.assertEqual(meta['columns'], ['text', 'code'])
        self.assertTrue(all(len(r) == 2 for r in rows))

    def test_validator_accepts_two_columns_without_changing_source(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'mohu_zrm.dict.yaml'
            path.write_text("---\nname: mohu_zrm\nsort: original\ncolumns: [text, code]\n...\n是\tu\n试\tu\n")
            before = path.read_bytes()
            self.assertEqual(check(path), 2)
            self.assertEqual(path.read_bytes(), before)

    def test_validator_rejects_weight_column_in_body(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'mohu_zrm.dict.yaml'
            path.write_text("---\nname: mohu_zrm\nsort: original\ncolumns: [text, code]\n...\n试\tu\t100\n")
            with self.assertRaisesRegex(ValueError, "只允许两列"):
                check(path)

    def test_runtime_has_one_code_table_and_no_mode_switch(self):
        for scheme in ('zrm', 'flypy'):
            config = yaml.safe_load((ROOT / f'mohu_{scheme}.schema.yaml').read_text())
            self.assertNotIn('multi_short_code', [s.get('name') for s in config['switches']])
            self.assertEqual(config['translator']['dictionary'], f'mohu_{scheme}')
            self.assertNotIn('fixed', config)
            self.assertNotIn('fixed_legacy', config)
            shim = yaml.safe_load((ROOT / f'mohu_{scheme}_sentence_core.schema.yaml').read_text())
            self.assertIn('script_translator', shim['engine']['translators'])
            self.assertEqual(shim['translator']['dictionary'], f'mohu_{scheme}.words')
        for name in ('mohu_express_translator.lua', 'mohu_contextual_translator.lua', 'option_sync.lua'):
            self.assertNotIn('multi_short_code', (ROOT / 'lua' / name).read_text())

    def test_public_injection_settings_use_table_names(self):
        for scheme in ('zrm', 'flypy'):
            config = yaml.safe_load((ROOT / f'mohu_{scheme}.schema.yaml').read_text())
            self.assertTrue(config['mohu']['inject_table_chars'])
            self.assertTrue(config['mohu']['inject_table_words'])
            self.assertNotIn('inject_fixed_chars', config['mohu'])
            self.assertNotIn('inject_fixed_words', config['mohu'])
            shim = (ROOT / f'mohu_{scheme}_sentence_core.schema.yaml').read_text()
            self.assertNotIn('inject_fixed_', shim)
        generator = (ROOT / 'tools/build_flypy_assets.py').read_text()
        self.assertNotIn('fixed', generator)

    def test_retired_allocation_inputs_and_tables_are_removed(self):
        for pattern in ('mohu_*fixed*.dict.yaml', 'mohu_*fixed*.schema.yaml'):
            self.assertEqual(list(ROOT.glob(pattern)), [])
        for name in ('mohu_fixed_code_claims.tsv', 'mohu_fixed_secondary_codes.tsv',
                     'mohu_fixed_char_code_overrides.tsv', 'mohu_fixed_simp_legacy_chars.txt',
                     'tiger_race_profile.tsv'):
            self.assertFalse((ROOT / 'tools/data' / name).exists())
        self.assertFalse((ROOT / 'tools/rebuild_fixed_tiger.py').exists())
        self.assertFalse((ROOT / 'tools/fixed_tiger_allocation.py').exists())

    def test_build_has_no_allocator_or_master_rewrite(self):
        text = (ROOT / 'Makefile').read_text()
        self.assertNotIn('fixed_tiger', text)
        self.assertNotIn('sync_flykey_quickcodes.py --apply', text)


if __name__ == '__main__':
    unittest.main()
