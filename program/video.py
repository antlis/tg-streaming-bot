import os
import re
import asyncio
import subprocess
from time import time

import logging

from config import ASSISTANT_NAME, BOT_USERNAME, IMG_1, IMG_2, MAX_QUEUE_SIZE, SPONSORBLOCK_REMOVE
from driver.design.thumbnail import thumb
from driver.design.chatname import CHAT_TITLE
from driver.filters import command, other_filters
from driver.queues import QUEUE, add_to_queue
from driver.clients import call_py, user
from driver.transcode import prepare_for_stream
from driver.utils import make_progress, control_panel, media_video, drop_stale_queue
from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserAlreadyParticipant, UserNotParticipant, PeerIdInvalid
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from youtubesearchpython import VideosSearch


log = logging.getLogger(__name__)


def ytsearch(query: str):
    try:
        q = query.strip()
        is_yt = bool(re.match(r"https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", q))
        if re.match(r"https?://", q):
            # any direct URL — use yt-dlp for metadata (handles YouTube, Rutube, Vimeo, …)
            out = subprocess.run(
                ["yt-dlp", "--no-warnings", "--skip-download",
                 "--print", "%(title)s\x1f%(duration_string)s\x1f%(id)s\x1f%(thumbnail)s", q],
                capture_output=True, text=True, timeout=90,
            ).stdout.strip().split("\x1f")
            title = out[0] if out and out[0] else q
            duration = out[1] if len(out) > 1 else ""
            vid = out[2] if len(out) > 2 and out[2] else ""
            raw_thumb = out[3] if len(out) > 3 else ""
            thumbnail = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if is_yt and vid else raw_thumb
            return [title, q, duration, thumbnail]
        search = VideosSearch(query, limit=1).result()
        data = search["result"][0]
        songname = data["title"]
        url = data["link"]
        duration = data["duration"]
        thumbnail = f"https://i.ytimg.com/vi/{data['id']}/hqdefault.jpg"
        return [songname, url, duration, thumbnail]
    except Exception as e:
        log.warning("ytsearch failed: %s", e)
        return 0


_YT_RE = re.compile(r"https?://(www\.|m\.)?(youtube\.com|youtu\.be)/")


async def ytdl(link, status_msg=None):
    # For non-YouTube sites (e.g. Rutube) the formats are HLS-only muxed streams.
    # Extract the best ≤720p stream URL and hand it to ffmpeg live instead of
    # downloading the whole file. YouTube must be downloaded first (direct URLs 403).
    if not _YT_RE.match(link):
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--no-warnings", "--no-playlist",
            "-f", "best[height<=720]/best",
            "--print", "%(url)s",
            link,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            url = stdout.decode(errors="ignore").strip()
            if url:
                return 1, url
        err = stderr.decode(errors="ignore")[-400:]
        return 0, (err or "stream URL extraction failed")

    proc = await asyncio.create_subprocess_exec(
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        *(["--sponsorblock-remove", SPONSORBLOCK_REMOVE] if SPONSORBLOCK_REMOVE else []),
        "--no-simulate",
        "--newline",
        "--progress-template",
        "download:PROG|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        # android_vr client avoids YouTube's SABR-gating (the default clients
        # only offer the progressive itag-18 stream, which 403s).
        *(["--extractor-args", "youtube:player_client=android_vr"]
          if _YT_RE.match(link) else []),
        "--print",
        "after_move:filepath",
        # Prefer H.264 video + AAC(m4a) audio so the merge produces a universally
        # playable, light-to-stream mp4 (avoids AV1/VP9/Opus -> mp4 issues).
        "-f",
        "bestvideo[height<=?720][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height<=?720][ext=mp4]+bestaudio[ext=m4a]/best[height<=?720]/best",
        "--merge-output-format",
        "mp4",
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


@Client.on_message(command(["vplay", f"vplay@{BOT_USERNAME}"]) & other_filters)
async def vplay(c: Client, m: Message):
    await m.delete()
    replied = m.reply_to_message
    chat_id = m.chat.id
    keyboard = control_panel
    # "/vplay ... mute" starts the stream with the assistant muted
    args = [a.lower() for a in m.command[1:]]
    start_muted = "mute" in args
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
        if replied.video or replied.document:
            media = replied.video or replied.document
            ext = os.path.splitext(media.file_name or "")[1] or ".mp4"
            cached = os.path.join(os.getcwd(), "downloads", f"{media.file_unique_id}{ext}")
            if os.path.exists(cached) and os.path.getsize(cached) == media.file_size:
                # same Telegram file already fully downloaded — reuse it
                os.utime(cached, None)  # mark fresh for the LRU cache pruner
                loser = await replied.reply("📦 **already downloaded — starting...**")
                dl = cached
            else:
                if media.file_size and media.file_size > 2 * 1024 ** 3:
                    await replied.reply(
                        f"⚠️ This file is **{media.file_size / 1024 ** 3:.1f} GB**. Telegram caps "
                        "downloads at **2 GB** for standard accounts (4 GB for Premium), so it "
                        "may fail to download. Trying anyway…"
                    )
                loser = await replied.reply("📥 **downloading video...**")
                dl = await replied.download(
                    file_name=cached,
                    progress=make_progress(loser, "📥 Downloading video"),
                )
            link = replied.link
            # args may contain a quality (720/480/360) and/or "mute"
            qargs = [a for a in args if a in ("720", "480", "360")]
            Q = int(qargs[0]) if qargs else 720
            # Title preference: message caption (manual uploads put the title
            # there and often have no file_name) > file name > generic.
            if replied.caption:
                songname = str(replied.caption).splitlines()[0][:70]
            elif media.file_name:
                songname = media.file_name[:70]
            else:
                songname = "Video"

            if chat_id in QUEUE:
                pos = add_to_queue(chat_id, songname, dl, link, "Video", Q)
                if pos == -1:
                    return await loser.edit(f"🚫 queue is full (max {MAX_QUEUE_SIZE}).")
                await loser.delete()
                requester = f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                await m.reply_photo(
                    photo=f"{IMG_1}",
                    caption=f"💡 **Track added to queue »** `{pos}`\n\n🏷 **Name:** [{songname}]({link}) | `video`\n💭 **Chat:** `{chat_id}`\n🎧 **Request by:** {requester}",
                    reply_markup=keyboard,
                )
            else:
                try:
                  dl = await prepare_for_stream(dl, loser)
                  await loser.edit("🔄 **Joining vc...**")
                  await call_py.play(chat_id, media_video(dl, Q))
                  if start_muted:
                      try:
                          await call_py.mute(chat_id)
                      except Exception:
                          pass
                  add_to_queue(chat_id, songname, dl, link, "Video", Q)
                  await loser.delete()
                  requester = f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                  await m.reply_photo(
                    photo=f"{IMG_2}",
                    caption=f"🏷 **Name:** [{songname}]({link})\n💭 **Chat:** `{chat_id}`\n💡 **Status:** `Playing`\n🎧 **Request by:** {requester}\n📹 **Stream type:** `Video`",
                    reply_markup=keyboard,
                  )
                except Exception as ep:
                  await loser.delete()
                  await m.reply_text(f"🚫 error: `{ep}`")
        else:
            if len(m.command) < 2:
                await m.reply(
                    "» reply to an **video file** or **give something to search.**"
                )
            else:
                loser = await c.send_message(chat_id, "🔍 **Searching...**")
                query = m.text.split(None, 1)[1]
                if start_muted and query.lower().endswith("mute"):
                    query = query[:-4].strip()
                search = ytsearch(query)
                Q = 720
                if search == 0:
                    await loser.edit("❌ **no results found.**")
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
                    ok, ytlink = await ytdl(url, loser)
                    if ok == 0:
                        await loser.edit(f"❌ yt-dl issues detected\n\n» `{ytlink}`")
                    else:
                        if chat_id in QUEUE:
                            pos = add_to_queue(
                                chat_id, songname, ytlink, url, "Video", Q
                            )
                            if pos == -1:
                                return await loser.edit(f"🚫 queue is full (max {MAX_QUEUE_SIZE}).")
                            await loser.delete()
                            requester = f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                            await m.reply_photo(
                                photo=image,
                                caption=f"💡 **Track added to queue »** `{pos}`\n\n🏷 **Name:** [{songname}]({url}) | `video`\n⏱ **Duration:** `{duration}`\n🎧 **Request by:** {requester}",
                                reply_markup=keyboard,
                            )
                        else:
                            try:
                                await loser.edit("🔄 **Joining vc...**")
                                await call_py.play(chat_id, media_video(ytlink, Q))
                                if start_muted:
                                    try:
                                        await call_py.mute(chat_id)
                                    except Exception:
                                        pass
                                add_to_queue(chat_id, songname, ytlink, url, "Video", Q)
                                await loser.delete()
                                requester = f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                                await m.reply_photo(
                                    photo=image,
                                    caption=f"🏷 **Name:** [{songname}]({url})\n⏱ **Duration:** `{duration}`\n💡 **Status:** `Playing`\n🎧 **Request by:** {requester}\n📹 **Stream type:** `Video`",
                                    reply_markup=keyboard,
                                )
                            except Exception as ep:
                                await loser.delete()
                                await m.reply_text(f"🚫 error: `{ep}`")

    else:
        if len(m.command) < 2:
            await m.reply(
                "» reply to an **video file** or **give something to search.**"
            )
        else:
            loser = await c.send_message(chat_id, "🔍 **Searching...**")
            query = m.text.split(None, 1)[1]
            search = ytsearch(query)
            Q = 720
            if search == 0:
                await loser.edit("❌ **no results found.**")
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
                ok, ytlink = await ytdl(url, loser)
                if ok == 0:
                    await loser.edit(f"❌ yt-dl issues detected\n\n» `{ytlink}`")
                else:
                    if chat_id in QUEUE:
                        pos = add_to_queue(chat_id, songname, ytlink, url, "Video", Q)
                        if pos == -1:
                            return await loser.edit(f"🚫 queue is full (max {MAX_QUEUE_SIZE}).")
                        await loser.delete()
                        requester = (
                            f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                        )
                        await m.reply_photo(
                            photo=image,
                            caption=f"💡 **Track added to queue »** `{pos}`\n\n🏷 **Name:** [{songname}]({url}) | `video`\n⏱ **Duration:** `{duration}`\n🎧 **Request by:** {requester}",
                            reply_markup=keyboard,
                        )
                    else:
                        try:
                            await loser.edit("🔄 **Joining vc...**")
                            await call_py.play(chat_id, media_video(ytlink, Q))
                            if start_muted:
                                try:
                                    await call_py.mute(chat_id)
                                except Exception:
                                    pass
                            add_to_queue(chat_id, songname, ytlink, url, "Video", Q)
                            await loser.delete()
                            requester = f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                            await m.reply_photo(
                                photo=image,
                                caption=f"🏷 **Name:** [{songname}]({url})\n⏱ **Duration:** `{duration}`\n💡 **Status:** `Playing`\n🎧 **Request by:** {requester}\n📹 **Stream type:** `Video`",
                                reply_markup=keyboard,
                            )
                        except Exception as ep:
                            await loser.delete()
                            await m.reply_text(f"🚫 error: `{ep}`")


@Client.on_message(command(["vstream", f"vstream@{BOT_USERNAME}"]) & other_filters)
async def vstream(c: Client, m: Message):
    await m.delete()
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
    if len(m.command) < 2:
        await m.reply("» give me a live-link/m3u8 url/youtube link to stream.")
    else:
        if len(m.command) == 2:
            link = m.text.split(None, 1)[1]
            Q = 720
            loser = await c.send_message(chat_id, "🔄 **processing stream...**")
        elif len(m.command) == 3:
            op = m.text.split(None, 1)[1]
            link = op.split(None, 1)[0]
            quality = op.split(None, 1)[1]
            if quality == "720" or "480" or "360":
                Q = int(quality)
            else:
                Q = 720
                await m.reply(
                    "» __only 720, 480, 360 allowed__ \n💡 **now streaming video in 720p**"
                )
            loser = await c.send_message(chat_id, "🔄 **processing stream...**")
        else:
            return await m.reply("**/vstream {link} {720/480/360}**")

        regex = r"^(https?\:\/\/)?(www\.youtube\.com|youtu\.?be)\/.+"
        match = re.match(regex, link)
        if match:
            ok, livelink = await ytdl(link, loser)
        else:
            livelink = link
            ok = 1

        if ok == 0:
            await loser.edit(f"❌ yt-dl issues detected\n\n» `{livelink}`")
        else:
            if chat_id in QUEUE:
                pos = add_to_queue(chat_id, "Live Stream", livelink, link, "Video", Q)
                if pos == -1:
                    return await loser.edit(f"🚫 queue is full (max {MAX_QUEUE_SIZE}).")
                await loser.delete()
                requester = f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                await m.reply_photo(
                    photo=f"{IMG_1}",
                    caption=f"💡 **Track added to queue »** `{pos}`\n\n💭 **Chat:** `{chat_id}`\n🎧 **Request by:** {requester}",
                    reply_markup=keyboard,
                )
            else:
                try:
                    await loser.edit("🔄 **Joining vc...**")
                    await call_py.play(chat_id, media_video(livelink, Q))
                    add_to_queue(chat_id, "Live Stream", livelink, link, "Video", Q)
                    await loser.delete()
                    requester = (
                        f"[{m.from_user.first_name}](tg://user?id={m.from_user.id})"
                    )
                    await m.reply_photo(
                        photo=f"{IMG_2}",
                        caption=f"💡 **[Video live]({link}) stream started.**\n\n💭 **Chat:** `{chat_id}`\n💡 **Status:** `Playing`\n🎧 **Request by:** {requester}",
                        reply_markup=keyboard,
                    )
                except Exception as ep:
                    await loser.delete()
                    await m.reply_text(f"🚫 error: `{ep}`")
