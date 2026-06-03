import os
from time import time as _time

from config import API_HASH, API_ID, BOT_TOKEN, SESSION_NAME
from pyrogram import Client
from pytgcalls import PyTgCalls
from pyrogram.session.internals.msg_id import MsgId


# --- Fix Pyrogram 1.4.16 startup time-sync bug ---
# MsgId derives message IDs from `server_time`, which is 0 until the first
# server message is processed. The initial Ping can therefore be stamped with a
# near-zero (1970-era) msg_id, and Telegram rejects it as "msg_id too low"
# (seen reliably when connecting through a proxy/tunnel). The host clock is
# NTP-correct, so generate msg_ids straight from the local clock instead.
def _msg_id_from_local_clock(cls):
    now = _time()
    cls.msg_id_offset = cls.msg_id_offset + 4 if now == cls.last_time else 0
    msg_id = int(now * 2 ** 32) + cls.msg_id_offset
    cls.last_time = now
    return msg_id


MsgId.__new__ = _msg_id_from_local_clock

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

bot = Client(
    ":memory:",
    API_ID,
    API_HASH,
    bot_token=BOT_TOKEN,
    plugins={"root": "program"},
    proxy=PROXY,
)

user = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    proxy=PROXY,
)

call_py = PyTgCalls(user, overload_quiet_mode=True)
