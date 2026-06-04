from datetime import datetime
from sys import version_info
from time import time

from config import (
    ALIVE_IMG,
    ALIVE_NAME,
    BOT_NAME,
    BOT_USERNAME,
    GROUP_SUPPORT,
    OWNER_NAME,
    UPDATES_CHANNEL,
)
from program import __version__
from driver.clients import user
from driver.filters import command, other_filters
from pyrogram import Client, filters
from pyrogram import __version__ as pyrover
from pytgcalls import (__version__ as pytover)
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

__major__ = 0
__minor__ = 2
__micro__ = 1

__python_version__ = f"{version_info[0]}.{version_info[1]}.{version_info[2]}"


START_TIME = datetime.utcnow()
START_TIME_ISO = START_TIME.replace(microsecond=0).isoformat()
TIME_DURATION_UNITS = (
    ("week", 60 * 60 * 24 * 7),
    ("day", 60 * 60 * 24),
    ("hour", 60 * 60),
    ("min", 60),
    ("sec", 1),
)


async def _human_time_duration(seconds):
    if seconds == 0:
        return "inf"
    parts = []
    for unit, div in TIME_DURATION_UNITS:
        amount, seconds = divmod(int(seconds), div)
        if amount > 0:
            parts.append("{} {}{}".format(amount, unit, "" if amount == 1 else "s"))
    return ", ".join(parts)


@Client.on_message(
    command(["start", f"start@{BOT_USERNAME}"]) & filters.private
)
async def start_(client: Client, message: Message):
    rows = [
        [
            InlineKeyboardButton(
                "➕ Add me to your Group ➕",
                url=f"https://t.me/{BOT_USERNAME}?startgroup=true",
            )
        ],
        [InlineKeyboardButton("❓ Basic Guide", callback_data="cbhowtouse")],
        [InlineKeyboardButton("📚 Commands", callback_data="cbcmds")]
        + (
            [InlineKeyboardButton("❤️ info", url=f"https://t.me/{OWNER_NAME}")]
            if OWNER_NAME
            else []
        ),
    ]
    socials = []
    if GROUP_SUPPORT:
        socials.append(
            InlineKeyboardButton("👥 Official Group", url=f"https://t.me/{GROUP_SUPPORT}")
        )
    if UPDATES_CHANNEL:
        socials.append(
            InlineKeyboardButton("📣 Official Channel", url=f"https://t.me/{UPDATES_CHANNEL}")
        )
    if socials:
        rows.append(socials)
    await message.reply_text(
        f"""✨ **Welcome {message.from_user.mention()} !**\n
💭 [{BOT_NAME}](https://t.me/{BOT_USERNAME}) **Allows you to play music and video on groups through the new Telegram's video chats!**

💡 **Find out all the Bot's commands and how they work by clicking on the » 📚 Commands button!**

🔖 **To know how to use this bot, please click on the » ❓ Basic Guide button!**
""",
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )


@Client.on_message(
    command(["alive", f"alive@{BOT_USERNAME}"]) & filters.group
)
async def alive(client: Client, message: Message):
    current_time = datetime.utcnow()
    uptime_sec = (current_time - START_TIME).total_seconds()
    uptime = await _human_time_duration(int(uptime_sec))

    links = []
    if GROUP_SUPPORT:
        links.append(InlineKeyboardButton("✨ Group", url=f"https://t.me/{GROUP_SUPPORT}"))
    if UPDATES_CHANNEL:
        links.append(
            InlineKeyboardButton("📣 Channel", url=f"https://t.me/{UPDATES_CHANNEL}")
        )
    keyboard = InlineKeyboardMarkup([links]) if links else None

    if ALIVE_NAME and OWNER_NAME:
        master = f"\n🍀 My Master: [{ALIVE_NAME}](https://t.me/{OWNER_NAME})"
    elif ALIVE_NAME:
        master = f"\n🍀 My Master: {ALIVE_NAME}"
    else:
        master = ""

    alive = f"**Hello {message.from_user.mention()}, i'm {BOT_NAME}**\n\n✨ Bot is working normally{master}\n✨ Bot Version: `v{__version__}`\n🍀 Pyrogram Version: `{pyrover}`\n✨ Python Version: `{__python_version__}`\n🍀 PyTgCalls version: `{pytover.__version__}`\n✨ Uptime Status: `{uptime}`\n\n**Thanks for Adding me here, for playing video & music on your Group's video chat** ❤"

    await message.reply_photo(
        photo=f"{ALIVE_IMG}",
        caption=alive,
        reply_markup=keyboard,
    )


@Client.on_message(command(["ping", f"ping@{BOT_USERNAME}"]))
async def ping_pong(client: Client, message: Message):
    start = time()
    m_reply = await message.reply_text("pinging...")
    delta_ping = time() - start
    await m_reply.edit_text("🏓 `PONG!!`\n" f"⚡️ `{delta_ping * 1000:.3f} ms`")


@Client.on_message(command(["uptime", f"uptime@{BOT_USERNAME}"]))
async def get_uptime(client: Client, message: Message):
    current_time = datetime.utcnow()
    uptime_sec = (current_time - START_TIME).total_seconds()
    uptime = await _human_time_duration(int(uptime_sec))
    await message.reply_text(
        "🤖 bot status:\n"
        f"• **uptime:** `{uptime}`\n"
        f"• **start time:** `{START_TIME_ISO}`"
    )


@Client.on_message(filters.new_chat_members)
async def new_chat(c: Client, m: Message):
    ass_uname = (await user.get_me()).username
    bot_id = (await c.get_me()).id
    rows = []
    links = []
    if UPDATES_CHANNEL:
        links.append(InlineKeyboardButton("📣 Channel", url=f"https://t.me/{UPDATES_CHANNEL}"))
    if GROUP_SUPPORT:
        links.append(InlineKeyboardButton("💭 Support", url=f"https://t.me/{GROUP_SUPPORT}"))
    if links:
        rows.append(links)
    if ass_uname:
        rows.append([InlineKeyboardButton("👤 Assistant", url=f"https://t.me/{ass_uname}")])
    for member in m.new_chat_members:
        if member.id == bot_id:
            return await m.reply(
                "❤️ Thanks for adding me to the **Group** !\n\n"
                "Appoint me as administrator in the **Group**, otherwise I will not be able to work properly, and don't forget to type `/userbotjoin` for invite the assistant.\n\n"
                "Once done, then type `/reload`",
                reply_markup=InlineKeyboardMarkup(rows) if rows else None,
            )
