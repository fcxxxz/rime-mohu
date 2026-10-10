"""Derive full spellings for master-table long phrases missing from the corpus.

This is sentence-dictionary data, never a rewrite of editable short codes.
Existing phrase readings win; missing phrases use known component readings and
validated pypinyin fallbacks. No unknown character or guessed auxiliary is emitted.
"""
from __future__ import annotations

import math
from collections.abc import Iterator
from itertools import product
from pathlib import Path

import regex
import yaml
from pypinyin import lazy_pinyin

try:
    from . import flypyify, zrmify
except ImportError:
    import flypyify
    import zrmify

HAN = regex.compile(r'\p{Han}')
PHRASE = regex.compile(r'[\p{Han}，。！？、；：,.!?;:…“”‘’（）()《》\s]+')
TOKEN = regex.compile(r'[a-z]{2};[a-z]+')


def dictionary_rows(path: Path) -> Iterator[tuple[str, str, str]]:
    with path.open(encoding='utf-8') as source:
        header = []
        for line in source:
            if line.strip() == '...':
                break
            header.append(line)
        else:
            raise ValueError(f'missing dictionary body: {path}')
        meta = yaml.safe_load(''.join(header))
        columns = meta.get('columns', ['text', 'code', 'weight'])
        positions = {key: columns.index(key) for key in ('text', 'code', 'weight') if key in columns}
        for line in source:
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            parts = line.rstrip('\r\n').split('\t')
            yield tuple(parts[positions[key]] if key in positions and positions[key] < len(parts) else ''
                        for key in ('text', 'code', 'weight'))


def bare(text: str) -> str:
    return ''.join(HAN.findall(text))


def master_long_word_rows(root: Path, sources: list[Path]) -> tuple[list[tuple[str, str, str]], dict]:
    master = root / 'mohu_zrm.dict.yaml'
    targets = dict.fromkeys(text for text, _, _ in dictionary_rows(master)
                            if PHRASE.fullmatch(text) and len(bare(text)) > 4)
    wanted = {text[i:j] for value in targets for text in [bare(value)]
              for i in range(len(text)) for j in range(i + 2, len(text) + 1)}
    chars: dict[str, dict[str, tuple[int, str]]] = {}
    terms: dict[str, tuple[int, tuple[str, ...]]] = {}
    phrase_rows: dict[str, tuple[int, tuple[str, ...]]] = {}
    char_aliases: dict[str, set[str]] = {}
    source_pairs: set[tuple[str, str]] = set()
    present = set()
    for source in sources:
        if not source.exists():
            continue
        for text, code, weight in dictionary_rows(source):
            source_pairs.add((text, code))
            if text in targets:
                present.add(text)
            letters = bare(text)
            tokens = tuple(code.split())
            if len(text) == 1 and len(letters) == 1 and len(tokens) == 1 and TOKEN.fullmatch(tokens[0]):
                score = int(weight or 0)
                readings = chars.setdefault(text, {})
                syllable = tokens[0][:2]
                if syllable != 'pp' and (syllable not in readings or score > readings[syllable][0]):
                    readings[syllable] = (score, tokens[0])
                if source.name.endswith('.words.dict.yaml') and score > 0:
                    char_aliases.setdefault(text, set()).add(tokens[0])
                continue
            if len(letters) > 4 and len(tokens) == len(letters) and all(TOKEN.fullmatch(t) for t in tokens):
                score = int(weight or 0)
                previous = phrase_rows.get(text)
                if previous is None or score > previous[0]:
                    phrase_rows[text] = (score, tokens)
            if letters not in wanted and len(text) != 1:
                continue
            if len(tokens) != len(letters) or not all(TOKEN.fullmatch(t) for t in tokens):
                continue
            score = int(weight or 0)
            if letters in wanted and (letters not in terms or score > terms[letters][0]):
                terms[letters] = (score, tokens)
    rows = []
    aliases = []
    unresolved = []
    fallback_words = []
    for text in targets:
        if text in present:
            continue
        letters = bare(text)
        readings = lazy_pinyin(letters)
        # DP favours coverage by recorded multi-character words, then fewer
        # chunks, then their recorded weights. Pinyin is only a single-char fallback.
        best = {len(letters): ((0, 0, 0.0), (), False)}
        for i in range(len(letters) - 1, -1, -1):
            choices = []
            for j in range(i + 2, len(letters) + 1):
                term = terms.get(letters[i:j])
                if term and j in best:
                    weight, tokens = term
                    tail_score, tail_tokens, fallback = best[j]
                    choices.append(((tail_score[0], tail_score[1] - 1,
                                     tail_score[2] + math.log1p(max(0, weight))),
                                    tokens + tail_tokens, fallback))
            try:
                syllable = zrmify.zrmify(readings[i])
            except (KeyError, ValueError):
                syllable = ''
            token = chars.get(letters[i], {}).get(syllable)
            if token and i + 1 in best:
                tail_score, tail_tokens, _ = best[i + 1]
                choices.append(((tail_score[0] - 1, tail_score[1] - 1, tail_score[2]),
                                (token[1],) + tail_tokens, True))
            if choices:
                best[i] = max(choices, key=lambda c: c[0])
        if 0 not in best:
            unresolved.append(text)
            continue
        _, tokens, fallback = best[0]
        rows.append((text, ' '.join(tokens), '1'))
        if fallback:
            fallback_words.append(text)
        phrase_rows[text] = (1, tokens)

    # Some characters have a maintained alternative full syllable, such as
    # 几: ji;oj and jo;oj.  Existing long phrases must inherit these aliases;
    # otherwise jo + the remaining exact syllables falls back to independent
    # short-code characters instead of reaching the phrase.  Cap the Cartesian
    # expansion so repeated variant characters cannot explode the dictionary.
    for text, (weight, tokens) in phrase_rows.items():
        letters = bare(text)
        choices = []
        for char, token in zip(letters, tokens):
            alternatives = sorted({token} | char_aliases.get(char, set()))
            choices.append([value for value in alternatives if value[:2] != 'pp'])
        variants = product(*choices)
        for variant in variants:
            code = ' '.join(variant)
            if code == ' '.join(tokens) or (text, code) in source_pairs:
                continue
            aliases.append((text, code, str(weight)))
            if len(aliases) >= 200_000:
                break
        if len(aliases) >= 200_000:
            break
    rows.extend(aliases)
    return rows, {'master_long_words': len(targets), 'already_in_corpus': len(present),
                  'derived': len(rows) - len(aliases), 'aliases': len(aliases),
                  'unresolved': unresolved, 'uses_character_fallback': fallback_words}


def flypy_rows(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    return [(text, ' '.join(flypyify.flypyify(zrmify.unzrmify(token[:2])) + token[2:]
                           for token in code.split()), weight) for text, code, weight in rows]
