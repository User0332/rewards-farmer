import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any
from selenium import webdriver

from constants import USER_DATA_DIR
import element_selectors


HISTORY_FILE = Path(__file__).parent.parent / "points_history.json"
SUMMARY_FILE = Path(__file__).parent.parent / "points_summary.txt"


class PointsTracker:
	"""Tracks points earned per run, balance history, and produces points_summary.txt."""

	def __init__(self, history_path: Path = HISTORY_FILE, summary_path: Path = SUMMARY_FILE):
		self.history_path = history_path
		self.summary_path = summary_path
		self.start_balance: Optional[int] = None
		self.end_balance: Optional[int] = None
		self.start_today_points: Optional[int] = None
		self.end_today_points: Optional[int] = None
		self.start_time: Optional[datetime] = None

	@staticmethod
	def calculate_points_earned(
		start_balance: Optional[int],
		end_balance: Optional[int],
		start_today_points: Optional[int],
		end_today_points: Optional[int],
	) -> int:
		"""Points earned during the run, for history/summary.

		The site's daily "today points" counter is the source of truth: the
		lifetime balance can lag or freeze on rewards.bing.com, so a balance
		delta of 0 while today's counter moved must still count as earnings.
		Falls back to the balance delta only when today's points are unreadable.
		"""
		if start_today_points is not None and end_today_points is not None:
			return max(0, end_today_points - start_today_points)
		if start_balance is not None and end_balance is not None:
			return max(0, end_balance - start_balance)
		if end_today_points is not None:
			return max(0, end_today_points)
		return 0

	def load_history(self) -> List[Dict[str, Any]]:
		if not self.history_path.exists():
			return []
		try:
			data = json.loads(self.history_path.read_text(encoding="utf-8"))
			if isinstance(data, list):
				return data
		except Exception as exc:
			print(f"[WARNING] Could not parse points history file: {exc}")
		return []

	def save_history(self, history: List[Dict[str, Any]]):
		try:
			self.history_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
		except Exception as exc:
			print(f"[WARNING] Could not write points history file: {exc}")

	def record_start(self, driver: webdriver.Edge) -> Optional[int]:
		"""Record initial points balance at start of run after page has hydrated."""
		self.start_time = datetime.now()
		elements = element_selectors.ElementSelectionUtils(driver)
		
		# Allow brief hydration retry
		for _ in range(3):
			self.start_balance = elements.get_total_points_balance()
			self.start_today_points = elements.get_today_points()
			if self.start_balance is not None:
				break
			time.sleep(1.0)

		if self.start_balance is not None:
			today_str = f" (Today's Points: {self.start_today_points} pts)" if self.start_today_points is not None else ""
			print(f"[INFO] Initial points balance: {self.start_balance:,} pts{today_str}")
		else:
			print("[INFO] Could not read starting points balance from page.")
		return self.start_balance

	def record_end(self, driver: webdriver.Edge) -> Dict[str, Any]:
		"""Record final points balance, compute diff, update history and write summary."""
		end_time = datetime.now()
		elements = element_selectors.ElementSelectionUtils(driver)
		
		# Allow generous hydration retry with refresh fallback
		for attempt in range(5):
			self.end_balance = elements.get_total_points_balance()
			self.end_today_points = elements.get_today_points()
			if self.end_balance is not None and self.end_today_points is not None:
				break
			if attempt == 2:
				try:
					driver.refresh()
					time.sleep(3.0)
				except Exception:
					pass
			time.sleep(1.5)

		history = self.load_history()

		# If start balance was missing, try to use last recorded balance
		if self.start_balance is None and history:
			self.start_balance = history[-1].get("end_balance")

		# If end balance is missing, fallback to start balance
		if self.end_balance is None:
			self.end_balance = self.start_balance

		earned = self.calculate_points_earned(
			start_balance=self.start_balance,
			end_balance=self.end_balance,
			start_today_points=self.start_today_points,
			end_today_points=self.end_today_points,
		)

		entry = {
			"timestamp": end_time.isoformat(timespec="seconds"),
			"date": end_time.strftime("%Y-%m-%d %H:%M:%S"),
			"start_balance": self.start_balance,
			"end_balance": self.end_balance,
			"today_points": self.end_today_points,
			"points_earned": earned,
		}

		history.append(entry)
		self.save_history(history)
		self.update_summary_file(history, current_entry=entry)
		self.print_summary_to_console(current_entry=entry, history=history)

		return entry

	def _find_closest_historical_balance(
		self,
		history: List[Dict[str, Any]],
		target_days_ago: int,
		reference_time: datetime,
	) -> Optional[Dict[str, Any]]:
		"""Find the recorded entry closest to `target_days_ago` in the past."""
		if not history:
			return None

		target_dt = reference_time - timedelta(days=target_days_ago)
		candidates = []

		for entry in history:
			ts_str = entry.get("timestamp") or entry.get("date")
			if not ts_str:
				continue
			try:
				dt = datetime.fromisoformat(ts_str) if "T" in ts_str else datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
			except Exception:
				continue

			# Must be at or before target date + 1 day tolerance
			if dt <= target_dt or (target_days_ago == 1 and dt.date() < reference_time.date()):
				candidates.append((dt, entry))

		if not candidates:
			return None

		# Pick the one closest to target_dt
		candidates.sort(key=lambda item: abs((item[0] - target_dt).total_seconds()))
		return candidates[0][1]

	def generate_summary_text(self, history: List[Dict[str, Any]], current_entry: Optional[Dict[str, Any]] = None) -> str:
		now = datetime.now()
		latest = current_entry or (history[-1] if history else {})
		current_balance = latest.get("end_balance") or 0
		today_pts = latest.get("today_points")
		earned_latest = latest.get("points_earned") or 0

		# Look up 1 day, 7 days, 30 days ago
		entry_1d = self._find_closest_historical_balance(history, target_days_ago=1, reference_time=now)
		entry_7d = self._find_closest_historical_balance(history, target_days_ago=7, reference_time=now)
		entry_30d = self._find_closest_historical_balance(history, target_days_ago=30, reference_time=now)
		first_entry = history[0] if history else None

		def format_comparison(entry: Optional[Dict[str, Any]], label: str) -> str:
			if not entry or entry.get("end_balance") is None:
				return f"  {label:<18} : No data recorded yet"
			past_pts = entry.get("end_balance", 0)
			past_date = (entry.get("date") or entry.get("timestamp") or "")[:10]
			diff = current_balance - past_pts
			diff_str = f"+{diff:,}" if diff >= 0 else f"{diff:,}"
			return f"  {label:<18} : {past_pts:>8,} pts  ({past_date}) -> {diff_str:>10} pts"

		lines = [
			"=" * 70,
			"                  MICROSOFT REWARDS POINTS SUMMARY",
			"=" * 70,
			f"  Last Updated : {now.strftime('%Y-%m-%d %H:%M:%S')}",
			"",
			"CURRENT BALANCE & RUN EARNINGS",
			"-" * 70,
			f"  Current Total Balance : {current_balance:,} points",
		]

		if today_pts is not None:
			lines.append(f"  Today's Points Earned : {today_pts:,} points")

		lines.extend([
			f"  Earned in Latest Run  : +{earned_latest:,} points",
			"",
			"HISTORICAL COMPARISONS",
			"-" * 70,
			format_comparison(entry_1d, "1 Day Ago"),
			format_comparison(entry_7d, "1 Week Ago (7d)"),
			format_comparison(entry_30d, "1 Month Ago (30d)"),
		])

		if first_entry and first_entry.get("start_balance") is not None:
			first_pts = first_entry.get("start_balance", 0)
			first_date = (first_entry.get("date") or first_entry.get("timestamp") or "")[:10]
			total_gained = current_balance - first_pts
			total_gained_str = f"+{total_gained:,}" if total_gained >= 0 else f"{total_gained:,}"
			lines.append(f"  {'All-Time Tracked':<18} : {first_pts:>8,} pts  ({first_date}) -> {total_gained_str:>10} pts gained")

		lines.extend([
			"",
			"RECENT RUN HISTORY (Last 10 Runs)",
			"-" * 70,
			f"  {'Date & Time':<20} | {'Start Pts':<11} | {'End Pts':<11} | {'Today Pts':<10} | {'Earned'}",
			"-" * 70,
		])

		for item in reversed(history[-10:]):
			dt_str = item.get("date") or item.get("timestamp") or "N/A"
			s_pts = f"{item.get('start_balance', 0):,}" if item.get('start_balance') is not None else "N/A"
			e_pts = f"{item.get('end_balance', 0):,}" if item.get('end_balance') is not None else "N/A"
			t_pts = f"{item.get('today_points', 0):,}" if item.get('today_points') is not None else "-"
			pts_earned = f"+{item.get('points_earned', 0):,}" if item.get('points_earned') is not None else "N/A"
			lines.append(f"  {dt_str:<20} | {s_pts:<11} | {e_pts:<11} | {t_pts:<10} | {pts_earned}")

		lines.extend([
			"=" * 70,
			""
		])

		return "\n".join(lines)

	def update_summary_file(self, history: List[Dict[str, Any]], current_entry: Optional[Dict[str, Any]] = None):
		text = self.generate_summary_text(history, current_entry)
		try:
			self.summary_path.write_text(text, encoding="utf-8")
			print(f"[INFO] Updated points summary file: {self.summary_path.name}")
		except Exception as exc:
			print(f"[WARNING] Could not write summary file {self.summary_path}: {exc}")

	def print_summary_to_console(self, current_entry: Dict[str, Any], history: List[Dict[str, Any]]):
		print("\n" + self.generate_summary_text(history, current_entry))
