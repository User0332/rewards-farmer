from os.path import abspath, dirname, join
import os

# Resolved from this file rather than the working directory, so launching from
# somewhere else (a shortcut, Task Scheduler, an elevated shell that starts in
# C:\WINDOWS\system32) still finds the profile and the repo's own files.
REPO_ROOT = os.environ.get("REWARDS_FARMER_RESOURCE_ROOT") or dirname(dirname(abspath(__file__)))
DATA_HOME = os.environ.get("REWARDS_FARMER_DATA_HOME") or REPO_ROOT

DEFAULT_ROOT_DATA_DIR = join(DATA_HOME, "data-dir")
DOTENV_PATH = join(DATA_HOME, ".env")
RUNS_DIR = join(DATA_HOME, "runs")
