"""The Wire data: free agents, injuries, minutes and the schedule ahead. Runs after the morning paper.

    python -m pipeline.wire            # live (Yahoo login + ESPN)
    python -m pipeline.wire --no-yahoo # ESPN parts only

Writes, under cache/wire/:
  fa.json          Yahoo free agents (status=A): top 100 by rank over the last week and last month,
                   each with last-week, last-month and season totals and the injury tag.
  injuries.json    ESPN league injury report.
  schedule.json    {date: [teams]} for the next 21 days (ESPN scoreboard).
  league.json      Yahoo league settings: roster slots and their counts, any games-played cap, stat categories.
  rosters.json     Yahoo rosters for all teams today: each player's eligible positions, today's lineup slot and status.
  box/<date>.json  ESPN box scores for every game that night: minutes, starter flag and the stat line
                   for every player (kept 30 days, fetched once per date).
No analysis happens here; consumers rank the data their own way.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "cache" / "wire"
BOX = OUT / "box"
ET = ZoneInfo("America/New_York")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ABBR = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}
BOX_DAYS = 30
AHEAD = 21
TEAMNAME = {}  # ESPN display name -> abbreviation, filled from scoreboards


def norm(ab):
    ab = (ab or "").upper()
    return ABBR.get(ab, ab)


def _get(url, params=None, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=25)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def scoreboard(date):
    return _get(f"{ESPN}/scoreboard", {"dates": date.replace("-", ""), "limit": 50}).get("events", [])


def schedule_ahead(today):
    out, pairs = {}, {}
    for i in range(AHEAD):
        d = (today + timedelta(days=i)).isoformat()
        try:
            teams = set(); games = []
            for ev in scoreboard(d):
                cs = ev.get("competitions", [{}])[0].get("competitors", [])
                hv = {c.get("homeAway"): norm(c.get("team", {}).get("abbreviation")) for c in cs}
                if hv.get("home") and hv.get("away"):
                    games.append([hv["away"], hv["home"]])
                for c in cs:
                    t = c.get("team", {})
                    teams.add(norm(t.get("abbreviation")))
                    if t.get("displayName"):
                        TEAMNAME[t["displayName"]] = norm(t.get("abbreviation"))
            out[d] = sorted(teams); pairs[d] = games
        except Exception as e:
            print("schedule lookup failed for", d, e)
    return out, pairs


def _line(labels, vals):
    m = dict(zip(labels, vals))
    def pair(k):
        try:
            a, b = str(m.get(k, "0-0")).split("-")
            return [int(a), int(b)]
        except Exception:
            return [0, 0]
    def n(k):
        try:
            return int(str(m.get(k, "0")).replace("+", ""))
        except Exception:
            return 0
    fg, tp, ft = pair("FG"), pair("3PT"), pair("FT")
    mn = str(m.get("MIN", "0"))
    try:
        mins = float(mn.split(":")[0]) + (float(mn.split(":")[1]) / 60 if ":" in mn else 0)
    except Exception:
        mins = 0.0
    return {"min": round(mins, 1), "fgm": fg[0], "fga": fg[1], "tpm": tp[0], "tpa": tp[1], "ftm": ft[0], "fta": ft[1],
            "pts": n("PTS"), "oreb": n("OREB"), "dreb": n("DREB"), "ast": n("AST"), "stl": n("STL"),
            "blk": n("BLK"), "to": n("TO")}


def box_scores(date):
    """Every player's line for every finished game on date, or None if any game isn't final yet."""
    games, pairs = [], []
    for ev in scoreboard(date):
        state = ev.get("status", {}).get("type", {}).get("state")
        if state != "post":
            return None
        cs = ev.get("competitions", [{}])[0].get("competitors", [])
        hv = {c.get("homeAway"): norm(c.get("team", {}).get("abbreviation")) for c in cs}
        pairs.append([hv.get("away"), hv.get("home")])
        s = _get(f"{ESPN}/summary", {"event": ev["id"]})
        for team in s.get("boxscore", {}).get("players", []):
            ab = norm(team.get("team", {}).get("abbreviation"))
            opp = hv.get("home") if ab == hv.get("away") else hv.get("away")
            for st in team.get("statistics", []):
                labels = st.get("labels") or st.get("names") or []
                for a in st.get("athletes", []):
                    ath = a.get("athlete", {})
                    row = {"name": ath.get("displayName"), "espn_id": ath.get("id"), "team": ab, "opp": opp,
                           "pos": (ath.get("position") or {}).get("abbreviation"), "starter": bool(a.get("starter")),
                           "dnp": bool(a.get("didNotPlay")), "reason": a.get("reason") or ""}
                    if a.get("stats"):
                        row.update(_line(labels, a["stats"]))
                    games.append(row)
        time.sleep(0.2)
    return {"date": date, "games": pairs, "players": games}


def injuries():
    data = _get(f"{ESPN}/injuries")
    out = []
    for team in data.get("injuries", []):
        tname = team.get("displayName")
        for inj in team.get("injuries", []):
            ath = inj.get("athlete", {})
            det = inj.get("details", {}) or {}
            out.append({"name": ath.get("displayName"), "team": norm((ath.get("team") or {}).get("abbreviation")) or TEAMNAME.get(tname, tname),
                        "team_name": tname, "pos": (ath.get("position") or {}).get("abbreviation"),
                        "status": inj.get("status"), "date": inj.get("date"), "type": det.get("type"),
                        "return": det.get("returnDate"), "short": inj.get("shortComment"), "long": inj.get("longComment")})
    return out


def yahoo_fa(y, lk):
    from .yahoo import coll, flat
    out = {}
    for sort_type in ("lastweek", "lastmonth"):
        for start in range(0, 100, 25):
            for stype in ("lastweek", "lastmonth", "season"):
                lg = flat(y.get(f"league/{lk}/players;status=A;sort=AR;sort_type={sort_type};start={start};count=25"
                                f"/stats;type={stype}")["league"])
                for p in coll(lg.get("players"), "player"):
                    p = flat(p)
                    nm = p.get("name", {})
                    e = out.setdefault(p.get("player_key"), {
                        "name": nm.get("full") if isinstance(nm, dict) else str(nm),
                        "nba": norm(p.get("editorial_team_abbr")), "pos": p.get("display_position"),
                        "status": p.get("status", ""), "stats": {}})
                    e["stats"][stype] = {str(s.get("stat", s).get("stat_id")): s.get("stat", s).get("value")
                                         for s in p.get("player_stats", {}).get("stats", [])}
    return list(out.values())


def _positions(v):
    """Yahoo eligible_positions comes as a list of {position: X} (or one dict); return ['PG', 'G', ...]."""
    out = []
    if isinstance(v, dict):
        v = v.get("position") if isinstance(v.get("position"), list) else [v]
    for e in v or []:
        if isinstance(e, dict) and e.get("position"):
            out.append(e["position"])
        elif isinstance(e, str):
            out.append(e)
    return out


def yahoo_league(y, lk):
    from .yahoo import flat
    s = flat(flat(y.get(f"league/{lk}/settings")["league"])["settings"])
    slots = {}
    for rp in s.get("roster_positions") or []:
        rp = rp.get("roster_position", rp)
        slots[rp.get("position")] = int(rp.get("count") or 0)
    keep = {k: s.get(k) for k in ("max_games_played", "max_weekly_adds", "max_adds", "weekly_deadline",
                                  "trade_end_date", "uses_playoff", "playoff_start_week", "scoring_type")}
    return {"slots": slots, "settings": keep}


def yahoo_rosters(y, lk, date, n_teams=12):
    from .yahoo import coll, flat
    out = {}
    for i in range(1, n_teams + 1):
        tk = f"{lk}.t.{i}"
        tm = flat(y.get(f"team/{tk}/roster;date={date}")["team"])
        players = []
        for pcoll in coll(tm.get("roster", {}), "players"):
            for p in coll(pcoll, "player"):
                p = flat(p)
                nm = p.get("name", {})
                players.append({"name": nm.get("full") if isinstance(nm, dict) else str(nm), "pk": p.get("player_key"),
                                "nba": norm(p.get("editorial_team_abbr")), "eligible": _positions(p.get("eligible_positions")),
                                "slot": flat(p.get("selected_position", [])).get("position"),
                                "status": (p.get("status") or "").upper()})
        out[tk] = {"name": tm.get("name"), "players": players}
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    BOX.mkdir(parents=True, exist_ok=True)
    now = datetime.now(ET)
    today = now.date()
    report = []

    try:
        sched, pairs = schedule_ahead(today)
        json.dump({"generated_at": now.isoformat(timespec="minutes"), "dates": sched, "games": pairs}, open(OUT / "schedule.json", "w"))
        report.append(f"schedule {len(sched)} days")
    except Exception as e:
        report.append(f"schedule FAILED {e}")

    try:
        inj = injuries()
        json.dump({"generated_at": now.isoformat(timespec="minutes"), "injuries": inj}, open(OUT / "injuries.json", "w"))
        report.append(f"injuries {len(inj)}")
    except Exception as e:
        report.append(f"injuries FAILED {e}")

    got = 0
    for i in range(1, BOX_DAYS + 1):
        d = (today - timedelta(days=i)).isoformat()
        f = BOX / f"{d}.json"
        if f.exists():
            try:
                if "games" in json.load(open(f)):
                    continue  # already has opponents
            except Exception:
                pass
        try:
            b = box_scores(d)
            if b is not None:
                json.dump(b, open(f, "w"), separators=(",", ":"))
                got += 1
        except Exception as e:
            report.append(f"box {d} FAILED {e}")
    for f in BOX.glob("*.json"):
        if f.stem < (today - timedelta(days=BOX_DAYS + 2)).isoformat():
            f.unlink()
    report.append(f"box +{got} days ({len(list(BOX.glob('*.json')))} kept)")

    if "--no-yahoo" not in sys.argv:
        try:
            from .yahoo import Yahoo, load_dotenv
            load_dotenv()
            y = Yahoo(os.environ["YAHOO_CLIENT_ID"], os.environ["YAHOO_CLIENT_SECRET"], os.environ["YAHOO_REFRESH_TOKEN"])
            lk = y.league_key(os.environ["YAHOO_LEAGUE_ID"])
            fa = yahoo_fa(y, lk)
            json.dump({"generated_at": now.isoformat(timespec="minutes"), "players": fa}, open(OUT / "fa.json", "w"))
            report.append(f"free agents {len(fa)}")
        except Exception as e:
            report.append(f"free agents FAILED {e}")
            y = lk = None
        if y is not None:
            try:
                lg = yahoo_league(y, lk)
                json.dump({"generated_at": now.isoformat(timespec="minutes"), **lg}, open(OUT / "league.json", "w"))
                report.append("slots " + ",".join(f"{k}{v}" for k, v in lg["slots"].items())
                              + f"; max_games_played={lg['settings'].get('max_games_played')}")
            except Exception as e:
                report.append(f"league settings FAILED {e}")
            try:
                ro = yahoo_rosters(y, lk, today.isoformat())
                json.dump({"generated_at": now.isoformat(timespec="minutes"), "date": today.isoformat(), "teams": ro},
                          open(OUT / "rosters.json", "w"))
                elig = sum(1 for t in ro.values() for p in t["players"] if p["eligible"])
                report.append(f"rosters {len(ro)} teams, {elig} players with eligibility")
            except Exception as e:
                report.append(f"rosters FAILED {e}")

    msg = "; ".join(report)
    print(msg)
    bad = "FAILED" in msg
    if os.environ.get("GITHUB_ACTIONS"):
        print(("::warning::" if bad else "::notice::") + "wire: " + msg)


if __name__ == "__main__":
    main()
