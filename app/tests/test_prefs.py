import _bootstrap  # noqa: F401

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from file_replace import replace_file
from rfapp import prefs


class PrefsTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.path = self.root / "app-prefs.json"
		item = patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.root)})
		item.start()
		self.addCleanup(item.stop)

	def test_schema_defaults_and_read_does_not_create_file(self):
		self.assertEqual(prefs.load(), prefs._DEFAULTS)
		self.assertEqual(prefs.load()["v"], 1)
		self.assertFalse(self.path.exists())
		values = prefs.load()
		values["window"]["width"] = 1
		values["goals"]["account-1"] = 5000
		self.assertEqual(prefs.load(), prefs._DEFAULTS)

	def test_unknown_keys_and_nested_window_kept(self):
		self.path.write_text(json.dumps({"future_key": {"keep": 1}, "window": {"width": 900, "future": True}}))
		self.assertEqual(prefs.load()["window"], {"width": 900, "future": True})
		result = prefs.save({"language": "de", "window": {"x": 10}})
		self.assertEqual(result["future_key"], {"keep": 1})
		self.assertEqual(result["window"], {"x": 10})
		self.assertEqual(prefs.load()["language"], "de")

	def test_account_maps_are_replaced_for_removals(self):
		prefs.save({"account_labels": {"@root": "Mine"}, "goals": {"@root": 5000}})
		prefs.save({"account_labels": {"account-1": "Mine"}, "goals": {"account-1": 5000}})
		self.assertNotIn("@root", prefs.load()["account_labels"])
		self.assertNotIn("@root", prefs.load()["goals"])

	def test_invalid_json_returns_defaults_and_logs(self):
		for text in ("{", "[]"):
			self.path.write_text(text)
			with self.assertLogs("rfapp", "ERROR"):
				self.assertEqual(prefs.load(), prefs._DEFAULTS)

	def test_atomic_failure_does_not_replace_old_file_or_leave_temp(self):
		prefs.save({"setup_done": True})
		before = self.path.read_bytes()
		with patch("rfapp.prefs.os.replace", side_effect=OSError("locked")):
			with self.assertRaises(OSError):
				prefs.save({"setup_done": False})
		self.assertEqual(self.path.read_bytes(), before)
		self.assertEqual(list(self.root.iterdir()), [self.path])

	def test_save_uses_shared_helper_after_one_permission_failure(self):
		prefs.save({"setup_done": False})
		real_replace = os.replace
		attempts = []
		def replace(source, target):
			attempts.append((source, target))
			if len(attempts) == 1:
				raise PermissionError("reader open")
			real_replace(source, target)
		with patch("rfapp.prefs.replace_file", wraps=replace_file) as helper, patch("file_replace.os.replace", side_effect=replace), patch("file_replace.time.sleep"):
			values = prefs.save({"setup_done": True})
		helper.assert_called_once()
		self.assertEqual(len(attempts), 2)
		self.assertTrue(values["setup_done"])
		self.assertTrue(json.loads(self.path.read_text())["setup_done"])
		self.assertEqual(list(self.root.iterdir()), [self.path])

	def test_threaded_disjoint_updates_not_lost(self):
		threads = [threading.Thread(target=prefs.save, args=({f"future_{index}": index},)) for index in range(8)]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join()
		values = prefs.load()
		self.assertTrue(all(values[f"future_{index}"] == index for index in range(8)))


if __name__ == "__main__":
	unittest.main()
