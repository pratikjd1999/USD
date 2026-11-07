import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, TypeHandler, filters)

from . import timeutil
from .feed import FeedError
from .models import Quote, money
from .services import Services
from .validation import ValidationError, validate_target

log = logging.getLogger(__name__)

MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ Set price alert", callback_data="set"), InlineKeyboardButton("🔄 Refresh price", callback_data="refresh")],
    [InlineKeyboardButton("📋 My alerts", callback_data="list"), InlineKeyboardButton("🗑 Delete alert", callback_data="del")],
    [InlineKeyboardButton("🛑 Stop alarm", callback_data="stop"), InlineKeyboardButton("ℹ️ Status", callback_data="status")],
])
CANCEL = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel")]])


def svc_of(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["svc"]


async def current_quote(svc: Services, max_age_sec: float = 15) -> Quote:
    """Latest watcher quote if fresh, otherwise one direct fetch (no retries). May raise FeedError."""
    latest = svc.prices.latest
    if latest and (timeutil.now() - latest.fetched_at).total_seconds() <= max_age_sec:
        return latest
    return await svc.feed.fetch(retry=False)


async def current_price(svc: Services, max_age_sec: float = 15) -> float:
    return (await current_quote(svc, max_age_sec)).price


async def menu_text(svc: Services) -> str:
    try:
        quote = await current_quote(svc)
        price = f"{svc.cfg.symbol}: {money(quote.price)}\nChecked {timeutil.fmt(quote.fetched_at)}"
    except FeedError:
        price = f"{svc.cfg.symbol}: price unavailable right now"
    return f"Price Alert Bot\n\n{price}\n\nWhat would you like to do?"


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every other handler: anyone not on the allow list is logged and ignored."""
    user = update.effective_user
    if user is None or user.id not in svc_of(context).cfg.authorized_ids:
        log.warning("ignored update from unauthorized user %s (@%s)", user.id if user else None, user.username if user else None)
        raise ApplicationHandlerStop


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("awaiting_target", None)
    await update.effective_message.reply_text(await menu_text(svc_of(context)), reply_markup=MENU)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc, query = svc_of(context), update.callback_query
    action, _, arg = query.data.partition(":")
    reply = query.message.reply_text

    if action == "stop":
        from_alarm = (query.message.text or "").startswith("🚨")  # the Stop button on an alarm message
        stopped = svc.engine.stop("button" if from_alarm else "menu")
        await query.answer("Stopping…" if stopped else "No alarm is running.")
        return
    await query.answer()

    if action == "refresh":
        try:
            await query.edit_message_text(await menu_text(svc), reply_markup=MENU)
        except BadRequest:  # "message is not modified": the price has not changed since the last refresh
            pass
    elif action == "cancel":
        context.user_data.pop("awaiting_target", None)
        await reply("Cancelled.", reply_markup=MENU)
    elif action == "set":
        if svc.db.count_active() >= svc.cfg.max_active_alerts:
            await reply(f"You already have {svc.cfg.max_active_alerts} active alerts. Delete one first.")
            return
        try:
            price = await current_price(svc)
        except FeedError:
            await reply(f"Couldn't fetch the {svc.cfg.symbol} price right now. Try again in a moment.")
            return
        context.user_data["awaiting_target"] = True
        await reply(f"{svc.cfg.symbol} is {money(price)}. Send your target price.", reply_markup=CANCEL)
    elif action == "list":
        alerts = svc.db.active_alerts()
        if not alerts:
            await reply("No active alerts.", reply_markup=MENU)
            return
        lines = [f"#{a.id}  {a.symbol} {a.phrase} {money(a.target)}  ·  set {timeutil.fmt(timeutil.parse(a.created_at))}" for a in alerts]
        await reply("Active alerts:\n" + "\n".join(lines), reply_markup=MENU)
    elif action == "del" and not arg:
        alerts = svc.db.active_alerts()
        if not alerts:
            await reply("No active alerts to delete.", reply_markup=MENU)
            return
        buttons = [[InlineKeyboardButton(f"#{a.id}  {a.phrase} {money(a.target)}", callback_data=f"del:{a.id}")] for a in alerts]
        await reply("Tap an alert to delete it:", reply_markup=InlineKeyboardMarkup(buttons))
    elif action == "del":
        ok = svc.db.delete_alert(int(arg))
        await reply(f"Deleted alert #{arg}." if ok else f"Alert #{arg} is no longer active.", reply_markup=MENU)
    elif action == "status":
        await reply(status_text(svc), reply_markup=MENU)


def status_text(svc: Services) -> str:
    quote = svc.prices.latest
    lines = [
        f"{svc.cfg.symbol}: {money(quote.price)}" if quote else f"{svc.cfg.symbol}: no price yet",
        *([f"Price time: {timeutil.fmt(quote.updated_at)}"] if quote else []),
        f"Source: {svc.cfg.source}",
        f"Active alerts: {svc.db.count_active()}",
        f"Alarm window: {svc.cfg.alert_window_start:%H:%M}–{svc.cfg.alert_window_end:%H:%M} {timeutil.now():%Z}",
    ]
    if svc.engine.running:
        lines.append("🚨 An alarm is ringing — tap Stop alarm.")
    return "\n".join(lines)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc, message = svc_of(context), update.effective_message
    if not context.user_data.get("awaiting_target"):
        await message.reply_text("Use the menu:", reply_markup=MENU)
        return
    try:
        current = await current_price(svc)
    except FeedError:
        await message.reply_text(f"Couldn't fetch the {svc.cfg.symbol} price right now. Send your target again in a moment.", reply_markup=CANCEL)
        return
    try:
        target = validate_target(message.text, current, svc.db.count_active(), svc.cfg.max_deviation_pct, svc.cfg.max_active_alerts)
    except ValidationError as exc:
        await message.reply_text(f"{exc}\nSend another price, or Cancel.", reply_markup=CANCEL)
        return
    alert = svc.db.add_alert(target, "above" if target > current else "below", svc.cfg.symbol)
    context.user_data.pop("awaiting_target", None)
    log.info("alert %s created: %s %s (current %s)", alert.id, alert.direction, target, current)
    await message.reply_text(f"Alert set: {alert.symbol} {alert.phrase} {money(target)}. Current {money(current)}.", reply_markup=MENU)


def register_handlers(app: Application) -> None:
    app.add_handler(TypeHandler(Update, guard), group=-1)
    app.add_handler(CommandHandler(["start", "menu"], menu))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
