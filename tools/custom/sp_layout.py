#!/usr/bin/env python3
"""sp_layout.py —— 数据驱动的双拼布局引擎。

布局文件是用户可编辑的 YAML（拼音→键）：

    name: weiruan
    display: 微软
    initials:      # 与拼音同形的声母不写，只写 zh/ch/sh
      zh: v
      ch: i
      sh: u
    finals:        # 韵母→键，一行一个韵母
      iu: q
      ang: h
    zero:          # 零声母音节（a/o/e 开头）整音节打法
      a: aa
      ang: ah

引擎在全部合法拼音音节（zrmify.ALL_PINYIN）上展开成「音节→双字母码」
正反映射；反向映射由枚举反推，不需要任何手写逆规则。加载时校验：
韵母表无遗漏、编码均为双字母小写、无同码异音节碰撞、无拼写错误的
多余键。所有报错信息为中文，直接面向构建自定义方案的用户。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

TOOLS_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import zrmify  # noqa: E402

CUSTOM_DIR = Path(__file__).resolve().parent
LAYOUTS_DIR = CUSTOM_DIR / "layouts"

# 供参考的预设布局名（tools/custom/layouts/<name>.yaml）。
PRESET_NAMES = ("zrm", "flypy")

# 保留方案名：自定义方案不允许使用（会与现行产物冲突）。
RESERVED_NAMES = frozenset({"zrm", "flypy"})

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*$")
_CODE_PATTERN = re.compile(r"^[a-z]{2}$")
_CURLY_INITIALS = ("zh", "ch", "sh")

# finals 键的常见等价写法归一化（布局键位图常用 ü 形与全拼展开形）。
# 注意 jqx/y 系音节在拼音串里是 u 形（juan/jun），只有 l/n 系是真 v 形
# （lv/lve），所以 üan/ün 归一到 u 形、ü/üe 归一到 v 形；缺另一个写法时
# 展开校验会点名补齐。
_FINAL_ALIASES = {
    "ü": "v",
    "üe": "ve",
    "üan": "uan",
    "ün": "un",
    "iou": "iu",
    "uei": "ui",
    "uen": "un",
}


class LayoutError(ValueError):
    """布局文件不合法；str(e) 直接面向用户。"""


def _normalize_final(final: str) -> str:
    return _FINAL_ALIASES.get(final, final)


# 完整合法音节表：zrmify.ALL_PINYIN 漏了 14 个真实读音（语料
# tools/data/pinyin_simp.txt 实测出现），这里补齐；这些读音在
# zrmify/flypyify 的函数里都能正常编码，只是没进那张测试清单。
# 「n」不收：zrmify 把它折到 en（嗯→en），属于读音层归一，在字符
# 生成时处理，避免布局引擎出现 en←{en,n} 的同码歧义。
_EXTRA_SYLLABLES = (
    "biang", "cei", "chua", "dei", "den", "dia", "eng", "kei",
    "lo", "nun", "rua", "shei", "tei", "zhei",
)
# 读音层归一：与 tools/zrmify.py 对这些边缘读音的既有输出保持一致。
READING_NORMALIZATION = {"n": "en"}


def all_pinyins() -> list[str]:
    """完整合法拼音音节表（ALL_PINYIN + 补齐的边缘读音）。"""
    return list(dict.fromkeys(list(zrmify.ALL_PINYIN) + list(_EXTRA_SYLLABLES)))


def _all_pinyins() -> list[str]:
    return all_pinyins()


def _decompose(pinyin: str) -> tuple[str | None, str]:
    """把一个合法拼音拆成 (声母, 韵母)；零声母返回 (None, pinyin)。

    拆分规则与 zrmify.zrmify1 一致：zh/ch/sh 整体作声母（音节长度>2），
    其余取首字母；a/o/e 开头为零声母。y/w 按普通声母处理。
    """
    if pinyin[0] in "aeiou":
        return None, pinyin
    if len(pinyin) > 2 and pinyin[:2] in _CURLY_INITIALS:
        return pinyin[:2], pinyin[2:]
    return pinyin[:1], pinyin[1:]


def _known_finals() -> set[str]:
    finals: set[str] = set()
    for pinyin in _all_pinyins():
        _, final = _decompose(pinyin)
        finals.add(final)
    return finals


def _known_zero_initials() -> set[str]:
    return {
        pinyin
        for pinyin in _all_pinyins()
        if _decompose(pinyin)[0] is None
    }


@dataclass(frozen=True)
class SpLayout:
    """一个双拼布局：合法音节与双字母码的双射。

    finals/initials 保留原始键位表，供边缘读音的机械编码兜底。
    """

    name: str
    display: str
    source: Path
    forward: dict[str, str]
    reverse: dict[str, str]
    finals: dict[str, str]
    initials: dict[str, str]

    def to_code(self, pinyin: str) -> str:
        """拼音音节 → 双字母码。

        标准音节查正反映射；方言/边缘读音（yai、pia、biong 等，
        不在标准表但部件可分解）按布局机械组合；无法分解的报错。
        """
        code = self.forward.get(pinyin)
        if code is not None:
            return code
        initial, final = _decompose(pinyin)
        if initial is None:
            raise LayoutError(
                f"音节 {pinyin!r} 不在合法拼音表中，也无法用布局 {self.name} 分解编码"
            )
        key = self.finals.get(final)
        if key is None:
            raise LayoutError(
                f"音节 {pinyin!r} 的韵母 {final!r} 不在布局 {self.name} 的韵母表中"
            )
        if initial in _CURLY_INITIALS:
            initial_key = self.initials.get(initial)
            if initial_key is None:
                raise LayoutError(
                    f"音节 {pinyin!r} 需要声母 {initial}，但布局缺少它的键"
                )
        else:
            initial_key = initial
        return initial_key + key

    def from_code(self, code: str) -> str:
        """双字母码 → 拼音音节。"""
        try:
            return self.reverse[code]
        except KeyError:
            raise LayoutError(
                f"编码 {code!r} 不是布局 {self.name} 的合法双拼码"
            ) from None

    def is_code(self, code: str) -> bool:
        return code in self.reverse

    def zrm_to_code(self, zrm_syllable: str) -> str:
        """自然码双拼码 → 本布局码（经拼音中转）。"""
        return self.to_code(zrmify.unzrmify1(zrm_syllable))

    def zrm_to_code_or_none(self, zrm_syllable: str) -> str | None:
        try:
            return self.zrm_to_code(zrm_syllable)
        except (LayoutError, ValueError, IndexError, TypeError):
            return None


def _require_map(data: dict, key: str, source: Path) -> dict:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LayoutError(f"{source.name}: {key} 必须是映射表（韵母: 键），当前是 {type(value).__name__}")
    return value


def load_layout(path: Path) -> SpLayout:
    """加载并校验一个布局文件；任何问题抛 LayoutError（中文信息）。"""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LayoutError(f"找不到布局文件：{path}") from None
    except yaml.YAMLError as exc:
        raise LayoutError(f"{path.name} 不是合法 YAML：{exc}") from None
    if not isinstance(data, dict):
        raise LayoutError(f"{path.name}: YAML 内容必须是映射表")

    name = data.get("name")
    if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
        raise LayoutError(
            f"{path.name}: name 必须是小写字母开头的字母数字串（如 weiruan），当前为 {name!r}"
        )
    display = data.get("display")
    if display is None:
        display = name
    if not isinstance(display, str) or not display.strip():
        raise LayoutError(f"{path.name}: display 必须是非空字符串")

    initials = _require_map(data, "initials", path)
    finals_raw = _require_map(data, "finals", path)
    zero = _require_map(data, "zero", path)

    # 声母：只允许 zh/ch/sh（其余声母与拼音同形，不需要也不能改）。
    for initial, key in initials.items():
        if initial not in _CURLY_INITIALS:
            raise LayoutError(
                f"{path.name}: initials 只支持 zh/ch/sh（其余声母与拼音同形），"
                f"出现多余键 {initial!r}"
            )
        if not isinstance(key, str) or not re.fullmatch(r"[a-z]", key):
            raise LayoutError(
                f"{path.name}: 声母 {initial} 的键必须是单个小写字母，当前为 {key!r}"
            )

    # 韵母：归一化键名、校验都是单个小写字母、报告多余键（防笔误）。
    finals: dict[str, str] = {}
    for final, key in finals_raw.items():
        final = _normalize_final(str(final))
        if not isinstance(key, str) or not re.fullmatch(r"[a-z]", key):
            raise LayoutError(
                f"{path.name}: 韵母 {final} 的键必须是单个小写字母，当前为 {key!r}"
            )
        finals[final] = key
    known_finals = _known_finals()
    unknown = sorted(set(finals) - known_finals)
    if unknown:
        raise LayoutError(
            f"{path.name}: finals 中的 {', '.join(unknown)} 不是合法韵母（可能是笔误）；"
            f"合法韵母见 tools/custom/layouts/zrm.yaml"
        )

    # 零声母：键必须恰好覆盖全部零声母音节。
    known_zero = _known_zero_initials()
    zero_keys = set(zero)
    missing_zero = sorted(known_zero - zero_keys)
    if missing_zero:
        raise LayoutError(
            f"{path.name}: zero 缺少零声母音节 {', '.join(missing_zero)}"
        )
    extra_zero = sorted(zero_keys - known_zero)
    if extra_zero:
        raise LayoutError(
            f"{path.name}: zero 中的 {', '.join(extra_zero)} 不是零声母音节（可能是笔误）"
        )
    for pinyin, code in zero.items():
        if not isinstance(code, str) or not _CODE_PATTERN.fullmatch(code):
            raise LayoutError(
                f"{path.name}: 零声母 {pinyin} 的编码必须是两个小写字母，当前为 {code!r}"
            )

    # 在全部合法音节上展开。
    forward: dict[str, str] = {}
    missing_finals: set[str] = set()
    for pinyin in _all_pinyins():
        initial, final = _decompose(pinyin)
        if initial is None:
            forward[pinyin] = zero[pinyin]
            continue
        key = finals.get(final)
        if key is None:
            missing_finals.add(final)
            continue
        if initial in _CURLY_INITIALS:
            initial_key = initials.get(initial)
            if initial_key is None:
                raise LayoutError(
                    f"{path.name}: initials 缺少 {initial}（音节 {pinyin} 需要它）"
                )
        else:
            initial_key = initial
        forward[pinyin] = initial_key + key
    if missing_finals:
        raise LayoutError(
            f"{path.name}: finals 缺少韵母 {', '.join(sorted(missing_finals))}"
        )

    # 编码形状与碰撞校验。
    bad_codes = {p: c for p, c in forward.items() if not _CODE_PATTERN.fullmatch(c)}
    if bad_codes:
        sample = ", ".join(f"{p}→{c}" for p, c in list(bad_codes.items())[:5])
        raise LayoutError(f"{path.name}: 存在非「双字母小写」编码：{sample}")
    # 反查映射：同码多音节时标准音节优先于边缘音节（与 zrmify/
    # flypyify 的 unzrmify 消歧一致，如 lo←{luo,lo} 反查为 luo）；
    # 两个标准音节撞码才是真歧义，无法安全反查，报错——除非布局
    # 声明 allow_collision（如落云 ia/ü 同在 r 键：lia,lv 共享 lr，
    # 二键层同码由辅码区分，属方案设计而非笔误）。构建管线只走
    # 正向映射，此时反查取枚举中的首个标准音节，仅供参考。
    allow_collision = bool(data.get("allow_collision"))
    standard = set(zrmify.ALL_PINYIN)
    reverse: dict[str, str] = {}
    ambiguous: dict[str, set[str]] = {}
    for pinyin, code in forward.items():
        incumbent = reverse.get(code)
        if incumbent is None:
            reverse[code] = pinyin
        elif pinyin in standard and incumbent not in standard:
            reverse[code] = pinyin
        elif pinyin not in standard and incumbent in standard:
            continue
        elif pinyin in standard and incumbent in standard:
            group = ambiguous.setdefault(code, {incumbent})
            group.add(pinyin)
    if ambiguous and not allow_collision:
        sample = "; ".join(
            f"{code}←{','.join(sorted(pys))}" for code, pys in sorted(ambiguous.items())[:5]
        )
        raise LayoutError(
            f"{path.name}: 布局存在同码异音节碰撞（无法反查）：{sample}；"
            f"若属方案设计（二键层共享、辅码区分），可在布局里声明 allow_collision: true"
        )
    return SpLayout(name=name, display=display, source=path,
                    forward=forward, reverse=reverse,
                    finals=finals, initials=initials)


def load_layout_by_name(name: str) -> SpLayout:
    """按预设名加载 tools/custom/layouts/<name>.yaml。"""
    path = LAYOUTS_DIR / f"{name}.yaml"
    if not path.is_file():
        raise LayoutError(
            f"没有名为 {name!r} 的预设布局；可用预设：{', '.join(PRESET_NAMES)}"
            f"（目录 {LAYOUTS_DIR}）"
        )
    return load_layout(path)
