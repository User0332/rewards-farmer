import _bootstrap  # noqa: F401

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from selenium.common.exceptions import WebDriverException
from file_replace import replace_file
from rfapp import signin


class FakeOptions:
	def __init__(self):
		self.args = []

	def add_argument(self, argument):
		self.args.append(argument)


class FakeDriver:
	def __init__(self, url="https://rewards.bing.com/", closed=False):
		self.url = url
		self.closed = closed
		self.quit_count = 0
		self.visits = []
		self.limit = None

	@property
	def current_url(self):
		if self.closed:
			raise WebDriverException("window closed")
		return self.url

	def get(self, url):
		self.visits.append(url)

	def set_page_load_timeout(self, value):
		self.limit = value

	def quit(self):
		self.quit_count += 1


class SigninFlowTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.root = Path(self.temp.name)
		self.profile = self.root / "profiles"
		self.profile.mkdir()
		self.env = patch.dict(os.environ, {"REWARDS_FARMER_DATA_HOME": str(self.root), "USER_DATA_DIR": str(self.profile), "REWARDS_HEADLESS": "1", "USE_VIRTUAL_DESKTOP": "1"})
		self.env.start()
		self.addCleanup(self.env.stop)
		(self.root / ".env").write_text("QUERY_SOURCE=trends\nUSER_DATA_DIR=" + str(self.profile) + "\n")
		self.browser = ModuleType("browser")
		self.browser.build_options = Mock(side_effect=lambda account: FakeOptions())
		self.browser.build_service = Mock(return_value="fake-service")
		self.browser.start_failure_code = Mock(return_value="driver_missing")
		self.elements = ModuleType("element_selectors")
		self.elements.ElementSelectionUtils = Mock(side_effect=lambda driver: driver)
		self.rewards = ModuleType("rewards_state")
		self.rewards.read_balance = Mock(return_value=10)
		self.rewards.read_state = Mock(return_value=SimpleNamespace(unreadable="signed_out"))
		self.lock = SimpleNamespace(release=Mock())
		self.locks = ModuleType("run_lock")
		self.locks.acquire = Mock(return_value=self.lock)
		self.admin = ModuleType("rfapp.accounts_admin")
		self.admin.migrate_root_profile = Mock(return_value="account-1")
		self.admin._recycle = Mock(return_value=True)
		self.schedule = ModuleType("rfapp.schedule")
		self.schedule.ensure = Mock(return_value={"in_sync": True})
		modules = {"browser": self.browser, "element_selectors": self.elements, "rewards_state": self.rewards, "run_lock": self.locks, "rfapp.accounts_admin": self.admin, "rfapp.schedule": self.schedule}
		self.modules = patch.dict("sys.modules", modules)
		self.modules.start()
		self.addCleanup(self.modules.stop)
		import rfapp
		for name in ("accounts_admin", "schedule"):
			item = patch.object(rfapp, name, modules["rfapp." + name], create=True)
			item.start()
			self.addCleanup(item.stop)
		self.clock = 0.0
		self.sleeps = []
		def sleep(seconds):
			self.sleeps.append(seconds)
			self.clock += seconds
		for target, effect in [("rfapp.signin.time.monotonic", lambda: self.clock), ("rfapp.signin.time.sleep", sleep)]:
			item = patch(target, side_effect=effect)
			item.start()
			self.addCleanup(item.stop)
		self.visible = FakeDriver()
		self.verification = FakeDriver()
		self.edge = patch("selenium.webdriver.Edge", side_effect=[self.visible, self.verification])
		self.edge_mock = self.edge.start()
		self.addCleanup(self.edge.stop)

	def state(self, account="account-1"):
		return json.loads((self.root / "runs" / f"signin-{account.replace('@', '')}.json").read_text())

	def test_success_commits_new_account_after_graceful_quit(self):
		self.assertEqual(signin.run("account-1", True), 0)
		status = self.state()
		self.assertEqual(status["state"], "signed_in")
		self.assertEqual(status["pid"], os.getpid())
		self.assertTrue(status["at"].endswith("Z"))
		self.assertEqual(self.visible.quit_count, 1)
		self.assertEqual(self.visible.limit, 60)
		self.assertEqual(self.visible.visits, ["https://rewards.bing.com/"])
		self.assertEqual(self.sleeps, [3])
		self.assertEqual(os.environ["REWARDS_HEADLESS"], "0")
		self.assertEqual(os.environ["USE_VIRTUAL_DESKTOP"], "0")
		self.assertIn("REWARDS_ACCOUNTS=account-1", (self.root / ".env").read_text())
		self.schedule.ensure.assert_called_once()
		self.admin._recycle.assert_not_called()
		self.lock.release.assert_called_once()
		self.rewards.read_state.assert_not_called()

	def test_status_uses_shared_helper_after_one_permission_failure(self):
		real_replace = os.replace
		attempts = []
		def replace(source, target):
			attempts.append((source, target))
			if len(attempts) == 1:
				raise PermissionError("status reader open")
			real_replace(source, target)
		with patch("rfapp.signin.replace_file", wraps=replace_file) as helper, patch("file_replace.os.replace", side_effect=replace):
			written = signin._write_status("account-1", "waiting")
		helper.assert_called_once()
		self.assertEqual(len(attempts), 2)
		self.assertEqual(self.state(), written)
		self.assertEqual(self.state()["message_key"], "accounts.signin.waiting")
		self.assertEqual([path.name for path in (self.root / "runs").iterdir()], ["signin-account-1.json"])

	def test_zero_balance_is_success(self):
		self.rewards.read_balance.return_value = 0
		self.assertEqual(signin.run("account-1", False), 0)

	def test_cancel_never_verifies_or_deletes_existing_profile(self):
		(signin._cancel_path("account-1")).touch()
		self.assertEqual(signin.run("account-1", False), 1)
		self.assertEqual(self.state()["state"], "cancelled")
		self.assertEqual(self.visible.quit_count, 1)
		self.rewards.read_state.assert_not_called()
		self.admin._recycle.assert_not_called()
		self.assertFalse(signin._cancel_path("account-1").exists())
		self.lock.release.assert_called_once()

	def test_cancel_new_recycles_new_profile_only(self):
		profile = self.profile / "account-1"
		profile.mkdir()
		signin._cancel_path("account-1").touch()
		self.assertEqual(signin.run("account-1", True), 1)
		self.admin._recycle.assert_called_once_with([profile])
		self.assertNotIn("REWARDS_ACCOUNTS", (self.root / ".env").read_text())

	def test_failed_recycle_retains_diagnostic_path_and_releases_lock(self):
		profile = self.profile / "account-1"
		profile.mkdir()
		signin._cancel_path("account-1").touch()
		self.admin._recycle.return_value = False
		with self.assertLogs("rfapp", "WARNING"):
			self.assertEqual(signin.run("account-1", True), 1)
		self.assertIn(str(profile), self.state()["detail"])
		self.assertTrue(profile.exists())
		self.lock.release.assert_called_once()

	def test_window_closed_before_success_verifies_signed_out(self):
		self.visible.closed = True
		self.assertEqual(signin.run("account-1", False), 1)
		self.assertEqual(self.state()["state"], "cancelled")
		self.assertEqual(self.visible.quit_count, 1)
		self.assertEqual(self.verification.quit_count, 1)
		self.assertEqual(self.verification.limit, 60)
		self.assertEqual(self.edge_mock.call_args_list[1].kwargs["options"].args, ["--headless=new"])
		self.lock.release.assert_called_once()

	def test_closed_after_unobserved_success_preserves_session(self):
		self.visible.closed = True
		self.rewards.read_state.return_value = SimpleNamespace(unreadable=None)
		self.assertEqual(signin.run("account-1", True), 0)
		self.assertEqual(self.state()["state"], "signed_in")
		self.assertIn("REWARDS_ACCOUNTS=account-1", (self.root / ".env").read_text())
		self.admin._recycle.assert_not_called()

	def test_terms_of_use_retains_account_with_error_code(self):
		self.visible.closed = True
		self.rewards.read_state.return_value = SimpleNamespace(unreadable="terms_of_use")
		self.assertEqual(signin.run("account-1", True), 0)
		self.assertEqual(self.state()["error_code"], "terms_of_use")
		self.assertEqual(self.state()["message_key"], "errors.state.terms_of_use")

	def test_timeout_verifies_then_recycles_new_signed_out_profile(self):
		self.rewards.read_balance.return_value = None
		profile = self.profile / "account-1"
		profile.mkdir()
		self.assertEqual(signin.run("account-1", True), 1)
		self.assertEqual(self.state()["state"], "timed_out")
		self.assertEqual(self.clock, 900)
		self.assertEqual(self.verification.quit_count, 1)
		self.admin._recycle.assert_called_once_with([profile])

	def test_timeout_good_session_is_kept(self):
		self.rewards.read_balance.return_value = None
		self.rewards.read_state.return_value = SimpleNamespace(unreadable="page_blank")
		self.assertEqual(signin.run("account-1", True), 0)
		self.assertEqual(self.state()["state"], "signed_in")
		self.admin._recycle.assert_not_called()

	def test_other_hosts_never_read_balance(self):
		self.visible.url = "https://login.live.com/"
		self.assertEqual(signin.run("account-1", False), 1)
		self.rewards.read_balance.assert_not_called()
		self.rewards.read_state.assert_called_once()

	def test_visible_start_failure_and_release(self):
		self.edge_mock.side_effect = WebDriverException("driver missing")
		(self.profile / "account-1").mkdir()
		self.assertEqual(signin.run("account-1", True), 1)
		self.assertEqual(self.state()["state"], "failed")
		self.assertEqual(self.state()["error_code"], "driver_missing")
		self.assertEqual(self.state()["message_key"], "errors.browser.driver_missing")
		self.admin._recycle.assert_called_once()
		self.lock.release.assert_called_once()

	def test_verification_start_failure_quits_visible_and_releases(self):
		self.visible.closed = True
		self.edge_mock.side_effect = [self.visible, WebDriverException("driver missing")]
		self.assertEqual(signin.run("account-1", False), 1)
		self.assertEqual(self.state()["error_code"], "driver_missing")
		self.assertEqual(self.visible.quit_count, 1)
		self.lock.release.assert_called_once()

	def test_env_save_failure_never_recycles_a_good_signin(self):
		profile = self.profile / "account-1"
		profile.mkdir()
		with patch("rfapp.signin.EnvFile.save", side_effect=OSError("read only")), self.assertLogs("rfapp", "ERROR"):
			self.assertEqual(signin.run("account-1", True), 1)
		self.assertEqual(self.state()["state"], "failed")
		self.admin._recycle.assert_not_called()
		self.assertTrue(profile.exists())
		self.lock.release.assert_called_once()

	def test_busy_never_starts_browser(self):
		self.locks.acquire.return_value = None
		self.assertEqual(signin.run("account-1", True), 3)
		self.assertEqual(self.state()["state"], "busy")
		self.edge_mock.assert_not_called()
		self.lock.release.assert_not_called()

	def test_new_root_migration_precedes_edge(self):
		(self.profile / "Local State").touch()
		self.assertEqual(signin.run("account-2", True), 0)
		self.admin.migrate_root_profile.assert_called_once_with("account-2")
		self.locks.acquire.assert_called_once_with("signin", account="account-2", directory=str(self.profile))

	def test_migration_failure_maps_profile_in_use_and_releases(self):
		(self.profile / "Local State").touch()
		error = ValueError("locked")
		error.code = "profile_in_use"
		self.admin.migrate_root_profile.side_effect = error
		with self.assertLogs("rfapp", "ERROR"):
			self.assertEqual(signin.run("account-2", True), 1)
		self.assertEqual(self.state("account-2")["error_code"], "profile_in_use")
		self.edge_mock.assert_not_called()
		self.lock.release.assert_called_once()

	def test_invalid_id_cannot_write_outside_runs(self):
		for account_id in ("../outside", "@root"):
			with self.subTest(account_id=account_id), self.assertRaises(ValueError):
				signin.run(account_id, True)
		self.edge_mock.assert_not_called()
		self.locks.acquire.assert_not_called()


if __name__ == "__main__":
	unittest.main()
