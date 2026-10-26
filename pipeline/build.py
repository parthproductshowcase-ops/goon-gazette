"""Daily build: Yahoo -> stats -> recap -> docs/data/league.json

    python -m pipeline.build            # live (needs env vars below)
    python -m pipeline.build --sample   # fake league, no credentials

Env: YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET, YAHOO_REFRESH_TOKEN, YAHOO_LEAGUE_ID,
     ANTHROPIC_API_KEY (optional; template recap without it)
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import api_extras, night, players, polls, recap, stats

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data"
CACHE = ROOT / "cache" / "days"


def fetch_live():
    from .yahoo import Yahoo, load_dotenv
    load_dotenv()
    y = Yahoo(os.environ["YAHOO_CLIENT_ID"], os.environ["YAHOO_CLIENT_SECRET"],
              os.environ["YAHOO_REFRESH_TOKEN"])
    lk = y.league_key(os.environ["YAHOO_LEAGUE_ID"])
    meta = y.meta(lk)
    cats = y.cats(lk)
    ids = {c["id"] for c in cats}
    teams, standings = y.teams_and_standings(lk)
    yesterday = (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).date().isoformat()

    po = y.playoff_settings(lk)
    reg_end = min(po["playoff_start_week"] - 1, meta["end_week"])
    weekly, results = {}, {}
    for wk in range(meta["start_week"], meta["current_week"]):
        results[wk] = y.scoreboard(lk, wk, ids)
        weekly[wk] = {}
        for m in results[wk]:
            weekly[wk][m["a"]] = m["a_stats"]
            weekly[wk][m["b"]] = m["b_stats"]
    # remaining regular-season pairings (Yahoo lists future matchups on the scoreboard)
    schedule = {}
    for wk in range(meta["current_week"], reg_end + 1):
        schedule[wk] = [[m["a"], m["b"]] for m in y.scoreboard(lk, wk, set())]
    now = datetime.now(ZoneInfo("America/New_York"))
    week_progress = round(now.weekday() / 7, 2)  # Yahoo NBA weeks run Monday to Sunday

    # player-level data: every roster's daily stat lines, cached one file per finished day
    frac = y.frac_ids(lk)
    gw = y.game_weeks(lk.split(".")[0])
    cur = meta["current_week"]
    windows = {}
    if cur - 1 in gw:
        windows["last"] = {"week": cur - 1, **gw[cur - 1]}
    if cur in gw:
        windows["current"] = {"week": cur, "start": gw[cur]["start"], "end": min(gw[cur]["end"], yesterday)}
    days = {}
    want = set()
    for w in windows.values():
        if w["end"] >= w["start"]:
            want.update(players.date_range(w["start"], w["end"]))
    want.add(yesterday)
    if meta.get("draft_status") != "postdraft":
        want = set()  # no rosters to read before the draft
    CACHE.mkdir(parents=True, exist_ok=True)
    for dt in sorted(want):
        f = CACHE / f"{dt}.json"
        if f.exists():
            days[dt] = json.loads(f.read_text())
            continue
        days[dt] = {}
        for tk in teams:
            try:
                days[dt][tk] = y.roster_day(tk, dt, ids, frac)
            except Exception as e:
                print("roster fetch failed", tk, dt, e)
        f.write_text(json.dumps(days[dt]))
    try:
        transactions = y.transactions(lk)
    except Exception as e:
        print("transactions fetch failed:", e)
        transactions = []

    preseason = meta.get("draft_status") != "postdraft"
    d = {
        "league": meta, "cats": cats, "teams": teams, "standings": standings,
        "weekly": weekly, "results": results, "schedule": schedule, "date": yesterday,
        "num_playoff_teams": po["num_playoff_teams"], "reg_end": reg_end, "week_progress": week_progress,
        "matchups": [] if preseason else y.scoreboard(lk, meta["current_week"], ids),
        "season": {} if preseason else y.team_stats(lk, ids, "season"),
        "daily": {} if preseason else y.team_stats(lk, ids, "date", yesterday),
        "days": days, "windows": windows, "transactions": transactions, "frac": frac,
    }
    d["bench"] = players.bench_from_day(d, yesterday)
    # the night being reported can belong to the week that just ended (Monday's paper covers
    # Sunday, the last day of the old matchup), so pair opponents by the night's own week
    nw = cur
    if cur in gw and yesterday < gw[cur]["start"] and (cur - 1) in results:
        nw = cur - 1
    d["night_week"] = nw
    d["night_matchups"] = ([{"a": m["a"], "b": m["b"]} for m in results[nw]] if nw in results
                           else [{"a": m["a"], "b": m["b"]} for m in d["matchups"]])
    if not preseason:
        # rosters, injury tags and lineup slots into polls.json (Trade Block, Boot Hill, Lineup Card),
        # plus who owns whom and every NBA line last night for Free Agents Who Balled
        today = now.date().isoformat()
        d["owned_now"] = api_extras.rosters(y, teams, today)
        d["nba_lines"] = (api_extras.free_agent_lines(y, lk, yesterday, ids, frac)
                          or api_extras.espn_lines(yesterday))
    return d, preseason


def main():
    if "--sample" in sys.argv:
        _main()
        return
    try:
        warnings = _main()
        api_extras.status(True, edition="morning", source="yahoo-api", warnings=warnings)
    except Exception as e:
        api_extras.status(False, edition="morning", source="yahoo-api", error=f"{type(e).__name__}: {e}")
        raise


def _main():
    sample = "--sample" in sys.argv
    warnings = []
    if sample:
        from .sample import make
        d, preseason = make(), False
    else:
        d, preseason = fetch_live()

    hist = OUT / "history"
    hist.mkdir(parents=True, exist_ok=True)
    prev_files = sorted(p for p in hist.glob("*.json") if p.stem < d["date"])
    prev = json.loads(prev_files[-1].read_text()) if prev_files else None

    if preseason:
        a = {"league": d["league"], "date": d["date"], "preseason": True,
             "cats": d["cats"], "teams": d["teams"], "standings": d["standings"],
             "power": [], "luck": [], "punts": [], "matchups": [], "daily": None,
             "bench": [], "weeks_played": 0,
             "recap": {"headline": "TIP-OFF IS COMING",
                       "subhead": "The presses roll once the draft is done.",
                       "body": [f"{len(d['teams'])} teams, {len(d['cats'])} categories, zero excuses."],
                       "zingers": []}}
    else:
        a = stats.analyze(d, prev)
        a["night_week"] = d.get("night_week")
        if not sample:
            polls.snapshot()
            a["picks"] = polls.facts(a)
        # Last Night first (computed), so the recap can write its five notes from the same facts
        n = night.build(d) if d.get("days") else None
        a["recap"] = d["recap"] if sample else recap.write(a, n)

    a["generated_at"] = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="minutes")
    blob = json.dumps(a, indent=1)
    (OUT / "league.json").write_text(blob)
    # tiny flag file every page reads: while "sample" is true the current season shows as locked
    (OUT / "edition.json").write_text(json.dumps({"sample": bool(a.get("sample") or sample),
                                                  "date": d["date"], "generated_at": a["generated_at"]}))
    if not sample:
        (hist / f"{d['date']}.json").write_text(blob)
        stale = api_extras.block_check()
        warnings += [f"Trade Block: {b['player']} ({b['who']}) left the roster" for b in stale]
        if d.get("days"):
            from .web_current import write_night
            write_night(d, (a.get("recap") or {}).get("night"))
            try:
                from . import trades
                trades.write(d)
            except Exception as e:  # the analyzer never blocks the paper
                print("trades skipped:", e)
            try:
                from . import wrap
                wrap.write(d, a)
            except Exception as e:  # the wrap never blocks the paper
                print("wrap skipped:", e)
            try:
                from . import top50
                top50.write(d)
            except Exception as e:  # The Fifty never blocks the paper
                print("top50 skipped:", e)
    print("wrote", OUT / "league.json")
    return warnings


if __name__ == "__main__":
    main()
