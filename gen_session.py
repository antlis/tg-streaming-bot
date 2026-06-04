"""Generate the assistant session string (SESSION_NAME for .env).

Telegram rejects logins from old Pyrogram 1.x clients (406 UPDATE_APP_TO_LOGIN),
so this logs in with modern Pyrogram 2.x and repacks the credentials into the
1.x session-string format the bot uses. Run it with Pyrogram 2.x, e.g.:

    docker run -it --rm --env-file .env \
      -v $(pwd)/gen_session.py:/gen.py python:3.9 \
      sh -c "pip install -q pyrogram==2.0.106 tgcrypto && python3 /gen.py"

API_ID / API_HASH are taken from the environment (--env-file .env) or prompted.
Log in with the ASSISTANT account (a dedicated user account, not your own).
"""

import asyncio
import base64
import os
import struct

from pyrogram import Client

API_ID = int(os.getenv("API_ID") or input("API_ID (from my.telegram.org): "))
API_HASH = os.getenv("API_HASH") or input("API_HASH: ")
MAX_USER_ID_OLD = 2147483647


async def main():
    app = Client("gen2x", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await app.start()
    st = app.storage
    dc_id = await st.dc_id()
    test_mode = await st.test_mode()
    auth_key = await st.auth_key()
    user_id = await st.user_id()
    is_bot = await st.is_bot()
    await app.stop()

    fmt = ">B?256sI?" if user_id < MAX_USER_ID_OLD else ">B?256sQ?"
    packed = struct.pack(fmt, dc_id, test_mode, auth_key, user_id, is_bot)
    session_string = base64.urlsafe_b64encode(packed).decode().rstrip("=")

    print("\n==== SESSION STRING (Pyrogram 1.x format — paste into .env) ====\n")
    print(session_string)
    print()


asyncio.run(main())
