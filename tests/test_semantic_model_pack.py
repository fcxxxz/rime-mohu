import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.pack_semantic_model import pack_model


class SemanticModelPackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.model = self.root / "model"
        self.model.mkdir()
        (self.model / "config.json").write_text(
            json.dumps({"model_type": "qwen2", "quantization": {"bits": 4, "group_size": 64}})
        )
        (self.model / "tokenizer.json").write_text("{}")
        (self.model / "model.safetensors").write_bytes(bytes(range(256)) * 32)
        (self.model / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {"w": "model.safetensors"}})
        )
        (self.model / "README.md").write_text("Upstream model card")
        (self.model / ".download-token").write_text("must not be included")

    def tearDown(self):
        self.tmp.cleanup()

    def test_small_model_is_one_zip_with_loader_compatible_folder(self):
        assets = pack_model(
            self.model,
            self.root / "output",
            "0.5b-4bit",
            "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
        )
        archives = [p for p in assets if p.suffix == ".zip"]
        self.assertEqual(len(archives), 1)
        self.assertFalse(any(".part-" in p.name for p in assets))
        with zipfile.ZipFile(archives[0]) as z:
            self.assertIsNone(z.testzip())
            prefix = "Qwen2.5-0.5B-Instruct-4bit/"
            self.assertIn(prefix + "config.json", z.namelist())
            self.assertIn(prefix + "model.safetensors", z.namelist())
            self.assertNotIn(prefix + ".download-token", z.namelist())
            self.assertEqual(
                z.read(prefix + "model.safetensors"),
                (self.model / "model.safetensors").read_bytes(),
            )
            self.assertIn("mohu/model/", z.read(prefix + "安装说明.txt").decode())

    def test_large_model_has_bounded_tar_parts_and_existing_flat_layout(self):
        assets = pack_model(
            self.model,
            self.root / "output",
            "0.5b-4bit",
            "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
            max_asset_bytes=2048,
        )
        parts = sorted(p for p in assets if ".tar.part-" in p.name)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(0 < p.stat().st_size <= 2048 for p in parts))
        combined = self.root / "combined.tar"
        combined.write_bytes(b"".join(p.read_bytes() for p in parts))
        with tarfile.open(combined) as t:
            self.assertIn("config.json", t.getnames())
            self.assertEqual(
                t.extractfile("model.safetensors").read(),
                (self.model / "model.safetensors").read_bytes(),
            )

    def test_large_model_dereferences_cached_weight_symlink(self):
        weight = self.model / "model.safetensors"
        data = weight.read_bytes()
        weight.unlink()
        backing = self.root / "cached-weights"
        backing.write_bytes(data)
        weight.symlink_to(backing)
        assets = pack_model(
            self.model,
            self.root / "output",
            "0.5b-4bit",
            "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
            max_asset_bytes=2048,
        )
        combined = self.root / "combined.tar"
        combined.write_bytes(
            b"".join(p.read_bytes() for p in sorted(assets) if ".tar.part-" in p.name)
        )
        with tarfile.open(combined) as t:
            self.assertTrue(t.getmember("model.safetensors").isfile())
            self.assertEqual(t.extractfile("model.safetensors").read(), data)

    def test_repack_does_not_duplicate_generated_metadata(self):
        (self.model / "mohu-model-manifest.json").write_text("{}")
        (self.model / "安装说明.txt").write_text("obsolete")
        assets = pack_model(
            self.model,
            self.root / "output",
            "0.5b-4bit",
            "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
        )
        with zipfile.ZipFile(next(p for p in assets if p.suffix == ".zip")) as z:
            names = z.namelist()
            self.assertEqual(len(names), len(set(names)))

    def test_missing_shard_is_rejected(self):
        (self.model / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {"w": "missing.safetensors"}})
        )
        with self.assertRaisesRegex(ValueError, "missing"):
            pack_model(
                self.model,
                self.root / "output",
                "0.5b-4bit",
                "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
            )

    def test_unsafe_repository_name_is_rejected(self):
        with self.assertRaises(ValueError):
            pack_model(self.model, self.root / "output", "0.5b-4bit", "mlx-community/../unsafe")


if __name__ == "__main__":
    unittest.main()
