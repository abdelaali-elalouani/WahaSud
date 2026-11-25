#!/usr/bin/env python3
"""WahaSud server: REST API + static frontend. Python standard library only.

    python backend/server.py                 # http://127.0.0.1:8000
    WAHASUD_API_KEY=secret python backend/server.py --host 0.0.0.0 --port 8000

Environment variables
    WAHASUD_API_KEY     if set, writing sensor readings requires the header X-API-Key
    WAHASUD_DB          path of the SQLite file (default: data/wahasud.db)
    WAHASUD_WEATHER     set to 0 to stay on the simulated energy forecast (same as --no-weather)
    WAHASUD_GRID_PRICE  cost of a MWh of back-up grid electricity, in MAD (default 1100)
    WAHASUD_CO2_FACTOR  tonnes of CO2 per MWh of the grid (default 0.65)
"""
from __future__ import annotations

import argparse
import hmac
import json
import math
import mimetypes
import os
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import engine  # noqa: E402
import weather  # noqa: E402
from db import TZ, Store, hour_iso  # noqa: E402

ROOT = HERE.parent
MAX_BODY = 1_000_000          # bytes
MAX_BATCH = 2000              # readings per request
MAX_AGE_DAYS = 90             # oldest accepted reading
DECISION_STATUSES = ("valide", "reporte", "annule")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def clean(obj):
    """Make a structure JSON safe: round floats, drop NaN/inf."""
    if isinstance(obj, float):
        return round(obj, 3) if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def parse_reading(obj, index: int, now: datetime):
    """Validate one reading; returns (city_id, hour_utc, flow_m3h, level_pct)."""
    where = f"reading {index}: "
    if not isinstance(obj, dict):
        raise ApiError(400, where + "must be a JSON object")
    city = obj.get("city_id")
    if city not in engine.CITY_IDS:
        raise ApiError(400, where + "city_id must be one of " + ", ".join(engine.CITY_IDS))
    flow = obj.get("flow_m3h")
    if isinstance(flow, bool) or not isinstance(flow, (int, float)) or not math.isfinite(flow) or not 0 <= flow <= 1_000_000:
        raise ApiError(400, where + "flow_m3h must be a number between 0 and 1000000")
    level = obj.get("level_pct")
    if level is not None:
        if isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level) or not 0 <= level <= 100:
            raise ApiError(400, where + "level_pct must be a number between 0 and 100")
        level = float(level)
    ts = obj.get("ts")
    if ts is None:
        when = now
    else:
        try:
            when = datetime.fromisoformat(str(ts))
        except ValueError:
            raise ApiError(400, where + "ts must be an ISO 8601 date-time") from None
        if when.tzinfo is None:           # naive time = Morocco local time
            when = when.replace(tzinfo=TZ)
    if when > now + timedelta(hours=1):
        raise ApiError(400, where + "ts is in the future")
    if when < now - timedelta(days=MAX_AGE_DAYS):
        raise ApiError(400, where + f"ts is older than {MAX_AGE_DAYS} days")
    return city, hour_iso(when), float(flow), level


def number_param(query: dict, name: str, lo: float, hi: float) -> float:
    raw = query.get(name, [""])[0]
    try:
        value = float(raw)
    except ValueError:
        raise ApiError(400, f"{name} must be a number between {lo:g} and {hi:g}") from None
    if not math.isfinite(value) or not lo <= value <= hi:
        raise ApiError(400, f"{name} must be a number between {lo:g} and {hi:g}")
    return value


def make_server(host: str, port: int, store: Store, api_key: str | None = None,
                frontend: Path | None = None, forecast: "weather.WeatherForecast | None" = None,
                assumptions: dict | None = None) -> ThreadingHTTPServer:
    """forecast: real weather source (None = simulated energy forecast, which keeps tests deterministic)."""
    frontend_dir = (frontend or ROOT / "frontend").resolve()

    def inputs(query: dict):
        """Validated parameters shared by every read endpoint."""
        scenario = query.get("scenario", ["prevu"])[0]
        if scenario not in engine.SCENARIOS:
            raise ApiError(400, "scenario must be one of " + ", ".join(engine.SCENARIOS))
        leak = query.get("leak", [""])[0] or None
        if leak is not None and leak not in engine.CITY_IDS:
            raise ApiError(400, "leak must be one of " + ", ".join(engine.CITY_IDS))
        lang = query.get("lang", ["fr"])[0] or "fr"
        if lang not in engine.LANGS:
            raise ApiError(400, "lang must be one of " + ", ".join(engine.LANGS))
        return scenario, leak, lang

    def compute(query: dict) -> dict:
        scenario, leak, lang = inputs(query)
        now = datetime.now(TZ)
        model = engine.compute(scenario, leak, store.sensor_inputs(now), today=now.date(),
                               forecast=forecast.get() if forecast else None, lang=lang, assumptions=assumptions)
        model["forecast"]["updated"] = forecast.updated_iso() if forecast else None
        statuses = store.today_statuses(now.date().isoformat())
        for rec in model["decisions"]:
            rec["status"] = statuses.get(rec["id"])
        return model

    class Handler(BaseHTTPRequestHandler):
        server_version = "WahaSud/1.0"

        # ---- plumbing ---------------------------------------------------
        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj):
            self._send(status, json.dumps(clean(obj), ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ApiError(400, "invalid Content-Length") from None
            if length <= 0:
                raise ApiError(400, "a JSON body is required")
            if length > MAX_BODY:
                raise ApiError(413, "body too large")
            try:
                data = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                raise ApiError(400, "body is not valid JSON") from None
            if not isinstance(data, dict):
                raise ApiError(400, "body must be a JSON object")
            return data

        def _require_key(self):
            if api_key and not hmac.compare_digest(self.headers.get("X-API-Key", "").encode(), api_key.encode()):
                raise ApiError(401, "missing or invalid X-API-Key")

        def _static(self, path: str):
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (frontend_dir / rel).resolve()
            if frontend_dir not in target.parents or not target.is_file():
                raise ApiError(404, "not found")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def _dispatch(self, method: str):
            try:
                url = urlsplit(self.path)
                path, query = url.path, parse_qs(url.query)
                if not path.startswith("/api/"):
                    if method not in ("GET", "HEAD"):
                        raise ApiError(405, "method not allowed")
                    return self._static(path)
                handler = ROUTES.get((method, path.rstrip("/")))
                if handler is None:
                    known = {p for (_, p) in ROUTES}
                    raise ApiError(405 if path.rstrip("/") in known else 404, "unknown endpoint or method")
                self._json(200, handler(self, query))
            except ApiError as err:
                self._json(err.status, {"error": err.message})
            except Exception as err:  # never leak internals to the client
                sys.stderr.write(f"[error] {method} {self.path}: {err!r}\n")
                self._json(500, {"error": "internal server error"})

        def do_GET(self):
            self._dispatch("GET")

        def do_HEAD(self):
            self._dispatch("HEAD")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def log_message(self, fmt, *args):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ---- endpoints ------------------------------------------------------
    def health(h, q):
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def dashboard(h, q):
        return compute(q)

    def energy(h, q):
        m = compute(q)
        return {"scenario": m["scenario"], "date": m["date"], **m["energy"]}

    def water(h, q):
        m = compute(q)
        return {"scenario": m["scenario"], "date": m["date"], **m["water"]}

    def decisions(h, q):
        m = compute(q)
        return {"scenario": m["scenario"], "date": m["date"], "decisions": m["decisions"]}

    def history(h, q):
        try:
            limit = int(q.get("limit", ["50"])[0])
        except ValueError:
            raise ApiError(400, "limit must be an integer") from None
        return {"history": store.history(max(1, min(limit, 500)))}

    def post_decision(h, q):
        body = h._body()
        rec_id, status = body.get("id"), body.get("status")
        if status not in DECISION_STATUSES:
            raise ApiError(400, "status must be one of " + ", ".join(DECISION_STATUSES))
        # The title comes from the server's own computation, not from the client
        model = compute({"scenario": [str(body.get("scenario") or "prevu")], "leak": [str(body.get("leak") or "")],
                         "lang": [str(body.get("lang") or "fr")]})
        rec = next((r for r in model["decisions"] if r["id"] == rec_id), None)
        if rec is None:
            raise ApiError(404, "this decision does not exist in the current situation")
        store.log_decision(datetime.now(TZ).date().isoformat(), rec["id"], rec["title"], status)
        return {"ok": True, "id": rec["id"], "status": None if status == "annule" else status}

    def sensors(h, q):
        return {"sensors": compute(q)["sensors"]}

    def impact(h, q):
        m = compute(q)
        return {"scenario": m["scenario"], "date": m["date"], **m["impact"]}

    def whatif(h, q):
        scenario, leak, lang = inputs(q)
        site = q.get("site", [""])[0]
        if site not in engine.SITE_IDS:
            raise ApiError(400, "site must be one of " + ", ".join(engine.SITE_IDS))
        add_mw = number_param(q, "add_mw", 0.1, 500)
        now = datetime.now(TZ)
        return engine.whatif(site, add_mw, scenario, inject=leak, sensors=store.sensor_inputs(now), today=now.date(),
                             forecast=forecast.get() if forecast else None, lang=lang, assumptions=assumptions)

    def post_readings(h, q):
        h._require_key()
        body = h._body()
        items = body["readings"] if "readings" in body else [body]
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
            raise ApiError(400, f"readings must be a list of 1 to {MAX_BATCH} items")
        now = datetime.now(timezone.utc)
        rows = [parse_reading(item, i, now) for i, item in enumerate(items)]
        return {"accepted": store.upsert_readings(rows)}

    def delete_readings(h, q):
        h._require_key()
        city = q.get("city", [""])[0] or None
        if city is not None and city not in engine.CITY_IDS:
            raise ApiError(400, "city must be one of " + ", ".join(engine.CITY_IDS))
        return {"deleted": store.reset_readings(city)}

    ROUTES = {
        ("GET", "/api/health"): health,
        ("GET", "/api/dashboard"): dashboard,
        ("GET", "/api/energy"): energy,
        ("GET", "/api/water"): water,
        ("GET", "/api/decisions"): decisions,
        ("GET", "/api/decisions/history"): history,
        ("POST", "/api/decisions"): post_decision,
        ("GET", "/api/sensors"): sensors,
        ("GET", "/api/impact"): impact,
        ("GET", "/api/whatif"): whatif,
        ("POST", "/api/readings"): post_readings,
        ("DELETE", "/api/readings"): delete_readings,
    }
    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description="WahaSud server")
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1 (local only)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--db", default=os.environ.get("WAHASUD_DB", str(ROOT / "data" / "wahasud.db")))
    parser.add_argument("--no-weather", action="store_true",
                        default=os.environ.get("WAHASUD_WEATHER", "1") == "0",
                        help="do not call Open-Meteo, use the simulated energy forecast")
    args = parser.parse_args()
    api_key = os.environ.get("WAHASUD_API_KEY") or None
    assumptions = {}
    try:
        if os.environ.get("WAHASUD_GRID_PRICE"):
            assumptions["grid_price_mad_mwh"] = float(os.environ["WAHASUD_GRID_PRICE"])
        if os.environ.get("WAHASUD_CO2_FACTOR"):
            assumptions["co2_t_per_mwh"] = float(os.environ["WAHASUD_CO2_FACTOR"])
    except ValueError:
        parser.error("WAHASUD_GRID_PRICE and WAHASUD_CO2_FACTOR must be numbers")
    forecast = None if args.no_weather else weather.WeatherForecast(engine.SOLAR_SITES, engine.WIND_SITES)
    server = make_server(args.host, args.port, Store(args.db), api_key, forecast=forecast, assumptions=assumptions or None)
    if forecast:
        forecast.start()
        print("Energy forecast: Open-Meteo (falls back to the simulation if the service is unreachable).")
    else:
        print("Energy forecast: simulation (weather service disabled).")
    print(f"WahaSud running on http://{args.host}:{args.port}  (database: {args.db})")
    if not api_key:
        print("Warning: WAHASUD_API_KEY is not set, sensor writes are open. Set it before exposing the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
