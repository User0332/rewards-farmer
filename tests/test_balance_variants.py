"""Synthetic profile variants and bounded WebDriver latency, never a browser."""

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.remote.client_config import ClientConfig
from selenium.webdriver.remote.remote_connection import RemoteConnection
from selenium.webdriver.remote.webdriver import WebDriver

import element_selectors
import rewards_state
import rewards_tasks
from test_task_balances import Clock, make_tasks
from test_rewards_state import StateDriver
from fakes import safe_balance_packet


class SnapshotDriver:
	def __init__(self, buttons=(), *, header_texts=(), size=(1131, 643)):
		self.packet = safe_balance_packet(buttons, header_texts, size=size)
		self.scripts = []

	def execute_script(self, script, *args):
		assert script == element_selectors._BALANCE_SNAPSHOT
		assert ".click(" not in script and "cookie" not in script and "fetch(" not in script
		self.scripts.append(script)
		return self.packet


def profile(text, *, content=None, displayed=True, parts=()):
	return {"text": text, "content": text if content is None else content, "displayed": displayed, "parts": list(parts)}


class BalanceVariantTests(unittest.TestCase):
	def read(self, *buttons, **kwargs):
		driver = SnapshotDriver(buttons, **kwargs)
		elements = element_selectors.ElementSelectionUtils(driver)
		result = rewards_state.read_balance(driver, elements)
		self.assertEqual(len(driver.scripts), 1)
		return result, elements.balance_observation

	def test_bare_number(self):
		self.assertEqual(self.read(profile("12345"))[0], 12345)

	def test_comma_grouping(self):
		self.assertEqual(self.read(profile("12,345"))[0], 12345)

	def test_dot_grouping(self):
		self.assertEqual(self.read(profile("12.345"))[0], 12345)

	def test_space_grouping(self):
		for separator in (" ", "\u00a0", "\u202f"):
			with self.subTest(separator=repr(separator)):
				self.assertEqual(self.read(profile("12" + separator + "345"))[0], 12345)

	def test_points_suffix(self):
		self.assertEqual(self.read(profile("12,345 points"))[0], 12345)

	def test_available_points_label(self):
		self.assertEqual(self.read(profile("Available points: 12,345"))[0], 12345)

	def test_points_balance_label(self):
		self.assertEqual(self.read(profile("Points balance: 12,345"))[0], 12345)

	def test_name_and_level_badge_on_separate_lines(self):
		self.assertEqual(self.read(profile("Synthetic Name\nLevel 2\n12,345\nView profile"))[0], 12345)

	def test_inline_badge_uses_one_unambiguous_numeric_child(self):
		self.assertEqual(self.read(profile("Level 2 12,345 Synthetic Name", parts=("Level 2", "12,345", "Synthetic Name")))[0], 12345)

	def test_numeric_level_badge_is_not_a_balance_when_the_total_is_loading(self):
		self.assertIsNone(self.read(profile("Synthetic Name\nLevel\n2\nLoading...", parts=("2", "Loading...")))[0])
		self.assertEqual(self.read(profile("Synthetic Name\nLevel\n2\n12,345", parts=("2", "12,345")))[0], 12345)

	def test_responsive_hidden_text_content_when_no_visible_copy_can_be_read(self):
		value, observation = self.read(profile("", content="12,345", displayed=False), size=(320, 480))
		self.assertEqual(value, 12345)
		self.assertEqual(observation["buttons"][0]["displayed"], False)
		self.assertEqual(observation["inner_size"], [320, 480])

	def test_visible_copy_wins_over_an_old_hidden_copy(self):
		self.assertEqual(self.read(profile("", content="98,765", displayed=False), profile("12,345"))[0], 12345)

	def test_visible_loading_copy_cannot_reuse_a_hidden_stale_value(self):
		self.assertIsNone(self.read(profile("", content="98,765", displayed=False), profile("Loading..."))[0])

	def test_conflicting_copies_or_numeric_children_are_not_guessed(self):
		for buttons in ((profile("12,345"), profile("98,765")),
			(profile("", content="12,345", displayed=False), profile("", content="98,765", displayed=False)),
			(profile("Level 2 12,345 98,765", parts=("12,345", "98,765")),)):
			with self.subTest(buttons=buttons):
				self.assertIsNone(self.read(*buttons)[0])

	def test_app_header_without_profile_button_requires_a_balance_label(self):
		# Covers the spoof-header/variant hypothesis without changing headers,
		# cookies or the next task's document and without reading app state.
		self.assertEqual(self.read(header_texts=("Microsoft Rewards\nAvailable points\n12,345\nLevel 2",))[0], 12345)
		self.assertEqual(self.read(header_texts=("Points balance: 12,345",))[0], 12345)
		self.assertIsNone(self.read(header_texts=("Today's points\n98\nDaily streak\n2",))[0])

	def test_rejects_fractions_loading_malformed_and_ambiguous_numbers(self):
		for text in ("", "Loading...", "12,34", "12.34", "12 34", "60/60", "-1", "+10", "246 points today", "Level 2", "12,345\n98,765"):
			with self.subTest(text=text):
				self.assertIsNone(self.read(profile(text))[0])

	def test_null_observation_masks_a_synthetic_name_digits_and_unicode_letters(self):
		text = "Synthetic N\u00e4me 42!\nLoading..."
		value, observation = self.read(profile(text))
		self.assertIsNone(value)
		self.assertEqual(observation, {"headers": 1, "profile_buttons": 1,
			"buttons": [{"displayed": True, "text_shape": "aaaaaaaaa aaaa 99!\naaaaaaa...", "content_shape": "aaaaaaaaa aaaa 99!\naaaaaaa..."}],
			"inner_size": [1131, 643], "complete": True})
		encoded = json.dumps(observation)
		self.assertNotIn("Synthetic", encoded)
		self.assertNotIn("42", encoded)

	def test_empty_and_multiple_headers_are_observed_without_page_or_account_state(self):
		for headers in (0, 2):
			with self.subTest(headers=headers):
				driver = SnapshotDriver()
				driver.packet["headers"] = headers
				elements = element_selectors.ElementSelectionUtils(driver)
				self.assertIsNone(rewards_state.read_balance(driver, elements))
				self.assertEqual(elements.balance_observation, {"headers": headers, "profile_buttons": 0,
					"buttons": [], "inner_size": [1131, 643], "complete": True})

	def test_failed_reading_records_every_profile_buttons_visibility_and_masked_shape(self):
		value, observation = self.read(profile("Synthetic Name\nLoading..."), profile("", content="Not ready 42", displayed=False))
		self.assertIsNone(value)
		self.assertEqual(observation["profile_buttons"], 2)
		self.assertEqual(observation["buttons"], [
			{"displayed": True, "text_shape": "aaaaaaaaa aaaa\naaaaaaa...", "content_shape": "aaaaaaaaa aaaa\naaaaaaa..."},
			{"displayed": False, "text_shape": "", "content_shape": "aaa aaaaa 99"}])

	def test_after_snapshot_uses_the_same_variant_reader_without_a_balance_click(self):
		for button in (profile("Synthetic Name\n12,345 points"), profile("", content="12.345", displayed=False)):
			with self.subTest(button=button):
				class VariantStateDriver(StateDriver):
					def execute_script(self, script, *args):
						if script == element_selectors._BALANCE_SNAPSHOT:
							return SnapshotDriver((button,)).packet
						return super().execute_script(script, *args)
				driver = VariantStateDriver()
				with mock.patch.object(rewards_state.time, "sleep", return_value=None):
					state = rewards_state.read_state(driver, element_selectors.ElementSelectionUtils(driver), timeout=0)
				self.assertEqual(state.balance, 12345)
				self.assertIsNone(state.unreadable)
				self.assertEqual(driver.clicks, [driver.breakdown, driver.close, driver.daily_opener, driver.close])


class BalanceBudgetTests(unittest.TestCase):
	def test_night_pattern_charges_command_latency_and_later_readings_do_not_poll(self):
		clock = Clock()
		tasks = make_tasks(clock)
		calls = []
		def reader():
			calls.append(clock())
			clock.now += 0.12
			return None
		tasks.balance_reader = reader
		with mock.patch.object(rewards_tasks.run_record, "emit") as emit, mock.patch.object(rewards_tasks.logger, "info"):
			tasks.complete_all_tasks(clock=clock, sleep=clock.sleep)
		ends = [call.kwargs for call in emit.call_args_list if call.args == ("task_end",)]
		self.assertLessEqual(clock(), 60)
		self.assertLess(clock(), rewards_tasks.BALANCE_READ_TIMEOUT + 1.1)
		self.assertTrue(all(row["points_after"] is None for row in ends))
		self.assertTrue(all(row["balance_read"]["after"]["attempts"] == 1 for row in ends))
		self.assertTrue(all(row["balance_read"]["after"]["wait_mode"] == "immediate" for row in ends))
		self.assertEqual(len(calls), ends[0]["balance_read"]["before"]["attempts"] + 7)

	def test_shared_budget_survives_alternating_timeout_success_and_task_time(self):
		clock = Clock()
		tasks = make_tasks(clock, ready_at=float("inf"))
		# One timeout, one immediate success, repeat. Advancing task time must
		# not consume the reading budget or reset the shared remaining amount.
		elapsed = 0
		for _ in range(12):
			tasks.driver.ready_at = float("inf")
			began = clock()
			self.assertIsNone(tasks._read_balance(clock=clock, sleep=clock.sleep))
			elapsed += clock() - began
			clock.now += 100
			tasks.driver.ready_at = 0
			began = clock()
			tasks._read_balance(clock=clock, sleep=clock.sleep)
			elapsed += clock() - began
		self.assertLessEqual(elapsed, 60)
		self.assertEqual(tasks._balance_remaining, 0)

	def test_immediate_success_reenables_polling_and_a_new_run_resets_suppression(self):
		clock = Clock()
		tasks = make_tasks(clock, ready_at=float("inf"))
		self.assertIsNone(tasks._read_balance(clock=clock, sleep=clock.sleep))
		tasks.driver.ready_at = 0
		self.assertEqual(tasks._read_balance(clock=clock, sleep=clock.sleep), 1000)
		tasks.driver.ready_at = clock() + 0.5
		self.assertEqual(tasks._read_balance(clock=clock, sleep=clock.sleep), 1000)
		self.assertEqual(clock(), 8)
		tasks.driver.ready_at = float("inf")
		with mock.patch.object(rewards_tasks.run_record, "emit"), mock.patch.object(rewards_tasks.logger, "info"):
			for _ in range(2):
				began = clock()
				tasks.complete_all_tasks(clock=clock, sleep=clock.sleep)
				self.assertEqual(clock() - began, 7.5)

	def test_executor_bounds_every_command_and_restores_timeout_and_retry_behavior(self):
		clock = Clock()
		config = types.SimpleNamespace(timeout=120)
		seen = []
		connection = types.SimpleNamespace(request=lambda **kwargs: seen.append(kwargs))
		executor = types.SimpleNamespace(_client_config=config, _conn=connection)
		def execute():
			connection.request(timeout=config.timeout)
			clock.now += 1
		executor.execute = execute
		driver = types.SimpleNamespace(command_executor=executor)
		request = connection.request
		with rewards_tasks._balance_command_deadline(driver, 2, clock):
			executor.execute()
			executor.execute()
			with self.assertRaises(TimeoutException):
				executor.execute()
		self.assertEqual([call["timeout"].total for call in seen], [2 - rewards_tasks.BALANCE_COMMAND_MARGIN, 1 - rewards_tasks.BALANCE_COMMAND_MARGIN])
		self.assertTrue(all(call["retries"] is False for call in seen))
		self.assertEqual(config.timeout, 120)
		self.assertEqual(executor.execute, execute)
		self.assertEqual(connection.request, request)

	def test_serial_command_latency_consumes_the_same_budget_and_nested_deadline(self):
		clock = Clock()
		tasks = make_tasks(clock)
		config = types.SimpleNamespace(timeout=120)
		seen = []
		executor = types.SimpleNamespace(_client_config=config)
		def execute(seconds):
			remaining = config.timeout.total
			seen.append(remaining)
			clock.now += min(seconds, remaining)
			if seconds > remaining:
				raise TimeoutException("synthetic command latency")
		executor.execute = execute
		tasks.driver.command_executor = executor
		def slow_reader():
			# Serial calls cannot each inherit the whole poll timeout.
			executor.execute(0.2)
			executor.execute(0.2)
			return None
		tasks.balance_reader = slow_reader
		# Force the last immediate attempt to have less than one command left.
		tasks._balance_remaining = 8
		began = clock()
		for _ in range(8):
			tasks._read_balance(clock=clock, sleep=clock.sleep)
		self.assertLessEqual(clock() - began, 8)
		self.assertLessEqual(tasks._balance_remaining, rewards_tasks.BALANCE_COMMAND_MARGIN + 0.001)
		self.assertLess(min(seen), 0.2)
		self.assertEqual(config.timeout, 120)
		self.assertEqual(executor.execute, execute)
		self.assertFalse(hasattr(executor, "_balance_deadline"))

	def test_missing_snapshot_does_not_reuse_an_earlier_reading_observation(self):
		clock = Clock()
		tasks = make_tasks(clock)
		tasks.elements.balance_observation = {"headers": 99}
		tasks.balance_reader = mock.Mock(side_effect=TimeoutException("private error"))
		self.assertIsNone(tasks._read_balance(clock=clock, sleep=clock.sleep, timeout=0))
		self.assertIsNone(tasks._balance_read_diagnostic["observation"])
		self.assertNotIn("private", json.dumps(tasks._balance_read_diagnostic))

	def test_last_budget_slice_reserves_navigation_timeout_restore(self):
		clock = Clock()
		tasks = make_tasks(clock, ready_at=float("inf"))
		tasks.driver.pages["main"] = "https://www.bing.com/"
		tasks.driver.get_duration = 100
		tasks._balance_remaining = 1
		self.assertIsNone(tasks._read_balance(prepare=True, clock=clock, sleep=clock.sleep))
		self.assertEqual(tasks.driver.navigations, [(rewards_tasks.REWARDS_HOME_URL, 0.5)])
		self.assertEqual(tasks.driver.timeouts.page_load, 60)
		self.assertLessEqual(clock(), 1)

	def test_unreadable_diagnostic_records_post_navigation_host_from_the_snapshot(self):
		clock = Clock()
		tasks = make_tasks(clock, ready_at=float("inf"))
		tasks.driver.pages["main"] = "https://www.bing.com/search?q=private"
		self.assertIsNone(tasks._read_balance(prepare=True, clock=clock, sleep=clock.sleep))
		self.assertEqual(tasks._balance_read_diagnostic["initial_host"], "www.bing.com")
		self.assertEqual(tasks._balance_read_diagnostic["host"], "rewards.bing.com")
		self.assertNotIn("private", json.dumps(tasks._balance_read_diagnostic))

	def test_real_selenium_http_read_is_bounded_and_not_retried(self):
		calls = []
		release = threading.Event()
		class Handler(BaseHTTPRequestHandler):
			def do_POST(self):
				calls.append(self.path)
				release.wait(2)
			def log_message(self, *args):
				pass
		server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		thread = threading.Thread(target=server.serve_forever, daemon=True)
		thread.start()
		config = ClientConfig(remote_server_addr=f"http://127.0.0.1:{server.server_port}", timeout=5)
		executor = RemoteConnection(client_config=config)
		driver = WebDriver.__new__(WebDriver)
		driver.command_executor, driver.session_id = executor, "synthetic"
		driver._is_remote = False
		driver.error_handler = types.SimpleNamespace(check_response=lambda _: None)
		began = time.monotonic()
		try:
			with rewards_tasks._balance_command_deadline(driver, began + 0.15, time.monotonic):
				with self.assertRaises(Exception):
					driver.execute_script("return null;")
			elapsed = time.monotonic() - began
			self.assertGreater(elapsed, 0.05)
			self.assertLess(elapsed, 0.8)
			self.assertEqual(calls, ["/session/synthetic/execute/sync"])
			self.assertEqual(config.timeout, 5)
		finally:
			release.set()
			executor.close()
			server.shutdown()
			server.server_close()
			thread.join(2)

	def test_task_loop_holds_its_shared_wall_clock_budget_with_a_real_stalled_transport(self):
		calls = []
		release = threading.Event()
		class Handler(BaseHTTPRequestHandler):
			def do_POST(self):
				calls.append(self.path)
				release.wait(2)
			def log_message(self, *args):
				pass
		server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		thread = threading.Thread(target=server.serve_forever, daemon=True)
		thread.start()
		config = ClientConfig(remote_server_addr=f"http://127.0.0.1:{server.server_port}", timeout=5)
		executor = RemoteConnection(client_config=config)
		driver = WebDriver.__new__(WebDriver)
		driver.command_executor, driver.session_id = executor, "synthetic"
		driver._is_remote = False
		driver.error_handler = types.SimpleNamespace(check_response=lambda _: None)
		tasks = make_tasks(Clock())
		tasks.driver = driver
		tasks.balance_reader = lambda: rewards_state.read_balance(driver, tasks.elements)
		tasks.elements = element_selectors.ElementSelectionUtils(driver)
		began = time.monotonic()
		try:
			# Scale the same budget rule so this is a fast wall-clock test, not a
			# sixty-second wait in every unit suite. No browser session is made.
			with mock.patch.object(rewards_tasks, "BALANCE_RUN_BUDGET", 0.35), \
				mock.patch.object(tasks, "_balance_host", return_value="rewards.bing.com"), \
				mock.patch.object(tasks, "restore_main_tab"), mock.patch.object(tasks, "_prepare_balance_page", return_value="not_needed"), \
				mock.patch.object(rewards_tasks.run_record, "emit") as emit, mock.patch.object(rewards_tasks.logger, "info"):
				tasks.complete_all_tasks()
			elapsed = time.monotonic() - began
			self.assertLessEqual(elapsed, 0.35)
			self.assertGreater(elapsed, 0.15)
			# Each boundary is allowed its one immediate attempt while budget
			# remains. The reserved return time can fit a second short request;
			# that is not an HTTP retry of the first reading.
			self.assertGreaterEqual(len(calls), 1)
			self.assertLessEqual(len(calls), 8)
			self.assertTrue(all(path == "/session/synthetic/execute/sync" for path in calls))
			self.assertEqual(config.timeout, 5)
			ends = [call.kwargs for call in emit.call_args_list if call.args == ("task_end",)]
			self.assertEqual(len(ends), 7)
			self.assertTrue(all(row["points_after"] is None for row in ends))
		finally:
			release.set()
			executor.close()
			server.shutdown()
			server.server_close()
			thread.join(2)


if __name__ == "__main__":
	unittest.main()
