from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location(
    "sync_wanxiang", ROOT / "tools" / "sync_wanxiang.py"
)
assert SPEC and SPEC.loader
sync_wanxiang = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync_wanxiang
SPEC.loader.exec_module(sync_wanxiang)

REVISION = "a" * 40


def write_snapshot(data: Path, rows: list[str]) -> None:
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text(
        "# Rime dictionary\n---\nname: upstream\n...\n\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def write_aux(root: Path, chars: dict[str, str]) -> None:
    path = root / "tools/data/tiger_aux.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{char}\t{code}\n" for char, code in chars.items()), encoding="utf-8"
    )


class WanxiangEnvironment:
    """Patch module-level paths onto a disposable tree."""

    def __enter__(self) -> Path:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            mock.patch.object(sync_wanxiang, "ROOT", self.root),
            mock.patch.object(sync_wanxiang, "DATA", self.root / "tools/data/wanxiang"),
            mock.patch.object(
                sync_wanxiang, "MANIFEST", self.root / "tools/data/wanxiang/manifest.json"
            ),
            mock.patch.object(
                sync_wanxiang, "ENTRIES", self.root / "tools/data/wanxiang/entries.tsv"
            ),
            mock.patch.object(
                sync_wanxiang, "OUTPUT", self.root / "mohu_zrm.wanxiang.dict.yaml"
            ),
            mock.patch.object(
                sync_wanxiang, "REPORT", self.root / "tools/data/wanxiang/sync_report.md"
            ),
        ]
        for patch in self.patches:
            patch.start()
        return self.root

    def __exit__(self, *args: object) -> None:
        for patch in self.patches:
            patch.stop()
        self.temp.cleanup()


class NormalizeTest(unittest.TestCase):
    def test_strips_tone_marks_and_digits(self) -> None:
        self.assertEqual(sync_wanxiang.normalize_syllable("nǐ"), "ni")
        self.assertEqual(sync_wanxiang.normalize_syllable("hao3"), "hao")
        self.assertEqual(sync_wanxiang.normalize_syllable("Le4"), "le")

    def test_umlaut_becomes_v(self) -> None:
        # ü 必须先替换成 v 再剥声调，否则会退化成 u。
        self.assertEqual(sync_wanxiang.normalize_syllable("lǜ4"), "lv")
        self.assertEqual(sync_wanxiang.normalize_syllable("nǚ3"), "nv")
        self.assertEqual(sync_wanxiang.normalize_syllable("lü3"), "lv")

    def test_rejects_invalid_syllables(self) -> None:
        for bad in ("ang6", "zhang9", "ni hao", ""):
            with self.assertRaises(ValueError):
                sync_wanxiang.normalize_syllable(bad)

    def test_pinyin_length_must_match_text(self) -> None:
        with self.assertRaises(ValueError):
            sync_wanxiang.normalize_pinyin("ni hao", "你好啊")
        self.assertEqual(sync_wanxiang.normalize_pinyin("ni3 hao3", "你好"), "ni hao")


class ParseSourceTest(unittest.TestCase):
    def test_parses_rejects_and_counts(self) -> None:
        with WanxiangEnvironment() as root:
            path = root / "tools/data/wanxiang/raw/test.dict.yaml"
            write_snapshot(
                path,
                [
                    "库迪\tku4 di2\t100",
                    "瑞幸\trui4 xing4\t90",
                    "坏权重\tquan2 zhong3\tnope",
                    "坏拼音\tno such\t10",
                    "# 注释行忽略",
                ],
            )
            candidates, rejected = sync_wanxiang.parse_source(path, "test")
            self.assertEqual(rejected, 2)
            self.assertEqual(
                [candidate.text for candidate in candidates], ["库迪", "瑞幸"]
            )
            self.assertEqual(candidates[0].pinyin, "ku di")
            self.assertEqual(candidates[0].upstream_weight, 100)

    def test_short_rows_are_rejected_not_fatal(self) -> None:
        with WanxiangEnvironment() as root:
            path = root / "tools/data/wanxiang/raw/test.dict.yaml"
            write_snapshot(path, ["缺权重\tque1 quan2", "完好\twan2 hao3\t10"])
            candidates, rejected = sync_wanxiang.parse_source(path, "test")
            self.assertEqual(rejected, 1)
            self.assertEqual([candidate.text for candidate in candidates], ["完好"])


class SelectCandidatesTest(unittest.TestCase):
    def make_manifest(self, root: Path) -> None:
        manifest = {
            "revision": REVISION,
            "files": {
                "one": {
                    "path": "dicts/one.dict.yaml",
                    "raw_path": "raw/one.dict.yaml",
                    "sha256": "",
                },
                "two": {
                    "path": "dicts/two.dict.yaml",
                    "raw_path": "raw/two.dict.yaml",
                    "sha256": "",
                },
            },
        }
        data = root / "tools/data/wanxiang"
        data.mkdir(parents=True, exist_ok=True)
        (data / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def test_selection_dedup_and_conflicts(self) -> None:
        with WanxiangEnvironment() as root:
            self.make_manifest(root)
            data = root / "tools/data/wanxiang"
            write_snapshot(
                data / "raw/one.dict.yaml",
                [
                    "库迪\tku4 di2\t50",
                    "重复\tchong2 fu2\t10",
                    "长发\tchang2 fa1\t10",
                    "无辅\twu2 fu3\t10",
                ],
            )
            write_snapshot(
                data / "raw/two.dict.yaml",
                [
                    "重复\tzhong4 fu4\t99",
                    "长发\tzhang3 fa4\t30",
                    "已有\tyi3 you3\t10",
                ],
            )
            write_aux(
                root,
                {
                    "库": "kk",
                    "迪": "dd",
                    "重": "cc",
                    "复": "ff",
                    "长": "ll",
                    "发": "fa",
                    "已": "yy",
                    "有": "yo",
                },
            )
            (root / "mohu_zrm.base.dict.yaml").write_text(
                "---\nname: base\n...\n\n已有\tyi vw\t1\n", encoding="utf-8"
            )
            selected, stats = sync_wanxiang.select_candidates()
            by_text = {entry.text: entry for entry in selected}
            # 同词跨表取上游权重最高的一条。
            self.assertEqual(by_text["重复"].upstream_weight, 99)
            self.assertNotIn("已有", by_text)
            # 缺主辅码的词被拒收并计数。
            self.assertNotIn("无辅", by_text)
            self.assertEqual(stats["missing_auxiliary"], 1)
            self.assertEqual(stats["duplicate_existing"], 1)
            self.assertEqual(stats["pronunciation_conflicts"], 2)
            # 排序稳定。
            again, _ = sync_wanxiang.select_candidates()
            self.assertEqual(selected, again)


class RenderDictionaryTest(unittest.TestCase):
    def test_header_and_rows(self) -> None:
        candidate = sync_wanxiang.Candidate("你好", "ni hao", "one", 5)
        auxiliary = {"你": ["na"], "好": ["hb"]}
        text = sync_wanxiang.render_dictionary([candidate], "abcdef123456", auxiliary)
        lines = text.splitlines()
        self.assertIn("name: mohu_zrm.wanxiang", lines)
        self.assertIn('version: "abcdef123456"', lines)
        body = [line for line in lines if line and not line.startswith(("#", "---"))]
        self.assertIn("你好\tni;na hk;hb\t20", body)


class FetchSafetyTest(unittest.TestCase):
    def test_refuses_non_https_and_wrong_host(self) -> None:
        for url in (
            "http://api.github.com/repos/x",
            "https://evil.example.com/repos/x",
            "https://api.github.com:8443/repos/x",
        ):
            with self.assertRaises(ValueError):
                sync_wanxiang.fetch(url, expected_host="api.github.com")

    def test_refuses_disallowed_prefix(self) -> None:
        with self.assertRaises(ValueError):
            sync_wanxiang.fetch(
                "https://api.github.com/repos/other/contents/dicts/x",
                expected_host="api.github.com",
                allowed_prefix="/repos/amzxyz/rime-wanxiang/contents/dicts/",
            )

    def test_refuses_private_addresses(self) -> None:
        for address in ("127.0.0.1", "10.0.0.5", "169.254.1.1", "::1"):
            fake = [[2, 1, 6, "", (address, 443)]]
            with mock.patch.object(sync_wanxiang.socket, "getaddrinfo", return_value=fake):
                with self.assertRaises(ValueError):
                    sync_wanxiang._assert_public_host("api.github.com")

    def test_accepts_public_address(self) -> None:
        fake = [[2, 1, 6, "", ("140.82.112.6", 443)]]
        with mock.patch.object(sync_wanxiang.socket, "getaddrinfo", return_value=fake):
            sync_wanxiang._assert_public_host("api.github.com")


class DownloadSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "path": "dicts/one.dict.yaml",
            "sha256": "",
        }

    def fake_api(self, metadata: dict, blob: dict) -> mock.MagicMock:
        responses = [metadata, blob]
        return mock.patch.object(
            sync_wanxiang, "fetch_json", side_effect=lambda *a, **k: responses.pop(0)
        )

    def test_downloads_and_hashes(self) -> None:
        payload = b"hello wanxiang"
        metadata = {"sha": "b" * 40, "size": len(payload)}
        blob = {"encoding": "base64", "content": base64.b64encode(payload).decode()}
        with self.fake_api(metadata, blob):
            data, digest = sync_wanxiang.download_source(self.config, REVISION)
        self.assertEqual(data, payload)
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

    def test_rejects_size_mismatch(self) -> None:
        payload = b"hello wanxiang"
        metadata = {"sha": "b" * 40, "size": len(payload) - 1}
        blob = {"encoding": "base64", "content": base64.b64encode(payload).decode()}
        with self.fake_api(metadata, blob):
            with self.assertRaises(ValueError):
                sync_wanxiang.download_source(self.config, REVISION)

    def test_rejects_manifest_hash_mismatch(self) -> None:
        payload = b"hello wanxiang"
        config = {"path": "dicts/one.dict.yaml", "sha256": "c" * 64}
        metadata = {"sha": "b" * 40, "size": len(payload)}
        blob = {"encoding": "base64", "content": base64.b64encode(payload).decode()}
        with self.fake_api(metadata, blob):
            with self.assertRaises(ValueError):
                sync_wanxiang.download_source(config, REVISION)

    def test_rejects_non_base64_blob(self) -> None:
        metadata = {"sha": "b" * 40, "size": 5}
        blob = {"encoding": "none"}
        with self.fake_api(metadata, blob):
            with self.assertRaises(ValueError):
                sync_wanxiang.download_source(self.config, REVISION)


class VerifySnapshotsTest(unittest.TestCase):
    def test_checks_hashes_and_files(self) -> None:
        with WanxiangEnvironment() as root:
            data = root / "tools/data/wanxiang"
            payload = b"snapshot"
            manifest = {
                "revision": REVISION,
                "files": {
                    "one": {
                        "path": "dicts/one.dict.yaml",
                        "raw_path": "raw/one.dict.yaml",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                },
            }
            (data / "raw").mkdir(parents=True)
            (data / "raw/one.dict.yaml").write_bytes(payload)
            sync_wanxiang.verify_snapshots(manifest)
            manifest["files"]["one"]["sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                sync_wanxiang.verify_snapshots(manifest)
            manifest["files"]["one"]["raw_path"] = "raw/missing.dict.yaml"
            with self.assertRaises(ValueError):
                sync_wanxiang.verify_snapshots(manifest)
            manifest["revision"] = "short"
            with self.assertRaises(ValueError):
                sync_wanxiang.verify_snapshots(manifest)


class UpdateTest(unittest.TestCase):
    def test_noop_when_revision_unchanged(self) -> None:
        with WanxiangEnvironment() as root:
            data = root / "tools/data/wanxiang"
            data.mkdir(parents=True, exist_ok=True)
            (data / "manifest.json").write_text(
                json.dumps({"revision": REVISION, "files": {}}), encoding="utf-8"
            )
            with mock.patch.object(
                sync_wanxiang,
                "fetch",
                return_value=json.dumps({"sha": REVISION}).encode(),
            ):
                self.assertFalse(sync_wanxiang.update())


class BuildDeltaTest(unittest.TestCase):
    def test_added_removed_against_existing_output(self) -> None:
        with WanxiangEnvironment() as root:
            data = root / "tools/data/wanxiang"
            data.mkdir(parents=True, exist_ok=True)
            (data / "manifest.json").write_text(
                json.dumps(
                    {
                        "revision": REVISION,
                        "files": {
                            "one": {
                                "path": "dicts/one.dict.yaml",
                                "raw_path": "raw/one.dict.yaml",
                                "sha256": "",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            write_snapshot(data / "raw/one.dict.yaml", ["新增\txin1 zeng1\t10"])
            write_aux(root, {"新": "xx", "增": "zz"})
            (root / "mohu_zrm.wanxiang.dict.yaml").write_text(
                "---\nname: mohu_zrm.wanxiang\n...\n\n旧词\tjiu4 ci2\t20\n",
                encoding="utf-8",
            )
            stats = sync_wanxiang.build()
            self.assertEqual(stats["added"], 1)
            self.assertEqual(stats["removed"], 1)
            self.assertEqual(stats["selected"], 1)
            output = (root / "mohu_zrm.wanxiang.dict.yaml").read_text(encoding="utf-8")
            self.assertIn("新增\txn;xx zg;zz\t20", output.splitlines())
            # 第二次构建增量为零。
            self.assertEqual(sync_wanxiang.build()["added"], 0)


if __name__ == "__main__":
    unittest.main()
