"""Season summaries and all-time league history, computed from archive/<season>.json files.

Archive format (one per finished season, written by history.py):
  season, label, league_key, name, cats, num_playoff_teams, playoff_start_week, end_week,
  teams     {tk: {name, manager, guid, moves, trades}}
  standings {tk: {rank (final finish), seed, w, l, t}}   # w-l-t = regular-season category record
  weeks     {week: [{a, b, a_stats, b_stats, playoffs, consolation, winner}]}
  season_stats {tk: {cat: val}}
  draft     [{pick, team, player}]   # first round
"""
from collections import defaultdict
from statistics import mean, pstdev

from . import stats


# ---------- identity ----------
def person_of(team, cfg):
    """Teams change names every year; managers don't. Map a team to a stable person id."""
    for pid, p in (cfg.get("people") or {}).items():
        if team.get("guid") in p.get("match", []) or team.get("manager") in p.get("match", []):
            return pid
    g = team.get("guid")
    if g and g != "--hidden--":
        return g
    return "name:" + (team.get("manager") or team.get("name") or "unknown")


def season_label(season):
    y = int(season)
    return f"{y}–{str(y + 1)[-2:]}"


def _matchup_winner(m, cats):
    if m.get("winner"):
        return m["winner"]
    sc = [0, 0]
    for c in cats:
        r = stats.cmp_cat(m["a_stats"].get(c["id"]), m["b_stats"].get(c["id"]), c["hb"], c["pct"])
        if r > 0:
            sc[0] += 1
        elif r < 0:
            sc[1] += 1
    return m["a"] if sc[0] > sc[1] else m["b"] if sc[1] > sc[0] else None


def _cat_score(m, cats):
    sc = [0, 0, 0]
    for c in cats:
        r = stats.cmp_cat(m["a_stats"].get(c["id"]), m["b_stats"].get(c["id"]), c["hb"], c["pct"])
        sc[0 if r > 0 else 1 if r < 0 else 2] += 1
    return sc


def _reg_weeks(s):
    out = {}
    for wk, ms in s["weeks"].items():
        wk = int(wk)
        if wk < s["playoff_start_week"] and not any(m.get("playoffs") for m in ms):
            out[wk] = ms
    return out


# ---------- one season ----------
def season_summary(s, cfg):
    cats, teams, st = s["cats"], s["teams"], s["standings"]
    pid = {tk: person_of(t, cfg) for tk, t in teams.items()}
    reg = _reg_weeks(s)
    weekly = {wk: {} for wk in reg}
    for wk, ms in reg.items():
        for m in ms:
            weekly[wk][m["a"]] = m["a_stats"]
            weekly[wk][m["b"]] = m["b_stats"]

    luck = stats.luck({"cats": cats, "teams": teams, "standings": st, "weekly": weekly})
    luck_by = {r["team"]: r for r in luck}
    zs = stats.zscores(s["season_stats"], cats, list(teams))
    paper = sorted(teams, key=lambda t: -sum(zs[c["id"]][t] for c in cats))
    paper_rank = {t: i for i, t in enumerate(paper, 1)}

    by_rank = sorted(teams, key=lambda t: st[t]["rank"] or 99)
    standings = []
    for tk in by_rank:
        r = st[tk]
        g = r["w"] + r["l"] + r["t"]
        standings.append({
            "team": tk, "person": pid[tk], "rank": r["rank"], "seed": r.get("seed"),
            "w": r["w"], "l": r["l"], "t": r["t"], "pct": round((r["w"] + .5 * r["t"]) / g, 3) if g else None,
            "paper_rank": paper_rank[tk], "luck": luck_by.get(tk, {}).get("luck"),
            "moves": teams[tk].get("moves", 0), "trades": teams[tk].get("trades", 0),
        })

    # playoff bracket, championship side only
    pweeks = sorted(int(w) for w, ms in s["weeks"].items()
                    if any(m.get("playoffs") and not m.get("consolation") for m in ms))
    labels = {}
    names = ["Final", "Semifinals", "Quarterfinals", "Round of 16"]
    for i, w in enumerate(reversed(pweeks)):
        labels[w] = names[i] if i < len(names) else f"Week {w}"
    bracket = []
    for w in pweeks:
        games = []
        for m in s["weeks"][w]:
            if not m.get("playoffs") or m.get("consolation"):
                continue
            games.append({"a": m["a"], "b": m["b"], "score": _cat_score(m, cats),
                          "winner": _matchup_winner(m, cats)})
        bracket.append({"week": w, "round": labels[w], "games": games})

    # single-week records, regular season only (playoff weeks can be different lengths)
    best, worst = [], []
    for c in cats:
        rows = [(wk, tk, v.get(c["id"])) for wk, tv in weekly.items() for tk, v in tv.items()
                if v.get(c["id"]) is not None]
        if not rows:
            continue
        key = (lambda r: r[2]) if c["hb"] else (lambda r: -r[2])
        hi, lo = max(rows, key=key), min(rows, key=key)
        best.append({"cat": c["name"], "value": hi[2], "team": hi[1], "week": hi[0], "pct": c["pct"], "hb": c["hb"]})
        worst.append({"cat": c["name"], "value": lo[2], "team": lo[1], "week": lo[0], "pct": c["pct"], "hb": c["hb"]})

    champ, runner, sacko = by_rank[0], by_rank[1], by_rank[-1]
    reg_leader = min(teams, key=lambda t: st[t].get("seed") or 99)
    awards = []
    if paper[0] != champ:
        awards.append({"title": "Best team, no ring", "team": paper[0],
                       "note": f"No. 1 on paper by season category totals, finished {st[paper[0]]['rank']}."})
    if luck:
        awards.append({"title": "Horseshoe award", "team": luck[0]["team"],
                       "note": f"Luckiest record: +{luck[0]['luck'] * 100:.1f} points over all-play."})
        awards.append({"title": "Robbed", "team": luck[-1]["team"],
                       "note": f"Unluckiest record: {luck[-1]['luck'] * 100:.1f} points under all-play."})
    busiest = max(teams, key=lambda t: teams[t].get("moves", 0))
    awards.append({"title": "Waiver wire addict", "team": busiest,
                   "note": f"{teams[busiest].get('moves', 0)} moves."})
    trader = max(teams, key=lambda t: teams[t].get("trades", 0))
    if teams[trader].get("trades"):
        awards.append({"title": "Trade machine", "team": trader,
                       "note": f"{teams[trader]['trades']} trades."})

    draft = [{"pick": d["pick"], "team": d["team"], "player": d["player"],
              "finish": st.get(d["team"], {}).get("rank")} for d in sorted(s.get("draft", []), key=lambda d: d["pick"])]

    return {
        "season": s["season"], "label": s.get("label") or season_label(s["season"]),
        "name": s.get("name"), "cats": [c["name"] for c in cats],
        "teams": {tk: {"name": t["name"], "manager": t.get("manager"), "person": pid[tk]} for tk, t in teams.items()},
        "champion": champ, "runner_up": runner, "sacko": sacko, "reg_leader": reg_leader,
        "num_playoff_teams": s.get("num_playoff_teams"),
        "standings": standings, "bracket": bracket, "records": {"best": best, "worst": worst},
        "awards": awards, "draft": draft, "recap": s.get("recap"),
    }


# ---------- all time ----------
def history(archives, summaries, cfg):
    people = defaultdict(lambda: {"seasons": [], "titles": 0, "runner_ups": 0, "sackos": 0,
                                  "playoffs": 0, "w": 0, "l": 0, "t": 0, "trades": 0, "moves": 0,
                                  "luck": [], "names": []})
    display = {pid: p.get("display") for pid, p in (cfg.get("people") or {}).items()}
    latest_name = {}
    trophies = []
    for s, sm in zip(archives, summaries):
        n = len(sm["standings"])
        npo = sm.get("num_playoff_teams") or 0
        for row in sm["standings"]:
            p = people[row["person"]]
            team = sm["teams"][row["team"]]
            latest_name.setdefault(row["person"], team["manager"])
            p["seasons"].append({"season": sm["season"], "label": sm["label"], "team": team["name"],
                                 "rank": row["rank"], "of": n, "seed": row["seed"],
                                 "made_playoffs": bool(row["seed"] and npo and row["seed"] <= npo),
                                 "record": f"{row['w']}-{row['l']}-{row['t']}"})
            p["names"].append(team["name"])
            p["titles"] += row["rank"] == 1
            p["runner_ups"] += row["rank"] == 2
            p["sackos"] += row["rank"] == n
            p["playoffs"] += bool(row["seed"] and npo and row["seed"] <= npo)
            for k in ("w", "l", "t", "trades", "moves"):
                p[k] += row[k]
            if row["luck"] is not None:
                p["luck"].append(row["luck"])
        t = sm["teams"]
        trophies.append({"season": sm["season"], "label": sm["label"],
                         "champion": {"person": t[sm["champion"]]["person"], "team": t[sm["champion"]]["name"]},
                         "runner_up": {"person": t[sm["runner_up"]]["person"], "team": t[sm["runner_up"]]["name"]},
                         "sacko": {"person": t[sm["sacko"]]["person"], "team": t[sm["sacko"]]["name"]}})

    table = []
    for pid, p in people.items():
        g = p["w"] + p["l"] + p["t"]
        ranks = [x["rank"] for x in p["seasons"]]
        table.append({
            "person": pid, "name": display.get(pid) or latest_name.get(pid) or pid,
            "seasons": len(p["seasons"]), "titles": p["titles"], "runner_ups": p["runner_ups"],
            "sackos": p["sackos"], "playoffs": p["playoffs"],
            "w": p["w"], "l": p["l"], "t": p["t"], "pct": round((p["w"] + .5 * p["t"]) / g, 3) if g else None,
            "avg_finish": round(mean(ranks), 1), "best_finish": min(ranks),
            "finish_sd": round(pstdev(ranks), 2) if len(ranks) > 1 else None,
            "luck": round(mean(p["luck"]), 3) if p["luck"] else None,
            "trades": p["trades"], "moves": p["moves"],
            "history": sorted(p["seasons"], key=lambda x: x["season"]),
        })
    # championships first, then win %
    table.sort(key=lambda r: (-r["titles"], -(r["pct"] or 0)))

    # head to head, every week including playoffs
    h2h = defaultdict(lambda: {"w": 0, "l": 0, "t": 0, "cw": 0, "cl": 0, "ct": 0, "playoff_meetings": 0})
    for s, sm in zip(archives, summaries):
        pid = {tk: v["person"] for tk, v in sm["teams"].items()}
        for wk, ms in s["weeks"].items():
            for m in ms:
                if m.get("consolation"):
                    continue
                a, b = pid[m["a"]], pid[m["b"]]
                sc = _cat_score(m, s["cats"])
                win = _matchup_winner(m, s["cats"])
                for me, them, mine, theirs, flip in ((a, b, sc[0], sc[1], m["a"]), (b, a, sc[1], sc[0], m["b"])):
                    r = h2h[(me, them)]
                    r["cw"] += mine; r["cl"] += theirs; r["ct"] += sc[2]
                    if win is None:
                        r["t"] += 1
                    elif win == flip:
                        r["w"] += 1
                    else:
                        r["l"] += 1
                    r["playoff_meetings"] += bool(m.get("playoffs"))
    h2h_out = {f"{a}|{b}": v for (a, b), v in h2h.items()}

    rivalries = []
    seen = set()
    for (a, b), v in h2h.items():
        if (b, a) in seen:
            continue
        seen.add((a, b))
        games = v["w"] + v["l"] + v["t"]
        if games < 3:
            continue
        rivalries.append({"a": a, "b": b, "w": v["w"], "l": v["l"], "t": v["t"], "games": games,
                          "playoff_meetings": v["playoff_meetings"],
                          "gap": abs(v["w"] - v["l"]) / games})
    lopsided = sorted([r for r in rivalries if r["w"] != r["l"]], key=lambda r: (-r["gap"], -r["games"]))[:5]
    for r in lopsided:  # winner always on the left
        if r["l"] > r["w"]:
            r["a"], r["b"], r["w"], r["l"] = r["b"], r["a"], r["l"], r["w"]
    closest = sorted(rivalries, key=lambda r: (r["gap"], -r["games"]))[:5]
    playoff_feuds = sorted([r for r in rivalries if r["playoff_meetings"]], key=lambda r: -r["playoff_meetings"])[:5]

    # all-time single-week records by category name (categories may differ season to season)
    records = {}
    for sm in summaries:
        for kind in ("best", "worst"):
            for r in sm["records"][kind]:
                key = (kind, r["cat"])
                cand = {**r, "season": sm["season"], "label": sm["label"],
                        "person": sm["teams"][r["team"]]["person"], "team_name": sm["teams"][r["team"]]["name"]}
                cur = records.get(key)
                want_high = (kind == "best") == r["hb"]
                better = (lambda x, y: x > y) if want_high else (lambda x, y: x < y)
                if cur is None or better(cand["value"], cur["value"]):
                    records[key] = cand
    rec_best = [v for (k, _), v in records.items() if k == "best"]
    rec_worst = [v for (k, _), v in records.items() if k == "worst"]

    sup, used = [], set()
    elig = [r for r in table if r["seasons"] >= 2]

    def pick(title, rows, key, note, cond=lambda r: True):
        """One superlative per person, so the champ doesn't sweep the board."""
        rows = [r for r in rows if cond(r) and r["person"] not in used]
        if rows:
            r = max(rows, key=key)
            used.add(r["person"])
            sup.append({"title": title, "person": r["person"], "note": note(r)})

    pick("Dynasty", table, lambda r: (r["titles"], r["pct"] or 0), lambda r: f"{r['titles']} titles in {r['seasons']} seasons.", lambda r: r["titles"])
    pick("Sacko royalty", table, lambda r: r["sackos"], lambda r: f"{r['sackos']} last-place finishes.", lambda r: r["sackos"])
    pick("Ringless", table, lambda r: r["playoffs"], lambda r: f"{r['playoffs']} playoff trips, zero titles.", lambda r: not r["titles"] and r["playoffs"])
    pick("Bridesmaid", table, lambda r: r["runner_ups"], lambda r: f"{r['runner_ups']} runner-up finishes.", lambda r: r["runner_ups"])
    pick("Born lucky", elig, lambda r: r["luck"] or -9, lambda r: f"+{(r['luck'] or 0) * 100:.1f} points a year over all-play.", lambda r: (r["luck"] or 0) > 0)
    pick("Cursed", elig, lambda r: -(r["luck"] or 9), lambda r: f"{(r['luck'] or 0) * 100:.1f} points a year under all-play.", lambda r: (r["luck"] or 0) < 0)
    pick("Trade machine", table, lambda r: r["trades"], lambda r: f"{r['trades']} trades all time.", lambda r: r["trades"])
    pick("Mr. Consistent", elig, lambda r: -(r["finish_sd"] or 99), lambda r: f"Average finish {r['avg_finish']}, rarely moves.", lambda r: r["finish_sd"] is not None)

    names = {r["person"]: r["name"] for r in table}
    return {
        "seasons": [{"season": sm["season"], "label": sm["label"]} for sm in summaries],
        "people": names, "table": table, "trophies": trophies, "h2h": h2h_out,
        "rivalries": {"lopsided": lopsided, "closest": closest, "playoff": playoff_feuds},
        "records": {"best": rec_best, "worst": rec_worst}, "superlatives": sup,
    }
