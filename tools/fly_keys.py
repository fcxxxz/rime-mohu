#!/usr/bin/env python3
"""飞键替换对的单一事实源。

所有需要知道飞键集合的代码都从这里导入：
- tools/sync_flykey_quickcodes.py（fixed 词典飞键块生成）
- tools/build_flypy_assets.py（小鹤词典飞键块再生成）
- tools/build_mohu_lexicons.py（原生整句词表飞键闭包）
- tools/fix_tiger_lexicon_fly.py（源码表飞键行补齐）
- tools/sync_flykey_config.py（把配置同步到 Rime YAML）

用户可编辑 ``tools/data/mohu_fly_keys.tsv`` 增删替换对。该文件只影响
部署期生成：``make quick`` 会把同一配置同步到 Rime algebra、fixed
飞键区块和 Tiger 原生词表；运行时不读取此文件，也不会增加查询次数。

方向约定：source -> target。词典音节编码为 source（如 wz;），用户飞键
输入 target（如 wk;）。target 应是空闲伪音节；添加新对前应确认两个
方案的字符词典都没有把 target 当作正常音节使用。
"""

from __future__ import annotations

from pathlib import Path
import re


CONFIG_PATH = Path(__file__).resolve().parent / "data" / "mohu_fly_keys.tsv"
_SYLLABLE = re.compile(r"[a-z]{2}\Z")
_SCHEMES = ("zrm", "flypy")


def load_pairs(path: Path = CONFIG_PATH) -> dict[str, dict[str, str]]:
    """Load ordered scheme mappings from the deploy-time TSV configuration."""
    result: dict[str, dict[str, str]] = {scheme: {} for scheme in _SCHEMES}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3 or any(not field for field in fields):
            raise ValueError(
                f"invalid fly-key row at {path}:{line_number}: "
                "expected scheme<TAB>source<TAB>target"
            )
        scheme, source, target = fields
        if scheme not in result:
            raise ValueError(
                f"unsupported fly-key scheme at {path}:{line_number}: {scheme}"
            )
        if not _SYLLABLE.fullmatch(source) or not _SYLLABLE.fullmatch(target):
            raise ValueError(
                f"invalid fly-key syllable at {path}:{line_number}: "
                f"{source!r}->{target!r}"
            )
        if source == target:
            raise ValueError(
                f"fly-key source and target must differ at {path}:{line_number}"
            )
        pairs = result[scheme]
        if source in pairs:
            raise ValueError(f"duplicate fly-key source at {path}:{line_number}: {source}")
        if target in pairs.values():
            raise ValueError(f"duplicate fly-key target at {path}:{line_number}: {target}")
        pairs[source] = target

    # A target that is also a source would make the meaning of a block label
    # depend on rule order. Keep the deploy-time format deliberately explicit.
    for scheme, pairs in result.items():
        chained = sorted(set(pairs).intersection(pairs.values()))
        if chained:
            raise ValueError(
                f"fly-key mappings may not chain in {scheme}: {', '.join(chained)}"
            )
    return result


_PAIRS = load_pairs()
FLY_ZRM: dict[str, str] = _PAIRS["zrm"]
FLY_FLYPY: dict[str, str] = _PAIRS["flypy"]


def scheme_pairs(scheme: str) -> dict[str, str]:
    if scheme == "zrm":
        return FLY_ZRM
    if scheme == "flypy":
        return FLY_FLYPY
    raise ValueError(f"unsupported double-pinyin scheme: {scheme}")


def fly_closure(
    syllables: tuple[str, ...], fly: dict[str, str]
) -> set[tuple[str, ...]]:
    """Return every positional fly-key variant except the original tuple.

    Each occurrence is an independent position. Consequently two identical
    source syllables produce the partial combinations as well as the full
    replacement (``qxqx`` -> ``qoqx``, ``qxqo``, ``qoqo``).
    """
    if not syllables:
        return set()
    if any(not _SYLLABLE.fullmatch(value) for value in syllables):
        raise ValueError(f"invalid syllable tuple: {syllables!r}")
    seen = {syllables}
    frontier = [syllables]
    while frontier:
        current = frontier.pop()
        for index, value in enumerate(current):
            target = fly.get(value)
            if target is None:
                continue
            variant = current[:index] + (target,) + current[index + 1 :]
            if variant not in seen:
                seen.add(variant)
                frontier.append(variant)
    seen.remove(syllables)
    return seen


def bare_code_syllable_slots(word: str, code: str) -> tuple[int, ...]:
    """Return complete-syllable positions represented by a fixed bare code.

    Fixed dictionaries have no ``;`` separators, so only the established
    shapes are unambiguous: one-character codes use the first syllable;
    two-character words use one or two syllables for two- or four-code forms;
    three-character four-code forms use the final syllable. Initial-letter
    abbreviations and four-or-more-character abbreviations are left alone.
    """
    if len(word) == 1:
        return (0,) if len(code) >= 2 else ()
    if len(word) == 2:
        if len(code) < 2:
            return ()
        return (0, 1) if len(code) == 4 else (0,)
    if len(word) == 3 and len(code) == 4:
        return (1,)
    return ()


def fly_variants(
    word: str, code: str, fly: dict[str, str]
) -> list[tuple[str, tuple[str, str]]]:
    """Return fixed-code variants and their first differing fly-key pair."""
    slots = bare_code_syllable_slots(word, code)
    if not slots:
        return []
    base = tuple(code[2 * slot : 2 * slot + 2] for slot in slots)

    def assemble(syllables: tuple[str, ...]) -> str:
        chars = list(code)
        for slot, syllable in zip(slots, syllables):
            chars[2 * slot : 2 * slot + 2] = syllable
        return "".join(chars)

    variants: list[tuple[str, tuple[str, str]]] = []
    for merged in fly_closure(base, fly):
        new_code = assemble(merged)
        changed = next(
            index
            for index, (old, new) in enumerate(zip(base, merged))
            if old != new
        )
        variants.append((new_code, (base[changed], fly[base[changed]])))
    return sorted(variants)


def full_syllable_variants(
    code: str, text: str, fly: dict[str, str]
) -> set[str]:
    """Return variants for a complete native lexicon code.

    Unlike fixed bare codes, native rows are eligible only when the prefix has
    exactly one two-letter syllable per character. Any auxiliary or suffix
    after that prefix is preserved verbatim.
    """
    if not text or len(code) < 2 * len(text):
        return set()
    prefix = code[: 2 * len(text)]
    if not re.fullmatch(r"[a-z]{2}(?:[a-z]{2})*", prefix):
        return set()
    base = tuple(prefix[index : index + 2] for index in range(0, len(prefix), 2))
    suffix = code[2 * len(text) :]
    return {"".join(variant) + suffix for variant in fly_closure(base, fly)}
