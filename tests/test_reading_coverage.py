"""词码读音覆盖审计：词表编码使用的 (字, 音节) 必须能被引擎单字码表覆盖。

背景（2026-09-08 不得了/budelc 修复）：词库按词典注音给多音字词生成规范码
（不得了=bude lc），但引擎整句码表若缺该字-音节行（如「了」liǎo），整词在
全拼输入下就不可达，首选被「不得聊」类非词占据。词权越高的缺读影响越大，
本测试把「最高词权 ≥ READING_COVERAGE_MIN_WEIGHT 的 (字, 音节)」设为硬性
回归线；低权尾巴只汇总打印，供后续批量清理。
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEXICON = ROOT / "tiger_sentence_native" / "mohu_tiger.lexicon.txt"
TABLES = [
    "mohu_zrm.base.dict.yaml",
    "mohu_zrm.words.dict.yaml",
    "mohu_zrm.tencent.dict.yaml",
    "mohu_zrm.computer.dict.yaml",
    "mohu_zrm.moe.dict.yaml",
    "mohu_zrm.classics.dict.yaml",
    "mohu_zrm.wanxiang.dict.yaml",
]
READING_COVERAGE_MIN_WEIGHT = 100


def _load_covered_syllables() -> set[tuple[str, str]]:
    covered: set[tuple[str, str]] = set()
    for line in LEXICON.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) < 2 or fields[0].startswith("#"):
            continue
        code, text = fields[0], fields[1]
        if len(text) == 1 and len(code) >= 2:
            covered.add((text, code[:2]))
    return covered


def _collect_missing() -> dict[tuple[str, str], int]:
    """返回 (字, 音节) -> 使用该缺读的最高词权。"""
    covered = _load_covered_syllables()
    missing: dict[tuple[str, str], int] = {}
    for name in TABLES:
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or line == "...":
                continue
            fields = line.split("\t")
            if len(fields) < 3:
                continue
            word, code = fields[0], fields[1]
            try:
                weight = int(fields[2] or 0)
            except ValueError:
                continue
            syllables = [tok.split(";", 1)[0] for tok in code.split()]
            if len(syllables) != len(word):
                continue
            for char, syllable in zip(word, syllables):
                if (char, syllable) not in covered:
                    key = (char, syllable)
                    if weight > missing.get(key, -1):
                        missing[key] = weight
    return missing


class WordReadingCoverageTest(unittest.TestCase):
    def test_high_weight_word_readings_are_reachable(self) -> None:
        missing = _collect_missing()
        severe = {
            key: weight for key, weight in missing.items()
            if weight >= READING_COVERAGE_MIN_WEIGHT
        }
        self.assertEqual(
            {}, severe,
            f"{len(severe)} 个词权≥{READING_COVERAGE_MIN_WEIGHT} 的 (字,音节) 缺引擎行，"
            f"整词全拼不可达。补齐方法：chars.txt/pinyin_simp.txt 激活读音 + "
            f"master 词表补行族（见 docs/reports/2026-09-08-reading-coverage-fix.md）。"
            f"最高权样例: {sorted(severe.items(), key=lambda kv: -kv[1])[:5]}")


if __name__ == "__main__":
    unittest.main()
