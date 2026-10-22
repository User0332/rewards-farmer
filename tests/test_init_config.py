from contextlib import redirect_stdout
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)


spec = importlib.util.spec_from_file_location("init_config", os.path.join(SRC, "init-config.py"))
init_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init_config)


class QuietOutputTestCase(unittest.TestCase):
	def setUp(self):
		self.output = io.StringIO()
		self.stdout_context = redirect_stdout(self.output)
		self.stdout_context.__enter__()
		self.addCleanup(self.stdout_context.__exit__, None, None, None)


class TestPromptHelpers(QuietOutputTestCase):
	def test_one_of_with_default_retries_and_accepts_default(self):
		with mock.patch("builtins.input", side_effect=["invalid", ""]):
			self.assertEqual(init_config.one_of_with_default("Choice", ["a"], "a"), "a")

	def test_positive_integer_with_default_retries_invalid_values(self):
		with mock.patch("builtins.input", side_effect=["text", "0", "7"]):
			self.assertEqual(init_config.positive_integer_with_default("Number", 1), 7)

	def test_boolean_with_default_accepts_yes_no_and_retries(self):
		with mock.patch("builtins.input", side_effect=["maybe", "Y"]):
			self.assertTrue(init_config.boolean_with_default("Boolean", False))

		with mock.patch("builtins.input", side_effect=["N"]):
			self.assertFalse(init_config.boolean_with_default("Boolean", True))

	def test_nonempty_input_retries_blank_input(self):
		with mock.patch("builtins.input", side_effect=["  ", "value"]):
			self.assertEqual(init_config.nonempty_input("Value"), "value")


class TestPathInput(QuietOutputTestCase):
	def test_path_input_creates_directory_and_file(self):
		directory = Path("<mock-directory>/nested/directory")
		file_path = Path("<mock-directory>/nested/file.txt")

		with mock.patch("builtins.input", return_value=str(directory)):
			with mock.patch.object(Path, "mkdir") as mkdir:
				self.assertEqual(init_config.path_input("Directory", Path("unused"), "dir"), directory)
		mkdir.assert_called_once_with(parents=True, exist_ok=True)

		with mock.patch("builtins.input", return_value=str(file_path)):
			with mock.patch.object(Path, "mkdir") as mkdir:
				with mock.patch.object(Path, "touch") as touch:
					self.assertEqual(init_config.path_input("File", Path("unused"), "file"), file_path)
		touch.assert_called_once_with(exist_ok=True)
		mkdir.assert_called_once_with(parents=True, exist_ok=True)

	def test_path_input_retries_after_create_errors(self):
		valid_directory = Path("<mock-directory>/valid")
		with mock.patch("builtins.input", side_effect=["bad", str(valid_directory)]):
			with mock.patch.object(Path, "mkdir", side_effect=[OSError("no directory"), None]):
				self.assertEqual(init_config.path_input("Directory", Path("unused"), "dir"), valid_directory)

		valid_file = Path("<mock-directory>/valid.txt")
		with mock.patch("builtins.input", side_effect=["bad.txt", str(valid_file)]):
			with mock.patch.object(Path, "mkdir"):
				with mock.patch.object(Path, "touch", side_effect=[OSError("no file"), None]):
					self.assertEqual(init_config.path_input("File", Path("unused"), "file"), valid_file)

	def test_path_input_requires_existing_path_without_creation(self):
		missing = Path("<mock-directory>/missing")
		existing = Path("<mock-directory>/existing")
		with mock.patch("builtins.input", side_effect=[str(missing), str(existing)]):
			with mock.patch.object(Path, "exists", side_effect=[False, True]):
				self.assertEqual(init_config.path_input("Path", Path("unused")), existing)


class TestAccountConfiguration(QuietOutputTestCase):
	def test_manage_accounts_dotenv_retries_and_preserves_environment_override(self):
		with tempfile.TemporaryDirectory() as folder:
			path = Path(folder) / ".env"
			path.write_text("REWARDS_ACCOUNTS=file-account\nF61_NEW=file\nF61_EXISTING=file\n", encoding="utf-8")
			real_open = open
			attempts = []
			def open_file(name, *args, **kwargs):
				if Path(name) == path:
					attempts.append(True)
					if len(attempts) == 1:
						raise PermissionError("delete-pending")
				return real_open(name, *args, **kwargs)
			with mock.patch.dict(os.environ, {"REWARDS_ACCOUNTS": "existing-account", "F61_EXISTING": "environment"}, clear=True), mock.patch.object(init_config, "path_input", return_value=path), mock.patch.object(init_config, "one_of_with_default", return_value="exit"), mock.patch.object(init_config.dotenv, "set_key") as save, mock.patch("file_replace.builtins.open", side_effect=open_file):
				init_config.manage_accounts()
				self.assertEqual(os.environ["F61_NEW"], "file")
				self.assertEqual(os.environ["F61_EXISTING"], "environment")
				save.assert_called_once_with(str(path), "REWARDS_ACCOUNTS", "existing-account")
			self.assertEqual(len(attempts), 2)

	def test_profile_data_key_read_retries_with_dotenv_interpolation(self):
		with tempfile.TemporaryDirectory() as folder:
			path = Path(folder) / ".env"
			path.write_text("F61_ROOT=profiles\nUSER_DATA_DIR=${F61_ROOT}/custom\n", encoding="utf-8")
			real_open = open
			attempts = []
			def open_file(*args, **kwargs):
				attempts.append(True)
				if len(attempts) == 1:
					raise PermissionError("delete-pending")
				return real_open(*args, **kwargs)
			with mock.patch("file_replace.builtins.open", side_effect=open_file):
				self.assertEqual(init_config._env_key(path, "USER_DATA_DIR"), "profiles/custom")
			self.assertEqual(len(attempts), 2)

	def test_add_and_signin_profile_paths_each_retry_their_fresh_key_read(self):
		for action in ("add", "signin"):
			with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
				path = Path(folder) / ".env"
				profiles = Path(folder) / "profiles"
				path.write_text("USER_DATA_DIR='" + str(profiles) + "'\nREWARDS_ACCOUNTS=" + ("account-1" if action == "signin" else "") + "\n", encoding="utf-8")
				real_open = open
				attempts = []
				def open_file(name, *args, **kwargs):
					if Path(name) == path:
						attempts.append(True)
						if len(attempts) == 2:
							raise PermissionError("profile key delete-pending")
					return real_open(name, *args, **kwargs)
				choices = ["add", "exit"] if action == "add" else ["signin", "account-1", "<none>", "exit"]
				with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(init_config, "path_input", return_value=path), mock.patch.object(init_config, "one_of_with_default", side_effect=choices), mock.patch.object(init_config, "positive_integer_with_default", return_value=1), mock.patch.object(init_config, "nonempty_input", return_value="account-1"), mock.patch.object(init_config, "boolean_with_default", return_value=True), mock.patch.object(init_config.dotenv, "set_key"), mock.patch.object(init_config, "start_driver", return_value=object()) as start, mock.patch("file_replace.builtins.open", side_effect=open_file):
					init_config.manage_accounts()
				self.assertEqual(Path(start.call_args.args[0].user_data_dir), profiles / "account-1")
				self.assertEqual(len(attempts), 3)

	def test_single_account_writes_root_and_account_without_launching_browser(self):
		root = Path("<mock-data>")
		output = io.StringIO()
		with mock.patch.object(init_config, "path_input", return_value=root):
			with mock.patch("builtins.input", side_effect=["1", "default", "no"]):
				init_config.configure_multi_account(output)

		self.assertEqual(
			output.getvalue(),
			f"USER_DATA_DIR={root.resolve()}\nREWARDS_ACCOUNTS=default\n",
		)

	def test_multiple_accounts_retries_invalid_names_without_launching_browser(self):
		root = Path("<mock-data>")
		output = io.StringIO()
		answers = ["2", "", "bad name", "first", "no", "second", "n"]
		with mock.patch.object(init_config, "path_input", return_value=root):
			with mock.patch("builtins.input", side_effect=answers):
				with mock.patch.object(init_config, "start_driver") as start_driver:
					init_config.configure_multi_account(output)

		self.assertEqual(output.getvalue(), f"USER_DATA_DIR={root.resolve()}\nREWARDS_ACCOUNTS=first,second\n")
		start_driver.assert_not_called()


class TestConfigureVariables(QuietOutputTestCase):
	def test_trends_defaults_and_non_windows_options(self):
		output = io.StringIO()
		with mock.patch("builtins.input", side_effect=["", "yes", "no", "no"]):
			with mock.patch.object(init_config, "configure_multi_account") as configure_accounts:
				with mock.patch.object(init_config.sys, "platform", "linux"):
					init_config.configure_variables(output)

		self.assertEqual(output.getvalue(), "QUERY_SOURCE=trends\nREWARDS_HEADLESS=true\n")
		configure_accounts.assert_called_once_with(output)

	def test_openrouter_custom_paths_logging_and_windows_options(self):
		driver_path = Path("<mock-path>/msedgedriver.exe")
		edge_path = Path("<mock-path>/msedge.exe")
		output = io.StringIO()
		path_values = [driver_path, edge_path, None, None]
		answers = ["llm", "openrouter", "key", "", "2", "no", "yes", "yes", "DEBUG", "no"]
		with mock.patch("builtins.input", side_effect=answers):
			with mock.patch.object(init_config, "default_unset_path_input", side_effect=path_values):
				with mock.patch.object(init_config, "configure_multi_account"):
					with mock.patch.object(init_config.sys, "platform", "win32"):
						init_config.configure_variables(output)

			self.assertIn("LLM_PROVIDER=openrouter\n", output.getvalue())
			self.assertIn("OPENROUTER_API_KEY=key\n", output.getvalue())
			self.assertIn("OPENROUTER_MODEL=openrouter/free\n", output.getvalue())
			self.assertIn("LLM_REQUEST_TIMEOUT_SECONDS=2\n", output.getvalue())
			self.assertIn("MSEDGEDRIVER_PATH=", output.getvalue())
			self.assertIn("EDGE_BINARY=", output.getvalue())
			self.assertIn("REWARDS_FARMER_LOG_LEVEL=DEBUG\n", output.getvalue())
			self.assertIn("USE_VIRTUAL_DESKTOP=false\n", output.getvalue())

	def test_local_llm_defaults_and_logging_unset_paths(self):
		output = io.StringIO()
		answers = ["llm", "local", "", "", "", "", "no", "yes", "yes", "INFO"]
		with mock.patch("builtins.input", side_effect=answers):
			with mock.patch.object(init_config, "default_unset_path_input", return_value=None):
				with mock.patch.object(init_config, "configure_multi_account"):
					with mock.patch.object(init_config.sys, "platform", "linux"):
						init_config.configure_variables(output)

		self.assertIn("LOCAL_LLM_BASE_URL=http://localhost:11434/v1\n", output.getvalue())
		self.assertIn("LOCAL_LLM_MODEL=gemma4:cloud\n", output.getvalue())
		self.assertIn("LOCAL_LLM_API_KEY=\n", output.getvalue())
		self.assertIn("REWARDS_FARMER_LOG_LEVEL=INFO\n", output.getvalue())


class TestMain(QuietOutputTestCase):
	def test_main_opens_configures_and_writes_the_env_file(self):
		config_path = Path("config.env")
		open_mock = mock.mock_open()

		with mock.patch.object(init_config, "path_input", return_value=config_path):
			with mock.patch.object(init_config, "boolean_with_default", return_value=True):
				with mock.patch("builtins.open", open_mock):
					with mock.patch.object(init_config, "configure_variables") as configure_variables:
						self.assertIsNone(init_config.main())

		open_mock.assert_called_once_with(config_path, "w")
		configure_variables.assert_called_once_with(open_mock())

	def test_main_stops_before_opening_file_when_user_declines(self):
		with mock.patch.object(init_config, "path_input", return_value=Path("config.env")):
			with mock.patch.object(init_config, "boolean_with_default", return_value=False):
				with mock.patch.object(init_config, "configure_variables") as configure_variables:
					self.assertIsNone(init_config.main())

		configure_variables.assert_not_called()

	def test_main_reports_file_errors(self):
		with mock.patch.object(init_config, "path_input", return_value=Path("config.env")):
			with mock.patch.object(init_config, "boolean_with_default", return_value=True):
				with mock.patch("builtins.open", side_effect=OSError("cannot write")):
					init_config.main()

	def test_main_reports_configuration_write_errors(self):
		with mock.patch.object(init_config, "path_input", return_value=Path("config.env")):
			with mock.patch.object(init_config, "boolean_with_default", return_value=True):
				with mock.patch.object(init_config, "configure_variables", side_effect=OSError("cannot write")):
					with mock.patch("builtins.open", mock.mock_open()):
						init_config.main()
