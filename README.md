# tg-streaming-bot

Telegram bot that streams **music & video into group voice chats**, built with [Pyrogram](https://docs.pyrogram.org) and [py-tgcalls](https://github.com/pytgcalls/pytgcalls).

## ✨ Features
- Music & video streaming into group voice chats
- Play from YouTube (search **or** direct URL), or from audio/video files posted in Telegram
- Live stream support (links / m3u8)
- Queue & playlist, skip / pause / resume / stop
- Live download-progress bar for Telegram files
- Inline transport control panel on the now-playing card (⏸ ▶️ ⏭ 🔇 🔊 ⏹)
- Music & video downloader, inline search
- Volume control, assistant auto-join

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
3. Generate the assistant session string (logs in with Pyrogram 2.x and converts
   to the 1.x format the bot uses — Telegram blocks logins from old clients):
   ```sh
   docker run -it --rm --env-file .env \
     -v $(pwd)/gen_session.py:/gen.py python:3.9 \
     sh -c "pip install -q pyrogram==2.0.106 tgcrypto && python3 /gen.py"
   ```
   Log in with the **assistant** account, then paste the printed string into `SESSION_NAME` in `.env`.
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
| `ASSISTANT_NAME` | — | assistant @username (without @), used in messages |
| `OWNER_NAME` / `ALIVE_NAME` | — | owner link & name for `/start` and `/alive`; empty = hidden |
| `GROUP_SUPPORT` / `UPDATES_CHANNEL` | — | username or `+invitehash` for the Group/Channel buttons; empty = hidden |
| `ALIVE_IMG`, `IMG_1`–`IMG_4` | — | card images (URL or file path); bundled placeholder by default |
| `UPSTREAM_REPO` | — | your repo's git URL (shown as the "Source Code" button on `/start`) |
| `PROXY_*` | — | optional proxy for Telegram signaling |

## 🛠 Commands
| Command | Description |
| ------ | ------ |
| `/play <query or YouTube URL>` | play music (or reply to an audio / voice / audio-file message) |
| `/vplay <query or YouTube URL> [720\|480\|360] [mute]` | play video (or reply to a video); `mute` starts it muted |
| `/vstream <link>` | stream a live link / m3u8 / YouTube live |
| `/pause` `/resume` `/skip` `/stop` | playback control (admins) |
| `/vmute` `/vunmute` | mute / unmute the assistant in the voice chat |
| `/volume 1-200` | set volume (assistant must be admin) |
| `/playlist` | show the current queue |
| `/song <query>` / `/video <query>` | download instead of stream |
| `/userbotjoin` `/userbotleave` | assistant joins / leaves the group |
| `/ping` `/alive` `/uptime` | status checks |
| `/clean` `/rmd` | clean raw / downloaded files |

## Notes
- YouTube playback **downloads first, then streams** (yt-dlp with the `android_vr` client; H.264+AAC mp4) — direct stream URLs are blocked by YouTube these days. Expect a short delay before playback starts.
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

### High impact
- [ ] **Modernize the stack** — bump the base image to Python 3.11 and migrate to Pyrogram 2.x + py-tgcalls 2.x (ntgcalls). This removes three legacy workarounds at once: the `MsgId` time-sync monkeypatch, the session-string 1.x repacking in `gen_session.py`, and the yt-dlp `2025.10.14` cap (current yt-dlp handles YouTube without the `android_vr` pin). Big migration — the whole py-tgcalls streaming API changed.
- [ ] **Stream-while-downloading** — start playback once enough of the file is buffered instead of waiting for the full download (large files currently sit silent for minutes; pipe yt-dlp/ffmpeg output instead of `--print after_move:filepath`).
- [x] **Download progress for YouTube** — the progress bar currently only covers Telegram file downloads; parse yt-dlp's progress output (`--newline` / `--progress-template`) and edit the status message the same way.
- [x] **Auto-clean `downloads/`** — files accumulate forever; delete after playback ends (hook `on_stream_end`) or keep an LRU-capped cache. Add an optional max-file-size guard.

### Features
- [x] Register bot commands on startup (`set_bot_commands`) so Telegram's `/` autocomplete works without manual @BotFather setup
- [x] `/play` should accept audio sent as a *document* (generic .mp3 file) — currently only `audio`/`voice` types are recognized
- [x] `/vplay <query> mute` — start a video muted in one step (today: `/vplay` then `/vmute`)
- [ ] `/seek <seconds>` and a now-playing elapsed/duration display on the card
- [ ] Loop / repeat-one / shuffle modes for the queue
- [ ] Queue management: show durations, remove an arbitrary item, reorder
- [ ] Spotify / SoundCloud / direct-URL sources (yt-dlp already supports most of them — mainly needs URL routing + metadata)
- [ ] i18n — user-facing strings are hardcoded English, scattered across handlers; extract to a strings module

### Code quality / ops
- [ ] **Deduplicate `music.py` / `video.py`** — `ytsearch()`, `ytdl()`, the admin-permission gate, and the assistant-join logic are near-identical copies; extract to shared helpers
- [x] Drop unused dependencies: `motor`, `heroku3`, `dnspython`, `future` (no imports anywhere); consolidate `youtube-search` vs `youtube-search-python`
- [x] Auto-invalidate the admin cache on `ChatMemberUpdated` instead of requiring manual `/reload`
- [x] Add `docker-compose.yml` (one-command setup for adopters) and a container healthcheck
- [ ] Tests (there are none) — at least for the queue, the session-string repack, and `ytsearch` URL detection; wire into CI (the GitHub workflows are stale upstream leftovers)
- [ ] Auto-leave the voice chat after N minutes idle (assistant currently stays parked)
- [ ] Replace bare `except`/`print(e)` error handling with proper logging
