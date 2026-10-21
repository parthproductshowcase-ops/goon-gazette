"""Current-season paper from Yahoo's league web pages (stopgap while the API is 403).

    python -m pipeline.web_current                 # archive/_web/current.json -> docs/data/preview/league.json
    python -m pipeline.web_current --recap FILE    # use a hand-written recap (headline/subhead/zingers)

The export is read in Claude's browser pane (Parth logged in): team pages with ?date= for
daily player lines and lineup slots, the weekly matchup lists for the schedule, and the
transactions page. This builds the same `d` dict that build.fetch_live() makes from the API
and runs the normal stats, so the preview shows real league numbers. It writes to
docs/data/preview/ only; league.json, edition.json and the chains are untouched, and the
site shows it at goongazette.com/?preview.
"""
import json
import sys
from datetime import date as Date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import players, recap, stats

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "archive" / "_web" / "current.json"
OUT = ROOT / "docs" / "data" / "preview"
CATS_FROM = ROOT / "archive" / "2025.json"   # same 11 categories and stat ids as 2025-26
FRAC = {"9004003": "FGM/A", "9007006": "FTM/A", "9001002": "3PTM/A"}
PCT = {"5": "9004003", "8": "9007006", "11": "9001002"}
COUNT = {"10": "tpm", "12": "pts", "13": "oreb", "14": "dreb", "16": "ast", "17": "st", "18": "blk", "19": "to"}
ACTIVE_OUT = {"BN", "IL", "IL+"}


def tk(n):
    return f"web.l.39751.t.{n}"


def line(row, cols):
    r = dict(zip(cols, row))
    s = {cid: r[k] for cid, k in COUNT.items()}
    s["9004003"] = [r["fgm"], r["fga"]]
    s["9007006"] = [r["ftm"], r["fta"]]
    s["9001002"] = [r["tpm"], r["tpa"]]
    return {"pk": "nba.p." + r["pid"], "name": r["name"], "pos": r["slot"], "nba": r["nba"], "stats": s}


def team_totals(lines):
    """Category totals for one team from its active (non-bench) lines."""
    act = [l for l in lines if l["pos"] not in ACTIVE_OUT]
    out = {cid: sum(l["stats"][cid] for l in act) for cid in COUNT}
    for cid, fid in PCT.items():
        m = sum(l["stats"][fid][0] for l in act)
        a = sum(l["stats"][fid][1] for l in act)
        out[cid] = round(m / a, 3) if a else None
    return out


def build_d(src):
    cats = json.loads(CATS_FROM.read_text())["cats"]
    cols = src["day_cols"]
    teams = {tk(n): {"name": nm, "manager": mg, "logo": None, "moves": src["moves"].get(n, 0), "trades": 0}
             for n, (nm, mg) in src["teams"].items()}
    order = sorted(teams, key=lambda t: teams[t]["name"].lower())
    standings = {t: {"w": 0, "l": 0, "t": 0, "rank": i + 1} for i, t in enumerate(order)}
    days = {dt: {tk(n): [line(r, cols) for r in rows] for n, rows in byteam.items()}
            for dt, byteam in src["days"].items()}
    for dt in days:  # teams with nobody playing still need an entry
        for t in teams:
            days[dt].setdefault(t, [])
    dates = sorted(days)
    last = dates[-1]
    wk = src["week"]
    # week-to-date totals per team, summed across the scraped days
    wtd = {}
    for t in teams:
        lines = [l for dt in dates for l in days[dt][t]]
        wtd[t] = team_totals(lines)
    matchups = [{"week": wk, "status": "midevent", "playoffs": False, "consolation": False, "winner": None,
                 "a": tk(a), "b": tk(b), "a_stats": wtd[tk(a)], "b_stats": wtd[tk(b)]}
                for a, b in src["sched"][str(wk)]]
    schedule = {int(w): [[tk(a), tk(b)] for a, b in pairs] for w, pairs in src["sched"].items()
                if wk <= int(w) <= src["reg_end"]}
    span = (Date.fromisoformat(src["week_end"]) - Date.fromisoformat(src["week_start"])).days + 1
    done = (Date.fromisoformat(last) - Date.fromisoformat(src["week_start"])).days + 1
    tx = []
    for i, (ts, team, kind, player, source, dropped) in enumerate(src["transactions"]):
        t = int(datetime.fromisoformat(ts).timestamp())
        tx.append({"id": f"w{i}a", "ts": t, "kind": kind, "team": tk(team), "source": source, "faab": None,
                   "pk": None, "player": player})
        if dropped:
            tx.append({"id": f"w{i}d", "ts": t, "kind": "drop", "team": tk(team), "source": None, "faab": None,
                       "pk": None, "player": dropped})
    d = {
        "league": {"name": "BallinWithMyGoons", "season": "2026", "current_week": wk, "start_week": 1,
                   "end_week": src["end_week"], "num_teams": len(teams), "draft_status": "postdraft"},
        "cats": cats, "teams": teams, "standings": standings,
        "weekly": {}, "results": {}, "schedule": schedule, "date": last,
        "num_playoff_teams": src["num_playoff_teams"], "reg_end": src["reg_end"],
        "week_progress": round(done / span, 2),
        "matchups": matchups, "season": wtd, "daily": {t: team_totals(days[last][t]) for t in teams},
        "days": days, "windows": {"current": {"week": wk, "start": src["week_start"], "end": last}},
        "transactions": tx, "frac": FRAC,
    }
    d["bench"] = players.bench_from_day(d, last)
    return d


def main():
    src = json.loads(SRC.read_text())
    d = build_d(src)
    a = stats.analyze(d, None)
    a["sample"] = False
    a["preview"] = True
    a["source"] = "yahoo-web"
    if "--facts" in sys.argv:
        print(json.dumps(recap._facts(a), indent=1, ensure_ascii=False))
        return
    if "--recap" in sys.argv:
        a["recap"] = json.loads(Path(sys.argv[sys.argv.index("--recap") + 1]).read_text())
    else:
        a["recap"] = recap.fallback(a)
    a["generated_at"] = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="minutes")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "league.json").write_text(json.dumps(a, indent=1, ensure_ascii=False))
    print("wrote", OUT / "league.json")


if __name__ == "__main__":
    main()
