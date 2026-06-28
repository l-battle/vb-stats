# VNL 2026 (Men's) — Finals Qualification Tool

Live stats + Monte Carlo Finals-qualification simulator for the 2026 FIVB Men's
Volleyball Nations League. Pulls the **real** current standings and remaining
fixtures from Wikipedia, then projects each team's chance of reaching the Finals.

## Quick start

```bash
pip install pandas requests lxml
python3 vnl_cli.py
```

From the interactive menu you can:

1. View current standings / win rates (full VNL tiebreak applied)
2. Run the qualification simulation and see each team's Finals %
3. **What-if**: bake in a hypothetical result for an upcoming match and re-run
4. Reset what-if results
5. Tweak the model (number of simulations, Elo scale, rating spread, history weight)
6. Refresh data from the web

## Layout

| File | Role |
|------|------|
| `vnl_data.py`    | Fetches standings + remaining fixtures from Wikipedia; caches to `vnl_cache.json` (12h TTL, `--refresh` to force). |
| `vnl_history.py` | Fetches the FIVB world-ranking points (historical strength prior) via the Wikipedia API; caches to `vnl_history_cache.json` (7d TTL). |
| `vnl_sim.py`     | Simulation engine: Elo win-probability, VNL scoring, full 4-key tiebreak, host-berth logic, history blend. Run directly for a one-shot projection. |
| `vnl_cli.py`     | Interactive menu (the day-to-day entry point). |

## Notes

- **Historical prior:** team strength blends current-season point ratio with FIVB
  world-ranking points via shrinkage — `w = played / (played + K)`, so early matches
  lean on history and later ones on form. Tune `K` in the menu (option 5); `K = 0`
  turns the prior off (pure in-season form).
- **Tiebreak order:** points → matches won → set ratio → point ratio.
- **Host rule:** China is guaranteed a Finals berth (takes the 8th seed if it
  finishes outside the top 8 on merit).
- **Manual override:** if Wikipedia parsing ever misses a fixture, create
  `vnl_fixtures_override.json` — a JSON list of `[home, away]` pairs — to replace
  the parsed fixtures.
- Set-point totals used for the point-ratio tiebreak are simulated approximately;
  the Elo rating is seeded from point ratio and is a static strength estimate.
