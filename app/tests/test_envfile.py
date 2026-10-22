import _bootstrap  # noqa: F401

import io
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values
from file_replace import replace_file
from rfapp.envfile import EnvFile


class EnvFileTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.path = Path(self.temp.name) / ".env"

	def write(self, text):
		self.path.write_bytes(text.encode("utf-8"))
		return EnvFile.load(self.path)

	def test_byte_identical_corpus(self):
		corpus = ["", "# comment\n\nA=one\nUNKNOWN='two words'\n", "# comment\r\n\r\nexport A=first\r\nA=last # keep\r\n", "A='one\ntwo'\nB=\"escaped\\nline\"\n", "A=one", "BAD='unterminated\nA=good\n", "\ufeffA=one\r\n", "A=one\rB=two\r", "BARE_KEY\nA=2\n"]
		for text in corpus:
			with self.subTest(text=text):
				editor = self.write(text)
				editor.save()
				self.assertEqual(self.path.read_bytes(), text.encode("utf-8"))

	def test_last_duplicate_and_surrounding_comments(self):
		text = "# header\r\nA=first\r\n\r\nexport A='last' # note\r\nUNKNOWN=7\r\n"
		editor = self.write(text)
		self.assertEqual(editor.get("A"), "last")
		editor.set("A", "two words")
		editor.save()
		self.assertEqual(self.path.read_bytes(), text.replace("'last'", "'two words'").encode())
		self.assertEqual(dotenv_values(self.path)["A"], "two words")

	def test_edited_value_quotes_hashes_and_multiline(self):
		for original in ["A=old\n", "A='old # value' # keep\n", 'A="old\\\" value" # keep\n', "\n\nA='old\nvalue'\n"]:
			for value in ["abc_12./:\\,@%+=-", "hello world", "O'Reilly\\path", "a#b", "a\nb", "$literal", "üß"]:
				with self.subTest(original=original, value=value):
					editor = self.write(original)
					editor.set("A", value)
					editor.save()
					self.assertEqual(dotenv_values(self.path)["A"], value)
					self.assertEqual(EnvFile.load(self.path).get("A"), value)
					if "# keep" in original:
						self.assertIn("# keep", self.path.read_text())

	def test_unset_removes_duplicates_not_comments_or_blank_lines(self):
		editor = self.write("# header\nA=one\n\nA=two # keep\nB=3\n")
		editor.unset("A")
		editor.save()
		self.assertEqual(self.path.read_text(), "# header\n\n# keep\nB=3\n")
		self.assertIsNone(editor.get("A"))

	def test_clear_removes_assignment(self):
		editor = self.write("USER_DATA_DIR=somewhere\nUSER_DATA_DIR=elsewhere\n")
		editor.set("USER_DATA_DIR", "")
		editor.save()
		self.assertNotIn("USER_DATA_DIR", dotenv_values(self.path))

	def test_missing_and_append_marker_once(self):
		editor = EnvFile.load(self.path)
		self.assertIsNone(editor.get("A"))
		editor.set("A", "one")
		editor.set("B", "two")
		editor.save()
		self.assertEqual(self.path.read_text(), "# Written by the Rewards Farmer app\nA=one\nB=two\n")

	def test_append_preserves_crlf_and_unterminated_last_line(self):
		editor = self.write("A=one\r\nB=two")
		editor.set("C", "three")
		editor.save()
		self.assertEqual(self.path.read_bytes(), b"A=one\r\nB=two\r\n# Written by the Rewards Farmer app\r\nC=three\r\n")

	def test_all_example_keys_are_preserved(self):
		fixture = Path(__file__).parent / "fixtures" / "env" / "all-keys.env"
		text = fixture.read_text(encoding="utf-8")
		example = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
		keys = set(re.findall(r"(?m)^(?:# )?([A-Z_]+)=", example))
		self.assertTrue(keys.issubset(dotenv_values(stream=io.StringIO(text))))
		editor = self.write(text)
		editor.set("REWARDS_HEADLESS", "true")
		editor.save()
		self.assertEqual(self.path.read_text(), text.replace("REWARDS_HEADLESS=false", "REWARDS_HEADLESS=true"))
		self.assertEqual(dotenv_values(self.path)["UNKNOWN_USER_KEY"], "leave this alone")

	def test_atomic_replace_and_failure_cleanup(self):
		editor = self.write("A=old\n")
		editor.set("A", "new")
		with patch("rfapp.envfile.os.replace", side_effect=OSError("locked")):
			with self.assertRaises(OSError):
				editor.save()
		self.assertEqual(self.path.read_text(), "A=old\n")
		self.assertEqual(list(self.path.parent.iterdir()), [self.path])

	def test_save_uses_shared_helper_after_one_permission_failure(self):
		editor = self.write("A=old\n")
		editor.set("A", "new")
		real_replace = os.replace
		attempts = []
		def replace(source, target):
			attempts.append((source, target))
			if len(attempts) == 1:
				raise PermissionError("reader open")
			real_replace(source, target)
		with patch("rfapp.envfile.replace_file", wraps=replace_file) as helper, patch("file_replace.os.replace", side_effect=replace), patch("file_replace.time.sleep"):
			editor.save()
		helper.assert_called_once()
		self.assertEqual(len(attempts), 2)
		self.assertEqual(self.path.read_text(), "A=new\n")
		self.assertEqual(list(self.path.parent.iterdir()), [self.path])

	@unittest.skipUnless(sys.platform == "win32", "Windows reader handles do not share delete")
	def test_save_waits_for_plain_reader_in_another_thread(self):
		editor = self.write("A=old\n")
		editor.set("A", "new")
		opened = threading.Event()
		errors = []
		def read():
			try:
				with self.path.open("r", encoding="utf-8"):
					opened.set()
					time.sleep(0.3)
			except BaseException as exc:
				errors.append(exc)
		thread = threading.Thread(target=read)
		thread.start()
		try:
			self.assertTrue(opened.wait(3), "reader did not open")
			editor.save()
		finally:
			thread.join(3)
		self.assertFalse(thread.is_alive())
		self.assertEqual(errors, [])
		self.assertEqual(self.path.read_text(), "A=new\n")
		self.assertEqual(list(self.path.parent.iterdir()), [self.path])

	def test_no_environment_load(self):
		editor = self.write("RF_ENVFILE_ISOLATION=edited\n")
		with patch.dict(os.environ, {"RF_ENVFILE_ISOLATION": "original"}):
			editor.set("RF_ENVFILE_ISOLATION", "saved")
			editor.save()
			self.assertEqual(os.environ["RF_ENVFILE_ISOLATION"], "original")

	def test_invalid_key_rejected(self):
		editor = EnvFile.load(self.path)
		with self.assertRaises(ValueError):
			editor.set("A\nBAD", "value")

	def test_bare_key_does_not_mask_edited_or_cleared_value(self):
		editor = self.write("A=old\nA\n")
		editor.set("A", "new")
		editor.save()
		self.assertEqual(dotenv_values(self.path)["A"], "new")
		editor.unset("A")
		editor.save()
		self.assertNotIn("A", dotenv_values(self.path))
		editor = self.write("# header\n\nA # comment\n\nUNKNOWN=value\n")
		editor.unset("A")
		editor.save()
		self.assertEqual(self.path.read_text(), "# header\n\n# comment\n\nUNKNOWN=value\n")

	def test_setting_multi_line_keeps_following_unknown_and_blank(self):
		editor = self.write("A='first\nsecond' # inline\n\nUNKNOWN='unchanged'\n")
		editor.set("A", "new")
		editor.save()
		self.assertEqual(self.path.read_text(), "A=new # inline\n\nUNKNOWN='unchanged'\n")

	def test_dotenv_interpolation_is_quote_agnostic(self):
		with patch.dict(os.environ, {"RF_PROBE_VALUE": "expanded"}):
			self.assertEqual(dotenv_values(stream=io.StringIO("A='${RF_PROBE_VALUE}'\n"))["A"], "expanded")


if __name__ == "__main__":
	unittest.main()
