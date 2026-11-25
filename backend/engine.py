"""WahaSud calculation engine.

Pure functions, no I/O: given a scenario, an optional simulated leak and
(optionally) real sensor readings, it computes the energy forecast, the water
situation of each city, the allocation of tomorrow's production and the list of
recommended decisions.

Every number that is not measured is simulated with a seeded generator, so the
same inputs always give the same outputs.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

# --------------------------------------------------------------------------
# Constants (see "Hypothèses du modèle" in the web app)
# --------------------------------------------------------------------------
KWH_PER_M3 = 4.3            # 3.6 kWh desalination + 0.7 kWh transfer, per m3
PLANT_CAP = 160_000         # simulated desalination capacity, m3/day
DAYS = 30                   # forecast horizon
FULL_STOP_LEVEL = 90        # stop pumping to a city above this tank level (%)
REFILL_TARGET = 70          # target tank level (%) ...
REFILL_DAYS = 7             # ... reached in this many days

# id, n: name (fr), na: name (ar), mw: installed power, lat/lon: location (weather request and map)
SOLAR_SITES = [
    {"id": "solar-agadir", "n": "Parc solaire Agadir-Chtouka", "na": "حديقة أكادير-اشتوكة الشمسية", "mw": 12, "lat": 30.10, "lon": -9.30},
    {"id": "solar-guelmim", "n": "Centrale solaire Guelmim", "na": "محطة كلميم الشمسية", "mw": 10, "lat": 29.00, "lon": -10.10},
    {"id": "solar-laayoune", "n": "Parc solaire Laâyoune", "na": "حديقة العيون الشمسية", "mw": 18, "lat": 27.05, "lon": -13.05},
]
WIND_SITES = [
    {"id": "wind-tarfaya", "n": "Parc éolien Tarfaya", "na": "مزرعة طرفاية الريحية", "mw": 12, "lat": 27.94, "lon": -12.90},
    {"id": "wind-boujdour", "n": "Parc éolien Boujdour", "na": "مزرعة بوجدور الريحية", "mw": 8, "lat": 26.20, "lon": -14.40},
    {"id": "wind-dakhla", "n": "Parc éolien Dakhla", "na": "مزرعة الداخلة الريحية", "mw": 10, "lat": 23.80, "lon": -15.80},
]
SITE_IDS = [s["id"] for s in SOLAR_SITES + WIND_SITES]
WIND_CF_MAX = 0.95          # a wind farm never exceeds this share of its nominal power over a day

# Assumptions behind the impact figures (override with the server's environment variables)
GRID_PRICE_MAD_MWH = 1100.0   # cost of one MWh of back-up grid electricity, in dirhams
CO2_T_PER_MWH = 0.65          # tonnes of CO2 per MWh of the national grid mix
SCENARIOS = {"prevu": 1.0, "faible": 0.55, "surplus": 1.4}  # wind multiplier

# Hourly demand profile (index = hour of day, 0..23)
PROFILE = [.42, .36, .32, .31, .34, .5, .85, 1.15, 1.25, 1.15, 1.05, 1, 1.05, 1,
           .95, .95, 1, 1.1, 1.25, 1.3, 1.2, 1, .75, .55]
PMEAN = sum(PROFILE) / 24
# Share of the average flow expected between 1 am and 5 am when there is no leak
NIGHT = (PROFILE[1] + PROFILE[2] + PROFILE[3] + PROFILE[4]) / 4 / PMEAN

# pop: thousands of inhabitants, cap: tank capacity (m3), dem: daily demand (m3),
# conv: daily supply from conventional sources (m3), lvl: tank level (%)
CITIES = [
    {"id": "agadir", "n": "Agadir", "pop": 700, "cap": 700_000, "dem": 120_000, "conv": 85_000, "lvl": 68, "seed": 11},
    {"id": "tiznit", "n": "Tiznit", "pop": 75, "cap": 100_000, "dem": 18_000, "conv": 11_000, "lvl": 39, "seed": 12},
    {"id": "guelmim", "n": "Guelmim", "pop": 120, "cap": 80_000, "dem": 14_000, "conv": 6_000, "lvl": 24, "seed": 13, "leak": 2600},
    {"id": "tantan", "n": "Tan-Tan", "pop": 80, "cap": 50_000, "dem": 9_000, "conv": 2_000, "lvl": 52, "seed": 14},
    {"id": "laayoune", "n": "Laâyoune", "pop": 220, "cap": 130_000, "dem": 24_000, "conv": 3_000, "lvl": 46, "seed": 15},
    {"id": "boujdour", "n": "Boujdour", "pop": 60, "cap": 20_000, "dem": 3_500, "conv": 0, "lvl": 17, "seed": 16, "frozen": True},
    {"id": "dakhla", "n": "Dakhla", "pop": 120, "cap": 60_000, "dem": 11_000, "conv": 500, "lvl": 94, "seed": 17},
]
# name (ar), latitude, longitude of each city
_GEO = {
    "agadir": ("أكادير", 30.43, -9.60), "tiznit": ("تيزنيت", 29.70, -9.73), "guelmim": ("كلميم", 28.99, -10.06),
    "tantan": ("طانطان", 28.44, -11.10), "laayoune": ("العيون", 27.15, -13.20), "boujdour": ("بوجدور", 26.13, -14.48),
    "dakhla": ("الداخلة", 23.71, -15.93),
}
for _c in CITIES:
    _c["na"], _c["lat"], _c["lon"] = _GEO[_c["id"]]
CITY_IDS = [c["id"] for c in CITIES]
LANGS = ("fr", "ar")

WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "ماي", "يونيو", "يوليوز", "غشت", "شتنبر", "أكتوبر", "نونبر", "دجنبر"]
MONTHS_SHORT = ["janv", "févr", "mars", "avr", "mai", "juin", "juil", "août",
                "sept", "oct", "nov", "déc"]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def clamp(v: float, a: float, b: float) -> float:
    return min(b, max(a, v))


def js_round(x: float) -> int:
    """Round half up, like JavaScript's Math.round (Python rounds half to even)."""
    return int(math.floor(x + 0.5))


def nb(n: float) -> str:
    """French-style integer with non-breaking space as thousands separator."""
    return f"{js_round(n):,}".replace(",", " ")


def d1(n: float) -> str:
    """One decimal, French decimal comma."""
    return f"{n:.1f}".replace(".", ",")


def date_long(d: date, lang: str = "fr") -> str:
    if lang == "ar":
        return f"{WEEKDAYS_AR[d.weekday()]} {d.day} {MONTHS_AR[d.month - 1]}"
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def date_short(d: date, lang: str = "fr") -> str:
    if lang == "ar":
        return f"{d.day} {MONTHS_AR[d.month - 1]}"
    return f"{d.day} {MONTHS_SHORT[d.month - 1]}"


def _imul(a: int, b: int) -> int:
    return (a * b) & 0xFFFFFFFF


def seeded(seed: int):
    """mulberry32 pseudo random generator: returns a function giving floats in [0, 1)."""
    state = [seed & 0xFFFFFFFF]

    def rnd() -> float:
        state[0] = (state[0] + 0x6D2B79F5) & 0xFFFFFFFF
        a = state[0]
        t = _imul(a ^ (a >> 15), 1 | a)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return rnd


# --------------------------------------------------------------------------
# Energy forecast
# --------------------------------------------------------------------------
def _build_base():
    r = seeded(2026)
    base = []
    for d in range(DAYS):
        sf = clamp(.78 + .12 * math.sin(d * .9 + .4) + (r() - .5) * .16, .5, .97)
        wf = clamp(.42 + .22 * math.sin(d * .5 + 3.2) + (r() - .5) * .14, .1, .75)
        sj = [1 + (r() - .5) * .1 for _ in SOLAR_SITES]
        wj = [1 + (r() - .5) * .3 for _ in WIND_SITES]
        base.append({"sf": sf, "wf": wf, "sj": sj, "wj": wj})
    return base


BASE = _build_base()


def energy_day(d: int, scenario: str, forecast: dict | None = None, extra: dict | None = None) -> dict:
    """Production (MWh) of each site for day offset d (0 = tomorrow).

    forecast: {offset: {"solar": [MWh per MW per day, per site], "wind": [...], "hourly": {...}}}
              from weather.py. Days it does not cover use the seeded simulation.
    extra:    {site_id: additional MW}, used by the what-if planner.
    """
    wm = SCENARIOS[scenario]
    ex = extra or {}
    mw_s = [s["mw"] + ex.get(s["id"], 0) for s in SOLAR_SITES]
    mw_w = [s["mw"] + ex.get(s["id"], 0) for s in WIND_SITES]
    fd = (forecast or {}).get(d)
    if fd:
        solar = [mw_s[i] * fd["solar"][i] for i in range(len(SOLAR_SITES))]
        # the scenario multiplies the wind; a day never goes above WIND_CF_MAX of nominal power
        fw = [wm if per <= 0 else min(wm, WIND_CF_MAX * 24 / per) for per in fd["wind"]]
        wind = [mw_w[i] * fd["wind"][i] * fw[i] for i in range(len(WIND_SITES))]
        out = {"solar": solar, "wind": wind, "s": sum(solar), "w": sum(wind), "source": "meteo"}
        if "hourly" in fd:
            hs, hw = fd["hourly"]["solar"], fd["hourly"]["wind"]
            out["hs"] = [sum(mw_s[i] * hs[i][h] for i in range(len(mw_s))) for h in range(24)]
            out["hw"] = [sum(mw_w[i] * hw[i][h] * fw[i] for i in range(len(mw_w))) for h in range(24)]
        return out
    b = BASE[d]
    solar = [mw_s[i] * 5.6 * b["sf"] * b["sj"][i] for i in range(len(SOLAR_SITES))]
    wind = [mw_w[i] * 24 * clamp(b["wf"] * b["wj"][i] * wm, .02, .9) for i in range(len(WIND_SITES))]
    return {"solar": solar, "wind": wind, "s": sum(solar), "w": sum(wind), "source": "simulation"}


def hourly(e: dict) -> dict:
    """Spread a day's production over 24 hours (real hourly forecast when available, else sun + wind curves)."""
    if "hs" in e:
        return {"solar": e["hs"], "wind": e["hw"]}
    sh = [math.sin(math.pi * (h - 6.5) / 12) ** 1.3 if 7 <= h <= 18 else 0.0 for h in range(24)]
    ss = sum(sh)
    r = seeded(77)
    wh = [1 + .35 * math.sin(2 * math.pi * (h - 15) / 24) + (r() - .5) * .16 for h in range(24)]
    ws = sum(wh)
    return {"solar": [v / ss * e["s"] for v in sh], "wind": [v / ws * e["w"] for v in wh]}


# --------------------------------------------------------------------------
# Water: flow series, leak detection
# --------------------------------------------------------------------------
def simulated_series(c: dict, leak: float, frozen: bool) -> list:
    """24 hourly outflows (m3/h) of a city, simulated."""
    r = seeded(c["seed"])
    if frozen:
        return [c["dem"] / 24 for _ in PROFILE]
    return [(c["dem"] / 24) * (p / PMEAN) * (1 + (r() - .5) * .06) + leak / 24 for p in PROFILE]


def detect(q: list) -> dict:
    """Detect a leak (high night flow) or a frozen sensor (flat flow).

    q holds 24 hourly flows indexed by hour of day. The night flow (1 am to 5 am)
    is compared with the share expected without leak (NIGHT).
    """
    total = sum(q)
    mean = total / 24
    if mean <= 0:
        return {"kind": "frozen", "ratio": 0.0, "loss": 0.0}
    night = (q[1] + q[2] + q[3] + q[4]) / 4
    ratio = night / mean
    sd = math.sqrt(sum((v - mean) ** 2 for v in q) / 24)
    if sd / mean < .02:
        return {"kind": "frozen", "ratio": ratio, "loss": 0.0}
    loss = max(0.0, total * (ratio - NIGHT) / (1 - NIGHT))
    return {"kind": "leak" if loss / total > .05 else "ok", "ratio": ratio, "loss": loss}


# --------------------------------------------------------------------------
# Texts of the recommendations (French is the reference, Arabic follows)
# {m3d} {mwh} {mwhd} {mad} {madd} are unit placeholders filled from the same table.
# --------------------------------------------------------------------------
TXT = {
    "fr": {
        "m3d": "m³/j", "m3": "m³", "mwh": "MWh", "mwhd": "MWh/j", "mad": "MAD", "madd": "MAD/j", "days": "jours",
        "none": "aucune", "normal": "normal", "ok_rest": "rétablie",
        "e_title": "Énergie insuffisante demain : {unmet} {m3d} ne seront pas produits",
        "e_why1": "Prévision de {avail} {mwh} pour {needed} {mwh} nécessaires (couverture {cov} %)",
        "e_served": "Servies : {x}", "e_part": " · partielles : {x}", "e_defer": " · différées : {x}",
        "e_action": "Activer un appoint réseau de {gap} {mwh}, ou concentrer la production sur les villes prioritaires et reporter les autres.",
        "e_i1": "Eau non produite", "e_i2": "Énergie manquante", "e_i3": "Coût de l'appoint réseau",
        "e_ev1": "Couverture demain", "e_ev2": "Villes servies en totalité", "e_ev3": "Origine de la prévision",
        "src_meteo": "météo réelle", "src_simulation": "simulation",
        "c_title": "Servir {n} en priorité",
        "c_why1": "Réservoir à {lvl} %, autonomie de {aut} jours sans apport",
        "c_why2": "Score de besoin {score}/100, rang {rank} sur {tot}",
        "c_act_full": "Dessaler et transférer {alloc} {m3d} vers {n} dès demain.",
        "c_act_part": "Transférer {alloc} {m3d} sur les {need} demandés et compléter par des camions-citernes.",
        "c_i1": "Eau acheminée", "c_i2": "Manque restant",
        "c_ev1": "Niveau du réservoir", "c_ev2": "Autonomie", "c_ev3": "Score de besoin",
        "l_title": "Réparer la fuite probable à {n}",
        "l_why1": "Débit de nuit à {r} % du débit moyen (normal : environ {nr} %)",
        "l_why2": "Perte estimée à {loss} {m3d}, soit {pct} % de la demande",
        "l_action": "Envoyer une équipe de recherche de fuites sur le réseau de {n} et isoler le secteur dont le débit de nuit est anormal.",
        "l_i1": "Eau récupérée", "l_i2": "Énergie libérée", "l_i3": "Valeur estimée",
        "l_ev1": "Débit de nuit", "l_ev2": "Perte estimée", "l_ev3": "Part de la demande",
        "s_title": "Contrôler le capteur du réservoir de {n}",
        "s_why1": "Débit constant depuis 24 h, sans le rythme jour/nuit attendu",
        "s_why2": "Le niveau affiché ({lvl} %) et la détection de fuite ne sont plus fiables",
        "s_action": "Envoyer un technicien vérifier et recalibrer le capteur. En attendant, alimenter {n} avec une marge de sécurité.",
        "s_i1": "Détection de fuite", "s_i2": "Risque évité", "s_i2v": "décision sur donnée erronée",
        "s_ev1": "Variation du débit sur 24 h", "s_ev2": "Niveau affiché",
        "p_title": "Arrêter le dessalement et le pompage vers {n}",
        "p_why1": "Réservoir à {lvl} %, au-dessus du seuil d'arrêt de {stop} %",
        "p_why2": "Autonomie de {aut} jours sans apport",
        "p_action": "Couper l'alimentation de {n} et réorienter la production vers les villes dont le niveau est bas.",
        "p_i1": "Eau économisée", "p_i2": "Énergie libérée", "p_i3": "Valeur estimée",
        "p_ev1": "Niveau du réservoir", "p_ev2": "Seuil d'arrêt", "p_ev3": "Autonomie",
        "u_title": "Concentrer le pompage le {date}",
        "u_why1": "Excédent d'énergie prévu : {mwh_} {mwh} (couverture {cv} %)",
        "u_why2": "Réservoirs les plus bas : ",
        "u_action": "Produire au-delà du besoin ce jour-là et remplir les réservoirs bas avec de l'énergie renouvelable.",
        "u_i1": "Eau stockable en plus", "u_i2": "Énergie utilisée", "u_i3": "Valeur estimée",
        "u_ev1": "Couverture ce jour-là", "u_ev2": "Excédent", "u_ev3": "Dans (jours)",
        "h_title": "Prévoir un appoint pour {n} jours déficitaires sur 30",
        "h_why1": "Premiers jours concernés : {first}",
        "h_why2": "L'incertitude de la prévision augmente avec l'horizon (±23 % à 30 jours)",
        "h_action": "Négocier un appoint réseau ou constituer du stockage d'eau avant ces dates.",
        "h_i1": "Énergie à couvrir", "h_i1v": "{x} {mwh} sur 30 j", "h_i2": "Jours concernés", "h_i3": "Coût de l'appoint réseau",
        "h_ev1": "Jours déficitaires", "h_ev2": "Pire couverture", "h_ev3": "Origine de la prévision",
    },
    "ar": {
        "m3d": "م³/يوم", "m3": "م³", "mwh": "ميغاواط ساعة", "mwhd": "ميغاواط ساعة/يوم", "mad": "درهم", "madd": "درهم/يوم", "days": "أيام",
        "none": "لا شيء", "normal": "طبيعي", "ok_rest": "مُستعاد",
        "e_title": "طاقة غير كافية غدًا: {unmet} {m3d} لن يتم إنتاجها",
        "e_why1": "التوقعات {avail} {mwh} مقابل {needed} {mwh} مطلوبة (التغطية {cov} %)",
        "e_served": "المدن المزوَّدة: {x}", "e_part": " · تزويد جزئي: {x}", "e_defer": " · مؤجَّلة: {x}",
        "e_action": "تفعيل إمداد احتياطي من الشبكة بقدرة {gap} {mwh}، أو تركيز الإنتاج على المدن ذات الأولوية وتأجيل الباقي.",
        "e_i1": "ماء غير منتَج", "e_i2": "طاقة ناقصة", "e_i3": "كلفة الإمداد الاحتياطي",
        "e_ev1": "تغطية الغد", "e_ev2": "مدن مزوَّدة بالكامل", "e_ev3": "مصدر التوقعات",
        "src_meteo": "طقس فعلي", "src_simulation": "محاكاة",
        "c_title": "تزويد {n} أولاً",
        "c_why1": "الخزان عند {lvl} % واستقلالية {aut} أيام دون إمداد",
        "c_why2": "درجة الحاجة {score}/100، المرتبة {rank} من {tot}",
        "c_act_full": "تحلية ونقل {alloc} {m3d} إلى {n} ابتداءً من الغد.",
        "c_act_part": "نقل {alloc} {m3d} من أصل {need} مطلوبة وتكملة الباقي بالصهاريج المتنقلة.",
        "c_i1": "ماء مُوصَّل", "c_i2": "نقص متبقٍّ",
        "c_ev1": "مستوى الخزان", "c_ev2": "الاستقلالية", "c_ev3": "درجة الحاجة",
        "l_title": "إصلاح التسرب المحتمل في {n}",
        "l_why1": "التدفق الليلي عند {r} % من المتوسط (الطبيعي: حوالي {nr} %)",
        "l_why2": "الفقد المقدَّر {loss} {m3d}، أي {pct} % من الطلب",
        "l_action": "إرسال فريق للكشف عن التسربات في شبكة {n} وعزل القطاع الذي يُسجّل تدفقًا ليليًا غير عادي.",
        "l_i1": "ماء مسترجَع", "l_i2": "طاقة محرَّرة", "l_i3": "القيمة المقدَّرة",
        "l_ev1": "التدفق الليلي", "l_ev2": "الفقد المقدَّر", "l_ev3": "نسبة من الطلب",
        "s_title": "فحص مستشعر خزان {n}",
        "s_why1": "تدفق ثابت منذ 24 ساعة دون إيقاع النهار/الليل المعتاد",
        "s_why2": "المستوى المعروض ({lvl} %) وكشف التسرب لم يعودا موثوقين",
        "s_action": "إرسال تقني لفحص المستشعر وإعادة معايرته. وإلى ذلك الحين، تزويد {n} بهامش أمان.",
        "s_i1": "كشف التسرب", "s_i2": "خطر مُتجنَّب", "s_i2v": "قرار مبني على بيانات خاطئة",
        "s_ev1": "تغيّر التدفق خلال 24 ساعة", "s_ev2": "المستوى المعروض",
        "p_title": "إيقاف التحلية والضخ نحو {n}",
        "p_why1": "الخزان عند {lvl} %، فوق عتبة التوقف {stop} %",
        "p_why2": "استقلالية {aut} أيام دون إمداد",
        "p_action": "قطع التزويد عن {n} وتوجيه الإنتاج إلى المدن ذات المستوى المنخفض.",
        "p_i1": "ماء موفَّر", "p_i2": "طاقة محرَّرة", "p_i3": "القيمة المقدَّرة",
        "p_ev1": "مستوى الخزان", "p_ev2": "عتبة التوقف", "p_ev3": "الاستقلالية",
        "u_title": "تركيز الضخ يوم {date}",
        "u_why1": "فائض طاقة متوقع: {mwh_} {mwh} (التغطية {cv} %)",
        "u_why2": "أدنى الخزانات: ",
        "u_action": "الإنتاج فوق الحاجة في ذلك اليوم وملء الخزانات المنخفضة بالطاقة المتجددة.",
        "u_i1": "ماء إضافي قابل للتخزين", "u_i2": "طاقة مستعملة", "u_i3": "القيمة المقدَّرة",
        "u_ev1": "التغطية في ذلك اليوم", "u_ev2": "الفائض", "u_ev3": "بعد (أيام)",
        "h_title": "توقُّع إمداد احتياطي لـ {n} يومًا عجزًا من أصل 30",
        "h_why1": "أولى الأيام المعنية: {first}",
        "h_why2": "عدم اليقين في التوقعات يزداد مع الأفق (±23 % عند 30 يومًا)",
        "h_action": "التفاوض على إمداد احتياطي من الشبكة أو تكوين مخزون مائي قبل هذه التواريخ.",
        "h_i1": "طاقة يجب تغطيتها", "h_i1v": "{x} {mwh} خلال 30 يومًا", "h_i2": "أيام معنية", "h_i3": "كلفة الإمداد الاحتياطي",
        "h_ev1": "أيام العجز", "h_ev2": "أسوأ تغطية", "h_ev3": "مصدر التوقعات",
    },
}


# --------------------------------------------------------------------------
# Main computation
# --------------------------------------------------------------------------
def _evaluate_city(c: dict, inject, sensors: dict, lang: str = "fr") -> dict:
    info = sensors.get(c["id"]) or {}
    inj = js_round(c["dem"] * .15) if inject == c["id"] else 0

    lvl, lvl_src = c["lvl"], "simulation"
    if info.get("level") is not None:
        lvl, lvl_src = clamp(float(info["level"]), 0, 100), "capteur"

    if info.get("q"):
        # Real readings: the demand is what was measured, minus any detected leak
        q0 = [float(v) for v in info["q"]]
        det0 = detect(q0)
        dem = max(sum(q0) - (det0["loss"] if det0["kind"] == "leak" else 0), 1)
        q = [v + inj / 24 for v in q0]
        flow_src = "capteurs"
    else:
        dem = c["dem"]
        q = simulated_series(c, c.get("leak", 0) + inj, bool(c.get("frozen")))
        flow_src = "simulation"

    det = detect(q)
    leak = js_round(det["loss"] / 10) * 10 if det["kind"] == "leak" else 0
    draw = max(dem - c["conv"] + leak, 1)
    aut = lvl / 100 * c["cap"] / draw
    refill = max(0, (REFILL_TARGET - lvl) / 100 * c["cap"] / REFILL_DAYS)
    full = lvl >= FULL_STOP_LEVEL
    need = 0 if full else js_round(draw + refill)
    score = js_round(clamp(.55 * (100 - lvl) + .35 * clamp(100 * (1 - aut / 7), 0, 100)
                           + .1 * min(100, c["pop"] / 7), 0, 100))
    if full:
        status = "full"
    elif lvl < 20 or aut < 2:
        status = "crit"
    elif lvl < 40 or aut < 4:
        status = "low"
    else:
        status = "ok"
    return {
        "id": c["id"], "n": c["na"] if lang == "ar" else c["n"], "pop": c["pop"], "cap": c["cap"], "dem": dem, "conv": c["conv"],
        "lvl": lvl, "q": q, "det": det, "leak": leak, "aut": aut, "need": need, "score": score,
        "status": status, "full": full, "ratio": det["ratio"], "alloc": 0, "rank": 0,
        "lat": c["lat"], "lon": c["lon"], "src": {"flow": flow_src, "level": lvl_src},
    }


def compute(scenario: str = "prevu", inject=None, sensors=None, today: date | None = None,
            forecast: dict | None = None, extra: dict | None = None, lang: str = "fr",
            assumptions: dict | None = None) -> dict:
    """Full state of the platform for tomorrow and the next 30 days.

    forecast:    {iso_date: weather forecast per MW} from weather.py (None = simulation only)
    extra:       {site_id: additional MW} (what-if planner)
    assumptions: {"grid_price_mad_mwh": float, "co2_t_per_mwh": float} for the impact figures
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    if lang not in LANGS:
        raise ValueError(f"unknown language: {lang}")
    sensors = sensors or {}
    today = today or date.today()
    asm = {"grid_price_mad_mwh": GRID_PRICE_MAD_MWH, "co2_t_per_mwh": CO2_T_PER_MWH, **(assumptions or {})}

    fc = {}
    if forecast:
        for d in range(DAYS):
            day = forecast.get((today + timedelta(days=1 + d)).isoformat())
            if day:
                fc[d] = day
    ed = lambda d: energy_day(d, scenario, fc, extra)  # noqa: E731

    e = ed(0)
    avail = e["s"] + e["w"]
    cities = [_evaluate_city(c, inject, sensors, lang) for c in CITIES]

    supply_cap = min(PLANT_CAP, avail * 1000 / KWH_PER_M3)
    remaining = supply_cap
    order = sorted(cities, key=lambda c: -c["score"])
    for i, c in enumerate(order):
        c["rank"] = i + 1
        c["alloc"] = js_round(min(c["need"], max(0, remaining)))
        remaining -= c["alloc"]

    total_need = sum(c["need"] for c in cities)
    needed = min(total_need, PLANT_CAP) * KWH_PER_M3 / 1000
    allocated = sum(c["alloc"] for c in cities)
    coverage = avail / needed * 100 if needed > 0 else 100.0 * (1 if avail > 0 else 0)
    m = {
        "sc": scenario, "e": e, "avail": avail, "needed": needed, "coverage": coverage,
        "supplyCap": supply_cap, "cities": cities, "order": order, "totalNeed": total_need,
        "allocated": allocated, "unmet": total_need - allocated, "ed": ed, "lang": lang,
        "energy_source": e["source"], "asm": asm,
    }
    decisions = build_decisions(m, today, lang)
    impact = compute_impact(m)

    days = []
    for d in range(DAYS):
        x = ed(d)
        days.append({
            "offset": d, "date": (today + timedelta(days=1 + d)).isoformat(),
            "solar_mwh": x["s"], "wind_mwh": x["w"],
            "coverage_pct": (x["s"] + x["w"]) / needed * 100 if needed > 0 else 100.0,
            "uncertainty_pct": (.06 + d * .0058) * 100, "source": x["source"],
        })
    hh = hourly(e)
    n_live = sum(1 for c in cities if c["src"]["flow"] == "capteurs")
    mode = "simulation" if n_live == 0 else ("capteurs" if n_live == len(cities) else "mixte")
    site_name = (lambda s: s["na"]) if lang == "ar" else (lambda s: s["n"])
    ex = extra or {}

    return {
        "scenario": scenario,
        "lang": lang,
        "date": (today + timedelta(days=1)).isoformat(),
        "data_mode": mode,
        "constants": {
            "kwh_per_m3": KWH_PER_M3, "plant_capacity_m3_day": PLANT_CAP,
            "night_share": NIGHT, "full_stop_level_pct": FULL_STOP_LEVEL,
        },
        "forecast": {"source": e["source"], "real_days": len(fc), "horizon_days": DAYS},
        "energy": {
            "available_mwh": avail, "solar_mwh": e["s"], "wind_mwh": e["w"], "source": e["source"],
            "needed_mwh": needed, "coverage_pct": m["coverage"], "producible_m3_day": supply_cap,
            "sites": [{"id": s["id"], "name": site_name(s), "type": "solar", "mw": s["mw"] + ex.get(s["id"], 0),
                       "mwh": e["solar"][i], "lat": s["lat"], "lon": s["lon"]} for i, s in enumerate(SOLAR_SITES)]
                     + [{"id": s["id"], "name": site_name(s), "type": "wind", "mw": s["mw"] + ex.get(s["id"], 0),
                         "mwh": e["wind"][i], "lat": s["lat"], "lon": s["lon"]} for i, s in enumerate(WIND_SITES)],
            "hourly": {"solar": hh["solar"], "wind": hh["wind"]},
            "days": days,
        },
        "water": {
            "total_need_m3_day": total_need, "allocated_m3_day": allocated, "unmet_m3_day": total_need - allocated,
            "order": [c["id"] for c in order],
            "cities": [_public_city(c) for c in cities],
        },
        "sensors": [{
            "id": c["id"], "name": c["n"], "source": c["src"]["flow"],
            "readings_24h": (sensors.get(c["id"]) or {}).get("readings", 0),
            "last_reading": (sensors.get(c["id"]) or {}).get("last"),
            "level_pct": c["lvl"] if c["src"]["level"] == "capteur" else None,
        } for c in cities],
        "decisions": decisions,
        "impact": impact,
    }


def _public_city(c: dict) -> dict:
    return {
        "id": c["id"], "name": c["n"], "population_k": c["pop"], "tank_capacity_m3": c["cap"],
        "demand_m3_day": c["dem"], "level_pct": c["lvl"], "autonomy_days": c["aut"],
        "need_m3_day": c["need"], "allocated_m3_day": c["alloc"], "need_score": c["score"],
        "priority_rank": c["rank"], "tank_status": c["status"], "tank_full": c["full"],
        "flow_24h_m3h": [round(v, 2) for v in c["q"]],
        "detection": {"kind": c["det"]["kind"], "night_ratio": c["det"]["ratio"], "loss_m3_day": c["det"]["loss"]},
        "leak_m3_day": c["leak"], "lat": c["lat"], "lon": c["lon"],
        "data_source": c["src"],
    }


# --------------------------------------------------------------------------
# Impact: what the platform's decisions are worth
# --------------------------------------------------------------------------
def compute_impact(m: dict) -> dict:
    """Money, CO2 and water. Every figure is per day and rests on the stated assumptions."""
    price, co2 = m["asm"]["grid_price_mad_mwh"], m["asm"]["co2_t_per_mwh"]
    cities = m["cities"]
    used = m["allocated"] * KWH_PER_M3 / 1000                      # renewable energy turned into water
    used_ren = min(used, m["avail"])
    leak_m3 = sum(c["leak"] for c in cities if c["det"]["kind"] == "leak")
    leak_mwh = leak_m3 * KWH_PER_M3 / 1000
    stop_m3 = sum(max(0, c["dem"] - c["conv"]) for c in cities if c["full"])
    stop_mwh = stop_m3 * KWH_PER_M3 / 1000
    gap = max(0.0, m["needed"] - m["avail"])
    total_mad = (used_ren + leak_mwh + stop_mwh) * price
    return {
        "assumptions": {"grid_price_mad_mwh": price, "co2_t_per_mwh": co2, "kwh_per_m3": KWH_PER_M3},
        "renewable_used_mwh": used_ren,
        "renewable_value_mad_day": used_ren * price,
        "renewable_co2_t_day": used_ren * co2,
        "leak_water_m3_day": leak_m3, "leak_energy_mwh_day": leak_mwh,
        "leak_value_mad_day": leak_mwh * price, "leak_co2_t_day": leak_mwh * co2,
        "stop_water_m3_day": stop_m3, "stop_energy_mwh_day": stop_mwh,
        "stop_value_mad_day": stop_mwh * price, "stop_co2_t_day": stop_mwh * co2,
        "backup_gap_mwh": gap, "backup_cost_mad_day": gap * price,
        "total_value_mad_day": total_mad, "total_value_mad_year": total_mad * 365,
        "total_co2_t_day": (used_ren + leak_mwh + stop_mwh) * co2,
        "total_co2_t_year": (used_ren + leak_mwh + stop_mwh) * co2 * 365,
        "total_water_saved_m3_day": leak_m3 + stop_m3,
    }


# --------------------------------------------------------------------------
# What-if planner: the same day with extra installed power on one site
# --------------------------------------------------------------------------
def summarize(model: dict) -> dict:
    cs = model["water"]["cities"]
    return {
        "coverage_pct": model["energy"]["coverage_pct"],
        "available_mwh": model["energy"]["available_mwh"],
        "producible_m3_day": model["energy"]["producible_m3_day"],
        "allocated_m3_day": model["water"]["allocated_m3_day"],
        "unmet_m3_day": model["water"]["unmet_m3_day"],
        "cities_served": sum(1 for c in cs if c["need_m3_day"] > 0 and c["allocated_m3_day"] >= c["need_m3_day"]),
        "cities_waiting": sum(1 for c in cs if c["need_m3_day"] > 0 and c["allocated_m3_day"] < c["need_m3_day"]),
        "deficit_days": sum(1 for d in model["energy"]["days"] if d["coverage_pct"] < 100),
        "value_mad_day": model["impact"]["total_value_mad_day"],
        "co2_t_day": model["impact"]["total_co2_t_day"],
    }


def whatif(site_id: str, add_mw: float, scenario: str = "prevu", **kw) -> dict:
    """Compare tomorrow and the next 30 days with and without add_mw more installed power on a site.

    Accepts the same keyword arguments as compute() (inject, sensors, today, forecast, lang, assumptions).
    """
    site = next((s for s in SOLAR_SITES + WIND_SITES if s["id"] == site_id), None)
    if site is None:
        raise ValueError(f"unknown site: {site_id}")
    base = summarize(compute(scenario, **kw))
    variant = summarize(compute(scenario, extra={site_id: add_mw}, **kw))
    delta = {k: variant[k] - base[k] for k in base}
    return {
        "scenario": scenario,
        "site": {"id": site_id, "name": site["na"] if kw.get("lang") == "ar" else site["n"],
                 "type": "solar" if site in SOLAR_SITES else "wind", "mw": site["mw"], "add_mw": add_mw},
        "base": base, "variant": variant, "delta": delta,
    }


# --------------------------------------------------------------------------
# Recommended decisions
# --------------------------------------------------------------------------
def build_decisions(m: dict, today: date, lang: str = "fr") -> list:
    """Cross the energy forecast with the tank state. urgency: 0 urgent, 1 important, 2 planned.

    Every decision carries: why (reasons), action, impact (estimated effect), evidence (the figures
    behind it), city (id of the tank concerned, if any), sources (where the data comes from).
    """
    T = TXT[lang]
    units = {k: T[k] for k in ("m3d", "m3", "mwh", "mwhd", "mad", "madd")}
    if lang == "ar":
        # a space between digit groups would be reordered inside right-to-left text
        nb = lambda n: f"{js_round(n):,}"  # noqa: E731
        d1 = lambda n: f"{n:.1f}"  # noqa: E731
    else:
        nb, d1 = globals()["nb"], globals()["d1"]

    def t(key, **kw):
        return T[key].format(**units, **kw)

    out = []
    price = m["asm"]["grid_price_mad_mwh"]
    esrc = m["energy_source"]
    esrc_txt = T["src_" + esrc]
    names = lambda cs: ", ".join(c["n"] for c in cs)  # noqa: E731
    mad = lambda mwh: f"{nb(mwh * price)} {T['madd']}"  # noqa: E731
    cities, order = m["cities"], m["order"]

    if m["coverage"] < 100:
        gap = max(0, m["needed"] - m["avail"])
        served = [c for c in order if c["need"] > 0 and c["alloc"] >= c["need"]]
        part = [c for c in order if 0 < c["alloc"] < c["need"]]
        dif = [c for c in order if c["need"] > 0 and c["alloc"] == 0]
        why2 = t("e_served", x=names(served) or T["none"])
        if part:
            why2 += t("e_part", x=names(part))
        if dif:
            why2 += t("e_defer", x=names(dif))
        out.append({
            "id": "energie", "urgency": 0 if m["coverage"] < 75 else 1, "city": None,
            "title": t("e_title", unmet=nb(m["unmet"])),
            "why": [t("e_why1", avail=nb(m["avail"]), needed=nb(m["needed"]), cov=js_round(m["coverage"])), why2],
            "action": t("e_action", gap=nb(gap)),
            "impact": [[T["e_i1"], f"{nb(m['unmet'])} {T['m3d']}"], [T["e_i2"], f"{nb(gap)} {T['mwh']}"],
                       [T["e_i3"], mad(gap)]],
            "evidence": [[T["e_ev1"], f"{js_round(m['coverage'])} %"], [T["e_ev2"], f"{len(served)} / {len(cities)}"],
                         [T["e_ev3"], esrc_txt]],
            "value_mad_day": gap * price, "sources": {"energy": esrc, "flow": None},
        })

    for c in (c for c in cities if c["status"] == "crit"):
        act = t("c_act_full", alloc=nb(c["alloc"]), n=c["n"]) if c["alloc"] >= c["need"] \
            else t("c_act_part", alloc=nb(c["alloc"]), need=nb(c["need"]))
        out.append({
            "id": f"crit-{c['id']}", "urgency": 0, "city": c["id"], "title": t("c_title", n=c["n"]),
            "why": [t("c_why1", lvl=js_round(c["lvl"]), aut=d1(c["aut"])),
                    t("c_why2", score=c["score"], rank=c["rank"], tot=len(cities))],
            "action": act,
            "impact": [[T["c_i1"], f"{nb(c['alloc'])} {T['m3d']}"], [T["c_i2"], f"{nb(c['need'] - c['alloc'])} {T['m3d']}"]],
            "evidence": [[T["c_ev1"], f"{js_round(c['lvl'])} %"], [T["c_ev2"], f"{d1(c['aut'])} {T['days']}"],
                         [T["c_ev3"], f"{c['score']}/100"]],
            "value_mad_day": None, "sources": {"energy": esrc, "flow": c["src"]["flow"]},
        })

    for c in (c for c in cities if c["det"]["kind"] == "leak"):
        mwh = c["leak"] * KWH_PER_M3 / 1000
        out.append({
            "id": f"leak-{c['id']}", "urgency": 0 if c["leak"] / c["dem"] > .12 else 1, "city": c["id"],
            "title": t("l_title", n=c["n"]),
            "why": [t("l_why1", r=js_round(c["ratio"] * 100), nr=js_round(NIGHT * 100)),
                    t("l_why2", loss=nb(c["leak"]), pct=js_round(c["leak"] / c["dem"] * 100))],
            "action": t("l_action", n=c["n"]),
            "impact": [[T["l_i1"], f"{nb(c['leak'])} {T['m3d']}"], [T["l_i2"], f"{d1(mwh)} {T['mwhd']}"],
                       [T["l_i3"], mad(mwh)]],
            "evidence": [[T["l_ev1"], f"{js_round(c['ratio'] * 100)} % ({T['normal']} {js_round(NIGHT * 100)} %)"],
                         [T["l_ev2"], f"{nb(c['leak'])} {T['m3d']}"],
                         [T["l_ev3"], f"{js_round(c['leak'] / c['dem'] * 100)} %"]],
            "value_mad_day": mwh * price, "sources": {"energy": esrc, "flow": c["src"]["flow"]},
        })

    for c in (c for c in cities if c["det"]["kind"] == "frozen"):
        out.append({
            "id": f"sensor-{c['id']}", "urgency": 1, "city": c["id"], "title": t("s_title", n=c["n"]),
            "why": [t("s_why1"), t("s_why2", lvl=js_round(c["lvl"]))],
            "action": t("s_action", n=c["n"]),
            "impact": [[T["s_i1"], T["ok_rest"]], [T["s_i2"], T["s_i2v"]]],
            "evidence": [[T["s_ev1"], "< 2 %"], [T["s_ev2"], f"{js_round(c['lvl'])} %"]],
            "value_mad_day": None, "sources": {"energy": esrc, "flow": c["src"]["flow"]},
        })

    for c in (c for c in cities if c["full"]):
        sv = max(0, c["dem"] - c["conv"])
        mwh = sv * KWH_PER_M3 / 1000
        out.append({
            "id": f"stop-{c['id']}", "urgency": 1, "city": c["id"], "title": t("p_title", n=c["n"]),
            "why": [t("p_why1", lvl=js_round(c["lvl"]), stop=FULL_STOP_LEVEL), t("p_why2", aut=d1(c["aut"]))],
            "action": t("p_action", n=c["n"]),
            "impact": [[T["p_i1"], f"{nb(sv)} {T['m3d']}"], [T["p_i2"], f"{d1(mwh)} {T['mwhd']}"], [T["p_i3"], mad(mwh)]],
            "evidence": [[T["p_ev1"], f"{js_round(c['lvl'])} %"], [T["p_ev2"], f"{FULL_STOP_LEVEL} %"],
                         [T["p_ev3"], f"{d1(c['aut'])} {T['days']}"]],
            "value_mad_day": mwh * price, "sources": {"energy": esrc, "flow": c["src"]["flow"]},
        })

    surplus = None
    for d in range(1, 14):
        x = m["ed"](d)
        cv = (x["s"] + x["w"]) / m["needed"] * 100 if m["needed"] > 0 else 0
        if cv >= 130:
            surplus = {"d": d, "cv": cv, "mwh": x["s"] + x["w"] - m["needed"], "src": x["source"]}
            break
    if surplus:
        low = sorted((c for c in cities if not c["full"]), key=lambda c: c["lvl"])[:2]
        extra = min(surplus["mwh"] * 1000 / KWH_PER_M3, PLANT_CAP - min(m["totalNeed"], PLANT_CAP))
        used = extra * KWH_PER_M3 / 1000
        out.append({
            "id": "surplus", "urgency": 2, "city": None,
            "title": t("u_title", date=date_long(today + timedelta(days=1 + surplus["d"]), lang)),
            "why": [t("u_why1", mwh_=nb(surplus["mwh"]), cv=js_round(surplus["cv"])),
                    T["u_why2"] + ", ".join(f"{c['n']} ({js_round(c['lvl'])} %)" for c in low)],
            "action": t("u_action"),
            "impact": [[T["u_i1"], f"{nb(extra)} {T['m3']}"], [T["u_i2"], f"{nb(surplus['mwh'])} {T['mwh']}"],
                       [T["u_i3"], mad(used)]],
            "evidence": [[T["u_ev1"], f"{js_round(surplus['cv'])} %"], [T["u_ev2"], f"{nb(surplus['mwh'])} {T['mwh']}"],
                         [T["u_ev3"], str(surplus["d"] + 1)]],
            "value_mad_day": used * price, "sources": {"energy": surplus["src"], "flow": None},
        })

    deficit = []
    for d in range(DAYS):
        x = m["ed"](d)
        if x["s"] + x["w"] < m["needed"]:
            deficit.append({"d": d, "gap": m["needed"] - x["s"] - x["w"], "cv": (x["s"] + x["w"]) / m["needed"] * 100})
    if len(deficit) >= 4:
        first = ", ".join(date_short(today + timedelta(days=1 + x["d"]), lang) for x in deficit[:3])
        total_gap = sum(x["gap"] for x in deficit)
        out.append({
            "id": "horizon", "urgency": 2, "city": None,
            "title": t("h_title", n=len(deficit)),
            "why": [t("h_why1", first=first), t("h_why2")],
            "action": t("h_action"),
            "impact": [[T["h_i1"], t("h_i1v", x=nb(total_gap))], [T["h_i2"], f"{len(deficit)}"],
                       [T["h_i3"], f"{nb(total_gap * price)} {T['mad']}"]],
            "evidence": [[T["h_ev1"], f"{len(deficit)} / 30"], [T["h_ev2"], f"{js_round(min(x['cv'] for x in deficit))} %"],
                         [T["h_ev3"], esrc_txt]],
            "value_mad_day": None, "sources": {"energy": esrc, "flow": None},
        })

    return sorted(out, key=lambda r: r["urgency"])  # stable sort
