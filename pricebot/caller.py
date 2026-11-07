"""Rings the owner with a real Telegram call from a second account (Telethon).

No audio is exchanged: we send phone.RequestCall, which makes the owner's phone ring,
and hang up after a while. If the owner answers, Telegram tells us with a
PhoneCallAccepted update, which the alarm treats as a stop signal.
"""
import asyncio
import logging
import os
import random

from telethon import TelegramClient, events, types
from telethon.errors import RPCError
from telethon.tl.functions.phone import DiscardCallRequest, RequestCallRequest

from .config import Config

log = logging.getLogger(__name__)

PROTOCOL = types.PhoneCallProtocol(
    min_layer=92, max_layer=92, udp_p2p=True, udp_reflector=True, library_versions=["4.0.0"]
)


class Caller:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = TelegramClient(cfg.caller_session_path, cfg.api_id, cfg.api_hash)
        self._call = None  # the active PhoneCallWaiting, if any
        self._result: asyncio.Future | None = None

    async def start(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            raise RuntimeError("caller session is not logged in; run scripts/login_caller.py")
        await self.client.get_dialogs()  # fills the entity cache so the owner can be resolved
        self.client.add_event_handler(self._on_update, events.Raw(types.UpdatePhoneCall))
        log.info("caller account connected")

    async def stop(self) -> None:
        await self.hangup()
        await self.client.disconnect()

    async def _on_update(self, update: types.UpdatePhoneCall) -> None:
        call = update.phone_call
        if self._call is None or getattr(call, "id", None) != self._call.id:
            return
        if isinstance(call, types.PhoneCallAccepted):
            self._settle("answered")
        elif isinstance(call, types.PhoneCallDiscarded):
            self._settle("discarded")

    def _settle(self, outcome: str) -> None:
        if self._result and not self._result.done():
            self._result.set_result(outcome)

    async def ring(self, seconds: float) -> bool:
        """Ring the owner for up to `seconds`. True if they answered; False otherwise (including failures)."""
        loop = asyncio.get_running_loop()
        self._result = loop.create_future()
        try:
            owner = await self.client.get_input_entity(self.cfg.owner_id)
            res = await self.client(RequestCallRequest(
                user_id=owner,
                random_id=random.randint(0, 2**31 - 1),
                g_a_hash=os.urandom(32),
                protocol=PROTOCOL,
            ))
            self._call = res.phone_call
        except (RPCError, ValueError) as exc:
            log.error("call failed: %s: %s", type(exc).__name__, exc)
            return False
        try:
            outcome = await asyncio.wait_for(asyncio.shield(self._result), timeout=seconds)
        except asyncio.TimeoutError:
            outcome = "unanswered"
        finally:
            await self.hangup()
        log.info("call ended: %s", outcome)
        return outcome == "answered"

    async def hangup(self) -> None:
        call, self._call = self._call, None
        if call is None:
            return
        try:
            await self.client(DiscardCallRequest(
                peer=types.InputPhoneCall(id=call.id, access_hash=call.access_hash),
                duration=0,
                reason=types.PhoneCallDiscardReasonHangup(),
                connection_id=0,
            ))
        except RPCError as exc:
            log.warning("hangup failed: %s", exc)
