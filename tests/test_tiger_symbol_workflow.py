import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOHU_SCHEMAS = (
    "mohu_zrm",
    "mohu_zrm_fixed",
    "mohu_zrm_sentence",
    "mohu_zrm_aux",
    "mohu_flypy",
    "mohu_flypy_fixed",
    "mohu_flypy_sentence",
    "mohu_flypy_aux",
)
EXPECTED_QUICK = {
    ";q": "：“",
    ";w": "？",
    ";e": "（",
    ";r": "）",
    ";t": "→",
    ";y": "·",
    ";u": "~",
    ";i": "——",
    ";o": "〖",
    ";p": "〗",
    ";a": "！",
    ";s": "……",
    ";d": "、",
    ";f": "“",
    ";g": "”",
    ";h": "『",
    ";j": "』",
    ";k": "￥",
    ";l": "%",
    ";z": "|",
    ";x": "【",
    ";c": "】",
    ";v": "《",
    ";b": "》",
    ";n": "「",
    ";m": "」",
}


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def quick_entries() -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {}
    for line in read("mohu_fixed.symbols.dict.yaml").splitlines():
        if not line or line.startswith("#") or "\t" not in line:
            continue
        text, code, *_ = line.split("\t")
        if code.startswith(";"):
            entries.setdefault(code, []).append(text)
    return entries


class TigerSymbolWorkflowTest(unittest.TestCase):
    def test_quick_symbols_match_tiger(self) -> None:
        entries = quick_entries()
        for code, text in EXPECTED_QUICK.items():
            with self.subTest(code=code):
                self.assertEqual([text], entries.get(code))
        self.assertEqual(["：", "；"], entries.get(";"))

    def test_backslash_is_punctuation_and_symbol_commands_use_slash(self) -> None:
        symbols = read("symbols.yaml")
        self.assertRegex(symbols, r"(?m)^    ['\"]?\\['\"]?\s*:\s*\{\s*commit:\s*['\"]?、")
        self.assertNotRegex(symbols, r"(?m)^    ['\"]?/['\"]?\s*:\s*\{\s*commit:")
        self.assertIn("'/bd'", symbols)
        self.assertIn("'/bq'", symbols)
        self.assertIn("'/pi'", symbols)
        for old, new in (
            ("rq", "rqfh"),
            ("sj", "sjfh"),
            ("xq", "xqfh"),
            ("jq", "jqfh"),
        ):
            with self.subTest(symbol_command=old):
                self.assertIn(f"'/{new}'", symbols)
                self.assertNotIn(f"'/{old}':", symbols)
        self.assertNotRegex(symbols, r"(?m)^    ['\"]\\[A-Za-z]")

    def test_mohu_schemas_use_slash_workflow(self) -> None:
        for schema_id in MOHU_SCHEMAS:
            with self.subTest(schema=schema_id):
                text = read(f"{schema_id}.schema.yaml")
                self.assertNotIn("quick_repeat", text)
                self.assertIn("lua_translator@*mohu_symbol_hint", text)
                self.assertRegex(
                    text,
                    r"candidate_manager:\s*['\"]\^==\[hopwlu\]\?\[a-z\]\*\$['\"]",
                )
                self.assertIn("punct: '^/(", text)

    def test_shared_manager_and_pin_use_current_prefixes(self) -> None:
        shared = read("mohu.yaml")
        self.assertIn('prefix: "=="', shared)
        self.assertIn('infix: "//"', shared)
        self.assertNotIn('prefix: "\\\\gl"', shared)
        self.assertNotIn('infix: "\\\\\\\\"', shared)

    def test_pure_tiger_uses_slash_symbol_menu(self) -> None:
        tiger = read("tiger.schema.yaml")
        self.assertIn("punct: '^/([0-9]0?|[A-Za-z]*)$'", tiger)


if __name__ == "__main__":
    unittest.main()
