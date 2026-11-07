import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)

STOP_MARKUP = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Stop", callback_data="stop")]])


class TelegramNotifier:
    """Sends messages to Telegram. Failures are logged, never raised, so an alarm keeps going.

    Messages go to the owner by default; `everyone=True` (alarm messages) also reaches the other allowed users.
    """

    def __init__(self, bot: Bot, owner_id: int, others: tuple[int, ...] = ()):
        self.bot, self.owner_id, self.others = bot, owner_id, others

    async def send(self, text: str, *, stop_button: bool = False, everyone: bool = False) -> None:
        recipients = [self.owner_id, *(self.others if everyone else ())]
        for chat_id in dict.fromkeys(recipients):
            try:
                await self.bot.send_message(chat_id, text, reply_markup=STOP_MARKUP if stop_button else None)
            except Exception as exc:
                log.error("could not send Telegram message to %s: %s", chat_id, exc)
