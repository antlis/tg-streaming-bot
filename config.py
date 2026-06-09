import os
from os import getenv
from dotenv import load_dotenv

if os.path.exists("local.env"):
    load_dotenv("local.env")

load_dotenv()

# ---- fail fast on missing/invalid required config, with a clear message ----
_REQUIRED = ("API_ID", "API_HASH", "BOT_TOKEN", "BOT_USERNAME", "SESSION_NAME", "SUDO_USERS")
_missing = [k for k in _REQUIRED if not (getenv(k) or "").strip()]
if _missing:
    raise SystemExit(
        "❌ Missing required environment variable(s): "
        + ", ".join(_missing)
        + "\n   Copy example.env to .env and fill in the Required block (see README → Setup)."
    )

# Fallback card image (local file shipped with the repo). Override with your own
# ALIVE_IMG / IMG_1..IMG_4 URLs or file paths in .env.
_DEFAULT_IMG = "driver/source/LightBlue.png"

admins = {}
SESSION_NAME = getenv("SESSION_NAME", "session")
BOT_TOKEN = getenv("BOT_TOKEN")
BOT_NAME = getenv("BOT_NAME", "Music Bot")
try:
    API_ID = int(getenv("API_ID"))
except ValueError:
    raise SystemExit("❌ API_ID must be the numeric app id from my.telegram.org.")
API_HASH = getenv("API_HASH")
OWNER_NAME = getenv("OWNER_NAME", "")
ALIVE_NAME = getenv("ALIVE_NAME", "")
BOT_USERNAME = getenv("BOT_USERNAME", "")
ASSISTANT_NAME = getenv("ASSISTANT_NAME", "assistant")
GROUP_SUPPORT = getenv("GROUP_SUPPORT", "")
UPDATES_CHANNEL = getenv("UPDATES_CHANNEL", "")
try:
    SUDO_USERS = list(map(int, getenv("SUDO_USERS").split()))
except ValueError:
    raise SystemExit("❌ SUDO_USERS must be space-separated numeric Telegram user ids.")
COMMAND_PREFIXES = list(getenv("COMMAND_PREFIXES", "/ ! .").split())
ALIVE_IMG = getenv("ALIVE_IMG", _DEFAULT_IMG)
DURATION_LIMIT = int(getenv("DURATION_LIMIT", "540000"))
# Max size of the downloads cache in MB; pruned (oldest first) after each
# stream ends. 0 = delete everything not queued as soon as a stream ends.
DOWNLOADS_CACHE_LIMIT_MB = int(getenv("DOWNLOADS_CACHE_LIMIT_MB", "4096"))
# Leave the voice chat after this many minutes with no human listeners. 0 = off.
IDLE_LEAVE_MINUTES = int(getenv("IDLE_LEAVE_MINUTES", "10"))
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
# Card image shown while streaming radio (URL or file path). Defaults to IMG_1.
RADIO_IMG = getenv("RADIO_IMG", "") or IMG_1

# ---- abuse guards ----
# Per-user command rate limit: at most RATE_LIMIT_MAX commands per
# RATE_LIMIT_WINDOW seconds (sudo users are exempt). Set MAX to 0 to disable.
RATE_LIMIT_MAX = int(getenv("RATE_LIMIT_MAX", "5"))
RATE_LIMIT_WINDOW = int(getenv("RATE_LIMIT_WINDOW", "10"))
# Max upcoming tracks in a chat's queue (the now-playing item doesn't count).
# 0 = unlimited.
MAX_QUEUE_SIZE = int(getenv("MAX_QUEUE_SIZE", "50"))

# yt-dlp SponsorBlock — comma-separated categories to cut from YouTube downloads,
# e.g. "sponsor,selfpromo,interaction,intro,outro,music_offtopic". Empty = off.
SPONSORBLOCK_REMOVE = getenv("SPONSORBLOCK_REMOVE", "").strip()
