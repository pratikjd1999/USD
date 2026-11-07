# Price Alert Bot

Telegram bot that rings you when BTC crosses a target. Spec: [prd.md](prd.md).

## Run locally

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # fill in BOT_TOKEN and OWNER_ID
.venv/bin/python -m pricebot  # start the bot (Ctrl+C to stop)
.venv/bin/python -m pytest    # run the tests
```

- `BOT_TOKEN`: create a bot with [@BotFather](https://t.me/BotFather).
- `OWNER_ID`: your numeric Telegram user ID (e.g. from [@userinfobot](https://t.me/userinfobot)).
- Voice calls are optional. Without `API_ID`/`API_HASH` the alarm uses repeated bot messages only.

## Enable voice calls (second Telegram account)

1. Get `API_ID` / `API_HASH` for the caller account at https://my.telegram.org and put them in `.env`.
2. `.venv/bin/python scripts/login_caller.py` (enter the phone number, SMS code and 2FA password).
3. From the caller account, send your main account one message; on your main account, allow calls from contacts/everyone.
4. `.venv/bin/python scripts/test_call.py` should make your main account ring for 15 s.

## Deploy on a GCP VM (systemd)

Full details in [prd.md](prd.md) section 7. Short version, on Debian 12:

```bash
sudo timedatectl set-timezone Asia/Kolkata
sudo apt install -y python3-venv git sqlite3
sudo useradd --system --create-home --shell /usr/sbin/nologin pricebot
sudo git clone <repo-url> /opt/pricebot && cd /opt/pricebot
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo mkdir -p data && sudo cp .env.example .env   # fill in secrets, then:
sudo chown -R pricebot:pricebot /opt/pricebot && sudo chmod 600 .env
sudo -u pricebot .venv/bin/python scripts/login_caller.py   # only if using voice calls
sudo cp deploy/pricebot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pricebot
journalctl -u pricebot -f
```

`.env`, `data/alerts.db` and `data/caller.session` are git-ignored; create or copy them on the VM.
Update with `git pull`, `pip install -r requirements.txt`, `sudo systemctl restart pricebot`.
