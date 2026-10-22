import _bootstrap  # noqa: F401

import http.client
import io
import json
import os
import socket
import struct
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from ui import harness


def resource_row(code="ERR_CONNECTION_RESET", url="http://127.0.0.1:32123/img/app-icon.svg?proof=exact%20url"):
	return {"level": "SEVERE", "source": "network", "timestamp": 12345,
		"message": f"{url} - Failed to load resource: net::{code}"}


class TransportRowTests(unittest.TestCase):
	def setUp(self):
		self.owned = harness.UiHarness()
		self.owned.server = SimpleNamespace(server_port=32123)
		self.owned.driver = Mock()
		self.owned.driver.get_log.return_value = []
		self.output = io.StringIO()
		self.addCleanup(self._release_fakes)
		self.addCleanup(patch.stopall)
		patch.object(harness, "_tcp_snapshot", return_value={"status": "ok", "states": {"TIME_WAIT": 4}}).start()
		self.connection = Mock()
		self.response = self.connection.getresponse.return_value
		self.response.status = 200
		self.expected = (harness.UI_DIR / "img/app-icon.svg").read_bytes()
		self.body = self.expected
		def request(*_args, **_kwargs):
			self.chunks = iter((self.body, b""))
		self.connection.request.side_effect = request
		self.response.read1.side_effect = lambda _limit: next(self.chunks)
		self.factory = patch.object(harness.http.client, "HTTPConnection", return_value=self.connection).start()

	def _release_fakes(self):
		self.owned.driver = None
		self.owned.server = None

	def errors(self, rows):
		self.owned.driver.get_log.return_value = rows
		with redirect_stdout(self.output):
			return self.owned.errors()

	def records(self):
		return [json.loads(line.removeprefix("harness_transport_error "))
			for line in self.output.getvalue().splitlines() if line.startswith("harness_transport_error ")]

	def test_each_supported_code_needs_exact_refetch_and_is_recorded(self):
		for code in sorted(harness.TRANSPORT_CODES):
			with self.subTest(code=code):
				self.owned.transport_dropped = self.owned._transport_verified = 0
				row = resource_row(code)
				self.assertEqual([], self.errors([row]))
				record = self.records()[-1]
				self.assertEqual(self.id(), record["test"])
				self.assertEqual(row["message"].split(" - ")[0], record["url"])
				self.assertEqual(code, record["error"])
				self.assertEqual(200, record["refetch"])
				self.assertIs(True, record["bytes_equal"])
				self.assertIs(True, record["dropped"])
				self.assertEqual(12345, record["browser_timestamp"])
				self.assertEqual({"TIME_WAIT": 4}, record["tcp"]["states"])
				self.assertEqual(1, self.owned.transport_dropped)
		self.factory.assert_called_with("127.0.0.1", 32123, timeout=2)
		self.connection.request.assert_called_with("GET", "/img/app-icon.svg?proof=exact%20url",
			headers={"Connection": "close", "Accept-Encoding": "identity"})
		self.connection.close.assert_called()
		self.assertEqual(6, len(self.records()))

	def test_different_bytes_stay_red(self):
		self.body = b"different static bytes"
		row = resource_row()
		self.assertEqual([row["message"]], self.errors([row]))
		self.assertEqual(0, self.owned.transport_dropped)
		self.assertIs(False, self.records()[0]["bytes_equal"])
		self.assertIs(False, self.records()[0]["dropped"])

	def test_refetch_non_200_and_failures_stay_red(self):
		for status in (301, 304, 404, 500):
			with self.subTest(status=status):
				self.response.status = status
				row = resource_row()
				self.assertEqual([row["message"]], self.errors([row]))
				self.assertEqual(status, self.records()[-1]["refetch"])
		for error in (TimeoutError("synthetic timeout"), ConnectionRefusedError("synthetic refusal"), http.client.RemoteDisconnected()):
			with self.subTest(error=type(error).__name__):
				self.connection.getresponse.side_effect = error
				row = resource_row()
				self.assertEqual([row["message"]], self.errors([row]))
				self.assertEqual(type(error).__name__, self.records()[-1]["exception"])
		self.assertEqual(0, self.owned.transport_dropped)

	def test_http_javascript_missing_key_and_other_rows_stay_red(self):
		rows = [
			{"level": "SEVERE", "source": "network", "message": "http://127.0.0.1:32123/img/app-icon.svg - Failed to load resource: the server responded with a status of 500 (Internal Server Error)"},
			{"level": "SEVERE", "source": "network", "message": "http://127.0.0.1:32123/img/missing.svg - Failed to load resource: the server responded with a status of 404 (Not Found)"},
			{"level": "SEVERE", "source": "javascript", "message": "Uncaught Error: synthetic JavaScript failure"},
			{"level": "SEVERE", "source": "console-api", "message": "Missing translation key: shell.synthetic.missing"},
			resource_row("ERR_FAILED"), resource_row("ERR_ABORTED"),
			resource_row(url="http://localhost:32123/img/app-icon.svg"),
			resource_row(url="http://127.0.0.1:32124/img/app-icon.svg"),
			resource_row(url="http://example.com:32123/img/app-icon.svg"),
			resource_row(url="http://127.0.0.1:32123@elsewhere/img/app-icon.svg"),
			{**resource_row(), "source": "javascript"},
		]
		self.assertEqual([row["message"] for row in rows], self.errors(rows))
		self.factory.assert_not_called()
		self.assertEqual([], self.records())

	def test_existing_locale_404_and_non_severe_behavior_is_unchanged(self):
		rows = [{"level": "SEVERE", "message": "http://127.0.0.1:32123/i18n/de/optional.json - Failed to load resource: 404 (Not Found)"},
			{**resource_row(), "level": "WARNING"}]
		self.assertEqual([], self.errors(rows))
		self.assertEqual(0, self.owned.transport_dropped)
		self.factory.assert_not_called()

	def test_only_regular_files_inside_ui_root_qualify(self):
		rows = [resource_row(url="http://127.0.0.1:32123/"), resource_row(url="http://127.0.0.1:32123/img/"),
			resource_row(url="http://127.0.0.1:32123/missing.svg"),
			resource_row(url="http://127.0.0.1:32123/%2e%2e/%2e%2e/AGENTS.md"),
			resource_row(url="http://127.0.0.1:32123/img/app-icon.svg#fragment"),
			resource_row(url="https://127.0.0.1:32123/img/app-icon.svg")]
		self.assertEqual([row["message"] for row in rows], self.errors(rows))
		self.factory.assert_not_called()

	def test_symlink_resolution_outside_ui_root_is_not_refetched(self):
		row = resource_row()
		outside = harness.UI_DIR.parent / "outside-static.svg"
		with patch.object(harness.Path, "resolve", side_effect=[harness.UI_DIR, outside]):
			self.assertEqual([row["message"]], self.errors([row]))
		self.factory.assert_not_called()

	def test_encoded_static_path_keeps_exact_request_query(self):
		row = resource_row(url="http://127.0.0.1:32123/img/%61pp-icon.svg?case=encoded%2Fpath")
		self.assertEqual([], self.errors([row]))
		self.connection.request.assert_called_with("GET", "/img/%61pp-icon.svg?case=encoded%2Fpath",
			headers={"Connection": "close", "Accept-Encoding": "identity"})

	def test_empty_static_file_requires_empty_200_body(self):
		with tempfile.TemporaryDirectory() as temporary:
			from pathlib import Path
			root = Path(temporary)
			(root / "empty.txt").write_bytes(b"")
			row = resource_row(url="http://127.0.0.1:32123/empty.txt")
			self.body = b""
			with patch.object(harness, "UI_DIR", root):
				self.assertEqual([], self.errors([row]))
				self.body = b"unexpected"
				self.assertEqual([row["message"]], self.errors([row]))

	def test_source_and_message_shape_cannot_spoof_a_resource(self):
		rows = [{**resource_row(), "source": "console-api"},
			{**resource_row(), "message": resource_row()["message"] + " extra"},
			resource_row(url="http://127.0.0.1:32123/img/app-icon.svg\nforged")]
		self.assertEqual([row["message"] for row in rows], self.errors(rows))
		self.factory.assert_not_called()

	def test_each_refetch_opens_and_closes_its_own_connection(self):
		clients = [Mock(), Mock()]
		for client in clients:
			client.getresponse.return_value.status = 200
			client.getresponse.return_value.read1.side_effect = [self.expected, b""]
		self.factory.side_effect = clients
		self.assertEqual([], self.errors([resource_row(), resource_row()]))
		for client in clients:
			client.request.assert_called_once()
			client.close.assert_called_once()
		self.assertEqual(2, self.owned.transport_dropped)

	def test_refetch_deadline_and_overlong_body_stay_red(self):
		row = resource_row()
		with patch.object(harness.time, "monotonic", side_effect=[10, 10, 13]):
			self.assertEqual([row["message"]], self.errors([row]))
		self.assertEqual("TimeoutError", self.records()[-1]["exception"])
		self.body = self.expected + b"extra"
		self.assertEqual([row["message"]], self.errors([row]))
		self.assertIs(False, self.records()[-1]["bytes_equal"])
		self.connection.close.assert_called()

	def test_cap_failure_survives_driver_quit_error(self):
		self.owned._transport_cap_exceeded = True
		self.owned.driver.quit.side_effect = RuntimeError("synthetic quit failure")
		self.owned.server = None
		with redirect_stdout(self.output), self.assertRaisesRegex(AssertionError, "exceeded module limit 3"):
			self.owned.stop()
		self.assertIsNone(self.owned.driver)
		self.assertIn("cap_exceeded=True", self.output.getvalue())

	def test_disk_read_failure_keeps_the_row_red(self):
		row = resource_row()
		with patch.object(harness.Path, "read_bytes", side_effect=PermissionError("synthetic unreadable file")):
			self.assertEqual([row["message"]], self.errors([row]))
		self.assertEqual("PermissionError", self.records()[-1]["exception"])
		self.factory.assert_not_called()

	def test_cap_is_sticky_and_fails_module_after_cleanup(self):
		self.assertEqual([], self.errors([resource_row()] * harness.TRANSPORT_DROP_LIMIT))
		self.assertEqual(harness.TRANSPORT_DROP_LIMIT, self.owned.transport_dropped)
		self.assertIn(resource_row()["message"], self.errors([resource_row()]))
		self.assertIn("exceeded module limit 3", self.errors([])[0])
		self.owned.server = None
		with redirect_stdout(self.output), self.assertRaisesRegex(AssertionError, "exceeded module limit 3"):
			self.owned.stop()
		self.owned.driver = None
		self.assertIn("harness_transport_summary dropped=3 verified=4 limit=3 cap_exceeded=True", self.output.getvalue())

	def test_module_summary_reports_zero_and_is_not_duplicated(self):
		self.owned.server = None
		with redirect_stdout(self.output):
			self.owned.stop()
			self.owned.stop()
		self.assertEqual(1, self.output.getvalue().count("harness_transport_summary dropped=0"))


class TcpSnapshotTests(unittest.TestCase):
	def test_windows_counts_states_without_logging_endpoints(self):
		result = SimpleNamespace(returncode=0, stdout="  TCP 127.0.0.1:32123 127.0.0.1:50000 ESTABLISHED\n  TCP 0.0.0.0:99 0.0.0.0:0 LISTENING\n  TCP 127.0.0.1:50001 127.0.0.1:32123 TIME_WAIT\n  UDP 0.0.0.0:99 *:*\n")
		with patch.object(harness.os, "name", "nt"), patch.object(harness.subprocess, "run", return_value=result) as query:
			snapshot = harness._tcp_snapshot(32123)
		self.assertEqual({"ESTABLISHED": 1, "LISTENING": 1, "TIME_WAIT": 1}, snapshot["states"])
		self.assertEqual({"ESTABLISHED": 1, "TIME_WAIT": 1}, snapshot["harness_states"])
		self.assertNotIn("127.0.0.1", json.dumps(snapshot))
		self.assertEqual("-an", query.call_args.args[0][1])
		self.assertEqual(2, query.call_args.kwargs["timeout"])

	def test_non_windows_snapshot_is_explicitly_skipped(self):
		with patch.object(harness.os, "name", "posix"), patch.object(harness.subprocess, "run") as query:
			self.assertEqual("skipped", harness._tcp_snapshot(32123)["status"])
		query.assert_not_called()

	def test_snapshot_failure_stays_visible_in_record(self):
		with patch.object(harness.os, "name", "nt"), patch.object(harness.subprocess, "run", side_effect=subprocess.TimeoutExpired("netstat", 2)):
			self.assertEqual("TimeoutExpired", harness._tcp_snapshot(32123)["exception"])


@contextmanager
def reset_static_requests(owned, marker, always=False):
	original = harness._Handler.do_GET
	state = {"requests": 0, "resets": 0}
	def fault(handler):
		parts = urlsplit(handler.path)
		if handler.server is owned.server and parts.path == "/img/app-icon.svg" and parts.query == marker:
			state["requests"] += 1
			if always or state["resets"] == 0:
				state["resets"] += 1
				body = (harness.UI_DIR / "img/app-icon.svg").read_bytes()
				handler.send_response(200)
				handler.send_header("Content-Type", "image/svg+xml")
				handler.send_header("Content-Length", str(len(body)))
				handler.end_headers()
				handler.wfile.write(body[:1])
				handler.wfile.flush()
				handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh" if os.name == "nt" else "ii", 1, 0))
				handler.close_connection = True
				handler.connection.close()
				return
		return original(handler)
	with patch.object(harness._Handler, "do_GET", fault):
		yield state


@unittest.skipUnless(os.environ.get("REWARDS_APP_UI_TESTS") == "1", "Set REWARDS_APP_UI_TESTS=1 through the guarded parent UI runner")
class BrowserTransportTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.owned = harness.UiHarness()
		try:
			cls.owned.start().wait_ready()
		except BaseException:
			cls.owned.stop()
			raise

	@classmethod
	def tearDownClass(cls):
		cls.owned.stop()

	def fail_image(self, marker):
		return self.owned.driver.execute_async_script("""
			const marker=arguments[0],done=arguments[1],image=new Image();
			image.onload=()=>{image.remove();done('loaded')};image.onerror=()=>{image.remove();done('failed')};
			image.src='./img/app-icon.svg?'+marker;document.querySelector('#view').append(image);
		""", marker)

	def test_reset_once_is_independently_refetched_and_recorded(self):
		self.owned.open("#/overview", "shell/overview").wait_ready()
		self.assertEqual([], self.owned.errors())
		output = io.StringIO()
		with reset_static_requests(self.owned, "transport-once") as fault:
			self.assertEqual("failed", self.fail_image("transport-once"))
			with redirect_stdout(output):
				self.assertEqual([], self.owned.errors())
			self.assertEqual(1, fault["resets"])
			self.assertEqual(2, fault["requests"])
		print(output.getvalue(), end="", flush=True)
		records = [json.loads(line.removeprefix("harness_transport_error ")) for line in output.getvalue().splitlines()]
		self.assertEqual(1, len(records))
		self.assertIs(True, records[0]["dropped"])
		self.assertEqual(200, records[0]["refetch"])
		self.assertIs(True, records[0]["bytes_equal"])
		self.assertIn(records[0]["error"], harness.TRANSPORT_CODES)
		self.assertEqual(self.id(), records[0]["test"])

	def test_reset_every_request_stays_red(self):
		self.owned.open("#/overview", "shell/overview").wait_ready()
		self.assertEqual([], self.owned.errors())
		before = self.owned.transport_dropped
		output = io.StringIO()
		with reset_static_requests(self.owned, "transport-always", always=True) as fault:
			self.assertEqual("failed", self.fail_image("transport-always"))
			with redirect_stdout(output):
				errors = self.owned.errors()
			self.assertTrue(errors)
			self.assertIn("transport-always", errors[0])
			self.assertGreaterEqual(fault["resets"], 2)
		print(output.getvalue(), end="", flush=True)
		record = json.loads(output.getvalue().strip().removeprefix("harness_transport_error "))
		self.assertIs(False, record["dropped"])
		self.assertIs(False, record["bytes_equal"])
		self.assertEqual(before, self.owned.transport_dropped)
