import asyncio
import logging

# Keep WARNING/ERROR (real problems still surface); the concise [CALL] state
# prints in driver/utils.py cover call lifecycle without the INFO spam.
logging.basicConfig(level=logging.WARNING)
logging.getLogger("pytgcalls").setLevel(logging.WARNING)

from pytgcalls import idle
from pyrogram.types import BotCommand
from pyrogram.errors import FloodWait
from driver.clients import call_py, bot
from program.resume import track_position

BOT_COMMANDS = [
    BotCommand("play", "play music from YouTube, or reply to an audio"),
    BotCommand("vplay", "play video from YouTube, or reply to a video"),
    BotCommand("vstream", "stream a live / m3u8 / YouTube link"),
    BotCommand("pause", "pause playback (admin)"),
    BotCommand("resume", "resume playback (admin)"),
    BotCommand("continue", "resume the last track from where it stopped"),
    BotCommand("skip", "skip to the next track (admin)"),
    BotCommand("stop", "stop and leave the voice chat (admin)"),
    BotCommand("vmute", "mute the assistant in the voice chat"),
    BotCommand("vunmute", "unmute the assistant in the voice chat"),
    BotCommand("playlist", "show the current queue"),
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
    print("[INFO]: STARTING BOT CLIENT")
    # If Telegram rate-limited the token login (e.g. after several restarts in
    # a row), wait the window out gracefully instead of crash-looping — which
    # would re-attempt the login and keep the flood going.
    while True:
        try:
            await bot.start()
            break
        except FloodWait as e:
            wait = int(e.value) + 5
            print(f"[WARN]: FloodWait on bot login — sleeping {wait}s")
            await asyncio.sleep(wait)
    try:
        await bot.set_bot_commands(BOT_COMMANDS)
        print("[INFO]: BOT COMMANDS REGISTERED")
    except Exception as e:
        print(f"[WARN]: could not register bot commands: {e}")
    asyncio.ensure_future(heartbeat())
    asyncio.ensure_future(track_position())
    print("[INFO]: STARTING PYTGCALLS CLIENT")
    await call_py.start()
    await idle()
    print("[INFO]: STOPPING BOT & USERBOT")
    await bot.stop()

loop = asyncio.get_event_loop()
loop.run_until_complete(start_bot())
