"""The parts of the window that are worth a test: `.env` reading and writing.

The widgets themselves need a display and a person looking at them, but the
file round-trip does not, and it is the part that can quietly corrupt a
carefully written `.env` if it gets the quoting wrong.
"""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)

import gui


class TestEnvValue(unittest.TestCase):
	def test_plain_values_and_windows_paths_are_left_alone(self):
		self.assertEqual(gui._env_value("trends"), "trends")
		self.assertEqual(gui._env_value("C:\\Users\\me\\data-dir"), "C:\\Users\\me\\data-dir")
		self.assertEqual(gui._env_value("http://localhost:11434/v1"), "http://localhost:11434/v1")
		# A hash with no space before it is not a comment.
		self.assertEqual(gui._env_value("key#nospace"), "key#nospace")

	def test_values_that_would_not_survive_are_quoted(self):
		self.assertEqual(gui._env_value("has # hash"), '"has # hash"')
		self.assertEqual(gui._env_value(" leading"), '" leading"')
		self.assertEqual(gui._env_value("trailing "), '"trailing "')
		self.assertEqual(gui._env_value("has \"quote\""), '"has \\"quote\\""')


class TestEnvFile(unittest.TestCase):
	def setUp(self):
		self.directory = TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		self.path = Path(self.directory.name) / ".env"

	def _write(self, text: str) -> None:
		self.path.write_text(text, encoding="utf-8")

	def _read(self) -> str:
		return self.path.read_text(encoding="utf-8")

	def test_get_falls_back_to_the_default(self):
		self._write("QUERY_SOURCE=trends\n")
		env = gui.EnvFile(str(self.path))

		self.assertEqual(env.get("QUERY_SOURCE"), "trends")
		self.assertEqual(env.get("MISSING", "fallback"), "fallback")

	def test_update_replaces_a_key_without_touching_comments_or_unknown_keys(self):
		self._write(
			"# keep me\n"
			"QUERY_SOURCE=trends\n"
			"SOMETHING_ELSE=hidden\n"
		)
		gui.EnvFile(str(self.path)).update({"QUERY_SOURCE": "llm"})

		self.assertEqual(
			self._read(),
			"# keep me\n"
			"QUERY_SOURCE=llm\n"
			"SOMETHING_ELSE=hidden\n",
		)

	def test_update_appends_a_key_that_is_not_there_yet(self):
		self._write("QUERY_SOURCE=trends\n")
		gui.EnvFile(str(self.path)).update({"REWARDS_HEADLESS": "true"})

		self.assertEqual(self._read(), "QUERY_SOURCE=trends\nREWARDS_HEADLESS=true\n")

	def test_an_empty_value_removes_the_key(self):
		self._write("QUERY_SOURCE=trends\nEDGE_BINARY=C:\\Edge\\msedge.exe\n")
		gui.EnvFile(str(self.path)).update({"EDGE_BINARY": ""})

		self.assertEqual(self._read(), "QUERY_SOURCE=trends\n")

	def test_a_quoted_value_reads_back_unchanged(self):
		env = gui.EnvFile(str(self.path))
		env.update({"OPENROUTER_API_KEY": "has # hash and leading"})

		self.assertEqual(env.get("OPENROUTER_API_KEY"), "has # hash and leading")

	def test_updating_a_missing_file_creates_one(self):
		gui.EnvFile(str(self.path)).update({"QUERY_SOURCE": "llm"})

		self.assertEqual(self._read(), "QUERY_SOURCE=llm\n")


class TestSplitAccountNames(unittest.TestCase):
	def test_splits_on_commas_semicolons_and_whitespace(self):
		self.assertEqual(
			gui.split_account_names("a, b;c\nd   e\n\nf"),
			["a", "b", "c", "d", "e", "f"],
		)

	def test_blank_and_empty_input(self):
		self.assertEqual(gui.split_account_names(""), [])
		self.assertEqual(gui.split_account_names("  ,  ;  \n "), [])


class TestPartitionAccounts(unittest.TestCase):
	def test_everything_farms_when_nothing_is_excluded(self):
		farming, skipped = gui.partition_accounts(["a", "b", "c"], "")
		self.assertEqual(farming, ["a", "b", "c"])
		self.assertEqual(skipped, [])

	def test_excluded_names_are_held_back_and_order_is_kept(self):
		farming, skipped = gui.partition_accounts(["a", "b", "c", "d"], "b, d")
		self.assertEqual(farming, ["a", "c"])
		self.assertEqual(skipped, ["b", "d"])

	def test_matching_ignores_case_and_stale_names_are_ignored(self):
		farming, skipped = gui.partition_accounts(["Ada", "bob"], "ADA, someoneElse")
		self.assertEqual(farming, ["bob"])
		self.assertEqual(skipped, ["Ada"])

	def test_all_excluded_leaves_nothing_to_farm(self):
		farming, skipped = gui.partition_accounts(["a", "b"], "a;b")
		self.assertEqual(farming, [])
		self.assertEqual(skipped, ["a", "b"])


class TestReadProfileEmail(unittest.TestCase):
	def setUp(self):
		self.directory = TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		self.profile = Path(self.directory.name)

	def test_reads_the_email_from_preferences(self):
		(self.profile / "Default").mkdir()
		(self.profile / "Default" / "Preferences").write_text(
			'{"account_info": [{"email": "someone@outlook.com"}]}', encoding="utf-8")

		self.assertEqual(gui.read_profile_email(str(self.profile)), "someone@outlook.com")

	def test_signed_in_account_wins_over_an_older_recorded_one(self):
		# account_info keeps every account ever added to the profile, in no
		# particular order, so its first entry can be an older personal account
		# that is not the one actually farming. The profile's sign-in wins.
		(self.profile / "Default").mkdir()
		(self.profile / "Default" / "Preferences").write_text(
			'{"account_info": [{"email": "old@outlook.com"},'
			' {"email": "farmer@outlook.com"}]}', encoding="utf-8")
		(self.profile / "Local State").write_text(
			'{"profile": {"info_cache": {"Default": {"user_name": "farmer@outlook.com"}}}}',
			encoding="utf-8")

		self.assertEqual(gui.read_profile_email(str(self.profile)), "farmer@outlook.com")

	def test_falls_back_to_local_state_when_preferences_has_no_account(self):
		(self.profile / "Local State").write_text(
			'{"profile": {"info_cache": {"Default": {"user_name": "other@outlook.com"}}}}',
			encoding="utf-8")

		self.assertEqual(gui.read_profile_email(str(self.profile)), "other@outlook.com")

	def test_missing_folder_and_corrupt_files_do_not_raise(self):
		self.assertEqual(gui.read_profile_email(str(self.profile / "gone")), "no folder yet")

		(self.profile / "Default").mkdir()
		(self.profile / "Default" / "Preferences").write_text("{not json", encoding="utf-8")

		self.assertEqual(gui.read_profile_email(str(self.profile)), "not signed in")


class TestFailureReporting(unittest.TestCase):
	"""A crash started from the .bat must not vanish without a word.

	The launcher runs this file under `pythonw`, which has no console, so an
	exception on the way up used to look like the window simply refusing to
	open. `_report_failure` has to get the message somewhere durable.
	"""

	def test_the_message_is_written_to_the_log(self):
		with TemporaryDirectory() as directory:
			original = gui.REPO_ROOT
			gui.REPO_ROOT = directory
			self.addCleanup(setattr, gui, "REPO_ROOT", original)

			# The dialog is the other half, but a test has no one to click OK.
			with mock.patch.object(gui.messagebox, "showerror"):
				gui._report_failure("boom: the window fell over")

			log = Path(directory) / "gui-error.log"
			self.assertIn("boom: the window fell over", log.read_text(encoding="utf-8"))


class TestPalette(unittest.TestCase):
	def test_known_names_select_and_unknown_falls_back_to_light(self):
		self.assertEqual(gui.apply_palette("Dark"), "Dark")
		self.assertEqual(gui.apply_palette("Nonsense"), "Light")

		# Leave the module on the default so importing it elsewhere is not
		# affected by this test's ordering.
		gui.apply_palette("Light")

	def test_switching_updates_both_the_names_and_the_dict(self):
		gui.apply_palette("Dark")
		self.assertEqual(gui.APP_BG, gui.DARK_PALETTE["APP_BG"])
		self.assertEqual(gui.CONSOLE_BG, gui.DARK_PALETTE["CONSOLE_BG"])

		gui.apply_palette("Light")
		self.assertEqual(gui.APP_BG, gui.LIGHT_PALETTE["APP_BG"])


if __name__ == "__main__":
	unittest.main()
