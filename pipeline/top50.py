"""The Fifty: this season's top players, valued the way THIS league plays.

    write(d) -> dict          # d is the dict build.py / web_current.py make (needs d["days"])
    python -m pipeline.top50  # rebuild from the web archive and print the top 15

Every rostered player's season totals so far are z-scored per category across the whole
rostered pool (the Last Night Scorer: volume-aware % impact, turnovers count against). Then
each manager's strategy is read from his own season totals: categories where his team sits
at least 0.9 standard deviations below the league and in the bottom three are treated as
punted. A player's value TO HIS MANAGER drops those categories. Values are put on a 0 to 100
scale (100 = the most valuable player in the league by his own manager's categories, 0 = the
least) and the top 50 are listed by value to their manager, with the full-category value
beside it so a punt that is costing a star shows up.

Writes docs/data/top50.json. Never blocks the paper (callers wrap it in try/except).
"""
import json
from pathlib import Path
from statistics import mean, pstdev

from .players import Scorer, _add_line, _empty, _line, pct_map

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "top50.json"
PUNT_Z = -0.9      # a team this far below the league in a category is giving it up
PUNT_BOTTOM = 3    # and must be in the bottom three teams in it
PUNT_MAX = 3       # at most three punted categories per manager
MIN_GAMES = 2      # players with fewer games are listed but not ranked


def _agg_players(d):
    """Season totals per (team, player) across every line in d["days"], bench included."""
    cats, pmap = d["cats"], pct_map(d["cats"], d.get("frac") or {})
    P = {}
    for dt in sorted(d["days"]):
        for tk, lines in d["days"][dt].items():
            for ln in lines:
                k = (tk, ln.get("pk") or ln["name"])
                p = P.setdefault(k, {**_empty(), "name": ln["name"], "team": tk, "nba": ln.get("nba", "")})
                if _add_line(p, ln["stats"], cats, pmap):
                    p["games"] += 1
                    p["nba"] = ln.get("nba") or p["nba"]
    # a player who changed teams this season: keep the stint with the most games as "his" team
    return [p for p in P.values() if p["games"] > 0]


def _team_z(players, cats):
    """Each team's season totals per category, z-scored across the 12 teams."""
    teams = {}
    for p in players:
        t = teams.setdefault(p["team"], _empty())
        for cid, v in p["cnt"].items():
            t["cnt"][cid] = t["cnt"].get(cid, 0) + v
        for cid, (m, a) in p["frac"].items():
            cur = t["frac"].setdefault(cid, [0.0, 0.0])
            cur[0] += m; cur[1] += a
    z = {}
    for c in cats:
        vals = {}
        for tk, t in teams.items():
            if c["pct"]:
                m, a = t["frac"].get(c["id"], (0, 0))
                vals[tk] = m / a if a else None
            else:
                vals[tk] = t["cnt"].get(c["id"], 0)
        xs = [v for v in vals.values() if v is not None]
        if len(xs) < 2:
            continue
        mu, sd = mean(xs), pstdev(xs) or 1
        z[c["id"]] = {tk: ((v - mu) / sd) * (1 if c["hb"] else -1) if v is not None else 0 for tk, v in vals.items()}
    return z


def strategies(players, cats):
    """Per team: punted categories (by the rule above) and the two strongest."""
    z = _team_z(players, cats)
    teams = sorted({p["team"] for p in players})
    out = {}
    for tk in teams:
        punt, strong = [], []
        for c in cats:
            col = z.get(c["id"])
            if not col:
                continue
            order = sorted(teams, key=lambda x: col.get(x, 0))
            if col.get(tk, 0) <= PUNT_Z and tk in order[:PUNT_BOTTOM]:
                punt.append((col[tk], c["id"]))
            strong.append((col.get(tk, 0), c["id"]))
        out[tk] = {"punts": [cid for _, cid in sorted(punt)[:PUNT_MAX]],
                   "strong": [cid for _, cid in sorted(strong, reverse=True)[:2]],
                   "z": {cid: round(col.get(tk, 0), 2) for cid, col in z.items()}}
    return out


def build(d):
    cats = d["cats"]
    players = _agg_players(d)
    if not players:
        return None
    scorer = Scorer(players, cats)
    strat = strategies(players, cats)
    rows = []
    for p in players:
        z = scorer.z(p)
        punts = set(strat.get(p["team"], {}).get("punts", []))
        full = sum(z.values())
        mine = sum(v for cid, v in z.items() if cid not in punts)
        rows.append({"name": p["name"], "team": p["team"], "nba": p["nba"], "games": p["games"],
                     "full_z": round(full, 2), "mine_z": round(mine, 2), "z": {k: round(v, 2) for k, v in z.items()},
                     "line": _line(p, cats), "dropped": sorted(punts, key=lambda c: z.get(c, 0)),
                     "best": [cid for cid in sorted(z, key=z.get, reverse=True) if z[cid] > 0.4 and cid not in punts][:3]})
    ranked = [r for r in rows if r["games"] >= MIN_GAMES]
    lo = min(r["mine_z"] for r in ranked) if ranked else 0
    hi = max(max(r["mine_z"] for r in ranked), max(r["full_z"] for r in ranked)) if ranked else 1
    span = (hi - lo) or 1
    scale = lambda v: round(max(0.0, min(100.0, (v - lo) / span * 100)), 1)
    for r in rows:
        r["score"] = scale(r["mine_z"])
        r["full"] = scale(r["full_z"])
        r["gain"] = round(r["score"] - r["full"], 1)  # what the manager's strategy does for this player's value
    rows.sort(key=lambda r: (-(r["games"] >= MIN_GAMES), -r["score"], -r["full"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1 if r["games"] >= MIN_GAMES else None
    by_team = {}
    for r in rows:
        by_team.setdefault(r["team"], []).append(r["name"])
    return {"date": d["date"], "week": d["league"].get("current_week"), "cats": cats,
            "team_names": {tk: {"name": v["name"], "manager": v.get("manager", "")} for tk, v in d["teams"].items()},
            "strategies": strat, "players": rows[:50], "rest": rows[50:], "pool": len(rows), "min_games": MIN_GAMES,
            "rule": f"a category is punted when a team is {abs(PUNT_Z)} standard deviations below the league in it and in the bottom {PUNT_BOTTOM}"}


def write(d):
    t = build(d)
    if not t:
        print("top50 skipped: no player lines yet")
        return None
    OUT.write_text(json.dumps(t, indent=1, ensure_ascii=False))
    print("wrote", OUT, "top:", ", ".join(f"{r['name']} {r['score']}" for r in t["players"][:3]))
    return t


if __name__ == "__main__":
    from .web_current import build_d, SRC
    d = build_d(json.loads(SRC.read_text()))
    t = write(d)
    if t:
        nm = lambda tk: t["team_names"][tk]["name"]
        cat = {c["id"]: c["name"] for c in t["cats"]}
        for tk, s in t["strategies"].items():
            if s["punts"]:
                print(f"  {nm(tk)} punts {', '.join(cat[c] for c in s['punts'])}")
        for r in t["players"][:15]:
            print(f"  {r['rank']:>2} {r['name']:<26} {nm(r['team']):<22} {r['score']:>5} (all cats {r['full']:>5}) {r['games']} gp")
