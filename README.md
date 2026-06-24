# tg-streaming-bot

Telegram bot that streams **music & video into group voice chats**, built with [Pyrogram](https://docs.pyrogram.org) and [py-tgcalls](https://github.com/pytgcalls/pytgcalls).

## ✨ Features
- **Music & video** into group voice chats — from YouTube (search **or** URL), an audio/video file posted in Telegram, or a live link (m3u8 / YouTube-live)
- **`/search`** — pick from YouTube results (🎵 audio or 🎬 video) instead of auto-playing the first hit
- **Internet radio** — dozens of built-in stations, with the live now-playing track shown on the video card
- **Live TV (IPTV)** — search 50 000+ channels from the [iptv-org](https://github.com/iptv-org/iptv) public catalogue by name and stream them live; channel logo shown when playing
- **Local media library** — browse & play your own folders, with audio-track and subtitle selection
- **Recording** — capture the radio/audio to a voice message or the video to an H.264 mp4 and upload it (toggle on/off, live tracklist)
- **Screenshot** — grab the current video frame to the chat
- **Full controls** — pause / resume / skip / seek, **master volume**, mute, loop / shuffle / clear, seek-to-% buttons and a queue — one inline now-playing panel plus `/info`
- **Hardware transcoding** (VAAPI) or CPU for HEVC/MKV sources
- **Auto-DJ** (`/autoplay`) — keep playing related tracks when the queue runs out · **SponsorBlock** to skip sponsor/intro segments
- **Self-healing** — auto-reconnect on drops, resume after a restart, idle auto-leave
- Per-user **rate limiting** + a **max-queue** cap; everything env-configured for self-hosting

## How it works
Two Telegram identities are required:
- **The bot** (command interface) — must be a group **admin** with *Manage video chats*, *Delete messages*, and *Add users*.
- **An assistant user account** — logs in via a Pyrogram session string (`SESSION_NAME`) and is what actually joins the voice chat and streams. It must be a **member of every group** it plays in (play commands auto-join it), and should be a **dedicated account**: Telegram allows only one voice-chat connection per account, so don't use an account a human listens with.

## Setup
1. Clone and enter the repo:
   ```sh
   git clone https://github.com/antlis/tg-streaming-bot.git
   cd tg-streaming-bot
   ```
2. Copy the template and fill it in:
   ```sh
   cp example.env .env
   ```
   See the table below — only the **Required** block is mandatory.
3. Generate the assistant session string — log in as the **assistant** account
   and paste the printed string into `SESSION_NAME` in `.env`:
   ```sh
   docker run -it --rm --env-file .env \
     -v "$(pwd)/gen_session.py:/gen.py" python:3.11-slim \
     sh -c "pip install -q kurigram tgcrypto && python3 /gen.py"
   ```
4. Build & run (compose is the easy way — includes the cache volume and a healthcheck):
   ```sh
   docker compose up -d --build
   ```
   or plain docker:
   ```sh
   docker build -t musicbot .
   docker run -d --name musicbot --env-file .env -e PYTHONUNBUFFERED=1 \
     -v musicbot_downloads:/app/downloads \
     --restart unless-stopped musicbot
   ```
5. In the group: add the bot (as admin), start a voice chat, then `/play <song>`.

## Configuration (`.env`)
| Key | Required | Purpose |
|---|---|---|
| `API_ID` / `API_HASH` | ✅ | from my.telegram.org |
| `BOT_TOKEN` | ✅ | from @BotFather |
| `BOT_USERNAME` | ✅ | bot username without @ |
| `BOT_NAME` | ✅ | display name |
| `SESSION_NAME` | ✅ | assistant session string (step 2) |
| `SUDO_USERS` | ✅ | space-separated admin user ids |
| `DURATION_LIMIT` | — | max track length (minutes) |
| `DOWNLOADS_CACHE_LIMIT_MB` | — | downloads-cache cap in MB, pruned after each stream (default 4096; 0 = delete after playing) |
| `LIBRARY_HOST_DIR` / `LIBRARY_ROOT` / `LIBRARY_CATEGORIES` | — | local media library: host folder to mount read-only, the in-container path (`/library`), and an optional comma-separated category allowlist; leave unset to disable |
| `RADIO_STATIONS` | — | override the built-in `/radio` presets (`Name=URL,Name=URL,…`) |
| `RADIO_IMG` | — | card image shown while streaming radio (defaults to `IMG_1`) |
| `TRANSCODE_HWACCEL` | — | `vaapi` for GPU transcode/record (needs `/dev/dri` mounted + VA drivers); empty = CPU |
| `IDLE_LEAVE_MINUTES` | — | leave the voice chat after N minutes with no listeners (default 10; 0 = never) |
| `MAX_QUEUE_SIZE` | — | max upcoming tracks per chat (default 50; 0 = unlimited) |
| `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW` | — | per-user command rate limit (default 5 commands / 10s; sudo users exempt) |
| `SPONSORBLOCK_REMOVE` | — | comma-separated SponsorBlock categories to cut from YouTube downloads (e.g. `sponsor,selfpromo,music_offtopic`); empty = off |
| `COMMAND_PREFIXES` | — | accepted command prefixes (default `/ ! .`) |
| `ASSISTANT_NAME` | — | assistant @username (without @), used in messages |
| `OWNER_NAME` / `ALIVE_NAME` | — | owner link & name for `/start` and `/alive`; empty = hidden |
| `GROUP_SUPPORT` / `UPDATES_CHANNEL` | — | username or `+invitehash` for the Group/Channel buttons; empty = hidden |
| `ALIVE_IMG`, `IMG_1`–`IMG_4` | — | card images (URL or file path); bundled placeholder by default |
| `UPSTREAM_REPO` | — | your repo's git URL (shown as the "Source Code" button on `/start`) |
| `PROXY_*` | — | optional proxy for Telegram signaling |

## 🛠 Commands
| Command | Description |
| ------ | ------ |
| `/play <query\|URL>` | play music (or reply to an audio / voice / audio-file message) |
| `/vplay <query\|URL> [720\|480\|360] [mute]` | play video (or reply to a video); `mute` starts it muted |
| `/vstream <link>` | stream a live link / m3u8 / YouTube live |
| `/search <query>` | pick from YouTube results — 🎵 audio or 🎬 video |
| `/radio` | internet radio — pick a station |
| `/iptv <name>` | live TV — search the iptv-org catalogue (50 000+ channels) and stream; `/iptv` alone shows links to browse by country |
| `/record [duration]` · `/record START END` · `/stoprec` | record audio/video and send it (⏺ panel toggle); `duration` accepts `30m`, `1h`, `HH:MM:SS`; `START END` clips a specific range (e.g. `01:30:00 02:00:00`) — for local files/YouTube only; live streams use the duration and ignore the start offset |
| `/library` · `/lplay <name>` | browse / play the local media library |
| `/screenshot` | send a frame of the current video |
| `/pause` `/resume` `/skip` `/stop` | playback control (admins) |
| `/seek 12:30` · `/continue` | jump to a time / resume after a drop |
| `/volume 0-200` · `/vmute` `/vunmute` | master volume / mute (applied to the stream, heard by everyone) |
| `/loop` `/shuffle` `/clear` · `/autoplay` | queue modes · auto-DJ related tracks (admins) |
| `/info` · `/playlist` | now-playing panel with controls / the queue |
| `/song <query>` · `/video <query>` | download instead of stream |
| `/userbotjoin` `/userbotleave` · `/reload` | assistant join / leave · refresh admin cache |
| `/ping` `/alive` `/uptime` | status checks |

## Notes
- YouTube playback **downloads first, then streams** (yt-dlp with the `android_vr` client; H.264+AAC mp4) — direct stream URLs are blocked by YouTube these days. Expect a short delay before playback starts.
- **Non-YouTube URLs** (Rutube, Vimeo, IPTV m3u8, etc.) are extracted and streamed live — no download wait, but HLS tokens expire after ~30 minutes so very long sessions may need a seek/restart.
- Large Telegram files also download fully before streaming — a progress bar is shown; big files just take a while.

## Running where Telegram is filtered (Reality VPN sidecar)

Where Telegram — especially the voice servers — is blocked or throttled, a SOCKS
proxy isn't enough: it carries only the **signaling**, not the **voice UDP** that
py-tgcalls streams on. So `docker-compose.yml` ships an optional **`reality-vpn`
sidecar**: a [sing-box](https://sing-box.sagernet.org) container that raises a TUN
and tunnels **all** of the bot's traffic (signaling **and** voice) through a
[VLESS + Reality](https://github.com/XTLS/REALITY) server. The `bot` joins the
sidecar's network namespace (`network_mode: "service:reality-vpn"`), so everything
it does exits via your VPN — no per-client proxy config needed, and it doesn't
affect anything else on the host.

1. Copy the template and fill in your Reality server:
   ```sh
   cp reality-vpn.json.example reality-vpn.json
   ```
   Set `server` / `server_port` / `uuid` / `public_key` / `short_id` / `server_name`
   (the borrowed SNI) — straight from your server's Xray/sing-box Reality config or
   its `vless://…` share link.
2. Start everything — the bot waits until the VPN is healthy, then routes through it:
   ```sh
   docker compose up -d
   ```
   Verify the exit is your server, not your real IP:
   ```sh
   docker compose exec bot wget -qO- https://api.ipify.org
   ```

**Notes**
- The bot shares the sidecar's netns, so to switch servers edit `reality-vpn.json`
  then `docker compose up -d --force-recreate` (recreates both).
- Don't need a full tunnel? Skip the sidecar entirely and set the `PROXY_*` env
  vars instead — but note voice will then go direct.
- `reality-vpn.json` is gitignored (live credentials); only `.example` is committed.

## Roadmap / TODO

Done so far: modern stack (Python 3.11, Pyrogram 2.x / py-tgcalls 2.x), YouTube
search picker, internet radio, local media library with audio/subtitle
selection, audio **and** video recording, screenshots, master volume + mute,
seek / seek-to-%, loop / shuffle / clear, auto-reconnect & resume, idle
auto-leave, hardware (VAAPI) transcoding, download-cache GC, rate limiting,
startup config validation, logging, and CI.

Still open:
- [ ] **Stream-while-downloading** — begin playback once enough is buffered instead of waiting for the full download (large files sit silent for a while today).
- [ ] **Queue management** — show durations, remove an arbitrary item, reorder.
- [ ] **More sources** — Spotify / SoundCloud / direct-URL routing (yt-dlp already supports most; mainly needs URL routing + metadata).
- [ ] **Deduplicate `music.py` / `video.py`** — `ytsearch()`, `ytdl()`, the permission gate and assistant-join logic are near-identical copies; extract shared helpers.
- [ ] **More tests** — only a smoke/import check runs in CI today; add unit tests for the queue, seek/position, and `ytsearch` URL detection.
- [ ] **i18n** — user-facing strings are hardcoded English; extract to a strings module.
