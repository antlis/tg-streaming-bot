import os
import asyncio
import urllib.request

from config import BOT_USERNAME
from driver.filters import command, other_filters
from driver.queues import QUEUE, add_to_queue, clear_queue
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
        await query.edit_message_text(f"📻 **Now playing radio:** {name[:60]}", reply_markup=control_panel)
    except Exception as e:
        await query.edit_message_text(f"🚫 error: `{e}`")
