"""Tonight: the live-ish evening page, rebuilt every hour from 7pm to 1am ET.

    python -m pipeline.today            # live (Yahoo API)
    python -m pipeline.today --sample   # fake data for design work

Each run reads the week's scoreboard (live matchup totals) and every roster's in-progress
stat line for tonight, scores the lines with the same Scorer Last Night uses, compares the
matchups with the previous run (score moves, category leads that flipped), asks Claude for
a few lines of commentary, and writes docs/data/today.json. The 5am paper supersedes it:
the page is "tonight" only while today.json's date is today's game date.

Yahoo has no push feed; its API returns in-progress stats that it refreshes every few
minutes, so this is a poll. Each run is about 14 API calls.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import recap, stats
from .night import _agg, _desc, key
from .players import INACTIVE, Scorer, _line, pct_map
from .tonight import ESPN, ABBR

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "today.json"
NIGHTS = ROOT / "docs" / "data" / "nights"
ET = ZoneInfo("America/New_York")
SLOTS = [19, 20, 21, 22, 23, 0, 1]  # ET hours of the hourly runs; the 1am run is the last


def game_date(now):
    """The NBA night a run belongs to: anything before 5am ET is still the previous night."""
    return (now - timedelta(hours=5)).date().isoformat()


def slot_no(now):
    h = now.hour
    return SLOTS.index(h) + 1 if h in SLOTS else None


def nba_games(date):
    """Tonight's NBA games from ESPN's public scoreboard: status, clock and score per game.
    Empty list if the lookup fails (the page just skips the strip)."""
    try:
        r = requests.get(ESPN, params={"dates": date.replace("-", "")}, timeout=20)
        r.raise_for_status()
        games = []
        for ev in r.json().get("events", []):
            comp = ev.get("competitions", [{}])[0]
            st = comp.get("status", {}).get("type", {})
            sides = []
            for c in comp.get("competitors", []):
                ab = (c.get("team", {}).get("abbreviation") or "").upper()
                sides.append({"team": ABBR.get(ab, ab), "score": int(c.get("score") or 0), "home": c.get("homeAway") == "home"})
            sides.sort(key=lambda s: s["home"])  # away first
            games.append({"teams": [s["team"] for s in sides], "score": [s["score"] for s in sides],
                          "state": st.get("state", ""),  # pre | in | post
                          "clock": comp.get("status", {}).get("displayClock", ""),
                          "period": comp.get("status", {}).get("period", 0),
                          "detail": st.get("shortDetail", "")})
        return games
    except Exception as e:
        print("ESPN scoreboard failed:", e)
        return []


def reference_lines(cats, pmap, tonight_rows, limit=14):
    """Scorer reference: every player-game from the last `limit` Last Night files plus tonight's lines.
    Built from the committed docs/data/nights/<date>.json files (cache/days is not in git)."""
    ref = []
    files = sorted(NIGHTS.glob("20??-??-??.json"))[-limit:]
    for f in files:
        try:
            n = json.loads(f.read_text())
        except Exception:
            continue
        for t in n.get("teams", []):
            for r in t.get("rows", []):
                a = {"cnt": {}, "frac": {}, "games": 1, "dates": []}
                for c in cats:
                    v = r.get("line", {}).get(c["id"])
                    if c["pct"] and isinstance(v, list) and len(v) == 3:
                        a["frac"][c["id"]] = [float(v[1]), float(v[2])]
                    elif not c["pct"] and isinstance(v, (int, float)):
                        a["cnt"][c["id"]] = v
                if a["cnt"] or a["frac"]:
                    ref.append(a)
    ref.extend(a for a, played in tonight_rows if played)
    return ref


def score_lines(d):
    """Every rostered player with a stat tonight, valued like Last Night."""
    cats, pmap = d["cats"], pct_map(d["cats"], d.get("frac") or {})
    tonight = []
    for tk, lines in d["lines"].items():
        for ln in lines:
            a, played = _agg(ln["stats"], cats, pmap)
            tonight.append((a, played, tk, ln))
    ref = reference_lines(cats, pmap, [(a, p) for a, p, _, _ in tonight])
    if not ref:
        return []
    scorer = Scorer(ref, cats)
    good = {c["id"] for c in cats if c["hb"]}
    rows = []
    for a, played, tk, ln in tonight:
        if not played:
            continue
        z = scorer.z(a)
        r = {"name": ln["name"], "team": tk, "slot": ln.get("pos"), "nba": ln.get("nba", ""),
             "benched": ln.get("pos") in INACTIVE, "value": round(sum(z.values()), 2),
             "z": {k: round(v, 2) for k, v in z.items()}, "line": _line(a, cats)}
        r["desc"] = _desc(r)
        best = sorted(r["z"], key=r["z"].get, reverse=True)
        r["best"] = [c for c in best if c in good and r["z"][c] > 0.5][:3]
        rows.append(r)
    rows.sort(key=lambda r: -r["value"])
    return rows


def team_nights(d, rows, games):
    """Per team: summed value of starters so far, how many starters have a game tonight, how many have played."""
    playing = set()
    for g in games:
        playing.update(g["teams"])
    out = []
    for tk, lines in d["lines"].items():
        starters = [ln for ln in lines if ln.get("pos") not in INACTIVE]
        with_game = [ln for ln in starters if (ln.get("nba") or "").upper() in playing] if playing else []
        mine = [r for r in rows if r["team"] == tk and not r["benched"]]
        bench = [r for r in rows if r["team"] == tk and r["benched"]]
        out.append({"team": tk, "value": round(sum(r["value"] for r in mine), 1), "played": len(mine),
                    "with_game": len(with_game) if playing else None, "bench_value": round(sum(r["value"] for r in bench), 1),
                    "top": [r["name"] for r in mine[:2]]})
    out.sort(key=lambda t: -t["value"])
    for i, t in enumerate(out):
        t["rank"] = i + 1
    return out


def diff_matchups(cur, prev):
    """Score moves and category lead flips since the previous run (same night only)."""
    if not prev:
        return cur, []
    before = {}
    for m in prev.get("matchups", []):
        before[(m["a"], m["b"])] = m
    flips = []
    for m in cur:
        p = before.get((m["a"], m["b"]))
        if not p:
            continue
        m["prev_score"] = p["score"]
        pl = {r["cat"]: r["lead"] for r in p["cats"]}
        for r in m["cats"]:
            was = pl.get(r["cat"])
            if was and was != r["lead"]:
                r["flip"] = was
                flips.append({"a": m["a"], "b": m["b"], "cat": r["cat"], "was": was, "now": r["lead"],
                              "a_val": r["a"], "b_val": r["b"]})
    return cur, flips


def fetch_live():
    from .yahoo import Yahoo, load_dotenv
    load_dotenv()
    y = Yahoo(os.environ["YAHOO_CLIENT_ID"], os.environ["YAHOO_CLIENT_SECRET"], os.environ["YAHOO_REFRESH_TOKEN"])
    lk = y.league_key(os.environ["YAHOO_LEAGUE_ID"])
    meta = y.meta(lk)
    cats = y.cats(lk)
    ids = {c["id"] for c in cats}
    frac = y.frac_ids(lk)
    teams, _ = y.teams_and_standings(lk)
    now = datetime.now(ET)
    date = game_date(now)
    lines = {}
    for tk in teams:
        try:
            lines[tk] = y.roster_day(tk, date, ids, frac)
        except Exception as e:
            print("roster fetch failed", tk, e)
    return {"date": date, "week": meta["current_week"], "cats": cats, "teams": teams, "frac": frac,
            "matchups": y.scoreboard(lk, meta["current_week"], ids), "lines": lines, "now": now}


def sample():
    from .sample import make
    d = make()
    dt = sorted(d["days"])[-1]
    lines = {}
    for tk, ls in d["days"][dt].items():
        out = []
        for i, ln in enumerate(ls):
            st = {}
            for k, v in ln["stats"].items():  # roughly half a game in, and a few players yet to tip
                if i % 4 == 3:
                    continue
                st[k] = [x // 2 for x in v] if isinstance(v, list) else v // 2
            out.append({**ln, "stats": st, "nba": ["BOS", "LAL", "DEN", "MIA", "NYK", "PHX"][i % 6]})
        lines[tk] = out
    now = datetime.now(ET).replace(hour=22, minute=5)
    import random  # a little drift between sample runs so score moves and lead flips show up on the page
    rnd = random.Random()
    for m in d["matchups"]:
        for side in ("a_stats", "b_stats"):
            for k, v in list(m[side].items()):
                if isinstance(v, (int, float)) and v and rnd.random() < 0.25:
                    m[side][k] = round(v * rnd.uniform(0.9, 1.12), 3)
    games = [{"teams": ["BOS", "NYK"], "score": [58, 61], "state": "in", "clock": "4:12", "period": 3, "detail": "4:12 - 3rd"},
             {"teams": ["LAL", "DEN"], "score": [112, 104], "state": "post", "clock": "0.0", "period": 4, "detail": "Final"},
             {"teams": ["MIA", "PHX"], "score": [0, 0], "state": "pre", "clock": "", "period": 0, "detail": "10:30 PM EDT"}]
    return {"date": d["date"], "week": d["league"]["current_week"], "cats": d["cats"], "teams": d["teams"], "frac": d["frac"],
            "matchups": d["matchups"], "lines": lines, "now": now, "games": games}


def main():
    is_sample = "--sample" in sys.argv
    d = sample() if is_sample else fetch_live()
    now = d["now"]
    games = d.get("games") if is_sample else nba_games(d["date"])
    prev = None
    if OUT.exists():
        try:
            p = json.loads(OUT.read_text())
            if p.get("date") == d["date"] and bool(p.get("sample")) == is_sample:
                prev = p
        except Exception:
            prev = None
    matchups = stats.matchup_board({"matchups": d["matchups"], "cats": d["cats"]})
    matchups, flips = diff_matchups(matchups, prev)
    rows = score_lines(d)
    tn = team_nights(d, rows, games)
    final = bool(games) and all(g["state"] == "post" for g in games)
    slot = slot_no(now) or len(SLOTS)
    t = {"date": d["date"], "week": d["week"], "cats": d["cats"], "teams": d["teams"], "matchups": matchups,
         "flips": flips, "rows": rows, "team_nights": tn, "games": games, "as_of": now.strftime("%-I:%M%p").lower(),
         "update": slot, "updates": len(SLOTS), "final": final}
    copy = recap.today_fallback(t) if is_sample else recap.today(t)
    out = {"date": d["date"], "week": d["week"], "sample": is_sample,
           "generated_at": now.isoformat(timespec="minutes"), "as_of": t["as_of"], "update": slot, "updates": len(SLOTS),
           "final": final, "cats": d["cats"], "team_names": {tk: {"name": v["name"], "manager": v.get("manager", "")} for tk, v in d["teams"].items()},
           "matchups": matchups, "flips": flips, "games": games,
           "lines": rows[:10], "bench": [r for r in rows if r["benched"] and r["value"] > 2][:5],
           "team_nights": tn, "headline": copy.get("headline"), "notes": copy.get("notes", [])}
    OUT.write_text(json.dumps(out, indent=1))
    print("wrote", OUT, "update", slot, "final" if final else "live", len(rows), "lines", len(flips), "flips")


if __name__ == "__main__":
    main()
