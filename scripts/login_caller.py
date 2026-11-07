"""One-off interactive login for the caller account (the second Telegram account).

Run:  .venv/bin/python scripts/login_caller.py
Creates the session file at CALLER_SESSION_PATH.
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()


async def main() -> None:
    api_id, api_hash = os.getenv("API_ID"), os.getenv("API_HASH")
    owner_id = int(os.getenv("OWNER_ID", "0"))
    path = os.getenv("CALLER_SESSION_PATH", "data/caller.session")
    if not api_id or not api_hash:
        sys.exit("Set API_ID and API_HASH in .env first (from https://my.telegram.org).")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    client = TelegramClient(path, int(api_id), api_hash)
    await client.start()  # prompts for phone number, SMS code and 2FA password
    os.chmod(path, 0o600)
    me = await client.get_me()
    print(f"Logged in as {me.first_name} (id {me.id}). Session saved to {path}.")

    await client.get_dialogs()
    try:
        owner = await client.get_entity(owner_id)
        print(f"Can see the owner: {owner.first_name}. Calls should work.")
    except ValueError:
        print(
            "Cannot see the owner yet. From the CALLER account, send any message to your main account "
            "(or open a chat with it), then run this script again."
        )
    await client.disconnect()


asyncio.run(main())
