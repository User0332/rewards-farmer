"""Windows Virtual Desktop utilities.

Allows running automated browser tasks on a separate Windows Virtual Desktop
in the background without interrupting the user's primary workflow.
"""

import logging
import math
import os
import sys
import time

logger = logging.getLogger(__name__)

# Virtual key codes for Windows desktop navigation
VK_LWIN = 0x5B
VK_CONTROL = 0x11
VK_D = 0x44
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_F4 = 0x73
KEYEVENTF_KEYUP = 0x0002

_desktop_created = False
_on_worker_desktop = False
_start_desktop_id: bytes | None = None
_worker_desktop_id: bytes | None = None
_hops_to_worker = 1


def is_windows() -> bool:
	"""Check if running on a Windows platform."""
	return sys.platform == "win32"


def is_virtual_desktop_enabled() -> bool:
	"""Fetch USE_VIRTUAL_DESKTOP from environment variables or constants, defaulting to False."""
	env_val = os.environ.get("USE_VIRTUAL_DESKTOP")
	if env_val is not None:
		return env_val.strip().lower() in ("1", "true", "yes")
	try:
		from constants import USE_VIRTUAL_DESKTOP
		return bool(USE_VIRTUAL_DESKTOP)
	except Exception:
		return False


def should_switch_back_to_main_desktop() -> bool:
	"""Fetch SWITCH_BACK_TO_MAIN_DESKTOP from environment variables or constants, defaulting to True."""
	env_val = os.environ.get("SWITCH_BACK_TO_MAIN_DESKTOP")
	if env_val is not None:
		return env_val.strip().lower() in ("1", "true", "yes")
	try:
		from constants import SWITCH_BACK_TO_MAIN_DESKTOP
		return bool(SWITCH_BACK_TO_MAIN_DESKTOP)
	except Exception:
		return True


def is_cleanup_virtual_desktop_enabled() -> bool:
	"""Fetch CLEANUP_VIRTUAL_DESKTOP from environment variables or constants, defaulting to True."""
	env_val = os.environ.get("CLEANUP_VIRTUAL_DESKTOP")
	if env_val is not None:
		return env_val.strip().lower() in ("1", "true", "yes")
	try:
		from constants import CLEANUP_VIRTUAL_DESKTOP
		return bool(CLEANUP_VIRTUAL_DESKTOP)
	except Exception:
		return True


def get_switch_back_delay() -> float:
	"""Fetch delay in seconds before switching back to main desktop (default: 1.5)."""
	val = os.environ.get("SWITCH_BACK_DELAY_SECONDS", "").strip()
	try:
		delay = float(val) if val else 1.5
	except ValueError:
		return 1.5

	if not math.isfinite(delay) or delay < 0:
		logger.warning("Invalid SWITCH_BACK_DELAY_SECONDS=%r; using 1.5 seconds.", val)
		return 1.5

	return delay


def get_desktop_registry_data() -> tuple[list[bytes], bytes | None]:
	"""Reads VirtualDesktopIDs (list of 16-byte GUIDs) and CurrentVirtualDesktop from Windows registry.

	Returns (desktop_ids_list, current_desktop_id).
	Returns ([], None) on failure or non-Windows platforms.
	"""
	if not is_windows():
		return [], None

	try:
		import winreg

		base_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops"
		with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base_path) as key:
			raw_ids, _ = winreg.QueryValueEx(key, "VirtualDesktopIDs")
	except Exception:
		return [], None

	if not raw_ids or len(raw_ids) % 16 != 0:
		return [], None

	desktops = [raw_ids[i:i + 16] for i in range(0, len(raw_ids), 16)]

	current_id = None
	session_base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\SessionInfo"
	try:
		with winreg.OpenKey(winreg.HKEY_CURRENT_USER, session_base) as s_key:
			sub_count = winreg.QueryInfoKey(s_key)[0]
			for j in range(sub_count):
				sess_name = winreg.EnumKey(s_key, j)
				sess_vd = f"{session_base}\\{sess_name}\\VirtualDesktops"
				try:
					with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sess_vd) as vd_key:
						cid, _ = winreg.QueryValueEx(vd_key, "CurrentVirtualDesktop")
						if cid and len(cid) == 16:
							current_id = cid
							break
				except OSError:
					pass
	except Exception:
		pass

	if not current_id:
		try:
			with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base_path) as key:
				current_id, _ = winreg.QueryValueEx(key, "CurrentVirtualDesktop")
		except Exception:
			pass

	return desktops, current_id


def get_desktop_state() -> tuple[int, int]:
	"""Returns (current_desktop_index, total_desktops) using Windows registry.

	current_desktop_index is 0-indexed.
	Defaults to (0, 1) if undetected or on non-Windows platforms.
	"""
	desktops, current_id = get_desktop_registry_data()
	total = len(desktops)
	if total == 0:
		return 0, 1

	if current_id and current_id in desktops:
		return desktops.index(current_id), total

	return 0, total


def reset_virtual_desktop_state() -> None:
	"""Reset state for tracking desktop creation (useful for tests or multiple runs)."""
	global _desktop_created, _on_worker_desktop, _start_desktop_id, _worker_desktop_id, _hops_to_worker
	_desktop_created = False
	_on_worker_desktop = False
	_start_desktop_id = None
	_worker_desktop_id = None
	_hops_to_worker = 1


def press_hotkey(*keys) -> bool:
	"""Simulate pressing and releasing a sequence of hotkeys using Win32 API.

	Guarantees that all pressed modifier keys are released in reverse order
	even if an error occurs while pressing keys down.
	"""
	if not is_windows():
		logger.warning("Virtual desktop shortcuts are only supported on Windows.")
		return False

	pressed = []
	success = True
	try:
		import ctypes

		for key in keys:
			ctypes.windll.user32.keybd_event(key, 0, 0, 0)
			pressed.append(key)
		time.sleep(0.05)
	except Exception as exc:
		logger.error("Failed to simulate Windows virtual desktop hotkey: %s", exc)
		success = False
	finally:
		for key in reversed(pressed):
			try:
				import ctypes

				ctypes.windll.user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
			except Exception as exc:
				logger.error("Failed to release Windows key %s: %s", key, exc)
				success = False
		time.sleep(0.3)

	return success


def create_virtual_desktop() -> bool:
	"""Creates a new Windows Virtual Desktop and switches to it. Returns True on success."""
	if not is_windows():
		logger.warning("Virtual desktop creation is only supported on Windows.")
		return False
	if not press_hotkey(VK_LWIN, VK_CONTROL, VK_D):
		return False
	time.sleep(0.5)
	return True


def switch_to_left_desktop() -> bool:
	"""Switches to the previous (left) Virtual Desktop. Returns True on success."""
	if not is_windows():
		return False
	return press_hotkey(VK_LWIN, VK_CONTROL, VK_LEFT)


def switch_to_right_desktop() -> bool:
	"""Switches to the next (right) Virtual Desktop. Returns True on success."""
	if not is_windows():
		return False
	return press_hotkey(VK_LWIN, VK_CONTROL, VK_RIGHT)


def _navigate_to_guid(target_id: bytes) -> bool:
	"""Switches to target GUID by inspecting real-time registry desktop order and active desktop."""
	if not is_windows():
		return False

	desktops, current_id = get_desktop_registry_data()
	if not desktops or not current_id or current_id not in desktops or target_id not in desktops:
		return False

	current_idx = desktops.index(current_id)
	target_idx = desktops.index(target_id)
	delta = target_idx - current_idx

	if delta == 0:
		logger.debug("Already on target desktop (index %d).", target_idx)
		return True

	success = True
	if delta > 0:
		logger.debug("Moving right %d hops (from %d to %d)", delta, current_idx, target_idx)
		for _ in range(delta):
			if not switch_to_right_desktop():
				success = False
			time.sleep(0.1)
	else:
		hops = abs(delta)
		logger.debug("Moving left %d hops (from %d to %d)", hops, current_idx, target_idx)
		for _ in range(hops):
			if not switch_to_left_desktop():
				success = False
			time.sleep(0.1)

	return success


def switch_to_worker_desktop() -> bool:
	"""Switches to the worker desktop using dynamic GUID tracking if available, or hops fallback."""
	if not is_windows():
		return False
	if _worker_desktop_id and _start_desktop_id and _worker_desktop_id != _start_desktop_id:
		if _navigate_to_guid(_worker_desktop_id):
			return True
	success = True
	for _ in range(_hops_to_worker):
		if not switch_to_right_desktop():
			success = False
		time.sleep(0.1)
	return success


def switch_to_main_desktop() -> bool:
	"""Switches to the starting main desktop using dynamic GUID tracking if available, or hops fallback."""
	if not is_windows():
		return False
	if _worker_desktop_id and _start_desktop_id and _worker_desktop_id != _start_desktop_id:
		if _navigate_to_guid(_start_desktop_id):
			return True
	success = True
	for _ in range(_hops_to_worker):
		if not switch_to_left_desktop():
			success = False
		time.sleep(0.1)
	return success


def switch_to_target_desktop(target_id: bytes | None) -> bool:
	"""Switches to the specified virtual desktop GUID if distinct, else falls back."""
	if not is_windows():
		return False
	if target_id and _navigate_to_guid(target_id):
		return True
	if target_id == _start_desktop_id:
		return switch_to_main_desktop()
	return switch_to_worker_desktop()


def close_current_virtual_desktop() -> bool:
	"""Closes the current Virtual Desktop via Win+Ctrl+F4. Returns True on success."""
	if not is_windows():
		return False
	return press_hotkey(VK_LWIN, VK_CONTROL, VK_F4)


def prepare_desktop_before_launch() -> bool:
	"""Prepares virtual desktop before launching the browser. Returns True on success."""
	global _desktop_created, _on_worker_desktop, _start_desktop_id, _worker_desktop_id, _hops_to_worker
	if not is_windows() or not is_virtual_desktop_enabled():
		return True

	if not _desktop_created:
		logger.info("Creating and switching to a new Windows Virtual Desktop...")
		ids_before, _start_desktop_id = get_desktop_registry_data()
		start_idx, total_before = get_desktop_state()

		if not create_virtual_desktop():
			logger.error("Could not create the Windows Virtual Desktop.")
			return False
		_desktop_created = True
		_on_worker_desktop = True

		time.sleep(0.2)
		ids_after, cur_after = get_desktop_registry_data()
		new_ids = [x for x in ids_after if x not in ids_before]
		if new_ids:
			_worker_desktop_id = new_ids[0]
		else:
			_worker_desktop_id = cur_after

		_, total_after = get_desktop_state()
		worker_idx = total_after - 1 if total_after > total_before else total_before
		_hops_to_worker = max(1, worker_idx - start_idx)
		logger.debug(
			"Desktop navigation: starting from index %d, worker at %d (hops: %d)",
			start_idx,
			worker_idx,
			_hops_to_worker,
		)
	elif not _on_worker_desktop:
		logger.info("Switching to Windows Virtual Desktop for next account...")
		if not switch_to_worker_desktop():
			logger.error("Could not switch to the Windows Virtual Desktop.")
			return False
		_on_worker_desktop = True

	return True


def switch_back_after_launch() -> None:
	"""Switches back to the primary desktop after browser launch if configured."""
	global _on_worker_desktop
	if not is_windows() or not is_virtual_desktop_enabled():
		return

	if should_switch_back_to_main_desktop() and _on_worker_desktop:
		delay = get_switch_back_delay()
		time.sleep(delay)  # Give the browser window a moment to attach to the new desktop
		logger.info("Switching back to main desktop. Script is running in the background...")
		if switch_to_main_desktop():
			_on_worker_desktop = False
		else:
			logger.error("Could not switch back to the original Windows desktop.")
	elif not should_switch_back_to_main_desktop() and _on_worker_desktop:
		logger.info("SWITCH_BACK_TO_MAIN_DESKTOP is disabled. Remaining on the virtual desktop.")


def cleanup_virtual_desktop() -> bool:
	"""Closes the created worker virtual desktop and returns to the starting main desktop."""
	global _desktop_created, _on_worker_desktop, _start_desktop_id, _worker_desktop_id
	if not is_windows() or not is_virtual_desktop_enabled() or not _desktop_created:
		return True

	if not is_cleanup_virtual_desktop_enabled():
		logger.info("CLEANUP_VIRTUAL_DESKTOP is disabled. Leaving worker desktop open.")
		return True

	logger.info("Cleaning up Windows Virtual Desktop...")

	desktops, _ = get_desktop_registry_data()
	# If the worker desktop was already closed manually by the user in Task View:
	if _worker_desktop_id and desktops and _worker_desktop_id not in desktops:
		logger.info("Worker desktop was already closed. Navigating back to starting desktop...")
		if _start_desktop_id and _start_desktop_id != _worker_desktop_id:
			_navigate_to_guid(_start_desktop_id)
		_desktop_created = False
		_on_worker_desktop = False
		return True

	# Step 1: Ensure we are on the worker desktop
	if not switch_to_worker_desktop():
		logger.error("Failed to switch to worker desktop for cleanup.")
		return False
	_on_worker_desktop = True
	time.sleep(0.3)

	# Step 2: Close the worker desktop via Win+Ctrl+F4
	if not close_current_virtual_desktop():
		logger.error("Failed to close virtual desktop.")
		return False
	time.sleep(0.5)

	# Step 3: Return to starting desktop
	navigated = False
	if _start_desktop_id and _worker_desktop_id and _start_desktop_id != _worker_desktop_id:
		navigated = _navigate_to_guid(_start_desktop_id)

	if not navigated:
		remaining_left_hops = max(0, _hops_to_worker - 1)
		for _ in range(remaining_left_hops):
			switch_to_left_desktop()
			time.sleep(0.1)

	_desktop_created = False
	_on_worker_desktop = False
	logger.info("Worker desktop closed and returned to main desktop.")
	return True
