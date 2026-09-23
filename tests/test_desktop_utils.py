"""Tests for Windows Virtual Desktop utilities and environment variable configuration.

	python -m unittest discover -s tests
"""

import os
import sys
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)

import desktop_utils


@unittest.skipUnless(sys.platform.startswith("win"), "Skipped because OS is not Windows")
class VirtualDesktopConfigTests(unittest.TestCase):
	def setUp(self):
		self.saved_env = {
			"USE_VIRTUAL_DESKTOP": os.environ.get("USE_VIRTUAL_DESKTOP"),
			"SWITCH_BACK_TO_MAIN_DESKTOP": os.environ.get("SWITCH_BACK_TO_MAIN_DESKTOP"),
			"SWITCH_BACK_DELAY_SECONDS": os.environ.get("SWITCH_BACK_DELAY_SECONDS"),
			"CLEANUP_VIRTUAL_DESKTOP": os.environ.get("CLEANUP_VIRTUAL_DESKTOP"),
		}
		desktop_utils.reset_virtual_desktop_state()

	def tearDown(self):
		for key, val in self.saved_env.items():
			if val is None:
				os.environ.pop(key, None)
			else:
				os.environ[key] = val
		desktop_utils.reset_virtual_desktop_state()

	def test_use_virtual_desktop_defaults_to_false(self):
		os.environ.pop("USE_VIRTUAL_DESKTOP", None)
		self.assertFalse(desktop_utils.is_virtual_desktop_enabled())

	def test_use_virtual_desktop_parses_truthy_values(self):
		for val in ("1", "true", "True", "yes", "YES", " true "):
			os.environ["USE_VIRTUAL_DESKTOP"] = val
			self.assertTrue(
				desktop_utils.is_virtual_desktop_enabled(),
				f"Expected '{val}' to be parsed as True",
			)

	def test_use_virtual_desktop_parses_falsy_values(self):
		for val in ("0", "false", "no", "disabled", ""):
			os.environ["USE_VIRTUAL_DESKTOP"] = val
			self.assertFalse(
				desktop_utils.is_virtual_desktop_enabled(),
				f"Expected '{val}' to be parsed as False",
			)

	def test_switch_back_defaults_to_true(self):
		os.environ.pop("SWITCH_BACK_TO_MAIN_DESKTOP", None)
		self.assertTrue(desktop_utils.should_switch_back_to_main_desktop())

	def test_switch_back_parses_falsy_values(self):
		for val in ("0", "false", "no", ""):
			os.environ["SWITCH_BACK_TO_MAIN_DESKTOP"] = val
			self.assertFalse(
				desktop_utils.should_switch_back_to_main_desktop(),
				f"Expected '{val}' to be parsed as False",
			)

	def test_switch_back_parses_truthy_values(self):
		for val in ("1", "true", "True", "yes"):
			os.environ["SWITCH_BACK_TO_MAIN_DESKTOP"] = val
			self.assertTrue(
				desktop_utils.should_switch_back_to_main_desktop(),
				f"Expected '{val}' to be parsed as True",
			)


	def test_switch_back_delay_defaults_to_one_point_five(self):
		os.environ.pop("SWITCH_BACK_DELAY_SECONDS", None)
		self.assertEqual(desktop_utils.get_switch_back_delay(), 1.5)

	def test_switch_back_delay_parses_float(self):
		os.environ["SWITCH_BACK_DELAY_SECONDS"] = "2.5"
		self.assertEqual(desktop_utils.get_switch_back_delay(), 2.5)

	def test_switch_back_delay_falls_back_on_invalid_string(self):
		os.environ["SWITCH_BACK_DELAY_SECONDS"] = "invalid"
		self.assertEqual(desktop_utils.get_switch_back_delay(), 1.5)

	def test_switch_back_delay_falls_back_on_negative(self):
		os.environ["SWITCH_BACK_DELAY_SECONDS"] = "-1.0"
		self.assertEqual(desktop_utils.get_switch_back_delay(), 1.5)

	def test_switch_back_delay_falls_back_on_non_finite(self):
		for val in ("nan", "NaN", "inf", "-inf", "Infinity"):
			os.environ["SWITCH_BACK_DELAY_SECONDS"] = val
			self.assertEqual(
				desktop_utils.get_switch_back_delay(),
				1.5,
				f"Expected {val} to fall back to 1.5",
			)

	def test_cleanup_virtual_desktop_defaults_to_true(self):
		os.environ.pop("CLEANUP_VIRTUAL_DESKTOP", None)
		self.assertTrue(desktop_utils.is_cleanup_virtual_desktop_enabled())

	def test_cleanup_virtual_desktop_parses_falsy_values(self):
		for val in ("0", "false", "no", "disabled", ""):
			os.environ["CLEANUP_VIRTUAL_DESKTOP"] = val
			self.assertFalse(desktop_utils.is_cleanup_virtual_desktop_enabled())

	def test_cleanup_virtual_desktop_parses_truthy_values(self):
		for val in ("1", "true", "True", "yes"):
			os.environ["CLEANUP_VIRTUAL_DESKTOP"] = val
			self.assertTrue(desktop_utils.is_cleanup_virtual_desktop_enabled())


class PlatformSafetyTests(unittest.TestCase):
	def setUp(self):
		desktop_utils.reset_virtual_desktop_state()

	def tearDown(self):
		desktop_utils.reset_virtual_desktop_state()

	@mock.patch("desktop_utils.is_windows", return_value=False)
	def test_non_windows_safely_noops_hotkeys(self, mock_is_win):
		# Calling hotkey functions on non-Windows must not raise or call ctypes
		with self.assertLogs(desktop_utils.logger, level="WARNING"):
			self.assertFalse(desktop_utils.create_virtual_desktop())
		self.assertFalse(desktop_utils.switch_to_left_desktop())
		self.assertFalse(desktop_utils.switch_to_right_desktop())
		with self.assertLogs(desktop_utils.logger, level="WARNING"):
			self.assertFalse(desktop_utils.press_hotkey(0x5B, 0x11, 0x44))

	@mock.patch("desktop_utils.is_windows", return_value=False)
	def test_non_windows_safely_noops_desktop_lifecycle(self, mock_is_win):
		with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true"}):
			self.assertTrue(desktop_utils.prepare_desktop_before_launch())
			desktop_utils.switch_back_after_launch()
			self.assertFalse(desktop_utils._desktop_created)

	@mock.patch("desktop_utils.time.sleep")
	@mock.patch("desktop_utils.is_windows", return_value=True)
	@mock.patch("desktop_utils.create_virtual_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_left_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_right_desktop", return_value=True)
	def test_desktop_lifecycle_tracks_creation(self, mock_right, mock_left, mock_create, mock_is_win, mock_sleep):
		with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true", "SWITCH_BACK_TO_MAIN_DESKTOP": "true"}):
			# First launch creates the virtual desktop
			self.assertTrue(desktop_utils.prepare_desktop_before_launch())
			mock_create.assert_called_once()
			mock_right.assert_not_called()
			self.assertTrue(desktop_utils._desktop_created)

			# Switch back after launch
			desktop_utils.switch_back_after_launch()
			self.assertEqual(mock_left.call_count, desktop_utils._hops_to_worker)

			# Subsequent launch switches right
			self.assertTrue(desktop_utils.prepare_desktop_before_launch())
			self.assertEqual(mock_right.call_count, desktop_utils._hops_to_worker)

	@mock.patch("desktop_utils.is_windows", return_value=True)
	@mock.patch("desktop_utils.create_virtual_desktop", return_value=False)
	def test_failed_desktop_creation_leaves_state_false(self, mock_create, mock_is_win):
		with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true"}):
			with self.assertLogs(desktop_utils.logger, level="ERROR"):
				result = desktop_utils.prepare_desktop_before_launch()
			self.assertFalse(result)
			self.assertFalse(desktop_utils._desktop_created)
			self.assertFalse(desktop_utils._on_worker_desktop)

	@mock.patch("main.browser.start_driver", return_value=None)
	@mock.patch("main.desktop_utils.switch_back_after_launch")
	@mock.patch("main.desktop_utils.prepare_desktop_before_launch", return_value=True)
	def test_switch_back_runs_even_if_driver_launch_fails(self, mock_prepare, mock_switch_back, mock_start_driver):
		import accounts
		import main
		account = accounts.Account(name="test", user_data_dir="tmp", profile_name="Default")
		result = main.run_account(account)
		self.assertFalse(result)
		mock_prepare.assert_called_once()
		mock_switch_back.assert_called_once()

	@mock.patch("main.browser.start_driver", side_effect=RuntimeError("driver boom"))
	@mock.patch("main.desktop_utils.switch_back_after_launch")
	@mock.patch("main.desktop_utils.prepare_desktop_before_launch", return_value=True)
	def test_switch_back_runs_if_driver_launch_raises(self, mock_prepare, mock_switch_back, mock_start_driver):
		import accounts
		import main
		account = accounts.Account(name="test", user_data_dir="tmp", profile_name="Default")
		with self.assertRaises(RuntimeError):
			main.run_account(account)
		mock_prepare.assert_called_once()
		mock_switch_back.assert_called_once()

	@mock.patch("main.browser.start_driver")
	@mock.patch("main.desktop_utils.switch_back_after_launch")
	@mock.patch("main.desktop_utils.prepare_desktop_before_launch", return_value=False)
	def test_run_account_aborts_if_prepare_desktop_fails(self, mock_prepare, mock_switch_back, mock_start_driver):
		import accounts
		import main
		account = accounts.Account(name="test", user_data_dir="tmp", profile_name="Default")
		result = main.run_account(account)
		self.assertFalse(result)
		mock_prepare.assert_called_once()
		mock_start_driver.assert_not_called()
		mock_switch_back.assert_called_once()

	@mock.patch("desktop_utils.time.sleep")
	@mock.patch("desktop_utils.is_windows", return_value=True)
	@mock.patch("desktop_utils.create_virtual_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_left_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_right_desktop", return_value=True)
	def test_multi_account_with_switch_back_enabled(self, mock_right, mock_left, mock_create, mock_is_win, mock_sleep):
		with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true", "SWITCH_BACK_TO_MAIN_DESKTOP": "true"}):
			# Account 1
			desktop_utils.prepare_desktop_before_launch()
			mock_create.assert_called_once()
			self.assertTrue(desktop_utils._on_worker_desktop)

			desktop_utils.switch_back_after_launch()
			self.assertEqual(mock_left.call_count, desktop_utils._hops_to_worker)
			self.assertFalse(desktop_utils._on_worker_desktop)

			# Account 2
			desktop_utils.prepare_desktop_before_launch()
			self.assertEqual(mock_right.call_count, desktop_utils._hops_to_worker)
			self.assertTrue(desktop_utils._on_worker_desktop)

			desktop_utils.switch_back_after_launch()
			self.assertEqual(mock_left.call_count, desktop_utils._hops_to_worker * 2)
			self.assertFalse(desktop_utils._on_worker_desktop)

	@mock.patch("desktop_utils.time.sleep")
	@mock.patch("desktop_utils.is_windows", return_value=True)
	@mock.patch("desktop_utils.create_virtual_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_left_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_right_desktop", return_value=True)
	def test_multi_account_with_switch_back_disabled(self, mock_right, mock_left, mock_create, mock_is_win, mock_sleep):
		with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true", "SWITCH_BACK_TO_MAIN_DESKTOP": "false"}):
			# Account 1
			desktop_utils.prepare_desktop_before_launch()
			mock_create.assert_called_once()
			self.assertTrue(desktop_utils._on_worker_desktop)

			desktop_utils.switch_back_after_launch()
			mock_left.assert_not_called()
			self.assertTrue(desktop_utils._on_worker_desktop)

			# Account 2 (should NOT switch right again because it remains on worker desktop)
			desktop_utils.prepare_desktop_before_launch()
			mock_right.assert_not_called()
			self.assertTrue(desktop_utils._on_worker_desktop)

			desktop_utils.switch_back_after_launch()
			mock_left.assert_not_called()
			self.assertTrue(desktop_utils._on_worker_desktop)

	@mock.patch("desktop_utils.time.sleep")
	@mock.patch("desktop_utils.is_windows", return_value=True)
	@mock.patch("desktop_utils.switch_to_left_desktop", return_value=False)
	def test_failed_switch_back_leaves_on_worker_desktop_true(self, mock_left, mock_is_win, mock_sleep):
		with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true", "SWITCH_BACK_TO_MAIN_DESKTOP": "true"}):
			desktop_utils._on_worker_desktop = True
			with self.assertLogs(desktop_utils.logger, level="ERROR") as cm:
				desktop_utils.switch_back_after_launch()
			self.assertTrue(desktop_utils._on_worker_desktop)
			self.assertTrue(any("Could not switch back to the original Windows desktop" in m for m in cm.output))

	def test_reset_virtual_desktop_state_resets_all_flags(self):
		desktop_utils._desktop_created = True
		desktop_utils._on_worker_desktop = True
		desktop_utils.reset_virtual_desktop_state()
		self.assertFalse(desktop_utils._desktop_created)
		self.assertFalse(desktop_utils._on_worker_desktop)

	@mock.patch("desktop_utils.is_windows", return_value=True)
	def test_press_hotkey_releases_keys_on_key_down_error(self, mock_is_win):
		calls = []

		def fake_keybd_event(key, scancode, flags, extra):
			calls.append((key, flags))
			# Raise error on second key (VK_CONTROL)
			if key == 0x11 and flags == 0:
				raise RuntimeError("Simulated keydown failure")

		mock_ctypes = mock.MagicMock()
		mock_ctypes.windll.user32.keybd_event.side_effect = fake_keybd_event

		with mock.patch.dict("sys.modules", {"ctypes": mock_ctypes}):
			with mock.patch("desktop_utils.time.sleep"):
				with self.assertLogs(desktop_utils.logger, level="ERROR"):
					result = desktop_utils.press_hotkey(0x5B, 0x11, 0x44)

		self.assertFalse(result)
		# First key (0x5B) was pressed down, so it must be released (KEYEVENTF_KEYUP = 2) in finally
		self.assertIn((0x5B, 0), calls)
		self.assertIn((0x5B, 2), calls)

	@mock.patch("desktop_utils.is_windows", return_value=True)
	def test_press_hotkey_logs_error_on_exception(self, mock_is_win):
		mock_ctypes = mock.MagicMock()
		mock_ctypes.windll.user32.keybd_event.side_effect = Exception("win32 error")

		with mock.patch.dict("sys.modules", {"ctypes": mock_ctypes}):
			with mock.patch("desktop_utils.time.sleep"):
				with self.assertLogs(desktop_utils.logger, level="ERROR"):
					result = desktop_utils.press_hotkey(0x5B, 0x11, 0x44)
		self.assertFalse(result)

	@mock.patch("desktop_utils.is_windows", return_value=True)
	def test_press_hotkey_handles_error_during_key_release(self, mock_is_win):
		calls = []

		def fake_keybd_event(key, scancode, flags, extra):
			calls.append((key, flags))
			# Raise error when releasing VK_CONTROL (flags == 2)
			if key == 0x11 and flags == 2:
				raise RuntimeError("Simulated keyup failure")

		mock_ctypes = mock.MagicMock()
		mock_ctypes.windll.user32.keybd_event.side_effect = fake_keybd_event

		with mock.patch.dict("sys.modules", {"ctypes": mock_ctypes}):
			with mock.patch("desktop_utils.time.sleep"):
				with self.assertLogs(desktop_utils.logger, level="ERROR") as cm:
					result = desktop_utils.press_hotkey(0x5B, 0x11, 0x44)

		self.assertFalse(result)
		# Ensure 0x5B is still attempted to be released despite 0x11 keyup failure
		self.assertIn((0x5B, 2), calls)
		self.assertTrue(any("Failed to release Windows key 17" in m for m in cm.output))

	@mock.patch("desktop_utils.is_windows", return_value=False)
	def test_get_desktop_state_returns_fallback_on_non_windows(self, mock_is_win):
		self.assertEqual(desktop_utils.get_desktop_state(), (0, 1))

	@mock.patch("desktop_utils.time.sleep")
	@mock.patch("desktop_utils.is_windows", return_value=True)
	@mock.patch("desktop_utils.create_virtual_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_left_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_right_desktop", return_value=True)
	def test_multi_hop_navigation_tracks_exact_starting_desktop(
		self, mock_right, mock_left, mock_create, mock_is_win, mock_sleep
	):
		# Simulate user on Desktop 1 (idx 0) out of 3 total desktops
		# When Desktop 4 is created, total becomes 4, so hops = 3 - 0 = 3
		state_sequence = [
			(0, 3),  # before creation
			(3, 4),  # after creation
		]
		with mock.patch("desktop_utils.get_desktop_state", side_effect=state_sequence):
			with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true", "SWITCH_BACK_TO_MAIN_DESKTOP": "true"}):
				self.assertTrue(desktop_utils.prepare_desktop_before_launch())
				self.assertEqual(desktop_utils._hops_to_worker, 3)

				# Switch back should execute switch_to_left_desktop 3 times
				desktop_utils.switch_back_after_launch()
				self.assertEqual(mock_left.call_count, 3)
				self.assertFalse(desktop_utils._on_worker_desktop)

				# Next account should execute switch_to_right_desktop 3 times
				self.assertTrue(desktop_utils.prepare_desktop_before_launch())
				self.assertEqual(mock_right.call_count, 3)
				self.assertTrue(desktop_utils._on_worker_desktop)

	@mock.patch("desktop_utils.time.sleep")
	@mock.patch("desktop_utils.is_windows", return_value=True)
	@mock.patch("desktop_utils.close_current_virtual_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_left_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_right_desktop", return_value=True)
	def test_cleanup_virtual_desktop_closes_and_compensates_left_shift(
		self, mock_right, mock_left, mock_close, mock_is_win, mock_sleep
	):
		# Given _hops_to_worker = 3, user on main desktop:
		# 1. Switch right 3 times to worker desktop
		# 2. Close worker desktop (Win+Ctrl+F4)
		# 3. Windows drops to (worker - 1). Remaining hops left: 3 - 1 = 2 hops.
		desktop_utils._desktop_created = True
		desktop_utils._on_worker_desktop = False
		desktop_utils._hops_to_worker = 3

		with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true", "CLEANUP_VIRTUAL_DESKTOP": "true"}):
			result = desktop_utils.cleanup_virtual_desktop()

		self.assertTrue(result)
		self.assertEqual(mock_right.call_count, 3)
		mock_close.assert_called_once()
		self.assertEqual(mock_left.call_count, 2)
		self.assertFalse(desktop_utils._desktop_created)
		self.assertFalse(desktop_utils._on_worker_desktop)

	@mock.patch("desktop_utils.close_current_virtual_desktop")
	def test_cleanup_virtual_desktop_noops_when_disabled(self, mock_close):
		desktop_utils._desktop_created = True
		with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true", "CLEANUP_VIRTUAL_DESKTOP": "false"}):
			self.assertTrue(desktop_utils.cleanup_virtual_desktop())
		mock_close.assert_not_called()
		self.assertTrue(desktop_utils._desktop_created)

	@mock.patch("desktop_utils.time.sleep")
	@mock.patch("desktop_utils.is_windows", return_value=True)
	@mock.patch("desktop_utils.switch_to_left_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_right_desktop", return_value=True)
	def test_guid_navigation_reordered_desktops_moves_correct_direction(
		self, mock_right, mock_left, mock_is_win, mock_sleep
	):
		id_a = b"\x01" * 16
		id_b = b"\x02" * 16
		id_c = b"\x03" * 16

		# Target is id_a, current is id_c in [id_a, id_b, id_c] -> delta = -2 (left 2)
		with mock.patch("desktop_utils.get_desktop_registry_data", return_value=([id_a, id_b, id_c], id_c)):
			self.assertTrue(desktop_utils._navigate_to_guid(id_a))
			self.assertEqual(mock_left.call_count, 2)
			mock_right.assert_not_called()

		mock_left.reset_mock()
		mock_right.reset_mock()

		# Target is id_c, current is id_a in [id_a, id_b, id_c] -> delta = +2 (right 2)
		with mock.patch("desktop_utils.get_desktop_registry_data", return_value=([id_a, id_b, id_c], id_a)):
			self.assertTrue(desktop_utils._navigate_to_guid(id_c))
			self.assertEqual(mock_right.call_count, 2)
			mock_left.assert_not_called()

		mock_left.reset_mock()
		mock_right.reset_mock()

		# User reordered desktops such that worker (id_c) is now to the LEFT of main desktop (id_a):
		# Desktops: [id_c, id_b, id_a], current is id_a -> to reach id_c, delta = -2 (left 2)
		with mock.patch("desktop_utils.get_desktop_registry_data", return_value=([id_c, id_b, id_a], id_a)):
			self.assertTrue(desktop_utils._navigate_to_guid(id_c))
			self.assertEqual(mock_left.call_count, 2)
			mock_right.assert_not_called()

	@mock.patch("desktop_utils.time.sleep")
	@mock.patch("desktop_utils.is_windows", return_value=True)
	@mock.patch("desktop_utils.close_current_virtual_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_left_desktop", return_value=True)
	@mock.patch("desktop_utils.switch_to_right_desktop", return_value=True)
	def test_cleanup_with_guid_tracking_navigates_to_start_desktop(
		self, mock_right, mock_left, mock_close, mock_is_win, mock_sleep
	):
		id_main = b"\x11" * 16
		id_worker = b"\x22" * 16
		desktop_utils._desktop_created = True
		desktop_utils._on_worker_desktop = False
		desktop_utils._start_desktop_id = id_main
		desktop_utils._worker_desktop_id = id_worker

		reg_states = [
			([id_main, id_worker], id_main),  # initial check in cleanup
			([id_main, id_worker], id_main),  # switch_to_worker_desktop -> _navigate_to_guid(id_worker)
			([id_main], id_main),             # return to start desktop -> _navigate_to_guid(id_main)
		]
		with mock.patch("desktop_utils.get_desktop_registry_data", side_effect=reg_states):
			with mock.patch.dict(os.environ, {"USE_VIRTUAL_DESKTOP": "true", "CLEANUP_VIRTUAL_DESKTOP": "true"}):
				self.assertTrue(desktop_utils.cleanup_virtual_desktop())

		self.assertEqual(mock_right.call_count, 1)
		mock_close.assert_called_once()
		self.assertFalse(desktop_utils._desktop_created)
		self.assertFalse(desktop_utils._on_worker_desktop)


if __name__ == "__main__":
	unittest.main()


