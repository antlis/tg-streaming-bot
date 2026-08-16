import asyncio
import re
import time
import urllib.request
import logging

from config import BOT_USERNAME
from driver.decorators import errors, errors_cb
from driver.filters import command, other_filters
from driver.queues import add_to_queue, clear_queue, set_live, set_active_thread
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

# Some HLS master playlists (multi-variant) confuse ffmpeg 5.1.x.
# Download the master, pick a single variant URL, and feed that instead.
_HLS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
)
_HLS_VARIANT_PREFERENCE = ["1080", "720", "480", "360", "240"]


def _resolve_hls_variant(url: str) -> str:
    """If *url* is an HLS master playlist return the best single-variant URL,
    otherwise return *url* unchanged."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": _HLS_UA, "Referer": "https://rutube.ru/"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8", errors="ignore")

        # Not a master playlist → use the URL as-is
        if "#EXT-X-STREAM-INF" not in body:
            return url

        # Parse variant URLs and their resolutions
        variants: list[tuple[int, str]] = []  # (height, url)
        lines = body.splitlines()
        stream_inf = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#EXT-X-STREAM-INF:"):
                stream_inf = stripped
            elif stripped.startswith("http") and stream_inf is not None:
                # Try to extract RESOLUTION from the EXT-X-STREAM-INF line
                import re as _re
                m = _re.search(r"RESOLUTION=(\d+)x(\d+)", stream_inf)
                height = int(m.group(2)) if m else 0
                variants.append((height, stripped))
                stream_inf = None

        if not variants:
            return url

        # Sort by height descending, pick the one closest to preferred
        variants.sort(key=lambda x: -x[0])
        for pref in _HLS_VARIANT_PREFERENCE:
            for h, u in variants:
                if str(h).startswith(pref):
                    log.info("HLS variant: %s → %dp", u, h)
                    return u
        # Fallback: highest available
        best = variants[0]
        log.info("HLS variant (fallback): %s → %dp", best[1], best[0])
        return best[1]
    except Exception as e:
        log.warning("HLS variant resolution failed (%s), using original URL", e)
        return url

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
@errors
async def iptv_cmd(c: Client, m: Message):
    # snapshot before any await — see program/music.py's play() for why
    cmd_args = m.command
    await m.delete()
    chat_id = m.chat.id
    query = m.text.split(None, 1)[1].strip() if len(cmd_args) > 1 else ""

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

    thread_id = getattr(m, "message_thread_id", None)
    status = await c.send_message(chat_id, "📺 **Searching IPTV channels…**",
                                  message_thread_id=thread_id)
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
@errors_cb
async def iptv_pick(c: Client, query: CallbackQuery):
    chat_id = query.message.chat.id
    log.info("IPTV: pick callback chat=%s user=%s data=%s",
             chat_id, getattr(query.from_user, "id", None), query.data)

    # Anonymous admins have no from_user
    if not query.from_user:
        return await query.answer("Please use a regular user account, not an anonymous admin.", show_alert=True)

    a = await c.get_chat_member(chat_id, query.from_user.id)
    if not can_manage_vc(a):
        log.info("IPTV: user %s blocked — status=%s", query.from_user.id, a.status)
        return await query.answer("💡 only admins with manage video chats permission", show_alert=True)

    idx = int(query.matches[0].group(1))
    results = _RESULTS.get(chat_id, [])
    if idx >= len(results):
        log.info("IPTV: results expired chat=%s idx=%s cache_len=%s", chat_id, idx, len(results))
        # Edit the message so the user notices (popup alerts are easy to miss)
        try:
            await query.message.edit("⚠️ Search session expired — run `/iptv` again.")
        except Exception:
            await query.answer("⚠️ Session expired — run /iptv again.", show_alert=True)
        return

    ch = results[idx]
    name, url = ch["name"], ch["url"]
    label = f"{name}{' · ' + ch['country'] if ch['country'] else ''}"

    ok, reason = await ensure_assistant_in_chat(c, chat_id)
    if not ok:
        log.warning("IPTV: ensure_assistant_in_chat failed: %s", reason)
        return await query.answer(f"❌ {reason}"[:190], show_alert=True)

    await query.answer(f"▶️ {name}")
    await query.message.edit(f"📺 **Tuning to** {label}…")

    logo = ch.get("logo", "")
    thread_id = getattr(query.message, "message_thread_id", None)
    log.info("IPTV: %s selected channel %s url=%s thread=%s", query.from_user.id, label, url, thread_id)

    async def _finish(caption: str):
        """Replace the picker with a photo card once the stream is confirmed started."""
        try:
            await query.message.delete()
        except Exception:
            pass
        if logo:
            try:
                await c.send_photo(chat_id, logo, caption=caption, reply_markup=control_panel,
                                   message_thread_id=thread_id)
                return
            except Exception as photo_err:
                log.warning("IPTV: send_photo failed (%s), falling back to text", photo_err)
        await c.send_message(chat_id, caption, reply_markup=control_panel,
                             message_thread_id=thread_id)

    async def _err(msg: str):
        """Show error — edit the picker if it still exists, otherwise send a new message."""
        log.warning("IPTV: error for %s: %s", label, msg)
        try:
            await query.message.edit(f"❌ **IPTV error:** {msg}")
        except Exception:
            await c.send_message(chat_id, f"❌ **IPTV error:** {msg}",
                                 message_thread_id=thread_id)

    from driver.clients import call_py
    from pytgcalls.types import MediaStream
    from pytgcalls.types.stream import AudioQuality, VideoQuality

    try:
        await drop_stale_queue(chat_id)
        log.info("IPTV: calling play() for %s", label)
        # Resolve HLS master playlists to a single variant — ffmpeg 5.1
        # chokes on multi-variant manifests from certain CDNs (Rutube etc.)
        resolved = _resolve_hls_variant(url)
        if resolved != url:
            log.info("IPTV: resolved HLS variant for %s", label)
        stream = MediaStream(
            resolved,
            audio_parameters=AudioQuality.HIGH,
            video_parameters=VideoQuality.HD_720p,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://rutube.ru/",
            },
        )
        await call_py.play(chat_id, stream)
        # IPTV takes over immediately, like /radio — it's a single live
        # broadcast, never a real queue slot.
        clear_queue(chat_id)
        add_to_queue(chat_id, label, url, url, "Video", 0)
        set_live(chat_id, True)
        set_active_thread(chat_id, thread_id)
        log.info("IPTV: play() succeeded for %s", label)
        await _finish(f"📺 **Now streaming:** {label}\n🔴 _Live IPTV_")
    except Exception as e:
        import traceback
        log.error("IPTV: pytgcalls play() failed for %s\n%s", label, traceback.format_exc())
        await _err(f"`{e}`")


@Client.on_callback_query(filters.regex(r"^iptv_help$"))
@errors_cb
async def iptv_help_cb(_, query: CallbackQuery):
    await query.answer()
    await query.message.edit(
        "📺 **IPTV search**\n\nType `/iptv <channel name>` to search.\n\n"
        "Browse the full catalogue on GitHub:",
        reply_markup=_help_kb(),
    )
