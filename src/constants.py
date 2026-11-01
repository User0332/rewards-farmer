from os.path import abspath, dirname, join

# Resolved from this file rather than the working directory, so launching from
# somewhere else (a shortcut, Task Scheduler, an elevated shell that starts in
# C:\WINDOWS\system32) still finds the profile and the repo's own files.
REPO_ROOT = dirname(dirname(abspath(__file__)))

DEFAULT_ROOT_DATA_DIR = join(REPO_ROOT, "data-dir")
DOTENV_PATH = join(REPO_ROOT, ".env")
RUNS_DIR = join(REPO_ROOT, "runs")
