import os
import json
import asyncio
import hashlib
from time import time

from config import TRANSCODE_HWACCEL

# Telegram streaming core (ntgcalls) reliably handles H.264 video + AAC audio in
# an mp4 container. Other combos (notably HEVC/H.265, or Opus/AC3 in MKV) often
# fail to play. prepare_for_stream() converts those to a cached mp4 first.
GOOD_VCODEC = ("h264",)
GOOD_ACODEC = ("aac",)
CACHE_DIR = "downloads"


async def _ffprobe(path):
    """Return (vcodec, acodec, duration_seconds) or None if probing fails."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        data = json.loads(out.decode(errors="ignore") or "{}")
    except Exception:
        return None
    vcodec = acodec = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and vcodec is None:
            vcodec = s.get("codec_name")
        elif s.get("codec_type") == "audio" and acodec is None:
            acodec = s.get("codec_name")
    try:
        duration = float(data.get("format", {}).get("duration", 0) or 0)
    except (ValueError, TypeError):
        duration = 0.0
    return vcodec, acodec, duration


async def prepare_for_stream(path, status_msg=None):
    """Return a path that's safe to stream. If the source is already mp4/H.264/AAC
    it's returned unchanged; otherwise it's remuxed (fast, when video is H.264) or
    transcoded (H.264/AAC) into a cached mp4, editing status_msg with progress.
    On any failure, falls back to the original path so playback can still try."""
    info = await _ffprobe(path)
    if info is None:
        return path
    vcodec, acodec, duration = info
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp4" and vcodec in GOOD_VCODEC and acodec in GOOD_ACODEC:
        return path

    try:
        st = os.stat(path)
        key = hashlib.md5(f"{path}:{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()[:16]
    except OSError:
        return path
    out = os.path.join(CACHE_DIR, f"tc_{key}.mp4")
    if os.path.exists(out) and os.path.getsize(out) > 1024:
        try:
            os.utime(out, None)  # keep fresh for the LRU pruner
        except OSError:
            pass
        return out

    remux = vcodec in GOOD_VCODEC
    aargs = ["-c:a", "copy"] if acodec in GOOD_ACODEC else ["-c:a", "aac", "-b:a", "128k"]
    tmp = out + ".part"
    label = "📦 Repackaging" if remux else "🔄 Transcoding"

    # Build the list of ffmpeg attempts: remux (copy) needs no encoder; for a
    # real re-encode, try GPU (VAAPI) first when enabled, then fall back to CPU.
    if remux:
        attempts = [([], ["-c:v", "copy"])]
    else:
        attempts = []
        if TRANSCODE_HWACCEL == "vaapi":
            attempts.append((
                ["-vaapi_device", "/dev/dri/renderD128"],
                ["-vf", "format=nv12,hwupload,scale_vaapi=w=-2:h=720", "-c:v", "h264_vaapi", "-qp", "24"],
            ))
        attempts.append((
            [],
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-vf", "scale=-2:'min(720,ih)'", "-pix_fmt", "yuv420p"],
        ))

    for input_pre, vargs in attempts:
        if await _run_ffmpeg(input_pre, path, vargs, aargs, tmp, duration, status_msg, label):
            try:
                os.replace(tmp, out)
                return out
            except OSError:
                return tmp
        try:
            os.remove(tmp)
        except OSError:
            pass
    return path  # all conversions failed — let the core try the original


async def _run_ffmpeg(input_pre, path, vargs, aargs, tmp, duration, status_msg, label):
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-nostdin", *input_pre, "-i", path, *vargs, *aargs,
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", tmp,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    async def _drain():
        while True:
            if not await proc.stderr.readline():
                break

    drain = asyncio.ensure_future(_drain())
    last = 0.0
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode(errors="ignore").strip()
        if line.startswith("out_time_ms=") and status_msg is not None and duration:
            now = time()
            if now - last >= 3:
                last = now
                try:
                    secs = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    secs = 0
                pct = max(0, min(100, int(secs / duration * 100)))
                bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                try:
                    await status_msg.edit(f"**{label} (→ mp4)…**\n`{bar}` {pct}%")
                except Exception:
                    pass
    await proc.wait()
    await drain
    return proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1024
