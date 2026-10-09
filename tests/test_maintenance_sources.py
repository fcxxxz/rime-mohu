"""Derive policy from the editable table and share one Tiger decomposition source."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools import tiger_aux

ROOT = Path(__file__).resolve().parents[1]


class MasterCompatibilityTest(unittest.TestCase):
    def test_derives_only_explicit_single_character_full_compatibility_codes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'master.dict.yaml'
            path.write_text('---\nname: master\n...\n甲\taaaa\n甲\taaab\n甲\twkab\n乙\tbbac\n丙\tccad\n丁\tdda\n丁\tddaa\n戊\tda12\n双字\taaab\n？\t;w\n')
            aux = {
                '甲': tiger_aux.AuxiliaryEntry('aa', 'ab', 'ac'),
                '乙': tiger_aux.AuxiliaryEntry('bb'),
                '丙': tiger_aux.AuxiliaryEntry('cc', '', 'ad'),
                '丁': tiger_aux.AuxiliaryEntry('dd', 'da'),
                '戊': tiger_aux.AuxiliaryEntry('da', '12'),
            }
            self.assertEqual(tiger_aux.load_master_compatibility_characters(path, aux), {'甲', '丙'})
            # A manual main-table edit is sufficient; there is no second allowlist.
            path.write_text(path.read_text().replace('丙\tccad\n', ''))
            self.assertEqual(tiger_aux.load_master_compatibility_characters(path, aux), {'甲'})

    def test_repository_targets_are_explicit_master_compatibility_rows(self):
        targets = tiger_aux.load_master_compatibility_characters(
            ROOT / 'mohu_zrm.dict.yaml', tiger_aux.load_auxiliary_tsv(ROOT / 'tools/data/tiger_aux.txt'))
        self.assertIn('幕', targets)
        self.assertIn('臂', targets)
        self.assertNotIn('召', targets)  # its old list entry had no compatible auxiliary
        self.assertNotIn('式', targets)

    def test_obsolete_data_and_eager_loading_are_gone(self):
        for name in ('tiger_compatibility_chars.txt', 'mohu_chai.txt', 'simp_chars.txt'):
            self.assertFalse((ROOT / 'tools/data' / name).exists(), name)
        self.assertNotIn('chai_table', (ROOT / 'tools/utils.py').read_text())
        self.assertNotIn('mohu_chai.txt', (ROOT / 'tools/gen_mdx.py').read_text())
        rule = next(line for line in (ROOT / 'Makefile').read_text().splitlines() if line.startswith('mohu.mdx:'))
        self.assertIn('tiger_chaifen.txt', rule)


class TigerMdxTest(unittest.TestCase):
    def test_mdx_uses_tiger_auxiliary_and_decomposition(self):
        from tools import gen_mdx
        rows = gen_mdx.auxiliary_decompositions('式', tiger_aux.AuxiliaryEntry('pu'),
                                               {'式': '〔弋工 · pug〕'})
        page = gen_mdx.gen_page('式', ['ui'], rows)
        self.assertIn('ui;pu', page)
        self.assertIn('弋工', page)
        self.assertNotIn('ui;gy', page)
        self.assertNotIn('ui;yg', page)

    def test_mdx_keeps_compatible_codes_and_escapes_html(self):
        from tools import gen_mdx
        rows = gen_mdx.auxiliary_decompositions('甲', tiger_aux.AuxiliaryEntry('aa', 'ab'),
                                               {'甲': '<拆分&>'})
        page = gen_mdx.gen_page('甲', ['jw'], rows)
        self.assertIn('jw;aa', page)
        self.assertIn('jw;ab', page)
        self.assertIn('&lt;拆分&amp;&gt;', page)
        self.assertNotIn('<拆分&>', page)

    def test_missing_decomposition_keeps_char_and_codes_visible(self):
        from tools import gen_mdx
        rows = gen_mdx.auxiliary_decompositions('𖿲', tiger_aux.AuxiliaryEntry('pe'), {})
        self.assertEqual(rows, [('pe', '𖿲')])


if __name__ == '__main__':
    unittest.main()
