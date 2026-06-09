# Contributing

Thanks for your interest in improving **tg-streaming-bot**!

## Getting set up
1. Fork & clone the repo.
2. Copy `example.env` to `.env` and fill in the **Required** block (see the
   README → Setup). You'll need a Telegram bot token, API credentials, and an
   assistant session string.
3. Run it with Docker — `docker compose up -d --build` — or locally with
   Python 3.11 and `pip install -r requirements.txt`.

## Project layout
- `program/` — command handlers, one concern per file (`music`, `video`,
  `radio`, `library`, `search`, …). They're auto-loaded as Pyrogram plugins.
- `driver/` — clients, command filters, queue state, ffmpeg/transcode helpers,
  shared utilities.
- `config.py` — all configuration, read from the environment (`.env`). Add new
  settings here with a sensible default.
- `main.py` — startup: registers the bot commands and starts the clients and
  background tasks.

## Guidelines
- Keep instance-specific values (user IDs, channel links, file paths, images)
  in `config.py` / `.env`, never hardcoded in code — the project must run for
  anyone out of the box.
- Match the surrounding style; prefer small, focused commits.
- Before opening a PR, make sure `python -m compileall -q .` passes. CI runs a
  syntax + flake8 showstopper check (`E9,F63,F7,F82`).

## Don't commit secrets
`.env`, `*.session`, `reality-vpn.json`, and `docker-compose.override.yml` are
gitignored — keep it that way. When sharing logs in an issue, redact tokens and
session strings.

## Reporting issues
Open an issue describing what you did, what you expected, and the relevant logs
(`docker compose logs bot`, with secrets removed).
