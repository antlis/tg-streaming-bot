from cache.admins import admins
from cache.admins import delete as invalidate_admin_cache
from driver.clients import call_py
from pyrogram import Client, filters
from driver.decorators import authorized_users_only
from driver.filters import command, other_filters
import random
from driver.queues import QUEUE, clear_queue, get_queue, is_loop, set_loop, is_autoplay, set_autoplay
import os

from driver.utils import (
    skip_current_song, skip_item, can_manage_vc, replay_at_gain,
    seek_current, capture_frame, probe_duration,
)
from config import BOT_USERNAME, GROUP_SUPPORT, IMG_3, UPDATES_CHANNEL
from pyrogram.enums import ChatMembersFilter
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


bttn = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🔙 Go Back", callback_data="cbmenu")]]
)


bcl = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🗑 Close", callback_data="cls")]]
)

# Per-chat current volume (0-200) so the 🔉/🔊 buttons can step relative to it.
VOLUME = {}
VOLUME_STEP = 20


@Client.on_message(command(["reload", f"reload@{BOT_USERNAME}"]) & other_filters)
@authorized_users_only
async def update_admin(client, message):

    new_admins = []
    async for u in client.get_chat_members(
        message.chat.id, filter=ChatMembersFilter.ADMINISTRATORS
    ):
        new_admins.append(u.user.id)
    admins[message.chat.id] = new_admins
    await message.reply_text(
        "✅ Bot **reloaded correctly !**\n✅ **Admin list** has **updated !**"
    )


@Client.on_chat_member_updated()
async def _admin_change_watcher(client, event):
    # Any promotion/demotion/membership change invalidates the cached admin
    # list for that chat — it gets refetched on next use (no /reload needed).
    invalidate_admin_cache(event.chat.id)


@Client.on_message(command(["loop", f"loop@{BOT_USERNAME}", "repeat"]) & other_filters)
@authorized_users_only
async def loop_cmd(client, m: Message):
    chat_id = m.chat.id
    if chat_id not in QUEUE:
        return await m.reply("❌ **nothing is streaming**")
    on = not is_loop(chat_id)
    set_loop(chat_id, on)
    await m.reply("🔁 **Loop ON** — the current track will repeat when it ends." if on
                  else "➡️ **Loop OFF**")


@Client.on_message(command(["autoplay", f"autoplay@{BOT_USERNAME}", "autodj"]) & other_filters)
@authorized_users_only
async def autoplay_cmd(client, m: Message):
    chat_id = m.chat.id
    on = not is_autoplay(chat_id)
    set_autoplay(chat_id, on)
    await m.reply(
        "🔮 **Auto-DJ ON** — when the queue runs out I'll keep playing related "
        "YouTube tracks. (Stops when the voice chat empties.)" if on
        else "⏏️ **Auto-DJ OFF**"
    )


@Client.on_message(command(["shuffle", f"shuffle@{BOT_USERNAME}"]) & other_filters)
@authorized_users_only
async def shuffle_cmd(client, m: Message):
    chat_id = m.chat.id
    q = get_queue(chat_id)
    if not q or len(q) < 3:
        return await m.reply("❌ need at least **2 queued** tracks to shuffle.")
    upcoming = q[1:]
    random.shuffle(upcoming)
    q[1:] = upcoming
    await m.reply(f"🔀 **Shuffled** {len(upcoming)} upcoming tracks.")


@Client.on_message(command(["clear", f"clear@{BOT_USERNAME}", "clearqueue"]) & other_filters)
@authorized_users_only
async def clear_cmd(client, m: Message):
    chat_id = m.chat.id
    q = get_queue(chat_id)
    if not q or len(q) < 2:
        return await m.reply("❌ the queue is empty (nothing upcoming to clear).")
    removed = len(q) - 1
    del q[1:]
    await m.reply(f"🗑 **Cleared {removed} upcoming track(s)** — current keeps playing.")


@Client.on_message(command(["skip", f"skip@{BOT_USERNAME}", "vskip"]) & other_filters)
@authorized_users_only
async def skip(client, m: Message):

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="• Mᴇɴᴜ", callback_data="cbmenu"
                ),
                InlineKeyboardButton(
                    text="• Cʟᴏsᴇ", callback_data="cls"
                ),
            ]
        ]
    )

    chat_id = m.chat.id
    if len(m.command) < 2:
        op = await skip_current_song(chat_id)
        if op == 0:
            await m.reply("❌ nothing is currently playing")
        elif op == 1:
            await m.reply("✅ __Queues__ **is empty.**\n\n**• userbot leaving voice chat**")
        elif op == 2:
            await m.reply("🗑️ **Clearing the Queues**\n\n**• userbot leaving voice chat**")
        else:
            await m.reply_photo(
                photo=f"{IMG_3}",
                caption=f"⏭ **Skipped to the next track.**\n\n🏷 **Name:** [{op[0]}]({op[1]})\n💭 **Chat:** `{chat_id}`\n💡 **Status:** `Playing`\n🎧 **Request by:** {m.from_user.mention()}",
                reply_markup=keyboard,
            )
    else:
        skip = m.text.split(None, 1)[1]
        OP = "🗑 **removed song from queue:**"
        if chat_id in QUEUE:
            items = [int(x) for x in skip.split(" ") if x.isdigit()]
            items.sort(reverse=True)
            for x in items:
                if x == 0:
                    pass
                else:
                    hm = await skip_item(chat_id, x)
                    if hm == 0:
                        pass
                    else:
                        OP = OP + "\n" + f"**#{x}** - {hm}"
            await m.reply(OP)


@Client.on_message(
    command(["stop", f"stop@{BOT_USERNAME}", "end", f"end@{BOT_USERNAME}", "vstop"])
    & other_filters
)
@authorized_users_only
async def stop(client, m: Message):
    chat_id = m.chat.id
    if chat_id in QUEUE:
        try:
            await call_py.leave_call(chat_id)
            clear_queue(chat_id)
            await m.reply("✅ The userbot has disconnected from the video chat.")
        except Exception as e:
            await m.reply(f"🚫 **error:**\n\n`{e}`")
    else:
        await m.reply("❌ **nothing is streaming**")


@Client.on_message(
    command(["pause", f"pause@{BOT_USERNAME}", "vpause"]) & other_filters
)
@authorized_users_only
async def pause(client, m: Message):
    chat_id = m.chat.id
    if chat_id in QUEUE:
        try:
            await call_py.pause(chat_id)
            await m.reply(
                "⏸ **Track paused.**\n\n• **To resume the stream, use the**\n» /resume command."
            )
        except Exception as e:
            await m.reply(f"🚫 **error:**\n\n`{e}`")
    else:
        await m.reply("❌ **nothing in streaming**")


@Client.on_message(
    command(["resume", f"resume@{BOT_USERNAME}", "vresume"]) & other_filters
)
@authorized_users_only
async def resume(client, m: Message):
    chat_id = m.chat.id
    if chat_id in QUEUE:
        try:
            await call_py.resume(chat_id)
            await m.reply(
                "▶️ **Track resumed.**\n\n• **To pause the stream, use the**\n» /pause command."
            )
        except Exception as e:
            await m.reply(f"🚫 **error:**\n\n`{e}`")
    else:
        await m.reply("❌ **nothing in streaming**")


@Client.on_message(
    command(["mute", f"mute@{BOT_USERNAME}", "vmute"]) & other_filters
)
@authorized_users_only
async def mute(client, m: Message):
    chat_id = m.chat.id
    if chat_id in QUEUE:
        try:
            # mute = master volume 0 (re-feed), not a media-layer mute — keeps the
            # stream active so muting a video doesn't make Telegram blur it.
            await replay_at_gain(chat_id, 0)
            await m.reply("🔇 **Muted** (video stays sharp). » /unmute to restore.")
        except Exception as e:
            await m.reply(f"🚫 **error:**\n\n`{e}`")
    else:
        await m.reply("❌ **nothing in streaming**")


@Client.on_message(
    command(["unmute", f"unmute@{BOT_USERNAME}", "vunmute"]) & other_filters
)
@authorized_users_only
async def unmute(client, m: Message):
    chat_id = m.chat.id
    if chat_id in QUEUE:
        try:
            vol = VOLUME.get(chat_id, 100)
            await replay_at_gain(chat_id, vol)
            await m.reply(f"🔊 **Unmuted** ({vol}%). » /mute to silence.")
        except Exception as e:
            await m.reply(f"🚫 **error:**\n\n`{e}`")
    else:
        await m.reply("❌ **nothing in streaming**")


@Client.on_callback_query(filters.regex("cbpause"))
async def cbpause(_, query: CallbackQuery):
    if query.message.sender_chat:
        return await query.answer("you're an Anonymous Admin !\n\n» revert back to user account from admin rights.")
    a = await _.get_chat_member(query.message.chat.id, query.from_user.id)
    if not can_manage_vc(a):
        return await query.answer("💡 only admin with manage voice chats permission that can tap this button !", show_alert=True)
    chat_id = query.message.chat.id
    if chat_id in QUEUE:
        try:
            await call_py.pause(chat_id)
            await query.answer("⏸ paused")
        except Exception as e:
            await query.answer(f"🚫 error: {e}"[:190], show_alert=True)
    else:
        await query.answer("❌ nothing is currently streaming", show_alert=True)


@Client.on_callback_query(filters.regex("cbresume"))
async def cbresume(_, query: CallbackQuery):
    if query.message.sender_chat:
        return await query.answer("you're an Anonymous Admin !\n\n» revert back to user account from admin rights.")
    a = await _.get_chat_member(query.message.chat.id, query.from_user.id)
    if not can_manage_vc(a):
        return await query.answer("💡 only admin with manage voice chats permission that can tap this button !", show_alert=True)
    chat_id = query.message.chat.id
    if chat_id in QUEUE:
        try:
            await call_py.resume(chat_id)
            await query.answer("▶️ resumed")
        except Exception as e:
            await query.answer(f"🚫 error: {e}"[:190], show_alert=True)
    else:
        await query.answer("❌ nothing is currently streaming", show_alert=True)


@Client.on_callback_query(filters.regex("cbstop"))
async def cbstop(_, query: CallbackQuery):
    if query.message.sender_chat:
        return await query.answer("you're an Anonymous Admin !\n\n» revert back to user account from admin rights.")
    a = await _.get_chat_member(query.message.chat.id, query.from_user.id)
    if not can_manage_vc(a):
        return await query.answer("💡 only admin with manage voice chats permission that can tap this button !", show_alert=True)
    chat_id = query.message.chat.id
    if chat_id in QUEUE:
        try:
            await call_py.leave_call(chat_id)
            clear_queue(chat_id)
            await query.answer("⏹ stopped — left the voice chat")
        except Exception as e:
            await query.answer(f"🚫 error: {e}"[:190], show_alert=True)
    else:
        await query.answer("❌ nothing is currently streaming", show_alert=True)


@Client.on_callback_query(filters.regex("cbskip"))
async def cbskip(_, query: CallbackQuery):
    if query.message.sender_chat:
        return await query.answer("you're an Anonymous Admin !\n\n» revert back to user account from admin rights.")
    a = await _.get_chat_member(query.message.chat.id, query.from_user.id)
    if not can_manage_vc(a):
        return await query.answer("💡 only admin with manage voice chats permission that can tap this button !", show_alert=True)
    chat_id = query.message.chat.id
    if chat_id in QUEUE:
        try:
            op = await skip_current_song(chat_id)
            if op == 1:
                await query.answer("⏭ queue empty — streaming ended")
            elif op == 2:
                await query.answer("❌ error — cleared queue & left the vc", show_alert=True)
            else:
                await query.answer(f"⏭ now playing: {op[0]}"[:190])
        except Exception as e:
            await query.answer(f"🚫 error: {e}"[:190], show_alert=True)
    else:
        await query.answer("❌ nothing is currently streaming", show_alert=True)


@Client.on_callback_query(filters.regex("cbmute"))
async def cbmute(_, query: CallbackQuery):
    if query.message.sender_chat:
        return await query.answer("you're an Anonymous Admin !\n\n» revert back to user account from admin rights.")
    a = await _.get_chat_member(query.message.chat.id, query.from_user.id)
    if not can_manage_vc(a):
        return await query.answer("💡 only admin with manage voice chats permission that can tap this button !", show_alert=True)
    chat_id = query.message.chat.id
    if chat_id in QUEUE:
        try:
            await query.answer("🔇 muting…")
            await replay_at_gain(chat_id, 0)
        except Exception as e:
            await query.answer(f"🚫 error: {e}"[:190], show_alert=True)
    else:
        await query.answer("❌ nothing is currently streaming", show_alert=True)


@Client.on_callback_query(filters.regex("cbunmute"))
async def cbunmute(_, query: CallbackQuery):
    if query.message.sender_chat:
        return await query.answer("you're an Anonymous Admin !\n\n» revert back to user account from admin rights.")
    a = await _.get_chat_member(query.message.chat.id, query.from_user.id)
    if not can_manage_vc(a):
        return await query.answer("💡 only admin with manage voice chats permission that can tap this button !", show_alert=True)
    chat_id = query.message.chat.id
    if chat_id in QUEUE:
        try:
            vol = VOLUME.get(chat_id, 100)
            await query.answer(f"🔊 {vol}% — re-buffering…")
            await replay_at_gain(chat_id, vol)
        except Exception as e:
            await query.answer(f"🚫 error: {e}"[:190], show_alert=True)
    else:
        await query.answer("❌ nothing is currently streaming", show_alert=True)


@Client.on_message(
    command(["volume", f"volume@{BOT_USERNAME}", "vol"]) & other_filters
)
@authorized_users_only
async def change_volume(client, m: Message):
    chat_id = m.chat.id
    if len(m.command) < 2:
        return await m.reply("» **/volume 0-200**")
    if chat_id not in QUEUE:
        return await m.reply("❌ **nothing in streaming**")
    try:
        vol = max(0, min(200, int(m.command[1])))
    except ValueError:
        return await m.reply("» **/volume 0-200**")
    try:
        VOLUME[chat_id] = vol
        ok = await replay_at_gain(chat_id, vol)
        if ok:
            await m.reply(f"✅ **volume set to** `{vol}`% _(brief re-buffer)_")
        else:
            await m.reply("❌ couldn't apply volume — nothing is playing.")
    except Exception as e:
        await m.reply(f"🚫 **error:**\n\n`{e}`")


async def _step_volume(_, query: CallbackQuery, delta: int):
    if query.message.sender_chat:
        return await query.answer("you're an Anonymous Admin !\n\n» revert back to user account from admin rights.")
    a = await _.get_chat_member(query.message.chat.id, query.from_user.id)
    if not can_manage_vc(a):
        return await query.answer("💡 only admin with manage voice chats permission that can tap this button !", show_alert=True)
    chat_id = query.message.chat.id
    if chat_id not in QUEUE:
        return await query.answer("❌ nothing is currently streaming", show_alert=True)
    new_vol = max(0, min(200, VOLUME.get(chat_id, 100) + delta))
    try:
        VOLUME[chat_id] = new_vol
        await query.answer(f"🔊 volume {new_vol}% — re-buffering…")
        await replay_at_gain(chat_id, new_vol)
    except Exception as e:
        await query.answer(f"🚫 error: {e}"[:190], show_alert=True)


@Client.on_callback_query(filters.regex("cbvolup"))
async def cbvolup(_, query: CallbackQuery):
    await _step_volume(_, query, VOLUME_STEP)


@Client.on_callback_query(filters.regex("cbvoldown"))
async def cbvoldown(_, query: CallbackQuery):
    await _step_volume(_, query, -VOLUME_STEP)


@Client.on_callback_query(filters.regex(r"^seekp:"))
async def cbseekpercent(_, query: CallbackQuery):
    if query.message.sender_chat:
        return await query.answer("you're an Anonymous Admin !\n\n» revert back to user account from admin rights.")
    a = await _.get_chat_member(query.message.chat.id, query.from_user.id)
    if not can_manage_vc(a):
        return await query.answer("💡 only admin with manage voice chats permission that can tap this button !", show_alert=True)
    chat_id = query.message.chat.id
    if chat_id not in QUEUE:
        return await query.answer("❌ nothing is currently streaming", show_alert=True)
    src = QUEUE[chat_id][0][1]
    if str(src).startswith("http"):
        return await query.answer("⛔ can't seek a live stream", show_alert=True)
    try:
        pct = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        return await query.answer("bad seek", show_alert=True)
    dur = await probe_duration(src)
    if dur <= 0:
        return await query.answer("unknown duration — use /seek 12:30", show_alert=True)
    await query.answer(f"⏩ seeking to {pct}% — re-buffering…")
    await seek_current(chat_id, int(dur * pct / 100), VOLUME.get(chat_id, 100))


async def _send_screenshot(c, chat_id, fail):
    """Shared by the 📸 button and /screenshot. `fail` is an async (text)->None."""
    if chat_id not in QUEUE:
        return await fail("❌ nothing is playing.")
    if QUEUE[chat_id][0][3] != "Video":
        return await fail("📸 screenshots are for video.")
    path = await capture_frame(chat_id)
    if not path:
        return await fail("🚫 couldn't capture a frame.")
    try:
        await c.send_photo(chat_id, path, caption="📸 now playing")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@Client.on_callback_query(filters.regex(r"^snap$"))
async def cbsnap(c: Client, query: CallbackQuery):
    await query.answer("📸 capturing…")
    await _send_screenshot(c, query.message.chat.id,
                           lambda t: query.answer(t, show_alert=True))


@Client.on_message(command(["screenshot", f"screenshot@{BOT_USERNAME}", "snap"]) & other_filters)
async def screenshot_cmd(c: Client, m: Message):
    await _send_screenshot(c, m.chat.id, m.reply)
