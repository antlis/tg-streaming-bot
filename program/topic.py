import logging

from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.types import Message

from config import BOT_USERNAME
from driver.filters import command, other_filters
from driver.queues import clear_topic_lock, set_topic_lock, TOPIC_LOCK
from driver.utils import can_manage_vc

log = logging.getLogger(__name__)


@Client.on_message(command(["topic", f"topic@{BOT_USERNAME}"]) & other_filters)
async def cmd_topic(c: Client, m: Message):
    """/topic lock|unlock|status — restrict the bot to one forum topic in this
    group. /topic lock, sent from inside the desired topic, confines the bot
    to that topic here; every other topic (including "General") is then
    ignored. /topic unlock lifts it. /topic itself is always reachable
    regardless of the current lock (see driver/filters.py), so a chat can't
    get stuck."""
    chat = m.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.FORUM):
        return await m.reply("topic locking only applies to group chats with forum topics enabled.")

    args = m.command[1:]
    action = args[0].lower() if args else "status"

    if action in ("lock", "unlock"):
        member = await c.get_chat_member(chat.id, m.from_user.id)
        if not can_manage_vc(member):
            return await m.reply("💡 admins (manage video chats) only.")

    if action == "lock":
        if not m.is_topic_message:
            return await m.reply(
                "send `/topic lock` from inside the topic you want the bot restricted to "
                "(this was sent outside a topic, e.g. in \"General\")."
            )
        set_topic_lock(chat.id, m.message_thread_id)
        log.info("topic lock set: chat=%s topic=%s by=%s", chat.id, m.message_thread_id, m.from_user.id)
        await m.reply(
            f"🔒 **Bot restricted to this topic** (id `{m.message_thread_id}`) in this group.\n"
            "Other topics, including General, are now ignored. `/topic unlock` to undo."
        )
    elif action == "unlock":
        clear_topic_lock(chat.id)
        log.info("topic lock cleared: chat=%s by=%s", chat.id, m.from_user.id)
        await m.reply("🔓 **Topic restriction removed** — the bot now responds in every topic here.")
    elif action == "status":
        locked = TOPIC_LOCK.get(chat.id)
        if locked is None:
            await m.reply("no topic restriction set for this group.")
        else:
            await m.reply(f"🔒 restricted to topic id `{locked}`.")
    else:
        await m.reply("usage: `/topic lock|unlock|status`")
