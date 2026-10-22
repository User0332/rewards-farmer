import _bootstrap  # noqa: F401

import base64
import ctypes
import io
import json
import logging
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
import zlib

from PIL import Image
from rfapp import accounts_admin as admin, diagnostics, prefs


class AccountPictureTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.home = Path(self.temp.name)
		self.profiles = self.home / "profiles"
		self.profiles.mkdir()
		(self.home / ".env").write_text("QUERY_SOURCE=trends\nUSER_DATA_DIR='" + str(self.profiles) + "'\nREWARDS_ACCOUNTS=account-1\n", encoding="utf-8")
		item = patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.home)})
		item.start()
		self.addCleanup(item.stop)
		for cache in (admin._identity_cache, admin._picture_cache, admin._picture_warnings):
			cache.clear()
			self.addCleanup(cache.clear)
		translator = ModuleType("rfapp.i18n")
		translator.current = lambda: SimpleNamespace(t=lambda key, **params: "Synthetic Account " + str(params.get("number", 1)))
		translator.resolve = lambda *args: ("en", "ltr", "en-US")
		item = patch.dict(sys.modules, {"rfapp.i18n": translator})
		item.start()
		self.addCleanup(item.stop)
		import rfapp
		item = patch.object(rfapp, "i18n", translator, create=True)
		item.start()
		self.addCleanup(item.stop)
		for target in ("subprocess.Popen", "subprocess.run", "requests.sessions.Session.request", "selenium.webdriver.Edge"):
			item = patch(target, side_effect=AssertionError("No process, browser or network in picture test"))
			item.start()
			self.addCleanup(item.stop)

	def profile(self, account="account-1"):
		return self.profiles if account == "@root" else self.profiles / account

	def metadata(self, account="account-1", **updates):
		info = {"user_name": "sentinel-picture@example.invalid", "gaia_name": "Sentinel Picture Name", "gaia_picture_file_name": "Edge Profile Picture.png", "use_gaia_picture": True, "is_using_default_avatar": False, "last_downloaded_gaia_picture_url_with_size": "https://example.invalid/never-requested"}
		info.update(updates)
		root = self.profile(account)
		(root / "Default").mkdir(parents=True, exist_ok=True)
		(root / "Local State").write_text(json.dumps({"profile": {"info_cache": {"Default": info}}}), encoding="utf-8")
		admin._identity_cache.clear()
		return root / "Default" / info["gaia_picture_file_name"] if isinstance(info["gaia_picture_file_name"], str) else None

	def picture(self, format="PNG", *, account="account-1", image=None, name="Edge Profile Picture.png", **save):
		path = self.metadata(account, gaia_picture_file_name=name)
		image = image if image is not None else Image.new("RGB", (420, 420), (30, 140, 220))
		image.save(path, format=format, **save)
		return path

	def decoded(self, result):
		self.assertEqual(set(result), {"account_id", "rev", "data_url"})
		self.assertRegex(result["rev"], r"^[0-9a-f]{16}$")
		self.assertTrue(result["data_url"].startswith("data:image/png;base64,"))
		image = Image.open(io.BytesIO(base64.b64decode(result["data_url"].split(",", 1)[1])))
		self.addCleanup(image.close)
		self.assertEqual(image.format, "PNG")
		self.assertLessEqual(max(image.size), 128)
		self.assertEqual(image.width, image.height)
		return image

	def test_png_and_webp_are_reencoded_as_small_png(self):
		for format in ("PNG", "WEBP"):
			with self.subTest(format=format):
				admin._picture_cache.clear()
				self.picture(format)
				image = self.decoded(admin.get_account_picture("account-1"))
				self.assertEqual(image.size, (128, 128))
				for actual, expected in zip(image.getpixel((64, 64))[:3], (30, 140, 220)):
					self.assertLessEqual(abs(actual - expected), 3)

	def test_jpeg_exif_orientation_applies_before_crop_and_metadata_is_removed(self):
		image = Image.new("RGB", (80, 160), "red")
		image.paste("blue", (0, 80, 80, 160))
		exif = Image.Exif()
		exif[274] = 6
		exif[270] = "sentinel-picture@example.invalid"
		self.picture("JPEG", image=image, exif=exif, quality=95)
		result = admin.get_account_picture("account-1")
		decoded = self.decoded(result)
		self.assertEqual(decoded.size, (80, 80))
		self.assertGreater(decoded.getpixel((5, 40))[2], 180)
		self.assertGreater(decoded.getpixel((75, 40))[0], 180)
		self.assertFalse(decoded.getexif())
		self.assertNotIn(b"sentinel-picture@example.invalid", base64.b64decode(result["data_url"].split(",", 1)[1]))

	def test_wide_and_tall_images_crop_the_center_square(self):
		for size, center in (((300, 100), (100, 0, 200, 100)), ((100, 300), (0, 100, 100, 200))):
			with self.subTest(size=size):
				admin._picture_cache.clear()
				image = Image.new("RGB", size, "red")
				image.paste("green", center)
				self.picture(image=image)
				decoded = self.decoded(admin.get_account_picture("account-1"))
				self.assertEqual(decoded.size, (100, 100))
				self.assertEqual(decoded.getpixel((0, 0))[:3], (0, 128, 0))
				self.assertEqual(decoded.getpixel((99, 99))[:3], (0, 128, 0))

	def test_list_accounts_only_stats_picture_and_never_opens_or_decodes_it(self):
		path = self.picture()
		# Warm only Local State, so any picture open remains forbidden.
		admin._profile_identity("account-1")
		with patch.object(admin, "_open_shared", side_effect=AssertionError("List may not read picture bytes")), patch.object(admin, "_decode_picture", side_effect=AssertionError("List may not decode")):
			row = admin.list_accounts()["accounts"][0]
		self.assertRegex(row["picture_rev"], r"^[0-9a-f]{16}$")
		self.assertEqual(set(row), {"id", "label", "dir", "paused", "is_root", "signed_in", "balance", "balance_at", "last_run_at", "last_result", "email", "name", "picture_rev"})
		self.assertNotIn("data_url", row)
		self.assertEqual(row["picture_rev"], admin.get_account_picture("account-1")["rev"])
		self.assertTrue(path.exists())

	def test_disabled_or_default_avatar_does_not_open_picture(self):
		self.picture()
		for flags in ({"use_gaia_picture": False}, {"is_using_default_avatar": True}):
			with self.subTest(flags=flags):
				self.metadata(**flags)
				admin._profile_identity("account-1")
				with patch.object(admin, "_open_shared", side_effect=AssertionError("Disabled picture not read")), patch.object(admin, "_decode_picture") as decode:
					self.assertEqual(admin.get_account_picture("account-1"), {"account_id": "account-1", "rev": None, "data_url": None})
					self.assertIsNone(admin.list_accounts()["accounts"][0]["picture_rev"])
					decode.assert_not_called()

	def test_account_without_local_state_lists_three_times_without_warning(self):
		with self.assertNoLogs("rfapp", "WARNING"):
			for _ in range(3):
				self.assertIsNone(admin.list_accounts()["accounts"][0]["picture_rev"])
				self.assertIsNone(admin.get_account_picture("account-1")["data_url"])

	def test_profile_without_default_entry_lists_three_times_without_warning(self):
		root = self.profile()
		root.mkdir()
		(root / "Local State").write_text(json.dumps({"profile": {"info_cache": {"Profile 1": {"user_name": "sentinel-picture@example.invalid", "gaia_name": "Sentinel Picture Name"}}}}), encoding="utf-8")
		with self.assertNoLogs("rfapp", "WARNING"):
			for _ in range(3):
				self.assertIsNone(admin.list_accounts()["accounts"][0]["picture_rev"])
				self.assertIsNone(admin.get_account_picture("account-1")["data_url"])

	def test_profile_without_picture_lists_three_times_without_warning(self):
		self.metadata(use_gaia_picture=False, gaia_picture_file_name=None)
		with self.assertNoLogs("rfapp", "WARNING"):
			for _ in range(3):
				self.assertIsNone(admin.list_accounts()["accounts"][0]["picture_rev"])
				self.assertIsNone(admin.get_account_picture("account-1")["data_url"])

	def test_missing_enabled_picture_warns_once_across_repeated_list_and_get(self):
		self.metadata()
		with self.assertLogs("rfapp", "WARNING") as captured:
			for _ in range(3):
				self.assertIsNone(admin.list_accounts()["accounts"][0]["picture_rev"])
				self.assertIsNone(admin.get_account_picture("account-1")["data_url"])
		self.assertEqual(captured.output, ["WARNING:rfapp:account_picture_unreadable"])

	def test_broken_picture_lists_once_per_stamp_and_warns_again_on_new_stamp(self):
		path = self.metadata()
		path.write_bytes(b"")
		with self.assertLogs("rfapp", "WARNING") as captured:
			for _ in range(3):
				self.assertIsNone(admin.list_accounts()["accounts"][0]["picture_rev"])
			self.assertEqual(captured.output, ["WARNING:rfapp:account_picture_unreadable"])
			stat = path.stat()
			os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10000000))
			for _ in range(3):
				self.assertIsNone(admin.list_accounts()["accounts"][0]["picture_rev"])
		self.assertEqual(captured.output, ["WARNING:rfapp:account_picture_unreadable"] * 2)

	def test_malformed_existing_local_state_warns_once_per_stamp(self):
		root = self.profile()
		root.mkdir()
		path = root / "Local State"
		path.write_text("{", encoding="utf-8")
		with self.assertLogs("rfapp", "WARNING") as captured:
			for _ in range(3):
				admin.list_accounts()
				admin._identity_cache.clear()
			self.assertEqual(captured.output, ["WARNING:rfapp:account_picture_unreadable"])
			stat = path.stat()
			os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10000000))
			for _ in range(3):
				admin.list_accounts()
		self.assertEqual(captured.output, ["WARNING:rfapp:account_picture_unreadable"] * 2)

	def test_broken_decode_warns_once_per_picture_stamp(self):
		path = self.picture()
		path.write_bytes(path.read_bytes()[:40])
		with self.assertLogs("rfapp", "WARNING") as captured:
			for _ in range(3):
				self.assertIsNone(admin.get_account_picture("account-1")["data_url"])
			self.assertEqual(captured.output, ["WARNING:rfapp:account_picture_unreadable"])
			stat = path.stat()
			os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10000000))
			for _ in range(3):
				self.assertIsNone(admin.get_account_picture("account-1")["data_url"])
		self.assertEqual(captured.output, ["WARNING:rfapp:account_picture_unreadable"] * 2)

	def test_missing_and_locked_picture_return_null_and_generic_log(self):
		self.metadata()
		with self.assertLogs("rfapp", "WARNING") as captured:
			result = admin.get_account_picture("account-1")
		self.assertIsNone(result["data_url"])
		self.assertTrue(all(line.endswith("account_picture_unreadable") for line in captured.output))
		self.picture()
		admin._profile_identity("account-1")
		with patch.object(admin, "_open_shared", side_effect=PermissionError("never log sentinel-picture@example.invalid")), self.assertLogs("rfapp", "WARNING") as captured:
			self.assertIsNone(admin.get_account_picture("account-1")["data_url"])
		self.assertEqual(captured.output, ["WARNING:rfapp:account_picture_unreadable"])

	def test_plain_name_rejection_happens_before_picture_open(self):
		for name in ("../outside.png", "nested/image.png", "nested\\image.png", "foo..bar.png", "https://example.invalid/a.png", "C:picture.png", ".", "", None):
			with self.subTest(name=name):
				self.metadata(gaia_picture_file_name=name)
				admin._profile_identity("account-1")
				with patch.object(admin, "_open_shared", side_effect=AssertionError("Rejected name opened")) as opened, self.assertLogs("rfapp", "WARNING"):
					self.assertIsNone(admin.get_account_picture("account-1")["data_url"])
					opened.assert_not_called()

	def test_plain_name_guard_rejects_an_existing_file_with_dotdot(self):
		self.picture(name="photo..png")
		with self.assertLogs("rfapp", "WARNING"):
			self.assertIsNone(admin.get_account_picture("account-1")["data_url"])

	def test_oversize_file_rejected_before_open(self):
		path = self.metadata()
		with path.open("wb") as stream:
			stream.truncate(admin._PICTURE_LIMIT + 1)
		admin._profile_identity("account-1")
		with patch.object(admin, "_open_shared", side_effect=AssertionError("Oversized picture read")), self.assertLogs("rfapp", "WARNING"):
			self.assertIsNone(admin.get_account_picture("account-1")["data_url"])

	def test_picture_growth_after_stat_is_still_bounded_before_read(self):
		path = self.picture()
		real_open = admin._open_shared
		class GrowingReader:
			def __enter__(self):
				self.stream = real_open(path)
				return self
			def __exit__(self, *args):
				self.stream.close()
			def fileno(self):
				return self.stream.fileno()
			def read(self, count):
				self.limit = count
				return b"x" * count
		reader = GrowingReader()
		admin._profile_identity("account-1")
		with patch.object(admin, "_open_shared", return_value=reader), self.assertLogs("rfapp", "WARNING"):
			self.assertIsNone(admin.get_account_picture("account-1")["data_url"])
		self.assertEqual(reader.limit, admin._PICTURE_LIMIT + 1)

	def test_unknown_account_does_not_open_any_profile(self):
		with patch.object(admin, "_open_shared", side_effect=AssertionError("Unknown account read profile")):
			with self.assertRaises(ValueError) as failure:
				admin.get_account_picture("account-999")
		self.assertEqual(failure.exception.code, "accounts.unknown")

	def test_wrong_first_bytes_are_rejected_before_decoding(self):
		self.picture("BMP")
		with patch.object(admin, "_decode_picture", wraps=admin._decode_picture) as decode, self.assertLogs("rfapp", "WARNING"):
			self.assertIsNone(admin.get_account_picture("account-1")["data_url"])
		decode.assert_not_called()

	def test_pixel_limit_rejects_before_pillow_decodes_pixels(self):
		path = self.metadata()
		def chunk(kind, data):
			return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
		header = struct.pack(">IIBBBBB", 5000, 5000, 8, 2, 0, 0, 0)
		path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))
		with patch.object(Image.Image, "load", side_effect=AssertionError("Pixel limit must precede decode")) as load, self.assertLogs("rfapp", "WARNING"):
			self.assertIsNone(admin.get_account_picture("account-1")["data_url"])
		load.assert_not_called()

	def test_truncated_png_returns_null_without_cache_of_image(self):
		path = self.picture()
		path.write_bytes(path.read_bytes()[:40])
		with self.assertLogs("rfapp", "WARNING"):
			self.assertIsNone(admin.get_account_picture("account-1")["data_url"])

	def test_picture_cache_hit_has_no_second_read_or_decode(self):
		self.picture()
		with patch.object(admin, "_decode_picture", wraps=admin._decode_picture) as decode:
			first = admin.get_account_picture("account-1")
			with patch.object(admin, "_open_shared", side_effect=AssertionError("Warm cache reread")):
				second = admin.get_account_picture("account-1")
		self.assertEqual(first, second)
		decode.assert_called_once()
		first["data_url"] = "caller mutation"
		self.assertEqual(admin.get_account_picture("account-1"), second)

	def test_temporary_read_failure_does_not_poison_the_unchanged_picture_cache(self):
		self.picture()
		admin._profile_identity("account-1")
		with patch.object(admin, "_open_shared", side_effect=PermissionError("transient")), self.assertLogs("rfapp", "WARNING"):
			self.assertIsNone(admin.get_account_picture("account-1")["data_url"])
		self.decoded(admin.get_account_picture("account-1"))

	def test_edge_replacement_changes_revision_and_cached_image(self):
		path = self.picture()
		first = admin.get_account_picture("account-1")
		temporary = path.with_suffix(".new")
		Image.new("RGB", (160, 200), "orange").save(temporary, format="PNG")
		os.replace(temporary, path)
		row = admin.list_accounts()["accounts"][0]
		second = admin.get_account_picture("account-1")
		self.assertNotEqual(first["rev"], second["rev"])
		self.assertNotEqual(first["data_url"], second["data_url"])
		self.assertEqual(row["picture_rev"], second["rev"])

	def test_two_accounts_and_legacy_root_have_their_own_pictures(self):
		(self.home / ".env").write_text("QUERY_SOURCE=trends\nUSER_DATA_DIR='" + str(self.profiles) + "'\nREWARDS_ACCOUNTS=account-1,account-2\n", encoding="utf-8")
		self.picture(account="account-1", image=Image.new("RGB", (160, 160), "red"))
		self.picture(account="account-2", image=Image.new("RGB", (160, 160), "blue"))
		self.assertEqual(self.decoded(admin.get_account_picture("account-1")).getpixel((64, 64))[:3], (255, 0, 0))
		self.assertEqual(self.decoded(admin.get_account_picture("account-2")).getpixel((64, 64))[:3], (0, 0, 255))
		(self.home / ".env").write_text("QUERY_SOURCE=trends\nUSER_DATA_DIR='" + str(self.profiles) + "'\n", encoding="utf-8")
		self.picture(account="@root", image=Image.new("RGB", (160, 160), "green"))
		self.assertEqual(self.decoded(admin.get_account_picture("@root")).getpixel((64, 64))[:3], (0, 128, 0))
		self.assertTrue(admin.list_accounts()["accounts"][0]["is_root"])

	@unittest.skipUnless(os.name == "nt", "Windows picture read shares deletion")
	def test_replace_racing_open_picture_keeps_complete_old_then_new_image(self):
		path = self.picture()
		replacement = path.with_suffix(".replacement")
		Image.new("RGB", (220, 160), "red").save(replacement, format="PNG")
		from ctypes import wintypes
		replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
		replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
		replace.restype = wintypes.BOOL
		real_open = admin._open_shared
		def open_shared(name):
			stream = real_open(name)
			if name == path and replacement.exists():
				self.assertTrue(replace(str(path), str(replacement), None, 0, None, None), ctypes.get_last_error())
			return stream
		with patch.object(admin, "_open_shared", side_effect=open_shared):
			first = admin.get_account_picture("account-1")
		second = admin.get_account_picture("account-1")
		self.assertEqual(self.decoded(first).getpixel((64, 64))[:3], (30, 140, 220))
		self.assertEqual(self.decoded(second).getpixel((64, 64))[:3], (255, 0, 0))
		self.assertNotEqual(first["rev"], second["rev"])

	def test_diagnostics_and_app_log_contain_no_picture_or_identity(self):
		path = self.picture()
		source_bytes = path.read_bytes()
		prefs.save({"language": "en", "setup_done": True})
		(self.home / "runs").mkdir()
		(self.home / "index.json").write_text('{"locales":[{"code":"en"}]}', encoding="utf-8")
		log = logging.FileHandler(self.home / "app.log", encoding="utf-8")
		admin._logger.addHandler(log)
		try:
			result = admin.get_account_picture("account-1")
			admin.list_accounts()
			self.metadata(gaia_picture_file_name="photo..png")
			admin.get_account_picture("account-1")
		finally:
			admin._logger.removeHandler(log)
			log.close()
		text = (self.home / "app.log").read_text(encoding="utf-8")
		self.assertIn("account_picture_unreadable", text)
		for sentinel in ("sentinel-picture@example.invalid", "Sentinel Picture Name", str(self.home), result["data_url"]):
			self.assertNotIn(sentinel, text)
		with patch.object(diagnostics, "_run", return_value=SimpleNamespace(returncode=1, stdout=b"")), patch.object(diagnostics.platform, "platform", return_value="Synthetic Windows"), patch.object(diagnostics, "_edge_version", return_value=None), patch.object(diagnostics, "_identity", return_value=("SyntheticUser", "SyntheticPc")), patch.object(diagnostics.paths, "task_name", return_value="RewardsFarmerApp_test0000"), patch.object(diagnostics.paths, "resource", return_value=self.home / "index.json"):
			diagnostics.export(self.home / "diagnostics.zip")
		with zipfile.ZipFile(self.home / "diagnostics.zip") as archive:
			self.assertFalse(any("Local State" in name or "Picture" in name or "data-dir" in name for name in archive.namelist()))
			for name in archive.namelist():
				data = archive.read(name)
				for sentinel in (source_bytes, result["data_url"].encode(), b"sentinel-picture@example.invalid", b"Sentinel Picture Name"):
					self.assertNotIn(sentinel, data, name)


if __name__ == "__main__":
	unittest.main()
