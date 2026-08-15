from time import monotonic
from typing import List, Union

from pyrogram import filters
from pyrogram.enums import ChatType

from config import BOT_USERNAME, COMMAND_PREFIXES, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW, SUDO_USERS
from driver.queues import TOPIC_LOCK


# /topic itself must always get through, locked or not — otherwise a chat that
# locks the "wrong" topic (or the one someone later deletes) can't undo it.
_topic_cmd = filters.command(["topic", f"topic@{BOT_USERNAME}"], COMMAND_PREFIXES)


async def _topic_ok(_, client, m):
    if not TOPIC_LOCK or m.chat is None or m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.FORUM):
        return True
    locked = TOPIC_LOCK.get(m.chat.id)
    if locked is None:
        return True
    if await _topic_cmd(client, m):
        return True
    thread_id = m.message_thread_id if getattr(m, "topic_message", False) else None
    return thread_id == locked


topic_ok = filters.create(_topic_ok)

other_filters = filters.group & ~filters.via_bot & ~filters.forwarded & topic_ok
other_filters2 = (
    filters.private & ~filters.via_bot & ~filters.forwarded
)


# Per-user sliding-window command rate limit. Excess commands are silently
# dropped (the handler simply doesn't fire) so a spammer can't flood the bot.
_cmd_history = {}  # user_id -> [recent command timestamps]


async def _rate_ok(_, __, m):
    if not RATE_LIMIT_MAX:
        return True
    user = m.from_user
    if user is None or user.id in SUDO_USERS:
        return True
    now = monotonic()
    cutoff = now - RATE_LIMIT_WINDOW
    hist = _cmd_history.setdefault(user.id, [])
    while hist and hist[0] < cutoff:
        hist.pop(0)
    if len(hist) >= RATE_LIMIT_MAX:
        return False
    hist.append(now)
    return True


rate_limit = filters.create(_rate_ok)


def command(commands: Union[str, List[str]]):
    return filters.command(commands, COMMAND_PREFIXES) & rate_limit
