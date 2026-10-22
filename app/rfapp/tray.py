import ctypes
from datetime import datetime
import logging
import re
import threading

from PIL import Image, ImageDraw

from rfapp import i18n, notify, paths

_log = logging.getLogger("rfapp.tray")
_NIIF_RESPECT_QUIET_TIME = 0x80


def _dpi():
	return ctypes.windll.user32.GetDpiForSystem()


def _icon(*args, clicked=None, **kwargs):
	import pystray
	# The Windows backend omits balloon clicks, but keeps its dispatch hook overridable.
	class BalloonIcon(pystray.Icon):
		def _message(self, code, flags, **data):
			if data.get("szInfo"):
				# pystray forwards these fields to NOTIFYICONDATAW but exposes no public info flags.
				data["dwInfoFlags"] = data.get("dwInfoFlags", 0) | _NIIF_RESPECT_QUIET_TIME
			return super()._message(code, flags, **data)

		def _on_notify(self, wparam, lparam):
			if (lparam & 0xFFFF) == 1029 and clicked:
				threading.Thread(target=clicked, name="rfapp-balloon", daemon=True).start()
			else:
				super()._on_notify(wparam, lparam)
	return BalloonIcon(*args, **kwargs)


class Tray:
	def __init__(self, show, run_now, stop, quit, image=None):
		self._show = show
		self._run_now = run_now
		self._stop_run = stop
		self._quit = quit
		self._base = image.copy() if image is not None else None
		self._icon = None
		self._overview = {}
		self._warning = False
		self._lock = threading.RLock()
		self._ready = threading.Event()
		self._accent = None
		self._notification_run = None
		self._theme = None

	def _image(self):
		if self._base is None:
			with Image.open(paths.resource("ui", "img", "app-icon-256.png")) as source:
				self._base = source.convert("RGBA")
		size = 64 if _dpi() > 144 else 32
		image = self._base.resize((size, size), Image.Resampling.LANCZOS)
		draw = ImageDraw.Draw(image)
		if self._accent is None:
			text = paths.resource("ui", "css", "tokens.css").read_text(encoding="utf-8")
			from rfapp.window import _theme
			self._theme = _theme()
			theme_blocks = re.findall(r"\[data-theme\s*=\s*['\"]?" + self._theme + r"['\"]?\]\s*\{([^}]+)\}", text)
			match = re.search(r"--c-accent\s*:\s*([^;]+)", "\n".join(theme_blocks)) or re.search(r"--c-accent\s*:\s*([^;]+)", text)
			if not match:
				raise ValueError("Design tokens contain no --c-accent")
			self._accent = match.group(1).strip()
		accounts = [account for account in self._overview.get("accounts", []) if not account.get("paused")]
		states = [account.get("state") for account in accounts if account.get("state_is_today")]
		if accounts and len(states) == len(accounts) and all(state and state.get("earned_today") is not None and state.get("open_points") is not None and not state.get("unreadable") for state in states):
			earned = sum(state["earned_today"] for state in states)
			total = earned + sum(state["open_points"] for state in states)
			if total > 0:
				draw.arc((1, 1, size - 2, size - 2), -90, -90 + 360 * min(1, max(0, earned / total)), fill=self._accent, width=max(2, size // 12))
		if self._overview.get("active_run") or self._warning:
			color = self._accent if self._overview.get("active_run") else "#FFB74D"
			draw.ellipse((size * .68, size * .68, size - 1, size - 1), fill=color, outline="#FFFFFF", width=1)
		return image

	def _tooltip(self):
		t = i18n.current()
		if self._overview.get("active_run"):
			return t.t("tray.tooltip.running")[:127]
		accounts = [account for account in self._overview.get("accounts", []) if not account.get("paused")]
		states = [account.get("state") for account in accounts if account.get("state_is_today")]
		if accounts and len(states) == len(accounts) and all(state and state.get("earned_today") is not None and state.get("open_points") is not None and not state.get("unreadable") for state in states):
			earned = sum(state["earned_today"] for state in states)
			total = earned + sum(state["open_points"] for state in states)
			return t.t("tray.tooltip.today", earned=t.number(earned), total=total)[:127]
		return t.t("tray.tooltip.idle")[:127]

	def _menu(self):
		import pystray
		t = i18n.current()
		s = self._overview.get("schedule", {})
		if s.get("mode") == "manual" or not s.get("next_at"):
			next_text = t.t("tray.menu.manual")
		else:
			at = datetime.fromisoformat(s["next_at"].replace("Z", "+00:00")).astimezone()
			next_text = t.t("tray.menu.next_check" if s.get("next_is_check") else "tray.menu.next_run", time=t.time(at))
		active = bool(self._overview.get("active_run"))
		stoppable = bool((self._overview.get("active_run") or {}).get("supervisor_pid"))
		return pystray.Menu(
			pystray.MenuItem(t.t("tray.menu.open"), lambda: self._show(), default=True),
			pystray.MenuItem(t.t("tray.menu.stop") if stoppable else t.t("tray.menu.run_now"), lambda: self._stop_run() if stoppable else self._run_now(), enabled=not active or stoppable),
			pystray.Menu.SEPARATOR,
			pystray.MenuItem(next_text, None, enabled=False),
			pystray.Menu.SEPARATOR,
			pystray.MenuItem(t.t("tray.menu.quit"), lambda: self._quit()),
		)

	def start(self):
		with self._lock:
			if self._icon is not None:
				return
			self._icon = _icon("RewardsFarmerApp_" + paths.install_identity(), icon=self._image(), title=self._tooltip(), menu=self._menu(), clicked=self._notification_clicked)
			def setup(icon):
				icon.visible = True
				self._ready.set()
			self._icon.run_detached(setup=setup)
			if not self._ready.wait(5):
				self.stop()
				raise RuntimeError("Tray did not become ready")
			notify._set_tray(self)

	def _notification_clicked(self):
		if self._ready.is_set():
			self._show("#/history/" + self._notification_run if self._notification_run else "#/overview")

	def refresh(self):
		with self._lock:
			self._accent = None
			if self._icon:
				self._icon.icon = self._image()
				self._icon.title = self._tooltip()
				self._icon.menu = self._menu()
				self._icon.update_menu()

	def update(self, overview):
		with self._lock:
			if all(self._overview.get(key) == overview.get(key) for key in ("accounts", "active_run", "last_run", "schedule")):
				return
			self._overview = overview
			last = overview.get("last_run") or {}
			states = tuple((account.get("account_id"), notify._state_problem(account)) for account in overview.get("accounts", []) if notify._state_problem(account))
			if last.get("result") in notify._PROBLEMS or states:
				problem = (last.get("run_id"), last.get("result"), states)
				if problem != getattr(self, "_warning_problem", None):
					self._warning_problem = problem
					self._warning = True
			if self._icon:
				self._icon.icon = self._image()
				self._icon.title = self._tooltip()
				self._icon.menu = self._menu()
				self._icon.update_menu()

	def opened(self):
		with self._lock:
			self._warning = False
			if self._icon:
				self._icon.icon = self._image()

	def notify(self, title, body):
		with self._lock:
			if not self._icon or not self._ready.is_set():
				return False
			try:
				self._icon.notify(body, title)
				return True
			except Exception:
				_log.exception("Tray notification failed")
				return False

	def stop(self):
		with self._lock:
			self._ready.clear()
			notify._set_tray(None)
			if self._icon:
				self._icon.stop()
				self._icon = None
