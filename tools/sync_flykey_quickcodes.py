#!/usr/bin/env python3
"""同步 fixed 词典的飞键简快码。

将词典末尾的飞键区块（# 开始飞键 ... # 结束飞键）与主区块对齐。
约定（由现有飞键区块数据推导验证）：

- 单字：码首双拼命中飞键音节即替换（如 位 wzj->wkj，权重列随源词条复制）；
- 二字词：code[:2] 与 code[2:4] 两个音节位，双位命中全部替换（如 切切 qxqx->qoqo）；
- 三字词：仅末字双拼位 code[2:4]（四码词条）；
- 四字及以上词为首字母组合，无整音节，不参与飞键；
- 实际替换对按各词典数据自动推导（小鹤组为 ww->wc、qp->qo，xq->xo 两组通用）；
- 同码组内顺序镜像主区块中对应主码组的顺序（如 qoji 组对齐 qxji 组）；
- 词词条格式 `词\t码`，单字格式 `字\t码\t\t权重`。

同步动作（最小扰动，不重排无关行）：

- 主区块可飞而飞键块缺失的词条 → 插入飞键块（同码组后，否则按码序位置）；
- 飞键块词条的主码已改 → 重写飞键码；
- 飞键块有而主区块完全没有的词（陈旧飞键）→ 在词库区补 `词\t反替换码`；
- 与主区块完全重复的飞键词条 → 删除；
- 同码组顺序与主码组不一致的 → 组内重排。

用法::

    uv run python tools/sync_flykey_quickcodes.py [--apply] mohu_zrm_fixed.dict.yaml ...

默认只打印报告，加 --apply 才写回。文件必须位于仓库内的 dict.yaml。
"""
import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

START = re.compile(r'^#\s*开始飞键\s*(\S+)\s*->\s*(\S+)')
END = re.compile(r'^#\s*结束飞键')
ENTRY = re.compile(r'^([^\t#]+)\t([a-z]+)\t*(.*)$')
CIKU_MARK = re.compile(r'^#-+词库-+#')

CODE_LEN = 4


def check_path(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != REPO_ROOT or resolved.suffixes[-2:] != ['.dict', '.yaml']:
        raise SystemExit(f'只允许仓库根目录下的 .dict.yaml: {path}')


def fly_positions(word, code, seg):
    """seg（双字母音节）可替换的位置：0=code[:2]，1=code[2:4]。"""
    if len(word) == 1:
        return [0] if len(code) >= 2 and code[:2] == seg else []
    if len(word) == 2:
        pos = []
        if code[:2] == seg:
            pos.append(0)
        if len(code) == CODE_LEN and code[2:4] == seg:
            pos.append(1)
        return pos
    if len(word) == 3:
        return [1] if len(code) == CODE_LEN and code[2:4] == seg else []
    return []


def fly_code(word, code, old, new):
    out = code
    for pos in fly_positions(word, code, old):
        out = out[:2 * pos] + new + out[2 * pos + 2:]
    return out


def unfly_code(word, code, old, new):
    out = code
    for pos in fly_positions(word, code, new):
        out = out[:2 * pos] + old + out[2 * pos + 2:]
    return out


def make_line(word, code, rest):
    return f'{word}\t{code}' + (f'\t\t{rest}' if rest else '')


def eligible(word, code, old):
    return bool(fly_positions(word, code, old))


def load_lines(path):
    lines = path.read_text(encoding='utf-8').split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return lines


def find_blocks(lines):
    blocks = []
    i = 0
    while i < len(lines):
        m = START.match(lines[i])
        if m:
            j = i + 1
            while j < len(lines) and not END.match(lines[j]):
                j += 1
            blocks.append({'start': i, 'end': j, 'label': (m.group(1), m.group(2))})
            i = j
        i += 1
    return blocks


def fly_entries_of(lines, b):
    out = []
    for idx in range(b['start'] + 1, b['end']):
        m = ENTRY.match(lines[idx])
        if m:
            out.append((idx, m.group(1), m.group(2), m.group(3)))
    return out


def parse_main(lines, blocks):
    fly_set = set()
    for b in blocks:
        fly_set.update(range(b['start'], b['end'] + 1))
    out = []
    for idx, line in enumerate(lines):
        if idx in fly_set:
            continue
        m = ENTRY.match(line)
        if m:
            out.append((idx, m.group(1), m.group(2), m.group(3)))
    return out


def derive_sub(fly_entries, main_by_word):
    """从既有二字词配对推导本块替换 (old, new)；不唯一返回 None。"""
    subs = Counter()
    for _, w, fc, _ in fly_entries:
        for mc in main_by_word.get(w, []):
            if len(mc) != CODE_LEN or len(fc) != CODE_LEN:
                continue
            segs = [(mc[i:i + 2], fc[i:i + 2]) for i in (0, 2)
                    if mc[i:i + 2] != fc[i:i + 2]]
            if segs:
                subs[tuple(segs)] += 1
    if not subs:
        return None
    segs = subs.most_common(1)[0][0]
    if len(set(segs)) != 1:
        return None
    return segs[0]


def analyze(lines):
    blocks = find_blocks(lines)
    main_entries = parse_main(lines, blocks)
    main_by_word = defaultdict(list)
    for ln, w, c, rest in main_entries:
        main_by_word[w].append(c)

    plan = {'blocks': blocks, 'fly_add': defaultdict(list), 'fly_remove': [],
            'fly_rewrite': [], 'main_add': [], 'manual': []}

    for bi, b in enumerate(blocks):
        fly_entries = fly_entries_of(lines, b)
        b['fly_entries'] = fly_entries
        b['sub'] = derive_sub(fly_entries, main_by_word)
        if b['sub'] is None:
            plan['manual'].append(f'块 {b["label"]} 无法推导替换，跳过')
            continue
        old, new = b['sub']

        expected = defaultdict(dict)   # word -> {flycode: (order, rest)}
        for order, (ln, w, c, rest) in enumerate(main_entries):
            if eligible(w, c, old):
                expected[w].setdefault(fly_code(w, c, old, new), (order, rest))
        actual = defaultdict(set)
        for ln, w, fc, rest in fly_entries:
            actual[w].add(fc)

        for w, codes in expected.items():
            for c, (order, rest) in codes.items():
                if c not in actual.get(w, set()):
                    plan['fly_add'][bi].append((w, c, rest, order))

        for ln, w, fc, rest in fly_entries:
            if w in expected and fc in expected[w]:
                continue
            if fc in set(main_by_word.get(w, [])):
                plan['fly_remove'].append((bi, w, fc))
            elif w in expected:
                nc = sorted(expected[w])[0]
                plan['fly_rewrite'].append((bi, w, fc, nc, expected[w][nc][1]))
            elif unfly_code(w, fc, old, new) != fc:
                plan['main_add'].append((w, unfly_code(w, fc, old, new), rest))
            else:
                plan['manual'].append(f'块 {b["label"]}: {w} {fc} 无法反推主码，请人工确认')

        # 同码组顺序镜像主码组
        main_group = defaultdict(list)   # maincode -> [word...]（主区块出现序）
        for ln, w, c, rest in main_entries:
            main_group[c].append(w)
        b['order_groups'] = []
        fly_by_code = defaultdict(list)
        for ln, w, fc, rest in fly_entries:
            fly_by_code[fc].append(w)
        for fc, ws in fly_by_code.items():
            mc = unfly_code(ws[0], fc, old, new)
            ref = {w: i for i, w in enumerate(main_group.get(mc, []))}
            known = [w for w in ws if w in ref]
            if len(known) > 1 and known != sorted(known, key=lambda w: ref[w]):
                b['order_groups'].append(fc)
    return plan


def insert_main(out, blocks, word, code, rest):
    """在词库区插入主词条：同码组末尾，否则按码序位置。"""
    ciku_start = 0
    for idx, line in enumerate(out):
        if CIKU_MARK.match(line):
            ciku_start = idx + 1
            break
    fly0 = min(b['start'] for b in blocks)
    entry = make_line(word, code, rest)
    last_same = None
    first_greater = None
    for idx in range(ciku_start, fly0):
        m = ENTRY.match(out[idx])
        if not m:
            continue
        c = m.group(2)
        if c == code:
            last_same = idx
        elif c > code and first_greater is None:
            first_greater = idx
            break
    pos = (last_same + 1) if last_same is not None else (
        first_greater if first_greater is not None else fly0)
    out.insert(pos, entry)
    for b in blocks:
        if pos <= b['start']:
            b['start'] += 1
            b['end'] += 1
    return pos


def rebuild(lines, plan, report):
    out = list(lines)
    # 1) 主区块补词
    for w, c, rest in sorted(set(plan['main_add'])):
        pos = insert_main(out, plan['blocks'], w, c, rest)
        report.append(f'  主区块 + {w} {c} @line{pos + 1}')

    # 2) 飞键块（基于最新行号重新定位）
    for bi, b in enumerate(plan['blocks']):
        if b.get('sub') is None:
            continue
        blocks_now = find_blocks(out)
        bnow = blocks_now[bi]
        old, new = b['sub']
        entries = fly_entries_of(out, bnow)
        # 2a) 删除与改码
        removes = {(w, fc) for k, w, fc in plan['fly_remove'] if k == bi}
        rewrites = {(w, fc): (nc, rest) for k, w, fc, nc, rest in plan['fly_rewrite']
                    if k == bi}
        for idx, w, fc, rest in entries:
            if (w, fc) in removes:
                out[idx] = None
            elif (w, fc) in rewrites:
                nc, nrest = rewrites[(w, fc)]
                out[idx] = make_line(w, nc, nrest)
        # 2b) 同码组顺序对齐主码组（整行搬移，保留权重列）
        main_group = defaultdict(list)
        for ln, w, c, rest in parse_main(out, blocks_now):
            main_group[c].append(w)
        fly_by_code = defaultdict(list)
        for idx, w, fc, rest in fly_entries_of(out, bnow):
            fly_by_code[fc].append((idx, w))
        for fc in b.get('order_groups', []):
            span = fly_by_code[fc]
            mc = unfly_code(span[0][1], fc, old, new)
            ref = {w: i for i, w in enumerate(main_group.get(mc, []))}
            orig = {idx: out[idx] for idx, _ in span}
            ranked = sorted(span, key=lambda t: ref.get(t[1], 10 ** 9))
            for (slot_idx, _), (src_idx, _) in zip(span, ranked):
                out[slot_idx] = orig[src_idx]
        # 2c) 插入缺失词条
        for w, c, rest, order in sorted(plan['fly_add'].get(bi, []),
                                        key=lambda x: (x[1], x[3])):
            body = [i for i in range(bnow['start'] + 1, bnow['end'])
                    if ENTRY.match(out[i] or '')]
            last_same = None
            first_greater = None
            for i in body:
                m = ENTRY.match(out[i])
                ec = m.group(2)
                if ec == c:
                    last_same = i
                elif ec > c and first_greater is None:
                    first_greater = i
                    break
            if last_same is not None:
                pos = last_same + 1
            elif first_greater is not None:
                pos = first_greater
            else:
                pos = bnow['end']
            out.insert(pos, make_line(w, c, rest))
            report.append(f'  飞键 + {make_line(w, c, rest)}')
            for bb in plan['blocks']:
                if pos <= bb['start']:
                    bb['start'] += 1
                    bb['end'] += 1
            bnow = find_blocks(out)[bi]
        # 清理已删除的行（从后往前）
        for idx in range(bnow['end'] - 1, bnow['start'], -1):
            if out[idx] is None:
                del out[idx]
                for bb in plan['blocks']:
                    if idx < bb['start']:
                        bb['start'] -= 1
                        bb['end'] -= 1
                bnow = find_blocks(out)[bi]
    return [x for x in out if x is not None]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true', help='写回文件（默认仅报告）')
    ap.add_argument('dicts', nargs='+')
    args = ap.parse_args()

    for raw in args.dicts:
        path = Path(raw)
        check_path(path)
        lines = load_lines(path)
        plan = analyze(lines)
        print(f'== {path.name}')
        for bi, b in enumerate(plan['blocks']):
            old, new = b['sub'] if b.get('sub') else ('?', '?')
            n_add = len(plan['fly_add'].get(bi, []))
            n_rm = sum(1 for k, *_ in plan['fly_remove'] if k == bi)
            n_rw = sum(1 for k, *_ in plan['fly_rewrite'] if k == bi)
            n_ord = len(b.get('order_groups', []))
            print(f'  [{b["label"][0]}->{b["label"][1]} 实际 {old}->{new}] '
                  f'飞键条目 {len(b["fly_entries"])}, 补 {n_add}, 删 {n_rm}, '
                  f'改码 {n_rw}, 组内顺序 {n_ord}')
        for bi, adds in plan['fly_add'].items():
            for w, c, rest, order in sorted(adds, key=lambda x: (x[1], x[3])):
                print(f'  fly+ {w} {c}' + (f' (权重 {rest})' if rest else ''))
        for k, w, fc in plan['fly_remove']:
            print(f'  fly- {w} {fc}')
        for k, w, fc, nc, rest in plan['fly_rewrite']:
            print(f'  fly~ {w} {fc} -> {nc}')
        for w, c, rest in sorted(set(plan['main_add'])):
            print(f'  main+ {w} {c}')
        for msg in plan['manual']:
            print(f'  !! {msg}')
        if args.apply:
            report = []
            out = rebuild(lines, plan, report)
            print('\n'.join(report))
            path.write_text('\n'.join(out) + '\n', encoding='utf-8')
            print(f'已写回 {path.name}')


if __name__ == '__main__':
    main()
