# WahaSud

**Smart water management for southern Morocco.** WahaSud links three questions that are usually handled separately: *is there enough renewable energy to pump and desalinate water?*, *which cities need water first, and where is it leaking?*, and *what should decision makers do tomorrow?*

> **Résumé (FR).** WahaSud est une plateforme de gestion intelligente de l'eau pour le sud du Maroc : prévision d'énergie solaire et éolienne pour le dessalement et le pompage, niveau des réservoirs par ville, détection de fuites par le débit de nuit, répartition de la production selon le besoin, et recommandations de décision pour les autorités. L'interface est en français ; le serveur (API REST + base SQLite) n'a aucune dépendance externe.

Built for a hackathon. The energy forecast uses **real weather** (Open-Meteo) when the server has internet access and falls back to a simulation otherwise. Tank and flow data stay **simulated** until real sensors send measurements (see [Connecting real sensors](#connecting-real-sensors)).

## What it does

| Page | Question | What you get |
|---|---|---|
| **Énergie** | Is there enough renewable energy for tomorrow? | Solar and wind forecast (hourly for tomorrow, daily for 30 days with uncertainty), compared with the electricity needed to desalinate and pump; three weather scenarios. |
| **Eau** | Where does water run short, where does it leak? | Tank level and autonomy per city, a need score, allocation of tomorrow's production by priority, leak detection from night flow, stuck-sensor detection, pumping stopped when a tank is full. |
| **Décisions** | What should we do? | Ranked recommendations (urgent / important / planned) with the reason, the action, the estimated effect **and the figures behind it** (tank level, night flow, coverage...), the origin of the data (sensors, real weather or simulation) and a small flow chart for tank decisions. Each decision can be validated or postponed and is written to a journal. |
| **Impact** | What are these decisions worth? | Dirhams saved, CO₂ avoided and water recovered per day (and per year): renewable energy used, leaks to repair, pumping stopped towards full tanks. |

The interface is available in **French and Arabic** (right-to-left layout); the button at the top right switches, and the choice is remembered. The Eau page has a schematic **map** of the cities (colour = tank status) and production sites. The Énergie page has a **what-if planner**: add MW on one site and compare tomorrow and the next 30 days with and without it.

### Backend features
- **Real weather.** A background thread asks [Open-Meteo](https://open-meteo.com) (free, no key) for solar irradiance and 100 m wind speed at each production site, 16 days ahead, and refreshes every 3 hours. Days beyond 16 use the simulation. If the service is unreachable the last data is kept for up to 12 hours, then the simulation takes over; the Énergie page says which one is in use. Start with `--no-weather` to disable it.
- **Live sensors.** A city switches from simulation to real data as soon as a sensor has sent 24 consecutive hourly readings; leak detection and allocation then run on the measurements. The latest measured tank level replaces the simulated one.
- **Decision journal.** Validated, postponed and cancelled decisions are stored with their date in SQLite.
- **REST API** for everything the interface shows, so other tools can use it.

## Quick start

Requires Python 3.11 or newer. **No package to install.**

```bash
git clone https://github.com/<your-account>/wahasud.git
cd wahasud
python backend/server.py
# open http://127.0.0.1:8000
```

Try the live-sensor feature in a second terminal:

```bash
python scripts/simulate_sensors.py --leak guelmim:0.25 --freeze boujdour
```

Reload the **Eau** page: every city now shows *capteurs*, Guelmim has a detected leak of about 3 500 m³/day and Boujdour a stuck sensor. Remove the readings with `python scripts/simulate_sensors.py --reset`.

## Architecture

```
 browser (frontend/)          server (backend/)                 storage
 ┌──────────────────┐  JSON  ┌──────────────────────┐        ┌───────────────┐
 │ index.html       │ <────> │ server.py  REST API  │ <────> │ SQLite        │
 │ app.js  style.css│        │ engine.py  model     │        │ readings      │
 └──────────────────┘        └──────────────────────┘        │ decision_log  │
                                       ▲                      └───────────────┘
        sensors / scripts/simulate_sensors.py  ── POST /api/readings
```

- `backend/engine.py`: pure calculation (forecast, leak detection, allocation, recommendations). No I/O, easy to test.
- `backend/weather.py`: Open-Meteo client and conversion of weather into energy per MW (the engine scales it by each site's power).
- `backend/db.py`: SQLite storage, parameterised queries only.
- `backend/server.py`: HTTP server (standard library) serving the API and the static frontend.

## API

All responses are JSON. Query parameters `scenario` (`prevu`, `faible`, `surplus`), `leak` (a city id, simulates a leak) and `lang` (`fr` or `ar`, language of names and recommendation texts) are accepted by the read endpoints.

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness check |
| GET | `/api/dashboard` | Everything: energy, water, sensors, decisions |
| GET | `/api/energy` · `/api/water` · `/api/decisions` | One part of the dashboard |
| GET | `/api/sensors` | Source of the data and number of readings per city |
| GET | `/api/impact` | Money, CO₂ and water figures with the assumptions used |
| GET | `/api/whatif?site=wind-dakhla&add_mw=10` | Compare the situation with and without extra installed power on one site. Site ids: `solar-agadir`, `solar-guelmim`, `solar-laayoune`, `wind-tarfaya`, `wind-boujdour`, `wind-dakhla` |
| POST | `/api/decisions` | `{"id": "leak-guelmim", "status": "valide", "lang": "ar"}` with status `valide`, `reporte` or `annule` (the journal keeps the title in the language used) |
| GET | `/api/decisions/history?limit=50` | Decision journal, newest first |
| POST | `/api/readings` | One reading or `{"readings": [...]}` (up to 2000). **Needs `X-API-Key` if `WAHASUD_API_KEY` is set.** |
| DELETE | `/api/readings?city=<id>` | Delete readings (all cities if `city` is omitted). Same key rule. |

City ids: `agadir`, `tiznit`, `guelmim`, `tantan`, `laayoune`, `boujdour`, `dakhla`.

## Connecting real sensors

Each reading is the **average outflow of one hour** of a city's tank, in m³/h, and optionally the tank level.

```bash
curl -X POST http://127.0.0.1:8000/api/readings \
  -H "Content-Type: application/json" -H "X-API-Key: $WAHASUD_API_KEY" \
  -d '{"city_id": "tiznit", "ts": "2026-11-23T14:00:00+01:00", "flow_m3h": 742.5, "level_pct": 38.4}'
```

- `ts` is an ISO 8601 date-time; the reading is attached to the hour that contains it. Without an offset it is read as Morocco local time. Without `ts`, the current hour is used. A second reading for the same hour replaces the first.
- The detector uses the **24 last complete hours**. With fewer, the city stays on the simulation (the Eau page shows the count).
- Readings in the future or older than 90 days are refused.

## Configuration

| Variable / option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Use `0.0.0.0` to accept other machines |
| `--port` or `PORT` | `8000` | Port |
| `WAHASUD_DB` or `--db` | `data/wahasud.db` | SQLite file |
| `WAHASUD_API_KEY` | not set | If set, writing or deleting readings requires `X-API-Key` |
| `--no-weather` or `WAHASUD_WEATHER=0` | weather on | Do not call Open-Meteo, use the simulated energy forecast |
| `WAHASUD_GRID_PRICE` | `1100` | Cost of one MWh of back-up grid electricity, in MAD (impact figures) |
| `WAHASUD_CO2_FACTOR` | `0.65` | Tonnes of CO₂ per MWh of the grid (impact figures) |

## Tests

```bash
python -m unittest discover -s tests -v
```

70 tests cover the model, the weather conversion (with a fake Open-Meteo response, so no network is needed), the sensor logic, the impact figures, the what-if planner, both languages, input validation, the API key, path traversal and the decision journal. The simulated model was also checked value by value against the original JavaScript prototype. GitHub Actions runs the tests on Python 3.11 and 3.13 (`.github/workflows/tests.yml`).

## Model assumptions

- Energy needed: 3.6 kWh per m³ desalinated + 0.7 kWh per m³ transferred = 4.3 kWh/m³.
- Simulated desalination capacity: 160 000 m³/day.
- Solar: forecast irradiance / 1000 × installed MW × 0.80 performance ratio. Wind: power curve of a generic onshore turbine (starts at 3 m/s, rated at 12 m/s, stops at 25 m/s) × 0.93 availability. Both come from Open-Meteo for the first 16 days; later days use the seeded simulation. Uncertainty grows from ±6 % to ±23 % over 30 days.
- Impact: renewable energy used, energy of repaired leaks and of stopped pumping are valued at the grid price per MWh and the grid CO₂ factor above (stated assumptions, not measurements).
- Target tank level 70 % within 7 days. Pumping and desalination towards a city stop above 90 %.
- Leak detection: mean flow between 1 am and 5 am compared with the expected share (about 38 % of the mean). A perfectly flat flow signals a stuck sensor.
- Need score: tank level (55 %), autonomy without supply (35 %), population (10 %). Production is distributed in score order.

## Limits and roadmap

Honest list of what this prototype does **not** do yet:

- The energy forecast is weather-based but not calibrated: site coordinates, performance ratio and turbine curve are generic. Compare with the real output of the plants and adjust `weather.py`. Beyond 16 days it is still a simulation, and the demand is assumed constant.
- The site coordinates and the map are approximate (the map is a schematic coastline, not a survey).
- City parameters (tank capacity, demand, sites) are constants at the top of `engine.py`.
- No user accounts: the decision buttons are open to anyone who can reach the server. If you deploy publicly, put it behind authentication (reverse proxy) first. Sensor writes are protected by the API key.
- The decision journal records what was decided, not who decided it.
- SQLite is fine for a demo and a pilot; use a server database for many sensors.

Ideas: SMS/WhatsApp alerts for urgent decisions (needs an account with a messaging provider), ONEE/ONEP data feeds, tank-level projection over 30 days, one-click export of the daily brief as PDF, user roles.

## Project structure

```
backend/    engine.py  weather.py  db.py  server.py
frontend/   index.html  app.js  i18n.js  style.css
scripts/    simulate_sensors.py
tests/      test_engine.py  test_features.py  test_weather.py  test_api.py
```

## License

MIT, see [LICENSE](LICENSE).
