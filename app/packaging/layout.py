"""Pure resource rules shared by the spec and its browser-free tests."""

from pathlib import Path

EXCLUDES = (
	"matplotlib", "pygame", "keyboard", "pygetwindow", "pyrect", "ollama",
	"pydantic", "httpx", "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
	"gi", "cefpython3",
)
NON_WINDOWS_MANAGERS = (
	"selenium/webdriver/common/linux/", "selenium/webdriver/common/macos/",
)


def app_data(root: Path) -> list[tuple[str, str]]:
	ui = root / "app" / "ui"
	result = []
	for path in sorted(ui.rglob("*")):
		if not path.is_file() or "mock" in path.relative_to(ui).parts or "__pycache__" in path.parts:
			continue
		result.append((str(path), path.parent.relative_to(root).as_posix()))
	for name, destination in (("app/rfapp/cldr_plurals.json", "app/rfapp"), ("nouns.txt", "."), ("LICENSE", ".")):
		path = root / name
		if not path.is_file():
			raise FileNotFoundError(path)
		result.append((str(path), destination))
	return result


def windows_only(entries, *, toc: bool = False):
	# collect_data_files uses (source, destination); Analysis TOCs reverse those.
	result = []
	for entry in entries:
		path = entry[0] if toc else entry[1] + "/" + Path(entry[0]).name
		if not path.replace("\\", "/").startswith(NON_WINDOWS_MANAGERS):
			result.append(entry)
	return result
