"""The exit prompt: it must never turn a clean finish into a traceback.

The GUI runs main.py with stdin on the null device. On Windows that device is
a character device, so isatty() reports True and input() is reached - and then
raises EOFError at once. The prompt has to tell the two apart, because the
line after it tidies up the virtual desktop and a traceback would skip it.
"""

import builtins
import os
import sys
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)

import main


class _FakeStdin:
	def __init__(self, tty: bool):
		self._tty = tty

	def isatty(self) -> bool:
		return self._tty


class TestStdinIsInteractive(unittest.TestCase):
	def test_no_stdin_is_not_interactive(self):
		with mock.patch.object(main.sys, "stdin", None):
			self.assertFalse(main.stdin_is_interactive())

	def test_posix_falls_back_to_isatty(self):
		with mock.patch.object(main.os, "name", "posix"), \
			mock.patch.object(main.sys, "stdin", _FakeStdin(True)):
			self.assertTrue(main.stdin_is_interactive())

		with mock.patch.object(main.os, "name", "posix"), \
			mock.patch.object(main.sys, "stdin", _FakeStdin(False)):
			self.assertFalse(main.stdin_is_interactive())

	def test_windows_asks_the_console_not_isatty(self):
		# The null device is a character device, so isatty() would say True
		# here. The console check is what has to decide.
		with mock.patch.object(main.os, "name", "nt"), \
			mock.patch.object(main.sys, "stdin", _FakeStdin(True)), \
			mock.patch.object(main, "_windows_console_attached", return_value=False):
			self.assertFalse(main.stdin_is_interactive())

		with mock.patch.object(main.os, "name", "nt"), \
			mock.patch.object(main.sys, "stdin", _FakeStdin(True)), \
			mock.patch.object(main, "_windows_console_attached", return_value=True):
			self.assertTrue(main.stdin_is_interactive())


class TestPauseForEnter(unittest.TestCase):
	def test_interactive_prompts_once(self):
		with mock.patch.object(main, "stdin_is_interactive", return_value=True), \
			mock.patch.object(builtins, "input", return_value="") as prompted:
			main.pause_for_enter()

		prompted.assert_called_once()

	def test_end_of_file_on_a_console_does_not_raise(self):
		with mock.patch.object(main, "stdin_is_interactive", return_value=True), \
			mock.patch.object(builtins, "input", side_effect=EOFError):
			main.pause_for_enter()

	def test_non_interactive_is_not_prompted(self):
		with mock.patch.object(main, "stdin_is_interactive", return_value=False), \
			mock.patch.object(builtins, "input") as prompted:
			main.pause_for_enter()

		prompted.assert_not_called()

	def test_headless_is_never_prompted(self):
		with mock.patch.object(main, "HEADLESS", True), \
			mock.patch.object(main, "stdin_is_interactive", return_value=True), \
			mock.patch.object(builtins, "input") as prompted:
			main.pause_for_enter()

		prompted.assert_not_called()


if __name__ == "__main__":
	unittest.main()
