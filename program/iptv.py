import asyncio
import re
import time
import urllib.request
import logging

from config import BOT_USERNAME, MAX_QUEUE_SIZE
from driver.filters import command, other_filters
from driver.queues import QUEUE, add_to_queue, clear_queue
from driver.utils import (
    can_manage_vc, control_panel, media_video,
    drop_stale_queue, ensure_assistant_in_chat,
)
from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

log = logging.getLogger(__name__)

_INDEX_URL = "https://iptv-org.github.io/iptv/index.m3u"
_REPO_URL = "https://github.com/iptv-org/iptv"
_COUNTRY_PLAYLISTS_URL = f"{_REPO_URL}#playlists-by-country"
_CACHE_TTL = 12 * 3600  # refresh every 12 h

_channels: list = []
_cache_ts: float = 0.0
_cache_lock = asyncio.Lock()

# per-chat last search results: chat_id -> list of channel dicts
_RESULTS: dict = {}


# ── playlist fetch & parse ────────────────────────────────────────────────────

def _parse_m3u(text: str) -> list:
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            nm = re.search(r'tvg-name="([^"]*)"', line)
            lm = re.search(r'tvg-logo="([^"]*)"', line)
            cm = re.search(r'tvg-country="([^"]*)"', line)
            gm = re.search(r'group-title="([^"]*)"', line)
            display = line.rsplit(",", 1)[-1].strip() if "," in line else ""
            name = (nm.group(1) if nm and nm.group(1) else display) or "?"
            logo = lm.group(1) if lm else ""
            country = cm.group(1).upper() if cm else ""
            group = gm.group(1) if gm else ""
            # next non-blank, non-comment line is the URL
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):
                j += 1
            if j < len(lines) and lines[j].strip().startswith("http"):
                out.append({"name": name, "url": lines[j].strip(),
                            "logo": logo, "country": country, "group": group})
                i = j + 1
                continue
        i += 1
    return out


async def _get_channels() -> list:
    global _channels, _cache_ts
    async with _cache_lock:
        if _channels and time.time() - _cache_ts < _CACHE_TTL:
            return _channels
        try:
            log.info("IPTV: fetching index playlist…")
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(_INDEX_URL, timeout=60)
                         .read().decode("utf-8", errors="ignore"),
            )
            parsed = _parse_m3u(raw)
            log.info("IPTV: loaded %d channels", len(parsed))
            _channels = parsed
            _cache_ts = time.time()
        except Exception as e:
            log.warning("IPTV: playlist fetch failed: %s", e)
    return _channels


# ── search ────────────────────────────────────────────────────────────────────

def _search(channels: list, query: str, limit: int = 8) -> list:
    q = query.lower()
    ranked = []
    for ch in channels:
        n = ch["name"].lower()
        if q in n:
            score = 0 if n == q else (1 if n.startswith(q) else 2)
            ranked.append((score, ch["name"], ch))
    ranked.sort(key=lambda x: (x[0], x[1]))
    return [r[2] for r in ranked[:limit]]


# ── keyboards ─────────────────────────────────────────────────────────────────

def _results_kb(results: list) -> InlineKeyboardMarkup:
    rows = []
    for i, ch in enumerate(results):
        label = ch["name"]
        if ch["country"]:
            label += f" · {ch['country']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"iptv:{i}")])
    rows.append([
        InlineKeyboardButton("📋 All channels on GitHub", url=_COUNTRY_PLAYLISTS_URL),
        InlineKeyboardButton("🔍 Search again", callback_data="iptv_help"),
    ])
    return InlineKeyboardMarkup(rows)


def _help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Channels by country", url=_COUNTRY_PLAYLISTS_URL),
        InlineKeyboardButton("🌐 Full M3U playlist", url=_INDEX_URL),
    ], [
        InlineKeyboardButton("📦 iptv-org/iptv on GitHub", url=_REPO_URL),
    ]])


# ── /iptv command ─────────────────────────────────────────────────────────────

@Client.on_message(command(["iptv", f"iptv@{BOT_USERNAME}"]) & other_filters)
async def iptv_cmd(c: Client, m: Message):
    await m.delete()
    chat_id = m.chat.id
    query = m.text.split(None, 1)[1].strip() if len(m.command) > 1 else ""

    if not query:
        await m.reply(
            "📺 **IPTV — live TV channels from around the world**\n\n"
            "Search by channel name:\n"
            "» `/iptv BBC`\n"
            "» `/iptv CNN`\n"
            "» `/iptv euronews`\n\n"
            "Not sure what to search for? Browse the full catalogue on GitHub — "
            "channels are organised by country, language, and category.\n"
            "Direct M3U playlist link is there too if you want to import it "
            "into VLC or any IPTV player.",
            reply_markup=_help_kb(),
            disable_web_page_preview=True,
        )
        return

    status = await c.send_message(chat_id, "📺 **Searching IPTV channels…**")
    channels = await _get_channels()
    if not channels:
        return await status.edit(
            "❌ **Could not load the channel list — try again later.**\n"
            f"You can also browse manually: [iptv-org/iptv]({_REPO_URL})",
            disable_web_page_preview=True,
        )

    results = _search(channels, query)
    if not results:
        return await status.edit(
            f"❌ **No channels found for** `{query}`\n\n"
            f"Browse the full list: [channels by country]({_COUNTRY_PLAYLISTS_URL})",
            disable_web_page_preview=True,
        )

    _RESULTS[chat_id] = results
    names = "\n".join(
        f"• {ch['name']}{' · ' + ch['country'] if ch['country'] else ''}"
        for ch in results
    )
    await status.edit(
        f"📺 **IPTV — results for** `{query}`\n\n{names}\n\n_Tap a channel to stream it:_",
        reply_markup=_results_kb(results),
    )


# ── callbacks ─────────────────────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^iptv:(\d+)$"))
async def iptv_pick(c: Client, query: CallbackQuery):
    chat_id = query.message.chat.id

    a = await c.get_chat_member(chat_id, query.from_user.id)
    if not can_manage_vc(a):
        return await query.answer(
            "💡 only admins with manage video chats permission can do this!",
            show_alert=True,
        )

    idx = int(query.matches[0].group(1))
    results = _RESULTS.get(chat_id, [])
    if idx >= len(results):
        return await query.answer("⚠️ results expired — search again", show_alert=True)

    ch = results[idx]
    name, url = ch["name"], ch["url"]
    label = f"{name}{' · ' + ch['country'] if ch['country'] else ''}"

    ok, reason = await ensure_assistant_in_chat(c, chat_id)
    if not ok:
        return await query.answer(f"❌ {reason}"[:190], show_alert=True)

    await query.answer(f"▶️ {name}")
    await query.message.edit(f"📺 **Tuning to** {label}…")

    try:
        await drop_stale_queue(chat_id)
        if chat_id in QUEUE:
            pos = add_to_queue(chat_id, label, url, url, "Video", 0)
            if pos == -1:
                return await query.message.edit(f"🚫 queue is full (max {MAX_QUEUE_SIZE}).")
            await query.message.edit(
                f"💡 **Added to queue »** `{pos}`\n📺 **Channel:** {label}",
                reply_markup=control_panel,
            )
        else:
            from driver.clients import call_py
            await call_py.play(chat_id, media_video(url))
            clear_queue(chat_id)
            add_to_queue(chat_id, label, url, url, "Video", 0)
            await query.message.edit(
                f"📺 **Now streaming:** {label}\n🔴 _Live IPTV_",
                reply_markup=control_panel,
            )
    except Exception as e:
        log.warning("IPTV: stream error for %s: %s", url, e)
        await query.message.edit(f"❌ **Stream failed:** `{e}`")


@Client.on_callback_query(filters.regex(r"^iptv_help$"))
async def iptv_help_cb(_, query: CallbackQuery):
    await query.answer()
    await query.message.edit(
        "📺 **IPTV search**\n\nType `/iptv <channel name>` to search.\n\n"
        "Browse the full catalogue on GitHub:",
        reply_markup=_help_kb(),
    )
