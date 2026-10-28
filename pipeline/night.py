"""Last Night: every rostered player's line from the latest night, ranked, with receipts.

    build(d, recap_notes=None) -> dict   # d is the same dict build.py / web_current.py make

Writes nothing itself; callers save it to docs/data/night.json and docs/data/nights/<date>.json.
Values are z-scores across 11 categories against every player-game in the data so far
(volume-aware for % categories, turnovers count against), the same Scorer the weekly lists use.
Commentary is built only from numbers in the lines, so nothing is invented.
"""
from .players import INACTIVE, Scorer, _add_line, _empty, _line, pct_map
from .stats import cmp_cat

import re
import unicodedata

REB = ("13", "14")
SUFFIX = {"jr", "sr", "ii", "iii", "iv"}


def _parts(name):
    s = unicodedata.normalize("NFD", str(name)).encode("ascii", "ignore").decode().lower()
    raw = s.replace("-", "").split()
    parts = [re.sub(r"[^a-z]", "", p) for p in raw]
    keep = [(p, r) for p, r in zip(parts, raw) if p and p not in SUFFIX]
    return keep


def key(name):
    """Name match across Yahoo and ESPN: full first + last name, accents, punctuation and suffixes
    dropped. An abbreviated first name ('N. Alexander-Walker') keys as initial + last name."""
    k = _parts(name)
    if not k:
        return ""
    if len(k) == 1:
        return k[0][0]
    first, raw_first = k[0]
    if len(first) == 1 or raw_first.endswith("."):
        return f"{first[0]}. {k[-1][0]}"
    return f"{first} {k[-1][0]}"


def matches(name, keys):
    """True if `name` (full name) matches any key in `keys` by full or abbreviated-first key."""
    k = key(name)
    if k in keys:
        return True
    parts = _parts(name)
    return len(parts) > 1 and f"{parts[0][0][0]}. {parts[-1][0]}" in keys


def owner(name, owned):
    k = key(name)
    if k in owned:
        return owned[k]
    parts = _parts(name)
    return owned.get(f"{parts[0][0][0]}. {parts[-1][0]}") if len(parts) > 1 else None


def _agg(stats, cats, pmap):
    a = _empty()
    played = _add_line(a, stats, cats, pmap)
    a["games"] = 1 if played else 0
    return a, played


def _desc(p):
    """'38 PTS, 7 3PTM, 12-21 FG' style summary from a display line."""
    L, bits = p["line"], []
    n = lambda v: int(v) if isinstance(v, float) and v.is_integer() else v  # the API hands back 41.0
    if L.get("12"):
        bits.append(f"{n(L['12'])} PTS")
    reb = (L.get("13") or 0) + (L.get("14") or 0)
    if reb >= 8:
        bits.append(f"{n(reb)} REB")
    if (L.get("16") or 0) >= 6:
        bits.append(f"{n(L['16'])} AST")
    if (L.get("10") or 0) >= 3:
        bits.append(f"{n(L['10'])} 3PTM")
    if (L.get("17") or 0) >= 3:
        bits.append(f"{n(L['17'])} STL")
    if (L.get("18") or 0) >= 3:
        bits.append(f"{n(L['18'])} BLK")
    fg = L.get("5")
    if fg:
        bits.append(f"{n(fg[1])}-{n(fg[2])} FG")
    return ", ".join(bits[:4])


def build(d, recap_notes=None):
    day = d["date"]
    cats = d["cats"]
    pmap = pct_map(cats, d.get("frac") or {})
    T = d["teams"]
    # reference: every player-game in the data so far
    ref = []
    for lines_by_team in (d.get("days") or {}).values():
        for lines in lines_by_team.values():
            for ln in lines:
                a, played = _agg(ln["stats"], cats, pmap)
                if played:
                    ref.append(a)
    if not ref:
        return None
    scorer = Scorer(ref, cats)

    rows = []
    for tk, lines in (d["days"].get(day) or {}).items():
        for ln in lines:
            a, played = _agg(ln["stats"], cats, pmap)
            if not played:
                continue
            z = scorer.z(a)
            rows.append({"name": ln["name"], "team": tk, "slot": ln.get("pos"), "nba": ln.get("nba", ""),
                         "result": ln.get("result", ""), "benched": ln.get("pos") in INACTIVE,
                         "value": round(sum(z.values()), 2), "z": {k: round(v, 2) for k, v in z.items()},
                         "line": _line(a, cats)})
    if not rows:
        return None
    rows.sort(key=lambda r: -r["value"])
    for r in rows:
        r["desc"] = _desc(r)
        best = sorted(r["z"], key=r["z"].get, reverse=True)
        good = {c["id"] for c in cats if c["hb"]}  # "0 TO" is not a highlight
        r["best"] = [c for c in best if c in good and r["z"][c] > 0.5][:3]
        r["worst"] = [c for c in sorted(r["z"], key=r["z"].get) if r["z"][c] < -0.5][:2]

    CAT_HB = {c["id"]: c["hb"] for c in cats}
    # free agents who balled: every NBA line that night from players no fantasy team had
    rostered = {key(r["name"]) for r in rows}
    owned = d.get("owned_now") or {}
    free = []
    for ln in d.get("nba_lines") or []:
        if matches(ln["name"], rostered):
            continue
        a, played = _agg(ln["stats"], cats, pmap)
        if not played:
            continue
        z = scorer.z(a)
        f = {"name": ln["name"], "nba": ln.get("nba", ""), "result": ln.get("result", ""), "min": ln.get("min"),
             "value": round(sum(z.values()), 2), "z": {c: round(v, 2) for c, v in z.items()},
             "line": _line(a, cats), "owned_by": owner(ln["name"], owned)}
        f["desc"] = _desc(f)
        f["best"] = [c for c in sorted(f["z"], key=f["z"].get, reverse=True) if CAT_HB.get(c) and f["z"][c] > 0.5][:3]
        free.append(f)
    free = [f for f in sorted(free, key=lambda f: -f["value"]) if f["value"] > 0][:8]

    started = [r for r in rows if not r["benched"]]
    stars = rows[:8]
    # bricks: starters who actively hurt (% categories on volume, turnovers), not quiet nights
    hurt = {c["id"] for c in cats if c["pct"] or not c["hb"]}
    for r in rows:
        r["damage"] = round(sum(v for k, v in r["z"].items() if k in hurt and v < 0), 2)
        r["worst"] = [k for k in sorted(r["z"], key=r["z"].get) if k in hurt and r["z"][k] < -0.5][:2]
    bricks = sorted([r for r in started if r["damage"] < -1], key=lambda r: r["damage"])[:5]
    bench = [r for r in rows if r["benched"] and r["value"] > 0][:6]

    # category kings: the best single line in each category (FG%/FT%/3PT% need volume)
    kings = []
    mins = {"5": 10, "8": 6, "11": 6}
    for c in cats:
        cid = c["id"]
        pool = []
        for r in rows:
            v = r["line"].get(cid)
            if v is None:
                continue
            if c["pct"]:
                if v[2] < mins.get(cid, 5):
                    continue
                pool.append((v[0], v[2], r))
            else:
                pool.append((v, 0, r))
        if not pool:
            continue
        if c["hb"]:
            best = max(pool, key=lambda x: (x[0], x[1]))
        else:  # turnovers: the worst offender is the story
            best = max(pool, key=lambda x: x[0])
        kings.append({"cat": cid, "name": c["name"], "player": best[2]["name"], "team": best[2]["team"],
                      "value": best[2]["line"].get(cid), "bad": not c["hb"]})

    # every fantasy team: the night's players and how the night went against that night's opponent
    # (on Mondays the night is Sunday, the final day of the previous week's matchup)
    opp = {}
    for m in d.get("night_matchups") or d.get("matchups") or []:
        opp[m["a"]], opp[m["b"]] = m["b"], m["a"]
    night_tot = {}
    for tk in T:
        a = _empty()
        for ln in (d["days"].get(day) or {}).get(tk, []):
            if ln.get("pos") in INACTIVE:
                continue
            _add_line(a, ln["stats"], cats, pmap)
        night_tot[tk] = {c["id"]: ((a["frac"].get(c["id"], (0, 0))[0] / a["frac"][c["id"]][1])
                                   if c["pct"] and a["frac"].get(c["id"], (0, 0))[1] else
                                   (None if c["pct"] else a["cnt"].get(c["id"], 0))) for c in cats}
    teams = []
    for tk in T:
        mine = [r for r in rows if r["team"] == tk]
        o = opp.get(tk)
        won = lost = tied = 0
        if o:
            for c in cats:
                res = cmp_cat(night_tot[tk].get(c["id"]), night_tot[o].get(c["id"]), c["hb"], c["pct"])
                won += res > 0; lost += res < 0; tied += res == 0
        teams.append({"team": tk, "opp": o, "night": [won, lost, tied], "players": len(mine),
                      "started": sum(not r["benched"] for r in mine),
                      "value": round(sum(r["value"] for r in mine if not r["benched"]), 2),
                      "bench_value": round(sum(r["value"] for r in mine if r["benched"]), 2),
                      "rows": mine})
    teams.sort(key=lambda t: -t["value"])
    for i, t in enumerate(teams):
        t["rank"] = i + 1

    results = sorted({r["result"] for r in rows if r["result"]})
    nm = lambda tk: T[tk]["name"]
    notes = list(recap_notes or [])
    if not notes:
        s = stars[0]
        notes.append(f"{s['name']} ran the night for {nm(s['team'])}: {s['desc']}.")
        if bricks:
            b = bricks[0]
            cn = {c["id"]: c["name"] for c in cats}
            hurt_txt = " and ".join(f"{b['line'][k][1]}-{b['line'][k][2]} {cn[k].replace('%', '')}" if isinstance(b["line"][k], list)
                                    else f"{b['line'][k]} {cn[k]}" for k in b["worst"])
            notes.append(f"Biggest brick: {b['name']} for {nm(b['team'])}, {hurt_txt or 'empty calories'}.")
        if bench:
            b = bench[0]
            notes.append(f"{nm(b['team'])} left {b['name']} on the bench for {b['desc']}.")
        top, low = teams[0], [t for t in teams if t["players"]]
        if low:
            notes.append(f"Best night: {nm(top['team'])}. Worst: {nm(low[-1]['team'])}.")
        if free:
            f = free[0]
            notes.append(f"Best free agent: {f['name']} ({f['desc']}). "
                         + (f"{f['owned_by']} already grabbed him." if f["owned_by"] else "Still sitting on the wire."))
        idle = [nm(t["team"]) for t in teams if not t["started"]]
        if idle:
            notes.append(f"Nobody started a game for {', '.join(idle)}.")
    games = {tuple(sorted((r["nba"], r["result"].split()[-1]))) for r in rows if r["result"] and r["nba"]}
    return {"date": day, "players": len(rows), "games": len(games) or None,
            "results": results, "notes": notes, "stars": stars, "free": free, "bricks": bricks, "bench": bench,
            "kings": kings, "teams": teams, "cats": cats,
            "team_names": {tk: {"name": T[tk]["name"], "manager": T[tk].get("manager", "")} for tk in T}}
