"""
VNL 2026 (Men's) — simulation engine.

Pure-ish functions that take a `standings` dict and a `fixtures` list (both from
vnl_data.load_data) and project each team's chance of reaching the Finals.

Two kinds of output:
  1. Descriptive stats straight from the current table (win rate, point ratio) — exact.
  2. A Monte Carlo estimate of each team's Finals chance — only as good as the
     remaining fixtures fed in. With live Wikipedia data these are the REAL fixtures.

Model:
  - Win probability is logistic (Elo) on a rating seeded from each team's point ratio.
  - A match win is 3 sets; the loser's set count (0/1/2) is sampled from the favourite's
    dominance. Set points are sampled per set (to 25, or 15 for a deciding 5th) so the
    point-ratio tiebreak has something to move — these point totals are APPROXIMATE.

VNL ranking tiebreak order (all four implemented): points -> matches won ->
set ratio -> point ratio.

Run:  python3 vnl_sim.py        # prints a projection using live/cached data
"""

import random
import statistics
from collections import defaultdict

QUALIFY = 8          # 8 teams reach the Finals
HOST = "China"       # host gets a guaranteed berth (8th seed if outside the top 8)
ELO_SCALE = 400      # logistic scale: smaller -> bigger favourites
RATING_SPREAD = 1000  # turns point ratio into an Elo-style rating
PRIOR_K = 8          # shrinkage strength: blend weight is played/(played+K); 0 disables


# ---------------------------------------------------------------------------
# Strength model
# ---------------------------------------------------------------------------
def build_ratings(standings, spread=RATING_SPREAD, prior_points=None, prior_k=PRIOR_K):
    """Elo-style rating per team. The in-season component is point ratio of 1.00 == 0,
    >1 stronger, <1 weaker.

    If `prior_points` (e.g. FIVB world-ranking points from vnl_history) is given and
    `prior_k > 0`, blend in that historical prior by shrinkage:

        rating = w * in_season + (1 - w) * prior,   w = played / (played + prior_k)

    so a team leans on history early (few games) and on current form later. The prior
    is rescaled to the in-season rating's spread first, so the blend is apples-to-apples.
    Teams missing from `prior_points` keep their pure in-season rating.
    """
    in_raw = {t: standings[t]["pr"] - 1.0 for t in standings}
    in_rating = {t: in_raw[t] * spread for t in standings}
    if not prior_points or prior_k <= 0:
        return in_rating

    common = [t for t in standings if t in prior_points]
    if not common:
        return in_rating

    mean_pts = sum(prior_points[t] for t in common) / len(common)
    prior_raw = {t: prior_points[t] / mean_pts - 1.0 for t in common}
    # Match the prior's spread to the in-season spread so neither source dominates.
    s_in = statistics.pstdev([in_raw[t] for t in common]) or 1e-9
    s_pr = statistics.pstdev(list(prior_raw.values())) or 1e-9
    factor = s_in / s_pr

    ratings = {}
    for t in standings:
        if t in prior_raw:
            played = standings[t]["played"]
            w = played / (played + prior_k)
            ratings[t] = w * in_rating[t] + (1 - w) * prior_raw[t] * factor * spread
        else:
            ratings[t] = in_rating[t]
    return ratings


def win_prob(ratings, a, b, scale=ELO_SCALE):
    """Logistic (Elo) probability that team a beats team b in a match."""
    return 1.0 / (1.0 + 10 ** (-(ratings[a] - ratings[b]) / scale))


def match_points(loser_sets):
    """VNL scoring: 3 for a 3-0/3-1 win, 2 for a 3-2 win, 1 for a 2-3 loss."""
    if loser_sets < 2:
        return 3, 0          # winner, loser
    return 2, 1


def _set_points(is_decider=False):
    """Approximate (winner_pts, loser_pts) for one set. Deciding 5th set is to 15."""
    if is_decider:
        return 15, random.randint(8, 13)
    return 25, random.randint(16, 23)


def simulate_match(ratings, a, b, scale=ELO_SCALE):
    """Play one match. Return (winner, loser, loser_sets, points) where `points` is
    {team: (set_points_for, set_points_against)} contributions for this match."""
    pa = win_prob(ratings, a, b, scale)
    if random.random() < pa:
        winner, loser, pw = a, b, pa
    else:
        winner, loser, pw = b, a, 1 - pa

    # How dominant was the favourite? 0 == coin flip, 1 == sure thing.
    margin = (pw - 0.5) * 2
    p30 = 0.20 + 0.45 * margin     # blowout more likely when lopsided
    p31 = 0.40 - 0.10 * margin
    # close 3-2 more likely when even (p32 absorbs the remainder)

    r = random.random()
    if r < p30:
        loser_sets = 0
    elif r < p30 + p31:
        loser_sets = 1
    else:
        loser_sets = 2

    # Sample set points so the point-ratio tiebreak moves. The winner takes exactly
    # 3 sets and the loser `loser_sets`; in a 5-setter one winner set is the to-15
    # decider. (Set order doesn't affect the point totals, so it's left implicit.)
    points = _match_set_points(winner, loser, loser_sets)
    return winner, loser, loser_sets, points


def _match_set_points(winner, loser, loser_sets, sampler=_set_points):
    """Sum set points for a 3-`loser_sets` match. Returns {team: (for, against)}."""
    decider = (3 + loser_sets) == 5
    w_for = w_against = l_for = l_against = 0
    for i in range(3):  # the winner's three won sets (last one is the decider, if any)
        hi, lo = sampler(decider and i == 2)
        w_for += hi; w_against += lo
        l_for += lo; l_against += hi
    for _ in range(loser_sets):  # the loser's won sets (always to 25)
        hi, lo = sampler(False)
        l_for += hi; l_against += lo
        w_for += lo; w_against += hi
    return {winner: (w_for, w_against), loser: (l_for, l_against)}


# ---------------------------------------------------------------------------
# Ranking + qualification
# ---------------------------------------------------------------------------
def final_ranking(teams, pts, won, sw, sl, spw, spl):
    """Full VNL tiebreak chain: points -> matches won -> set ratio -> point ratio."""
    return sorted(
        teams,
        key=lambda t: (
            pts[t],
            won[t],
            sw[t] / max(sl[t], 1),
            spw[t] / max(spl[t], 1),
        ),
        reverse=True,
    )


def qualifiers(ranked, host=HOST, qualify=QUALIFY):
    """Top 8, but the host always gets in (takes the 8th seed if outside the top 8)."""
    top = ranked[:qualify]
    if host and host not in top:
        top = top[:qualify - 1] + [host]
    return set(top)


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------
def simulate(standings, fixtures, n_sims=20000, elo_scale=ELO_SCALE,
             spread=RATING_SPREAD, host=HOST, qualify=QUALIFY, seed=None,
             prior_points=None, prior_k=PRIOR_K):
    """Run the Monte Carlo and return a results dict keyed by team."""
    if seed is not None:
        random.seed(seed)
    ratings = build_ratings(standings, spread, prior_points, prior_k)
    teams = list(standings)

    qual_count = defaultdict(int)
    points_sum = defaultdict(float)
    rank_sum = defaultdict(float)

    for _ in range(n_sims):
        pts = {t: standings[t]["pts"] for t in teams}
        won = {t: standings[t]["won"] for t in teams}
        sw = {t: standings[t]["sw"] for t in teams}
        sl = {t: standings[t]["sl"] for t in teams}
        spw = {t: standings[t]["spw"] for t in teams}
        spl = {t: standings[t]["spl"] for t in teams}

        for a, b in fixtures:
            winner, loser, loser_sets, mpts = simulate_match(ratings, a, b, elo_scale)
            wp, lp = match_points(loser_sets)
            pts[winner] += wp
            pts[loser] += lp
            won[winner] += 1
            sw[winner] += 3
            sl[winner] += loser_sets
            sw[loser] += loser_sets
            sl[loser] += 3
            for t, (pf, pa) in mpts.items():
                spw[t] += pf
                spl[t] += pa

        ranked = final_ranking(teams, pts, won, sw, sl, spw, spl)
        for spot in qualifiers(ranked, host, qualify):
            qual_count[spot] += 1
        for i, t in enumerate(ranked):
            points_sum[t] += pts[t]
            rank_sum[t] += i + 1

    results = {}
    for t in teams:
        st = standings[t]
        results[t] = {
            "qualify": qual_count[t] / n_sims,
            "exp_pts": points_sum[t] / n_sims,
            "avg_rank": rank_sum[t] / n_sims,
            "win_rate": st["won"] / max(st["played"], 1),
        }
    return results


def apply_result(standings, home, away, home_sets, away_sets):
    """Return a NEW standings dict with one match result baked in (for what-if mode).
    Set points are filled in approximately so the point ratio stays sensible."""
    new = {t: dict(s) for t, s in standings.items()}
    if home_sets > away_sets:
        winner, loser, loser_sets = home, away, away_sets
    else:
        winner, loser, loser_sets = away, home, home_sets
    wp, lp = match_points(loser_sets)

    # Nominal, deterministic set points (no randomness in a what-if result).
    nominal = lambda decider: (15, 11) if decider else (25, 20)
    sp = _match_set_points(winner, loser, loser_sets, sampler=nominal)
    (w_for, w_against), (l_for, l_against) = sp[winner], sp[loser]

    for team, dwon, dsf, dsa, dpf, dpa, dp in (
        (winner, 1, 3, loser_sets, w_for, w_against, wp),
        (loser, 0, loser_sets, 3, l_for, l_against, lp),
    ):
        s = new[team]
        s["played"] += 1
        s["won"] += dwon
        s["lost"] += 0 if dwon else 1
        s["pts"] += dp
        s["sw"] += dsf
        s["sl"] += dsa
        s["spw"] += dpf
        s["spl"] += dpa
        s["sr"] = round(s["sw"] / max(s["sl"], 1), 3)
        s["pr"] = round(s["spw"] / max(s["spl"], 1), 3)
    return new


def print_report(standings, results, n_sims):
    print(f"VNL 2026 Men's — projection over {n_sims:,} simulations\n")
    print(f"{'Team':<16}{'WinRate':>8}{'Qualify%':>10}{'ExpPts':>8}{'AvgRank':>9}")
    print("-" * 51)
    order = sorted(results, key=lambda t: results[t]["qualify"], reverse=True)
    for t in order:
        r = results[t]
        print(f"{t:<16}{r['win_rate']:>7.0%}{r['qualify']:>9.0%}"
              f"{r['exp_pts']:>8.1f}{r['avg_rank']:>9.1f}")


if __name__ == "__main__":
    from vnl_data import load_data
    from vnl_history import load_world_ranking

    data = load_data()
    print(f"(data source: {data['source']}, fetched {data['fetched_at']}, "
          f"{len(data['fixtures'])} remaining fixtures)")
    try:
        hist = load_world_ranking()
        prior = hist["points"]
        print(f"(history prior: world ranking {hist['as_of']}, K={PRIOR_K})\n")
    except Exception as exc:
        prior = None
        print(f"(history prior unavailable: {exc})\n")

    res = simulate(data["standings"], data["fixtures"], n_sims=20000, seed=42,
                   prior_points=prior)
    print_report(data["standings"], res, 20000)
