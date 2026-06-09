import asyncio
import logging

# Libraries stay at WARNING (real problems still surface); our own packages log
# at INFO so the concise call-lifecycle + startup lines show without lib spam.
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for _noisy in ("pytgcalls", "pyrogram", "ntgcalls"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
for _ours in ("program", "driver", "__main__"):
    logging.getLogger(_ours).setLevel(logging.INFO)
log = logging.getLogger(__name__)

from pytgcalls import idle
from pyrogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats
from pyrogram.errors import FloodWait
from driver.clients import call_py, bot
from driver.queues import load_resume
from program.resume import track_position
from program.radio import radio_updater

BOT_COMMANDS = [
    BotCommand("play", "play music from YouTube, or reply to an audio"),
    BotCommand("vplay", "play video from YouTube, or reply to a video"),
    BotCommand("vstream", "stream a live / m3u8 / YouTube link"),
    BotCommand("search", "search YouTube and pick a result to play"),
    BotCommand("library", "browse the local media library"),
    BotCommand("lplay", "play a local library file by name"),
    BotCommand("radio", "tune in to an internet radio station"),
    BotCommand("record", "record the current audio/video (tap stop or auto 1h)"),
    BotCommand("stoprec", "stop the current recording and send it"),
    BotCommand("pause", "pause playback (admin)"),
    BotCommand("resume", "resume playback (admin)"),
    BotCommand("continue", "resume the last track from where it stopped"),
    BotCommand("skip", "skip to the next track (admin)"),
    BotCommand("loop", "toggle repeat of the current track (admin)"),
    BotCommand("shuffle", "shuffle the upcoming queue (admin)"),
    BotCommand("clear", "clear upcoming tracks, keep current (admin)"),
    BotCommand("stop", "stop and leave the voice chat (admin)"),
    BotCommand("seek", "jump to a time in the current track, e.g. /seek 12:30"),
    BotCommand("vmute", "mute the assistant in the voice chat"),
    BotCommand("vunmute", "unmute the assistant in the voice chat"),
    BotCommand("playlist", "show the current queue"),
    BotCommand("info", "now playing + controls"),
    BotCommand("help", "show the command list"),
    BotCommand("song", "download a song from YouTube"),
    BotCommand("video", "download a video from YouTube"),
    BotCommand("userbotjoin", "make the assistant join this group"),
    BotCommand("userbotleave", "make the assistant leave this group"),
    BotCommand("ping", "check if the bot is alive"),
    BotCommand("alive", "bot status"),
    BotCommand("uptime", "bot uptime"),
]


async def heartbeat():
    # Touched every 30s; the docker-compose healthcheck verifies freshness.
    while True:
        try:
            with open("/tmp/heartbeat", "w") as f:
                f.write("ok")
        except OSError:
            pass
        await asyncio.sleep(30)


async def start_bot():
    log.info("starting bot client")
    # If Telegram rate-limited the token login (e.g. after several restarts in
    # a row), wait the window out gracefully instead of crash-looping — which
    # would re-attempt the login and keep the flood going.
    while True:
        try:
            await bot.start()
            break
        except FloodWait as e:
            wait = int(e.value) + 5
            log.warning("FloodWait on bot login — sleeping %ss", wait)
            await asyncio.sleep(wait)
    try:
        # set for the default scope plus groups/private explicitly, so the "/"
        # menu populates reliably in group chats (not just DMs)
        await bot.set_bot_commands(BOT_COMMANDS)
        await bot.set_bot_commands(BOT_COMMANDS, scope=BotCommandScopeAllGroupChats())
        await bot.set_bot_commands(BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        log.info("bot commands registered")
    except Exception as e:
        log.warning("could not register bot commands: %s", e)
    load_resume()  # restore resume state so /continue survives restarts
    asyncio.ensure_future(heartbeat())
    asyncio.ensure_future(track_position())
    asyncio.ensure_future(radio_updater())
    log.info("starting pytgcalls client")
    await call_py.start()
    await idle()
    log.info("stopping bot & userbot")
    await bot.stop()

loop = asyncio.get_event_loop()
loop.run_until_complete(start_bot())
