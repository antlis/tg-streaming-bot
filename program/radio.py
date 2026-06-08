import os
import re
import asyncio
import urllib.request

from config import BOT_USERNAME, RADIO_IMG
from driver.filters import command, other_filters
from driver.queues import QUEUE, add_to_queue, clear_queue, get_queue
from driver.clients import call_py
from driver.utils import (
    control_panel,
    media_audio,
    ensure_assistant_in_chat,
    drop_stale_queue,
    can_manage_vc,
)
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

# Active now-playing cards: chat_id -> {"msg", "stream", "name", "last"}
RADIO = {}


def _icy_metadata(url):
    """Best-effort ICY read: station name (header) + current track (StreamTitle
    from the first inline metadata block). Returns {} on any failure."""
    info = {}
    try:
        req = urllib.request.Request(url, headers={"Icy-MetaData": "1", "User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=12)
    except Exception:
        return info
    try:
        name = resp.headers.get("icy-name")
        if name:
            info["name"] = name.strip()
        metaint = int(resp.headers.get("icy-metaint", "0") or 0)
        if metaint > 0:
            resp.read(metaint)               # skip one audio block
            length = resp.read(1)
            if length:
                meta = resp.read(ord(length) * 16).decode("utf-8", "ignore")
                m = re.search(r"StreamTitle='(.*?)';", meta)
                if m and m.group(1).strip():
                    info["title"] = m.group(1).strip()
    except Exception:
        pass
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return info


def _caption(station, track):
    cap = f"📻 **Now playing radio**\n🎙 {station[:60]}"
    if track:
        cap += f"\n🎶 `{track[:96]}`"
    return cap

# Curated internet-radio presets (same set as tg-mpv-bot). Override with the
# RADIO_STATIONS env var: "Name=url,Name2=url2,...".
DEFAULT_STATIONS = [
    ("Record Techno", "https://radiorecord.hostingradio.ru/techno96.aacp"),
    ("Record Trancemission", "https://radiorecord.hostingradio.ru/tm96.aacp"),
    ("Record Deep", "https://radiorecord.hostingradio.ru/deep96.aacp"),
    ("Hardcore Radio NL — hardcore/gabber", "http://stream.hardcoreradio.nl:8000/;"),
    ("Radio Paradise — eclectic rock", "https://stream.radioparadise.com/mp3-192"),
    ("FIP — eclectic, Radio France", "https://icecast.radiofrance.fr/fip-midfi.mp3"),
    ("Nightride FM — synthwave", "https://stream.nightride.fm/nightride.mp3"),
    ("KEXP Seattle — indie", "https://kexp-mp3-128.streamguys1.com/kexp128.mp3"),
    ("SomaFM Groove Salad — ambient", "https://somafm.com/groovesalad.pls"),
    ("SomaFM Drone Zone — ambient", "https://somafm.com/dronezone.pls"),
    ("SomaFM Indie Pop Rocks! — alternative", "https://somafm.com/indiepop.pls"),
    ("SomaFM Secret Agent — lounge", "https://somafm.com/secretagent.pls"),
    ("SomaFM Deep Space One — ambient", "https://somafm.com/deepspaceone.pls"),
    ("SomaFM Underground 80s — alternative", "https://somafm.com/u80s.pls"),
    ("SomaFM Space Station Soma — electronic", "https://somafm.com/spacestation.pls"),
    ("SomaFM Lush — electronic", "https://somafm.com/lush.pls"),
    ("SomaFM Synphaera Radio — ambient", "https://somafm.com/synphaera.pls"),
    ("SomaFM Left Coast 70s", "https://somafm.com/seventies.pls"),
    ("SomaFM DEF CON Radio — electronic", "https://somafm.com/defcon.pls"),
    ("SomaFM Folk Forward", "https://somafm.com/folkfwd.pls"),
    ("SomaFM Boot Liquor — americana", "https://somafm.com/bootliquor.pls"),
    ("SomaFM Beat Blender — electronic", "https://somafm.com/beatblender.pls"),
    ("SomaFM Bossa Beyond — bossanova", "https://somafm.com/bossa.pls"),
    ("SomaFM ThistleRadio — celtic", "https://somafm.com/thistle.pls"),
    ("SomaFM The Trip — electronic", "https://somafm.com/thetrip.pls"),
    ("SomaFM PopTron — alternative", "https://somafm.com/poptron.pls"),
    ("SomaFM Heavyweight Reggae", "https://somafm.com/reggae.pls"),
    ("SomaFM Sonic Universe — jazz", "https://somafm.com/sonicuniverse.pls"),
    ("SomaFM Illinois Street Lounge", "https://somafm.com/illstreet.pls"),
    ("SomaFM Suburbs of Goa — world", "https://somafm.com/suburbsofgoa.pls"),
    ("SomaFM The Dark Zone — ambient", "https://somafm.com/darkzone.pls"),
    ("SomaFM Vaporwaves — electronic", "https://somafm.com/vaporwaves.pls"),
    ("SomaFM cliqhop idm — electronic", "https://somafm.com/cliqhop.pls"),
    ("SomaFM Dub Step Beyond — electronic", "https://somafm.com/dubstep.pls"),
    ("SomaFM Metal Detector", "https://somafm.com/metal.pls"),
    ("SomaFM Covers — eclectic", "https://somafm.com/covers.pls"),
    ("SomaFM Doomed — ambient", "https://somafm.com/doomed.pls"),
]


def _load_stations():
    raw = os.getenv("RADIO_STATIONS", "").strip()
    if not raw:
        return DEFAULT_STATIONS
    out = []
    for pair in raw.split(","):
        if "=" in pair:
            name, url = pair.split("=", 1)
            if name.strip() and url.strip():
                out.append((name.strip(), url.strip()))
    return out or DEFAULT_STATIONS


STATIONS = _load_stations()
PAGE = 8


async def _resolve(url):
    """ffmpeg can't follow .pls/.m3u playlists, so fetch them and pull out the
    first real stream URL. Direct streams (.mp3/.aac/icecast/.m3u8) pass through."""
    low = url.lower().split("?")[0]
    if not (low.endswith(".pls") or (low.endswith(".m3u") and not low.endswith(".m3u8"))):
        return url

    def fetch():
        text = urllib.request.urlopen(url, timeout=12).read().decode("utf-8", "ignore")
        for line in text.splitlines():
            s = line.strip()
            if s.lower().startswith("file") and "=" in s:
                return s.split("=", 1)[1].strip()
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("http"):
                return s
        return url

    try:
        return await asyncio.to_thread(fetch)
    except Exception:
        return url


def _kb(page):
    page = max(0, min(page, (len(STATIONS) - 1) // PAGE))
    rows = [
        [InlineKeyboardButton(f"📻 {STATIONS[i][0][:52]}", callback_data=f"rd:{i}")]
        for i in range(page * PAGE, min((page + 1) * PAGE, len(STATIONS)))
    ]
    pages = (len(STATIONS) + PAGE - 1) // PAGE
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"rdp:{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="rdnoop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"rdp:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🗑 Close", callback_data="cls")])
    return InlineKeyboardMarkup(rows)


@Client.on_message(command(["radio", f"radio@{BOT_USERNAME}"]) & other_filters)
async def radio(c: Client, m: Message):
    await m.reply("📻 **Radio** — pick a station:", reply_markup=_kb(0))


@Client.on_callback_query(filters.regex(r"^rdnoop$"))
async def radio_noop(_, query: CallbackQuery):
    await query.answer()


@Client.on_callback_query(filters.regex(r"^rdp:"))
async def radio_page(_, query: CallbackQuery):
    page = int(query.data.split(":")[1])
    await query.edit_message_text("📻 **Radio** — pick a station:", reply_markup=_kb(page))


@Client.on_callback_query(filters.regex(r"^rd:"))
async def radio_tune(c: Client, query: CallbackQuery):
    try:
        name, url = STATIONS[int(query.data.split(":")[1])]
    except (ValueError, IndexError):
        return await query.answer("list changed — reopen /radio", show_alert=True)
    chat_id = query.message.chat.id
    member = await c.get_chat_member(chat_id, query.from_user.id)
    if not can_manage_vc(member):
        return await query.answer("💡 admins (manage video chats) only", show_alert=True)
    ok, reason = await ensure_assistant_in_chat(c, chat_id)
    if not ok:
        return await query.answer(f"❌ {reason}"[:190], show_alert=True)
    await query.answer("tuning in…")
    await query.edit_message_text(f"📻 tuning **{name[:60]}**…")
    stream = await _resolve(url)
    try:
        await drop_stale_queue(chat_id)
        await call_py.play(chat_id, media_audio(stream))
        clear_queue(chat_id)  # radio takes over — it's a single live stream
        add_to_queue(chat_id, name[:70], stream, url, "Audio", 0)
    except Exception as e:
        return await query.edit_message_text(f"🚫 error: `{e}`")
    # now-playing photo card, refreshed by radio_updater()
    track = (await asyncio.to_thread(_icy_metadata, stream)).get("title")
    try:
        await query.message.delete()
    except Exception:
        pass
    card = await c.send_photo(chat_id, RADIO_IMG, caption=_caption(name, track), reply_markup=control_panel)
    RADIO[chat_id] = {"msg": card, "stream": stream, "name": name, "last": _caption(name, track)}


async def radio_updater():
    """Refresh the now-playing track on active radio cards every ~25s; drop the
    card once the chat is no longer playing that station."""
    while True:
        await asyncio.sleep(25)
        for chat_id in list(RADIO.keys()):
            st = RADIO[chat_id]
            q = get_queue(chat_id)
            if not q or q[0][1] != st["stream"]:   # station changed / stopped
                RADIO.pop(chat_id, None)
                continue
            track = (await asyncio.to_thread(_icy_metadata, st["stream"])).get("title")
            cap = _caption(st["name"], track)
            if cap != st.get("last"):
                st["last"] = cap
                try:
                    await st["msg"].edit_caption(cap, reply_markup=control_panel)
                except Exception:
                    pass
