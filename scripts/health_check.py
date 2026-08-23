#!/usr/bin/env python3
"""Health check script — pings bot + AI server, alerts via Telegram if down."""
import os
import json
import time
import httpx
from datetime import datetime
from pathlib import Path

# Config
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
ALERT_CHAT_ID = int(ALLOWED_USERS[0]) if ALLOWED_USERS and ALLOWED_USERS[0] else 0

ENDPOINTS = {
    "yuki-bot": "http://127.0.0.1:8000/health",
    "yuki-ai-server": "http://127.0.0.1:8001/health",
}

ALERT_COOLDOWN = 600  # 10 min between alerts per service

LOG_DIR = Path("/opt/yuki-bot/logs")
LOG_DIR.mkdir(exist_ok=True)

STATE_FILE = LOG_DIR / ".health_state.json"


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def send_telegram_alert(chat_id: int, text: str):
    if not BOT_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception:
        pass


def check_endpoint(name: str, url: str) -> bool:
    try:
        r = httpx.get(url, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state = load_state()
    results = []

    for name, url in ENDPOINTS.items():
        ok = check_endpoint(name, url)
        status = "OK" if ok else "DOWN"
        results.append(f"{name}: {status}")

        if not ok:
            last = state.get(f"alert_{name}", 0)
            if time.time() - last > ALERT_COOLDOWN:
                send_telegram_alert(ALERT_CHAT_ID, f"⚠️ {name} DOWN! — {now}")
                state[f"alert_{name}"] = time.time()
        else:
            state.pop(f"alert_{name}", None)

    log_line = f"[{now}] {' | '.join(results)}"
    print(log_line)

    with open(LOG_DIR / "health.log", "a") as f:
        f.write(log_line + "\n")

    save_state(state)


if __name__ == "__main__":
    main()
