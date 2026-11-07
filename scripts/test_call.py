"""Ring the owner once for 15 seconds to check that the caller account works.

Run:  .venv/bin/python scripts/test_call.py
"""
import asyncio
import logging

from dotenv import load_dotenv

from pricebot.caller import Caller
from pricebot.config import load_config

load_dotenv()
logging.basicConfig(level=logging.INFO)


async def main() -> None:
    caller = Caller(load_config())
    await caller.start()
    print("Ringing the owner for 15 seconds… answer it to test the answered signal.")
    print("Answered!" if await caller.ring(15) else "Not answered (or the call failed, see log above).")
    await caller.stop()


asyncio.run(main())
