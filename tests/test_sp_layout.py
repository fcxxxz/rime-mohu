"""tools/custom/sp_layout.py 的单元测试。

核心保证：内置 zrm/flypy 预设布局与 tools/zrmify.py、tools/flypyify.py
在全部合法拼音音节上完全等价（正反映射），即布局引擎可以无损替代
手写映射模块；坏布局文件给出可读的中文报错。
"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import flypyify  # noqa: E402
import zrmify  # noqa: E402
from tools.custom.sp_layout import (  # noqa: E402
    LayoutError,
    all_pinyins,
    load_layout,
    load_layout_by_name,
)

LAYOUTS_DIR = ROOT / "tools/custom/layouts"


def write_layout(data: dict, directory: Path, filename: str = "layout.yaml") -> Path:
    path = directory / filename
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def zrm_preset_data() -> dict:
    return yaml.safe_load((LAYOUTS_DIR / "zrm.yaml").read_text(encoding="utf-8"))


class PresetEquivalenceTest(unittest.TestCase):
    def _assert_reverse_consistent(self, layout) -> None:
        """反查满足：独占码回到自身；共享码（如 lo←luo,lo）回到标准音节。"""
        standard = set(zrmify.ALL_PINYIN)
        sharers: dict[str, list[str]] = {}
        for pinyin in all_pinyins():
            sharers.setdefault(layout.to_code(pinyin), []).append(pinyin)
        for pinyin in all_pinyins():
            code = layout.to_code(pinyin)
            group = sharers[code]
            if len(group) == 1:
                self.assertEqual(
                    layout.from_code(code), pinyin, f"反查不一致: {code}"
                )
            else:
                winners = [p for p in group if p in standard]
                self.assertIn(
                    layout.from_code(code), winners, f"共享码反查未落在标准音节: {code}"
                )

    def test_zrm_preset_matches_zrmify(self) -> None:
        layout = load_layout(LAYOUTS_DIR / "zrm.yaml")
        for pinyin in all_pinyins():
            self.assertEqual(
                layout.to_code(pinyin), zrmify.zrmify1(pinyin),
                f"zrm 编码不一致: {pinyin}",
            )
        self._assert_reverse_consistent(layout)

    def test_flypy_preset_matches_flypyify(self) -> None:
        layout = load_layout(LAYOUTS_DIR / "flypy.yaml")
        for pinyin in all_pinyins():
            self.assertEqual(
                layout.to_code(pinyin), flypyify.flypyify1(pinyin),
                f"flypy 编码不一致: {pinyin}",
            )
        self._assert_reverse_consistent(layout)

    def test_zrm_to_code_cross_scheme(self) -> None:
        flypy = load_layout(LAYOUTS_DIR / "flypy.yaml")
        # 自然码码 → 拼音 → 小鹤码，与既有转换函数一致。
        for pinyin in all_pinyins():
            zrm_code = zrmify.zrmify1(pinyin)
            self.assertEqual(
                flypy.zrm_to_code(zrm_code), flypyify.flypyify1(pinyin)
            )

    def test_load_by_name(self) -> None:
        layout = load_layout_by_name("zrm")
        self.assertEqual(layout.name, "zrm")
        self.assertEqual(layout.display, "自然码")


class LayoutValidationTest(unittest.TestCase):
    def test_missing_final_is_reported(self) -> None:
        data = zrm_preset_data()
        del data["finals"]["ang"]
        with TemporaryDirectory() as tmp:
            path = write_layout(data, Path(tmp))
            with self.assertRaises(LayoutError) as ctx:
                load_layout(path)
            self.assertIn("缺少韵母 ang", str(ctx.exception))

    def test_unknown_final_key_is_rejected(self) -> None:
        data = zrm_preset_data()
        data["finals"]["iag"] = "h"  # iang 的笔误
        with TemporaryDirectory() as tmp:
            path = write_layout(data, Path(tmp))
            with self.assertRaises(LayoutError) as ctx:
                load_layout(path)
            self.assertIn("iag", str(ctx.exception))

    def test_collision_is_reported(self) -> None:
        data = zrm_preset_data()
        # 把 ei 挪到 ai 的键上：bai 与 bei 同码，反查歧义。
        data["finals"]["ei"] = data["finals"]["ai"]
        with TemporaryDirectory() as tmp:
            path = write_layout(data, Path(tmp))
            with self.assertRaises(LayoutError) as ctx:
                load_layout(path)
            self.assertIn("碰撞", str(ctx.exception))

    def test_missing_zero_initial_is_reported(self) -> None:
        data = zrm_preset_data()
        del data["zero"]["ang"]
        with TemporaryDirectory() as tmp:
            path = write_layout(data, Path(tmp))
            with self.assertRaises(LayoutError) as ctx:
                load_layout(path)
            self.assertIn("ang", str(ctx.exception))

    def test_extra_zero_initial_is_rejected(self) -> None:
        data = zrm_preset_data()
        data["zero"]["aiang"] = "ah"
        with TemporaryDirectory() as tmp:
            path = write_layout(data, Path(tmp))
            with self.assertRaises(LayoutError) as ctx:
                load_layout(path)
            self.assertIn("aiang", str(ctx.exception))

    def test_missing_curly_initial_is_reported(self) -> None:
        data = zrm_preset_data()
        del data["initials"]["ch"]
        with TemporaryDirectory() as tmp:
            path = write_layout(data, Path(tmp))
            with self.assertRaises(LayoutError) as ctx:
                load_layout(path)
            self.assertIn("ch", str(ctx.exception))

    def test_extra_initial_is_rejected(self) -> None:
        data = zrm_preset_data()
        data["initials"]["b"] = "p"
        with TemporaryDirectory() as tmp:
            path = write_layout(data, Path(tmp))
            with self.assertRaises(LayoutError) as ctx:
                load_layout(path)
            self.assertIn("多余键", str(ctx.exception))

    def test_bad_name_is_rejected(self) -> None:
        data = zrm_preset_data()
        data["name"] = "MyLayout"
        with TemporaryDirectory() as tmp:
            path = write_layout(data, Path(tmp))
            with self.assertRaises(LayoutError) as ctx:
                load_layout(path)
            self.assertIn("name", str(ctx.exception))

    def test_missing_file(self) -> None:
        with self.assertRaises(LayoutError):
            load_layout(LAYOUTS_DIR / "nope.yaml")

    def test_unknown_preset_name(self) -> None:
        with self.assertRaises(LayoutError) as ctx:
            load_layout_by_name("nope")
        self.assertIn("预设", str(ctx.exception))

    def test_ue_umlaut_alias_fills_ve_only(self) -> None:
        # üe 归一到 ve：只写 üe 不写 ue 时，jue/que/xue 的 ue 韵母缺失报错。
        data = zrm_preset_data()
        del data["finals"]["ve"]
        del data["finals"]["ue"]
        data["finals"]["üe"] = "t"
        with TemporaryDirectory() as tmp:
            path = write_layout(data, Path(tmp))
            with self.assertRaises(LayoutError) as ctx:
                load_layout(path)
            self.assertIn("缺少韵母 ue", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
