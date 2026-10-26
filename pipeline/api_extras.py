"""The jobs the web morning edition did from Parth's Mac, rebuilt on the Yahoo API path so
`pipeline.build` (GitHub Actions) covers them once the official feed opens.

    rosters(y, teams, date)   -> polls.json rosters (players, IL, injury status, lineup slots) + owned_now
    free_agent_lines(y, lk, date, cat_ids, frac_ids) -> Yahoo's own free agents' lines that night (Free Agents Who Balled)
    espn_lines(date)          -> the same from ESPN box scores (fallback; the web edition's source)
    block_check(polls)        -> Trade Block listings whose player left the seller's roster
    status(ok, **fields)      -> docs/data/status.json heartbeat

Nothing here ever raises into the build: every entry point catches, prints a GitHub
`::warning::` annotation and returns an empty result.
"""
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import night
from .yahoo import coll, flat, parse_frac, parse_val

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
POLLS = DATA / "polls.json"
STATUS = DATA / "status.json"
ET = ZoneInfo("America/New_York")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
IL_SLOTS = {"IL", "IL+"}
MGR_ID = {1: "johnj", 2: "eshanb", 3: "adarsh", 4: "phil", 5: "chris", 6: "parth",
          7: "roshan", 8: "keshav", 9: "arnavb", 10: "vishal", 11: "sagar", 12: "jason"}


def warn(msg):
    print(f"::warning::{msg}")


def team_no(tk):
    return int(str(tk).rsplit(".t.", 1)[-1])


# ---------- rosters, injury tags and lineup slots ----------
def roster(y, tk, date):
    """One team's roster on `date`: name, slot, Yahoo status code (O, INJ, D, Q, GTD...) and NBA team."""
    tm = flat(y.get(f"team/{tk}/roster;date={date}/players")["team"])
    out = []
    for pcoll in coll(tm.get("roster", {}), "players"):
        for p in coll(pcoll, "player"):
            p = flat(p)
            nm = p.get("name", {})
            out.append({"name": nm.get("full") if isinstance(nm, dict) else str(nm),
                        "slot": flat(p.get("selected_position", [])).get("position") or "",
                        "status": (p.get("status") or "").upper(),
                        "status_full": p.get("status_full") or "",
                        "nba": (p.get("editorial_team_abbr") or "").upper()})
    return out


def rosters(y, teams, date, write=True):
    """Read every roster and store them in polls.json like the web edition's --merge did.
    Returns {night.key(name): team key} for Free Agents Who Balled, or {} on failure."""
    try:
        got = {}
        for tk in teams:
            got[tk] = roster(y, tk, date)
        if write:
            pc = json.loads(POLLS.read_text())
            R = pc.setdefault("rosters", {"season": "2026-27", "teams": {}})
            R["source"] = "yahoo-api"
            for tk, rows in got.items():
                if len(rows) < 10:
                    warn(f"roster {tk} has only {len(rows)} players; kept the old one")
                    continue
                mid = MGR_ID.get(team_no(tk))
                if not mid:
                    continue
                R["teams"][mid] = {
                    "team": teams[tk]["name"], "team_id": team_no(tk),
                    "players": [r["name"] for r in rows],
                    "il": [r["name"] for r in rows if r["slot"] in IL_SLOTS],
                    "status": {r["name"]: r["status"] for r in rows if r["status"]},
                    "slots": {r["name"]: r["slot"] for r in rows},
                }
                pc["players"] = sorted(set(pc.get("players", [])) | {r["name"] for r in rows})
            R["updated"] = datetime.now(ET).isoformat(timespec="minutes")
            POLLS.write_text(json.dumps(pc, indent=1, ensure_ascii=False))
            print("rosters: wrote", sum(len(v) for v in got.values()), "players to polls.json")
        return {night.key(r["name"]): tk for tk, rows in got.items() for r in rows}
    except Exception as e:
        warn(f"rosters skipped: {e}")
        return {}


# ---------- free agents who balled, straight from Yahoo ----------
def free_agent_lines(y, lk, date, cat_ids, frac_ids, count=50):
    """Lines for the free agents (status=A) who scored that night, in the shape night.py reads
    for d["nba_lines"]. Yahoo ranks them for the date, so no name matching against rosters.
    Pulls the top `count` by points plus the top 25 by assists, rebounds and blocks (dedup),
    so a stat-stuffer with a quiet scoring night still shows. Returns [] on failure."""
    sorts = [("12", count), ("16", 25), ("15", 25), ("18", 25)]  # PTS, AST, REB, BLK stat ids
    seen, out = set(), []
    try:
        for sid, n in sorts:
            start = 0
            while start < n:
                page = min(25, n - start)
                lg = flat(y.get(f"league/{lk}/players;status=A;sort={sid};sort_type=date;sort_date={date}"
                                f";start={start};count={page}/stats;type=date;date={date}")["league"])
                got = 0
                for p in coll(lg.get("players"), "player"):
                    p = flat(p)
                    got += 1
                    pk = p.get("player_key")
                    if pk in seen:
                        continue
                    stats = {}
                    for st in p.get("player_stats", {}).get("stats", []):
                        st = st.get("stat", st)
                        k = str(st.get("stat_id"))
                        if k in cat_ids:
                            stats[k] = parse_val(st.get("value"))
                        elif k in frac_ids:
                            stats[k] = parse_frac(st.get("value"))
                    if not any(v for v in stats.values() if not isinstance(v, list)) and \
                       not any(v[1] for v in stats.values() if isinstance(v, list)):
                        continue  # did not play
                    seen.add(pk)
                    nm = p.get("name", {})
                    out.append({"pk": pk, "name": nm.get("full") if isinstance(nm, dict) else str(nm), "pos": "FA",
                                "nba": (p.get("editorial_team_abbr") or "").upper(), "result": "", "stats": stats})
                if got < page:
                    break
                start += page
        print("yahoo free agents:", len(out), "lines for", date)
        return out
    except Exception as e:
        warn(f"free agent lines skipped: {e}")
        return []


# ---------- every NBA line that night from ESPN (fallback) ----------
def _pair(s):
    try:
        a, b = str(s).split("-")
        return int(a), int(b)
    except Exception:
        return 0, 0


def _num(s):
    try:
        return float(s)
    except Exception:
        return 0.0


def espn_rows(date, pause=0.5):
    """[name, nba, min, result, fgm, fga, ftm, fta, tpm, tpa, pts, oreb, dreb, ast, st, blk, to] for
    every player who logged minutes in a completed game on `date` (ET), best 60 by a rough score.
    Same row shape as pipeline/web_scrape.js, so night.py sees no difference."""
    sb = requests.get(f"{ESPN}/scoreboard", params={"dates": date.replace("-", "")}, timeout=30).json()
    rows = []
    for ev in sb.get("events") or []:
        if not (ev.get("status", {}).get("type", {}).get("completed")):
            continue
        comp = ev["competitions"][0]["competitors"]
        s = requests.get(f"{ESPN}/summary", params={"event": ev["id"]}, timeout=30).json()
        for t in (s.get("boxscore") or {}).get("players") or []:
            ab = t["team"]["abbreviation"]
            me = next((c for c in comp if c["team"]["abbreviation"] == ab), None)
            opp = next((c for c in comp if c is not me), None)
            res = ""
            if me and opp:
                res = f"{'W' if me.get('winner') else 'L'}, {me.get('score')}-{opp.get('score')} " \
                      f"{'vs' if me.get('homeAway') == 'home' else '@'} {opp['team']['abbreviation']}"
            st = t["statistics"][0]
            ix = {k: i for i, k in enumerate(st.get("labels") or [])}
            for a in st.get("athletes") or []:
                stats = a.get("stats") or []
                if a.get("didNotPlay") or not stats:
                    continue
                v = lambda k: stats[ix[k]] if k in ix and ix[k] < len(stats) else "0"
                if not _num(v("MIN")) > 0:
                    continue
                fgm, fga = _pair(v("FG")); tpm, tpa = _pair(v("3PT")); ftm, fta = _pair(v("FT"))
                rows.append([a["athlete"]["displayName"], ab, _num(v("MIN")), res, fgm, fga, ftm, fta, tpm, tpa,
                             _num(v("PTS")), _num(v("OREB")), _num(v("DREB")), _num(v("AST")),
                             _num(v("STL")), _num(v("BLK")), _num(v("TO"))])
        time.sleep(pause)
    rough = lambda r: r[10] + 1.2 * (r[11] + r[12]) + 1.5 * r[13] + 3 * (r[14] + r[15]) + r[8] - r[16]
    return sorted(rows, key=rough, reverse=True)[:60]


def espn_lines(date):
    """`d["nba_lines"]` in the shape night.py reads, or [] on failure."""
    from .web_current import line
    cols = ["slot", "name", "pid", "nba", "pos", "result", "fgm", "fga", "ftm", "fta", "tpm", "tpa",
            "pts", "oreb", "dreb", "ast", "st", "blk", "to"]
    try:
        rows = espn_rows(date)
        out = [{**line(["FA", r[0], "", r[1], "", r[3], *r[4:]], cols), "min": r[2]} for r in rows]
        print("espn:", len(out), "lines for", date)
        return out
    except Exception as e:
        warn(f"espn lines skipped: {e}")
        return []


# ---------- trade block: a listed player who left the seller's roster ----------
def block_check(polls=None):
    """Returns [{who, player}] for listings that no longer match the seller's roster; [] when clean."""
    try:
        pc = polls or json.loads(POLLS.read_text())
        r = requests.get(pc["endpoint"], params={"block": 1}, timeout=30, allow_redirects=True)
        r.raise_for_status()
        listings = (r.json() or {}).get("listings") or []
        R = pc.get("rosters", {}).get("teams", {})
        bad = []
        for L in listings:
            who, player = L.get("who"), L.get("player")
            have = {night.key(n) for n in R.get(who, {}).get("players", [])}
            if have and not night.matches(player, have):
                bad.append({"who": who, "player": player})
        for b in bad:
            warn(f"Trade Block: {b['player']} is listed by {b['who']} but is no longer on that roster")
        print("block check:", len(listings), "listings,", len(bad), "stale")
        return bad
    except Exception as e:
        warn(f"block check skipped: {e}")
        return []


# ---------- heartbeat ----------
def status(ok, **fields):
    """docs/data/status.json: {ok, edition, generated_at, ...}. Read by the daily check and the footer."""
    try:
        now = datetime.now(ET).isoformat(timespec="minutes")
        STATUS.write_text(json.dumps({"ok": bool(ok), "generated_at": now, **fields}, indent=1))
    except Exception as e:
        warn(f"status.json not written: {e}")
