import os

from config import API_HASH, API_ID, BOT_TOKEN, SESSION_NAME
from pyrogram import Client
from pytgcalls import PyTgCalls

# Optional proxy support (for networks where Telegram is filtered).
# Set PROXY_HOST/PROXY_PORT in the env to enable; scheme defaults to socks5.
# Requires pysocks (already in requirements). Leave unset for a direct connection.
PROXY = None
if os.getenv("PROXY_HOST"):
    PROXY = {
        "scheme": os.getenv("PROXY_SCHEME", "socks5"),
        "hostname": os.getenv("PROXY_HOST"),
        "port": int(os.getenv("PROXY_PORT", "1080")),
    }
    if os.getenv("PROXY_USER"):
        PROXY["username"] = os.getenv("PROXY_USER")
        PROXY["password"] = os.getenv("PROXY_PASS")

# The bot session is persisted (downloads/ is a volume) so container restarts
# reuse it instead of re-running auth.ImportBotAuthorization every boot —
# repeated re-auths trip Telegram's FloodWait on the token.
bot = Client(
    "bot",
    API_ID,
    API_HASH,
    bot_token=BOT_TOKEN,
    plugins={"root": "program"},
    proxy=PROXY,
    workdir="downloads",
)

user = Client(
    "assistant",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_NAME,
    proxy=PROXY,
)

call_py = PyTgCalls(user)
