from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

packaging = Path(SPEC).resolve().parent
sys.path.insert(0, str(packaging))
from layout import EXCLUDES, app_data, windows_only

root = Path(os.environ.get("RF_SOURCE_ROOT", str(packaging.parents[1]))).resolve()
sys.path.insert(0, str(root / "app"))
assets = Path(os.environ["RF_BUILD_ASSETS"])
datas = app_data(root)
datas += collect_data_files("webview") + collect_data_files("selenium")
datas += copy_metadata("pystray")
datas += [(str(path), "third-party/" + path.parent.relative_to(assets / "third-party").as_posix())
	for path in sorted((assets / "third-party").rglob("*")) if path.is_file()]
binaries = collect_dynamic_libs("pythonnet") + collect_dynamic_libs("selenium")
hiddenimports = ["main", "llm_utils", "random_image_for_visual_search", "pystray._win32"]
hiddenimports += collect_submodules("rfapp") + collect_submodules("selenium")

a = Analysis(
	[str(root / "app" / "launcher.py")], pathex=[str(root / "app"), str(root / "src")],
	binaries=windows_only(binaries), datas=windows_only(datas), hiddenimports=hiddenimports,
	hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=list(EXCLUDES),
	noarchive=False, optimize=0, module_collection_mode={"pystray": "py"},
)
a.datas = windows_only(a.datas, toc=True)
a.binaries = windows_only(a.binaries, toc=True)
pyz = PYZ(a.pure)
exe = EXE(
	pyz, a.scripts, [], exclude_binaries=True, name="RewardsFarmer",
	icon=str(assets / "app.ico"), debug=False, strip=False, upx=False, console=False,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="RewardsFarmer")
