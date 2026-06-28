"""
VNL 2026 (Men's) — historical strength prior.

Fetches the FIVB Senior World Ranking (men's) points from Wikipedia and caches them
locally. These points are a long-run strength estimate built from years of results, so
they make a good PRIOR for the simulator: early in the season (few matches played) we
lean on this history; as real results accumulate the in-season form takes over. The
blend itself lives in vnl_sim.build_ratings().

We use the Wikipedia *API* (action=parse) with a descriptive User-Agent rather than
scraping raw HTML: the ranking article makes pandas.read_html fall back to an
uninstalled parser, and a bare UA gets 403'd by Wikimedia.

Public API:
    load_world_ranking(refresh=False, max_age_days=7) -> dict
        {
          "fetched_at": ISO-8601 str,
          "source":     "wikipedia" | "cache" | "cache (stale)",
          "as_of":      "<the table's 'as of' caption, if found>",
          "points":     { team: float, ... },   # team names match the standings
        }

Standalone:
    python3 vnl_history.py            # fetch (or use cache) and print the ranking
    python3 vnl_history.py --refresh  # force a fresh fetch
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

API_URL = "https://en.wikipedia.org/w/api.php"
RANKING_PAGE = "FIVB Senior World Rankings"

# Descriptive UA per Wikimedia's policy (a bare "Mozilla/5.0" gets 403'd).
USER_AGENT = "vb-stats/1.0 (https://github.com/l-battle/vb-stats; mysticaldumpling21@gmail.com)"

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_HERE, "vnl_history_cache.json")

# The 18 VNL teams, as named in the standings — only these are kept from the ranking.
_VNL_TEAMS = {
    "Japan", "United States", "Ukraine", "Slovenia", "Poland", "Italy", "Serbia",
    "Turkey", "Brazil", "Bulgaria", "France", "Belgium", "Germany", "Argentina",
    "Canada", "Iran", "China", "Cuba",
}


def _clean(text) -> str:
    return re.sub(r"\[.*?\]", "", str(text)).strip()


def _fetch_html() -> str:
    resp = requests.get(
        API_URL,
        params={"action": "parse", "page": RANKING_PAGE, "prop": "text", "format": "json"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["parse"]["text"]["*"]


def _parse_points(html):
    """Find the men's ranking table and pull {team: points} plus the 'as of' caption."""
    soup = BeautifulSoup(html, "html.parser")  # built-in parser; no extra dependency
    best_points, best_caption = {}, ""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        points = {}
        for tr in rows:
            cells = [_clean(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
            for j, cell in enumerate(cells):
                if cell in _VNL_TEAMS:
                    for nxt in cells[j + 1:]:  # first number after the team name
                        m = re.fullmatch(r"\d+(?:\.\d+)?", nxt.replace(",", ""))
                        if m:
                            points[cell] = float(m.group())
                            break
                    break
        if len(points) > len(best_points):
            best_points = points
            cap = rows[0].get_text(" ") if rows else ""
            best_caption = _clean(cap)
    return best_points, best_caption


def _fetch_and_parse() -> dict:
    points, caption = _parse_points(_fetch_html())
    if len(points) < 10:  # sanity: we expect ~18 of the VNL teams
        raise ValueError(f"world-ranking parse found only {len(points)} teams")
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "wikipedia",
        "as_of": caption,
        "points": points,
    }


def _cache_age_days(data) -> float:
    try:
        ts = datetime.fromisoformat(data["fetched_at"])
        return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    except (KeyError, ValueError):
        return float("inf")


def load_world_ranking(refresh=False, max_age_days=7) -> dict:
    """Return world-ranking points (live or cached). Falls back to a stale cache if a
    fresh fetch fails; raises only if there's no cache to fall back on."""
    cached = None
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as fh:
                cached = json.load(fh)
        except (json.JSONDecodeError, OSError):
            cached = None

    if not refresh and cached and _cache_age_days(cached) <= max_age_days:
        data = dict(cached)
        data["source"] = "cache"
        return data

    try:
        data = _fetch_and_parse()
        with open(CACHE_FILE, "w") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        return data
    except Exception as exc:
        if cached:
            print(f"[warn] world-ranking fetch failed ({exc}); using cache.", file=sys.stderr)
            data = dict(cached)
            data["source"] = "cache (stale)"
            return data
        raise


if __name__ == "__main__":
    d = load_world_ranking(refresh="--refresh" in sys.argv)
    print(f"source={d['source']}  fetched_at={d['fetched_at']}")
    print(f"as_of: {d['as_of']}")
    print(f"teams: {len(d['points'])}\n")
    for team in sorted(d["points"], key=lambda t: d["points"][t], reverse=True):
        print(f"  {team:<15}{d['points'][team]:>8.2f}")
