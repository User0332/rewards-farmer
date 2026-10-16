# User0332/rewards-farmer

Automation for MS Rewards based on [https://youtu.be/4qdPcMNaioA](https://youtu.be/4qdPcMNaioA).

## Table of Contents

- [Core Setup & Running Instructions](#core-setup--running-instructions)
	- [Where search queries come from](#where-search-queries-come-from)
	- [Installing Dependencies](#installing-dependencies)
	- [Profile Setup](#profile-setup)
	- [Graphical interface](#graphical-interface)
- [If Edge will not start](#if-edge-will-not-start)
- [Running more than one account](#running-more-than-one-account)
- [Docker](#docker)
- [Logging](#logging)
- [Windows Virtual Desktop (Windows only)](#windows-virtual-desktop-windows-only)


## Core Setup & Running Instructions

IMPORTANT: Use at your own risk. Microsoft may take action against your account for using automated scripts to gain rewards points. The YouTube video contains more details about the techniques implemented to avoid detection of this script.

Clone the repository.

```sh
git clone https://github.com/User0332/rewards-farmer
cd rewards-farmer
```

### Installing Dependencies

Activate the virtual environment & install dependencies (you may have to use `python -m poetry` instead of `poetry`).
You must have Python 3.12+ and Poetry installed.

If `iex (poetry env activate)` fails with *"Cannot bind argument to parameter 'Command' because it is null"*, `poetry install` did not create an environment. Run `python --version` first: an older Python leaves poetry with nothing to activate, and the message explaining that goes to stderr rather than into `iex`.

Windows (PowerShell)

```sh
poetry install
iex (poetry env activate)
```

\*nix (Bash)

```sh
poetry install
eval $(poetry env activate)
```

You must also have a [webdriver for Microsoft Edge](https://learn.microsoft.com/en-us/microsoft-edge/webdriver/?tabs=c-sharp) installed. If you already have the Edge Browser installed, you probably have this component as well.

### Configuration Script

At this point you can run the config script using `python src/init-config.py`, which will walk you through the available configuration options. However, it is still recommended that you continue reading the rest of this README before doing so to fully understand each option.

Running `init-config.py` with the `--manage-accounts` flag (shown below) will allow you to add, remove, and sign in to accounts without reconfiguring the entire project.

```sh
py .\src\init-config.py --manage-accounts
```

### Graphical interface

If you would rather not use the command line, `src/gui.py` puts the same
three things in a window - no extra features, just buttons in front of what
already exists. It uses Tkinter, which ships with Python, so nothing new needs
installing.

```sh
python src/gui.py
```

On Windows you can use `pythonw src/gui.py` to open it without a console
window behind it. The `run-gui.bat` launcher in the project root does the
same without needing a terminal: double-click it, or copy it wherever you
like. It finds the Poetry environment itself, so it works even when launched
from outside the folder. A companion `run-cli.bat` runs the normal
command-line `main.py` in a console instead, for when you want to watch its
output, which is what `python src/main.py` does.

- **Run** starts `main.py` for the accounts in `.env` and shows its log while
  it works, with a Stop button to end a run early. It farms every account on
  the Accounts tab except the ones you excluded.
- **Accounts** adds and removes profile names and signs one in, the same as
  `init-config.py --manage-accounts`. Add one name at a time, or use **Add
  many...** to paste a whole list (one per line, or comma separated). The
  list shows the Microsoft account each profile is actually signed into,
  which is the account a run farms - worth checking, since a profile's folder
  name says nothing about who is logged in to it. Adding a name does not open
  a browser; use Sign in to do that once per profile.
  Every account is included in a run by default. Select one or more rows and
  use **Exclude** to leave them out (or **Include** to put them back);
  double-clicking a row flips it. Excluded rows are dimmed and marked
  *Skipped* in the Run column. The choice is remembered in `.env` under
  `GUI_EXCLUDED_ACCOUNTS`, again a key the bot itself never reads - the
  window simply hands a run the shorter `REWARDS_ACCOUNTS`, so running
  `main.py` by hand still works through the whole list.
- **Settings** reads and writes the same `.env` values `init-config.py` asks
  about. Only the keys it shows are touched, so comments and any other values
  in the file are left as they were.

The window follows Windows' light or dark setting on first run, and the
**Light mode / Dark mode** button in the header switches it either way. The
choice is remembered in `.env` under `GUI_THEME`, a key the bot itself never
reads.

### Where search queries come from

The bot needs short strings to type into Bing. Two backends produce them, set with `QUERY_SOURCE`:

| `QUERY_SOURCE` | Needs | Notes |
| --- | --- | --- |
| `llm` (default) | OpenRouter or Ollama account + model | `llm` via Ollama is the current behaviour, unchanged |
| `trends` | nothing | Google Trends, Wikipedia and Bing autosuggest |

```sh
QUERY_SOURCE=trends python src/main.py          # bash
$env:QUERY_SOURCE="trends"; python src/main.py  # PowerShell
```

`trends` needs no account, no API key and no model download, so the LLM setup below is optional if you use it. If every feed is unreachable it falls back to `nouns.txt` rather than failing the run.

If you would like to use LLMs, you should also configure an LLM provider through a `.env` file in the project root. The script now talks to either OpenRouter or a local OpenAI-compatible LLM endpoint depending on `LLM_PROVIDER`.

A sample `nouns.txt` file is included in the project root and can be modified by the user to contain seed words for an LLM to complete 20 searches. The wordlist should be separated by newline.

Example `.env` values:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_key_here
OPENROUTER_MODEL=openai/gpt-4o-mini

# Or use a local endpoint instead
# LLM_PROVIDER=local
# LOCAL_LLM_BASE_URL=http://localhost:11434/v1
# LOCAL_LLM_MODEL=gemma3:4b
```

For OpenRouter, the code uses the OpenAI-compatible chat completions API at `https://openrouter.ai/api/v1/chat/completions`. For local models, the endpoint must also be OpenAI-compatible. More configuration options can be found in [`.env.example`](.env.example).

If the configuration options are not provided, they default to `LLM_PROVIDER=local`, `LOCAL_LLM_BASE_URL=http://localhost:11434/v1`, `LOCAL_LLM_MODEL=gemma4:cloud`, and `OPENROUTER_MODEL=openrouter/free`.

You must also provide an image for the script to upload to complete the visual search task. A helper script is included at `src/random_image_for_visual_search.py` that will download an image from Wikipedia named `visual_search.jpg` into the project root for you. You may also provide an image of your own, just ensure that the absolute path of the image is placed in the `VISUAL_SEARCH_IMAGE_PATH` constant at the top of `rewards_tasks.py`.

### Profile Setup

The profile directory in `src/constants.py` is set to `Default`. If this signs you in to a global profile that you do not want to use for automation, then you can create a new profile from within the webdriver instance manually and then change the `PROFILE_NAME` constant to `Profile 1` (or the equivalent number).

Run main.py (`python src/main.py`; paths are resolved from the repository, so it can be started from any directory), wait for the page to launch, and then CTRL-C to quit the application immediately. Sign in to the created profile with your Microsoft account on both Bing and `rewards.bing.com`.

EU Users: you may have to accept a consent banner once on `rewards.bing.com` and on the Bing search page, `bing.com`. Once you consent, your choice will be saved for future runs using the same profile, so you will not need to interact with the banner during automated runs.

The bot finds buttons by their English labels, so it starts Edge with `--accept-lang=en-US` and Rewards and Bing load in English whatever language Edge is set to. Edge saves this setting in the profile, so Bing and Rewards also show up in English when you open that profile yourself.

Close all webdriver browser instances. Run `main.py` again; the automation should start working.

## If Edge will not start

When the browser fails to start, the log names the likely cause from the driver's own message, and falls back to printing that message as is. Three optional environment variables help when it does not:

| Variable | Effect |
| --- | --- |
| `MSEDGEDRIVER_PATH` | Full path to `msedgedriver` to use, instead of letting selenium look for one. Try this first on *Unable to obtain driver for MicrosoftEdge*. |
| `EDGE_BINARY` | Full path to the Edge executable, for an install selenium does not find on its own. |
| `REWARDS_DRIVER_LOG` | Path to write a verbose msedgedriver log to. On *Chrome instance exited* this log holds Edge's actual reason; attach it to a bug report. |

```sh
$env:REWARDS_DRIVER_LOG="msedgedriver.log"; python src/main.py   # PowerShell
REWARDS_DRIVER_LOG=msedgedriver.log python src/main.py          # bash
```

`src/check_selectors.py` starts Edge the same way and reads the same variables.

## Running more than one account

Rewards is per Microsoft account and the browser profile holds the sign-in, so an account here is a profile directory. `REWARDS_ACCOUNTS` takes a comma separated list, and each name gets its own directory under `data-dir`:

```sh
REWARDS_ACCOUNTS=personal,spare python src/main.py
```

Each is signed in once by hand, the same way as the single profile, using its own directory:

```
msedge --user-data-dir="<repo>\data-dir\personal" --profile-directory=Default https://rewards.bing.com
```

They run one after another, and an account that fails is reported and skipped rather than ending the run, whether it fails to start or dies partway through. Leave `REWARDS_ACCOUNTS` unset and everything behaves exactly as before, using the single profile in `data-dir`.

## Docker

Runs the bot without installing Edge, a driver or Python on the host.

```sh
docker compose build
docker compose run --rm rewards-farmer
```

The container defaults to `QUERY_SOURCE=trends`, so it needs no Ollama account and no model. Set `QUERY_SOURCE=llm` and `OLLAMA_HOST` to a reachable address to use a model instead.

**Sign in first.** The profile in `data-dir` starts logged out. Sign in from inside the container, which opens the Rewards page in a browser there and puts that browser on your screen as a web page:

```sh
docker compose run --rm --service-ports signin
```

Open <http://localhost:6080>, sign in, then close the Edge window on that screen. The container exits on its own and the profile is ready. Nothing is installed on the host, and the host operating system stops mattering, because the profile is written inside the container rather than on the host. The screen is an ordinary web page, so whatever browser you already have will do.

`--service-ports` is not optional. `docker compose run` publishes no ports without it, and the page then never loads.

One account at a time, since it is one browser window:

```sh
REWARDS_ACCOUNTS=personal docker compose run --rm --service-ports signin
```

The port is published on `127.0.0.1` only, so it is not reachable from the network. While the service is up it is showing a live Microsoft sign-in page.

Signing in signs the browser in, not just the website, so Edge may sync bookmarks and autofill into the profile it just created. `data-dir` is a bot profile living in the project directory rather than your everyday browser profile, and it is gitignored, but it is worth knowing what ends up there.

<details>
<summary>Why sign-in has to happen inside the container</summary>

Chromium encrypts cookie values with a key it gets from the operating system, and the container has to be able to unwrap that key to read the profile.

On **Linux** with no keyring running it falls back to a fixed key, which is true both on a plain Linux host and inside the image, so a profile signed in on such a host does carry straight in.

On **Windows** the key is wrapped with DPAPI and tied to the Windows account that wrote it, and the container has no DPAPI. A profile signed in with a normal Windows Edge window reported 73 cookies on disk, of which Edge in the container could read 19 — the ones it had just set itself — while `.MSA.Auth` and `ANON`, the ones the sign-in actually rests on, came back absent. The container starts, looks healthy and behaves as though it were logged out. **macOS** wraps the key with the login Keychain, which the container cannot reach either.

Signing in through the container sidesteps all of this: the profile is written by the same Edge that later reads it, so the two never disagree about the key.

</details>

You can still sign in with a host browser if you prefer, and on a Linux host it works. Close every window of that profile afterwards, and close them rather than killing them: Chromium allows one process per profile directory, and a browser that was killed leaves a `SingletonLock` naming the machine that wrote it, which the container reads as the profile being open somewhere else.

**Provide the visual search image on the host too.** `visual_search.jpg` is not in the repository and is not built into the image, so create it once in the project root and the compose file mounts it in:

```sh
python src/random_image_for_visual_search.py
```

Without it every other task still runs; only the visual search one fails.

Multiple accounts work the same way in the container. Sign each profile in once, one at a time, then run them together:

```sh
REWARDS_ACCOUNTS=personal docker compose run --rm --service-ports signin
REWARDS_ACCOUNTS=spare    docker compose run --rm --service-ports signin

REWARDS_ACCOUNTS=personal,spare docker compose run --rm rewards-farmer
```

`REWARDS_HEADLESS=1` is set in the image. It also works on the host if you want a run with no visible window; the pointer code needs an explicit window size in that mode, which `main.py` sets.

## Logging

The script logs to the console. Two optional environment variables change that:

| Variable | Default | Effect |
| --- | --- | --- |
| `REWARDS_FARMER_LOG_LEVEL` | `INFO` | Set to `DEBUG` to also attach the full stack trace to every `[FAIL]` line. |
| `REWARDS_FARMER_LOG_FILE` | unset | Path to also write the log to, useful for unattended runs. |

Windows (PowerShell)
```sh
$env:REWARDS_FARMER_LOG_LEVEL="DEBUG"; $env:REWARDS_FARMER_LOG_FILE="run.log"; python src/main.py
```

*nix (Bash)
```sh
REWARDS_FARMER_LOG_LEVEL=DEBUG REWARDS_FARMER_LOG_FILE=run.log python src/main.py
```

If you are opening an issue about a crash, running with `REWARDS_FARMER_LOG_LEVEL=DEBUG` and attaching the log is the most useful thing you can include.

## Windows Virtual Desktop (Windows only)

To run the browser on a separate Windows Virtual Desktop so searches run in the background without interrupting your current workspace:

| Variable | Default | Effect |
| --- | --- | --- |
| `USE_VIRTUAL_DESKTOP` | `false` | When `true`, automatically creates a new Windows Virtual Desktop via `Win+Ctrl+D` and launches the browser there. Windows only. |
| `SWITCH_BACK_TO_MAIN_DESKTOP` | `true` | When `true` (and `USE_VIRTUAL_DESKTOP` is enabled), automatically switches back to your starting desktop after launching Edge. |
| `SWITCH_BACK_DELAY_SECONDS` | `1.5` | Delay in seconds to wait before switching back, giving Edge time to attach its window to the new desktop. |
| `CLEANUP_VIRTUAL_DESKTOP` | `true` | When `true`, automatically closes the created worker virtual desktop via `Win+Ctrl+F4` after completing all profiles and pressing Enter, returning focus to your main desktop. |

Set these in your `.env` file or provide them as environment variables:

Windows (PowerShell)
```sh
$env:USE_VIRTUAL_DESKTOP="true"; python src/main.py
```

> **Note:** The script automatically detects which virtual desktop you started from and calculates the exact number of navigation hops so it returns directly to your starting desktop. When `CLEANUP_VIRTUAL_DESKTOP=true`, the worker desktop is safely closed after you press Enter on exit, returning you to your main desktop.

Please open up a GitHub issue if you run into any difficulties.

