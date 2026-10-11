"""Log in to Telegram once and print a StringSession for the Actions secret.

    export TG_API_ID=... TG_API_HASH=...
    python tg_session_string.py

Paste the printed string into the TG_SESSION_STRING repository secret. Anyone
holding it can act as your Telegram account; revoke it under Settings ->
Devices in Telegram if it leaks.
"""
import os

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

with TelegramClient(StringSession(), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as client:
    print(client.session.save())
