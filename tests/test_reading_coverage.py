"""词码读音覆盖审计：词表编码使用的 (字, 音节) 必须能被引擎单字码表覆盖。

背景（2026-09-08 不得了/budelc 修复）：词库按词典注音给多音字词生成规范码
（不得了=bude lc），但引擎整句码表若缺该字-音节行（如「了」liǎo），整词在
全拼输入下就不可达，首选被「不得聊」类非词占据。词权越高的缺读影响越大，
本测试把「最高词权 ≥ READING_COVERAGE_MIN_WEIGHT 的 (字, 音节)」设为硬性
回归线；低权尾巴只汇总打印，供后续批量清理。

2026-09-21 万象改真实词权后新增两类豁免（均有独立依据，不是放松门槛）：
1. 引擎未收录字（chars.txt 简频=0 被有意排除的繁体/生僻字）：引擎字集是
   另一项策略决定，这些词用编码仍可打出，与 weight-20 时代行为一致；
2. tools/data/wanxiang/cuoyin_words.txt 里的错音词（东庠 dong yang 类）：
   错音表按设计携带非规范读音供打错音出词，引擎不应学习。
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LEXICON = ROOT / "tiger_sentence_native" / "mohu_tiger.lexicon.txt"
CUOYIN_WORDS = ROOT / "tools/data/wanxiang/cuoyin_words.txt"
TABLES = [
    "tools/data/lexicon_sources/zrm/mohu_zrm.base.dict.yaml",
    "tools/data/lexicon_sources/zrm/mohu_zrm.words.dict.yaml",
    "tools/data/lexicon_sources/zrm/mohu_zrm.tencent.dict.yaml",
    "tools/data/lexicon_sources/zrm/mohu_zrm.moe.dict.yaml",
    "tools/data/lexicon_sources/zrm/mohu_zrm.classics.dict.yaml",
    "tools/data/lexicon_sources/zrm/mohu_zrm.wanxiang.dict.yaml",
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


def _load_engine_chars() -> set[str]:
    chars: set[str] = set()
    for line in LEXICON.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) < 2 or fields[0].startswith("#"):
            continue
        code, text = fields[0], fields[1]
        if len(text) == 1 and len(code) >= 2:
            chars.add(text)
    return chars


def _load_cuoyin_words() -> set[str]:
    if not CUOYIN_WORDS.is_file():
        return set()
    return {
        line.strip()
        for line in CUOYIN_WORDS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _declared_memory_aliases() -> set[tuple[str, str]]:
    """A configured memory-code alias is not a new Mandarin pronunciation.

    Derive only exact four-key -> two-key rules already declared by the schema,
    and identify their characters in the actual master table. No hardcoded
    character or reading whitelist is introduced.
    """
    config = yaml.safe_load((ROOT / "mohu.yaml").read_text())
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)

    aliases = {}
    for rule in strings(config.get("algebra", {})):
        match = re.fullmatch(r"derive/\^([a-z]{4})\$?/([a-z]{2})/", rule)
        if match:
            aliases[match.group(1)] = match.group(2)
    result = set()
    for line in (ROOT / "mohu_zrm.dict.yaml").read_text().splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and len(fields[0]) == 1 and fields[1] in aliases:
            result.add((fields[0], aliases[fields[1]]))
    return result


def _collect_missing() -> dict[tuple[str, str], int]:
    """返回 (字, 音节) -> 使用该缺读的最高词权。"""
    covered = _load_covered_syllables()
    engine_chars = _load_engine_chars()
    cuoyin_words = _load_cuoyin_words()
    memory_aliases = _declared_memory_aliases()
    missing: dict[tuple[str, str], int] = {}
    for name in TABLES:
        path = ROOT / name
        if not path.exists():
            continue
        header, body = path.read_text(encoding="utf-8").split("\n...\n", 1)
        columns = yaml.safe_load(header).get("columns", ["text", "code", "weight"])
        positions = {key: columns.index(key) for key in ("text", "code", "weight")}
        for line in body.splitlines():
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) <= max(positions.values()):
                continue
            word, code = fields[positions["text"]], fields[positions["code"]]
            if word in cuoyin_words:
                continue
            try:
                weight = int(float(fields[positions["weight"]] or 0))
            except ValueError:
                continue
            syllables = [tok.split(";", 1)[0] for tok in code.split()]
            if len(syllables) != len(word):
                continue
            for char, syllable in zip(word, syllables):
                if char not in engine_chars:
                    continue
                if (char, syllable) not in covered and (char, syllable) not in memory_aliases:
                    key = (char, syllable)
                    if weight > missing.get(key, -1):
                        missing[key] = weight
    return missing


class WordReadingCoverageTest(unittest.TestCase):
    def test_memory_aliases_are_derived_from_existing_rules_and_master(self):
        self.assertIn(("未", "we"), _declared_memory_aliases())
        self.assertNotIn(("饭", "fa"), _declared_memory_aliases())

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
