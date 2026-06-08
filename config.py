import os
from os import getenv
from dotenv import load_dotenv

if os.path.exists("local.env"):
    load_dotenv("local.env")

load_dotenv()

# Fallback card image (local file shipped with the repo). Override with your own
# ALIVE_IMG / IMG_1..IMG_4 URLs or file paths in .env.
_DEFAULT_IMG = "driver/source/LightBlue.png"

admins = {}
SESSION_NAME = getenv("SESSION_NAME", "session")
BOT_TOKEN = getenv("BOT_TOKEN")
BOT_NAME = getenv("BOT_NAME", "Music Bot")
API_ID = int(getenv("API_ID"))
API_HASH = getenv("API_HASH")
OWNER_NAME = getenv("OWNER_NAME", "")
ALIVE_NAME = getenv("ALIVE_NAME", "")
BOT_USERNAME = getenv("BOT_USERNAME", "")
ASSISTANT_NAME = getenv("ASSISTANT_NAME", "assistant")
GROUP_SUPPORT = getenv("GROUP_SUPPORT", "")
UPDATES_CHANNEL = getenv("UPDATES_CHANNEL", "")
SUDO_USERS = list(map(int, getenv("SUDO_USERS").split()))
COMMAND_PREFIXES = list(getenv("COMMAND_PREFIXES", "/ ! .").split())
ALIVE_IMG = getenv("ALIVE_IMG", _DEFAULT_IMG)
DURATION_LIMIT = int(getenv("DURATION_LIMIT", "540000"))
# Max size of the downloads cache in MB; pruned (oldest first) after each
# stream ends. 0 = delete everything not queued as soon as a stream ends.
DOWNLOADS_CACHE_LIMIT_MB = int(getenv("DOWNLOADS_CACHE_LIMIT_MB", "4096"))
# Local media library root (in-container path; mount your host folder there).
# Each immediate subfolder becomes a browsable category. Empty = feature off.
LIBRARY_ROOT = getenv("LIBRARY_ROOT", "")
# Optional allowlist of top-level category folders to expose (comma-separated).
# Empty = show every subfolder of LIBRARY_ROOT.
LIBRARY_CATEGORIES = [c.strip() for c in getenv("LIBRARY_CATEGORIES", "").split(",") if c.strip()]
# Hardware-accelerated transcoding. "" = CPU (libx264). "vaapi" = Intel/AMD GPU
# H.264 encode via /dev/dri (needs the device mounted + VA drivers in the image).
TRANSCODE_HWACCEL = getenv("TRANSCODE_HWACCEL", "").strip().lower()
UPSTREAM_REPO = getenv("UPSTREAM_REPO", "")
IMG_1 = getenv("IMG_1", _DEFAULT_IMG)
IMG_2 = getenv("IMG_2", _DEFAULT_IMG)
IMG_3 = getenv("IMG_3", _DEFAULT_IMG)
IMG_4 = getenv("IMG_4", _DEFAULT_IMG)
