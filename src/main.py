import sys
import time
import argparse
from typing import Dict
from browser_lifecycle import BrowserManager
import rewards_tasks
import llm_utils
import points_tracker
from constants import USER_DATA_DIR, PROFILE_NAME, DEFAULT_MODEL


def critical_tasks_succeeded(task_status: Dict[str, str]) -> bool:
	"""True only if every gate task ran ('ok') in this run.

	'skipped' counts as failure for critical tasks: silently missing required
	searches would under-earn points without anyone noticing.
	"""
	return all(status == "ok" for status in task_status.values())


def main():
	parser = argparse.ArgumentParser(description="Automated Microsoft Rewards Farmer with Anti-Detection & LLM Engine")
	parser.add_argument("--headless", action="store_true", help="Run Edge in headless mode")
	parser.add_argument("--profile", default=PROFILE_NAME, help=f"Browser profile directory name (default: {PROFILE_NAME})")
	parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Preferred Ollama model (default: {DEFAULT_MODEL})")
	parser.add_argument("--no-cooldown", action="store_true", help="Disable the 15-minute search cooldown loop (default: cooldown enabled)")
	parser.add_argument("--no-mobile", action="store_true", help="Disable Edge Mobile search emulation (default: mobile enabled to farm all points)")
	parser.add_argument("--debug-cursor", action="store_true", help="Render red tracking cursor (for local visual debugging only)")
	args = parser.parse_args()

	print("=" * 60)
	print("  Microsoft Rewards Farmer - Natural Human Emulation")
	print("=" * 60)

	# 1. Pre-flight Ollama LLM check
	print("\n[PRE-FLIGHT] Checking Ollama LLM service...")
	resolved_model, is_online = llm_utils.resolve_available_model(args.model)
	if is_online:
		print(f"[PRE-FLIGHT] Ollama engine ready with model: {resolved_model}")
	else:
		print(f"[PRE-FLIGHT] Ollama offline or unavailable. Operating with intelligent offline query synthesizer.")

	# 2. Managed Browser Session with Singleton Lock Protection & Signal Interception
	print("\n[PRE-FLIGHT] Initializing isolated browser session...")
	with BrowserManager(user_data_dir=USER_DATA_DIR, profile_name=args.profile, headless=args.headless) as driver:
		print("[INFO] Browser session started successfully.")
		
		# 3. Initialize Rewards UI (navigates to rewards.bing.com/earn and waits for focus/hydration)
		rewards = rewards_tasks.RewardsTaskUtils(
			driver,
			debug_cursor=args.debug_cursor,
			enable_cooldown=not args.no_cooldown,
			enable_mobile=not args.no_mobile
		)

		# 4. Record starting points balance and today's points once the page is loaded
		tracker = points_tracker.PointsTracker()
		tracker.record_start(driver)

		# 5. Execute all daily rewards tasks (Daily set, PC searches, Mobile searches, Misc cards, Visual search, Bonus)
		task_status = rewards.complete_all_tasks()

		# 6. Switch back to earn page, allow generous refresh for Bing telemetry to register points, and record balance
		rewards.switch_to_earn_page(refresh_wait=6.0)
		time.sleep(4.0)
		tracker.record_end(driver)

	if critical_tasks_succeeded(task_status):
		print("\n[DONE] All scheduled daily tasks finished.")
	else:
		print("\n[CRITICAL] A critical task did not complete successfully.")
		sys.exit(1)


if __name__ == "__main__":
	try:
		main()
	except KeyboardInterrupt:
		print("\n[INFO] Run interrupted by user (KeyboardInterrupt). Exiting cleanly.")
		sys.exit(130)
	except Exception as exc:
		print(f"\n[FATAL] Unhandled error during execution: {exc}")
		sys.exit(1)