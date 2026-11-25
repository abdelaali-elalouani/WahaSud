#!/usr/bin/env python3
"""Send simulated hourly sensor readings to a running WahaSud server.

It plays the role of the tank flow meters: for each city it sends the last 24
complete hours, which is what the server needs to switch that city from its
built-in simulation to "capteurs" (real readings).

Examples
    python scripts/simulate_sensors.py                          # all cities, normal flow
    python scripts/simulate_sensors.py --leak guelmim:0.25      # leak = 25 % of the daily demand
    python scripts/simulate_sensors.py --freeze boujdour        # stuck sensor (flat flow)
    python scripts/simulate_sensors.py --api-key SECRET         # if the server has WAHASUD_API_KEY
    python scripts/simulate_sensors.py --reset                  # delete all readings

Only the standard library is used.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import engine  # noqa: E402
from db import TZ  # noqa: E402


def call(url: str, method: str, key: str | None, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")
        sys.exit(f"Server answered {err.code}: {detail}")
    except urllib.error.URLError as err:
        sys.exit(f"Cannot reach {url}: {err.reason}. Is the server running?")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--api-key", default=os.environ.get("WAHASUD_API_KEY"))
    ap.add_argument("--cities", default="all", help="comma separated city ids, or 'all'")
    ap.add_argument("--leak", action="append", default=[], metavar="CITY[:SHARE]",
                    help="add a leak equal to SHARE of the daily demand (default 0.2); repeatable")
    ap.add_argument("--freeze", action="append", default=[], metavar="CITY", help="stuck sensor (flat flow)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--reset", action="store_true", help="delete all readings and exit")
    args = ap.parse_args()
    base = args.url.rstrip("/")

    if args.reset:
        print(call(base + "/api/readings", "DELETE", args.api_key))
        return

    leaks = {}
    for item in args.leak:
        city, _, share = item.partition(":")
        leaks[city] = float(share or 0.2)
    ids = engine.CITY_IDS if args.cities == "all" else args.cities.split(",")
    unknown = [c for c in ids + list(leaks) + args.freeze if c not in engine.CITY_IDS]
    if unknown:
        sys.exit("Unknown city id(s): " + ", ".join(unknown) + ". Valid: " + ", ".join(engine.CITY_IDS))

    rnd = random.Random(args.seed)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    readings = []
    for city in engine.CITIES:
        if city["id"] not in ids:
            continue
        leak_m3_day = city["dem"] * leaks.get(city["id"], 0)
        for back in range(24, 0, -1):                       # the 24 last complete hours
            slot = end - timedelta(hours=back)
            hour = slot.astimezone(TZ).hour
            if city["id"] in args.freeze:
                flow = city["dem"] / 24
            else:
                flow = city["dem"] / 24 * engine.PROFILE[hour] / engine.PMEAN * (1 + (rnd.random() - .5) * .06) + leak_m3_day / 24
            readings.append({"city_id": city["id"], "ts": slot.isoformat(), "flow_m3h": round(flow, 2),
                             "level_pct": city["lvl"] if back == 1 else None})
    print(call(base + "/api/readings", "POST", args.api_key, {"readings": readings}),
          f"-> {len(readings)} readings for {len(ids)} cities")


if __name__ == "__main__":
    main()
