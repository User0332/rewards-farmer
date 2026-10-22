"""Edit dotenv bindings without rebuilding the surrounding file."""

import io
import os
import re
import tempfile
from pathlib import Path

from dotenv.parser import parse_stream
from file_replace import read_file, replace_file


_BARE = re.compile(r"^[A-Za-z0-9_./:\\,@%+=-]+$")
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_APP_COMMENT = "# Written by the Rewards Farmer app"


class EnvFile:
	def __init__(self, path: Path, text: str):
		self.path = Path(path)
		self._bom = "\ufeff" if text.startswith("\ufeff") else ""
		self._text = text.removeprefix("\ufeff")
		self._newline = next(iter(re.findall(r"\r\n|\n|\r", self._text)), "\n")

	@classmethod
	def load(cls, path: Path) -> "EnvFile":
		try:
			return cls(path, read_file(path, newline=""))
		except FileNotFoundError:
			return cls(path, "")

	def get(self, key: str) -> str | None:
		value = None
		for binding in parse_stream(io.StringIO(self._text)):
			if binding.key == key and not binding.error:
				value = binding.value
		return value

	def set(self, key: str, value: str) -> None:
		if not _KEY.fullmatch(key):
			raise ValueError("Invalid dotenv key")
		if not isinstance(value, str):
			raise TypeError("Dotenv values must be strings")
		if not value:
			self.unset(key)
			return
		quoted = value if _BARE.fullmatch(value) else "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
		bindings = list(parse_stream(io.StringIO(self._text)))
		matches = [index for index, binding in enumerate(bindings) if binding.key == key and not binding.error and binding.value is not None]
		# A bare key after an assignment masks it in dotenv_values. Appending
		# restores the requested effective value without dropping either line.
		if matches and any(binding.key == key and binding.value is None and not binding.error for binding in bindings[matches[-1] + 1:]):
			matches = []
		if matches:
			index = matches[-1]
			original = bindings[index].original.string
			prefix, suffix = self._surroundings(original)
			bindings[index] = bindings[index]._replace(original=bindings[index].original._replace(string=f"{prefix}{key}={quoted}{suffix}"))
			self._text = "".join(binding.original.string for binding in bindings)
		else:
			if self._text and not self._text.endswith(("\r", "\n")):
				self._text += self._newline
			if _APP_COMMENT not in self._text.splitlines():
				self._text += _APP_COMMENT + self._newline
			self._text += f"{key}={quoted}{self._newline}"

	def unset(self, key: str) -> None:
		parts = []
		for binding in parse_stream(io.StringIO(self._text)):
			if binding.key != key or binding.error:
				parts.append(binding.original.string)
			else:
				if binding.value is None:
					match = re.match(r"(\s*)(?:export[ \t]+)?(?:'[^']+'|[^=\s#]+)([ \t]*(?:#[^\r\n]*)?(?:\r\n|\n|\r|$))$", binding.original.string)
					prefix, suffix = match.groups() if match else ("", "")
				else:
					prefix, suffix = self._surroundings(binding.original.string)
				# A parser binding can own blank lines before the assignment.
				prefix = re.sub(r"(?:export[ \t]+)$", "", prefix)
				parts.append(prefix)
				if "#" in suffix:
					parts.append(suffix.lstrip(" \t"))
		self._text = "".join(parts)

	@staticmethod
	def _surroundings(original: str) -> tuple[str, str]:
		match = re.match(r"(\s*(?:export[ \t]+)?)(?:'[^']+'|[^=\s#]+)[ \t]*=[ \t]*", original)
		if match is None:
			return "", ""
		start = match.end()
		end = start
		if start < len(original) and original[start] in "'\"":
			quote = original[start]
			end += 1
			while end < len(original):
				if original[end] == "\\":
					end += 2
				elif original[end] == quote:
					end += 1
					break
				else:
					end += 1
		else:
			line = re.match(r"[^\r\n]*", original[start:]).group()
			comment = re.search(r"[ \t]+#", line)
			end = start + (comment.start() if comment else len(line.rstrip(" \t")))
		return match.group(1), original[end:]

	def save(self) -> None:
		self.path.parent.mkdir(parents=True, exist_ok=True)
		name = None
		try:
			with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=self.path.parent, prefix=".env-", delete=False) as stream:
				name = stream.name
				stream.write(self._bom + self._text)
				stream.flush()
				os.fsync(stream.fileno())
			replace_file(name, self.path)
		finally:
			if name is not None:
				Path(name).unlink(missing_ok=True)
