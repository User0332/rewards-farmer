# User0332/rewards-farmer

Automation for MS Rewards based on [https://youtu.be/4qdPcMNaioA](https://youtu.be/4qdPcMNaioA).

# Running Instructions

IMPORTANT: Use at your own risk. Microsoft may take action against your account for using automated scripts to gain rewards points. The YouTube video contains more details about the techniques implemented to avoid detection of this script.

Clone the repository.

```sh
git clone https://github.com/User0332/rewards-farmer
```

A sample `nouns.txt` file is included in the project root and can be modified by the user to contain seed words for the LLM to complete 20 searches. The wordlist should be separated by newline.

```sh
cd rewards-farmer
# Edit the included nouns.txt file to add or replace words as needed
```

# Where search queries come from

The bot needs short strings to type into Bing. Two backends produce them, set with `QUERY_SOURCE`:

| `QUERY_SOURCE` | Needs | Notes |
| --- | --- | --- |
| `llm` (default) | Ollama account + model | Current behaviour, unchanged |
| `trends` | nothing | Google Trends, Wikipedia and Bing autosuggest |

```sh
QUERY_SOURCE=trends python src/main.py          # bash
$env:QUERY_SOURCE="trends"; python src/main.py  # PowerShell
```

`trends` needs no account, no API key and no model download, so the Ollama setup below is optional if you use it. If every feed is unreachable it falls back to `nouns.txt` rather than failing the run.

You should also have an Ollama account created (for the LLM), the `ollama` tool installed, and you should have signed in to the Ollama CLI via the command line using `ollama signin`. This project will use a minimal amount of Ollama cloud usage using `gemma4:cloud`. If you wish to use a different model, please change the `model` parameter in the `get_ollama_response` function in `src/llm_utils.py`.

You must also provide an image for the script to upload to complete the visual search task. A helper script is included at `src/random_image_for_visual_search.py` that will download an image from Wikipedia named `visual_search.jpg` into the project root for you. You may also provide an image of your own, just ensure that the absolute path of the image is placed in the `VISUAL_SEARCH_IMAGE_PATH` constant at the top of `rewards_tasks.py`.

Activate the virtual environment & install dependencies (you may have to use `python -m poetry` instead of `poetry`).
You must have Python 3.12+ and Poetry installed.

If `iex (poetry env activate)` fails with *"Cannot bind argument to parameter 'Command' because it is null"*, `poetry install` did not create an environment. Run `python --version` first: an older Python leaves poetry with nothing to activate, and the message explaining that goes to stderr rather than into `iex`.

Windows (PowerShell)
```sh
poetry install
iex (poetry env activate)
```

*nix (Bash)
```sh
poetry install
eval $(poetry env activate)
```

You must also have a [webdriver for Microsoft Edge](https://learn.microsoft.com/en-us/microsoft-edge/webdriver/?tabs=c-sharp) installed. If you already have the Edge Browser installed, you probably have this component as well.

The profile directory in `src/constants.py` is set to `Default`. If this signs you in to a global profile that you do not want to use for automation, then you can create a new profile from within the webdriver instance manually and then change the `PROFILE_NAME` constant to `Profile 1` (or the equivalent number).

Run main.py (`python src/main.py`, it must be run from the root directory so the relative paths work out), wait for the page to launch, and then CTRL-C to quit the application immediately. Sign in to the created profile with your Microsoft account on both Bing and `rewards.bing.com`.

EU Users: you may have to accept a consent banner once on `rewards.bing.com` and on the Bing search page, `bing.com`. Once you consent, your choice will be saved for future runs using the same profile, so you will not need to interact with the banner during automated runs.

Close all webdriver browser instances. Run `main.py` again; the automation should start working.

# Running more than one account

Rewards is per Microsoft account and the browser profile holds the sign-in, so an account here is a profile directory. `REWARDS_ACCOUNTS` takes a comma separated list, and each name gets its own directory under `data-dir`:

```sh
REWARDS_ACCOUNTS=personal,spare python src/main.py
```

Each is signed in once by hand, the same way as the single profile, using its own directory:

```
msedge --user-data-dir="<repo>\data-dir\personal" --profile-directory=Default https://rewards.bing.com
```

They run one after another, and an account that fails is reported and skipped rather than ending the run, whether it fails to start or dies partway through. Leave `REWARDS_ACCOUNTS` unset and everything behaves exactly as before, using the single profile in `data-dir`.

# Docker

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

Open <http://localhost:6080>, sign in, then close the Edge window on that screen. The container exits on its own and the profile is ready. Nothing is installed on the host: any browser will do, on Windows, macOS or Linux.

`--service-ports` is not optional. `docker compose run` publishes no ports without it, and the page then never loads.

One account at a time, since it is one browser window:

```sh
REWARDS_ACCOUNTS=personal docker compose run --rm --service-ports signin
```

The port is published on `127.0.0.1` only, so it is not reachable from the network. While the service is up it is showing a live Microsoft sign-in page.

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

# Logging

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

Please open up a GitHub issue if you run into any difficulties.