"""Current-season paper from Yahoo's league web pages (stopgap while the API is 403).

    python -m pipeline.web_current --merge FILE        # fold a scrape (pipeline/web_scrape.js output) into archive/2026_web.json
    python -m pipeline.web_current --facts             # print the recap facts for the Squawk Box writer
    python -m pipeline.web_current --publish           # build the paper into docs/data (league.json, edition.json, history/)
    python -m pipeline.web_current                     # build only the private preview (docs/data/preview/league.json)

The Squawk Box comes from archive/recap_current.json when its "date" matches the paper's
date (written by hand or by the morning task, in recap.VOICE); otherwise the template.

The export holds every final day's player lines (only players who played), the schedule,
moves and transactions. This rebuilds the same `d` dict build.fetch_live() makes from the API
(weekly totals, results, standings, this week's matchups) and runs the normal stats. When the
API opens, the daily workflow takes over and overwrites league.json.
"""
import json
import sys
from datetime import date as Date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import night, players, recap, stats, trades

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "archive" / "2026_web.json"
RECAP = ROOT / "archive" / "recap_current.json"
DATA = ROOT / "docs" / "data"
POLLS = DATA / "polls.json"
CATS_FROM = ROOT / "archive" / "2025.json"   # same 11 categories and stat ids as 2025-26
FRAC = {"9004003": "FGM/A", "9007006": "FTM/A", "9001002": "3PTM/A"}
PCT = {"5": "9004003", "8": "9007006", "11": "9001002"}
COUNT = {"10": "tpm", "12": "pts", "13": "oreb", "14": "dreb", "16": "ast", "17": "st", "18": "blk", "19": "to"}
BENCHED = {"BN", "IL", "IL+"}
MGR_ID = {1: "johnj", 2: "eshanb", 3: "adarsh", 4: "phil", 5: "chris", 6: "parth",
          7: "roshan", 8: "keshav", 9: "arnavb", 10: "vishal", 11: "sagar", 12: "jason"}
MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def tk(n):
    return f"web.l.39751.t.{n}"


# ---------- weeks ----------
def week_of(src, day):
    d, end1 = Date.fromisoformat(day), Date.fromisoformat(src["week1_end"])
    return 1 if d <= end1 else 2 + (d - end1 - timedelta(days=1)).days // 7


def week_span(src, w):
    if w == 1:
        return src["week1_start"], src["week1_end"]
    s = Date.fromisoformat(src["week1_end"]) + timedelta(days=1 + 7 * (w - 2))
    return s.isoformat(), (s + timedelta(days=6)).isoformat()


# ---------- lines and totals ----------
def line(row, cols):
    r = dict(zip(cols, row))
    s = {cid: r[k] for cid, k in COUNT.items()}
    s["9004003"] = [r["fgm"], r["fga"]]
    s["9007006"] = [r["ftm"], r["fta"]]
    s["9001002"] = [r["tpm"], r["tpa"]]
    return {"pk": "nba.p." + r["pid"], "name": r["name"], "pos": r["slot"], "nba": r["nba"],
            "result": r.get("result", ""), "stats": s}


def totals(lines):
    """Category totals from active-slot lines (bench and IL don't count, as on Yahoo)."""
    act = [l for l in lines if l["pos"] not in BENCHED]
    out = {cid: sum(l["stats"][cid] for l in act) for cid in COUNT}
    for cid, fid in PCT.items():
        m = sum(l["stats"][fid][0] for l in act)
        a = sum(l["stats"][fid][1] for l in act)
        out[cid] = round(m / a, 3) if a else None
    return out


# ---------- transactions ----------
def parse_when(s):
    """'Oct 21, 6:57 am' -> epoch seconds (season runs Oct to Apr: Jan-Jun are 2027)."""
    mon, rest = s.split(" ", 1)
    day, tm = rest.split(", ")
    y = 2026 if MONTHS[mon] >= 7 else 2027
    dt = datetime.strptime(f"{y}-{MONTHS[mon]:02d}-{int(day):02d} {tm.upper()}", "%Y-%m-%d %I:%M %p")
    return int(dt.replace(tzinfo=ET).timestamp())


def transactions(src):
    out = []
    for i, t in enumerate(src.get("transactions", [])):
        ts = parse_when(t["when"])
        for j, p in enumerate(t["people"]):
            how = p["how"].lower()
            if not p["name"] or "trade" in how:
                continue
            drop = how.startswith("to ")
            out.append({"id": f"w{i}.{j}", "ts": ts, "kind": "drop" if drop else "add", "team": tk(t["team"]),
                        "source": None if drop else ("waivers" if "waiver" in how else "freeagents"),
                        "faab": None, "pk": None, "player": p["name"]})
    return out


# ---------- build ----------
def build_d(src, today=None):
    cats = json.loads(CATS_FROM.read_text())["cats"]
    cols = src["day_cols"]
    mv = src.get("moves", {})
    teams = {tk(int(n)): {"name": nm, "manager": mg, "logo": None,
                          "moves": (mv.get(n) or {}).get("moves", 0), "trades": (mv.get(n) or {}).get("trades", 0)}
             for n, (nm, mg) in src["teams"].items()}
    days = {}
    for dt, byteam in src["days"].items():
        days[dt] = {t: [] for t in teams}
        for n, rows in byteam.items():
            days[dt][tk(int(n))] = [line(r, cols) for r in rows]
    dates = sorted(days)
    last = dates[-1]
    today = today or datetime.now(ET).date().isoformat()
    cur = max(1, week_of(src, max(today, src["week1_start"])))
    cur = min(cur, src["end_week"])

    def week_totals(w, upto=None):
        s, e = week_span(src, w)
        e = min(e, upto) if upto else e
        ds = [x for x in dates if s <= x <= e]
        return {t: totals([l for x in ds for l in days[x][t]]) for t in teams}

    weekly, results = {}, {}
    for w in range(1, cur):
        weekly[w] = week_totals(w)
        results[w] = [{"week": w, "a": tk(a), "b": tk(b), "a_stats": weekly[w][tk(a)], "b_stats": weekly[w][tk(b)]}
                      for a, b in src["sched"][str(w)]]
    # standings from finished weeks: category W-L-T, ranked by win share
    rec = {t: [0, 0, 0] for t in teams}
    for w, ms in results.items():
        for m in ms:
            for c in cats:
                r = stats.cmp_cat(m["a_stats"].get(c["id"]), m["b_stats"].get(c["id"]), c["hb"], c["pct"])
                if r > 0:
                    rec[m["a"]][0] += 1; rec[m["b"]][1] += 1
                elif r < 0:
                    rec[m["b"]][0] += 1; rec[m["a"]][1] += 1
                else:
                    rec[m["a"]][2] += 1; rec[m["b"]][2] += 1
    share = lambda t: (rec[t][0] + rec[t][2] / 2) / (sum(rec[t]) or 1)
    order = sorted(teams, key=lambda t: (-share(t), teams[t]["name"].lower()))
    standings = {t: {"w": rec[t][0], "l": rec[t][1], "t": rec[t][2], "rank": i + 1} for i, t in enumerate(order)}

    now_tot = week_totals(cur, last)
    matchups = [{"week": cur, "status": "midevent", "playoffs": cur > src["reg_end"], "consolation": False,
                 "winner": None, "a": tk(a), "b": tk(b), "a_stats": now_tot[tk(a)], "b_stats": now_tot[tk(b)]}
                for a, b in src["sched"].get(str(cur), [])]
    schedule = {int(w): [[tk(a), tk(b)] for a, b in pairs] for w, pairs in src["sched"].items()
                if cur <= int(w) <= src["reg_end"]}
    # the night being reported can belong to the week that just ended (Monday's paper covers
    # Sunday, the last day of the old matchup), so pair opponents by the night's own week
    nw = min(max(1, week_of(src, last)), src["end_week"])
    night_matchups = [{"a": tk(a), "b": tk(b)} for a, b in src["sched"].get(str(nw), [])]
    cs, ce = week_span(src, cur)
    span = (Date.fromisoformat(ce) - Date.fromisoformat(cs)).days + 1
    done = max(0, (min(Date.fromisoformat(last), Date.fromisoformat(ce)) - Date.fromisoformat(cs)).days + 1)
    windows = {"current": {"week": cur, "start": cs, "end": min(ce, last)}}
    if cur > 1:
        ls, le = week_span(src, cur - 1)
        windows["last"] = {"week": cur - 1, "start": ls, "end": le}
    d = {
        "league": {"name": "BallinWithMyGoons", "season": "2026", "current_week": cur, "start_week": 1,
                   "end_week": src["end_week"], "num_teams": len(teams), "draft_status": "postdraft"},
        "cats": cats, "teams": teams, "standings": standings,
        "weekly": weekly, "results": results, "schedule": schedule, "date": last,
        "num_playoff_teams": src["num_playoff_teams"], "reg_end": src["reg_end"],
        "week_progress": round(done / span, 2),
        "matchups": matchups, "night_week": nw, "night_matchups": night_matchups,
        "season": {t: totals([l for x in dates for l in days[x][t]]) for t in teams},
        "daily": {t: totals(days[last][t]) for t in teams},
        "days": days, "windows": windows, "transactions": transactions(src), "frac": FRAC,
    }
    d["bench"] = players.bench_from_day(d, last)
    # every NBA line that night (free agents who balled) and who owns whom right now
    # nba row: name, nba, min, result, fgm, fga, ftm, fta, tpm, tpa, pts, oreb, dreb, ast, st, blk, to
    d["nba_lines"] = [{**line(["FA", r[0], "", r[1], "", r[3], *r[4:]], cols), "min": r[2]}
                      for r in (src.get("nba") or {}).get(last, [])]
    try:
        R = json.loads(POLLS.read_text()).get("rosters", {}).get("teams", {})
        d["owned_now"] = {night.key(n): v["team"] for v in R.values() for n in v.get("players", [])}
    except Exception:
        d["owned_now"] = {}
    return d


def analyze(src, today=None):
    d = build_d(src, today)
    analyze.d = d
    hist = DATA / "history"
    prev_files = sorted(p for p in hist.glob("*.json") if p.stem < d["date"]) if hist.exists() else []
    prev = json.loads(prev_files[-1].read_text()) if prev_files else None
    if prev and not str(next(iter(prev.get("teams", {})), "")).startswith("web."):
        prev = None  # older snapshot from the sample or the API; team keys don't line up
    a = stats.analyze(d, prev)
    a["sample"] = False
    a["source"] = "yahoo-web"
    a["night_week"] = d["night_week"]
    return a


# ---------- merge a scrape ----------
def merge(path):
    src = json.loads(SRC.read_text())
    new = json.loads(Path(path).read_text())
    for dt, byteam in (new.get("days") or {}).items():
        if len(byteam) != 12:
            sys.exit(f"{dt}: expected 12 teams, got {len(byteam)}; not merged")
        src["days"][dt] = byteam
    for dt, rows in (new.get("nba") or {}).items():
        src.setdefault("nba", {})[dt] = rows
    if new.get("moves"):
        src["moves"] = {str(k): v for k, v in new["moves"].items()}
    if new.get("transactions"):
        seen = {(t["team"], t["when"], tuple(p["name"] for p in t["people"])) for t in src.get("transactions", [])}
        add = [t for t in new["transactions"] if (t["team"], t["when"], tuple(p["name"] for p in t["people"])) not in seen]
        src["transactions"] = add + src.get("transactions", [])
    if new.get("rosters"):
        names = {str(k): v["team"] for k, v in new["rosters"].items() if v.get("team")}
        for k, nm in names.items():
            if k in src["teams"]:
                src["teams"][k][0] = nm
        pc = json.loads(POLLS.read_text())
        R = pc.setdefault("rosters", {"season": "2026-27", "source": "yahoo-web", "teams": {}})
        for k, v in new["rosters"].items():
            if len(v.get("players") or []) < 10:
                continue
            R["teams"][MGR_ID[int(k)]] = {"team": v["team"], "team_id": int(k), "players": v["players"], "il": v.get("il", [])}
            pc["players"] = sorted(set(pc.get("players", [])) | set(v["players"]))
        R["updated"] = datetime.now(ET).isoformat(timespec="minutes")
        POLLS.write_text(json.dumps(pc, indent=1, ensure_ascii=False))
    SRC.write_text(json.dumps(src, indent=1, ensure_ascii=False))
    print("merged", sorted((new.get("days") or {}).keys()), "into", SRC.name)


def write_night(d, notes=None):
    """Last Night page data: docs/data/night.json plus a dated copy and an index of dates."""
    n = night.build(d, notes)
    if not n:
        return
    blob = json.dumps(n, ensure_ascii=False)
    (DATA / "nights").mkdir(parents=True, exist_ok=True)
    (DATA / "nights" / f"{n['date']}.json").write_text(blob)
    (DATA / "night.json").write_text(blob)
    dates = sorted(p.stem for p in (DATA / "nights").glob("20*.json"))
    (DATA / "nights" / "index.json").write_text(json.dumps(dates))
    print("wrote night", n["date"])


def write(a, publish):
    a["generated_at"] = datetime.now(ET).isoformat(timespec="minutes")
    blob = json.dumps(a, indent=1, ensure_ascii=False)
    if publish:
        (DATA / "league.json").write_text(blob)
        (DATA / "edition.json").write_text(json.dumps({"sample": False, "source": "yahoo-web",
                                                       "date": a["date"], "generated_at": a["generated_at"]}))
        (DATA / "history").mkdir(parents=True, exist_ok=True)
        (DATA / "history" / f"{a['date']}.json").write_text(blob)
        print("published", DATA / "league.json")
        write_night(analyze.d, (a.get("recap") or {}).get("night"))
        try:
            trades.write(analyze.d)
        except Exception as e:  # the analyzer never blocks the paper
            print("trades skipped:", e)
    else:
        a["preview"] = True
        (DATA / "preview").mkdir(parents=True, exist_ok=True)
        (DATA / "preview" / "league.json").write_text(json.dumps(a, indent=1, ensure_ascii=False))
        print("wrote", DATA / "preview" / "league.json")


def main():
    if "--merge" in sys.argv:
        merge(sys.argv[sys.argv.index("--merge") + 1])
        return
    today = sys.argv[sys.argv.index("--today") + 1] if "--today" in sys.argv else None
    a = analyze(json.loads(SRC.read_text()), today)
    if "--facts" in sys.argv:
        print(json.dumps(recap._facts(a), indent=1, ensure_ascii=False))
        return
    r = json.loads(RECAP.read_text()) if RECAP.exists() else {}
    a["recap"] = r if r.get("date") == a["date"] else recap.fallback(a)
    write(a, "--publish" in sys.argv)


if __name__ == "__main__":
    main()
