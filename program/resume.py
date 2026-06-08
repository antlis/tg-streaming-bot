"""Resume an interrupted stream from where it stopped.

A background task snapshots the current track + playback position every few
seconds (RESUME[chat_id]); when a stream drops (network/tunnel hiccup) the queue
is cleared but RESUME survives, so `/continue` (or the ⏮ button) replays the
same media seeked to the saved position via ffmpeg `-ss`.
"""
import os
import asyncio

from config import BOT_USERNAME
from driver.clients import call_py, bot
from driver.queues import QUEUE, RESUME, get_queue, save_resume
from driver.filters import command, other_filters
from driver.utils import can_manage_vc, control_panel
from pytgcalls.types import MediaStream, AudioQuality, VideoQuality, Call
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

_VQ = {720: VideoQuality.HD_720p, 480: VideoQuality.SD_480p, 360: VideoQuality.SD_360p}

# Auto-recovery tuning: how long a stream may sit frozen (seconds) before it's
# treated as a silent drop, and how many auto-resume attempts before giving up.
STALL_SECONDS = 15
MAX_RECOVER = 3


def _fmt(sec):
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _media(info):
    pos = int(info["pos"])
    if info["type"] == "Video":
        return MediaStream(
            info["url"],
            audio_parameters=AudioQuality.HIGH,
            video_parameters=_VQ.get(info["Q"], VideoQuality.HD_720p),
            ffmpeg_parameters=f"-ss {pos}",
        )
    return MediaStream(
        info["url"], video_flags=MediaStream.Flags.IGNORE, ffmpeg_parameters=f"-ss {pos}"
    )


async def _auto_recover(chat_id, info, attempts):
    """Auto-resume after a detected drop/stall, with a per-chat attempt cap so a
    persistently-dead network can't spam the chat."""
    if attempts.get(chat_id, 0) >= MAX_RECOVER:
        return  # already gave up; stays quiet until the stream recovers on its own
    attempts[chat_id] = attempts.get(chat_id, 0) + 1
    try:
        res = await resume_last(chat_id)
    except Exception:
        res = None
    if res == -1:
        await bot.send_message(chat_id, "⚠️ stream dropped and the cached file expired — replay the link.")
        RESUME.pop(chat_id, None)
        attempts[chat_id] = MAX_RECOVER
        return
    if res not in (0, None):
        suffix = f" (attempt {attempts[chat_id]}/{MAX_RECOVER})" if attempts[chat_id] > 1 else ""
        await bot.send_message(
            chat_id,
            f"🔄 **connection dropped — auto-resuming** [{info['name']}]({info['link']}) from `{_fmt(info['pos'])}`{suffix}",
            disable_web_page_preview=True,
            reply_markup=control_panel,
        )


async def track_position():
    """Every 5s: snapshot the playing track + position per active chat (so a drop
    can be resumed), and act as a watchdog — if a chat's call is gone or its
    position has been frozen for STALL_SECONDS while not paused, auto-resume it.
    RESUME is separate from QUEUE, so it survives the queue being cleared."""
    seen = {}       # chat_id -> (last_pos, frozen_seconds)
    attempts = {}   # chat_id -> consecutive auto-resume attempts
    ticks = 0
    while True:
        await asyncio.sleep(5)
        ticks += 1
        if ticks % 6 == 0:   # persist resume state every ~30s
            save_resume()
        try:
            calls = await call_py.calls
        except Exception:
            calls = {}
        for chat_id in list(QUEUE.keys()):
            try:
                q = get_queue(chat_id)
                if not q:
                    continue
                head = q[0]
                call = calls.get(chat_id)

                # case 1: ntgcalls has no live call for this chat -> dropped
                if call is None:
                    seen.pop(chat_id, None)
                    await _auto_recover(chat_id, RESUME.get(chat_id, {"name": head[0], "link": head[2], "pos": 0}), attempts)
                    continue

                # don't penalise a deliberately paused stream
                if getattr(call, "status", None) == Call.Status.PAUSED:
                    seen[chat_id] = (None, 0)
                    continue

                pos = await call_py.time(chat_id)
                if pos and pos > 0:
                    RESUME[chat_id] = {
                        "name": head[0], "url": head[1], "link": head[2],
                        "type": head[3], "Q": head[4], "pos": int(pos),
                    }

                # case 2: position frozen while supposedly active -> silent stall
                prev, frozen = seen.get(chat_id, (None, 0))
                if prev is not None and pos == prev:
                    frozen += 5
                else:
                    frozen = 0
                    attempts[chat_id] = 0  # healthy progress resets the backoff
                seen[chat_id] = (pos, frozen)
                if frozen >= STALL_SECONDS:
                    seen[chat_id] = (pos, 0)
                    await _auto_recover(chat_id, RESUME.get(chat_id, {"name": head[0], "link": head[2], "pos": 0}), attempts)
            except Exception:
                pass


async def resume_last(chat_id):
    """Replay the last track from its saved position. Returns the info dict on
    success, 0 if nothing saved, -1 if the cached file is gone."""
    info = RESUME.get(chat_id)
    if not info:
        return 0
    if not (str(info["url"]).startswith("http") or os.path.exists(info["url"])):
        return -1
    await call_py.play(chat_id, _media(info))
    QUEUE[chat_id] = [[info["name"], info["url"], info["link"], info["type"], info["Q"]]]
    return info


@Client.on_message(command(["continue", "resumelast", f"continue@{BOT_USERNAME}"]) & other_filters)
async def continue_cmd(c: Client, m):
    chat_id = m.chat.id
    info = RESUME.get(chat_id)
    if not info:
        return await m.reply_text("❌ nothing to resume yet — play something first.")
    res = await resume_last(chat_id)
    if res == -1:
        return await m.reply_text("⚠️ the cached file expired — please replay the link normally.")
    await m.reply_text(
        f"⏮ **Resuming** [{info['name']}]({info['link']}) from `{_fmt(info['pos'])}`",
        disable_web_page_preview=True,
        reply_markup=control_panel,
    )


@Client.on_callback_query(filters.regex("cbrestore"))
async def cbrestore(c: Client, query: CallbackQuery):
    if query.message.sender_chat:
        return await query.answer("you're an Anonymous Admin !")
    member = await c.get_chat_member(query.message.chat.id, query.from_user.id)
    if not can_manage_vc(member):
        return await query.answer("💡 only admins with manage-voice-chats can do this", show_alert=True)
    chat_id = query.message.chat.id
    info = RESUME.get(chat_id)
    if not info:
        return await query.answer("❌ nothing to resume", show_alert=True)
    try:
        res = await resume_last(chat_id)
        if res == -1:
            return await query.answer("⚠️ cached file expired — replay the link", show_alert=True)
        await query.answer(f"⏮ resuming from {_fmt(info['pos'])}")
    except Exception as e:
        await query.answer(f"🚫 {e}"[:190], show_alert=True)
