# Changelog

All notable changes to **tg-streaming-bot** are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and the project aims to
follow [Semantic Versioning](https://semver.org/).

## [1.8.8] — 2026-08-21
### Fixed
- **Proactive bot messages (auto-resume, stream-end, autoplay-next, idle-leave) could leak into "General" in a topic-locked group instead of the locked topic.** These all send via `get_active_thread(chat_id)`, which was tracked purely per-play from the triggering command's `message_thread_id` — a separate, non-persisted mechanism from `/topic lock`. If that capture ever came back empty (e.g. a `/vplay` processed during a connection reconnect, where pyrogram's catch-up update can be missing topic metadata), every background message for that session fell back to no thread at all. `get_active_thread()` now prefers `TOPIC_LOCK` when the chat has one set — by definition of the lock, any command that could have started the session had to come from that topic anyway — and only falls back to the per-play capture for unlocked chats.

## [1.8.7] — 2026-08-21
### Fixed
- **The actual cause of 1.8.6's auto-unpause**, which turned out not to be fully fixed by that release. The `.playback` attribute fix was correct but not sufficient: pytgcalls reports a genuinely paused stream's `playback` as `Status.IDLE`, not `Status.PAUSED` (confirmed via live logging), so it's indistinguishable from a dropped stream — no attribute-based check can tell them apart. Pause state is now tracked explicitly (`driver.queues.PAUSED`, set by `/pause`/`/resume` and their panel buttons) instead of inferred from ntgcalls' own state. `/info`'s Paused/Playing status now reads the same explicit flag.

## [1.8.6] — 2026-08-21
### Fixed
- **`/pause` auto-resumed itself (and appeared to rewind) after ~15 seconds.** The stall watchdog in `track_position()` is supposed to skip a deliberately paused stream by checking `call.status == Call.Status.PAUSED` — but pytgcalls' `Call` object has no `.status` attribute (it's `.playback`), so that check silently always missed. A paused stream's frozen position was treated as a silent stall, and after 15s the watchdog auto-recovered it by replaying from the last saved position — unpausing it and jumping back slightly. `/info`'s "⏸ Paused" / "▶️ Playing" status had the identical typo and always showed "Playing".

## [1.8.5] — 2026-08-21
### Fixed
- **`/play <search terms>` and `/vplay <search terms>` could leave "🔍 Searching..." stuck forever** and reply with an unhelpful generic error instead. The thumbnail-card step (fetch the video's thumbnail, draw the title/chat name onto it) had no error handling in either handler, so any hiccup there — most commonly a failed/slow thumbnail fetch — propagated uncaught past the status message entirely. `thumb()` also silently continued past a failed fetch straight into `Image.open()`, which could open a stale thumbnail left over from a previous, unrelated search under the same user id rather than failing clearly. Both are now handled: `thumb()` raises immediately on a bad fetch (with a 15s timeout so it can't hang indefinitely), and both handlers catch it and edit the status message instead of leaving it stuck.

## [1.8.4] — 2026-08-16
### Fixed
- **The actual cause of 1.8.3's `TypeError: NoneType has no len()`**, which turned out not to be fixed by that release for chats with topic lock active. Root cause: `other_filters` (part of every command handler's filter chain) checked for `/topic` using pyrogram's `filters.command()`, which mutates `message.command` as a side effect of every check it runs — including non-matches. Since `other_filters` is ANDed in *after* the handler's own `command([...])` filter already matched and set `message.command` correctly, that check clobbered it back to `None` before the handler ever ran, 100% reproducibly, in any topic-locked chat. Replaced with a plain non-mutating text match. The 1.8.3 snapshot-before-await changes stay in place as defense in depth but weren't the real fix.

## [1.8.3] — 2026-08-16
### Fixed
- **`/play`, `/vplay`, `/vstream`, `/iptv` crashed on a YouTube link with `TypeError: object of type 'NoneType' has no len()`.** These handlers read `m.command` only after several `await`s (permission checks, assistant-join); pyrogram's message cache can reset a cached `Message`'s `.command` back to `None` when another handler's filter check runs against the same object concurrently, so by the time the code got around to reading it, it could already be gone. Now snapshotted before the first `await`. Also closed the same gap once at the source for every handler wrapped by `authorized_users_only` (`/skip`, `/volume`, `/record`, `/seek`), which had a smaller version of the same race via its own admin-list lookup.

## [1.8.2] — 2026-08-16
### Fixed
- **`/library` audio/subtitle track selection silently dropped when the bot was already busy.** Picking a track then hitting ▶️ Play while something else was playing queued the raw, untouched file (default tracks, no subtitles) instead of your selection, with no indication it had done so. The selection is now transcoded in before queuing, so it's honored whenever it does play.

## [1.8.1] — 2026-08-16
### Fixed
- **End-of-stream messages ("streaming end", auto-DJ, auto-resume-after-drop, idle-leave) always posted to "General"**, ignoring topic lock — they fire from a pytgcalls lifecycle event, not a Telegram message, so there was never a thread id to read. A new per-chat "active thread" tracker records which topic the current playback session started from, and all background-task messages now post there instead.

## [1.8.0] — 2026-08-16
### Added
- **Global error wrapper on every command and button.** Previously an unhandled exception in a handler produced total silence — nothing sent to the chat, only a line in `docker logs` (that's exactly how the `/topic lock` crash went unnoticed last release). Every `@Client.on_message` and `@Client.on_callback_query` handler is now wrapped: message commands reply with the error, button callbacks show it as a toast alert, and both log it server-side first.

## [1.7.1] — 2026-08-15
### Fixed
- **`/topic lock` did nothing** — threw an unhandled error on every attempt in a real forum group (`chat.type` is `ChatType.FORUM` there, not `GROUP`/`SUPERGROUP`, and the message attribute this kurigram version exposes is `topic_message`, not `is_topic_message`). Both are now recognized correctly.
- **Radio/`/record` posting to "General"** instead of the topic the command came from — the final now-playing card and recording-completion messages were sent without the topic's thread id. Same fix applied to `/play`, `/vplay`, `/vstream`, and `/screenshot`'s status/result messages, which had the identical gap.
- **Radio/IPTV queuing behind other content instead of playing immediately** — they're live broadcasts, not real queue items. Starting `/play`, `/vplay`, `/vstream`, `/library`, `/lplay`, or `/search` while radio/IPTV was active now interrupts it right away instead of silently queuing. `/iptv` itself is also now always-interrupt like `/radio` already was, instead of queuing behind existing content.

## [1.7.0] — 2026-08-15
### Added
- **`/topic lock|unlock|status`** — restrict the bot to a single forum topic in a group; every other topic there (including "General") is then silently ignored. `/topic` itself always stays reachable so a chat can't get stuck locked. Lock/unlock require the "manage video chats" admin permission.

### Fixed
- Radio/IPTV stream hosts (`radiorecord.hostingradio.ru`, `streamguys1.com`, `radiofrance.fr`) failing to resolve with ffmpeg's "Failed to resolve hostname" — Docker's embedded DNS resolver was mishandling CNAME-chained answers for those hosts; the container now resolves via `1.1.1.1`/`8.8.8.8` directly.
- `/radio` retries once on a transient `play()` failure (DNS blips, Telegram's `INTERDC_X_CALL_ERROR` on joining the voice chat).
- IPTV: multi-variant HLS master playlists (e.g. Rutube) that confused ffmpeg 5.1 now resolve to a single variant before playing; added a browser User-Agent/Referer for CDNs that reject bare ffmpeg requests.

## [1.6.0] — 2026-06-24
### Added
- **`/record START END`** — clip a specific time range from what's playing. Accepts `HH:MM:SS`, `MM:SS`, `Nh`/`Nm`/`Ns`, or plain seconds for both arguments (e.g. `/record 01:30:00 02:00:00` records 30 minutes starting at 1 h 30 m). Works for local files and downloaded YouTube tracks; for live HTTP streams (radio, IPTV) the start offset is ignored and only the duration (`END − START`) is used, with a note in the status message.
- **Duration shorthand** for `/record` — e.g. `/record 30m` or `/record 1h` alongside the existing plain-seconds form.
- **Ahead-of-playback warning** — when the requested start time is ahead of the current playback position (meaning yt-dlp may not have downloaded that far yet), the recording starts but the status message warns that the clip may be shorter than expected.

## [1.5.0] — 2026-06-21
### Added
- **IPTV (`/iptv <name>`)** — search 50 000+ live TV channels from the [iptv-org](https://github.com/iptv-org/iptv) public catalogue and stream them live; channel logo shown when playing. `/iptv` with no args shows links to browse channels by country/category. Works in forum groups (posts to the correct topic).
- **Non-YouTube URL support** in `/play` and `/vplay` — Rutube, Vimeo, and any other yt-dlp-supported site now work by extracting the stream URL and feeding it live to ffmpeg (no multi-GB download wait). YouTube continues to download first as before.

### Fixed
- `ytsearch()` no longer tries to do a YouTube keyword search when given a direct URL from a non-YouTube site (was returning "no results found").
- `callback.py` was missing the `can_manage_vc` import (undefined name, F821).
- Unused `global admins` declaration removed from `admins.py` (F824).

## [1.4.1] — 2026-06-09
### Changed
- **Gapless Auto-DJ** — the next related track is now prefetched in the background while the current one is still playing, so it starts seamlessly instead of after a short fetch pause at the end of each track.

## [1.4.0] — 2026-06-09
### Added
- **Auto-DJ (`/autoplay`)** — when the queue runs out, keep playing related YouTube tracks (the song's Mix), endless-radio style. Off by default; stops on its own when the voice chat empties (idle auto-leave). Non-YouTube sources just end normally.
- **SponsorBlock** — set `SPONSORBLOCK_REMOVE` (e.g. `sponsor,selfpromo,music_offtopic`) to cut sponsor reads and non-music intros/outros from YouTube downloads (`/play`, `/vplay`, `/search`, `/song`, `/video`). Off by default.

## [1.3.0] — 2026-06-09
### Added
- **`/screenshot`** (and a 📸 button on the panel) — grab the current video frame and send it to the chat.
- **Seek-to-% buttons** (0 / 25 / 50 / 75 %) on the now-playing panel.
- **Record toggle** — the ⏺ panel button starts/stops a recording, and the recorder is reachable from the `/info` panel.
- Internal version now tracks the release tags (shown in `/alive`).

### Fixed
- **Recording A/V sync** — re-encodes now drop B-frames and resample audio so the picture and sound line up (audio was ~80 ms ahead).
- **Empty / 0:00 video recordings** — the seek is clamped inside the source, E-AC3 audio is re-encoded to AAC, HEVC/non-H.264 video is re-encoded to H.264, and the result is remuxed to faststart so Telegram plays it.
- Volume / mute changes no longer restart the video from the beginning (absolute position is tracked across re-feeds).
- Friendlier `/radio` error when no voice chat is open and the assistant can't start one.
- `print()` / bare `except` replaced with proper logging.

## [1.2.0] — 2026-06-09
### Added
- **GPU (VAAPI) encoding for recordings.** When `TRANSCODE_HWACCEL=vaapi`, a
  recording that needs re-encoding (HEVC/other → H.264) is encoded on the GPU
  (`h264_vaapi`) instead of the CPU, so recording no longer stutters the live
  stream. Falls back to CPU `libx264` when the GPU isn't configured.

## [1.1.0] — 2026-06-09
### Added
- **Master volume** via an ffmpeg gain re-feed (`/volume 0-200`, 🔉/🔊) — affects
  the whole room, since Telegram ignores a streaming bot's own participant volume.
- **Record button is a toggle** (⏺ Rec / Stop) on the control / `/info` panel.
- **Video recording** that actually works end to end.

### Changed
- **Mute is now volume 0** (🔇 / `/vmute`) instead of a media-layer mute, so muting
  a video no longer makes Telegram downgrade it to a blurry layer.
- Friendlier `/radio` error when no voice chat is open and the assistant can't
  start one (explains it needs the assistant to be an admin, or a VC opened first).

### Fixed
- Volume/mute changes no longer restart the video from the beginning — the
  absolute playback position is tracked across re-feeds.
- Video recordings no longer come out 0:00 / empty:
  - seek is clamped inside the source duration (a past-EOF seek produced nothing);
  - audio is re-encoded to AAC (library MKVs are often E-AC3, which can't be
    copied into mp4);
  - HEVC/non-H.264 video is re-encoded to H.264 720p so Telegram renders it;
  - the result is remuxed to a faststart mp4 so the duration/preview is correct.

## [1.0.0] — 2026-06-09
First public release — a self-hosted Telegram bot that streams music & video into
group voice chats.

### Added
- Play music & video from YouTube (search or URL) or any audio/video posted in chat.
- `/search` — pick from YouTube results as audio or video.
- Live streams (`/vstream`: m3u8 / YouTube-live), with selectable quality.
- Internet radio (`/radio`) with the live now-playing track on the video card.
- Recording (`/record`) of radio/audio to a voice message, with a live tracklist.
- Local media library (`/library`, `/lplay`) with audio-track and subtitle selection.
- Full playback controls — pause/resume/skip/seek/volume/loop/shuffle/queue, plus
  an inline control panel and `/info`.
- Hardware (VAAPI) or CPU transcoding for HEVC/MKV sources.
- Self-healing — auto-reconnect on drops, resume after a restart, idle auto-leave.
- One-command Docker deploy; everything configured via environment variables.

[1.4.1]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.4.1
[1.4.0]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.4.0
[1.3.0]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.3.0
[1.2.0]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.2.0
[1.1.0]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.1.0
[1.0.0]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.0.0
