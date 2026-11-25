import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import engine  # noqa: E402

TODAY = date(2026, 11, 23)


def normal_flow(dem=18_000, leak=0.0):
    return [dem / 24 * p / engine.PMEAN + leak / 24 for p in engine.PROFILE]


class DeterminismTests(unittest.TestCase):
    def test_seeded_generator_is_deterministic_and_in_range(self):
        a, b = engine.seeded(42), engine.seeded(42)
        xs = [a() for _ in range(100)]
        self.assertEqual(xs, [b() for _ in range(100)])
        self.assertTrue(all(0 <= x < 1 for x in xs))

    def test_same_inputs_same_output(self):
        self.assertEqual(engine.compute("prevu", None, None, TODAY), engine.compute("prevu", None, None, TODAY))

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaises(ValueError):
            engine.compute("nope")


class DetectionTests(unittest.TestCase):
    def test_normal_flow(self):
        self.assertEqual(engine.detect(normal_flow())["kind"], "ok")

    def test_leak_is_detected_and_estimated(self):
        det = engine.detect(normal_flow(leak=3000))
        self.assertEqual(det["kind"], "leak")
        self.assertAlmostEqual(det["loss"], 3000, delta=60)

    def test_flat_flow_is_a_frozen_sensor(self):
        self.assertEqual(engine.detect([750.0] * 24)["kind"], "frozen")

    def test_all_zero_is_not_a_crash(self):
        self.assertEqual(engine.detect([0.0] * 24)["kind"], "frozen")


class ModelTests(unittest.TestCase):
    def test_allocation_respects_energy_and_demand(self):
        for sc in engine.SCENARIOS:
            m = engine.compute(sc, None, None, TODAY)
            w, en = m["water"], m["energy"]
            self.assertLessEqual(w["allocated_m3_day"], en["producible_m3_day"] + len(engine.CITIES))
            for c in w["cities"]:
                self.assertLessEqual(c["allocated_m3_day"], c["need_m3_day"])
            self.assertEqual(w["unmet_m3_day"], w["total_need_m3_day"] - w["allocated_m3_day"])

    def test_ranks_follow_scores(self):
        m = engine.compute("prevu", None, None, TODAY)
        by = {c["id"]: c for c in m["water"]["cities"]}
        scores = [by[i]["need_score"] for i in m["water"]["order"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual([by[i]["priority_rank"] for i in m["water"]["order"]], list(range(1, 8)))

    def test_weak_wind_gives_less_energy_than_strong_wind(self):
        low = engine.compute("faible", None, None, TODAY)["energy"]["available_mwh"]
        high = engine.compute("surplus", None, None, TODAY)["energy"]["available_mwh"]
        self.assertLess(low, high)

    def test_simulated_leak_creates_a_decision(self):
        base = engine.compute("prevu", None, None, TODAY)
        self.assertNotIn("leak-tiznit", [r["id"] for r in base["decisions"]])
        leaky = engine.compute("prevu", "tiznit", None, TODAY)
        self.assertIn("leak-tiznit", [r["id"] for r in leaky["decisions"]])

    def test_known_situation_of_the_simulation(self):
        m = engine.compute("prevu", None, None, TODAY)
        ids = [r["id"] for r in m["decisions"]]
        self.assertIn("leak-guelmim", ids)       # built-in leak
        self.assertIn("sensor-boujdour", ids)    # built-in frozen sensor
        self.assertIn("stop-dakhla", ids)        # tank above 90 %
        self.assertEqual(m["data_mode"], "simulation")

    def test_decisions_are_sorted_by_urgency(self):
        u = [r["urgency"] for r in engine.compute("faible", None, None, TODAY)["decisions"]]
        self.assertEqual(u, sorted(u))

    def test_french_dates(self):
        self.assertEqual(engine.date_long(date(2026, 11, 24)), "mardi 24 novembre")
        self.assertEqual(engine.date_short(date(2026, 12, 5)), "5 déc")


class SensorInputTests(unittest.TestCase):
    def sensors(self, city, q, level=None):
        return {city: {"q": q, "level": level, "readings": 24, "last": "2026-11-23T10:00:00Z"}}

    def test_real_readings_replace_the_simulation(self):
        m = engine.compute("prevu", None, self.sensors("tiznit", normal_flow(18_000)), TODAY)
        c = next(c for c in m["water"]["cities"] if c["id"] == "tiznit")
        self.assertEqual(c["data_source"]["flow"], "capteurs")
        self.assertEqual(c["detection"]["kind"], "ok")
        self.assertAlmostEqual(c["demand_m3_day"], 18_000, delta=1)
        self.assertEqual(m["data_mode"], "mixte")

    def test_real_leak_is_detected_from_readings(self):
        m = engine.compute("prevu", None, self.sensors("tiznit", normal_flow(18_000, leak=3_000)), TODAY)
        c = next(c for c in m["water"]["cities"] if c["id"] == "tiznit")
        self.assertEqual(c["detection"]["kind"], "leak")
        self.assertAlmostEqual(c["leak_m3_day"], 3_000, delta=100)
        self.assertAlmostEqual(c["demand_m3_day"], 18_000, delta=100)   # demand excludes the leak
        self.assertIn("leak-tiznit", [r["id"] for r in m["decisions"]])

    def test_measured_level_overrides_simulated_level(self):
        m = engine.compute("prevu", None, self.sensors("tiznit", None, level=12.0), TODAY)
        c = next(c for c in m["water"]["cities"] if c["id"] == "tiznit")
        self.assertEqual(c["level_pct"], 12.0)
        self.assertEqual(c["tank_status"], "crit")
        self.assertEqual(c["data_source"], {"flow": "simulation", "level": "capteur"})

    def test_incomplete_readings_fall_back_to_simulation(self):
        sensors = {"tiznit": {"q": None, "level": None, "readings": 10, "last": "2026-11-23T10:00:00Z"}}
        m = engine.compute("prevu", None, sensors, TODAY)
        c = next(c for c in m["water"]["cities"] if c["id"] == "tiznit")
        self.assertEqual(c["data_source"]["flow"], "simulation")
        s = next(s for s in m["sensors"] if s["id"] == "tiznit")
        self.assertEqual(s["readings_24h"], 10)

    def test_all_cities_on_sensors_gives_capteurs_mode(self):
        sensors = {c["id"]: {"q": normal_flow(c["dem"]), "level": None, "readings": 24, "last": None} for c in engine.CITIES}
        self.assertEqual(engine.compute("prevu", None, sensors, TODAY)["data_mode"], "capteurs")


if __name__ == "__main__":
    unittest.main()
