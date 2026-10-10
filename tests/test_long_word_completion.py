"""Master-table long phrases must reach both sentence completion dictionaries."""
import tempfile
import unittest
from pathlib import Path

import yaml

from tools import build_sentence_dictionary as merger

ROOT = Path(__file__).resolve().parents[1]


class LongWordCompletionTest(unittest.TestCase):
    def fixture(self, root):
        folder = root / 'tools/data/lexicon_sources/zrm'
        folder.mkdir(parents=True)
        (root / 'mohu_zrm.dict.yaml').write_text(
            '---\ncolumns: [text, code]\n...\n先射箭后画靶\txujb\n'
            '先射箭后画靶\txujb\n先射箭，后画靶\txujb\n'
            '办理银行账户\tblzh\n已有完整长词\tyywc\n'
            '网址https://a\turlx\n未知𰻞𰻞长词\twwic\n四字短词\tszdc\n')
        chars = {'先': 'xm;qp', '射': 'ue;zk', '箭': 'jm;rh', '后': 'hb;xf',
                 '画': 'hw;fq', '靶': 'ba;tm', '办': 'bj;sl', '理': 'li;nd',
                 '银': 'yn;zi', '行': 'xy;px', '账': 'vh;bp', '户': 'hu;du'}
        (folder / 'mohu_zrm.chars.dict.yaml').write_text(
            '---\nname: chars\n...\n' + ''.join(f'{c}\t{code}\t10\n' for c, code in chars.items())
            + '行\thh;px\t9\n')
        (folder / 'mohu_zrm.base.dict.yaml').write_text(
            '---\nname: base\n...\n银行\tyn;zi hh;px\t100\n'
            '已有完整长词\tyi;fi yb;nv wj;qp vg;qp ih;pc ci;sd\t88\n')
        return folder

    def test_missing_long_phrase_is_derived_with_full_spelling_and_neutral_weight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            rows, report = merger.master_long_word_rows(root)
            self.assertIn(('先射箭后画靶', 'xm;qp ue;zk jm;rh hb;xf hw;fq ba;tm', '1'), rows)
            self.assertIn(('先射箭，后画靶', 'xm;qp ue;zk jm;rh hb;xf hw;fq ba;tm', '1'), rows)
            self.assertEqual(sum(r[0] == '先射箭后画靶' for r in rows), 1)
            self.assertNotIn('已有完整长词', {r[0] for r in rows})
            self.assertNotIn('四字短词', {r[0] for r in rows})
            self.assertTrue(report['unresolved'])
            self.assertTrue(all('https' not in r[0] for r in rows))
            self.assertEqual((rows, report), merger.master_long_word_rows(root))

    def test_existing_term_readings_override_single_character_pinyin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            rows, _ = merger.master_long_word_rows(root)
            code = next(code for text, code, _ in rows if text == '办理银行账户')
            self.assertIn('yn;zi hh;px', code)
            self.assertNotIn('yn;zi xy;px', code)

    def test_supplemental_rows_participate_in_hash_and_preserve_all_source_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.yaml'
            source.write_text('---\nname: source\n...\n先\txm;qp\t10\n')
            output = root / 'merged.yaml'
            merger.merge_dictionary('mohu_zrm.words', [source], output)
            before = output.read_text()
            extra = [('先射箭后画靶', 'xm;qp ue;zk jm;rh hb;xf hw;fq ba;tm', '1')]
            merger.merge_dictionary('mohu_zrm.words', [source], output, supplemental_rows=extra)
            self.assertIn('先\txm;qp\t10\n', output.read_text())
            self.assertIn('\t'.join(extra[0]), output.read_text())
            self.assertNotEqual(before.split('version: ')[1][:14], output.read_text().split('version: ')[1][:14])
            first = output.read_bytes()
            merger.merge_dictionary('mohu_zrm.words', [source], output, supplemental_rows=extra)
            self.assertEqual(first, output.read_bytes())

    def test_both_schemes_use_the_same_derived_phrases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            for scheme in ('zrm', 'flypy'):
                dest = root / 'tools/data/lexicon_sources' / scheme
                dest.mkdir(exist_ok=True)
                for suffix in merger.SOURCE_SUFFIXES:
                    path = dest / f'mohu_{scheme}.{suffix}.dict.yaml'
                    if not path.exists():
                        path.write_text('---\nname: source\n...\n')
            merger.build(root)
            self.assertIn('先射箭后画靶\txm;qp ue;zk jm;rh hb;xf hw;fq ba;tm\t1',
                          (root / 'mohu_zrm.words.dict.yaml').read_text())
            self.assertIn('先射箭后画靶\txm;qp ue;zk jm;rh hz;xf hx;fq ba;tm\t1',
                          (root / 'mohu_flypy.words.dict.yaml').read_text())

    def test_completion_limit_is_wired_after_user_overrides_in_both_schemes(self):
        for scheme in ('zrm', 'flypy'):
            schema = yaml.safe_load((ROOT / f'mohu_{scheme}.schema.yaml').read_text())
            filters = schema['engine']['filters']
            limiter = filters.index('lua_filter@*mohu_completion_filter')
            self.assertGreater(limiter, filters.index('lua_filter@*mohu_candidate_override*override_filter'))
            self.assertLess(limiter, filters.index('lua_filter@*mohu_hint_filter'))
            self.assertEqual(schema['mohu']['word_completion_limit'], 3)


if __name__ == '__main__':
    unittest.main()
