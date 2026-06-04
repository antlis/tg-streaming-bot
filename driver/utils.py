import os
import asyncio
from time import time
from config import DOWNLOADS_CACHE_LIMIT_MB
from driver.clients import bot, call_py
from driver.queues import QUEUE, clear_queue, get_queue, pop_an_item
from pytgcalls import filters as call_filters
from pytgcalls.types import MediaStream, AudioQuality, VideoQuality, ChatUpdate, StreamEnded
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from pyrogram import Client, filters


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


def can_manage_vc(member) -> bool:
    """Pyrogram 2.x: owners implicitly have every right; admins need the
    can_manage_video_chats privilege."""
    if member.status == ChatMemberStatus.OWNER:
        return True
    if member.status == ChatMemberStatus.ADMINISTRATOR:
        return bool(member.privileges and member.privileges.can_manage_video_chats)
    return False


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
        # never prune Pyrogram session files living in the volume
        if name.endswith((".session", ".session-journal")):
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
            except:
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
            print(e)
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
    if chat_id in QUEUE:
        clear_queue(chat_id)
    prune_downloads()


# NB: filters are classes — register an INSTANCE (stream_end()), not the class.
# Scope to the audio track so a video ending doesn't fire the skip twice.
@call_py.on_update(call_filters.stream_end(StreamEnded.Type.AUDIO))
async def stream_end_handler(_, update):
    chat_id = update.chat_id
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
