"""Evening edition: tonight's lineup card. Run after lineups lock (about 7:15pm ET).

    python -m pipeline.tonight            # live
    python -m pipeline.tonight --sample   # fake data for design work

Reads today's rosters (who is benched with a game, who is starting while listed out,
empty slots), today's adds and drops, and the live matchup scores. Writes
docs/data/tonight.json. The home page uses it to refresh the scoreboard and show the
Lineup Card until the 4am edition replaces it.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import recap
from .yahoo import coll, flat

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "tonight.json"
OUT_STATUSES = {"O", "OUT", "INJ", "IL", "IL+", "SUSP", "NA"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ABBR = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX", "BKN": "BKN"}


def teams_playing_today(date):
    """NBA teams with a game today, by Yahoo-style abbreviation. Empty set if the lookup fails."""
    try:
        r = requests.get(ESPN, params={"dates": date.replace("-", "")}, timeout=20)
        r.raise_for_status()
        out = set()
        for ev in r.json().get("events", []):
            for c in ev.get("competitions", [{}])[0].get("competitors", []):
                ab = (c.get("team", {}).get("abbreviation") or "").upper()
                out.add(ab)
                out.add(ABBR.get(ab, ab))
        return out
    except Exception as e:
        print("schedule lookup failed:", e)
        return set()


def roster_today(y, tk, date):
    tm = flat(y.get(f"team/{tk}/roster;date={date}/players")["team"])
    out = []
    for pcoll in coll(tm.get("roster", {}), "players"):
        for p in coll(pcoll, "player"):
            p = flat(p)
            nm = p.get("name", {})
            out.append({"name": nm.get("full") if isinstance(nm, dict) else str(nm),
                        "pos": flat(p.get("selected_position", [])).get("position"),
                        "status": (p.get("status") or "").upper(),
                        "nba": (p.get("editorial_team_abbr") or "").upper()})
    return out


def lineup_flags(roster, playing):
    bench_game = [p["name"] for p in roster if p["pos"] == "BN" and p["status"] == "" and (not playing or p["nba"] in playing)]
    starting_out = [{"player": p["name"], "status": p["status"]} for p in roster
                    if p["pos"] not in ("BN", "IL", "IL+", None) and p["status"] in OUT_STATUSES]
    empty = 0  # Yahoo omits empty slots from the roster list; count active slots short of the usual 10
    active = [p for p in roster if p["pos"] not in ("BN", "IL", "IL+", None)]
    if active and len(active) < 10:
        empty = 10 - len(active)
    return {"benched_with_game": bench_game[:3], "starting_out": starting_out[:3], "empty_slots": empty,
            "schedule_known": bool(playing)}


def fetch_live():
    from .yahoo import Yahoo, load_dotenv
    load_dotenv()
    y = Yahoo(os.environ["YAHOO_CLIENT_ID"], os.environ["YAHOO_CLIENT_SECRET"], os.environ["YAHOO_REFRESH_TOKEN"])
    lk = y.league_key(os.environ["YAHOO_LEAGUE_ID"])
    meta = y.meta(lk)
    cats = y.cats(lk)
    ids = {c["id"] for c in cats}
    teams, _ = y.teams_and_standings(lk)
    now = datetime.now(ZoneInfo("America/New_York"))
    today = now.date().isoformat()
    playing = teams_playing_today(today)
    lineups = {}
    for tk in teams:
        try:
            lineups[tk] = lineup_flags(roster_today(y, tk, today), playing)
        except Exception as e:
            print("roster fetch failed", tk, e)
    start_of_day = int(datetime.combine(now.date(), datetime.min.time(), tzinfo=ZoneInfo("America/New_York")).timestamp())
    try:
        moves = [x for x in y.transactions(lk, 60) if x["ts"] >= start_of_day]
    except Exception as e:
        print("transactions fetch failed:", e)
        moves = []
    return {"date": today, "week": meta["current_week"], "cats": cats, "teams": teams,
            "matchups": y.scoreboard(lk, meta["current_week"], ids), "lineups": lineups, "moves_today": moves}


def sample():
    from .sample import make
    d = make()
    tks = list(d["teams"])
    lineups = {tk: {"benched_with_game": [], "starting_out": [], "empty_slots": 0, "schedule_known": True} for tk in tks}
    lineups[tks[2]]["benched_with_game"] = ["Rookie McBucket"]
    lineups[tks[5]]["starting_out"] = [{"player": "Glass Eater", "status": "O"}]
    lineups[tks[7]]["empty_slots"] = 1
    lineups[tks[9]]["benched_with_game"] = ["Point God", "Stretch Five"]
    return {"date": d["date"], "week": d["league"]["current_week"], "cats": d["cats"], "teams": d["teams"],
            "matchups": d["matchups"], "lineups": lineups,
            "moves_today": [{"team": tks[1], "kind": "add", "player": "Two Way Tony"}, {"team": tks[1], "kind": "drop", "player": "Corner Carl"}]}


def main():
    is_sample = "--sample" in sys.argv
    t = sample() if is_sample else fetch_live()
    card = recap.tonight_fallback(t) if is_sample else recap.tonight(t)
    out = {"date": t["date"], "week": t["week"], "sample": is_sample,
           "generated_at": datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="minutes"),
           "matchups": t["matchups"], "lineups": t["lineups"], "moves_today": t["moves_today"],
           "headline": card.get("headline"), "zingers": card.get("zingers", [])}
    OUT.write_text(json.dumps(out, indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
