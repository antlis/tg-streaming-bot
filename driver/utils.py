import os
import asyncio
import logging
from time import time
from config import DOWNLOADS_CACHE_LIMIT_MB, ASSISTANT_NAME
from driver.clients import bot, call_py, user
from driver.queues import QUEUE, clear_queue, get_queue, pop_an_item, is_loop
from pytgcalls import filters as call_filters
from pytgcalls.types import MediaStream, AudioQuality, VideoQuality, ChatUpdate, StreamEnded
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserAlreadyParticipant, UserNotParticipant, PeerIdInvalid
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from pyrogram import Client, filters

log = logging.getLogger(__name__)

_VQ = {720: VideoQuality.HD_720p, 480: VideoQuality.SD_480p, 360: VideoQuality.SD_360p}


def media_audio(path):
    """Audio-only MediaStream (ignore the video track)."""
    return MediaStream(path, video_flags=MediaStream.Flags.IGNORE)


def media_video(path, quality=720):
    """Audio+video MediaStream at the given vertical resolution."""
    return MediaStream(
        path,
        audio_parameters=AudioQuality.HIGH,
        video_parameters=_VQ.get(quality, VideoQuality.HD_720p),
    )


def _gain_params(vol, pos=0):
    """py-tgcalls ffmpeg-parameter DSL string that applies an audio `volume`
    filter *after* `-i` (--audio ---mid section) and optionally an input seek.
    Two dashes select the section, three select the start/mid/end slot."""
    af = f"--audio ---mid -af volume={max(0.0, vol):.3f}"
    return f"-ss {int(pos)} {af}" if pos and pos > 0 else af


def media_audio_gain(path, vol, pos=0):
    return MediaStream(path, video_flags=MediaStream.Flags.IGNORE, ffmpeg_parameters=_gain_params(vol, pos))


def media_video_gain(path, quality, vol, pos=0):
    return MediaStream(
        path,
        audio_parameters=AudioQuality.HIGH,
        video_parameters=_VQ.get(quality, VideoQuality.HD_720p),
        ffmpeg_parameters=_gain_params(vol, pos),
    )


# Absolute playback offset of the last (re)feed per chat. call_py.time() resets
# to 0 on every play(), so to seek a re-fed stream back to the live spot across
# *repeated* re-feeds we track the offset ourselves: base + elapsed-since-feed.
_GAIN_BASE = {}  # chat_id -> {"src": str, "base": int(seconds)}


async def replay_at_gain(chat_id, vol_pct):
    """Re-feed the current stream at vol_pct% loudness — the master-volume / mute
    lever. Because it keeps the assistant continuously streaming (rather than
    muting at the media layer), muting a video this way (vol 0) doesn't make
    Telegram downgrade it to a blurry low-quality layer. Costs a brief re-buffer."""
    vol = max(0, vol_pct) / 100.0
    # Radio uses an image+audio_path stream; let radio rebuild it (keeps the card).
    try:
        from program.radio import replay_radio_at_gain
        if await replay_radio_at_gain(chat_id, vol):
            return True
    except Exception:
        pass
    q = get_queue(chat_id)
    if not q:
        return False
    src, typ, quality = q[0][1], q[0][3], q[0][4]
    pos = 0
    if not str(src).startswith("http"):   # seek local files back to the live spot
        prev = _GAIN_BASE.get(chat_id)
        base = prev["base"] if prev and prev.get("src") == src else 0
        try:
            delta = int(await call_py.time(chat_id))
        except Exception:
            delta = 0
        pos = base + max(0, delta)
    stream = media_video_gain(src, quality, vol, pos) if typ == "Video" else media_audio_gain(src, vol, pos)
    await call_py.play(chat_id, stream)
    if not str(src).startswith("http"):
        _GAIN_BASE[chat_id] = {"src": src, "base": pos}
    log.info("re-fed %s at %s%% from %ss", chat_id, vol_pct, pos)
    return True


async def drop_stale_queue(chat_id) -> bool:
    """If we think a chat is queued but py-tgcalls has no live call for it
    (e.g. the stream silently died without firing a lifecycle event), clear the
    stale queue so the next /play rejoins instead of queuing into a dead call."""
    if chat_id not in QUEUE:
        return False
    try:
        live = chat_id in (await call_py.calls)
    except Exception:
        live = False
    if not live:
        log.info("%s queued but no live call — clearing stale queue", chat_id)
        clear_queue(chat_id)
        return True
    return False


def can_manage_vc(member) -> bool:
    """Pyrogram 2.x: owners implicitly have every right; admins need the
    can_manage_video_chats privilege."""
    if member.status == ChatMemberStatus.OWNER:
        return True
    if member.status == ChatMemberStatus.ADMINISTRATOR:
        return bool(member.privileges and member.privileges.can_manage_video_chats)
    return False


async def ensure_assistant_in_chat(c, chat_id, chat_username=None):
    """Make sure the assistant userbot is a member of the chat (auto-join via
    public username or an exported invite link). Returns (True, None) on success
    or (False, reason)."""
    try:
        ubot = (await user.get_me()).id
        b = await c.get_chat_member(chat_id, ubot)
        if b.status == ChatMemberStatus.BANNED:
            return False, f"@{ASSISTANT_NAME} is banned in this group — unban the assistant first."
        return True, None
    except (UserNotParticipant, PeerIdInvalid):
        try:
            if chat_username:
                await user.join_chat(chat_username)
            else:
                invitelink = await c.export_chat_invite_link(chat_id)
                if invitelink.startswith("https://t.me/+"):
                    invitelink = invitelink.replace("https://t.me/+", "https://t.me/joinchat/")
                await user.join_chat(invitelink)
            return True, None
        except UserAlreadyParticipant:
            return True, None
        except Exception as e:
            return False, f"userbot failed to join: {e}"


async def ensure_can_play(c, m) -> bool:
    """Shared streaming gate: the bot must be an admin with Manage-video-chats /
    Delete-messages / Add-users, the assistant must be in the group, and any
    stale queue is cleared. Replies with the reason and returns False if not ready."""
    chat_id = m.chat.id
    if m.sender_chat:
        await m.reply_text("you're an __Anonymous__ Admin !\n\n» revert back to a user account.")
        return False
    try:
        a = await c.get_chat_member(chat_id, (await c.get_me()).id)
    except Exception as e:
        await m.reply_text(f"error:\n\n{e}")
        return False
    if a.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        await m.reply_text("💡 Make me an **Administrator** with **Manage video chats**, **Delete messages** and **Add users**.")
        return False
    p = a.privileges
    if not (p and p.can_manage_video_chats and p.can_delete_messages and p.can_invite_users):
        await m.reply_text("missing a required permission: **Manage video chats / Delete messages / Add users**")
        return False
    ok, reason = await ensure_assistant_in_chat(c, chat_id, m.chat.username)
    if not ok:
        await m.reply_text(f"❌ {reason}")
        return False
    await drop_stale_queue(chat_id)
    return True


keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="• Mᴇɴᴜ", callback_data="cbmenu"),
                InlineKeyboardButton(text="• Cʟᴏsᴇ", callback_data="cls"),
            ]
        ]
    )


# Now-playing transport panel (tg-mpv-bot style) — reuses the existing callbacks.
control_panel = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("⏸", callback_data="cbpause"),
            InlineKeyboardButton("▶️", callback_data="cbresume"),
            InlineKeyboardButton("⏭", callback_data="cbskip"),
            InlineKeyboardButton("⏹", callback_data="cbstop"),
        ],
        [
            InlineKeyboardButton("🔉", callback_data="cbvoldown"),
            InlineKeyboardButton("🔊", callback_data="cbvolup"),
            InlineKeyboardButton("🔇", callback_data="cbmute"),
            InlineKeyboardButton("🔈", callback_data="cbunmute"),
        ],
        [
            InlineKeyboardButton("⏺ Rec / Stop", callback_data="rectoggle"),
            InlineKeyboardButton("🗑 Close", callback_data="cls"),
        ],
    ]
)


def make_progress(message, label="📥 Downloading"):
    """Return an async Pyrogram download-progress callback that edits `message`
    with a percentage bar, throttled to at most once / 3s to avoid FloodWait."""
    state = {"t": 0.0, "pct": -1}

    async def progress(current, total):
        pct = int(current * 100 / total) if total else 0
        now = time()
        if pct != 100 and (now - state["t"] < 3 or pct == state["pct"]):
            return
        state["t"] = now
        state["pct"] = pct
        filled = pct // 10
        bar = "█" * filled + "░" * (10 - filled)
        try:
            await message.edit(
                f"**{label}…**\n`{bar}` **{pct}%**  "
                f"({current // 1048576}/{total // 1048576} MB)"
            )
        except Exception:
            pass

    return progress


def _queued_paths():
    """Local file paths referenced by any chat's active queue (never prune these)."""
    used = set()
    for chat_queue in QUEUE.values():
        for item in chat_queue:
            used.add(item[1])
    return used


def prune_downloads():
    """Keep downloads/ under DOWNLOADS_CACHE_LIMIT_MB by deleting the oldest
    cached files first. Files still referenced by a queue are never touched.
    A limit of 0 clears everything that isn't queued."""
    limit = DOWNLOADS_CACHE_LIMIT_MB * 1024 * 1024
    folder = os.path.join(os.getcwd(), "downloads")
    if not os.path.isdir(folder):
        return
    used = _queued_paths()
    files = []
    for name in os.listdir(folder):
        # never prune session files or the persisted resume state
        if name.endswith((".session", ".session-journal")) or name == "resume.json":
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path) and path not in used:
            try:
                files.append((os.path.getmtime(path), path, os.path.getsize(path)))
            except OSError:
                pass
    total = sum(size for _, _, size in files)
    for _, path, size in sorted(files):  # oldest first
        if total <= limit:
            break
        try:
            os.remove(path)
            total -= size
        except OSError:
            pass


async def skip_current_song(chat_id):
    if chat_id in QUEUE:
        chat_queue = get_queue(chat_id)
        if len(chat_queue) == 1:
            await call_py.leave_call(chat_id)
            clear_queue(chat_id)
            return 1
        else:
            try:
                songname = chat_queue[1][0]
                url = chat_queue[1][1]
                link = chat_queue[1][2]
                type = chat_queue[1][3]
                Q = chat_queue[1][4]
                if type == "Audio":
                    await call_py.play(chat_id, media_audio(url))
                elif type == "Video":
                    await call_py.play(chat_id, media_video(url, Q))
                pop_an_item(chat_id)
                return [songname, link, type]
            except Exception:
                log.warning("failed to advance queue in %s — leaving call", chat_id)
                await call_py.leave_call(chat_id)
                clear_queue(chat_id)
                return 2
    else:
        return 0


async def skip_item(chat_id, h):
    if chat_id in QUEUE:
        chat_queue = get_queue(chat_id)
        try:
            x = int(h)
            songname = chat_queue[x][0]
            chat_queue.pop(x)
            return songname
        except Exception as e:
            log.warning("skip_item failed: %s", e)
            return 0
    else:
        return 0


# py-tgcalls 2.x: lifecycle is delivered through on_update + filters instead of
# the old per-event decorators (on_kicked / on_closed_voice_chat / on_left).
@call_py.on_update(
    call_filters.chat_update(
        ChatUpdate.Status.KICKED
        | ChatUpdate.Status.LEFT_GROUP
        | ChatUpdate.Status.CLOSED_VOICE_CHAT
    )
)
async def chat_update_handler(_, update):
    chat_id = update.chat_id
    log.info("chat update in %s: %s — clearing queue", chat_id, update.status)
    if chat_id in QUEUE:
        clear_queue(chat_id)
    prune_downloads()


# NB: filters are classes — register an INSTANCE (stream_end()), not the class.
# Scope to the audio track so a video ending doesn't fire the skip twice.
@call_py.on_update(call_filters.stream_end(StreamEnded.Type.AUDIO))
async def stream_end_handler(_, update):
    chat_id = update.chat_id
    # loop mode: replay the current track instead of advancing
    if is_loop(chat_id):
        q = get_queue(chat_id)
        if q:
            head = q[0]
            try:
                stream = media_video(head[1], head[4]) if head[3] == "Video" else media_audio(head[1])
                await call_py.play(chat_id, stream)
                log.info("stream ended in %s — looping current track", chat_id)
                return
            except Exception:
                pass
    log.info("stream ended in %s — advancing queue", chat_id)
    op = await skip_current_song(chat_id)
    if op == 1:
        await bot.send_message(chat_id, "✅ streaming end")
    elif op == 2:
        await bot.send_message(chat_id, "❌ an error occurred\n\n» **Clearing** __Queues__ and leaving video chat.")
    else:
        await bot.send_message(chat_id, f"💡 **Streaming next track**\n\n🏷 **Name:** [{op[0]}]({op[1]}) | `{op[2]}`\n💭 **Chat:** `{chat_id}`", disable_web_page_preview=True, reply_markup=keyboard)
    prune_downloads()


async def bash(cmd):
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    err = stderr.decode().strip()
    out = stdout.decode().strip()
    return out, err
