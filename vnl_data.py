"""
VNL 2026 (Men's) — live data layer.

Pulls the real preliminary-round standings and the real remaining fixtures from
Wikipedia (cleanest structured source) and caches them to a local JSON file so we
don't hammer the page on every run.

Public API:
    load_data(refresh=False, max_age_hours=12) -> dict
        {
          "fetched_at": ISO-8601 str,
          "source":     "wikipedia" | "cache" | "override",
          "standings":  { team: {played, won, lost, pts, sw, sl, sr,
                                  spw, spl, pr}, ... },
          "fixtures":   [ [home, away], ... ],   # remaining prelim matches only
        }

Manual override:
    If `vnl_fixtures_override.json` exists next to this file and contains a non-empty
    JSON list of [home, away] pairs, those REPLACE the parsed fixtures. Use it when
    Wikipedia parsing misses or mangles something — hand-edit and re-run.

Standalone:
    python3 vnl_data.py            # fetch (or use cache) and print a summary
    python3 vnl_data.py --refresh  # force a fresh fetch
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from io import StringIO

import pandas as pd
import requests

WIKI_URL = (
    "https://en.wikipedia.org/wiki/"
    "2026_FIVB_Men%27s_Volleyball_Nations_League"
)

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_HERE, "vnl_cache.json")
OVERRIDE_FILE = os.path.join(_HERE, "vnl_fixtures_override.json")

# A preliminary-round pool on the page is a 12-match table. The Final round tables
# are smaller (and currently have TBD teams), so this size filter keeps us to prelim.
POOL_SIZE = 12

# Columns that identify a per-match results table from pandas.read_html.
_MATCH_COLS = {"Date", "Time", "Score", "Set 1", "Total", "Attd", "Report"}
_HOME_COL, _AWAY_COL = "Unnamed: 2", "Unnamed: 4"

_USER_AGENT = "vnl-stats/1.0 (personal stats tool; pandas.read_html)"


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------
def _clean_team(name) -> str:
    """Strip Wikipedia footnote markers, e.g. 'China[a]' -> 'China'."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    return re.sub(r"\[.*?\]", "", str(name)).strip()


def fetch_html() -> str:
    resp = requests.get(WIKI_URL, headers={"User-Agent": _USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_standings(tables) -> dict:
    """Find the 'Ranking' table and turn it into our standings dict."""
    need = {"Pos", "Team", "Pld", "W", "L", "Pts", "SW", "SL", "SR", "SPW", "SPL"}
    table = None
    for t in tables:
        if need.issubset({str(c) for c in t.columns}):
            table = t
            break
    if table is None:
        raise ValueError("Could not locate the standings (Ranking) table on the page.")

    standings = {}
    for _, row in table.iterrows():
        team = _clean_team(row["Team"])
        if not team:
            continue
        spw, spl = int(row["SPW"]), int(row["SPL"])
        standings[team] = {
            "played": int(row["Pld"]),
            "won": int(row["W"]),
            "lost": int(row["L"]),
            "pts": int(row["Pts"]),
            "sw": int(row["SW"]),
            "sl": int(row["SL"]),
            "sr": round(int(row["SW"]) / max(int(row["SL"]), 1), 3),
            "spw": spw,
            "spl": spl,
            # Point ratio — the finest VNL tiebreak; doubles as our strength proxy.
            "pr": round(spw / max(spl, 1), 3),
        }
    return standings


def _is_played(score) -> bool:
    """A played match has a real set score like '3-1'; an unplayed one is just '-'."""
    return bool(re.search(r"\d", str(score)))


def parse_fixtures(tables, valid_teams) -> list:
    """Remaining preliminary fixtures: unplayed rows in 12-match pool tables whose
    two teams are both real (in the standings)."""
    valid = set(valid_teams)
    fixtures = []
    for t in tables:
        if not _MATCH_COLS.issubset({str(c) for c in t.columns}):
            continue
        if len(t) != POOL_SIZE:  # skip Final round tables (different size / TBD)
            continue
        for _, row in t.iterrows():
            if _is_played(row["Score"]):
                continue
            home = _clean_team(row.get(_HOME_COL))
            away = _clean_team(row.get(_AWAY_COL))
            if home in valid and away in valid:
                fixtures.append([home, away])
    return fixtures


def _fetch_and_parse() -> dict:
    tables = pd.read_html(StringIO(fetch_html()))
    standings = parse_standings(tables)
    fixtures = parse_fixtures(tables, standings.keys())
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "wikipedia",
        "standings": standings,
        "fixtures": fixtures,
    }


# ---------------------------------------------------------------------------
# Cache + override + public loader
# ---------------------------------------------------------------------------
def _cache_age_hours(data) -> float:
    try:
        ts = datetime.fromisoformat(data["fetched_at"])
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    except (KeyError, ValueError):
        return float("inf")


def _apply_override(data) -> dict:
    if not os.path.exists(OVERRIDE_FILE):
        return data
    with open(OVERRIDE_FILE) as fh:
        override = json.load(fh)
    if override:  # non-empty list of [home, away] pairs
        data = dict(data)
        data["fixtures"] = [list(pair) for pair in override]
        data["source"] = f"{data['source']}+override"
    return data


def load_data(refresh=False, max_age_hours=12) -> dict:
    """Return live (or cached) standings + remaining fixtures.

    Uses the cache when it exists and is younger than `max_age_hours`, unless
    `refresh=True`. Falls back to a stale cache if a fresh fetch fails.
    """
    cached = None
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as fh:
                cached = json.load(fh)
        except (json.JSONDecodeError, OSError):
            cached = None

    if not refresh and cached and _cache_age_hours(cached) <= max_age_hours:
        data = dict(cached)
        data["source"] = "cache"
        return _apply_override(data)

    try:
        data = _fetch_and_parse()
        with open(CACHE_FILE, "w") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except Exception as exc:  # network / parse failure -> lean on cache if we have one
        if cached:
            print(f"[warn] fetch failed ({exc}); using cached data.", file=sys.stderr)
            data = dict(cached)
            data["source"] = "cache (stale)"
        else:
            raise
    return _apply_override(data)


def _summary(data):
    s, fx = data["standings"], data["fixtures"]
    print(f"source={data['source']}  fetched_at={data['fetched_at']}")
    print(f"teams={len(s)}  remaining fixtures={len(fx)}")
    rem = {}
    for h, a in fx:
        rem[h] = rem.get(h, 0) + 1
        rem[a] = rem.get(a, 0) + 1
    print("\nremaining matches per team:")
    for team in sorted(s, key=lambda t: s[t]["pts"], reverse=True):
        st = s[team]
        print(f"  {team:<15} played={st['played']:>2} pts={st['pts']:>2} "
              f"pr={st['pr']:.3f} remaining={rem.get(team, 0)}")


if __name__ == "__main__":
    _summary(load_data(refresh="--refresh" in sys.argv))
