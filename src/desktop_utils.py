import os
import time
import ctypes

VK_LWIN = 0x5B
VK_CONTROL = 0x11
VK_D = 0x44
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_F4 = 0x73
KEYEVENTF_KEYUP = 0x0002

def press_hotkey(*keys):
	for key in keys:
		ctypes.windll.user32.keybd_event(key, 0, 0, 0)
	time.sleep(0.05)
	for key in reversed(keys):
		ctypes.windll.user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
	time.sleep(0.3)

def create_virtual_desktop():
	"""Creates a new Windows Virtual Desktop and switches to it."""
	press_hotkey(VK_LWIN, VK_CONTROL, VK_D)
	time.sleep(0.5)

def switch_to_left_desktop():
	"""Switches to the previous (left) Virtual Desktop."""
	press_hotkey(VK_LWIN, VK_CONTROL, VK_LEFT)
	time.sleep(0.3)

def switch_to_right_desktop():
	"""Switches to the next (right) Virtual Desktop."""
	press_hotkey(VK_LWIN, VK_CONTROL, VK_RIGHT)
	time.sleep(0.3)

def close_current_virtual_desktop():
	"""Closes the current Virtual Desktop."""
	press_hotkey(VK_LWIN, VK_CONTROL, VK_F4)
	time.sleep(0.3)
