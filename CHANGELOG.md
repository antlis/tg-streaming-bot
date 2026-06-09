# Changelog

All notable changes to **tg-streaming-bot** are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and the project aims to
follow [Semantic Versioning](https://semver.org/).

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

[1.4.0]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.4.0
[1.3.0]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.3.0
[1.2.0]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.2.0
[1.1.0]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.1.0
[1.0.0]: https://github.com/antlis/tg-streaming-bot/releases/tag/v1.0.0
