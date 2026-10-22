"""List missing and stale translations without contacting any translation service."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rfapp import i18n, paths  # noqa: E402


def source_hash(entry: dict) -> str:
	content = {key: value for key, value in entry.items() if key != "note"}
	canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def report(locale: str, root: Path | None = None) -> dict:
	if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*", locale):
		raise ValueError("Invalid locale code")
	root = root or paths.ui_dir() / "i18n"
	index = json.loads((root / "index.json").read_text(encoding="utf-8"))
	source = index["source"]
	missing, stale = [], []
	for namespace in index["namespaces"]:
		source_path = root / source / f"{namespace}.json"
		if not source_path.is_file():
			continue
		entries = json.loads(source_path.read_text(encoding="utf-8"))["messages"]
		target_path = root / locale / f"{namespace}.json"
		target = json.loads(target_path.read_text(encoding="utf-8"))["messages"] if target_path.is_file() else {}
		for key, entry in entries.items():
			# Mock-only copy falls back to English and must never become translation work.
			if key.startswith(("shell.mock.", "shell.demo.")):
				continue
			if key not in target:
				missing.append(key)
			elif locale != source and target[key].get("src") != source_hash(entry):
				stale.append(key)
	return {
		"locale": locale,
		"missing": sorted(missing),
		"stale": sorted(stale),
		"plural_categories": [category for category in i18n._categories if "pluralRule-count-" + category in i18n._rules_for(locale)],
		"dir": i18n._direction(locale, index),
	}


def main(argv=None) -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("locale")
	parser.add_argument("--json", action="store_true")
	args = parser.parse_args(argv)
	result = report(args.locale)
	if args.json:
		print(json.dumps(result, ensure_ascii=False))
	else:
		print(f"{result['locale']}: {len(result['missing'])} missing, {len(result['stale'])} stale")
		for field in ("missing", "stale"):
			for key in result[field]:
				print(f"{field}: {key}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
