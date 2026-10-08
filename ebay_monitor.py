#!/usr/bin/env python3
"""eBay new-listing monitor using the official Browse API.

Setup:
  pip install requests
  export EBAY_CLIENT_ID=...        # App ID from developer.ebay.com (Production keyset)
  export EBAY_CLIENT_SECRET=...    # Cert ID
  export NTFY_TOPIC=some-long-random-string   # subscribe to it in the ntfy app

Run:
  python ebay_monitor.py            # loops forever
  python ebay_monitor.py --once     # single check (for cron)
"""
import argparse
import base64
import json
import os
import time
from pathlib import Path

import requests

QUERY = os.getenv("EBAY_QUERY", "yugioh ultimate")
MARKETPLACE = os.getenv("EBAY_MARKETPLACE", "EBAY_GB")  # EBAY_US, EBAY_DE, ...
INTERVAL = int(os.getenv("POLL_SECONDS", "120"))
STATE_FILE = Path(os.getenv("STATE_FILE", "seen_items.json"))
# Optional Browse API filter string, e.g. "price:[..50],priceCurrency:GBP" or
# "buyingOptions:{FIXED_PRICE}". Leave empty for no filter.
FILTER = os.getenv("EBAY_FILTER", "")

CLIENT_ID = os.environ["EBAY_CLIENT_ID"]
CLIENT_SECRET = os.environ["EBAY_CLIENT_SECRET"]
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")

_token = {"value": None, "expires": 0.0}


def get_token() -> str:
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials",
              "scope": "https://api.ebay.com/oauth/api_scope"},
        timeout=30,
    )
    if not r.ok:
        print(f"eBay token error {r.status_code}: {r.text}")
        print(f"(client id length={len(CLIENT_ID)}, secret length={len(CLIENT_SECRET)}, "
              f"id looks like production={'PRD' in CLIENT_ID})")
    r.raise_for_status()
    j = r.json()
    _token["value"] = j["access_token"]
    _token["expires"] = time.time() + j["expires_in"]
    return _token["value"]


def search() -> list[dict]:
    params = {"q": QUERY, "sort": "newlyListed", "limit": "50"}
    if FILTER:
        params["filter"] = FILTER
    r = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={"Authorization": f"Bearer {get_token()}",
                 "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE},
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("itemSummaries", [])


def notify(item: dict) -> None:
    price = item.get("price", {})
    price_s = f"{price.get('value', '?')} {price.get('currency', '')}".strip()
    kind = "/".join(item.get("buyingOptions", []))
    url = item.get("itemWebUrl", "")
    body = f"{item.get('title')}\n{price_s} ({kind})"
    print(f"NEW: {body.replace(chr(10), ' | ')}  {url}")

    if NTFY_TOPIC:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=body.encode("utf-8"),
                      headers={"Title": "New eBay listing", "Click": url,
                               "Priority": "high"}, timeout=15)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT, "text": f"{body}\n{url}"},
                      timeout=15)


def load_seen() -> set | None:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return None  # None = first run


def save_seen(seen: set) -> None:
    # keep the file bounded
    STATE_FILE.write_text(json.dumps(list(seen)[-5000:]))


def check() -> None:
    seen = load_seen()
    items = search()
    ids = {i["itemId"] for i in items}
    if seen is None:
        print(f"First run: recording {len(ids)} existing listings, no alerts sent.")
        save_seen(ids)
        return
    new = [i for i in items if i["itemId"] not in seen]
    for item in reversed(new):  # oldest first
        notify(item)
    if new:
        save_seen(seen | ids)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    while True:
        try:
            check()
        except Exception as e:  # keep running through transient errors
            print(f"Error: {e}")
            if args.once:
                raise  # fail loudly so GitHub shows the real problem
        if args.once:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
