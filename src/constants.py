import os
from pathlib import Path

# Project root path
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Browser & Profile paths
USER_DATA_DIR = os.environ.get("REWARDS_USER_DATA_DIR", str(PROJECT_ROOT / "data-dir"))
PROFILE_NAME = os.environ.get("REWARDS_PROFILE_NAME", "Default")
VISUAL_SEARCH_IMAGE_PATH = PROJECT_ROOT / "visual_search.jpg"

# URLs
BING_BASE_URL = "https://www.bing.com/"
REWARDS_EARN_URL = "https://rewards.bing.com/earn"
REWARDS_DASHBOARD_URL = "https://rewards.bing.com/dashboard"

# Desktop & Mobile User Agents (Windows Edge spoofing on Linux)
WINDOWS_DESKTOP_UA = (
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
	"AppleWebKit/537.36 (KHTML, like Gecko) "
	"Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"
)
MOBILE_UA = (
	"Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
	"AppleWebKit/605.1.15 (KHTML, like Gecko) "
	"Version/17.5 Mobile/15E148 Safari/604.1 Edg/125.0.0.0"
)

# Ollama & LLM settings
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
MODEL_FALLBACK_PREFERENCES = (
	"llama3.2",
	"llama3.2:3b",
	"llama3.2:1b",
	"llama3.1",
	"llama3.1:8b",
	"llama3",
	"mistral",
	"phi3",
	"phi3.5",
	"gemma2",
	"qwen2.5",
	"qwen2",
	"tinyllama"
)

# Human timing / delays (seconds)
IDLE_DELAY_MIN = 2.5
IDLE_DELAY_MAX = 6.0
MICRO_DELAY_MIN = 0.4
MICRO_DELAY_MAX = 1.2
TASK_PAUSE_MIN = 3.0
TASK_PAUSE_MAX = 7.5

# Search Cooldown Settings (Microsoft 15-minute search throttling)
COOLDOWN_BATCH_SIZE = 4
COOLDOWN_SEARCH_DELAY_MIN = 10.0
COOLDOWN_SEARCH_DELAY_MAX = 20.0
COOLDOWN_SLEEP_MIN = 15.5 * 60  # 15.5 minutes in seconds
COOLDOWN_SLEEP_MAX = 17.5 * 60  # 17.5 minutes in seconds
MAX_COOLDOWN_ROUNDS = 6
MAX_TOTAL_RUNTIME_SECONDS = 110 * 60  # 110 min safety cut-off

# Bing / Start Mobile App API settings (Read to Earn & Daily Check-In)
BING_APP_CLIENT_ID = "0000000040170455"
BING_APP_SCOPE = "service::prod.rewardsplatform.microsoft.com::MBI_SSL"
BING_APP_USER_AGENT = "Bing/32.5.431027001 (com.microsoft.bing; build:431027001; iOS 17.6.1) Alamofire/5.10.2"
START_APP_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/605.1.15 BingSapphire/33.4.440603001"
REWARDS_ACTIVITIES_URL = "https://prod.rewardsplatform.microsoft.com/dapi/me/activities"
OAUTH_AUTHORIZE_URL = "https://login.live.com/oauth20_authorize.srf"
OAUTH_REDIRECT_URL = "https://login.live.com/oauth20_desktop.srf"
OAUTH_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"