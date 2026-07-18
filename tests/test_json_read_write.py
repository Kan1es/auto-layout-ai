from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.app.errors import JsonWriteError
from backend.app.json_read_write import read_json, write_json


class JsonReadWriteTest(unittest.TestCase):
    def test_write_json_replaces_existing_file_atomically(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            write_json(path, {"prompt": "old"})

            write_json(path, {"prompt": "new"})

            self.assertEqual(read_json(path), {"prompt": "new"})
            self.assertEqual(list(path.parent.glob(".settings.json.*.tmp")), [])

    def test_failed_replace_preserves_existing_file_and_removes_temp(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            write_json(path, {"prompt": "working"})

            with patch(
                "backend.app.json_read_write.os.replace",
                side_effect=OSError("synthetic replace failure"),
            ):
                with self.assertRaises(JsonWriteError):
                    write_json(path, {"prompt": "broken"})

            self.assertEqual(read_json(path), {"prompt": "working"})
            self.assertEqual(list(path.parent.glob(".settings.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
