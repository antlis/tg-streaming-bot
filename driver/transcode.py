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


async def _run_cmd(args, tmp, duration, status_msg, label):
    """Run an arbitrary ffmpeg arg list (output options + input), with progress."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-nostdin", *args,
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


_IMAGE_SUBS = ("hdmv_pgs_subtitle", "dvd_subtitle", "dvdsub", "dvb_subtitle", "dvbsub", "xsub")


async def probe_tracks(path):
    """List selectable audio + subtitle tracks of a file.
    Returns (audios, subs):
      audios = [(abs_index, label)]
      subs   = [(abs_index, sub_ordinal, label, is_image)]"""
    audios, subs = [], []
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-print_format", "json", "-show_streams", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        streams = json.loads(out.decode(errors="ignore") or "{}").get("streams", [])
    except Exception:
        return audios, subs
    s_ord = 0
    for s in streams:
        tags = s.get("tags", {}) or {}
        if s.get("codec_type") == "audio":
            label = tags.get("language") or tags.get("title") or f"audio {len(audios) + 1}"
            audios.append((s["index"], label))
        elif s.get("codec_type") == "subtitle":
            label = tags.get("language") or tags.get("title") or f"sub {s_ord + 1}"
            subs.append((s["index"], s_ord, label, s.get("codec_name") in _IMAGE_SUBS))
            s_ord += 1
    return audios, subs


async def transcode_selection(src, audio_abs, sub_abs=None, sub_ord=0, sub_image=False, status_msg=None):
    """Transcode `src` to a cached H.264/AAC mp4 with a chosen audio track mapped
    and (optionally) a chosen subtitle burned in. Returns the path, or `src` on
    failure. Cached per (src, audio, sub)."""
    info = await _ffprobe(src)
    vcodec = info[0] if info else None
    duration = info[2] if info else 0
    try:
        st = os.stat(src)
        key = hashlib.md5(f"{src}:{st.st_mtime_ns}:{audio_abs}:{sub_abs}".encode()).hexdigest()[:16]
    except OSError:
        return src
    out = os.path.join(CACHE_DIR, f"sel_{key}.mp4")
    if os.path.exists(out) and os.path.getsize(out) > 1024:
        try:
            os.utime(out, None)
        except OSError:
            pass
        return out
    tmp = out + ".part"
    a_aac = ["-c:a", "aac", "-b:a", "160k"]
    common_tail = ["-dn", "-sn", *a_aac]

    # build the preferred command, then a no-subtitle fallback
    attempts = []
    if sub_abs is not None and sub_image:
        attempts.append(["-i", src, "-filter_complex", f"[0:v:0][0:{sub_abs}]overlay,scale=-2:'min(720,ih)'[v]",
                         "-map", "[v]", "-map", f"0:{audio_abs}", "-c:v", "libx264", "-preset", "veryfast",
                         "-crf", "23", "-pix_fmt", "yuv420p", *common_tail])
    elif sub_abs is not None:
        esc = src.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        attempts.append(["-i", src, "-map", "0:v:0", "-map", f"0:{audio_abs}",
                         "-vf", f"subtitles='{esc}':si={sub_ord},scale=-2:'min(720,ih)'",
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", *common_tail])
    # audio-only fallback (also the no-sub path): GPU if HEVC+enabled, else CPU, else copy
    if vcodec == "h264":
        audio_only = ["-i", src, "-map", "0:v:0", "-map", f"0:{audio_abs}", "-c:v", "copy", *common_tail]
    elif TRANSCODE_HWACCEL == "vaapi":
        audio_only = ["-vaapi_device", "/dev/dri/renderD128", "-i", src, "-map", "0:v:0", "-map", f"0:{audio_abs}",
                      "-vf", "format=nv12,hwupload,scale_vaapi=w=-2:h=720", "-c:v", "h264_vaapi", "-qp", "24", *common_tail]
    else:
        audio_only = ["-i", src, "-map", "0:v:0", "-map", f"0:{audio_abs}", "-vf", "scale=-2:'min(720,ih)'",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", *common_tail]
    attempts.append(audio_only)

    label = "🔄 Preparing (audio/subs)" if sub_abs is not None else "🔄 Preparing audio"
    for args in attempts:
        if await _run_cmd(args, tmp, duration, status_msg, label):
            try:
                os.replace(tmp, out)
                return out
            except OSError:
                return tmp
        try:
            os.remove(tmp)
        except OSError:
            pass
    return src
