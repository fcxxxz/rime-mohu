"""Self-contained sentence dictionaries and an explicit runtime package contract."""
import tempfile
import unittest
from pathlib import Path

import yaml

from tools import build_sentence_dictionary as builder
from tools import build_split_dist

ROOT = Path(__file__).resolve().parents[1]


class SentenceDictionaryTest(unittest.TestCase):
    def test_merge_preserves_mixed_columns_empty_codes_weights_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chars = root / 'chars.dict.yaml'
            chars.write_text('---\nname: chars\n...\n式\tui;pu\t600521\n')
            words = root / 'words.dict.yaml'
            words.write_text('---\nname: words\ncolumns: [text, weight, code]\n...\n测试\t7\tce;sd ui;sp\n未编码\t1\n无权重\n')
            duplicate = root / 'duplicate.dict.yaml'
            duplicate.write_text('---\nname: duplicate\n...\n式\tui;pu\t600521\n')
            output = root / 'merged.dict.yaml'
            builder.merge_dictionary('mohu_zrm.words', [chars, words, duplicate], output)
            first = output.read_bytes()
            builder.merge_dictionary('mohu_zrm.words', [chars, words, duplicate], output)
            self.assertEqual(first, output.read_bytes())
            header, body = first.decode().split('\n...\n', 1)
            meta = yaml.safe_load(header)
            self.assertNotIn('import_tables', meta)
            self.assertEqual(meta['columns'], ['text', 'code', 'weight'])
            self.assertEqual(meta['name'], 'mohu_zrm.words')
            rows = [line for line in body.splitlines() if line and not line.startswith('#')]
            self.assertEqual(rows, ['式\tui;pu\t600521', '测试\tce;sd ui;sp\t7', '未编码\t\t1', '无权重', '式\tui;pu\t600521'])

    def test_packager_uses_explicit_scheme_files_not_a_prefix_glob(self):
        text = (ROOT / 'tools/build_split_dist.py').read_text()
        self.assertNotIn('ROOT.glob(f"mohu_{scheme}*")', text)
        expected = ('mohu_flypy.schema.yaml', 'mohu_flypy_sentence_core.schema.yaml',
                    'mohu_flypy.dict.yaml', 'mohu_flypy.words.dict.yaml',
                    'mohu_flypy_custom_phrases.txt')
        self.assertEqual(build_split_dist.scheme_root_paths('flypy'), expected)

    def test_schemas_read_merged_sentence_library(self):
        for scheme in ('zrm', 'flypy'):
            config = yaml.safe_load((ROOT / f'mohu_{scheme}.schema.yaml').read_text())
            for namespace in ('smart', 'smart_static'):
                self.assertEqual(config[namespace]['dictionary'], f'mohu_{scheme}.words')
            self.assertFalse((ROOT / f'mohu_{scheme}.extended.dict.yaml').exists())


if __name__ == '__main__':
    unittest.main()
