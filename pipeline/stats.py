"""Category-league analytics. Pure Python, no Yahoo knowledge.

Input `d` (internal format):
  league   {name, season, current_week, ...}
  cats     [{id, name, hb (higher is better), pct}]
  teams    {tk: {name, manager, logo}}
  standings{tk: {rank, w, l, t}}        # category W-L-T, as Yahoo H2H cats reports it
  weekly   {week: {tk: {cat: val}}}     # completed weeks only
  matchups [{week, a, b, a_stats, b_stats}]   # current week, live
  season   {tk: {cat: val}}
  daily    {tk: {cat: val}}             # yesterday
  bench    {tk: [{player, stats}]}      # yesterday's benched producers
  date     'YYYY-MM-DD' (the day being recapped)
"""
from statistics import mean, pstdev


def _z(vals, hb):
    clean = [v for v in vals.values() if v is not None]
    if len(clean) < 2:
        return {t: 0.0 for t in vals}
    mu, sd = mean(clean), pstdev(clean)
    if sd == 0:
        return {t: 0.0 for t in vals}
    sign = 1 if hb else -1
    return {t: (sign * (v - mu) / sd if v is not None else 0.0) for t, v in vals.items()}


def zscores(table, cats, teams):
    return {c["id"]: _z({t: (table.get(t) or {}).get(c["id"]) for t in teams}, c["hb"]) for c in cats}


def cmp_cat(a, b, hb, pct=False):
    """1 if a wins the category, -1 if b, 0 tie/unknown."""
    if a is None or b is None:
        return 0
    if abs(a - b) < (1e-4 if pct else 1e-9):
        return 0
    return (1 if a > b else -1) * (1 if hb else -1)


def is_swing(a, b, c):
    if a is None or b is None:
        return False
    diff = abs(a - b)
    if c["pct"]:
        return diff <= 0.015
    big = max(abs(a), abs(b), 1)
    return diff <= 2 or diff / big <= 0.06


def _pct(w, l, t):
    g = w + l + t
    return (w + 0.5 * t) / g if g else None


def power_rankings(d, prev):
    teams, cats = list(d["teams"]), d["cats"]
    zs = zscores(d["season"], cats, teams)
    weeks = sorted(d["weekly"])
    zr = zscores(d["weekly"][weeks[-1]], cats, teams) if weeks else zs
    prev_rank = {p["team"]: p["rank"] for p in (prev or {}).get("power", [])}
    rows = []
    for t in teams:
        s = sum(zs[c["id"]][t] for c in cats)
        r = sum(zr[c["id"]][t] for c in cats)
        rows.append({
            "team": t, "score": round(0.7 * s + 0.3 * r, 2), "season_z": round(s, 2),
            "recent_z": round(r, 2),
            "cat_z": {c["id"]: round(zs[c["id"]][t], 2) for c in cats},
        })
    rows.sort(key=lambda x: -x["score"])
    for i, row in enumerate(rows, 1):
        row["rank"] = i
        row["move"] = (prev_rank[row["team"]] - i) if row["team"] in prev_rank else 0
        z = row["cat_z"]
        row["best_cat"] = max(z, key=z.get) if z else None
        row["worst_cat"] = min(z, key=z.get) if z else None
    return rows, zs


def all_play(d):
    """Every team vs every team, every completed week, category by category."""
    cats, teams = d["cats"], list(d["teams"])
    rec = {t: [0, 0, 0] for t in teams}
    for wk in d["weekly"].values():
        for t in teams:
            for o in teams:
                if o == t:
                    continue
                for c in cats:
                    r = cmp_cat((wk.get(t) or {}).get(c["id"]), (wk.get(o) or {}).get(c["id"]),
                                c["hb"], c["pct"])
                    rec[t][0 if r > 0 else 1 if r < 0 else 2] += 1
    return rec


def luck(d):
    ap = all_play(d)
    rows = []
    for t in d["teams"]:
        s = d["standings"].get(t, {})
        actual = _pct(s.get("w", 0), s.get("l", 0), s.get("t", 0))
        exp = _pct(*ap[t])
        if actual is None or exp is None:
            continue
        rows.append({"team": t, "actual": round(actual, 3), "expected": round(exp, 3),
                     "luck": round(actual - exp, 3)})
    rows.sort(key=lambda x: -x["luck"])
    return rows


def punts(d, zs, min_weeks=2):
    if len(d["weekly"]) < min_weeks:
        return []
    teams, out = list(d["teams"]), []
    for t in teams:
        gave_up = []
        for c in d["cats"]:
            col = zs[c["id"]]
            bottom2 = sorted(teams, key=lambda x: col[x])[:2]
            if col[t] <= -1.4 and t in bottom2:
                gave_up.append((col[t], c["id"]))
        if gave_up:  # worst two at most, so it reads as a strategy, not a bad week
            out.append({"team": t, "cats": [cid for _, cid in sorted(gave_up)[:2]]})
    return out


def matchup_board(d):
    out = []
    for m in d["matchups"]:
        rows, score = [], [0, 0, 0]
        for c in d["cats"]:
            a, b = m["a_stats"].get(c["id"]), m["b_stats"].get(c["id"])
            r = cmp_cat(a, b, c["hb"], c["pct"])
            score[0 if r > 0 else 1 if r < 0 else 2] += 1
            rows.append({"cat": c["id"], "a": a, "b": b,
                         "lead": "a" if r > 0 else "b" if r < 0 else "tie",
                         "swing": is_swing(a, b, c)})
        out.append({"a": m["a"], "b": m["b"], "score": score, "cats": rows,
                    "swings": sum(1 for x in rows if x["swing"])})
    out.sort(key=lambda m: -m["swings"])
    return out


def daily_report(d):
    daily = d.get("daily") or {}
    teams = [t for t in d["teams"] if daily.get(t)]
    if len(teams) < 2:
        return None
    zs = zscores(daily, d["cats"], teams)
    tot = {t: sum(zs[c["id"]][t] for c in d["cats"]) for t in teams}
    order = sorted(teams, key=lambda t: -tot[t])
    leaders = {}
    for c in d["cats"]:
        vals = [(t, daily[t].get(c["id"])) for t in teams if daily[t].get(c["id"]) is not None]
        if vals:
            best = max(vals, key=lambda x: x[1] if c["hb"] else -x[1])
            leaders[c["id"]] = {"team": best[0], "value": best[1]}
    return {"best": order[0], "worst": order[-1], "z": {t: round(tot[t], 2) for t in teams},
            "leaders": leaders}


def bench_report(d):
    counting = [c for c in d["cats"] if c["hb"] and not c["pct"]]
    pts = next((c["id"] for c in counting if c["name"].upper() in ("PTS", "POINTS")), None)
    rows = []
    for t, players in (d.get("bench") or {}).items():
        if not players:
            continue
        wasted = sum((p["stats"].get(pts) or 0) for p in players) if pts else 0
        top = max(players, key=lambda p: (p["stats"].get(pts) or 0) if pts else 0)
        rows.append({"team": t, "players": len(players), "wasted_pts": wasted,
                     "top_player": top["player"], "top_pts": top["stats"].get(pts) if pts else None})
    rows.sort(key=lambda r: (-r["wasted_pts"], -r["players"]))
    return rows


def analyze(d, prev=None):
    power, zs = power_rankings(d, prev)
    lk = {r["team"]: r["luck"] for r in luck(d)}
    pn = {p["team"]: p["cats"] for p in punts(d, zs)}
    for p in power:
        p["luck"] = lk.get(p["team"])
        p["punts"] = pn.get(p["team"], [])
    from .players import weekly_report
    return {
        "players": weekly_report(d) if d.get("days") else {},
        "race": race(d, prev) if d.get("schedule") else None,
        "last_week": last_week(d, {p["team"]: p["rank"] for p in power}),
        "trends": trends(d),
        "league": d["league"], "date": d.get("date"), "sample": d.get("sample", False),
        "cats": d["cats"], "teams": d["teams"], "standings": d["standings"],
        "power": power, "luck": luck(d), "punts": punts(d, zs),
        "matchups": matchup_board(d), "daily": daily_report(d), "bench": bench_report(d),
        "weeks_played": len(d["weekly"]),
        "gauntlet": gauntlet(d),
    }


# ---------- race to the championship ----------
def _cat_strength(d):
    """Per team, per category: share of all-play category matchups won, regressed to .5 early."""
    teams, cats, weeks = list(d["teams"]), d["cats"], d["weekly"]
    n, k = len(weeks), 2
    out = {}
    for t in teams:
        out[t] = {}
        for c in cats:
            w = g = 0
            for wk in weeks.values():
                for o in teams:
                    if o == t:
                        continue
                    r = cmp_cat((wk.get(t) or {}).get(c["id"]), (wk.get(o) or {}).get(c["id"]), c["hb"], c["pct"])
                    if r:
                        g += 1
                        w += r > 0
            q = w / g if g else .5
            out[t][c["id"]] = (q * n + .5 * k) / (n + k)
    return out


def _log5(a, b):
    a, b = min(max(a, .05), .95), min(max(b, .05), .95)
    return a * (1 - b) / (a * (1 - b) + b * (1 - a))


def race(d, prev=None, sims=2000, seed=42):
    import random
    rnd = random.Random(seed)
    teams, cats = list(d["teams"]), d["cats"]
    npo = d.get("num_playoff_teams") or 6
    strength = _cat_strength(d)
    cur = d["league"]["current_week"]
    sched = {int(w): ms for w, ms in (d.get("schedule") or {}).items()}
    live = {(m["a"], m["b"]): m for m in d.get("matchups", [])}
    f = d.get("week_progress", 0)
    # per matchup, per category win probability for side a
    plan = []
    for w in sorted(sched):
        for a, b in sched[w]:
            probs = []
            for c in cats:
                p = _log5(strength[a][c["id"]], strength[b][c["id"]])
                m = live.get((a, b)) if w == cur else None
                if m:  # blend in the live lead as the week goes on
                    r = cmp_cat(m["a_stats"].get(c["id"]), m["b_stats"].get(c["id"]), c["hb"], c["pct"])
                    p = (1 - f) * p + f * (1 if r > 0 else 0 if r < 0 else .5)
                probs.append(p)
            plan.append((a, b, probs))
    base = {t: [d["standings"][t]["w"], d["standings"][t]["l"], d["standings"][t]["t"]] for t in teams}
    po = {t: 0 for t in teams}
    top = {t: 0 for t in teams}
    wins = {t: 0.0 for t in teams}
    for _ in range(sims):
        rec = {t: base[t][:] for t in teams}
        for a, b, probs in plan:
            for p in probs:
                if rnd.random() < p:
                    rec[a][0] += 1; rec[b][1] += 1
                else:
                    rec[b][0] += 1; rec[a][1] += 1
        order = sorted(teams, key=lambda t: (-(rec[t][0] + .5 * rec[t][2]) / max(1, sum(rec[t])), rnd.random()))
        for i, t in enumerate(order):
            po[t] += i < npo
            top[t] += i == 0
            wins[t] += rec[t][0]
    prev_odds = {r["team"]: r["playoff_odds"] for r in (prev or {}).get("race", {}).get("rows", [])}
    by_rank = sorted(teams, key=lambda t: d["standings"][t].get("rank") or 99)
    lead = d["standings"][by_rank[0]]
    line = d["standings"][by_rank[npo - 1]] if len(by_rank) >= npo else lead
    rows = []
    for t in by_rank:
        s = d["standings"][t]
        odds = po[t] / sims
        rows.append({
            "team": t, "rank": s.get("rank"), "w": s["w"], "l": s["l"], "t": s["t"],
            "back": ((lead["w"] - s["w"]) + (s["l"] - lead["l"])) / 2,
            "vs_line": ((line["w"] - s["w"]) + (s["l"] - line["l"])) / 2,
            "playoff_odds": round(odds, 3), "top_seed_odds": round(top[t] / sims, 3),
            "proj_w": round(wins[t] / sims), "odds_move": round(odds - prev_odds[t], 3) if t in prev_odds else None,
        })
    return {"rows": rows, "playoff_teams": npo, "weeks_left": len(sched), "reg_end": d.get("reg_end"),
            "sims": sims}


# ---------- the gauntlet: every team against every team, every week ----------
def gauntlet(d):
    """Category lines per team for each finished week plus the live one, with real opponents.
    The page (index.html#gauntlet) plays every pair off with the same rules as cmp_cat."""
    cats = d["cats"]

    def lines(tbl):
        out = {}
        for t, row in (tbl or {}).items():
            if not row:
                continue
            out[t] = {c["id"]: (None if row.get(c["id"]) is None else round(row[c["id"]], 5 if c["pct"] else 2))
                      for c in cats}
        return out

    def opps(ms):
        o = {}
        for m in ms or []:
            o[m["a"]], o[m["b"]] = m["b"], m["a"]
        return o

    weeks = []
    for w in sorted(d["weekly"]):
        weeks.append({"week": w, "live": False, "lines": lines(d["weekly"][w]),
                      "opp": opps((d.get("results") or {}).get(w))})
    cur = (d.get("league") or {}).get("current_week")
    if d.get("matchups") and cur not in d["weekly"]:
        tbl = {}
        for m in d["matchups"]:
            tbl[m["a"]], tbl[m["b"]] = m["a_stats"], m["b_stats"]
        played = any(v for row in tbl.values() for c in cats if not c["pct"] and c["hb"]
                     for v in [(row or {}).get(c["id"])] if v)
        if played:  # a fresh week with no games yet stays off; the page shows last week's final
            weeks.append({"week": cur, "live": True, "through": d.get("date"), "lines": lines(tbl),
                          "opp": opps(d["matchups"])})
    weeks = [w for w in weeks if len(w["lines"]) >= 2]
    return {"weeks": weeks} if weeks else None


# ---------- last week ----------
def last_week(d, power_rank):
    if not d["weekly"]:
        return None
    lw = max(d["weekly"])
    cats, wk = d["cats"], d["weekly"][lw]
    teams = [t for t in d["teams"] if t in wk]
    ap = {}
    for t in teams:
        beat = 0
        for o in teams:
            if o == t:
                continue
            sc = sum(cmp_cat(wk[t].get(c["id"]), wk[o].get(c["id"]), c["hb"], c["pct"]) for c in cats)
            beat += sc > 0
        ap[t] = beat
    games = []
    for m in (d.get("results") or {}).get(lw, []):
        sc = [0, 0, 0]
        for c in cats:
            r = cmp_cat(m["a_stats"].get(c["id"]), m["b_stats"].get(c["id"]), c["hb"], c["pct"])
            sc[0 if r > 0 else 1 if r < 0 else 2] += 1
        win = m["a"] if sc[0] > sc[1] else m["b"] if sc[1] > sc[0] else None
        games.append({"a": m["a"], "b": m["b"], "score": sc, "winner": win})
    upset = None
    for g in games:
        if not g["winner"]:
            continue
        loser = g["b"] if g["winner"] == g["a"] else g["a"]
        gap = power_rank.get(g["winner"], 0) - power_rank.get(loser, 0)
        if gap > 0 and (upset is None or gap > upset["gap"]):
            upset = {"winner": g["winner"], "loser": loser, "gap": gap, "score": g["score"]}
    closest = min(games, key=lambda g: abs(g["score"][0] - g["score"][1])) if games else None
    losers = [(g["b"] if g["winner"] == g["a"] else g["a"]) for g in games if g["winner"]]
    robbed = max(losers, key=lambda t: ap.get(t, 0)) if losers else None
    leaders = {}
    for c in cats:
        vals = [(t, wk[t].get(c["id"])) for t in teams if wk[t].get(c["id"]) is not None]
        if vals:
            b = max(vals, key=lambda x: x[1] if c["hb"] else -x[1])
            leaders[c["id"]] = {"team": b[0], "value": b[1]}
    order = sorted(teams, key=lambda t: -ap[t])
    return {"week": lw, "games": games, "allplay": ap, "opponents": len(teams) - 1,
            "mvp": order[0], "dud": order[-1], "upset": upset, "closest": closest,
            "robbed": robbed if robbed and ap.get(robbed, 0) >= (len(teams) - 1) / 2 else None,
            "leaders": leaders}


# ---------- trends ----------
def trends(d):
    weeks = sorted(d["weekly"])
    if len(weeks) < 2:
        return None
    teams, cats = list(d["teams"]), d["cats"]
    rank_by_week = {t: [] for t in teams}
    for i in range(len(weeks)):
        cum = {}
        for t in teams:
            cum[t] = {}
            for c in cats:
                vals = [d["weekly"][w].get(t, {}).get(c["id"]) for w in weeks[:i + 1]]
                vals = [v for v in vals if v is not None]
                cum[t][c["id"]] = (sum(vals) / len(vals) if c["pct"] else sum(vals)) if vals else None
        zs = zscores(cum, cats, teams)
        order = sorted(teams, key=lambda t: -sum(zs[c["id"]][t] for c in cats))
        for r, t in enumerate(order, 1):
            rank_by_week[t].append(r)
    ap_by_week = {t: [] for t in teams}
    for w in weeks:
        wk = d["weekly"][w]
        for t in teams:
            win = g = 0
            for o in teams:
                if o == t:
                    continue
                for c in cats:
                    r = cmp_cat(wk.get(t, {}).get(c["id"]), wk.get(o, {}).get(c["id"]), c["hb"], c["pct"])
                    if r:
                        g += 1
                        win += r > 0
            ap_by_week[t].append(round(win / g, 3) if g else None)
    form = []
    for t in teams:
        s = [x for x in ap_by_week[t] if x is not None]
        if not s:
            continue
        recent = s[-3:]
        form.append({"team": t, "season": round(mean(s), 3), "recent": round(mean(recent), 3),
                     "delta": round(mean(recent) - mean(s), 3),
                     "rank_change": rank_by_week[t][0] - rank_by_week[t][-1]})
    form.sort(key=lambda r: -r["delta"])
    return {"weeks": weeks, "rank_by_week": rank_by_week, "allplay_by_week": ap_by_week, "form": form}
