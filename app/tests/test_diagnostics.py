import _bootstrap  # noqa: F401

import builtins
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from rfapp import diagnostics


class DiagnosticsTests(unittest.TestCase):
	def setUp(self):
		self.real_subprocess_run = subprocess.run
		guard = patch.object(diagnostics, "_run", side_effect=AssertionError("Unpatched process in diagnostics test"))
		guard.start()
		self.addCleanup(guard.stop)

	def test_redact_all_duplicate_multiline_and_export_secrets(self):
		text = "# keep\r\nOPENROUTER_API_KEY=one\r\nexport OPENROUTER_API_KEY='two'\r\nLOCAL_LLM_API_KEY='multi\nline'\r\nREWARDS_WEBHOOK_URL=https://discord.com/secret\r\nQUERY_SOURCE=trends\r\n"
		result = diagnostics._redact(text)
		self.assertEqual(result.count("<redacted>"), 4)
		self.assertIn("# keep\r\n", result)
		self.assertIn("QUERY_SOURCE=trends\r\n", result)
		for secret in ("=one", "'two'", "multi", "line'", "/secret"):
			self.assertNotIn(secret, result)

	def test_decodes_utf16_and_utf8(self):
		self.assertEqual(diagnostics._decode_output("<Task/>".encode("utf-16")), "<Task/>")
		self.assertEqual(diagnostics._decode_output("<Task/>".encode("utf-16-le")), "<Task/>")
		self.assertEqual(diagnostics._decode_output("ä".encode()), "ä")

	def test_malformed_secret_assignment_fails_closed(self):
		with self.assertRaises(diagnostics.DiagnosticsError):
			diagnostics._redact("OPENROUTER_API_KEY='unterminated-secret\nQUERY_SOURCE=trends\n")

	def test_export_allowlist_and_retention(self):
		with tempfile.TemporaryDirectory() as tmp:
			home = Path(tmp)
			runs = home / "runs"
			runs.mkdir()
			for index in range(12):
				(runs / f"20261014T180000Z-{index:04x}.jsonl").write_text('{"v":1}\n', encoding="utf-8")
				(runs / f"20261014T180000Z-{index:04x}.log").write_text("synthetic log", encoding="utf-8")
			(runs / "signin-account-1.json").write_text("private", encoding="utf-8")
			(home / "data-dir").mkdir()
			(home / "data-dir/Cookies").write_text("private", encoding="utf-8")
			profile = home / "data-dir" / "account-1"
			profile.mkdir()
			profile_email, profile_name = "identity@example.invalid", "Synthetic Profile Identity"
			(profile / "Local State").write_text(json.dumps({"profile": {"info_cache": {"Default": {"user_name": profile_email, "gaia_name": profile_name}}}}), encoding="utf-8")
			(home / "app.log").write_text("app", encoding="utf-8")
			(home / "app.log.1").write_text("old", encoding="utf-8")
			(home / "app-prefs.json").write_text('{"v":1}', encoding="utf-8")
			(home / ".env").write_text("OPENROUTER_API_KEY=secret\nLOCAL_LLM_API_KEY=local\nREWARDS_WEBHOOK_URL=https://discord.com/token\nQUERY_SOURCE=trends\n", encoding="utf-8")
			(home / "index.json").write_text('{"locales":[{"code":"en"}]}', encoding="utf-8")
			paths = SimpleNamespace(resource=lambda *parts: home / "index.json", runs_dir=lambda: runs, data_home=lambda: home, env_path=lambda: home / ".env", prefs_path=lambda: home / "app-prefs.json", task_name=lambda: "RewardsFarmerApp_test0000", is_frozen=lambda: False)
			modules = {"rfapp.prefs": SimpleNamespace(load=lambda: {"language": "system"}), "rfapp.i18n": SimpleNamespace(resolve=lambda *args: ("en", "ltr", "en-US"))}
			response = SimpleNamespace(returncode=0, stdout="<Task>synthetic</Task>".encode("utf-16"))
			with patch.object(diagnostics, "paths", paths), patch.dict(sys.modules, modules), patch.object(diagnostics, "_run", return_value=response) as query, patch.object(diagnostics, "_edge_version", return_value="155.synthetic"):
				diagnostics.export(home / "diagnostics.zip")
			self.assertIs(subprocess.run, self.real_subprocess_run)
			query.assert_called_once()
			self.assertEqual(query.call_args.args[0], ["schtasks", "/Query", "/TN", "RewardsFarmerApp_test0000", "/XML"])
			with zipfile.ZipFile(home / "diagnostics.zip") as archive:
				names = archive.namelist()
				self.assertEqual(sum(name.endswith(".jsonl") for name in names), 10)
				self.assertEqual(sum(name.startswith("runs/") and name.endswith(".log") for name in names), 10)
				self.assertFalse(any("Cookies" in name or "data-dir" in name or "signin" in name for name in names))
				self.assertFalse(any("Local State" in name for name in names))
				for name in names:
					self.assertNotIn(profile_email.encode(), archive.read(name), name)
					self.assertNotIn(profile_name.encode(), archive.read(name), name)
				self.assertNotIn("20261014T180000Z-0000.jsonl", " ".join(names))
				text = archive.read(".env").decode()
				self.assertEqual(text.count("<redacted>"), 3)
				self.assertNotIn("secret", text)
				self.assertNotIn("/token", text)
				self.assertEqual(archive.read("schedule.xml").decode(), "<Task>synthetic</Task>")
				self.assertEqual(json.loads(archive.read("system.json"))["format_locale"], "en-US")

	def test_export_failure_has_binding_code(self):
		with patch.dict(sys.modules, {"rfapp.prefs": SimpleNamespace(load=Mock(side_effect=OSError("denied"))), "rfapp.i18n": SimpleNamespace()}):
			with self.assertRaises(diagnostics.DiagnosticsError) as failure:
				diagnostics.export("not-written.zip")
			self.assertEqual(failure.exception.code, "diagnostics.failed")
		# This harmless command proves the default seam refuses an unpatched call.
		with self.assertRaisesRegex(AssertionError, "Unpatched process in diagnostics test"):
			diagnostics._run([sys.executable, "-c", "pass"], capture_output=True, text=True, timeout=10)

	def test_export_retries_env_and_preferences_reads_without_changing_redaction(self):
		with tempfile.TemporaryDirectory() as tmp:
			home = Path(tmp)
			runs = home / "runs"
			runs.mkdir()
			env, preferences = home / ".env", home / "app-prefs.json"
			env.write_bytes(b"# keep\r\nOPENROUTER_API_KEY=synthetic-secret\r\nQUERY_SOURCE=trends\r\n")
			preferences.write_text('{"v":1,"language":"de","setup_done":false}', encoding="utf-8")
			(home / "index.json").write_text('{"locales":[{"code":"de"}]}', encoding="utf-8")
			paths = SimpleNamespace(resource=lambda *parts: home / "index.json", runs_dir=lambda: runs, data_home=lambda: home, env_path=lambda: env, prefs_path=lambda: preferences, task_name=lambda: "RewardsFarmerApp_test0000", is_frozen=lambda: False)
			modules = {"rfapp.prefs": SimpleNamespace(load=lambda: {"language": "de"}), "rfapp.i18n": SimpleNamespace(resolve=lambda *args: ("de", "ltr", "de-DE"))}
			attempts = {env: 0, preferences: 0}
			real_open = builtins.open
			def open_file(path, *args, **kwargs):
				if not isinstance(path, int) and Path(path) in attempts:
					attempts[Path(path)] += 1
					if attempts[Path(path)] == 1:
						raise PermissionError("delete-pending")
				return real_open(path, *args, **kwargs)
			response = SimpleNamespace(returncode=1, stdout=b"")
			with patch.object(diagnostics, "paths", paths), patch.dict(sys.modules, modules), patch.object(diagnostics, "_run", return_value=response), patch.object(diagnostics, "_edge_version", return_value=None), patch.object(diagnostics, "_identity", return_value=("SyntheticUser", "SyntheticPc")), patch("file_replace.builtins.open", side_effect=open_file):
				diagnostics.export(home / "diagnostics.zip")
			self.assertEqual(attempts, {env: 2, preferences: 2})
			with zipfile.ZipFile(home / "diagnostics.zip") as archive:
				self.assertEqual(archive.read(".env"), b"# keep\r\nOPENROUTER_API_KEY='<redacted>'\r\nQUERY_SOURCE=trends\r\n")
				self.assertEqual(json.loads(archive.read("app-prefs.json")), {"v": 1, "language": "de", "setup_done": False})
				self.assertEqual(archive.read("schedule.xml"), b"")
				for name in archive.namelist():
					self.assertNotIn(b"synthetic-secret", archive.read(name))

	def test_every_zip_member_uses_consistent_personal_data_placeholders(self):
		with tempfile.TemporaryDirectory() as tmp:
			home = Path(tmp)
			runs = home / "runs"
			runs.mkdir()
			user, computer = "SyntheticWindowsUser", "SYNTHETIC-PC-42"
			accounts = ["jordan-private", "account-2", "morgan-private", "paused-private"]
			labels = {"jordan-private": "Jordan Personal Label", "account-2": "Work Personal Label", "morgan-private": "Morgan Personal Label", "paused-private": "Paused Personal Label"}
			hosts = ("llm.synthetic-private.invalid", "router.synthetic-private.invalid")
			secrets = ("synthetic-openrouter-secret-42", "synthetic-local-secret-42", "https://hooks.invalid/synthetic-webhook-secret-42")
			planted = [user, computer, *[account for account in accounts if not account.startswith("account-")], *labels.values(), *hosts, *secrets]
			message = " ".join(planted)
			env = f"REWARDS_ACCOUNTS={','.join(accounts[:3])}\nREWARDS_PAUSED_ACCOUNTS={accounts[3]}\nOPENROUTER_API_KEY={secrets[0]}\nLOCAL_LLM_API_KEY={secrets[1]}\nREWARDS_WEBHOOK_URL={secrets[2]}\nLOCAL_LLM_BASE_URL=https://{hosts[0]}:11434/v1\nOPENROUTER_BASE_URL=https://{hosts[1]}/v1\n# {message}\n"
			(home / ".env").write_text(env, encoding="utf-8")
			preferences = {"language": "system", "account_labels": labels, "goals": {accounts[0]: 5000}, "note": message}
			(home / "app-prefs.json").write_text(json.dumps(preferences), encoding="utf-8")
			(home / "app.log").write_text(message, encoding="utf-8")
			(home / "app.log.1").write_text(message, encoding="utf-8")
			run_id = "20261015T100000Z-aaaa"
			events = [{"type": "run_start", "accounts": accounts, "detail": message}, {"type": "account_start", "account": accounts[0], "profile": f"C:\\Users\\{user}\\data-dir\\{accounts[0]}"}]
			(runs / (run_id + ".jsonl")).write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
			(runs / (run_id + ".log")).write_text(message, encoding="utf-8")
			(home / "index.json").write_text('{"locales":[{"code":"en"}]}', encoding="utf-8")
			paths = SimpleNamespace(resource=lambda *parts: home / "index.json", runs_dir=lambda: runs, data_home=lambda: home, env_path=lambda: home / ".env", prefs_path=lambda: home / "app-prefs.json", task_name=lambda: "RewardsFarmerApp_test0000", is_frozen=lambda: False)
			modules = {"rfapp.prefs": SimpleNamespace(load=lambda: preferences), "rfapp.i18n": SimpleNamespace(resolve=lambda *args: ("en", "ltr", "en-US"))}
			response = SimpleNamespace(returncode=0, stdout=("<Task>" + message + "</Task>").encode("utf-16"))
			with patch.object(diagnostics, "paths", paths), patch.dict(sys.modules, modules), patch.object(diagnostics, "_run", return_value=response), patch.object(diagnostics, "_edge_version", return_value="155.synthetic"), patch.object(diagnostics, "_identity", return_value=(user, computer)), patch.object(diagnostics.platform, "platform", return_value=message):
				diagnostics.export(home / "diagnostics.zip")
			with zipfile.ZipFile(home / "diagnostics.zip") as archive:
				for name in archive.namelist():
					text = archive.read(name).decode("utf-8")
					for value in planted:
						self.assertNotIn(value.casefold(), text.casefold(), name)
					for placeholder in ("<user>", "<pc>", "<account-1>", "<account-2>", "<account-3>", "<host>", "<redacted>"):
						self.assertIn(placeholder, text, name)
				p = json.loads(archive.read("app-prefs.json"))
				self.assertEqual(p["account_labels"], {"<account-1>": "<account-1>", "account-2": "account-2", "<account-2>": "<account-2>", "<account-3>": "<account-3>"})

	def test_standard_llm_hosts_and_app_ids_are_preserved(self):
		for host in ("localhost", "127.0.0.1", "[::1]", "openrouter.ai"):
			with self.subTest(host=host), patch.object(diagnostics, "_identity", return_value=("SyntheticUser42", "SyntheticPc42")):
				text = "LOCAL_LLM_BASE_URL=http://" + host + "/v1\nREWARDS_ACCOUNTS=@root,account-4\n"
				clean = diagnostics._sanitizer(text, {"account_labels": {"account-4": "Public Label"}}, [])
				self.assertEqual(clean(text), text)
				self.assertEqual(clean("Public Label"), "account-4")
