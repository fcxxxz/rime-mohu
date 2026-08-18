import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / 'Rime皮肤编辑器' / 'local' / 'local_server.py'
SPEC = importlib.util.spec_from_file_location('skin_editor_local_server', MODULE_PATH)
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class SkinEditorLocalServerTest(unittest.TestCase):
    def test_schema_yaml_is_readable_but_not_writable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = SERVER.resolve_allowed_path(root, 'tiger.schema.yaml')
            self.assertEqual(path, (root / 'tiger.schema.yaml').resolve())
            with self.assertRaisesRegex(ValueError, '不允许访问'):
                SERVER.resolve_allowed_path(root, 'tiger.schema.yaml', write=True)

    def test_custom_yaml_remains_writable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = SERVER.resolve_allowed_path(root, 'tiger.custom.yaml', write=True)
            self.assertEqual(path, (root / 'tiger.custom.yaml').resolve())

    def test_macos_deploy_uses_squirrel_reload(self):
        command = SERVER.find_deploy_command(
            platform='darwin',
            exists=lambda path: path.endswith('/Squirrel'),
        )
        self.assertEqual(command, [
            '/Library/Input Methods/Squirrel.app/Contents/MacOS/Squirrel',
            '--reload',
        ])

    def test_snapshot_includes_selected_schema_and_schema_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / 'user.yaml').write_text('var:\n  previously_selected_schema: tiger\n', encoding='utf-8')
            (root / 'tiger.schema.yaml').write_text('schema:\n  schema_id: tiger\n', encoding='utf-8')
            snapshot = SERVER.read_config_snapshot(root)
            self.assertIn('user', snapshot['rawFiles'])
            self.assertEqual([item['name'] for item in snapshot['yamlFiles']], ['tiger.schema.yaml'])


if __name__ == '__main__':
    unittest.main()
