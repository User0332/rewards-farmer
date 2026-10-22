"""Build operations, no app launch or package installation."""

import argparse
import ast
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import uuid
import zipfile

from layout import EXCLUDES, app_data, NON_WINDOWS_MANAGERS

RUNTIME_DISTRIBUTIONS = (
	"selenium", "requests", "python-dotenv", "Pillow", "numpy", "pywebview",
	"pystray", "pythonnet", "clr-loader", "proxy-tools", "bottle", "typing-extensions",
	"certifi", "charset-normalizer", "idna", "urllib3", "trio", "trio-websocket",
	"attrs", "sortedcontainers", "outcome", "sniffio", "PySocks", "websocket-client",
	"wsproto", "h11", "cffi", "pycparser", "six", "setuptools", "packaging", "pyinstaller",
)


def write_json(path, value):
	Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def digest(path):
	with Path(path).open("rb") as handle:
		return hashlib.file_digest(handle, "sha256").hexdigest()


def version(root):
	# Evaluate no module code while determining release identity.
	tree = ast.parse((Path(root) / "app/rfapp/__init__.py").read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
			value = ast.literal_eval(node.value)
			if isinstance(value, str) and len(value.split(".")) == 3 and all(part.isdecimal() for part in value.split(".")):
				return value
	raise ValueError("rfapp.__version__ must contain three numeric components")


def tool_hashes(tools):
	return {path.relative_to(tools).as_posix(): digest(path) for path in sorted(Path(tools).rglob("*")) if path.is_file() and "__pycache__" not in path.parts}


def notices(assets, tools):
	directory = assets / "third-party"
	directory.mkdir()
	rows = []
	for name in RUNTIME_DISTRIBUTIONS:
		dist = metadata.distribution(name)
		dest = directory / dist.metadata["Name"]
		dest.mkdir()
		files = []
		for item in dist.files or ():
			if not any(token in item.name.lower() for token in ("license", "copying", "notice", "authors")):
				continue
			source = Path(dist.locate_file(item))
			if not source.is_file():
				continue
			target = dest / str(item).replace("\\", "_").replace("/", "_")
			target.write_bytes(source.read_bytes())
			files.append(target.name)
		if name == "proxy-tools" and not files:
			license_path = Path(tools) / "licenses/proxy-tools-LICENSE.txt"
			(dest / license_path.name).write_bytes(license_path.read_bytes())
			files.append(license_path.name)
		if not files:
			raise ValueError(f"No installed license/notice found for {name}")
		rows.append({"name": dist.metadata["Name"], "version": dist.version, "notices": files})
	python_license = Path(sys.base_prefix) / "LICENSE.txt"
	(directory / "Python-LICENSE.txt").write_bytes(python_license.read_bytes())
	(directory / "THIRD-PARTY-NOTICES.txt").write_text(
		"Rewards Farmer includes the packages and matching notices listed in packages.json.\n"
		"pystray is LGPLv3. Its matching Python source is outside the executable archive\n"
		"under _internal/pystray. You may modify or replace it with compatible source\n"
		"and recombine it with this application. COPYING and COPYING.LGPL are included.\n"
		"Project and Python licenses are included separately.\n", encoding="utf-8")
	write_json(directory / "packages.json", rows)
	return rows


def prepare(args):
	work = Path(args.work)
	root = work / "source"
	root.mkdir()
	with zipfile.ZipFile(work / "source.zip") as archive:
		for item in archive.infolist():
			target = (root / item.filename).resolve()
			if not target.is_relative_to(root.resolve()):
				raise ValueError("Archive entry escapes the clean source directory")
		archive.extractall(root)
	for directory in ("assets", "tmp", "pyinstaller-cache", "installer"):
		(work / directory).mkdir()
	assets = work / "assets"
	from PIL import Image
	with Image.open(root / "app/ui/img/app-icon-256.png") as image:
		image.save(assets / "app.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
	packages = notices(assets, args.tools)
	tools = tool_hashes(args.tools)
	archived_tools = root / "app/packaging"
	tools_match = archived_tools.is_dir() and tool_hashes(archived_tools) == tools
	write_json(work / "source-identity.json", {
		"commit": args.commit, "archive_sha256": digest(work / "source.zip"),
		"version": version(root), "packaging_tools": tools,
		"packaging_source": "archived commit" if tools_match else "first-pass tooling outside archived commit",
		"pyproject_sha256": digest(root / "pyproject.toml"), "lock_sha256": digest(root / "poetry.lock"),
		"python": sys.version, "runtime_packages": packages,
	})


def freeze(args):
	work = Path(args.work)
	command = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--distpath", str(work / "dist"), "--workpath", str(work / "build"), args.spec]
	start = time.perf_counter()
	with (work / "build.log").open("w", encoding="utf-8") as log:
		result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=900, cwd=work / "source")
	write_json(work / "freeze-result.json", {"command": command, "exit_code": result.returncode, "seconds": round(time.perf_counter() - start, 3)})
	if result.returncode:
		print((work / "build.log").read_text(encoding="utf-8", errors="replace"))
	return result.returncode


def manifest(args):
	work = Path(args.work)
	bundle = work / "dist/RewardsFarmer"
	internal = bundle / "_internal"
	paths = sorted(path for path in bundle.rglob("*") if path.is_file())
	rows = [{"path": path.relative_to(bundle).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)} for path in paths]
	assert (bundle / "RewardsFarmer.exe").is_file()
	pe = (bundle / "RewardsFarmer.exe").read_bytes()
	pe_offset = struct.unpack_from("<I", pe, 0x3c)[0]
	assert struct.unpack_from("<H", pe, pe_offset + 4)[0] == 0x8664, "Not x64"
	assert struct.unpack_from("<H", pe, pe_offset + 24 + 68)[0] == 2, "Not windowed"
	from PyInstaller.archive.readers import pkg_archive_contents
	modules = pkg_archive_contents(str(bundle / "RewardsFarmer.exe"))
	assert all(module.split(".")[0] not in EXCLUDES for module in modules), "Excluded Python package in executable archive"
	assert {"rfapp.paths", "rfapp.supervisor", "rfapp.diagnostics", "main", "numpy", "selenium.webdriver.edge.options"}.issubset(modules), "Dynamic runtime module missing"
	for source, destination in app_data(work / "source"):
		target = internal / destination / Path(source).name
		assert target.is_file() and digest(target) == digest(source), f"Resource mismatch: {source}"
	assert not (internal / "app/ui/mock").exists()
	assert (internal / "selenium/webdriver/common/windows/selenium-manager.exe").is_file()
	assert any(path.name == "Python.Runtime.dll" for path in paths), "pythonnet runtime DLL missing"
	assert any(path.name == "WebView2Loader.dll" and "x64" in str(path) for path in paths), "x64 WebView2 loader missing"
	assert any(path.name == "Microsoft.Web.WebView2.Core.dll" for path in paths), "WebView2 core assembly missing"
	assert not any(any(prefix in row["path"] for prefix in NON_WINDOWS_MANAGERS) for row in rows)
	assert (internal / "pystray/__init__.py").is_file(), "LGPL source is not replaceable"
	licenses = list((internal / "third-party/pystray").glob("*"))
	assert any("COPYING.LGPL" in path.name for path in licenses) and any(path.name.endswith("COPYING") for path in licenses)
	assert (internal / "numpy").is_dir(), "The farmer requires numpy"
	assert not any(row["path"].split("/")[1] in EXCLUDES for row in rows if row["path"].startswith("_internal/"))
	assert not any(Path(row["path"]).name == ".env" or "data-dir" in Path(row["path"]).parts for row in rows)
	value = {"source": json.loads((work / "source-identity.json").read_text(encoding="utf-8")), "bundle_bytes": sum(row["bytes"] for row in rows), "bundle_files": len(rows), "python_archive_modules": modules, "files": rows}
	write_json(work / "bundle-manifest.json", value)
	print(f"Bundle: {value['bundle_bytes']} bytes, {len(rows)} files")


def signature_status(path):
	env = os.environ.copy()
	env["RF_SIGNATURE_FILE"] = str(path)
	result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "(Get-AuthenticodeSignature -LiteralPath $env:RF_SIGNATURE_FILE).Status.ToString()"], capture_output=True, stdin=subprocess.DEVNULL, timeout=30, env=env)
	assert result.returncode == 0, "Could not read Authenticode status"
	return result.stdout.decode("utf-8", errors="replace").strip()


def finish(args):
	work = Path(args.work)
	setup = work / "installer" / f"RewardsFarmer-{args.version}-windows-x64-Setup.exe"
	manifest = json.loads((work / "bundle-manifest.json").read_text(encoding="utf-8"))
	assert setup.is_file()
	setup_signature = signature_status(setup)
	exe_signature = signature_status(work / "dist/RewardsFarmer/RewardsFarmer.exe")
	assert setup_signature == "NotSigned" and exe_signature == "NotSigned", "Unsigned build identity changed; use a separate approved signing flow"
	result = {"version": args.version, "setup": str(setup), "setup_bytes": setup.stat().st_size, "setup_sha256": digest(setup), "bundle_bytes": manifest["bundle_bytes"], "manifest": str(work / "bundle-manifest.json"), "freeze": json.loads((work / "freeze-result.json").read_text(encoding="utf-8")), "compile_seconds": round(args.compile_seconds, 3) if args.compile_seconds is not None else None, "signing": "unsigned", "setup_authenticode": setup_signature, "exe_authenticode": exe_signature}
	write_json(work / "build-result.json", result)
	(work / "installer" / (setup.name + ".sha256")).write_text(f"{result['setup_sha256']}  {setup.name}\n", encoding="ascii")
	write_json(work / "installer-tool-identity.json", tool_hashes(Path(__file__).resolve().parent))
	print(f"Setup.exe: {setup} ({result['setup_bytes']} bytes, unsigned)")
	print(f"Installed payload: {result['bundle_bytes']} bytes, before the uninstaller and user data")
	print(f"Manifest: {result['manifest']}")


def main():
	parser = argparse.ArgumentParser()
	sub = parser.add_subparsers(dest="command", required=True)
	workspace = sub.add_parser("workspace")
	workspace.add_argument("--scratch", required=True)
	workspace.add_argument("--commit", required=True)
	prep = sub.add_parser("prepare")
	prep.add_argument("--work", required=True)
	prep.add_argument("--commit", required=True)
	prep.add_argument("--tools", required=True)
	ver = sub.add_parser("version")
	ver.add_argument("--root", required=True)
	frz = sub.add_parser("freeze")
	frz.add_argument("--work", required=True)
	frz.add_argument("--spec", required=True)
	mft = sub.add_parser("manifest")
	mft.add_argument("--work", required=True)
	end = sub.add_parser("finish")
	end.add_argument("--work", required=True)
	end.add_argument("--version", required=True)
	end.add_argument("--compile-seconds", type=float)
	args = parser.parse_args()
	if args.command == "workspace":
		path = Path(args.scratch).resolve() / (args.commit[:12] + "-" + uuid.uuid4().hex[:8])
		path.mkdir(parents=True)
		print(path)
	elif args.command == "version":
		print(version(args.root))
	else:
		return globals()[args.command](args)
	return 0


if __name__ == "__main__":
	sys.exit(main())
