import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import engine  # noqa: E402
import weather  # noqa: E402

TODAY = date(2026, 11, 23)


def fake_hourly(ghi_peak=900.0, wind=9.0, days=16, start=TODAY):
    """Open-Meteo style ``hourly`` block: a sun bell curve and a constant wind."""
    times, ghi, ws = [], [], []
    for d in range(days):
        day = (start + timedelta(days=d)).isoformat()
        for h in range(24):
            times.append(f"{day}T{h:02d}:00")
            ghi.append(max(0.0, ghi_peak * (1 - ((h - 12.5) / 6) ** 2)) if 7 <= h <= 18 else 0.0)
            ws.append(wind)
    return {"time": times, "shortwave_radiation": ghi, "wind_speed_100m": ws}


def fake_fetcher(lat, lon, **kw):
    return fake_hourly()


class ConversionTests(unittest.TestCase):
    def test_solar_follows_irradiance(self):
        self.assertAlmostEqual(weather.solar_mwh_per_mw(1000), weather.PERFORMANCE_RATIO)
        self.assertEqual(weather.solar_mwh_per_mw(0), 0)
        self.assertEqual(weather.solar_mwh_per_mw(-5), 0)

    def test_wind_power_curve(self):
        self.assertEqual(weather.wind_mwh_per_mw(2.9), 0)                      # below cut-in
        self.assertAlmostEqual(weather.wind_mwh_per_mw(12), weather.WIND_AVAILABILITY)   # rated
        self.assertAlmostEqual(weather.wind_mwh_per_mw(20), weather.WIND_AVAILABILITY)
        self.assertEqual(weather.wind_mwh_per_mw(25), 0)                       # cut-out
        self.assertLess(weather.wind_mwh_per_mw(8), weather.wind_mwh_per_mw(10))   # grows with the wind

    def test_incomplete_days_are_dropped(self):
        h = fake_hourly(days=2)
        for k in h:
            h[k] = h[k][:-5]                      # the last day loses 5 hours
        self.assertEqual(len(weather.hourly_by_day(h, "wind_speed_100m")), 1)

    def test_build_forecast_shape(self):
        n_s, n_w = len(engine.SOLAR_SITES), len(engine.WIND_SITES)
        fc = weather.build_forecast(engine.SOLAR_SITES, engine.WIND_SITES, [fake_hourly()] * n_s, [fake_hourly()] * n_w)
        self.assertEqual(len(fc), 16)
        day = fc[TODAY.isoformat()]
        self.assertEqual(len(day["solar"]), n_s)
        self.assertEqual(len(day["hourly"]["wind"][0]), 24)
        self.assertAlmostEqual(sum(day["hourly"]["solar"][0]), day["solar"][0])


class ProviderTests(unittest.TestCase):
    def test_refresh_and_get(self):
        wf = weather.WeatherForecast(engine.SOLAR_SITES, engine.WIND_SITES, fetcher=fake_fetcher)
        self.assertIsNone(wf.get())
        self.assertTrue(wf.refresh())
        self.assertIn(TODAY.isoformat(), wf.get())
        self.assertIsNotNone(wf.updated_iso())

    def test_failure_keeps_previous_data(self):
        wf = weather.WeatherForecast(engine.SOLAR_SITES, engine.WIND_SITES, fetcher=fake_fetcher)
        wf.refresh()

        def broken(lat, lon, **kw):
            raise OSError("network down")

        wf.fetcher = broken
        self.assertFalse(wf.refresh())
        self.assertIsNotNone(wf.get())
        self.assertIn("network down", wf.last_error)

    def test_first_failure_means_no_forecast(self):
        def broken(lat, lon, **kw):
            raise OSError("offline")

        wf = weather.WeatherForecast(engine.SOLAR_SITES, engine.WIND_SITES, fetcher=broken)
        self.assertFalse(wf.refresh())
        self.assertIsNone(wf.get())


class EngineWithWeatherTests(unittest.TestCase):
    def setUp(self):
        n_s, n_w = len(engine.SOLAR_SITES), len(engine.WIND_SITES)
        # tomorrow is TODAY + 1: the fake data starts there
        self.fc = weather.build_forecast(engine.SOLAR_SITES, engine.WIND_SITES,
                                         [fake_hourly(start=TODAY + timedelta(days=1))] * n_s,
                                         [fake_hourly(start=TODAY + timedelta(days=1))] * n_w)

    def test_real_days_then_simulation(self):
        m = engine.compute("prevu", None, None, TODAY, forecast=self.fc)
        self.assertEqual(m["forecast"], {"source": "meteo", "real_days": 16, "horizon_days": 30})
        sources = [d["source"] for d in m["energy"]["days"]]
        self.assertEqual(sources[:16], ["meteo"] * 16)
        self.assertEqual(sources[16:], ["simulation"] * 14)

    def test_without_forecast_nothing_changes(self):
        a = engine.compute("prevu", None, None, TODAY)
        self.assertEqual(a["forecast"]["source"], "simulation")
        self.assertEqual(a, engine.compute("prevu", None, None, TODAY, forecast=None))

    def test_hourly_chart_matches_daily_total(self):
        m = engine.compute("prevu", None, None, TODAY, forecast=self.fc)
        h = m["energy"]["hourly"]
        self.assertAlmostEqual(sum(h["solar"]) + sum(h["wind"]), m["energy"]["available_mwh"], places=6)

    def test_wind_scenarios_still_apply_to_real_wind(self):
        weak = engine.compute("faible", None, None, TODAY, forecast=self.fc)["energy"]["wind_mwh"]
        normal = engine.compute("prevu", None, None, TODAY, forecast=self.fc)["energy"]["wind_mwh"]
        strong = engine.compute("surplus", None, None, TODAY, forecast=self.fc)["energy"]["wind_mwh"]
        self.assertLess(weak, normal)
        self.assertLessEqual(normal, strong)

    def test_solar_matches_hand_calculation(self):
        m = engine.compute("prevu", None, None, TODAY, forecast=self.fc)
        per_mw = sum(weather.solar_mwh_per_mw(v) for v in fake_hourly()["shortwave_radiation"][:24])
        expected = per_mw * sum(s["mw"] for s in engine.SOLAR_SITES)
        self.assertAlmostEqual(m["energy"]["solar_mwh"], expected, places=6)


if __name__ == "__main__":
    unittest.main()
