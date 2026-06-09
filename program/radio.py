import io
import os
import re
import time
import asyncio
import hashlib
import urllib.request

from PIL import Image, ImageDraw, ImageFont
from config import BOT_USERNAME, RADIO_IMG
from driver.filters import command, other_filters
from driver.queues import QUEUE, add_to_queue, clear_queue, get_queue
from driver.clients import call_py
from driver.decorators import authorized_users_only
from driver.utils import (
    control_panel,
    ensure_assistant_in_chat,
    drop_stale_queue,
    can_manage_vc,
)
from pytgcalls.types import MediaStream, AudioQuality, VideoQuality
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


def _render_card(station):
    """Render a static placeholder image (station name + LIVE over the radio art)
    to stream as the voice-chat video feed. Cached per station."""
    key = hashlib.md5(station.encode()).hexdigest()[:10]
    out = os.path.join("downloads", f"rcard_{key}.png")
    if os.path.exists(out):
        return out
    W, H = 1280, 720
    try:
        if str(RADIO_IMG).startswith("http"):
            data = urllib.request.urlopen(RADIO_IMG, timeout=12).read()
            bg = Image.open(io.BytesIO(data)).convert("RGB")
        else:
            bg = Image.open(RADIO_IMG).convert("RGB")
        bg = bg.resize((W, H))
        bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.5)  # darken for legibility
    except Exception:
        bg = Image.new("RGB", (W, H), (18, 18, 28))
    draw = ImageDraw.Draw(bg)
    try:
        f_big = ImageFont.truetype("driver/source/medium.ttf", 66)
        f_small = ImageFont.truetype("driver/source/regular.ttf", 38)
    except Exception:
        f_big = f_small = ImageFont.load_default()
    name = station if len(station) <= 38 else station[:37] + "…"
    draw.text((64, 300), name, fill=(255, 255, 255), font=f_big)
    draw.text((64, 392), "RADIO  •  LIVE", fill=(255, 90, 90), font=f_small)
    try:
        bg.save(out)
        return out
    except Exception:
        return None

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


# Active recordings: chat_id -> {proc, out, name, url, tracks, stop(Event), status, start}
RECORDING = {}
RECORD_MAX = 3600  # hard cap (1 hour)


def _dur(sec):
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _rec_caption(name, tracks, secs, icon="🎙"):
    cap = f"{icon} **{name[:60]}** · {_dur(secs)}"
    if tracks:
        cap += "\n\n**Tracklist:**\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(tracks))
    return cap[:1024]


_rec_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏹ Stop & send", callback_data="recstop")]])


async def _begin_record(c: Client, chat_id, secs, send_status):
    """Start a recording of whatever is playing. send_status(text, markup) sends
    the status message and returns it — lets /record and the panel button share
    this, replying inline vs sending into the chat respectively."""
    if chat_id in RECORDING:
        return await send_status("🔴 already recording — tap **⏹ Stop & send** (or /stoprec).", None)
    q = get_queue(chat_id)
    if not q:
        return await send_status("❌ nothing is playing to record.", None)
    name, url, typ = q[0][0], q[0][1], q[0][3]
    is_video = (typ == "Video")
    is_http = str(url).startswith("http")
    # Local files: start at the current playback position and pace at realtime
    # (-re) so "stop" captures exactly what you've watched/heard since you began.
    # Live http streams are already realtime, so neither flag is needed.
    pre = []
    if not is_http:
        try:
            pre += ["-ss", str(int(await call_py.time(chat_id)))]
        except Exception:
            pass
        pre += ["-re"]
    if is_video:
        out = os.path.join("downloads", f"rec_{chat_id}.mp4")
        # The streamed source is already H.264/AAC so copy is instant & lossless;
        # fragmented mp4 stays playable even if the stop kills ffmpeg mid-write.
        args = ["-i", url, "-t", str(secs), "-c", "copy",
                "-movflags", "+frag_keyframe+empty_moov+default_base_moof", out]
    else:
        out = os.path.join("downloads", f"rec_{chat_id}.ogg")
        args = ["-i", url, "-t", str(secs), "-vn", "-ac", "1",
                "-c:a", "libopus", "-b:a", "64k", out]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-nostdin", *pre, *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    track0 = None
    if not is_video and is_http:
        track0 = (await asyncio.to_thread(_icy_metadata, url)).get("title")
    status = await send_status(
        f"🔴 **Recording {'video' if is_video else 'audio'}** `{name[:50]}`…\n"
        f"Tap **⏹ Stop & send** when done (auto-stops at {_dur(secs)}).",
        _rec_kb,
    )
    RECORDING[chat_id] = {
        "proc": proc, "out": out, "name": name, "url": url, "video": is_video,
        "tracks": [track0] if track0 else [], "stop": asyncio.Event(),
        "status": status, "start": time.time(),
    }
    asyncio.ensure_future(_record_watch(c, chat_id))


@Client.on_message(command(["record", f"record@{BOT_USERNAME}", "rec"]) & other_filters)
@authorized_users_only
async def record_cmd(c: Client, m: Message):
    secs = RECORD_MAX
    if len(m.command) > 1:
        try:
            secs = max(1, min(RECORD_MAX, int(m.command[1])))
        except ValueError:
            pass

    async def send(text, markup=None):
        return await m.reply(text, reply_markup=markup)

    await _begin_record(c, m.chat.id, secs, send)


@Client.on_callback_query(filters.regex(r"^rectoggle$"))
async def rectoggle_cb(c: Client, query: CallbackQuery):
    chat_id = query.message.chat.id
    member = await c.get_chat_member(chat_id, query.from_user.id)
    if not can_manage_vc(member):
        return await query.answer("💡 admins (manage video chats) only", show_alert=True)
    if chat_id in RECORDING:   # toggle: already recording -> stop & send
        RECORDING[chat_id]["stop"].set()
        return await query.answer("⏹ stopping & sending…")
    await query.answer("⏺ recording…")

    async def send(text, markup=None):
        return await c.send_message(chat_id, text, reply_markup=markup)

    await _begin_record(c, chat_id, RECORD_MAX, send)


async def _remux_faststart(src):
    """Copy a fragmented-mp4 recording into a normal faststart mp4 (correct moov /
    duration so Telegram plays it). Returns the new path, or None on failure."""
    dst = src[:-4] + "_final.mp4" if src.endswith(".mp4") else src + "_final.mp4"
    try:
        p = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-nostdin", "-i", src, "-c", "copy", "-movflags", "+faststart", dst,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(p.wait(), 120)
        if p.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            return dst
    except Exception:
        pass
    return None


async def _record_watch(c: Client, chat_id):
    rec = RECORDING.get(chat_id)
    if not rec:
        return
    proc, url = rec["proc"], rec["url"]
    while True:
        if not rec["video"] and str(url).startswith("http"):
            t = (await asyncio.to_thread(_icy_metadata, url)).get("title")
            if t and t not in rec["tracks"]:
                rec["tracks"].append(t)
        try:
            await asyncio.wait_for(rec["stop"].wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
        if rec["stop"].is_set() or proc.returncode is not None:
            break
    if RECORDING.pop(chat_id, None) is None:   # someone else finalized
        return
    if proc.returncode is None:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), 10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    try:
        await rec["status"].delete()
    except Exception:
        pass
    out = rec["out"]
    if not (os.path.exists(out) and os.path.getsize(out) > 0):
        return await c.send_message(chat_id, "🚫 recording failed.")
    secs = time.time() - rec["start"]
    if rec["video"]:
        cap = _rec_caption(rec["name"], [], secs, icon="🎬")
        # The recording is a fragmented mp4 (survives the SIGTERM stop), but its
        # moov reports the wrong duration so Telegram shows 0:00 / no content.
        # Remux to a normal faststart mp4 (copy, fast) so it plays as a video.
        send_path = await _remux_faststart(out) or out
        try:
            await c.send_video(chat_id, send_path, caption=cap, supports_streaming=True)
        except Exception as e:
            await c.send_message(chat_id, f"🚫 couldn't send the recording: `{e}`")
        if send_path != out:
            try:
                os.remove(send_path)
            except OSError:
                pass
    else:
        cap = _rec_caption(rec["name"], rec["tracks"], secs)
        try:
            await c.send_voice(chat_id, out, caption=cap)
        except Exception:
            try:
                await c.send_audio(chat_id, out, caption=cap, title=rec["name"][:60])
            except Exception as e:
                await c.send_message(chat_id, f"🚫 couldn't send the recording: `{e}`")
    try:
        os.remove(out)
    except OSError:
        pass


@Client.on_callback_query(filters.regex(r"^recstop$"))
async def recstop_cb(c: Client, query: CallbackQuery):
    chat_id = query.message.chat.id
    rec = RECORDING.get(chat_id)
    if not rec:
        return await query.answer("not recording", show_alert=True)
    member = await c.get_chat_member(chat_id, query.from_user.id)
    if not can_manage_vc(member):
        return await query.answer("💡 admins (manage video chats) only", show_alert=True)
    rec["stop"].set()
    await query.answer("⏹ stopping & sending…")


@Client.on_message(command(["stoprec", f"stoprec@{BOT_USERNAME}"]) & other_filters)
@authorized_users_only
async def stoprec_cmd(c: Client, m: Message):
    rec = RECORDING.get(m.chat.id)
    if not rec:
        return await m.reply("❌ not recording.")
    rec["stop"].set()
    await m.reply("⏹ stopping & sending…")


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
    card_img = await asyncio.to_thread(_render_card, name)
    try:
        await drop_stale_queue(chat_id)
        if card_img:
            # stream the still placeholder as the VC video + radio as audio
            await call_py.play(chat_id, MediaStream(
                card_img, audio_path=stream,
                video_parameters=VideoQuality.SD_480p, audio_parameters=AudioQuality.HIGH,
            ))
        else:
            await call_py.play(chat_id, MediaStream(stream, video_flags=MediaStream.Flags.IGNORE))
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
    card = await c.send_photo(chat_id, card_img or RADIO_IMG, caption=_caption(name, track), reply_markup=control_panel)
    RADIO[chat_id] = {"msg": card, "stream": stream, "name": name, "track": track,
                      "last": _caption(name, track), "card": card_img}


async def replay_radio_at_gain(chat_id, vol):
    """Re-tune the current radio station at the given gain (0-… , 1.0 = 100%),
    preserving the still-image video card. Returns False if not on radio."""
    st = RADIO.get(chat_id)
    if not st:
        return False
    af = f"--audio ---mid -af volume={max(0.0, vol):.3f}"
    card = st.get("card")
    if card:
        await call_py.play(chat_id, MediaStream(
            card, audio_path=st["stream"],
            video_parameters=VideoQuality.SD_480p, audio_parameters=AudioQuality.HIGH,
            ffmpeg_parameters=af,
        ))
    else:
        await call_py.play(chat_id, MediaStream(
            st["stream"], video_flags=MediaStream.Flags.IGNORE, ffmpeg_parameters=af,
        ))
    return True


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
            st["track"] = track
            cap = _caption(st["name"], track)
            if cap != st.get("last"):
                st["last"] = cap
                try:
                    await st["msg"].edit_caption(cap, reply_markup=control_panel)
                except Exception:
                    pass
