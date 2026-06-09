"""Download a track/video from YouTube and send it as a file (no voice chat).

Unlike /play and /vplay (which stream into the group voice chat), these just
fetch the media and upload it to the chat. They reuse the same modern yt-dlp
helpers as the streaming commands so they survive YouTube's SABR changes.
"""

import os

from config import BOT_USERNAME
from driver.filters import command, other_filters
from program.music import ytsearch as audio_search, ytdl as audio_dl
from program.video import ytsearch as video_search, ytdl as video_dl
from pyrogram import Client
from pyrogram.types import Message


@Client.on_message(command(["song", f"song@{BOT_USERNAME}"]) & other_filters)
async def song(c: Client, m: Message):
    if len(m.command) < 2:
        return await m.reply("» usage: `/song <name or YouTube URL>`")
    query = m.text.split(None, 1)[1]
    status = await m.reply("🔎 **finding song…**")
    if query.startswith("http"):
        title, link = query, query
    else:
        res = audio_search(query)
        if res == 0:
            return await status.edit("❌ song not found — try a different name.")
        title, link = res[0], res[1]
    await status.edit(f"📥 **downloading** `{title[:50]}`…")
    ok, path = await audio_dl("bestaudio", link, status)
    if ok == 0:
        return await status.edit(f"❌ download failed:\n`{str(path)[:150]}`")
    await status.edit("📤 **uploading…**")
    try:
        await m.reply_audio(path, title=title[:60], caption=f"🎧 {title[:60]}")
        await status.delete()
    except Exception as e:
        await status.edit(f"🚫 upload failed: `{e}`")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@Client.on_message(command(["video", f"video@{BOT_USERNAME}", "vsong", f"vsong@{BOT_USERNAME}"]) & other_filters)
async def video(c: Client, m: Message):
    if len(m.command) < 2:
        return await m.reply("» usage: `/video <name or YouTube URL>`")
    query = m.text.split(None, 1)[1]
    status = await m.reply("🔎 **finding video…**")
    if query.startswith("http"):
        title, link = query, query
    else:
        res = video_search(query)
        if res == 0:
            return await status.edit("❌ video not found — try a different name.")
        title, link = res[0], res[1]
    await status.edit(f"📥 **downloading** `{title[:50]}`…")
    ok, path = await video_dl(link, status)
    if ok == 0:
        return await status.edit(f"❌ download failed:\n`{str(path)[:150]}`")
    await status.edit("📤 **uploading…**")
    try:
        await m.reply_video(path, caption=f"🎬 {title[:60]}")
        await status.delete()
    except Exception as e:
        await status.edit(f"🚫 upload failed: `{e}`")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
