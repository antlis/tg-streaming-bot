

import os
import re
import asyncio
import subprocess
from time import time

from config import ASSISTANT_NAME, BOT_USERNAME, IMG_1, IMG_2
from driver.design.thumbnail import thumb
from driver.design.chatname import CHAT_TITLE
from driver.filters import command, other_filters
from driver.queues import QUEUE, add_to_queue
from driver.clients import call_py, user
from driver.utils import bash, make_progress, control_panel, media_audio, drop_stale_queue
from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserAlreadyParticipant, UserNotParticipant, PeerIdInvalid
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from youtubesearchpython import VideosSearch


def ytsearch(query: str):
    try:
        if re.match(r"https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", query.strip()):
            # direct URL — the search lib can't parse URLs, so use yt-dlp for metadata
            out = subprocess.run(
                ["yt-dlp", "--no-warnings", "--skip-download",
                 "--print", "%(title)s\x1f%(duration_string)s\x1f%(id)s", query],
                capture_output=True, text=True, timeout=90,
            ).stdout.strip().split("\x1f")
            title = out[0] if out and out[0] else "YouTube"
            duration = out[1] if len(out) > 1 else ""
            vid = out[2] if len(out) > 2 and out[2] else ""
            thumbnail = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else ""
            return [title, query.strip(), duration, thumbnail]
        search = VideosSearch(query, limit=1).result()
        data = search["result"][0]
        songname = data["title"]
        url = data["link"]
        duration = data["duration"]
        thumbnail = f"https://i.ytimg.com/vi/{data['id']}/hqdefault.jpg"
        return [songname, url, duration, thumbnail]
    except Exception as e:
        print(e)
        return 0


async def ytdl(format: str, link: str, status_msg=None):
    # Download the audio to a local file and return its path. YouTube 403s the
    # direct stream URL for ffmpeg, so download via yt-dlp and stream the file.
    # If status_msg is given, it's edited with live download progress.
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--no-simulate",
        "--newline",
        "--progress-template",
        "download:PROG|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        # android_vr client avoids YouTube's SABR-gating that 403s the default streams.
        "--extractor-args",
        "youtube:player_client=android_vr",
        "--print",
        "after_move:filepath",
        "-f",
        "bestaudio[ext=m4a]/bestaudio/best",
        "-o",
        "downloads/%(id)s.%(ext)s",
        f"{link}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_buf = []

    async def _drain_stderr():
        while True:
            chunk = await proc.stderr.readline()
            if not chunk:
                break
            stderr_buf.append(chunk.decode(errors="ignore"))

    stderr_task = asyncio.ensure_future(_drain_stderr())

    path = ""
    last_edit = 0.0
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode(errors="ignore").strip()
        if not line:
            continue
        if line.startswith("PROG|"):
            if status_msg is not None and time() - last_edit >= 3:
                last_edit = time()
                parts = line.split("|")
                pct = parts[1].strip() if len(parts) > 1 else ""
                spd = parts[2].strip() if len(parts) > 2 else ""
                eta = parts[3].strip() if len(parts) > 3 else ""
                try:
                    await status_msg.edit(
                        f"📥 **Downloading from YouTube…** `{pct}`\n({spd}, ETA {eta})"
                    )
                except Exception:
                    pass
        else:
            path = line  # --print after_move:filepath is the last line on success
    await proc.wait()
    await stderr_task
    if proc.returncode == 0 and path:
        return 1, path
    return 0, ("".join(stderr_buf)[-500:] or "download failed")


@Client.on_message(command(["play", f"play@{BOT_USERNAME}"]) & other_filters)
async def play(c: Client, m: Message):
    await m.delete()
    replied = m.reply_to_message
    chat_id = m.chat.id
    keyboard = control_panel
    if m.sender_chat:
        return await m.reply_text("you're an __Anonymous__ Admin !\n\n» revert back to user account from admin rights.")
    try:
        aing = await c.get_me()
    except Exception as e:
        return await m.reply_text(f"error:\n\n{e}")
    a = await c.get_chat_member(chat_id, aing.id)
    if a.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        await m.reply_text(
            f"💡 To use me, I need to be an **Administrator** with the following **permissions**:\n\n» ❌ __Delete messages__\n» ❌ __Add users__\n» ❌ __Manage video chat__\n\nData is **updated** automatically after you **promote me**"
        )
        return
    priv = a.privileges
    if not (priv and priv.can_manage_video_chats):
        await m.reply_text(
            "missing required permission:" + "\n\n» ❌ __Manage video chat__"
        )
        return
    if not (priv and priv.can_delete_messages):
        await m.reply_text(
            "missing required permission:" + "\n\n» ❌ __Delete messages__"
        )
        return
    if not (priv and priv.can_invite_users):
        await m.reply_text("missing required permission:" + "\n\n» ❌ __Add users__")
        return
    try:
        ubot = (await user.get_me()).id
        b = await c.get_chat_member(chat_id, ubot)
        if b.status == ChatMemberStatus.BANNED:
            await m.reply_text(
                f"@{ASSISTANT_NAME} **is banned in group** {m.chat.title}\n\n» **unban the userbot first if you want to use this bot.**"
            )
            return
    except (UserNotParticipant, PeerIdInvalid):
        if m.chat.username:
            try:
                await user.join_chat(m.chat.username)
            except UserAlreadyParticipant:
                pass
            except Exception as e:
                await m.reply_text(f"❌ **userbot failed to join**\n\n**reason**: `{e}`")
                return
        else:
            try:
                invitelink = await c.export_chat_invite_link(
                    m.chat.id
                )
                if invitelink.startswith("https://t.me/+"):
                    invitelink = invitelink.replace(
                        "https://t.me/+", "https://t.me/joinchat/"
                    )
                await user.join_chat(invitelink)
            except UserAlreadyParticipant:
                pass
            except Exception as e:
                return await m.reply_text(
                    f"❌ **userbot failed to join**\n\n**reason**: `{e}`"
                )
    # if a previous stream died silently, clear the stale queue so we rejoin
    await drop_stale_queue(chat_id)
    if replied:
        # audio sent as a generic file/document (e.g. a bare .mp3) counts too
        audio_doc = (
            replied.document
            if replied.document and (replied.document.mime_type or "").startswith("audio/")
            else None
        )
        if replied.audio or replied.voice or audio_doc:
            media = replied.audio or replied.voice or audio_doc
            ext = os.path.splitext(getattr(media, "file_name", None) or "")[1] or ".m4a"
            cached = os.path.join(os.getcwd(), "downloads", f"{media.file_unique_id}{ext}")
            if os.path.exists(cached) and os.path.getsize(cached) == media.file_size:
                # same Telegram file already fully downloaded — reuse it
                os.utime(cached, None)  # mark fresh for the LRU cache pruner
                suhu = await replied.reply("📦 **already downloaded — starting...**")
                dl = cached
            else:
                suhu = await replied.reply("📥 **downloading audio...**")
                dl = await replied.download(
                    file_name=cached,
                    progress=make_progress(suhu, "📥 Downloading audio"),
                )
            link = replied.link
            # Title preference: audio metadata title > caption > file name > generic
            if replied.audio and replied.audio.title:
                songname = replied.audio.title[:70]
            elif replied.caption:
                songname = str(replied.caption).splitlines()[0][:70]
            elif getattr(media, "file_name", None):
                songname = media.file_name[:70]
            elif replied.voice:
                songname = "Voice Note"
            else:
                songname = "Audio"
            if chat_id in QUEUE:
                pos = add_to_queue(chat_id, songname, dl, link, "Audio", 0)
                await suhu.delete()
                await m.reply_photo(
                    photo=f"{IMG_1}",
                    caption=f"💡 **Track added to queue »** `{pos}`\n\n🏷 **Name:** [{songname}]({link}) | `music`\n💭 **Chat:** `{chat_id}`\n🎧 **Request by:** {m.from_user.mention()}",
                    reply_markup=keyboard,
                )
            else:
             try:
                await suhu.edit("🔄 **Joining vc...**")
                await call_py.play(chat_id, media_audio(dl))
                add_to_queue(chat_id, songname, dl, link, "Audio", 0)
                await suhu.delete()
                requester = f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                await m.reply_photo(
                    photo=f"{IMG_2}",
                    caption=f"🏷 **Name:** [{songname}]({link})\n💭 **Chat:** `{chat_id}`\n💡 **Status:** `Playing`\n🎧 **Request by:** {requester}\n📹 **Stream type:** `Music`",
                    reply_markup=keyboard,
                )
             except Exception as e:
                await suhu.delete()
                await m.reply_text(f"🚫 error:\n\n» {e}")
        else:
            if len(m.command) < 2:
                await m.reply(
                    "» reply to an **audio file** or **give something to search.**"
                )
            else:
                suhu = await c.send_message(chat_id, "🔍 **Searching...**")
                query = m.text.split(None, 1)[1]
                search = ytsearch(query)
                if search == 0:
                    await suhu.edit("❌ **no results found.**")
                else:
                    songname = search[0]
                    title = search[0]
                    url = search[1]
                    duration = search[2]
                    thumbnail = search[3]
                    userid = m.from_user.id
                    gcname = m.chat.title
                    ctitle = await CHAT_TITLE(gcname)
                    image = await thumb(thumbnail, title, userid, ctitle)
                    format = "bestaudio[ext=m4a]"
                    ok, ytlink = await ytdl(format, url, suhu)
                    if ok == 0:
                        await suhu.edit(f"❌ yt-dl issues detected\n\n» `{ytlink}`")
                    else:
                        if chat_id in QUEUE:
                            pos = add_to_queue(
                                chat_id, songname, ytlink, url, "Audio", 0
                            )
                            await suhu.delete()
                            requester = f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                            await m.reply_photo(
                                photo=image,
                                caption=f"💡 **Track added to queue »** `{pos}`\n\n🏷 **Name:** [{songname}]({url}) | `music`\n**⏱ Duration:** `{duration}`\n🎧 **Request by:** {requester}",
                                reply_markup=keyboard,
                            )
                        else:
                            try:
                                await suhu.edit("🔄 **Joining vc...**")
                                await call_py.play(chat_id, media_audio(ytlink))
                                add_to_queue(chat_id, songname, ytlink, url, "Audio", 0)
                                await suhu.delete()
                                requester = f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                                await m.reply_photo(
                                    photo=image,
                                    caption=f"🏷 **Name:** [{songname}]({url})\n**⏱ Duration:** `{duration}`\n💡 **Status:** `Playing`\n🎧 **Request by:** {requester}\n📹 **Stream type:** `Music`",
                                    reply_markup=keyboard,
                                )
                            except Exception as ep:
                                await suhu.delete()
                                await m.reply_text(f"🚫 error: `{ep}`")

    else:
        if len(m.command) < 2:
            await m.reply(
                "» reply to an **audio file** or **give something to search.**"
            )
        else:
            suhu = await c.send_message(chat_id, "🔍 **Searching...**")
            query = m.text.split(None, 1)[1]
            search = ytsearch(query)
            if search == 0:
                await suhu.edit("❌ **no results found.**")
            else:
                songname = search[0]
                title = search[0]
                url = search[1]
                duration = search[2]
                thumbnail = search[3]
                userid = m.from_user.id
                gcname = m.chat.title
                ctitle = await CHAT_TITLE(gcname)
                image = await thumb(thumbnail, title, userid, ctitle)
                format = "bestaudio[ext=m4a]"
                ok, ytlink = await ytdl(format, url, suhu)
                if ok == 0:
                    await suhu.edit(f"❌ yt-dl issues detected\n\n» `{ytlink}`")
                else:
                    if chat_id in QUEUE:
                        pos = add_to_queue(chat_id, songname, ytlink, url, "Audio", 0)
                        await suhu.delete()
                        requester = (
                            f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                        )
                        await m.reply_photo(
                            photo=image,
                            caption=f"💡 **Track added to queue »** `{pos}`\n\n🏷 **Name:** [{songname}]({url}) | `music`\n**⏱ Duration:** `{duration}`\n🎧 **Request by:** {requester}",
                            reply_markup=keyboard,
                        )
                    else:
                        try:
                            await suhu.edit("🔄 **Joining vc...**")
                            await call_py.play(chat_id, media_audio(ytlink))
                            add_to_queue(chat_id, songname, ytlink, url, "Audio", 0)
                            await suhu.delete()
                            requester = f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                            await m.reply_photo(
                                photo=image,
                                caption=f"🏷 **Name:** [{songname}]({url})\n**⏱ Duration:** `{duration}`\n💡 **Status:** `Playing`\n🎧 **Request by:** {requester}\n📹 **Stream type:** `Music`",
                                reply_markup=keyboard,
                            )
                        except Exception as ep:
                            await suhu.delete()
                            await m.reply_text(f"🚫 error: `{ep}`")
