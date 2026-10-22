import os
import io
from pathlib import Path
import time
from accounts import Account, directory_name_is_valid
from browser import start_driver
import sys
import dotenv
from file_replace import read_file
from typing import Literal, TextIO
from argparse import ArgumentParser

def one_of_with_default(prompt: str, options: list[str], default: str) -> str:
	while True:
		user_input = input(f"{prompt} ({'/'.join(options)}) [Default: {default}]: ").strip() or default

		if user_input in options:
			return user_input
		else:
			print(f"Invalid choice.")

def positive_integer_with_default(prompt: str, default: int) -> int:
	while True:
		user_input = input(f"{prompt} [Default: {default}]: ").strip() or str(default)

		try:
			value = int(user_input)
			if value > 0:
				return value
			else:
				print("Please enter a positive integer.")
		except ValueError:
			print("Invalid input. Please enter a positive integer.")

def boolean_with_default(prompt: str, default: bool) -> bool:
	while True:
		default_str = "yes" if default else "no"
		user_input = input(f"{prompt} (yes/no) [Default: {default_str}]: ").strip().lower() or default_str

		if user_input in ("yes", "y"):
			return True
		elif user_input in ("no", "n"):
			return False
		else:
			print("Invalid input. Please enter 'yes' or 'no'.")

def nonempty_input(prompt: str) -> str:
	while True:
		user_input = input(prompt).strip()
		if user_input:
			return user_input
		else:
			print("Input cannot be empty. Please try again.")

def path_input(prompt: str, default: Path, create: Literal["dir", "file", "none"]="none") -> Path:
	while True:
		path = default

		if user_input := input(f"{prompt} [Default: {default}]: ").strip():
			path = Path(user_input)

		if create == "dir":
			try:
				path.mkdir(parents=True, exist_ok=True)
				return path
			except OSError as e:
				print(f"Invalid directory '{path}': {e}")
				continue
		elif create == "file":
			try:
				path.parent.mkdir(parents=True, exist_ok=True)
				path.touch(exist_ok=True)
				return path
			except OSError as e:
				print(f"Invalid file '{path}': {e}")
				continue
		elif path.exists():
			return path
		else:
			print(f"Path '{path}' does not exist. Please enter a valid path.")

def default_unset_path_input(prompt: str, create: Literal["dir", "file", "none"]="none") -> Path | None:
	while True:
		if user_input := input(f"{prompt} (default: unset): ").strip():
			path = Path(user_input)
		else: return None

		if create == "dir":
			try:
				path.mkdir(parents=True, exist_ok=True)
				return path
			except OSError as e:
				print(f"Invalid directory '{path}': {e}")
				continue
		elif create == "file":
			try:
				path.parent.mkdir(parents=True, exist_ok=True)
				path.touch(exist_ok=True)
				return path
			except OSError as e:
				print(f"Invalid file '{path}': {e}")
				continue
		elif path.exists():
			return path
		else:
			print(f"Path '{path}' does not exist. Please enter a valid path.")

def configure_multi_account(f: TextIO):
	root_data_dir = path_input("Enter the root data directory for accounts", Path(__file__).parent.parent / "data-dir", create="dir")

	f.write(f"USER_DATA_DIR={root_data_dir.resolve()}\n")

	number_of_accounts = positive_integer_with_default("Enter the number of accounts to configure", 1)

	accounts = []

	for i in range(1, number_of_accounts + 1):
		print(f"\nConfiguring account {i}:")

		while True:
			data_dir_name = nonempty_input("Enter an account name: ").strip()

			if data_dir_name in accounts:
				print(f"Account name '{data_dir_name}' is already used. Please choose a different name.")
				continue

			if directory_name_is_valid(data_dir_name): break

			print(f"Invalid account name '{data_dir_name}'. Please use letters, digits, dot, dash or underscore, and do not end in a dot. Do not use spaces.")

		accounts.append(data_dir_name)

		if boolean_with_default("Would you like to sign in to this profile now?", True):
			print(f"Launching browser for account '{data_dir_name}' for sign-in")

			driver = start_driver(
				Account(
					data_dir_name,
					user_data_dir=root_data_dir / data_dir_name,
					profile_name="Default",
				)
			)

			if not driver:
				print("Failed to start the browser for sign-in. Please ensure msedgedriver.exe and msedge.exe are correctly set up.")
				continue

			print("Close the browser window after signing in to the account. The script will wait until the browser is closed before proceeding.")

			while True:
				try:
					driver.title
					time.sleep(0.5)
				except Exception: break


	f.write(f"REWARDS_ACCOUNTS={','.join(accounts)}\n")

def configure_variables(f: TextIO):
	search_backend = one_of_with_default("Which search backend would you like to use?", ["trends", "llm"], "trends")

	if search_backend == "trends":
		f.write("QUERY_SOURCE=trends\n")

	else:
		f.write("QUERY_SOURCE=llm\n")

		llm_setup_type = one_of_with_default("Which LLM setup would you like to use? (Select 'local' for custom endpoints)", ["openrouter", "local"], "openrouter")

		if llm_setup_type == "openrouter":
			f.write("LLM_PROVIDER=openrouter\n")
			llm_api_key = input("Enter your OpenRouter API key: ").strip()
			llm_model = input("Enter the model name (default: openrouter/free): ").strip() or "openrouter/free"

			f.write(f"OPENROUTER_API_KEY={llm_api_key}\n")
			f.write(f"OPENROUTER_MODEL={llm_model}\n")
		else:
			f.write("LLM_PROVIDER=local\n")
			local_llm_base_url = input("Enter the custom endpoint base URL (default: http://localhost:11434/v1): ").strip() or "http://localhost:11434/v1"
			local_llm_model = input("Enter the model name (default: gemma4:cloud): ").strip() or "gemma4:cloud"
			local_llm_api_key = input("Enter the custom endpoint LLM API key (if required, otherwise leave blank): ").strip()

			f.write(f"LOCAL_LLM_BASE_URL={local_llm_base_url}\n")
			f.write(f"LOCAL_LLM_MODEL={local_llm_model}\n")
			f.write(f"LOCAL_LLM_API_KEY={local_llm_api_key}\n")


		llm_request_timeout_int = positive_integer_with_default("Enter the LLM request timeout in seconds", 60)

		f.write(f"LLM_REQUEST_TIMEOUT_SECONDS={llm_request_timeout_int}\n")

	headless = boolean_with_default("Do you want to run the browser in headless mode?", False)

	f.write(f"REWARDS_HEADLESS={str(headless).lower()}\n")

	print()

	# We need to set up the driver and browser paths before configuring accounts, as the user may want to sign in to each account during configuration.
	custom_paths = boolean_with_default("Do you want to specify custom paths for msedgedriver.exe and msedge.exe?", False)

	if custom_paths:
		msedgedriver_path = default_unset_path_input("Enter custom path to msedgedriver.exe")

		if msedgedriver_path:
			f.write(f"MSEDGEDRIVER_PATH={msedgedriver_path.resolve()}\n")
			os.environ["MSEDGEDRIVER_PATH"] = str(msedgedriver_path.resolve()) # set within this process so we can launch the driver for sign-in during account configuration

		edge_binary_path = default_unset_path_input("Enter custom path to msedge.exe")

		if edge_binary_path:
			f.write(f"EDGE_BINARY={edge_binary_path.resolve()}\n")
			os.environ["EDGE_BINARY"] = str(edge_binary_path.resolve()) # set within this process so we can launch the browser for sign-in during account configuration

	print()

	configure_multi_account(f)

	print()

	setup_logging = boolean_with_default("Do you want to set up logging for the driver and Rewards Farmer?", False)

	if setup_logging:
		driver_log_path = default_unset_path_input("Enter path for driver log file", create="file")

		if driver_log_path:
			f.write(f"REWARDS_DRIVER_LOG={driver_log_path.resolve()}\n")

		farmer_log_file = default_unset_path_input("Enter path for Rewards Farmer log file", create="file")

		if farmer_log_file:
			f.write(f"REWARDS_FARMER_LOG_FILE={farmer_log_file.resolve()}\n")

		farmer_log_level = one_of_with_default("Enter the desired log level for Rewards Farmer", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], "INFO")
		f.write(f"REWARDS_FARMER_LOG_LEVEL={farmer_log_level}\n")

	print()

	if sys.platform.startswith("win"):
		print("Windows-specific configuration options:")

		create_virtual_desktop = boolean_with_default("Do you want the script to automatically run the browser on a separate virtual desktop?", False)

		f.write(f"USE_VIRTUAL_DESKTOP={str(create_virtual_desktop).lower()}\n")

		if create_virtual_desktop:
			switch_back_to_main_desktop = boolean_with_default("Do you want to switch back to the main desktop after launching the browser?", True)
			f.write(f"SWITCH_BACK_TO_MAIN_DESKTOP={str(switch_back_to_main_desktop).lower()}\n")

			if switch_back_to_main_desktop:
				switch_back_delay_seconds = positive_integer_with_default("Enter the delay in seconds before switching back to the main desktop", 1)
				f.write(f"SWITCH_BACK_DELAY_SECONDS={switch_back_delay_seconds}\n")

			cleanup_virtual_desktop = boolean_with_default("Do you want to clean up (close) the extra virtual desktop after the script finishes?", True)
			f.write(f"CLEANUP_VIRTUAL_DESKTOP={str(cleanup_virtual_desktop).lower()}\n")

	print("\nConfiguration finished!\nYou are ready to run rewards-farmer with the new configuration.\n\nYou may edit these settings at any time by modifying the .env file directly.")

def main():
	print("Welcome to the Rewards Farmer Configuration Script!")
	print("This script will help you set up the necessary configuration for running the program.\n")

	path_to_dotenv = path_input(f"Path to .env file: ", Path(__file__).parent.parent / ".env", create="file")

	print("WARNING: This script will overwrite any existing configuration in the .env file.")

	cont = boolean_with_default("Do you want to continue?", True)

	if not cont:
		print("Exiting without making changes.")
		return

	try:
		with open(path_to_dotenv, "w") as f:
			configure_variables(f)
	except OSError as e:
		print(f"Error creating or clearing the .env file: {e}")

def _env_key(path, key):
	try:
		text = read_file(path)
	except FileNotFoundError:
		text = ""
	return dotenv.dotenv_values(stream=io.StringIO(text)).get(key)


def manage_accounts():
	print("Add, remove, or modify accounts without changing other configuration settings here.\n")

	path_to_dotenv = path_input(f"Path to .env file: ", Path(__file__).parent.parent / ".env", create="file")

	# we could use dotenv.get_key to read REWARDS_ACCOUNTS, but loading the .env file ensures that
	# sign-in works correctly if the user configured custom paths for the driver and browser.
	try:
		text = read_file(path_to_dotenv)
	except FileNotFoundError:
		text = ""
	dotenv.load_dotenv(stream=io.StringIO(text))

	current_accounts_str = os.environ.get("REWARDS_ACCOUNTS", "")
	current_accounts = [acc.strip() for acc in current_accounts_str.split(",")] if current_accounts_str else []

	if not current_accounts:
		print("No accounts are currently configured.")
	else:
		print(f"Current accounts ({len(current_accounts)}): {', '.join(current_accounts)}")

	while 1:
		opt = one_of_with_default("Would you like to add, remove, or modify accounts?", ["add", "remove", "signin", "exit"], "exit")

		if opt == "add":
			n = positive_integer_with_default("How many accounts would you like to add?", 1)

			for i in range(n):
				print(f"\nAdding account {i + 1} of {n}:")

				while True:
					data_dir_name = nonempty_input("Enter an account name: ").strip()

					if data_dir_name in current_accounts:
						print(f"Account name '{data_dir_name}' is already used. Please choose a different name.")
						continue

					if directory_name_is_valid(data_dir_name): break

					print(f"Invalid account name '{data_dir_name}'. Please use letters, digits, dot, dash or underscore, and do not end in a dot. Do not use spaces.")

				current_accounts.append(data_dir_name)

				if boolean_with_default("Would you like to sign in to this profile now?", True):
					print(f"Launching browser for account '{data_dir_name}' for sign-in")

					driver = start_driver(
						Account(
							data_dir_name,
							user_data_dir=Path(_env_key(path_to_dotenv, "USER_DATA_DIR") or Path(__file__).parent.parent / "data-dir") / data_dir_name,
							profile_name="Default",
						)
					)

					if not driver:
						print("Failed to start the browser for sign-in. Please ensure msedgedriver.exe and msedge.exe are correctly set up.")
						continue

					print("Close the browser window after signing in to the account. The script will wait until the browser is closed before proceeding.")

					while True:
						try:
							driver.title
							time.sleep(0.5)
						except Exception: break

		elif opt == "remove":
			if not current_accounts:
				print("No accounts to remove.")
				continue

			while True:
				acc_to_remove = one_of_with_default("Enter the account name to remove (enter <none> when done)", [*current_accounts, "<none>"], "<none>")

				if acc_to_remove == "<none>":
					break

				current_accounts.remove(acc_to_remove)
				print(f"Account '{acc_to_remove}' removed.")
		elif opt == "signin":
			if not current_accounts:
				print("No accounts to sign in to.")
				continue

			while True:
				acc_to_signin = one_of_with_default("Enter the account name to sign in (enter <none> when done)", [*current_accounts, "<none>"], "<none>")

				if acc_to_signin == "<none>":
					break

				print(f"Launching browser for account '{acc_to_signin}' for sign-in")

				driver = start_driver(
					Account(
						acc_to_signin,
						user_data_dir=Path(_env_key(path_to_dotenv, "USER_DATA_DIR") or Path(__file__).parent.parent / "data-dir") / acc_to_signin,
						profile_name="Default",
					)
				)

				if not driver:
					print("Failed to start the browser for sign-in. Please ensure msedgedriver.exe and msedge.exe are correctly set up.")
					continue

				print("Close the browser window after signing in to the account. The script will wait until the browser is closed before proceeding.")

				while True:
					try:
						driver.title
						time.sleep(0.5)
					except Exception: break

		else: break

	# Update the .env file with the new account list

	dotenv.set_key(str(path_to_dotenv), "REWARDS_ACCOUNTS", ",".join(current_accounts))

if __name__ == "__main__":
	parser = ArgumentParser(description="Configure the Rewards Farmer application.")

	parser.add_argument("--manage-accounts", action="store_true", help="Launch the account management interface to add, remove, or modify accounts without changing other configuration settings.")

	launch_account_management: bool = parser.parse_args().manage_accounts

	if launch_account_management:
		manage_accounts()
		exit()

	main()
