import os
import re
import hashlib
import asyncio

from config import BOT_USERNAME, LIBRARY_ROOT, LIBRARY_CATEGORIES, MAX_QUEUE_SIZE
from driver.decorators import errors, errors_cb
from driver.filters import command, other_filters
from driver.queues import QUEUE, add_to_queue, drop_if_live, set_active_thread
from driver.clients import call_py
from driver.transcode import prepare_for_stream, probe_tracks, transcode_selection
from driver.utils import (
    control_panel,
    media_video,
    ensure_can_play,
    ensure_assistant_in_chat,
    drop_stale_queue,
    can_manage_vc,
)
from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

VIDEO_EXT = (".mp4", ".mkv", ".avi", ".webm", ".mov", ".m4v", ".ts", ".flv", ".wmv", ".mpg", ".mpeg")
PAGE = 10

# Cosmetic only — falls back to a generic folder icon for anything not listed.
CATEGORY_EMOJI = {
    "cartoons": "🎨",
    "movie": "🎬",
    "movies": "🎬",
    "shows": "📺",
    "series": "📺",
    "tutorials": "🎓",
    "tutorial": "🎓",
    "music": "🎵",
}

# token -> absolute path. Tokens keep callback_data tiny (paths are too long /
# can exceed Telegram's 64-byte limit). Populated lazily as folders are listed.
_TOKENS = {}

# Pending audio/subtitle pick-at-play selections: chat_id -> dict
SELECT = {}


def _select_kb(sel):
    rows = []
    for abs_i, label in sel["audios"]:
        mark = "🔊 " if abs_i == sel["audio"] else "▫️ "
        rows.append([InlineKeyboardButton(f"{mark}{label[:40]}", callback_data=f"la:{abs_i}")])
    if sel["subs"]:
        off = "💬 " if sel["sub"] is None else "▫️ "
        sub_row = [InlineKeyboardButton(f"{off}subs off", callback_data="ls:-1")]
        rows.append(sub_row)
        for abs_i, _ord, label, _img in sel["subs"]:
            mark = "💬 " if abs_i == sel["sub"] else "▫️ "
            rows.append([InlineKeyboardButton(f"{mark}{label[:40]}", callback_data=f"ls:{abs_i}")])
    rows.append([InlineKeyboardButton("▶️ Play", callback_data="lpgo"),
                 InlineKeyboardButton("🗑 Close", callback_data="cls")])
    return InlineKeyboardMarkup(rows)


def _select_text(sel):
    return (f"🎬 **{sel['name'][:60]}**\n\nPick audio"
            + (" / subtitles" if sel["subs"] else "")
            + ", then **▶️ Play**:")


def _disabled():
    return not LIBRARY_ROOT


def _tok(path):
    t = hashlib.md5(path.encode()).hexdigest()[:12]
    _TOKENS[t] = path
    return t


def _within_library(path):
    return os.path.abspath(path).startswith(os.path.abspath(LIBRARY_ROOT))


def _top_categories():
    if not (LIBRARY_ROOT and os.path.isdir(LIBRARY_ROOT)):
        return []
    cats = []
    for name in sorted(os.listdir(LIBRARY_ROOT)):
        if name.startswith("."):
            continue
        full = os.path.join(LIBRARY_ROOT, name)
        if os.path.isdir(full) and (not LIBRARY_CATEGORIES or name in LIBRARY_CATEGORIES):
            cats.append((name, full))
    return cats


def _entries(dirpath):
    """(subdirs, video_files) — immediate children only, sorted, dotfiles skipped."""
    subdirs, files = [], []
    try:
        for e in sorted(os.scandir(dirpath), key=lambda x: x.name.lower()):
            if e.name.startswith("."):
                continue
            if e.is_dir():
                subdirs.append((e.name, e.path))
            elif e.is_file() and e.name.lower().endswith(VIDEO_EXT):
                files.append((e.name, e.path))
    except OSError:
        pass
    return subdirs, files


def _categories_kb():
    rows = [[InlineKeyboardButton(
                f"{CATEGORY_EMOJI.get(name.lower(), '📁')} {name}",
                callback_data=f"lx:{_tok(path)}:0")]
            for name, path in _top_categories()]
    # Web-sourced cartoon library
    rows.append([InlineKeyboardButton("🌐 Cartoons", callback_data="cx:shows")])
    rows.append([InlineKeyboardButton("🗑 Close", callback_data="cls")])
    return InlineKeyboardMarkup(rows)


def _listing_kb(dirpath, page):
    subdirs, files = _entries(dirpath)
    items = [("dir", n, p) for n, p in subdirs] + [("file", n, p) for n, p in files]
    pages = max(1, (len(items) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    rows = []
    for kind, name, path in items[page * PAGE:(page + 1) * PAGE]:
        if kind == "dir":
            rows.append([InlineKeyboardButton(f"📁 {name[:50]}", callback_data=f"lx:{_tok(path)}:0")])
        else:
            rows.append([InlineKeyboardButton(f"🎬 {name[:50]}", callback_data=f"lp:{_tok(path)}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"lx:{_tok(dirpath)}:{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="libnoop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"lx:{_tok(dirpath)}:{page + 1}"))
    if nav:
        rows.append(nav)
    if files:
        rows.append([InlineKeyboardButton(f"▶️ Play all ({len(files)})", callback_data=f"lall:{_tok(dirpath)}")])
    parent = os.path.dirname(dirpath)
    bottom = []
    if dirpath != os.path.abspath(LIBRARY_ROOT) and _within_library(parent) and parent != os.path.abspath(LIBRARY_ROOT):
        bottom.append(InlineKeyboardButton("⬆ Up", callback_data=f"lx:{_tok(parent)}:0"))
    bottom.append(InlineKeyboardButton("⬅ Categories", callback_data="libcats"))
    rows.append(bottom)
    return InlineKeyboardMarkup(rows)


@Client.on_message(command(["library", f"library@{BOT_USERNAME}", "lib"]) & other_filters)
@errors
async def library(c: Client, m: Message):
    if _disabled() and not _top_categories():
        # No local library — still show cartoons if available
        if CARTOON_SHOWS:
            return await m.reply(
                "📚 **Library** — pick a category:",
                reply_markup=_categories_kb(),
            )
        return await m.reply("📚 the local library isn't configured (set `LIBRARY_ROOT`).")
    await m.reply("📚 **Library** — pick a category:", reply_markup=_categories_kb())


@Client.on_callback_query(filters.regex(r"^libcats$"))
@errors_cb
async def lib_cats_cb(_, query: CallbackQuery):
    await query.edit_message_text("📚 **Local library** — pick a category:", reply_markup=_categories_kb())


@Client.on_callback_query(filters.regex(r"^libnoop$"))
@errors_cb
async def lib_noop_cb(_, query: CallbackQuery):
    await query.answer()


@Client.on_callback_query(filters.regex(r"^lx:"))
@errors_cb
async def lib_browse_cb(_, query: CallbackQuery):
    parts = query.data.split(":")
    path = _TOKENS.get(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    if not path or not os.path.isdir(path):
        return await query.answer("list changed — reopen /library", show_alert=True)
    name = os.path.basename(path)
    await query.edit_message_text(f"📁 **{name}** — pick a folder or file:", reply_markup=_listing_kb(path, page))


@Client.on_callback_query(filters.regex(r"^lall:"))
@errors_cb
async def lib_play_all_cb(c: Client, query: CallbackQuery):
    path = _TOKENS.get(query.data.split(":")[1])
    if not path or not os.path.isdir(path):
        return await query.answer("list changed — reopen /library", show_alert=True)
    chat_id = query.message.chat.id
    member = await c.get_chat_member(chat_id, query.from_user.id)
    if not can_manage_vc(member):
        return await query.answer("💡 admins (manage video chats) only", show_alert=True)
    _, files = _entries(path)
    if not files:
        return await query.answer("no video files in this folder", show_alert=True)
    ok, reason = await ensure_assistant_in_chat(c, chat_id)
    if not ok:
        return await query.answer(f"❌ {reason}"[:190], show_alert=True)
    await drop_stale_queue(chat_id)
    drop_if_live(chat_id)  # radio/IPTV never hold a real queue slot
    set_active_thread(chat_id, getattr(query.message, "message_thread_id", None))
    await query.answer(f"queuing {len(files)}…")
    status = await query.message.reply(f"💡 **Queuing {len(files)} tracks** from `{os.path.basename(path)}`…")
    await _queue_folder(chat_id, files, status)


@Client.on_callback_query(filters.regex(r"^lp:"))
@errors_cb
async def lib_play_cb(c: Client, query: CallbackQuery):
    path = _TOKENS.get(query.data.split(":")[1])
    if not path or not os.path.isfile(path):
        return await query.answer("file not found — reopen /library", show_alert=True)
    chat_id = query.message.chat.id
    member = await c.get_chat_member(chat_id, query.from_user.id)
    if not can_manage_vc(member):
        return await query.answer("💡 admins (manage video chats) only", show_alert=True)
    ok, reason = await ensure_assistant_in_chat(c, chat_id)
    if not ok:
        return await query.answer(f"❌ {reason}"[:190], show_alert=True)
    await drop_stale_queue(chat_id)
    drop_if_live(chat_id)  # radio/IPTV never hold a real queue slot
    set_active_thread(chat_id, getattr(query.message, "message_thread_id", None))
    name = os.path.basename(path)
    # if the file has multiple audio tracks or any subtitles, offer a pick-at-play
    # selector; otherwise just play it
    audios, subs = await probe_tracks(path)
    if len(audios) > 1 or subs:
        await query.answer()
        SELECT[chat_id] = {
            "src": path, "name": name, "audios": audios, "subs": subs,
            "audio": audios[0][0] if audios else 0, "sub": None, "sub_ord": 0, "sub_image": False,
        }
        return await query.edit_message_text(_select_text(SELECT[chat_id]), reply_markup=_select_kb(SELECT[chat_id]))
    await query.answer("starting…")
    await _start_library_video(c, query.message, chat_id, name, path)


async def _start_library_video(c, msg, chat_id, name, streamable_src, status=None):
    """Stream a (possibly already-transcoded) local video; queue if busy."""
    status = status or msg
    if chat_id in QUEUE:
        pos = add_to_queue(chat_id, name[:70], streamable_src, streamable_src, "Video", 720)
        if pos == -1:
            return await status.edit(f"🚫 queue is full (max {MAX_QUEUE_SIZE}).")
        return await status.edit(f"💡 **Queued #{pos}:** `{name[:60]}`", reply_markup=control_panel)
    try:
        streamable = await prepare_for_stream(streamable_src, status)
        await call_py.play(chat_id, media_video(streamable, 720))
        add_to_queue(chat_id, name[:70], streamable, streamable, "Video", 720)
        await status.edit(f"🎬 **Now playing:** `{name[:60]}`", reply_markup=control_panel)
    except Exception as e:
        await status.edit(f"🚫 error: `{e}`")


async def _queue_folder(chat_id, files, status):
    """Queue every (name, path) in `files` (already sorted) as one playlist:
    play the first immediately if idle, or append the whole batch if something
    is already playing. Stops early if the queue fills up. `status` is edited
    with the outcome."""
    first_name, first_path = files[0]
    rest = files[1:]
    added, full = 0, False
    try:
        if chat_id in QUEUE:
            for name, path in files:
                if add_to_queue(chat_id, name[:70], path, path, "Video", 720) == -1:
                    full = True
                    break
                added += 1
            text = f"💡 **Queued {added} track{'s' if added != 1 else ''}**"
        else:
            streamable = await prepare_for_stream(first_path, status)
            await call_py.play(chat_id, media_video(streamable, 720))
            add_to_queue(chat_id, first_name[:70], streamable, streamable, "Video", 720)
            added = 1
            for name, path in rest:
                if add_to_queue(chat_id, name[:70], path, path, "Video", 720) == -1:
                    full = True
                    break
                added += 1
            text = f"🎬 **Now playing:** `{first_name[:60]}`"
            if added > 1:
                text += f"\n💡 queued {added - 1} more"
        if full:
            text += f" (queue full — rest skipped, max {MAX_QUEUE_SIZE})"
        await status.edit(text, reply_markup=control_panel)
    except Exception as e:
        await status.edit(f"🚫 error: `{e}`")


def _find_folder(needle):
    """First folder anywhere under an allowed category whose name contains
    `needle` (case-insensitive) — including a category root itself, e.g.
    'tutorials' matches the whole tutorials category."""
    for _, catpath in _top_categories():
        for root, dirs, _files in os.walk(catpath):
            dirs.sort(key=str.lower)
            if needle in os.path.basename(root).lower():
                return root
    return None


@Client.on_callback_query(filters.regex(r"^la:"))
@errors_cb
async def lib_audio_cb(c: Client, query: CallbackQuery):
    sel = SELECT.get(query.message.chat.id)
    if not sel:
        return await query.answer("selection expired — reopen /library", show_alert=True)
    sel["audio"] = int(query.data.split(":")[1])
    await query.answer("audio set")
    await query.edit_message_text(_select_text(sel), reply_markup=_select_kb(sel))


@Client.on_callback_query(filters.regex(r"^ls:"))
@errors_cb
async def lib_sub_cb(c: Client, query: CallbackQuery):
    sel = SELECT.get(query.message.chat.id)
    if not sel:
        return await query.answer("selection expired — reopen /library", show_alert=True)
    abs_i = int(query.data.split(":")[1])
    if abs_i == -1:
        sel["sub"] = None
    else:
        for a, ordn, _label, img in sel["subs"]:
            if a == abs_i:
                sel["sub"], sel["sub_ord"], sel["sub_image"] = a, ordn, img
                break
    await query.answer("subtitle set")
    await query.edit_message_text(_select_text(sel), reply_markup=_select_kb(sel))


@Client.on_callback_query(filters.regex(r"^lpgo$"))
@errors_cb
async def lib_go_cb(c: Client, query: CallbackQuery):
    chat_id = query.message.chat.id
    sel = SELECT.pop(chat_id, None)
    if not sel:
        return await query.answer("selection expired — reopen /library", show_alert=True)
    member = await c.get_chat_member(chat_id, query.from_user.id)
    if not can_manage_vc(member):
        return await query.answer("💡 admins (manage video chats) only", show_alert=True)
    await query.answer("preparing…")
    await ensure_assistant_in_chat(c, chat_id)
    await drop_stale_queue(chat_id)
    drop_if_live(chat_id)  # radio/IPTV never hold a real queue slot
    set_active_thread(chat_id, getattr(query.message, "message_thread_id", None))
    name = sel["name"]
    subtxt = "" if sel["sub"] is None else " · subs on"
    try:
        await query.edit_message_text(f"🎬 preparing `{name[:50]}`…")
        # Transcode the picked audio/subtitle track up front, busy or not — the
        # queue only stores a ready-to-stream path, so if we queued sel["src"]
        # (the raw file) instead, the selection would be silently lost and
        # playback would fall back to the file's default tracks.
        ready = await transcode_selection(
            sel["src"], sel["audio"], sel["sub"], sel["sub_ord"], sel["sub_image"], query.message,
        )
        if chat_id in QUEUE:
            pos = add_to_queue(chat_id, name[:70], ready, ready, "Video", 720)
            if pos == -1:
                return await query.edit_message_text(f"🚫 queue is full (max {MAX_QUEUE_SIZE}).")
            return await query.edit_message_text(f"💡 **Queued #{pos}:** `{name[:60]}`{subtxt}", reply_markup=control_panel)
        await call_py.play(chat_id, media_video(ready, 720))
        add_to_queue(chat_id, name[:70], ready, ready, "Video", 720)
        await query.edit_message_text(f"🎬 **Now playing:** `{name[:60]}`{subtxt}", reply_markup=control_panel)
    except Exception as e:
        await query.edit_message_text(f"🚫 error: `{e}`")


@Client.on_message(command(["lplay", f"lplay@{BOT_USERNAME}"]) & other_filters)
@errors
async def lplay(c: Client, m: Message):
    if _disabled():
        return await m.reply("📚 the local library isn't configured.")
    if len(m.command) < 2:
        return await m.reply("» usage: `/lplay <part of a filename or folder name>`  (or browse with /library)")
    needle = m.text.split(None, 1)[1].strip().lower()

    # A folder-name match queues the whole folder as a playlist (e.g. a show,
    # a course, a category like "tutorials"); tried before the single-file
    # search below since a folder hit is usually the more useful match.
    folder = _find_folder(needle)
    if folder:
        _, files = _entries(folder)
        if files:
            if not await ensure_can_play(c, m):
                return
            chat_id = m.chat.id
            drop_if_live(chat_id)  # radio/IPTV never hold a real queue slot
            set_active_thread(chat_id, m.message_thread_id)
            status = await m.reply(f"💡 **Queuing {len(files)} tracks** from `{os.path.basename(folder)}`…")
            return await _queue_folder(chat_id, files, status)

    found = None
    for _, catpath in _top_categories():       # only within allowed categories
        for root, _dirs, fnames in os.walk(catpath):
            for fn in fnames:
                if fn.lower().endswith(VIDEO_EXT) and needle in fn.lower():
                    found = (fn, os.path.join(root, fn))
                    break
            if found:
                break
        if found:
            break
    if not found:
        return await m.reply(f"🔍 nothing in the library matches `{needle}`")
    name, path = found
    if not await ensure_can_play(c, m):
        return
    chat_id = m.chat.id
    if chat_id in QUEUE:
        pos = add_to_queue(chat_id, name[:70], path, path, "Video", 720)
        if pos == -1:
            return await m.reply(f"🚫 queue is full (max {MAX_QUEUE_SIZE}).")
        return await m.reply(f"💡 **Queued #{pos}:** `{name[:60]}`", reply_markup=control_panel)
    status = await m.reply(f"🎬 preparing `{name[:50]}`…")
    try:
        streamable = await prepare_for_stream(path, status)
        await call_py.play(chat_id, media_video(streamable, 720))
        add_to_queue(chat_id, name[:70], streamable, streamable, "Video", 720)
        await status.edit(f"🎬 **Now playing:** `{name[:60]}`", reply_markup=control_panel)
    except Exception as e:
        await status.edit(f"🚫 error: `{e}`")


# ---------------------------------------------------------------------------
# Cartoon library (web-sourced) — integrated into /library
# ---------------------------------------------------------------------------

from program.cartoons import (
    _scrape_seasons as _c_seasons,
    _scrape_episodes as _c_episodes,
    _show_name as _c_show_name,
)

CARTOON_SHOWS = {"south-park": "South Park"}


def _cartoon_shows_kb():
    rows = []
    for sid, name in CARTOON_SHOWS.items():
        rows.append([InlineKeyboardButton(f"🌐 {name}", callback_data=f"cx:seasons:{sid}")])
    rows.append([InlineKeyboardButton("⬅ Back to Library", callback_data="libcats")])
    rows.append([InlineKeyboardButton("🗑 Close", callback_data="cls")])
    return InlineKeyboardMarkup(rows)


def _cartoon_seasons_kb(show_id, seasons):
    rows = []
    for s in seasons:
        rows.append([InlineKeyboardButton(
            f"🌐 {s['name']}",
            callback_data=f"cx:eps:{show_id}:{s['season']}",
        )])
    rows.append([InlineKeyboardButton("⬅ Shows", callback_data="cx:shows")])
    rows.append([InlineKeyboardButton("🗑 Close", callback_data="cls")])
    return InlineKeyboardMarkup(rows)


def _cartoon_episodes_kb(show_id, season_num, episodes, page=0):
    _PAGE = 8
    pages = max(1, (len(episodes) + _PAGE - 1) // _PAGE)
    page = max(0, min(page, pages - 1))
    rows = []
    for ep in episodes[page * _PAGE:(page + 1) * _PAGE]:
        s, e = ep["season"], ep["episode"]
        title = ep["title"][:45]
        dur = ep["duration"]
        label = f"🌐 S{s:02d}E{e:02d}: {title}"
        if dur:
            label += f" ({dur})"
        rows.append([InlineKeyboardButton(label, callback_data=f"cx:play:{show_id}:{s}:{e}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"cx:eps:{show_id}:{season_num}:{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="cx noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"cx:eps:{show_id}:{season_num}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅ Seasons", callback_data=f"cx:seasons:{show_id}")])
    rows.append([InlineKeyboardButton("🗑 Close", callback_data="cls")])
    return InlineKeyboardMarkup(rows)


@Client.on_callback_query(filters.regex(r"^cx noop$"))
@errors_cb
async def cx_noop(_, query: CallbackQuery):
    await query.answer()


@Client.on_callback_query(filters.regex(r"^cx:shows$"))
@errors_cb
async def cx_shows_cb(_, query: CallbackQuery):
    await query.edit_message_text(
        "📺 **Cartoon Library** — web-sourced content (🌐)\n\nPick a show:",
        reply_markup=_cartoon_shows_kb(),
    )


@Client.on_callback_query(filters.regex(r"^cx:seasons:(.+)$"))
@errors_cb
async def cx_seasons_cb(c: Client, query: CallbackQuery):
    show_id = query.matches[0].group(1)
    await query.answer("loading seasons…")
    seasons = await asyncio.to_thread(_c_seasons, show_id)
    if not seasons:
        return await query.edit_message_text("❌ failed to load seasons.")
    await query.edit_message_text(
        f"🌐 **{_c_show_name(show_id)}** — pick a season:",
        reply_markup=_cartoon_seasons_kb(show_id, seasons),
    )


@Client.on_callback_query(filters.regex(r"^cx:eps:([^:]+):(\d+)(?::(\d+))?$"))
@errors_cb
async def cx_episodes_cb(c: Client, query: CallbackQuery):
    show_id = query.matches[0].group(1)
    season_num = int(query.matches[0].group(2))
    page = int(query.matches[0].group(3)) if query.matches[0].group(3) else 0
    await query.answer("loading episodes…")
    episodes = await asyncio.to_thread(_c_episodes, show_id, season_num)
    if not episodes:
        return await query.edit_message_text("❌ no episodes found for this season.")
    await query.edit_message_text(
        f"🌐 **Episodes** — Season {season_num} — pick an episode:",
        reply_markup=_cartoon_episodes_kb(show_id, season_num, episodes, page),
    )


@Client.on_callback_query(filters.regex(r"^cx:play:([^:]+):(\d+):(\d+)$"))
@errors_cb
async def cx_play_cb(c: Client, query: CallbackQuery):
    show_id = query.matches[0].group(1)
    season_num = int(query.matches[0].group(2))
    episode_num = int(query.matches[0].group(3))

    chat_id = query.message.chat.id
    member = await c.get_chat_member(chat_id, query.from_user.id)
    if not can_manage_vc(member):
        return await query.answer("💡 admins (manage video chats) only", show_alert=True)

    ok, reason = await ensure_assistant_in_chat(c, chat_id)
    if not ok:
        return await query.answer(f"❌ {reason}"[:190], show_alert=True)

    await drop_stale_queue(chat_id)
    drop_if_live(chat_id)
    set_active_thread(chat_id, getattr(query.message, "message_thread_id", None))

    await query.answer("downloading…")
    await query.edit_message_text(f"📥 **Downloading S{season_num:02d}E{episode_num:02d}…**")

    from program.video import ytdl
    episodes = await asyncio.to_thread(_c_episodes, show_id, season_num)
    ep = None
    for e in episodes:
        if e["season"] == season_num and e["episode"] == episode_num:
            ep = e
            break
    if not ep:
        return await query.edit_message_text("❌ episode not found")

    title = ep["title"]
    url = f"https://www.southparkstudios.com{ep['url']}"
    ok, ytlink = await ytdl(url, query.message)
    if ok == 0:
        return await query.edit_message_text(f"❌ download failed\n\n`{ytlink}`")

    songname = f"S{season_num:02d}E{episode_num:02d}: {title}"
    if chat_id in QUEUE:
        pos = add_to_queue(chat_id, songname, ytlink, url, "Video", 720)
        if pos == -1:
            return await query.edit_message_text(f"🚫 queue is full (max {MAX_QUEUE_SIZE}).")
        return await query.edit_message_text(
            f"💡 **Queued #{pos}:** `{songname[:60]}`",
            reply_markup=control_panel,
        )
    try:
        await call_py.play(chat_id, media_video(ytlink, 720))
        add_to_queue(chat_id, songname, ytlink, url, "Video", 720)
        await query.edit_message_text(
            f"🎬 **Now playing:** `{songname[:60]}`",
            reply_markup=control_panel,
        )
    except Exception as e:
        await query.edit_message_text(f"🚫 error: `{e}`")
