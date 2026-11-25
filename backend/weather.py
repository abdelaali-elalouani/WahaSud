"""Real weather forecast for WahaSud (Open-Meteo, free, no account, no key).

For every production site we ask Open-Meteo for the hourly solar irradiance
(shortwave radiation, W/m2) and the wind speed at 100 m (m/s) for the next 16
days, and convert them into energy per installed MW:

    solar  : MWh per MW and hour = irradiance / 1000 x PERFORMANCE_RATIO
    wind   : MWh per MW and hour = capacity factor of a generic turbine power curve

The engine multiplies these values by the installed power of each site, so
changing a site's power (what-if planner) needs no new weather request.

The server calls ``WeatherForecast.start()`` once: a background thread refreshes
the data every few hours. If the service is unreachable the previous data is
kept for up to ``MAX_AGE`` and then dropped, and the engine falls back to its
seeded simulation. Standard library only.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 16            # maximum horizon offered by the service
PERFORMANCE_RATIO = 0.80      # inverter, temperature, soiling and wiring losses of a PV plant
CUT_IN, RATED, CUT_OUT = 3.0, 12.0, 25.0   # m/s, generic 2-3 MW onshore turbine
WIND_AVAILABILITY = 0.93      # wake, electrical losses and maintenance
REFRESH_EVERY = 3 * 3600      # seconds
MAX_AGE = 12 * 3600           # forecast older than this is not used


def solar_mwh_per_mw(ghi_wm2: float) -> float:
    """Energy of one hour (MWh) for 1 MW of panels under this irradiance."""
    return max(0.0, ghi_wm2) / 1000.0 * PERFORMANCE_RATIO


def wind_mwh_per_mw(speed_ms: float) -> float:
    """Energy of one hour (MWh) for 1 MW of turbines at this wind speed (generic power curve)."""
    v = speed_ms
    if v < CUT_IN or v >= CUT_OUT:
        cf = 0.0
    elif v >= RATED:
        cf = 1.0
    else:
        cf = (v ** 3 - CUT_IN ** 3) / (RATED ** 3 - CUT_IN ** 3)
    return cf * WIND_AVAILABILITY


def fetch_site(lat: float, lon: float, days: int = FORECAST_DAYS, timeout: float = 12.0) -> dict:
    """One request to Open-Meteo; returns the parsed ``hourly`` block."""
    qs = urlencode({
        "latitude": lat, "longitude": lon,
        "hourly": "shortwave_radiation,wind_speed_100m",
        "wind_speed_unit": "ms", "timezone": "Africa/Casablanca", "forecast_days": days,
    })
    req = Request(f"{API_URL}?{qs}", headers={"User-Agent": "WahaSud/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["hourly"]


def hourly_by_day(hourly: dict, key: str) -> dict:
    """Group an Open-Meteo hourly series by local date; keeps only complete days (24 values)."""
    days: dict = {}
    for stamp, value in zip(hourly["time"], hourly[key]):
        if value is None:
            continue
        days.setdefault(stamp[:10], {})[int(stamp[11:13])] = float(value)
    return {d: [h[i] for i in range(24)] for d, h in days.items() if len(h) == 24}


def build_forecast(solar_sites: list, wind_sites: list, raw_solar: list, raw_wind: list) -> dict:
    """Turn raw hourly data of each site into the structure the engine reads.

    raw_solar / raw_wind: one Open-Meteo ``hourly`` block per site, same order as the sites.
    Returns {iso_date: {"solar": [MWh/MW/day per site], "wind": [...],
                        "hourly": {"solar": [[24 values] per site], "wind": [...]}}}
    Only days that are complete for every site are kept.
    """
    sol = [hourly_by_day(h, "shortwave_radiation") for h in raw_solar]
    win = [hourly_by_day(h, "wind_speed_100m") for h in raw_wind]
    dates = set.intersection(*(set(x) for x in sol + win)) if (sol or win) else set()
    out = {}
    for d in sorted(dates):
        hs = [[solar_mwh_per_mw(v) for v in s[d]] for s in sol]
        hw = [[wind_mwh_per_mw(v) for v in w[d]] for w in win]
        out[d] = {"solar": [sum(h) for h in hs], "wind": [sum(h) for h in hw], "hourly": {"solar": hs, "wind": hw}}
    return out


class WeatherForecast:
    """Keeps the latest forecast in memory and refreshes it in a background thread."""

    def __init__(self, solar_sites: list, wind_sites: list, fetcher=fetch_site):
        self.solar_sites, self.wind_sites, self.fetcher = solar_sites, wind_sites, fetcher
        self._lock = threading.Lock()
        self._data: dict | None = None
        self._updated: float | None = None
        self.last_error: str | None = None

    def refresh(self) -> bool:
        """Fetch every site; all-or-nothing. Returns True when the data was updated."""
        try:
            raw_s = [self.fetcher(s["lat"], s["lon"]) for s in self.solar_sites]
            raw_w = [self.fetcher(s["lat"], s["lon"]) for s in self.wind_sites]
            data = build_forecast(self.solar_sites, self.wind_sites, raw_s, raw_w)
            if not data:
                raise ValueError("no complete day in the weather response")
        except Exception as err:  # network down, bad payload, timeout...
            self.last_error = repr(err)
            sys.stderr.write(f"[weather] refresh failed, keeping previous data: {err!r}\n")
            return False
        with self._lock:
            self._data, self._updated, self.last_error = data, time.time(), None
        return True

    def get(self) -> dict | None:
        """Forecast keyed by ISO date, or None if absent or too old."""
        with self._lock:
            if self._data is None or self._updated is None or time.time() - self._updated > MAX_AGE:
                return None
            return self._data

    def updated_iso(self) -> str | None:
        with self._lock:
            if self._updated is None:
                return None
            return datetime.fromtimestamp(self._updated, timezone.utc).isoformat(timespec="seconds")

    def start(self, every: float = REFRESH_EVERY) -> threading.Thread:
        def loop():
            while True:
                self.refresh()
                time.sleep(every if self.get() is not None else 600)   # retry sooner after a failure

        t = threading.Thread(target=loop, name="weather-refresh", daemon=True)
        t.start()
        return t
