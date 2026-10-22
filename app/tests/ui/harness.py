import json
import os
import shutil
import socket
import tempfile
import threading
import time
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit


UI_DIR = Path(__file__).resolve().parents[2] / "ui"
SCRATCH = Path(os.environ.get("REWARDS_APP_UI_SCRATCH", str(Path(tempfile.gettempdir()) / "rewards-farmer-ui")))
SCREENSHOT_DIR = Path(os.environ.get("REWARDS_APP_SCREENSHOT_DIR", str(Path(tempfile.gettempdir()) / "rewards-farmer-ui" / datetime.now().strftime("%Y%m%d-%H%M%S"))))


def browser_preflight():
	if os.environ.get("REWARDS_APP_UI_TESTS") != "1":
		raise RuntimeError("UI browser tests require REWARDS_APP_UI_TESTS=1")
	return True


class _HttpServer(ThreadingHTTPServer):
	def __init__(self, *args, **kwargs):
		self._stats_lock = threading.Lock()
		self._connections = 0
		self._requests = 0
		self._active_connections = {}
		self._requests_by_connection = {}
		super().__init__(*args, **kwargs)

	def get_request(self):
		connection, address = super().get_request()
		with self._stats_lock:
			self._connections += 1
			self._active_connections[connection] = self._connections
			self._requests_by_connection[self._connections] = 0
		return connection, address

	def record_request(self, connection):
		with self._stats_lock:
			identity = self._active_connections[connection]
			self._requests += 1
			self._requests_by_connection[identity] += 1

	def release_connection(self, connection):
		with self._stats_lock:
			self._active_connections.pop(connection, None)

	def stats(self):
		with self._stats_lock:
			return {"connections": self._connections, "requests": self._requests,
				"requests_by_connection": dict(self._requests_by_connection)}

	def server_close(self):
		# These are only sockets accepted by this harness, never other runtimes.
		with self._stats_lock:
			connections = list(self._active_connections)
		for connection in connections:
			try:
				connection.shutdown(socket.SHUT_RDWR)
			except OSError:
				pass
			connection.close()
		super().server_close()


class _Handler(SimpleHTTPRequestHandler):
	protocol_version = "HTTP/1.1"

	def do_GET(self):
		path = urlsplit(self.path).path
		# Screen lanes have not authored their CSS during the shell gate. Their
		# absent sheets are empty here only; existing sheets and JS are never masked.
		if path.startswith("/css/screens/") and path.endswith(".css") and not (UI_DIR / path.lstrip("/")).is_file():
			self.send_response(200)
			self.send_header("Content-Type", "text/css")
			self.send_header("Content-Length", "0")
			self.end_headers()
			return
		super().do_GET()

	def end_headers(self):
		# Python 3.12 send_head frames files, directories and redirects; send_error
		# frames its body and closes. A 304 has no body. Close any other unframed
		# final response so a future custom path cannot leave keep-alive hanging.
		headers = getattr(self, "_headers_buffer", [])
		status = headers[0].split()[1] if headers else b""
		framed = any(header.lower().startswith(b"content-length:") for header in headers)
		if not framed and not status.startswith(b"1"):
			self.send_header("Connection", "close")
		super().end_headers()

	def log_request(self, code="-", size="-"):
		# One final response per handled request, including parser/error responses.
		self.server.record_request(self.request)

	def finish(self):
		try:
			super().finish()
		finally:
			self.server.release_connection(self.request)

	def log_message(self, *args):
		pass


class UiHarness:
	def __init__(self):
		self.driver = None
		self.server = None
		self.thread = None
		self.profile = None
		self.signed_in = None
		self.inprivate = True
		self.http_counts = {"connections": 0, "requests": 0, "requests_by_connection": {}}

	def start(self):
		if self.driver is not None:
			raise RuntimeError("This module already has a browser")
		browser_preflight()
		from selenium import webdriver
		SCRATCH.mkdir(parents=True, exist_ok=True)
		self.profile = Path(tempfile.mkdtemp(prefix="edge-shell-", dir=SCRATCH))
		self.server = _HttpServer(("127.0.0.1", 0), partial(_Handler, directory=str(UI_DIR)))
		self.thread = threading.Thread(target=self.server.serve_forever, name="ui-loopback", daemon=True)
		self.thread.start()
		options = webdriver.EdgeOptions()
		for argument in ("--headless=new", "--inprivate", "--disable-component-update", "--window-size=1280,800", f"--user-data-dir={self.profile}", "--lang=en-US"):
			options.add_argument(argument)
		options.set_capability("ms:loggingPrefs", {"browser": "ALL"})
		try:
			self.driver = webdriver.Edge(options=options)
			self.driver.set_page_load_timeout(25)
			self.driver.set_script_timeout(25)
			self.driver.get(self.url("#/overview", "shell/overview"))
			state_path = self.profile / "Local State"
			if not state_path.is_file():
				raise RuntimeError("Temporary Local State missing; account state cannot be proven")
			state = json.loads(state_path.read_text(encoding="utf-8"))
			profiles = state.get("profile", {}).get("info_cache", {}).values()
			self.signed_in = bool(state.get("account_info")) or any(any(info.get(key) for key in ("user_name", "gaia_id", "edge_account_id", "account_id")) for info in profiles)
			print("temporary_profile_signed_in=" + ("yes" if self.signed_in else "no"), flush=True)
			if self.signed_in:
				raise RuntimeError("Temporary Edge profile signed in; browser proof stopped")
			self.wait_ready()
		except BaseException:
			self.stop()
			raise
		return self

	def stop(self):
		try:
			try:
				if self.driver is not None:
					self.driver.quit()
			finally:
				self.driver = None
				self._remove_profile()
		finally:
			if self.server is not None:
				self.server.shutdown()
				self.server.server_close()
				self.http_counts = self.server.stats()
				print(f"harness_connections={self.http_counts['connections']} harness_requests={self.http_counts['requests']}", flush=True)
				self.server = None
			if self.thread is not None:
				self.thread.join(timeout=5)
				if self.thread.is_alive():
					raise RuntimeError("Owned HTTP server thread did not stop")
				self.thread = None

	def _remove_profile(self):
		profile = self.profile
		if profile is None:
			return
		try:
			# Only the direct, real directory this harness made, never a link or a sibling.
			if profile.is_symlink() or profile.is_junction() or profile.parent.resolve() != SCRATCH.resolve() or profile.resolve().parent != SCRATCH.resolve() or not profile.name.startswith("edge-shell-"):
				print(f"harness_profile_cleanup=refused path={profile}", flush=True)
				return
			deadline = time.monotonic() + 5
			while True:
				try:
					shutil.rmtree(profile)
					print(f"harness_profile_cleanup=removed path={profile}", flush=True)
					self.profile = None
					return
				except FileNotFoundError:
					self.profile = None
					return
				except OSError as failure:
					if time.monotonic() >= deadline:
						print(f"harness_profile_cleanup=failed path={profile} error={failure}", flush=True)
						return
					time.sleep(0.2)
		except OSError as failure:
			print(f"harness_profile_cleanup=failed path={profile} error={failure}", flush=True)

	def connection_stats(self):
		return self.server.stats() if self.server is not None else self.http_counts

	def url(self, route, fixture, theme="dark", lang="en", reduced_motion=False):
		query = urlencode({"mock": fixture, "theme": theme, "lang": lang, "reduced_motion": "1" if reduced_motion else "0"})
		return f"http://127.0.0.1:{self.server.server_port}/index.html?{query}{route}"

	def open(self, route, fixture, theme="dark", lang="en", reduced_motion=False):
		url = self.url(route, fixture, theme, lang, reduced_motion)
		if urlsplit(url).hostname != "127.0.0.1":
			raise RuntimeError("Only loopback pages are permitted")
		if self.driver.current_url == url:
			self.driver.refresh()
		else:
			self.driver.get(url)
		return self

	def wait_ready(self, timeout=20):
		from selenium.webdriver.support.ui import WebDriverWait
		def ready(browser):
			state = browser.execute_script("return {ready:document.getElementById('app').dataset.ready,busy:document.getElementById('view').getAttribute('aria-busy')} ")
			if state.get("ready") == "error":
				raise RuntimeError("Shell initialization failed: " + json.dumps(self.errors()))
			return state.get("ready") == "true" and state.get("busy") == "false"
		try:
			WebDriverWait(self.driver, timeout).until(ready)
		except BaseException:
			print("shell_readiness_errors=" + json.dumps(self.errors()), flush=True)
			raise
		return self

	def viewport(self, width, height):
		self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})

	def screenshot(self, name):
		name = str(name)
		if not name or Path(name).name != name or any(char in name for char in ('/', '\\', ':')) or name in (".", ".."):
			raise ValueError("Screenshot name must be a file name, not a path")
		path = SCREENSHOT_DIR / (name if name.endswith(".png") else name + ".png")
		path.parent.mkdir(parents=True, exist_ok=True)
		if not self.driver.save_screenshot(str(path)):
			raise RuntimeError("Screenshot was not written")
		return str(path)

	def tab_order(self, count):
		if not isinstance(count, int) or count < 0:
			raise ValueError("Tab count must be a non-negative integer")
		from selenium.webdriver.common.keys import Keys
		self.driver.execute_script("document.getElementById('view').focus()")
		focused = []
		for _ in range(count):
			self.driver.switch_to.active_element.send_keys(Keys.TAB)
			focused.append(self.driver.execute_script("""
				const node=document.activeElement;
				return node.id || node.getAttribute('aria-label') ||
					(node.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id=>document.getElementById(id)?.textContent || '').join(' ').trim() ||
					[...(node.labels || [])].map(label=>label.textContent).join(' ').trim() || node.textContent.trim() || node.tagName.toLowerCase();
			"""))
		return focused

	def errors(self):
		# A partial source locale legitimately fetches missing namespaces as 404.
		# Runtime errors, missing keys and every other failed resource remain red.
		return [row["message"] for row in self.driver.get_log("browser") if row["level"] == "SEVERE" and not ("/i18n/" in row["message"] and "404" in row["message"])]
