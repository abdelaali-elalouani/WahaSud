import http.client
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import engine  # noqa: E402
import server  # noqa: E402
from db import TZ, Store  # noqa: E402

KEY = "test-key"


def last_24_hours(city_id, dem, leak=0.0, level=None):
    """24 complete hours of readings ending at the previous full hour."""
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    rows = []
    for back in range(24, 0, -1):
        slot = end - timedelta(hours=back)
        p = engine.PROFILE[slot.astimezone(TZ).hour]
        rows.append({"city_id": city_id, "ts": slot.isoformat(), "flow_m3h": dem / 24 * p / engine.PMEAN + leak / 24,
                     "level_pct": level if back == 1 else None})
    return rows


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = server.make_server("127.0.0.1", 0, Store(Path(cls.tmp.name) / "test.db"), api_key=KEY)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.req("DELETE", "/api/readings", key=KEY)

    def req(self, method, path, body=None, key=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-API-Key"] = key
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        if raw:
            return resp.status, data, resp.getheader("Content-Type")
        return resp.status, json.loads(data)

    # ---- read endpoints -------------------------------------------------
    def test_health(self):
        status, body = self.req("GET", "/api/health")
        self.assertEqual((status, body["status"]), (200, "ok"))

    def test_dashboard_shape(self):
        status, d = self.req("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(set(d), {"scenario", "lang", "date", "data_mode", "constants", "forecast", "energy", "water", "sensors", "decisions", "impact"})
        self.assertEqual(len(d["energy"]["days"]), 30)
        self.assertEqual(len(d["water"]["cities"]), 7)
        self.assertEqual(len(d["energy"]["hourly"]["solar"]), 24)

    def test_focused_endpoints(self):
        self.assertIn("available_mwh", self.req("GET", "/api/energy?scenario=faible")[1])
        self.assertIn("cities", self.req("GET", "/api/water")[1])
        self.assertIn("decisions", self.req("GET", "/api/decisions")[1])
        self.assertEqual(len(self.req("GET", "/api/sensors")[1]["sensors"]), 7)

    def test_bad_parameters(self):
        self.assertEqual(self.req("GET", "/api/dashboard?scenario=zzz")[0], 400)
        self.assertEqual(self.req("GET", "/api/dashboard?leak=atlantis")[0], 400)
        self.assertEqual(self.req("GET", "/api/nothing")[0], 404)
        self.assertEqual(self.req("POST", "/api/dashboard", {})[0], 405)

    def test_leak_parameter_adds_a_decision(self):
        d = self.req("GET", "/api/dashboard?leak=tiznit")[1]
        self.assertIn("leak-tiznit", [r["id"] for r in d["decisions"]])

    # ---- static files ---------------------------------------------------
    def test_frontend_is_served(self):
        for path, ctype in (("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")):
            status, body, ct = self.req("GET", path, raw=True)
            self.assertEqual(status, 200, path)
            self.assertTrue(ct.startswith(ctype) or "javascript" in ct, (path, ct))

    def test_path_traversal_is_blocked(self):
        for path in ("/../backend/server.py", "/..%2fbackend/server.py", "/%2e%2e/backend/server.py", "//etc/passwd"):
            status, body, _ = self.req("GET", path, raw=True)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"make_server", body)

    # ---- sensors --------------------------------------------------------
    def test_readings_require_the_api_key(self):
        rows = last_24_hours("tiznit", 18_000)
        self.assertEqual(self.req("POST", "/api/readings", {"readings": rows})[0], 401)
        self.assertEqual(self.req("POST", "/api/readings", {"readings": rows}, key="wrong")[0], 401)
        self.assertEqual(self.req("DELETE", "/api/readings")[0], 401)
        status, body = self.req("POST", "/api/readings", {"readings": rows}, key=KEY)
        self.assertEqual((status, body["accepted"]), (200, 24))

    def test_sensor_readings_switch_a_city_to_real_data_and_detect_a_leak(self):
        rows = last_24_hours("tiznit", 18_000, leak=3_000, level=30)
        self.assertEqual(self.req("POST", "/api/readings", {"readings": rows}, key=KEY)[0], 200)
        d = self.req("GET", "/api/dashboard")[1]
        city = next(c for c in d["water"]["cities"] if c["id"] == "tiznit")
        self.assertEqual(city["data_source"], {"flow": "capteurs", "level": "capteur"})
        self.assertEqual(city["detection"]["kind"], "leak")
        self.assertAlmostEqual(city["leak_m3_day"], 3_000, delta=150)
        self.assertEqual(city["level_pct"], 30)
        self.assertEqual(d["data_mode"], "mixte")
        sensor = next(s for s in d["sensors"] if s["id"] == "tiznit")
        self.assertEqual(sensor["readings_24h"], 24)
        self.assertIn("leak-tiznit", [r["id"] for r in d["decisions"]])
        # other cities stay simulated
        other = next(c for c in d["water"]["cities"] if c["id"] == "agadir")
        self.assertEqual(other["data_source"]["flow"], "simulation")

    def test_partial_readings_keep_the_simulation(self):
        rows = last_24_hours("tiznit", 18_000)[:10]
        self.req("POST", "/api/readings", {"readings": rows}, key=KEY)
        d = self.req("GET", "/api/dashboard")[1]
        city = next(c for c in d["water"]["cities"] if c["id"] == "tiznit")
        self.assertEqual(city["data_source"]["flow"], "simulation")
        self.assertEqual(next(s for s in d["sensors"] if s["id"] == "tiznit")["readings_24h"], 10)

    def test_single_reading_and_overwrite(self):
        r = {"city_id": "dakhla", "flow_m3h": 400.5, "level_pct": 55}
        self.assertEqual(self.req("POST", "/api/readings", r, key=KEY)[1]["accepted"], 1)
        self.assertEqual(self.req("POST", "/api/readings", r, key=KEY)[1]["accepted"], 1)  # same hour: overwritten

    def test_invalid_readings_are_rejected(self):
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        bad = [
            {"city_id": "atlantis", "flow_m3h": 1},
            {"city_id": "tiznit", "flow_m3h": -5},
            {"city_id": "tiznit", "flow_m3h": "12"},
            {"city_id": "tiznit", "flow_m3h": True},
            {"city_id": "tiznit", "flow_m3h": 1, "level_pct": 140},
            {"city_id": "tiznit", "flow_m3h": 1, "ts": "yesterday"},
            {"city_id": "tiznit", "flow_m3h": 1, "ts": future},
            {"city_id": "tiznit", "flow_m3h": 1, "ts": old},
        ]
        for item in bad:
            self.assertEqual(self.req("POST", "/api/readings", item, key=KEY)[0], 400, item)
        self.assertEqual(self.req("POST", "/api/readings", {"readings": []}, key=KEY)[0], 400)
        self.assertEqual(self.req("POST", "/api/readings", {"readings": "x"}, key=KEY)[0], 400)

    def test_malformed_json_and_empty_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/readings", b"{not json", {"X-API-Key": KEY, "Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()
        self.assertEqual(self.req("POST", "/api/readings", None, key=KEY)[0], 400)

    # ---- decisions ------------------------------------------------------
    def test_decision_journal(self):
        rec_id = "leak-guelmim"
        status, body = self.req("POST", "/api/decisions", {"id": rec_id, "status": "valide"})
        self.assertEqual((status, body["status"]), (200, "valide"))
        d = self.req("GET", "/api/dashboard")[1]
        self.assertEqual(next(r for r in d["decisions"] if r["id"] == rec_id)["status"], "valide")
        hist = self.req("GET", "/api/decisions/history?limit=5")[1]["history"]
        self.assertEqual(hist[0]["rec_id"], rec_id)
        self.assertEqual(hist[0]["status"], "valide")
        self.assertIn("Guelmim", hist[0]["title"])       # title written by the server
        # cancelling clears the status but stays in the journal
        self.req("POST", "/api/decisions", {"id": rec_id, "status": "annule"})
        d = self.req("GET", "/api/dashboard")[1]
        self.assertIsNone(next(r for r in d["decisions"] if r["id"] == rec_id)["status"])
        self.assertEqual(self.req("GET", "/api/decisions/history")[1]["history"][0]["status"], "annule")

    def test_decision_validation(self):
        self.assertEqual(self.req("POST", "/api/decisions", {"id": "leak-guelmim", "status": "nope"})[0], 400)
        self.assertEqual(self.req("POST", "/api/decisions", {"id": "does-not-exist", "status": "valide"})[0], 404)
        self.assertEqual(self.req("POST", "/api/decisions", {"id": "leak-tiznit", "status": "valide"})[0], 404)  # no such leak now
        self.assertEqual(self.req("POST", "/api/decisions", {"id": "leak-tiznit", "status": "valide", "leak": "tiznit"})[0], 200)

    def test_sql_injection_attempts_are_harmless(self):
        self.assertEqual(self.req("GET", "/api/decisions/history?limit=" + quote("1;DROP TABLE readings"))[0], 400)
        self.assertEqual(self.req("DELETE", "/api/readings?city=" + quote("x'; DROP TABLE readings;--"), key=KEY)[0], 400)
        self.assertEqual(self.req("GET", "/api/sensors")[0], 200)


class NewEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = server.make_server("127.0.0.1", 0, Store(Path(cls.tmp.name) / "t.db"), api_key=None,
                                     assumptions={"grid_price_mad_mwh": 1000.0, "co2_t_per_mwh": 0.5})
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_language_parameter(self):
        _, fr = self.get("/api/dashboard")
        _, ar = self.get("/api/dashboard?lang=ar")
        self.assertEqual(fr["lang"], "fr")
        self.assertEqual(ar["lang"], "ar")
        self.assertNotEqual(fr["water"]["cities"][0]["name"], ar["water"]["cities"][0]["name"])
        self.assertEqual(self.get("/api/dashboard?lang=en")[0], 400)

    def test_dashboard_reports_the_forecast_origin(self):
        _, d = self.get("/api/dashboard")
        self.assertEqual(d["forecast"]["source"], "simulation")
        self.assertIsNone(d["forecast"]["updated"])

    def test_impact_uses_configured_assumptions(self):
        status, i = self.get("/api/impact?leak=tiznit")
        self.assertEqual(status, 200)
        self.assertEqual(i["assumptions"]["grid_price_mad_mwh"], 1000.0)
        self.assertAlmostEqual(i["leak_value_mad_day"], i["leak_energy_mwh_day"] * 1000.0, delta=1)

    def test_whatif(self):
        status, r = self.get("/api/whatif?site=wind-dakhla&add_mw=10&scenario=faible")
        self.assertEqual(status, 200)
        self.assertGreater(r["delta"]["coverage_pct"], 0)
        self.assertEqual(r["site"]["id"], "wind-dakhla")

    def test_whatif_validation(self):
        for q in ("", "?site=nope&add_mw=5", "?site=wind-dakhla", "?site=wind-dakhla&add_mw=abc",
                  "?site=wind-dakhla&add_mw=-3", "?site=wind-dakhla&add_mw=99999", "?site=wind-dakhla&add_mw=nan"):
            self.assertEqual(self.get("/api/whatif" + q)[0], 400, q)

    def test_dashboard_sites_carry_ids_and_coordinates(self):
        _, d = self.get("/api/dashboard")
        for s in d["energy"]["sites"]:
            self.assertIn(s["id"], engine.SITE_IDS)
            self.assertTrue(-20 < s["lon"] < -5 and 20 < s["lat"] < 35)
        for c in d["water"]["cities"]:
            self.assertIn("lat", c)

    def test_decision_title_follows_the_language(self):
        _, d = self.get("/api/decisions?lang=ar&leak=tiznit")
        rec = next(r for r in d["decisions"] if r["id"] == "leak-tiznit")
        status, _ = self.post("/api/decisions", {"id": rec["id"], "status": "valide", "leak": "tiznit", "lang": "ar"})
        self.assertEqual(status, 200)
        _, h = self.get("/api/decisions/history?limit=1")
        self.assertEqual(h["history"][0]["title"], rec["title"])


class NoKeyTests(unittest.TestCase):
    def test_without_configured_key_writes_are_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            srv = server.make_server("127.0.0.1", 0, Store(Path(tmp) / "t.db"), api_key=None)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
                conn.request("POST", "/api/readings", json.dumps({"city_id": "tiznit", "flow_m3h": 10}),
                             {"Content-Type": "application/json"})
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
