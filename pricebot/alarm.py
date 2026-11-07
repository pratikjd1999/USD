import asyncio
import logging
from dataclasses import dataclass, field

from . import timeutil
from .config import Config
from .db import Database
from .models import Alert, money

log = logging.getLogger(__name__)


@dataclass
class Alarm:
    alerts: list[Alert]
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str | None = None


class AlarmEngine:
    """Runs the message loop and the call loop together until the first stop signal."""

    def __init__(self, cfg: Config, db: Database, notifier, caller=None):
        self.cfg, self.db, self.notifier, self.caller = cfg, db, notifier, caller
        self._alarm: Alarm | None = None
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._alarm is not None and not self._alarm.stop_event.is_set()

    async def start(self, alerts: list[Alert]) -> None:
        if self._task and not self._task.done():
            if self.running:
                self._alarm.alerts.extend(alerts)  # a new hit while ringing joins the same alarm
                await self.notifier.send(self._text(self._alarm), stop_button=True, everyone=True)
                return
            await self._task  # previous alarm is still cleaning up
        self._alarm = Alarm(alerts=list(alerts))
        self._task = asyncio.create_task(self._run(self._alarm))

    async def resume(self) -> None:
        """Restart an alarm that was interrupted by a crash or restart."""
        pending = self.db.pending_triggered()
        if pending:
            log.warning("resuming alarm for alerts %s", [a.id for a in pending])
            await self.notifier.send("♻️ The app restarted while an alarm was active. Resuming it.", everyone=True)
            await self.start(pending)

    def stop(self, reason: str) -> bool:
        if not self.running:
            return False
        self._alarm.reason = reason
        self._alarm.stop_event.set()
        return True

    async def wait(self) -> None:
        if self._task:
            await self._task

    def _text(self, alarm: Alarm) -> str:
        return "\n".join(
            f"🚨 {a.symbol} hit {money(a.trigger_price)} (target {money(a.target)})" for a in alarm.alerts
        )

    async def _message_loop(self, alarm: Alarm) -> None:
        while True:
            await self.notifier.send(self._text(alarm), stop_button=True, everyone=True)
            await asyncio.sleep(self.cfg.msg_interval_sec)

    async def _call_loop(self, alarm: Alarm) -> None:
        while True:
            if await self.caller.ring(self.cfg.call_ring_sec):
                self.stop("call_answered")
                return
            await asyncio.sleep(self.cfg.call_pause_sec)

    async def _run(self, alarm: Alarm) -> None:
        loops = [asyncio.create_task(self._message_loop(alarm))]
        if self.caller:
            loops.append(asyncio.create_task(self._call_loop(alarm)))
        try:
            await asyncio.wait_for(alarm.stop_event.wait(), timeout=self.cfg.alarm_timeout_sec)
        except asyncio.TimeoutError:
            alarm.reason = "timeout"
            alarm.stop_event.set()
        finally:
            for task in loops:
                task.cancel()
            await asyncio.gather(*loops, return_exceptions=True)
            if self.caller:
                try:
                    await self.caller.hangup()
                except Exception:
                    log.exception("hangup failed")
            reason = alarm.reason or "shutdown"
            if reason != "shutdown":  # on shutdown leave alerts triggered so the alarm resumes next start
                self.db.finish_alerts([a.id for a in alarm.alerts], reason)
            log.info("alarm ended (%s) for alerts %s", reason, [a.id for a in alarm.alerts])
        await self.notifier.send("⏰ Alarm timed out." if reason == "timeout" else "✅ Alarm stopped.", everyone=True)
