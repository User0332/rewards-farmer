from os.path import abspath

USER_DATA_DIR = abspath("./data-dir")
PROFILE_NAME = "Profile 1"

# 3 for IN/UK/EU accounts, 5 for US accounts
POINTS_PER_SEARCH = 3

# Directory containing images for visual search (defaults to project root ".")
VISUAL_SEARCH_IMAGES_DIR = abspath(".")

# Set to an integer (e.g. 5, 20, 30) to force a fixed search count, or None to auto-detect
CUSTOM_SEARCH_COUNT = None

# Automatically create and run the browser on a new Windows Virtual Desktop
USE_VIRTUAL_DESKTOP = True

# Automatically switch back to your main desktop so you can keep working undisturbed
SWITCH_BACK_TO_MAIN_DESKTOP = True