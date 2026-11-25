import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import engine  # noqa: E402

TODAY = date(2026, 11, 23)


class LanguageTests(unittest.TestCase):
    def test_same_decisions_in_both_languages(self):
        fr = engine.compute("faible", None, None, TODAY, lang="fr")
        ar = engine.compute("faible", None, None, TODAY, lang="ar")
        self.assertEqual([d["id"] for d in fr["decisions"]], [d["id"] for d in ar["decisions"]])
        self.assertEqual([d["urgency"] for d in fr["decisions"]], [d["urgency"] for d in ar["decisions"]])

    def test_arabic_text_has_no_unfilled_placeholder_and_is_arabic(self):
        ar = engine.compute("faible", "tiznit", None, TODAY, lang="ar")
        for d in ar["decisions"]:
            text = " ".join([d["title"], d["action"], *d["why"], *(x for pair in d["impact"] for x in pair),
                             *(x for pair in d["evidence"] for x in pair)])
            self.assertNotIn("{", text, d["id"])
            self.assertTrue(any("\u0600" <= ch <= "\u06FF" for ch in d["title"]), d["id"])
        self.assertEqual({c["name"] for c in ar["water"]["cities"]} & {c["n"] for c in engine.CITIES}, set())

    def test_french_has_no_unfilled_placeholder(self):
        fr = engine.compute("faible", "tiznit", None, TODAY)
        for d in fr["decisions"]:
            self.assertNotIn("{", " ".join([d["title"], d["action"], *d["why"]]), d["id"])

    def test_both_languages_have_the_same_text_keys(self):
        self.assertEqual(set(engine.TXT["fr"]), set(engine.TXT["ar"]))

    def test_unknown_language_is_rejected(self):
        with self.assertRaises(ValueError):
            engine.compute("prevu", None, None, TODAY, lang="en")


class EvidenceTests(unittest.TestCase):
    def test_every_decision_carries_its_data(self):
        m = engine.compute("faible", "tiznit", None, TODAY)
        self.assertTrue(m["decisions"])
        for d in m["decisions"]:
            self.assertTrue(d["evidence"], d["id"])
            self.assertIn("energy", d["sources"])
            self.assertIn("city", d)

    def test_city_decisions_point_to_their_tank(self):
        m = engine.compute("prevu", "tiznit", None, TODAY)
        leak = next(d for d in m["decisions"] if d["id"] == "leak-tiznit")
        self.assertEqual(leak["city"], "tiznit")
        self.assertGreater(leak["value_mad_day"], 0)
        self.assertEqual(leak["sources"]["flow"], "simulation")


class ImpactTests(unittest.TestCase):
    def test_leak_value_follows_the_assumptions(self):
        a = engine.compute("prevu", "tiznit", None, TODAY, assumptions={"grid_price_mad_mwh": 1000, "co2_t_per_mwh": 0.5})
        b = engine.compute("prevu", "tiznit", None, TODAY, assumptions={"grid_price_mad_mwh": 2000, "co2_t_per_mwh": 0.5})
        self.assertAlmostEqual(b["impact"]["leak_value_mad_day"], 2 * a["impact"]["leak_value_mad_day"])
        self.assertAlmostEqual(a["impact"]["leak_energy_mwh_day"], a["impact"]["leak_water_m3_day"] * engine.KWH_PER_M3 / 1000)
        self.assertAlmostEqual(a["impact"]["leak_co2_t_day"], a["impact"]["leak_energy_mwh_day"] * 0.5)

    def test_total_is_the_sum_of_its_parts(self):
        i = engine.compute("prevu", "guelmim", None, TODAY)["impact"]
        parts = i["renewable_value_mad_day"] + i["leak_value_mad_day"] + i["stop_value_mad_day"]
        self.assertAlmostEqual(i["total_value_mad_day"], parts)
        self.assertAlmostEqual(i["total_value_mad_year"], parts * 365, delta=0.01)

    def test_renewable_use_never_exceeds_production(self):
        for sc in engine.SCENARIOS:
            m = engine.compute(sc, None, None, TODAY)
            self.assertLessEqual(m["impact"]["renewable_used_mwh"], m["energy"]["available_mwh"] + 1e-9)

    def test_no_division_by_zero_when_every_tank_is_full(self):
        sensors = {c["id"]: {"q": None, "level": 99.0, "readings": 0, "last": None} for c in engine.CITIES}
        m = engine.compute("prevu", None, sensors, TODAY)
        self.assertEqual(m["water"]["total_need_m3_day"], 0)
        self.assertTrue(all(c["tank_full"] for c in m["water"]["cities"]))


class WhatIfTests(unittest.TestCase):
    def test_more_wind_improves_a_weak_wind_day(self):
        r = engine.whatif("wind-dakhla", 10, "faible", today=TODAY)
        self.assertGreater(r["delta"]["coverage_pct"], 0)
        self.assertGreaterEqual(r["delta"]["allocated_m3_day"], 0)
        self.assertLessEqual(r["delta"]["deficit_days"], 0)
        self.assertEqual(r["site"]["add_mw"], 10)

    def test_zero_extra_changes_nothing(self):
        r = engine.whatif("solar-guelmim", 0, "prevu", today=TODAY)
        self.assertTrue(all(abs(v) < 1e-9 for v in r["delta"].values()))

    def test_added_solar_power_scales_that_site_only(self):
        base = engine.compute("prevu", None, None, TODAY)["energy"]["sites"]
        more = engine.compute("prevu", None, None, TODAY, extra={"solar-guelmim": 10})["energy"]["sites"]
        for a, b in zip(base, more):
            if a["id"] == "solar-guelmim":
                self.assertAlmostEqual(b["mwh"], a["mwh"] * (a["mw"] + 10) / a["mw"])
            else:
                self.assertAlmostEqual(a["mwh"], b["mwh"])

    def test_unknown_site(self):
        with self.assertRaises(ValueError):
            engine.whatif("nuclear-plant", 10, today=TODAY)


if __name__ == "__main__":
    unittest.main()
