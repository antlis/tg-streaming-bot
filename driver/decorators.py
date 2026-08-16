import functools
import logging
from typing import Callable
from pyrogram import Client
from pyrogram.types import CallbackQuery, Message
from config import SUDO_USERS
from driver.admins import get_administrators

log = logging.getLogger(__name__)


def errors(func: Callable) -> Callable:
    """Catch anything a message handler raises, log it, and tell the user —
    otherwise it's swallowed by pyrogram's dispatcher and the bot just goes
    silent with no visible reply."""
    @functools.wraps(func)
    async def decorator(client: Client, message: Message):
        try:
            return await func(client, message)
        except Exception as e:
            log.exception("handler %s failed", func.__name__)
            try:
                await message.reply(f"⚠️ {type(e).__name__}: {e}"[:500])
            except Exception:
                pass

    return decorator


def errors_cb(func: Callable) -> Callable:
    """Same as `errors`, for callback-query handlers — reports via a toast
    (query.answer) instead of a chat reply."""
    @functools.wraps(func)
    async def decorator(client: Client, query: CallbackQuery):
        try:
            return await func(client, query)
        except Exception as e:
            log.exception("callback handler %s failed", func.__name__)
            try:
                await query.answer(f"⚠️ {type(e).__name__}: {e}"[:200], show_alert=True)
            except Exception:
                pass

    return decorator


def authorized_users_only(func: Callable) -> Callable:
    async def decorator(client: Client, message: Message):
        if message.from_user.id in SUDO_USERS:
            return await func(client, message)

        # pyrogram's message-cache can mutate a Message's .command back to
        # None once another handler's filter check runs against the same
        # cached object concurrently — easily triggered by this await. Snapshot
        # it first and restore it so the wrapped handler sees it intact.
        cmd_snapshot = message.command
        administrators = await get_administrators(message.chat)
        message.command = cmd_snapshot

        for administrator in administrators:
            if administrator == message.from_user.id:
                return await func(client, message)

    return decorator


def sudo_users_only(func: Callable) -> Callable:
    async def decorator(client: Client, message: Message):
        if message.from_user.id in SUDO_USERS:
            return await func(client, message)

    return decorator


def humanbytes(size):
    """Convert Bytes To Bytes So That Human Can Read It"""
    if not size:
        return ""
    power = 2 ** 10
    raised_to_pow = 0
    dict_power_n = {0: "", 1: "Ki", 2: "Mi", 3: "Gi", 4: "Ti"}
    while size > power:
        size /= power
        raised_to_pow += 1
    return str(round(size, 2)) + " " + dict_power_n[raised_to_pow] + "B"
