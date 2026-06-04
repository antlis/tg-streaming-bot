"""Generate the assistant session string (SESSION_NAME for .env).

Run it with the bot's own Pyrogram (2.x), easiest inside the built image:

    docker run -it --rm --env-file .env \
      -v "$(pwd)/gen_session.py:/gen.py" musicbot python3 /gen.py

API_ID / API_HASH come from the environment (--env-file .env) or are prompted.
Log in with the ASSISTANT account (a dedicated user account, not your own).
"""

import os

from pyrogram import Client

API_ID = int(os.getenv("API_ID") or input("API_ID (from my.telegram.org): "))
API_HASH = os.getenv("API_HASH") or input("API_HASH: ")

with Client("gen", api_id=API_ID, api_hash=API_HASH, in_memory=True) as app:
    print("\n==== SESSION STRING (paste into .env as SESSION_NAME) ====\n")
    print(app.export_session_string())
    print()
