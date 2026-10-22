import _bootstrap  # noqa: F401

import ast
import re
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1]
TOPICS = {
	"run.started", "run.snapshot", "run.log", "run.finished",
	"overview.changed", "signin.status", "settings.changed",
	"update.available", "theme.changed", "navigate",
}


def pushed_topics(root):
	result = set()
	for path in root.glob("*.py"):
		for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
			if not isinstance(node, ast.Call) or not node.args:
				continue
			name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
			if name in ("push", "_push") and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
				result.add(node.args[0].value)
	return result


def listened_topics(root):
	result = set()
	pattern = re.compile(r"\b(?:services\.)?bridge\.on\s*\(\s*([\"'])([^\"']+)\1")
	for path in root.rglob("*.js"):
		text = path.read_text(encoding="utf-8")
		# Ignore comments without corrupting strings containing URLs.
		text = re.sub(r"/\*[\s\S]*?\*/|(?m:^\s*//.*$)", "", text)
		result.update(match.group(2) for match in pattern.finditer(text))
	return result


class PushTopicTests(unittest.TestCase):
	def test_python_pushes_equal_frozen_table(self):
		self.assertEqual(TOPICS, pushed_topics(APP / "rfapp"))

	def test_page_listeners_equal_frozen_table(self):
		self.assertEqual(TOPICS, listened_topics(APP / "ui/js"))

	def test_every_push_has_a_listener_and_every_listener_a_push(self):
		self.assertEqual(pushed_topics(APP / "rfapp"), listened_topics(APP / "ui/js"))


if __name__ == "__main__":
	unittest.main()
