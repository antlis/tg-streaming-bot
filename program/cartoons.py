import re
import asyncio
import logging
import urllib.request
import json
from time import time

from config import BOT_USERNAME, MAX_QUEUE_SIZE
from driver.decorators import errors, errors_cb
from driver.filters import command, other_filters
from driver.queues import QUEUE, add_to_queue, drop_if_live, set_active_thread
from driver.clients import call_py, user
from driver.utils import (
    control_panel,
    media_video,
    ensure_can_play,
    ensure_assistant_in_chat,
    drop_stale_queue,
    can_manage_vc,
)
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserAlreadyParticipant, UserNotParticipant, PeerIdInvalid
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

log = logging.getLogger(__name__)

_BASE = "https://www.southparkstudios.com"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_CACHE_TTL = 3600  # 1 hour

# In-memory cache: {show_id: {"seasons": [...], "episodes": {season_num: [...]}, "ts": float}}
_CACHE = {}


# ---------------------------------------------------------------------------
# Scraping helpers
# ---------------------------------------------------------------------------

def _fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read().decode())


def _deep_find_episodes(d, depth=0):
    """Recursively find episode URL entries in the nested React SSR JSON."""
    if depth > 15:
        return
    if isinstance(d, dict):
        url = d.get("url", "")
        if isinstance(url, str) and url.startswith("/episodes/"):
            meta = d.get("meta", {})
            header = meta.get("header", {}).get("title", {}).get("text", "")
            sub = meta.get("subHeader", "")
            desc = meta.get("description", "")
            date = meta.get("date", "")
            media = d.get("media", {})
            duration = media.get("duration", "")
            img = media.get("image", {}).get("url", "")
            # Parse "S1 • E1" from header
            m = re.match(r"S(\d+)\s*[•·]\s*E(\d+)", header)
            season = int(m.group(1)) if m else 0
            episode = int(m.group(2)) if m else 0
            yield {
                "title": sub or url.split("/")[-1].replace("-", " ").title(),
                "url": url,
                "season": season,
                "episode": episode,
                "duration": duration,
                "description": desc,
                "thumbnail": img,
                "date": date,
            }
        for v in d.values():
            yield from _deep_find_episodes(v, depth + 1)
    elif isinstance(d, list):
        for item in d:
            yield from _deep_find_episodes(item, depth + 1)


def _deep_find_seasons(d, depth=0):
    """Recursively find season URL entries in the nested React SSR JSON."""
    if depth > 15:
        return
    if isinstance(d, dict):
        url = d.get("url", "")
        if isinstance(url, str) and re.match(r"/seasons/south-park/.+/season-\d+", url):
            meta = d.get("meta", {})
            sub = meta.get("subHeader", "")
            # Extract season number from URL
            m = re.search(r"season-(\d+)", url)
            season_num = int(m.group(1)) if m else 0
            yield {"name": sub or f"Season {season_num}", "url": url, "season": season_num}
        for v in d.values():
            yield from _deep_find_seasons(v, depth + 1)
    elif isinstance(d, list):
        for item in d:
            yield from _deep_find_seasons(item, depth + 1)


def _scrape_seasons(show_id="south-park"):
    """Fetch the list of seasons for a show."""
    now = time()
    cached = _CACHE.get(show_id)
    if cached and now - cached["ts"] < _CACHE_TTL and "seasons" in cached:
        return cached["seasons"]

    url = f"{_BASE}/seasons/{show_id}?json=true"
    try:
        data = _fetch_json(url)
    except Exception as e:
        log.warning("scrape_seasons failed: %s", e)
        return []

    seasons = list(_dedup_seasons(_deep_find_seasons(data)))
    # Season 1 is shown inline on the main page (no separate link).
    # If we found seasons starting from 2+, prepend season 1.
    if seasons and seasons[0]["season"] > 1:
        seasons.insert(0, {"name": "Season 1", "url": "/seasons/south-park", "season": 1})
    elif not seasons:
        # Fallback: at minimum we know season 1 exists
        seasons = [{"name": "Season 1", "url": "/seasons/south-park", "season": 1}]

    if show_id not in _CACHE:
        _CACHE[show_id] = {"ts": now}
    _CACHE[show_id]["seasons"] = seasons
    return seasons


def _dedup_seasons(gen):
    seen = set()
    for s in gen:
        if s["season"] not in seen:
            seen.add(s["season"])
            yield s


def _scrape_episodes(show_id, season_num):
    """Fetch the episode list for a specific season."""
    now = time()
    cached = _CACHE.get(show_id, {})
    eps = cached.get("episodes", {}).get(season_num)
    if eps is not None and now - cached.get("ts", 0) < _CACHE_TTL:
        return eps

    # Season 1 episodes are inline on the main page (not a separate URL)
    if season_num == 1:
        eps = _scrape_episodes_from_main(show_id)
    else:
        seasons = _scrape_seasons(show_id)
        season_url = None
        for s in seasons:
            if s["season"] == season_num:
                season_url = s["url"]
                break
        if not season_url:
            return []
        full_url = f"{_BASE}{season_url}?json=true"
        try:
            data = _fetch_json(full_url)
        except Exception as e:
            log.warning("scrape_episodes failed for %s S%d: %s", show_id, season_num, e)
            return []
        eps = list(_deep_find_episodes(data))

    if show_id not in _CACHE:
        _CACHE[show_id] = {"ts": now}
    _CACHE[show_id].setdefault("episodes", {})[season_num] = eps
    return eps


def _scrape_episodes_from_main(show_id):
    """Scrape episodes from the main seasons page (used for season 1)."""
    url = f"{_BASE}/seasons/{show_id}?json=true"
    try:
        data = _fetch_json(url)
    except Exception as e:
        log.warning("scrape_episodes_from_main failed: %s", e)
        return []
    return list(_deep_find_episodes(data))


# ---------------------------------------------------------------------------
# Inline keyboards
# ---------------------------------------------------------------------------

def _shows_kb():
    rows = [
        [InlineKeyboardButton("🌐 South Park", callback_data="cartoon:seasons:south-park")],
    ]
    rows.append([InlineKeyboardButton("🗑 Close", callback_data="cls")])
    return InlineKeyboardMarkup(rows)


def _seasons_kb(show_id, seasons):
    rows = []
    for s in seasons:
        rows.append([InlineKeyboardButton(
            f"🌐 {s['name']}",
            callback_data=f"cartoon:eps:{show_id}:{s['season']}",
        )])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="cartoon:shows")])
    rows.append([InlineKeyboardButton("🗑 Close", callback_data="cls")])
    return InlineKeyboardMarkup(rows)


def _episodes_kb(show_id, season_num, episodes, page=0):
    PAGE = 8
    pages = max(1, (len(episodes) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    rows = []
    for ep in episodes[page * PAGE:(page + 1) * PAGE]:
        s = ep["season"]
        e = ep["episode"]
        title = ep["title"][:45]
        dur = ep["duration"]
        label = f"🌐 S{s:02d}E{e:02d}: {title}"
        if dur:
            label += f" ({dur})"
        rows.append([InlineKeyboardButton(label, callback_data=f"cartoon:play:{show_id}:{s}:{e}")])

    # Pagination
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"cartoon:eps:{show_id}:{season_num}:{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="cartoon noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"cartoon:eps:{show_id}:{season_num}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("⬅ Seasons", callback_data=f"cartoon:seasons:{show_id}")])
    rows.append([InlineKeyboardButton("🗑 Close", callback_data="cls")])
    return InlineKeyboardMarkup(rows)


def _episodes_text(show_id, season_num):
    return f"🌐 **Episodes** — Season {season_num} — pick an episode:"


# ---------------------------------------------------------------------------
# Play helper
# ---------------------------------------------------------------------------

async def _play_episode(c, msg, chat_id, show_id, season_num, episode_num):
    """Download + play a single episode."""
    from program.video import ytdl

    episodes = _scrape_episodes(show_id, season_num)
    ep = None
    for e in episodes:
        if e["season"] == season_num and e["episode"] == episode_num:
            ep = e
            break
    if not ep:
        return await msg.edit("❌ episode not found")

    title = ep["title"]
    url = f"{_BASE}{ep['url']}"
    ok, ytlink = await ytdl(url, msg)
    if ok == 0:
        return await msg.edit(f"❌ download failed\n\n`{ytlink}`")

    songname = f"S{season_num:02d}E{episode_num:02d}: {title}"
    if chat_id in QUEUE:
        pos = add_to_queue(chat_id, songname, ytlink, url, "Video", 720)
        if pos == -1:
            return await msg.edit(f"🚫 queue is full (max {MAX_QUEUE_SIZE}).")
        return await msg.edit(
            f"💡 **Queued #{pos}:** `{songname[:60]}`",
            reply_markup=control_panel,
        )
    try:
        await call_py.play(chat_id, media_video(ytlink, 720))
        add_to_queue(chat_id, songname, ytlink, url, "Video", 720)
        await msg.edit(
            f"🎬 **Now playing:** `{songname[:60]}`",
            reply_markup=control_panel,
        )
    except Exception as e:
        await msg.edit(f"🚫 error: `{e}`")


# ---------------------------------------------------------------------------
# /cartoons command
# ---------------------------------------------------------------------------

@Client.on_message(command(["cartoons", f"cartoons@{BOT_USERNAME}"]) & other_filters)
@errors
async def cartoons_cmd(c: Client, m: Message):
    await m.reply(
        "📺 **Cartoon Library** — web-sourced content (🌐)\n\nPick a show:",
        reply_markup=_shows_kb(),
    )


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------

@Client.on_callback_query(filters.regex(r"^cartoon noop$"))
@errors_cb
async def cartoon_noop(_, query: CallbackQuery):
    await query.answer()


@Client.on_callback_query(filters.regex(r"^cartoon:shows$"))
@errors_cb
async def cartoon_shows_cb(_, query: CallbackQuery):
    await query.edit_message_text(
        "📺 **Cartoon Library** — web-sourced content (🌐)\n\nPick a show:",
        reply_markup=_shows_kb(),
    )


@Client.on_callback_query(filters.regex(r"^cartoon:seasons:(.+)$"))
@errors_cb
async def cartoon_seasons_cb(c: Client, query: CallbackQuery):
    show_id = query.matches[0].group(1)
    await query.answer("loading seasons…")
    seasons = await asyncio.to_thread(_scrape_seasons, show_id)
    if not seasons:
        return await query.edit_message_text("❌ failed to load seasons.")
    await query.edit_message_text(
        f"🌐 **{_show_name(show_id)}** — pick a season:",
        reply_markup=_seasons_kb(show_id, seasons),
    )


@Client.on_callback_query(filters.regex(r"^cartoon:eps:([^:]+):(\d+)(?::(\d+))?$"))
@errors_cb
async def cartoon_episodes_cb(c: Client, query: CallbackQuery):
    show_id = query.matches[0].group(1)
    season_num = int(query.matches[0].group(2))
    page = int(query.matches[0].group(3)) if query.matches[0].group(3) else 0
    await query.answer("loading episodes…")
    episodes = await asyncio.to_thread(_scrape_episodes, show_id, season_num)
    if not episodes:
        return await query.edit_message_text("❌ no episodes found for this season.")
    await query.edit_message_text(
        _episodes_text(show_id, season_num),
        reply_markup=_episodes_kb(show_id, season_num, episodes, page),
    )


@Client.on_callback_query(filters.regex(r"^cartoon:play:([^:]+):(\d+):(\d+)$"))
@errors_cb
async def cartoon_play_cb(c: Client, query: CallbackQuery):
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
    await query.edit_message_text(
        f"📥 **Downloading S{season_num:02d}E{episode_num:02d}…**"
    )
    await _play_episode(c, query.message, chat_id, show_id, season_num, episode_num)


def _show_name(show_id):
    return {
        "south-park": "South Park",
    }.get(show_id, show_id)
