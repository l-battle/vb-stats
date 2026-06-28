"""
VNL 2026 (Men's) — interactive CLI.

Drive the stats tool without editing source. From the menu you can:
  1. View current standings (real, from Wikipedia) with the full tiebreak applied
  2. Run the Finals-qualification simulation and see each team's Finals %
  3. What-if mode: bake in a hypothetical result for an upcoming fixture and re-run
  4. Tweak the model at runtime (number of simulations, Elo scale)
  5. Refresh data from the web
  0. Quit

Run:  python3 vnl_cli.py
"""

import sys

import vnl_sim as engine
from vnl_data import load_data
from vnl_history import load_world_ranking


class Session:
    """Mutable working state: base data + any applied what-if results."""

    def __init__(self):
        self.meta = {}
        self.base_standings = {}
        self.base_fixtures = []
        self.standings = {}
        self.fixtures = []
        self.whatifs = []  # list of (home, away, home_sets, away_sets)
        self.n_sims = 20000
        self.elo_scale = engine.ELO_SCALE
        self.spread = engine.RATING_SPREAD
        self.last_results = None
        # Historical strength prior (FIVB world ranking).
        self.prior_points = None
        self.prior_as_of = ""
        self.prior_source = "unavailable"
        self.prior_k = engine.PRIOR_K

    def load(self, refresh=False):
        data = load_data(refresh=refresh)
        self.meta = {"source": data["source"], "fetched_at": data["fetched_at"]}
        self.base_standings = data["standings"]
        self.base_fixtures = [tuple(f) for f in data["fixtures"]]
        self._load_history(refresh=refresh)
        self._rebuild()

    def _load_history(self, refresh=False):
        """Load the world-ranking prior; degrade gracefully if it's unavailable."""
        try:
            hist = load_world_ranking(refresh=refresh)
            self.prior_points = hist["points"]
            self.prior_as_of = hist["as_of"]
            self.prior_source = hist["source"]
        except Exception as exc:
            self.prior_points = None
            self.prior_source = "unavailable"
            print(f"[warn] historical prior unavailable ({exc}); "
                  f"using in-season form only.")

    @property
    def prior_active(self):
        return bool(self.prior_points) and self.prior_k > 0

    def _rebuild(self):
        """Re-derive working standings/fixtures from base + applied what-ifs."""
        self.standings = {t: dict(s) for t, s in self.base_standings.items()}
        self.fixtures = list(self.base_fixtures)
        self.last_results = None
        for home, away, hs, as_ in self.whatifs:
            self.standings = engine.apply_result(self.standings, home, away, hs, as_)
            if (home, away) in self.fixtures:
                self.fixtures.remove((home, away))
            elif (away, home) in self.fixtures:
                self.fixtures.remove((away, home))

    def add_whatif(self, home, away, hs, as_):
        self.whatifs.append((home, away, hs, as_))
        self._rebuild()

    def reset_whatifs(self):
        self.whatifs = []
        self._rebuild()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def current_ranking(standings):
    teams = list(standings)
    pick = lambda k: {t: standings[t][k] for t in teams}
    return engine.final_ranking(
        teams, pick("pts"), pick("won"), pick("sw"), pick("sl"),
        pick("spw"), pick("spl"),
    )


def show_standings(sess):
    print(f"\nCurrent standings  (source: {sess.meta['source']}, "
          f"fetched {sess.meta['fetched_at']})")
    if sess.whatifs:
        print(f"  [{len(sess.whatifs)} what-if result(s) applied — option 4 to reset]")
    print(f"\n{'#':>2}  {'Team':<16}{'Pld':>4}{'W':>3}{'L':>3}{'Pts':>5}"
          f"{'SR':>7}{'PR':>7}{'Win%':>7}")
    print("-" * 56)
    for i, t in enumerate(current_ranking(sess.standings), 1):
        s = sess.standings[t]
        wr = s["won"] / max(s["played"], 1)
        flag = " *" if t == engine.HOST else "  "
        print(f"{i:>2}{flag}{t:<16}{s['played']:>4}{s['won']:>3}{s['lost']:>3}"
              f"{s['pts']:>5}{s['sr']:>7.3f}{s['pr']:>7.3f}{wr:>6.0%}")
    print(f"\n  * = host ({engine.HOST}); guaranteed a Finals berth (8th seed if "
          f"outside top {engine.QUALIFY}).")


def run_sim(sess):
    if sess.prior_active:
        prior_note = f"history prior ON ({sess.prior_as_of}, K={sess.prior_k})"
    else:
        prior_note = "history prior OFF (in-season form only)"
    print(f"\nRunning {sess.n_sims:,} simulations "
          f"(Elo scale={sess.elo_scale}, {len(sess.fixtures)} fixtures left)")
    print(f"  {prior_note}...")
    sess.last_results = engine.simulate(
        sess.standings, sess.fixtures, n_sims=sess.n_sims,
        elo_scale=sess.elo_scale, spread=sess.spread,
        prior_points=sess.prior_points, prior_k=sess.prior_k,
    )
    print()
    engine.print_report(sess.standings, sess.last_results, sess.n_sims)
    if sess.whatifs:
        print(f"\n(reflects {len(sess.whatifs)} applied what-if result(s))")


# ---------------------------------------------------------------------------
# What-if
# ---------------------------------------------------------------------------
def _choose_fixture(sess):
    fx = sess.fixtures
    if not fx:
        print("No remaining fixtures to set a result for.")
        return None
    print("\nRemaining fixtures:")
    for i, (h, a) in enumerate(fx, 1):
        print(f"  {i:>3}. {h} vs {a}")
    raw = input("Pick a fixture number (or filter by typing a team name): ").strip()
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw) - 1
        return fx[idx] if 0 <= idx < len(fx) else None
    matches = [(h, a) for (h, a) in fx if raw.lower() in (h + a).lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        print("No fixture matches that.")
        return None
    print("Multiple matches — be more specific:")
    for h, a in matches:
        print(f"    {h} vs {a}")
    return None


def whatif(sess):
    fixture = _choose_fixture(sess)
    if not fixture:
        return
    home, away = fixture
    print(f"\nHypothetical: {home} vs {away}")
    raw = input(f"Enter set score as home-away (e.g. 3-1 means {home} wins 3-1): ").strip()
    try:
        hs, as_ = (int(x) for x in raw.replace(":", "-").split("-"))
    except ValueError:
        print("Couldn't parse that score.")
        return
    valid = {(3, 0), (3, 1), (3, 2), (0, 3), (1, 3), (2, 3)}
    if (hs, as_) not in valid:
        print("Not a valid volleyball result (winner must reach 3 sets: "
              "3-0, 3-1, 3-2, or the reverse).")
        return
    sess.add_whatif(home, away, hs, as_)
    win = home if hs > as_ else away
    print(f"Applied: {home} {hs}-{as_} {away}  ->  {win} wins. "
          f"({len(sess.fixtures)} fixtures left.)")
    if input("Re-run the simulation now? [Y/n]: ").strip().lower() not in ("n", "no"):
        run_sim(sess)


def tweak(sess):
    prior = "n/a" if not sess.prior_points else (
        f"K={sess.prior_k} ({'on' if sess.prior_active else 'off'})")
    print(f"\nCurrent model: simulations={sess.n_sims:,}  Elo scale={sess.elo_scale}  "
          f"rating spread={sess.spread}  history prior {prior}")
    print("  (Elo scale: smaller -> bigger favourites. Spread: how strongly point "
          "ratio maps to strength.")
    print("   History prior K: shrinkage toward world-ranking history; higher = lean "
          "on history longer, 0 = off.)")
    raw = input(f"New simulation count [{sess.n_sims}]: ").strip()
    if raw:
        try:
            sess.n_sims = max(1, int(raw))
        except ValueError:
            print("  ignored (not an integer)")
    raw = input(f"New Elo scale [{sess.elo_scale}]: ").strip()
    if raw:
        try:
            sess.elo_scale = float(raw)
        except ValueError:
            print("  ignored (not a number)")
    raw = input(f"New rating spread [{sess.spread}]: ").strip()
    if raw:
        try:
            sess.spread = float(raw)
        except ValueError:
            print("  ignored (not a number)")
    if sess.prior_points:
        raw = input(f"New history prior K (0 = off) [{sess.prior_k}]: ").strip()
        if raw:
            try:
                sess.prior_k = max(0.0, float(raw))
            except ValueError:
                print("  ignored (not a number)")
    print(f"Model now: simulations={sess.n_sims:,}  Elo scale={sess.elo_scale}  "
          f"rating spread={sess.spread}  history prior K={sess.prior_k} "
          f"({'on' if sess.prior_active else 'off'})")


MENU = """
============== VNL 2026 Men's — Finals tool ==============
  1. View current standings / win rates
  2. Run qualification simulation
  3. What-if: set a hypothetical result and re-run
  4. Reset what-if results
  5. Tweak model (simulations, Elo scale, history weight)
  6. Refresh data from the web
  0. Quit
=========================================================="""


def main():
    sess = Session()
    print("Loading VNL data...")
    try:
        sess.load()
    except Exception as exc:
        print(f"Failed to load data: {exc}")
        return 1
    print(f"Loaded {len(sess.standings)} teams, {len(sess.fixtures)} remaining "
          f"fixtures (source: {sess.meta['source']}).")
    if sess.prior_active:
        print(f"History prior: world ranking {sess.prior_as_of} "
              f"(source: {sess.prior_source}, K={sess.prior_k}).")

    actions = {
        "1": lambda: show_standings(sess),
        "2": lambda: run_sim(sess),
        "3": lambda: whatif(sess),
        "4": lambda: (sess.reset_whatifs(), print("What-if results cleared.")),
        "5": lambda: tweak(sess),
        "6": lambda: (sess.load(refresh=True),
                      print(f"Refreshed: {len(sess.fixtures)} fixtures "
                            f"(source: {sess.meta['source']}).")),
    }
    while True:
        print(MENU)
        choice = input("Choose: ").strip()
        if choice in ("0", "q", "quit", "exit"):
            print("Bye.")
            return 0
        action = actions.get(choice)
        if action:
            action()
        else:
            print("Unknown choice.")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nBye.")
