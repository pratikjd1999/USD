# Price Alert Bot — PRD

**Date:** 18 Oct 2026 (IST) · **Owner:** Pratik Jadhav

## 1. Overview

A personal Telegram bot for setting BTC price alerts. When an alert hits, it keeps ringing the owner's Telegram until they stop it. It runs as a single Python app on a free Google Cloud e2-micro VM and polls [gold-api.com](https://api.gold-api.com/price/BTC) **every 5 seconds**.

**Problem.** Watching a chart is tiring, and phone notifications are easy to miss. Telegram bots can't place calls, so a plain bot message isn't loud enough for an important level.

**Solution.**
- A bot menu to set and manage alerts.
- A price watcher polling `https://api.gold-api.com/price/BTC` every 5 seconds.
- An alarm that sends repeated bot messages and real Telegram voice calls (from a second "caller" account) until the owner taps **Stop**, answers the call, or **2 minutes** pass.

**Goals**
- Set an alert in under 30 seconds.
- Detect a hit on the first poll after the price crosses the target, even if it never lands exactly on it.
- Start ringing immediately and keep ringing until acknowledged.
- Run 24/7 at zero monthly cost (apart from a prepaid SIM for the caller account).

**Time standard.** All user-facing times, logs, timestamps and schedules use **Indian Standard Time (IST, UTC+5:30, `Asia/Kolkata`)**, formatted `DD Mon YYYY, HH:MM:SS IST` (24-hour).

## 2. User and stories

One user: the owner, identified by Telegram user ID. Everyone else is ignored.

| ID | As the owner, I want to… | So that… |
|----|--------------------------|----------|
| US-1 | open a menu in the bot | I never have to remember commands |
| US-2 | set an alert by typing a number | I can react without watching the chart |
| US-3 | be told if my price looks wrong | a typo doesn't create a useless alert |
| US-4 | see and delete my active alerts | I can keep my list clean |
| US-5 | have my phone ring until I respond | I don't miss the alert while away or asleep |
| US-6 | stop the alarm by tapping Stop, answering the call, or after 2 minutes | silencing it takes one action |
| US-7 | be warned when the feed or app stops working | I never rely on a dead system |

## 3. Functional requirements

**FR-1 Access control**
- Respond only to `OWNER_ID`. Ignore and log everyone else.

**FR-2 Menu**
- `/start` and `/menu` show inline buttons: *Set price alert, My alerts, Delete alert, Stop alarm, Status*.
- *Status* shows the last price, its time (IST), the source, and the number of active alerts.

**FR-3 Set price alert**
1. User taps *Set price alert*; the bot fetches the price and replies "BTC is $X. Send your target price."
2. User sends a number.
3. The bot validates it (FR-4). On failure it explains why and asks again; *Cancel* ends the flow.
4. Direction is set automatically: target above current = *rises to*, below = *falls to*.
5. The alert is saved as active and confirmed: "Alert set: BTC rises to $X. Current $Y."

**FR-4 Price validation**
- Positive number; commas and a leading `$` are stripped.
- At most 2 decimal places.
- Within ±30% of the current price (`MAX_DEVIATION_PCT`).
- Not equal to the current price.
- At most 20 active alerts.

**FR-5 My alerts / Delete alert**
- *My alerts* lists active alerts: ID, direction, target, created time (IST).
- *Delete alert* shows one button per active alert; tapping one marks it deleted and confirms.

**FR-6 Price watcher**
- Every **5 seconds** (`POLL_INTERVAL_SEC = 5`), `GET https://api.gold-api.com/price/BTC` with a 10-second timeout.
- Read `price` and `updatedAt`; keep the latest and previous price. Convert timestamps to IST for display.
- **Cross detection:** an alert fires if the target lies between the previous and current price (inclusive), or the current price is already past it.
- On failure, retry up to 3 times with 5, 15 and 30 s backoff, then wait for the next cycle. Cycles never overlap.

**FR-7 Trigger**
- Mark the alert *triggered* before starting the alarm, so it fires only once.
- If several fire in one poll, start one alarm listing all of them.
- **Session window:** alarms and calls run only between **05:30 and 23:59 IST**. If a target is hit outside that window, the alert is closed (`stopped_by = outside_session`) and the bot sends one message instead: "Alert #ID: BTC hit $X (target $T). Triggered outside session time." No alarm or call.

**FR-8 Alarm engine**
- **Message loop:** every 5 s, send "BTC hit $X (target $T)" with a Stop button.
- **Call loop:** ring for 30 s, pause 5 s, repeat.
- Both loops run together and end on the first stop signal (FR-10).
- **Timeout:** after **2 minutes** with no response, stop both loops and send "Alarm timed out".

**FR-9 Caller module**
- A second Telegram account (prepaid SIM) via Telethon; session stored in `caller.session`.
- Places a private call to `OWNER_ID` (`phone.RequestCall`), waits, then hangs up (`phone.DiscardCall`). No audio; the ring is the alert.
- Answering the call counts as a stop signal.
- If a call fails (privacy settings, flood wait), log it and keep the message loop running.

**FR-10 Stop**
- Stop signals: Stop button, *Stop alarm* menu item, answering the call, or the 2-minute timeout.
- On stop: end both loops, hang up any active call, mark alerts *done*, confirm "Alarm stopped."

**FR-11 Health monitor**
- **Feed down:** 3 failed poll cycles in a row → "Price feed down since HH:MM IST"; send a recovery message when it returns.
- **Feed stale:** `updatedAt` unchanged for 15 minutes → warning (BTC trades 24/7).
- **Heartbeat:** ping Healthchecks.io after successful cycles, at most once per minute. If pings stop for 10 minutes, Healthchecks.io emails the owner (covers a dead VM or crashed app).

**FR-12 Restart safety**
- On startup, load alerts from SQLite and resume. An alert left *triggered* but not *done* restarts its alarm.

## 4. Architecture

Six modules run in one Python process; the only outside services are Telegram, gold-api.com and Healthchecks.io (all outbound calls). The **alarm engine** is the hub: the watcher hands it hits, and it drives both the bot's messages and the caller's ring loop.

**Data flow**
1. Owner sets an alert; the handler validates it and saves it to SQLite.
2. Every 5 s the watcher fetches the price, logs it, and checks it against active alerts.
3. On a hit, the watcher marks the alert *triggered* and hands it to the alarm engine.
4. The alarm engine sends Stop-button messages every 5 s and has the caller ring in 30 s bursts.
5. Stop, answer or timeout ends both loops; the alert is marked *done*.
6. The health monitor pings Healthchecks.io and warns the owner if the feed fails or goes stale.

## 5. Data and configuration

All state is in one SQLite file (`data/alerts.db`). Secrets are in `.env`, never committed. Timestamps are stored as ISO 8601 with the IST offset (`+05:30`).

**Table `alerts`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto-increment |
| symbol | TEXT | `BTC` in v1 |
| target | REAL | Validated target, USD |
| direction | TEXT | `above` or `below` |
| status | TEXT | `active` → `triggered` → `done`, or `deleted` |
| created_at | TEXT | ISO 8601, IST |
| triggered_at | TEXT | Set when fired |
| trigger_price | REAL | Price that fired it |
| stopped_at | TEXT | Set on stop |
| stopped_by | TEXT | `button`, `menu`, `call_answered`, `timeout` or `outside_session` |

**Table `price_log`**: one row per poll (`ts`, `price`, `source_updated_at`, `ok`, `error`). Pruned daily at 00:00 IST, keeping 7 days.

**Configuration (`.env`)**

| Key | Example | Purpose |
|-----|---------|---------|
| BOT_TOKEN | from @BotFather | Telegram bot |
| OWNER_ID | 123456789 | Owner's Telegram user ID |
| API_ID / API_HASH | from my.telegram.org | Caller account (Telethon) |
| CALLER_SESSION_PATH | data/caller.session | Telethon login session |
| PRICE_URL | https://api.gold-api.com/price/BTC | Price source |
| POLL_INTERVAL_SEC | 5 | Poll spacing |
| TIMEZONE | Asia/Kolkata | IST for display, logs and schedules |
| ALERT_WINDOW_START / ALERT_WINDOW_END | 05:30 / 23:59 | Session window for alarms (IST) |
| MAX_DEVIATION_PCT | 30 | Validation band |
| MSG_INTERVAL_SEC | 5 | Alarm message spacing |
| CALL_RING_SEC / CALL_PAUSE_SEC | 30 / 5 | Ring loop timing |
| ALARM_TIMEOUT_SEC | 120 | Safety cap (2 minutes) |
| HEALTHCHECK_URL | https://hc-ping.com/… | Heartbeat |

## 6. Non-functional requirements

| Area | Requirement |
|------|-------------|
| Detection latency | Within one poll: ≤ 5 s, plus up to 50 s if retries are needed |
| Alarm latency | First message and first ring within 10 s of detection |
| Availability | 24/7; systemd restarts the app within 10 s of a crash and on VM reboot |
| Durability | Alerts survive restarts and reboots (SQLite on persistent disk) |
| Resources | Under 200 MB RAM and 5% average CPU on a 1 GB e2-micro |
| API etiquette | 1 request per 5 s (0.2/s), inside gold-api.com's "no multiple requests per second" rule |
| Security | Owner-only bot; `.env` and `caller.session` mode 600, never committed; SSH by key only; no inbound ports except SSH |
| Logging | Structured logs to journald in IST; every alert state change and stop reason recorded |
| Cost | $0/month on Google Cloud free tier; one prepaid SIM |
| Stack | Python 3.11+, python-telegram-bot, Telethon, httpx, SQLite, systemd |

## 7. Deployment (Google Cloud e2-micro)

One systemd service, outbound connections only (Telegram, gold-api.com, Healthchecks.io).

| Item | Choice |
|------|--------|
| Machine | e2-micro (2 shared vCPU, 1 GB RAM) |
| Region | us-central1, us-west1 or us-east1 (free-tier only) |
| OS | Debian 12 |
| Disk | 30 GB standard persistent disk |
| Firewall | Ingress: SSH only. Egress: default allow |
| Timezone | `Asia/Kolkata` (`timedatectl set-timezone Asia/Kolkata`) |
| Process manager | systemd, `Restart=always` |

**Setup**
1. Create the VM with the settings above and add your SSH key.
2. Set the timezone to IST; install Python 3.11+, git and sqlite3; create a `pricebot` user with no sudo.
3. Clone the repo to `/opt/pricebot`, create a virtualenv, `pip install -r requirements.txt`.
4. Copy `.env` to `/opt/pricebot/.env` and set mode 600.
5. Run `python scripts/login_caller.py` once and enter the SMS code to create `data/caller.session`.
6. Install `pricebot.service`, then `systemctl enable --now pricebot`.
7. Create a Healthchecks.io check (10-minute period) and put its ping URL in `.env`.
8. Test end to end: set an alert $1 from the current price; confirm messages, ring and Stop all work.

**Operations**
- **Logs:** `journalctl -u pricebot -f`
- **Update:** `git pull`, reinstall requirements, `systemctl restart pricebot`
- **Backup:** copy `data/alerts.db` and `data/caller.session` off the VM after any change (keep the session private).
- **Billing:** set a $1 budget alert in Google Cloud.

## 8. Risks and mitigations

Polling every 5 s makes alerts near-real-time, but only as fresh as the source: gold-api.com may refresh its price less often than every 5 s.

| Risk | Impact | Mitigation |
|------|--------|------------|
| Source updates slower than 5 s | Alerts fire later than polling suggests | Cross detection (FR-6); stale-feed warning (FR-11); pluggable source |
| gold-api.com is a one-person free service with no SLA | Outage or wrong price | Health monitor; optional backup source later |
| gold-api.com bans IPs sending multiple requests per second | Feed lost | 1 request per 5 s; no overlapping cycles; retries spaced 5–30 s |
| Caller account banned or logged out | No calls | Real prepaid SIM, low activity, calls only the owner; message loop keeps running; login failure alerts the owner |
| Owner's privacy settings block calls | No ring | Add caller as a contact; allow calls from contacts |
| VM down or app crashed | No alerts | systemd restart; Healthchecks.io emails if pings stop |
| Owner unreachable during alarm | Spam, flood limits | 2-minute cap; 5 s message spacing |
| `caller.session` leaked | Caller account compromised | Mode 600, never committed, private backups |
| Free-tier terms change | Unexpected cost | $1 budget alert |

## 9. Milestones and acceptance criteria

Each step is testable on a laptop before moving to the VM.

1. **Bot skeleton**: menu, owner check, set/list/delete alerts, validation, SQLite.
   *Done when:* an alert can be set, listed and deleted; invalid prices are rejected with a reason; a non-owner gets no reply.
2. **Price watcher**: 5-second polling, price log, cross detection, IST timestamps.
   *Done when:* a target between two consecutive prices fires exactly once; all times show in IST.
3. **Alarm + caller**: message loop, Telethon ring loop, Stop button, answer-to-stop, 2-minute cap.
   *Done when:* a fired alert rings within 10 s, and Stop, answering, or the timeout ends both loops.
4. **Health + restart safety**: feed down/stale warnings, heartbeat, resume on restart.
   *Done when:* blocking the price URL warns after 3 failed cycles; killing the app mid-alarm resumes it on restart.
5. **Deploy**: e2-micro VM, systemd, end-to-end test.
   *Done when:* the app survives a VM reboot and passes the end-to-end test.

**Open questions**
- [ ] Is 5 seconds the final interval, or should a faster feed (e.g. websocket) replace polling later?
- [ ] Is the 2-minute alarm cap right, or should it ring until stopped?
- [ ] Should v1 support XAU/USD as well as BTC?
- [ ] Should prices also be shown in INR alongside USD?
- [ ] Python is assumed (Telethon is better documented than GramJS). Confirm, or switch to Node.js.
