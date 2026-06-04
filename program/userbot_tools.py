import asyncio
from driver.clients import user
from pyrogram.types import Message
from pyrogram import Client, filters
from config import BOT_USERNAME, SUDO_USERS
from driver.filters import command, other_filters
from pyrogram.errors import UserAlreadyParticipant, UserNotParticipant, PeerIdInvalid
from driver.decorators import authorized_users_only, sudo_users_only


@Client.on_message(
    command(["userbotjoin", f"userbotjoin@{BOT_USERNAME}"]) & ~filters.private & ~filters.bot
)
@authorized_users_only
async def join_chat(c: Client, m: Message):
    chat_id = m.chat.id
    ubot_id = (await user.get_me()).id
    # make the assistant join (ignore if it's already a member)
    try:
        invite_link = await m.chat.export_invite_link()
        if "+" in invite_link:
            link_hash = (invite_link.replace("+", "")).split("t.me/")[1]
            await user.join_chat(f"https://t.me/joinchat/{link_hash}")
        else:
            await user.join_chat(invite_link)
    except UserAlreadyParticipant:
        pass
    except Exception as e:
        return await m.reply(f"❌ **userbot failed to join**\n\n**reason**: `{e}`")
    # optional: promote it (needed only for /volume). The bot may not be able to
    # resolve the assistant peer yet (PeerIdInvalid) — that's fine, skip quietly.
    try:
        await m.chat.promote_member(ubot_id, can_manage_voice_chats=True)
    except (PeerIdInvalid, Exception):
        pass
    return await user.send_message(chat_id, "✅ userbot is in the chat")


@Client.on_message(command(["userbotleave",
                            f"leave@{BOT_USERNAME}"]) & filters.group & ~filters.edited
)
@authorized_users_only
async def leave_chat(_, m: Message):
    chat_id = m.chat.id
    try:
        await user.leave_chat(chat_id)
        return await _.send_message(
            chat_id,
            "✅ userbot leaved chat",
        )
    except UserNotParticipant:
        return await _.send_message(
            chat_id,
            "❌ userbot already leave chat",
        )


@Client.on_message(command(["leaveall", f"leaveall@{BOT_USERNAME}"]))
@sudo_users_only
async def leave_all(client, message):
    if message.from_user.id not in SUDO_USERS:
        return

    left = 0
    failed = 0
    lol = await message.reply("🔄 **userbot** leaving all chats !")
    async for dialog in user.iter_dialogs():
        try:
            await user.leave_chat(dialog.chat.id)
            left += 1
            await lol.edit(
                f"Userbot leaving all group...\n\nLeft: {left} chats.\nFailed: {failed} chats."
            )
        except BaseException:
            failed += 1
            await lol.edit(
                f"Userbot leaving...\n\nLeft: {left} chats.\nFailed: {failed} chats."
            )
        await asyncio.sleep(0.7)
    await client.send_message(
        message.chat.id, f"✅ Left from: {left} chats.\n❌ Failed in: {failed} chats."
    )


@Client.on_message(filters.left_chat_member)
async def ubot_leave(c: Client, m: Message):
    ass_id = (await user.get_me()).id
    bot_id = (await c.get_me()).id
    chat_id = m.chat.id
    left_member = m.left_chat_member
    if left_member.id == bot_id:
        await user.leave_chat(chat_id)
    elif left_member.id == ass_id:
        await c.leave_chat(chat_id)
