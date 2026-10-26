"""The Weekly Wrap: an editorial one-pager for the week that just ended (docs/wrap.html).

    python -m pipeline.wrap            # print the facts for the writer (week = last finished week)
    python -m pipeline.wrap --write    # write docs/data/wrap.json, wraps/week-N.json, wraps/index.json

Facts come from the same `d` / `a` the paper uses (web edition or API build). Editorial copy
(headline, lead, zingers, section titles in the week's theme) lives in archive/wrap_copy/week-N.json,
written by the Monday morning edition in recap.VOICE; without it the page uses templates.
Themes rotate by week from config/wrap_themes.json.
"""
import json
import sys
from pathlib import Path

from . import players as P
from . import stats
from .stats import cmp_cat

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
COPY = ROOT / "archive" / "wrap_copy"
THEMES = ROOT / "config" / "wrap_themes.json"
PRIOR = ROOT / "archive" / "2026_prior.json"
ANALYZER = ROOT / "labs" / "analyzer.json"
POLLS = DATA / "polls.json"
IL = {"IL", "IL+", "IR"}
OUT_CODES = {"O", "INJ", "D"}  # Yahoo tags that mean he is not playing (Q, P, GTD, DTD are maybes)


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def theme_for(week):
    t = _load(THEMES, {}) or {}
    return (t.get("weeks") or {}).get(str(week)) or t.get("default") or "western"


def score(sa, sb, cats):
    w = l = t = 0
    for c in cats:
        r = cmp_cat(sa.get(c["id"]), sb.get(c["id"]), c["hb"], c["pct"])
        w += r > 0; l += r < 0; t += r == 0
    return [w, l, t]


def _totals(days, ds, team):
    act = [x for dt in ds for x in (days.get(dt) or {}).get(team, []) if x["pos"] not in P.INACTIVE]
    out = {}
    for cid in ("10", "12", "13", "14", "16", "17", "18", "19"):
        out[cid] = sum(x["stats"].get(cid, 0) for x in act)
    for cid, fid in (("5", "9004003"), ("8", "9007006"), ("11", "9001002")):
        m = sum(x["stats"][fid][0] for x in act); a = sum(x["stats"][fid][1] for x in act)
        out[cid] = round(m / a, 3) if a else None
    return out


def build(d, a):
    w = (d.get("league") or {}).get("current_week", 1) - 1
    if w < 1 or w not in d.get("results", {}):
        return None
    cats, teams, days = d["cats"], d["teams"], d["days"]
    cname = {c["id"]: c["name"] for c in cats}
    win = (d.get("windows") or {}).get("last") or {}
    ds = sorted(x for x in days if win.get("start", "") <= x <= win.get("end", ""))
    pmap = P.pct_map(cats, d["frac"])
    # one scorer for the whole week so values are comparable
    pl, _ = P.aggregate(days, ds, cats, pmap)
    scorer = P.Scorer(list(pl.values()), cats)
    late = ds[-2:]
    late_pl, _ = P.aggregate(days, late, cats, pmap)

    prior = _load(PRIOR, {}) or {}
    cost = {r[0]: r[2] for r in prior.get("draft", [])}
    drafted_by = {r[0]: r[3] for r in prior.get("draft", [])}

    # ---------- results with the day-by-day trail ----------
    games = []
    for m in d["results"][w]:
        ta, tb = m["a"], m["b"]
        trail = [score(_totals(days, ds[:i + 1], ta), _totals(days, ds[:i + 1], tb), cats) for i in range(len(ds))]
        fin = score(m["a_stats"], m["b_stats"], cats)
        before = trail[-3] if len(trail) >= 3 else trail[0]  # score before the last two days
        mb, mf = before[0] - before[1], fin[0] - fin[1]
        lead_before = ta if mb > 0 else tb if mb < 0 else None
        winner = ta if mf > 0 else tb if mf < 0 else None
        if lead_before and winner and lead_before != winner:
            tag = "comeback"
        elif lead_before and not winner:
            tag = "blown"
        elif not lead_before and winner:
            tag = "late"
        else:
            tag = "held"
        # categories that changed hands over the last two days
        sb4a, sb4b = _totals(days, ds[:-2], ta), _totals(days, ds[:-2], tb)
        flips = []
        for c in cats:
            r0 = cmp_cat(sb4a.get(c["id"]), sb4b.get(c["id"]), c["hb"], c["pct"])
            r1 = cmp_cat(m["a_stats"].get(c["id"]), m["b_stats"].get(c["id"]), c["hb"], c["pct"])
            if r0 != r1:
                flips.append({"cat": c["name"], "to": ta if r1 > 0 else tb if r1 < 0 else None})
        gainer = ta if mf - mb > 0 else tb if mf - mb < 0 else None
        heroes = []
        if gainer:
            rows = [p for (tk, _), p in late_pl.items() if tk == gainer and p["games"]]
            rows.sort(key=lambda p: -scorer.value(p))
            heroes = [{"name": p["name"], "value": scorer.value(p), "line": P._line(p, cats)} for p in rows[:2]]
        games.append({"a": ta, "b": tb, "final": fin, "trail": trail, "before": before,
                      "swing": abs(mf - mb), "tag": tag, "winner": winner, "lead_before": lead_before,
                      "gainer": gainer, "flips": flips, "heroes": heroes})
    order = {"comeback": 0, "blown": 1, "late": 2, "held": 3}
    games.sort(key=lambda g: (order[g["tag"]], -g["swing"], -abs(g["final"][0] - g["final"][1])))

    # ---------- the week's best lines and the priciest duds ----------
    rows = P.top_players(pl, scorer, cats, n=len(pl))
    top = rows[:5]
    duds = [dict(r, cost=cost.get(r["name"])) for r in rows if (cost.get(r["name"]) or 0) >= 20 and r["games"] >= 2]
    duds = sorted(duds, key=lambda r: r["value"])[:3]
    for r in top:
        r["cost"] = cost.get(r["name"])

    # ---------- injuries: Yahoo's injury tags on the current rosters, IL slots, and IL stints this week ----------
    seen = {}
    for dt in ds:
        for tk, lines in days[dt].items():
            for x in lines:
                s = seen.setdefault((tk, x["name"]), {"il": 0, "first_il": None, "played": 0, "last": None})
                if x["pos"] in IL:
                    s["il"] += 1
                    s["first_il"] = s["first_il"] or dt
                elif any(v for v in x["stats"].values() if not isinstance(v, list)) or x["stats"]["9004003"][1]:
                    s["played"] += 1
                    s["last"] = dt
    R = (_load(POLLS, {}) or {}).get("rosters", {}).get("teams", {})
    by_id = {stats.team_id(k): k for k in teams}  # team number -> today's key (web or API)
    an = (_load(ANALYZER, {}) or {}).get("players", {})
    inj, done = [], set()
    for v in R.values():
        tk = by_id.get(str(v["team_id"]))
        if not tk:
            continue
        st, sl = v.get("status") or {}, v.get("slots") or {}
        for n in v.get("players", []):
            code = st.get(n, "")
            on_il = n in (v.get("il") or [])
            s = seen.get((tk, n), {})
            if not (code in OUT_CODES or on_il or s.get("il")):
                continue
            done.add((tk, n))
            slot = sl.get(n, "")
            inj.append({"team": tk, "name": n, "cost": cost.get(n), "status": code or ("IL" if on_il else ""),
                        "slot": slot, "played": s.get("played", 0), "missed": s.get("il", 0),
                        "first_il": s.get("first_il"), "last_game": s.get("last"),
                        "still_out": code in OUT_CODES or on_il,
                        # an injured player sitting in an active lineup slot right now
                        "started_hurt": code in ("O", "INJ") and slot not in P.INACTIVE and slot != "",
                        "gp_left": (an.get(n) or {}).get("gp")})
    rank = {"O": 0, "INJ": 0, "D": 1, "IL": 2, "Q": 3, "GTD": 3, "DTD": 3}
    inj.sort(key=lambda r: (not r["started_hurt"], rank.get(r["status"], 4), -(r["cost"] or 0)))

    # ---------- player lines for the page's stat pins (week and last two days) ----------
    # keyed "name|team" (a player can change teams mid-week); bare "name" = the stint with most games
    pins = {}
    for (tk, _), p in pl.items():
        if not p["games"]:
            continue
        z = scorer.z(p)
        e = {"team": tk, "games": p["games"], "line": P._line(p, cats), "best": sorted(z, key=z.get, reverse=True)[:3]}
        pins[f"{p['name']}|{tk}"] = e
        if p["name"] not in pins or pins[p["name"]]["games"] < e["games"]:
            pins[p["name"]] = e
    for (tk, _), p in late_pl.items():
        k = f"{p['name']}|{tk}"
        if p["games"] and k in pins:
            pins[k]["weekend"] = P._line(p, cats)

    # ---------- rest of year outlook ----------
    race = {r["team"]: r for r in ((a.get("race") or {}).get("rows") or [])}
    at = (_load(ANALYZER, {}) or {}).get("teams", {})
    model = (_load(ANALYZER, {}) or {}).get("model", {})
    prev = _load(DATA / "wraps" / f"week-{w - 1}.json", {}) or {}
    prev_out = {o["team"]: o for o in (prev.get("outlook") or [])}
    lw = a.get("last_week") or {}
    outlook = []
    for tk in teams:
        r, t = race.get(tk, {}), at.get(tk, {})
        st = d["standings"].get(tk, {})
        outlook.append({"team": tk, "w": st.get("w"), "l": st.get("l"), "t": st.get("t"), "rank": st.get("rank"),
                        "strength": t.get("strength"), "strong": [cname.get(c, c) for c in t.get("strong", [])],
                        "weak": [cname.get(c, c) for c in t.get("weak", [])],
                        "odds": r.get("playoff_odds"), "top_seed": r.get("top_seed_odds"),
                        "odds_move": r.get("odds_move"), "allplay": (lw.get("allplay") or {}).get(tk),
                        "prev_strength": (prev_out.get(tk) or {}).get("strength")})
    for i, o in enumerate(sorted(outlook, key=lambda o: -(o["strength"] or 0))):
        o["proj_rank"] = i + 1
    outlook.sort(key=lambda o: (-(o["odds"] or 0), o["proj_rank"]))

    # manager display names (Yahoo nicknames like "parth s" -> "Parth"), via polls.json rosters team_id
    pc = _load(POLLS, {}) or {}
    names = {m["id"]: m["name"] for m in pc.get("managers", [])}
    disp = {by_id[str(v["team_id"])]: names[mid] for mid, v in (pc.get("rosters") or {}).get("teams", {}).items()
            if mid in names and str(v["team_id"]) in by_id}
    return {
        "week": w, "start": win.get("start"), "end": win.get("end"), "days": ds, "theme": theme_for(w),
        "date": d.get("date"), "teams": {k: {"name": v["name"], "manager": disp.get(k, v["manager"])} for k, v in teams.items()},
        "games": games, "top": top, "duds": duds, "injuries": inj, "outlook": outlook, "pins": pins,
        "proj_as_of": model.get("proj_as_of"), "weeks_left": model.get("weeks_left"),
        "awards": {"mvp": lw.get("mvp"), "dud": lw.get("dud"), "robbed": lw.get("robbed"),
                   "luckiest": ((a.get("luck") or [{}])[0] or {}).get("team")},
        "moves": {k: v.get("moves", 0) for k, v in teams.items()},
    }


def attach_copy(x):
    c = _load(COPY / f"week-{x['week']}.json")
    x["copy"] = c if c and c.get("week") == x["week"] else None
    return x


def write(d, a, force=False):
    """Freezes a week once its copy has landed, so the Monday numbers the copy quotes don't drift
    as the daily paper reruns. --force rebuilds anyway."""
    x = build(d, a)
    if not x:
        print("wrap skipped: no finished week")
        return None
    old = _load(DATA / "wraps" / f"week-{x['week']}.json")
    if old and old.get("copy") and not force:
        x = old
    attach_copy(x)
    blob = json.dumps(x, ensure_ascii=False)
    (DATA / "wraps").mkdir(parents=True, exist_ok=True)
    (DATA / "wraps" / f"week-{x['week']}.json").write_text(blob)
    (DATA / "wrap.json").write_text(blob)
    weeks = sorted(int(p.stem.split("-")[1]) for p in (DATA / "wraps").glob("week-*.json"))
    (DATA / "wraps" / "index.json").write_text(json.dumps(weeks))
    print("wrote wrap week", x["week"], "copy" if x["copy"] else "template")
    return x


def main():
    from . import web_current as W
    src = json.loads(W.SRC.read_text())
    a = W.analyze(src)
    d = W.analyze.d
    if "--write" in sys.argv:
        write(d, a, "--force" in sys.argv)
    else:
        print(json.dumps(build(d, a), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
