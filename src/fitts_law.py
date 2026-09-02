"""Measure Fitts-law constants a and b from repeated target-click trials.

The user starts at a fixed home point, moves to a randomly generated 2D
rectangular target, and clicks it. The movement time begins when the cursor
moves more than a few pixels from the home position and ends when the click is
received. For each trial, the target width term is the average of the target's
height and width: W = (H + W) / 2.

The regression is performed as:
	MT = a + b * ID
where:
	ID = log2(2D / W)
with D as the distance from the home point to the target center.
"""

from __future__ import annotations

import ctypes
import math
import random
import time
from dataclasses import dataclass
from typing import List, Sequence, Tuple
import tkinter as tk


@dataclass
class Trial:
	distance: float
	target_width: float
	target_height: float
	width_term: float
	index_of_difficulty: float
	movement_time: float


def get_screen_size() -> Tuple[int, int]:
	if hasattr(ctypes, "windll"):
		user32 = ctypes.windll.user32
		return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
	try:
		root = tk.Tk()
		root.withdraw()
		w, h = root.winfo_screenwidth(), root.winfo_screenheight()
		root.destroy()
		return w, h
	except Exception:
		return 1920, 1080


def set_cursor_position(x: int, y: int) -> None:
	if hasattr(ctypes, "windll"):
		ctypes.windll.user32.SetCursorPos(int(x), int(y))


def point_in_rectangle(px: float, py: float, x: float, y: float, w: float, h: float) -> bool:
	return x <= px <= x + w and y <= py <= y + h


def generate_target(start: Tuple[int, int], screen_w: int, screen_h: int) -> Tuple[float, float, float, float, float]:
	margin = 80
	for _ in range(1000):
		target_w = random.randint(30, 180)
		target_h = random.randint(30, 180)
		x = random.randint(margin, max(margin, screen_w - target_w - margin))
		y = random.randint(margin, max(margin, screen_h - target_h - margin))
		center_x = x + target_w / 2
		center_y = y + target_h / 2
		distance = math.hypot(center_x - start[0], center_y - start[1])
		if distance < 100:
			continue
		return x, y, target_w, target_h, distance

	# Fallback if the random search fails.
	target_w = 120
	target_h = 80
	x = screen_w * 0.75
	y = screen_h * 0.35
	return x, y, target_w, target_h, math.hypot(x + target_w / 2 - start[0], y + target_h / 2 - start[1])


def run_single_trial(start: Tuple[int, int], target_x: float, target_y: float, target_w: float, target_h: float, distance: float, trial_no: int, total_trials: int) -> float:
	screen_w, screen_h = get_screen_size()
	root = tk.Tk()
	root.title("Fitts Law Calibration")
	root.attributes("-fullscreen", True)
	root.attributes("-topmost", True)
	root.configure(bg="#f3f3f3")

	canvas = tk.Canvas(root, width=screen_w, height=screen_h, bg="#f3f3f3", highlightthickness=0)
	canvas.pack(fill="both", expand=True)

	start_x, start_y = start
	start_marker = canvas.create_oval(start_x - 14, start_y - 14, start_x + 14, start_y + 14, fill="#1f1f1f")
	target_id = canvas.create_rectangle(
		target_x,
		target_y,
		target_x + target_w,
		target_y + target_h,
		fill="#5c8dff",
		outline="#0d2d73",
		width=3,
	)

	canvas.create_text(
		screen_w // 2,
		48,
		text=f"Trial {trial_no}/{total_trials}: move from the center to the blue rectangle and click it.",
		font=("Segoe UI", 18),
		fill="#111111",
	)

	set_cursor_position(start_x, start_y)
	root.update_idletasks()
	root.update()

	trial_result = {"movement_time": None}
	movement_started = {"value": False}
	movement_start_time = {"value": 0.0}

	def on_motion(event):
		if not movement_started["value"]:
			dx = abs(event.x_root - start_x)
			dy = abs(event.y_root - start_y)
			if max(dx, dy) > 3:
				movement_started["value"] = True
				movement_start_time["value"] = time.perf_counter()

	def on_click(event):
		if not movement_started["value"]:
			return

		if point_in_rectangle(event.x_root, event.y_root, target_x, target_y, target_w, target_h):
			trial_result["movement_time"] = time.perf_counter() - movement_start_time["value"]
			root.quit()
			root.destroy()
			return

		canvas.create_text(
			screen_w // 2,
			90,
			text="Missed the target. Click the blue rectangle only.",
			fill="#d32f2f",
			font=("Segoe UI", 16),
		)
		canvas.update()

	root.bind("<Motion>", on_motion)
	root.bind("<ButtonPress-1>", on_click)
	root.bind("<Escape>", lambda _: (root.destroy(), raise_system_exit()))

	root.mainloop()

	if trial_result["movement_time"] is None:
		raise RuntimeError("Trial ended without a valid target click.")

	return trial_result["movement_time"]


def raise_system_exit():
	raise SystemExit


def fit_fitts_law(trials: Sequence[Trial]) -> Tuple[float, float, float]:
	if not trials:
		raise ValueError("At least one trial is required.")

	x_values = [trial.index_of_difficulty for trial in trials]
	y_values = [trial.movement_time for trial in trials]

	x_mean = sum(x_values) / len(x_values)
	y_mean = sum(y_values) / len(y_values)

	numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
	denominator = sum((x - x_mean) ** 2 for x in x_values)

	if denominator == 0:
		raise ValueError("Index of difficulty did not vary across trials.")

	b = numerator / denominator
	a = y_mean - b * x_mean

	rss = sum((y - (a + b * x)) ** 2 for x, y in zip(x_values, y_values))
	tss = sum((y - y_mean) ** 2 for y in y_values)
	r_squared = 1.0 if tss == 0 else 1.0 - (rss / tss)

	return a, b, r_squared


def collect_trials(trial_count: int = 15) -> List[Trial]:
	screen_w, screen_h = get_screen_size()
	start = (screen_w // 2, screen_h // 2)
	trials: List[Trial] = []

	for trial_no in range(1, trial_count + 1):
		target_x, target_y, target_w, target_h, distance = generate_target(start, screen_w, screen_h)
		movement_time = run_single_trial(start, target_x, target_y, target_w, target_h, distance, trial_no, trial_count)

		width_term = (target_w + target_h) / 2.0
		if width_term <= 0:
			raise ValueError("Target width must be greater than zero.")

		index_of_difficulty = math.log2((2.0 * distance) / width_term)
		trials.append(
			Trial(
				distance=distance,
				target_width=target_w,
				target_height=target_h,
				width_term=width_term,
				index_of_difficulty=index_of_difficulty,
				movement_time=movement_time,
			)
		)

	return trials


def main() -> None:
	try:
		trials = collect_trials(trial_count=18)
		a, b, r_squared = fit_fitts_law(trials)

		print("Fitts Law calibration results")
		print("=" * 40)
		print(f"Target width term W = (height + width) / 2")
		print(f"Regression: MT = {a:.4f} + {b:.4f} * ID")
		print(f"R^2 = {r_squared:.4f}")
		print("\nSample trials:")
		for trial in trials:
			print(
				f"  D={trial.distance:.1f}px, W={trial.width_term:.1f}px, "
				f"ID={trial.index_of_difficulty:.3f}, MT={trial.movement_time:.3f}s"
			)

		result_window = tk.Tk()
		result_window.title("Fitts Law Estimate")
		result_window.geometry("500x180")
		result_window.configure(bg="#ffffff")
		label = tk.Label(
			result_window,
			text=(
				f"Estimated model:\nMT = {a:.4f} + {b:.4f} * ID\n"
				f"R^2 = {r_squared:.4f}\n\n"
			),
			font=("Segoe UI", 14),
			bg="#ffffff",
			justify="left",
			padx=20,
			pady=20,
		)
		label.pack(fill="both", expand=True)
		result_window.mainloop()
	except SystemExit:
		pass
	except Exception as exc:  # pragma: no cover - message shown in console for user feedback.
		print(f"An error occurred: {exc}")
		raise


if __name__ == "__main__":
	main()
