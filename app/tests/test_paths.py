import _bootstrap  # noqa: F401

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rfapp import paths


class PathsTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.env = patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.root), "USERNAME": "TESTER"})
		self.env.start()
		self.addCleanup(self.env.stop)

	def test_source_resources(self):
		with patch.object(sys, "frozen", False, create=True):
			self.assertFalse(paths.is_frozen())
			self.assertEqual(paths.repo_root(), Path(__file__).resolve().parents[2])
			self.assertEqual(paths.ui_dir(), paths.repo_root() / "app" / "ui")
			self.assertEqual(paths.resource("ui", "index.html"), paths.ui_dir() / "index.html")

	def test_data_paths_and_lazy_runs(self):
		self.assertEqual(paths.data_home(), self.root)
		self.assertEqual(paths.env_path(), self.root / ".env")
		self.assertEqual(paths.prefs_path(), self.root / "app-prefs.json")
		self.assertEqual(paths.app_log_path(), self.root / "app.log")
		self.assertFalse((self.root / "runs").exists())
		self.assertTrue(paths.runs_dir().is_dir())
		self.assertEqual(paths.runs_dir(), self.root / "runs")

	def test_source_default_leaves_environment_unset(self):
		old = sys.path[:]
		self.addCleanup(setattr, sys, "path", old)
		with patch.object(sys, "frozen", False, create=True), patch.dict(os.environ, {}, clear=True):
			self.assertEqual(paths.data_home(), paths.repo_root())
			paths.prepare_environment()
			paths.prepare_environment()
			self.assertEqual(sys.path[0], str(paths.repo_root() / "src"))
			self.assertEqual(sys.path.count(sys.path[0]), 1)
			self.assertNotIn("REWARDS_FARMER_DATA_HOME", os.environ)
			self.assertNotIn("REWARDS_FARMER_RESOURCE_ROOT", os.environ)
			self.assertEqual(os.environ["SE_AVOID_STATS"], "true")
			self.assertNotIn("SE_CACHE_PATH", os.environ)

	def test_frozen_paths_and_environment(self):
		bundle = self.root / "bundle"
		with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(bundle), create=True), patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.root)}, clear=True):
			self.assertTrue(paths.is_frozen())
			self.assertEqual(paths.repo_root(), bundle)
			self.assertEqual(paths.ui_dir(), bundle / "app" / "ui")
			paths.prepare_environment()
			self.assertEqual(os.environ["REWARDS_FARMER_DATA_HOME"], str(self.root))
			self.assertEqual(os.environ["REWARDS_FARMER_RESOURCE_ROOT"], str(bundle))
			self.assertEqual(os.environ["SE_AVOID_STATS"], "true")
			self.assertEqual(os.environ["SE_CACHE_PATH"], str(self.root / "selenium"))
			self.assertFalse((self.root / "selenium").exists())

	def test_frozen_default_home(self):
		with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(self.root), create=True), patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=True):
			self.assertEqual(paths.data_home(), self.root / "RewardsFarmer")
			paths.prepare_environment()
			self.assertEqual(os.environ["REWARDS_FARMER_DATA_HOME"], str(self.root / "RewardsFarmer"))
			self.assertEqual(os.environ["SE_AVOID_STATS"], "true")
			self.assertEqual(os.environ["SE_CACHE_PATH"], str(self.root / "RewardsFarmer" / "selenium"))

	def test_selenium_environment_preserves_user_values(self):
		old = sys.path[:]
		self.addCleanup(setattr, sys, "path", old)
		for frozen in (False, True):
			with self.subTest(frozen=frozen), patch.object(sys, "frozen", frozen, create=True), patch.object(sys, "_MEIPASS", str(self.root), create=True), patch.dict(os.environ, {"SE_AVOID_STATS": "false", "SE_CACHE_PATH": str(self.root / "custom-cache")}):
				paths.prepare_environment()
				self.assertEqual(os.environ["SE_AVOID_STATS"], "false")
				self.assertEqual(os.environ["SE_CACHE_PATH"], str(self.root / "custom-cache"))

	def test_identity_is_user_and_install_specific(self):
		expected = hashlib.sha256(f"tester|{os.path.normcase(str(self.root))}".encode()).hexdigest()[:8]
		self.assertEqual(paths.install_identity(), expected)
		self.assertEqual(paths.task_name(), "RewardsFarmerApp_" + expected)
		with patch.dict(os.environ, {"USERNAME": "tester"}):
			self.assertEqual(paths.install_identity(), expected)
		with patch.dict(os.environ, {"USERNAME": "other"}):
			self.assertNotEqual(paths.install_identity(), expected)
		with patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.root / "other")}):
			self.assertNotEqual(paths.install_identity(), expected)

	def test_source_command_prefers_pythonw_and_preserves_args(self):
		args = ["--signin", "account-1", "--new"]
		with patch.object(sys, "frozen", False, create=True), patch.object(Path, "is_file", return_value=True):
			self.assertEqual(paths.launcher_command(args), [str(Path(sys.executable).with_name("pythonw.exe")), str(paths.repo_root() / "app" / "launcher.py"), *args])
			self.assertEqual(paths.farmer_command(["--help"])[2:], ["--farm", "--help"])
		self.assertEqual(args, ["--signin", "account-1", "--new"])

	def test_source_command_falls_back(self):
		with patch.object(sys, "frozen", False, create=True), patch.object(Path, "is_file", return_value=False):
			self.assertEqual(paths.launcher_command([])[0], sys.executable)

	def test_frozen_commands(self):
		with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", "C:\\app\\RewardsFarmer.exe"):
			self.assertEqual(paths.launcher_command(["--tray"]), [sys.executable, "--tray"])
			self.assertEqual(paths.farmer_command(["--check-only"]), [sys.executable, "--farm", "--check-only"])


if __name__ == "__main__":
	unittest.main()
