"""Player-level weekly analytics: top performers, waiver 'plays of the week', waiver wire review.

Inputs (internal format, added to `d` by build.py):
  days     {date: {tk: [{pk, name, pos, stats: {cat_id: val, frac_id: [makes, attempts]}}]}}
  windows  {"last": {week, start, end}, "current": {week, start, end}}   # end = last day with data
  transactions [{id, ts, kind (add|drop), team, source, faab, pk, player}]
  frac     {frac_stat_id: "FGM/A", ...}   # Yahoo display-only makes/attempts stats
Only stats from players in an active lineup slot count, same as Yahoo's scoring.
"""
from datetime import date as Date, datetime, timedelta
from statistics import mean, pstdev
from zoneinfo import ZoneInfo

from .stats import cmp_cat

INACTIVE = {"BN", "IL", "IL+", "IR", "NA", None}
ET = ZoneInfo("America/New_York")


def et_date(ts):
    return datetime.fromtimestamp(ts, ET).date().isoformat()


def date_range(start, end):
    s, e = Date.fromisoformat(start), Date.fromisoformat(end)
    return [(s + timedelta(days=i)).isoformat() for i in range((e - s).days + 1)]


def pct_map(cats, frac):
    """Map each % category to its makes/attempts stat, e.g. FG% -> FGM/A."""
    out = {}
    for c in cats:
        if c["pct"]:
            pre = c["name"].replace("%", "").strip().upper()
            for fid, nm in frac.items():
                if nm.upper().startswith(pre) and "/" in nm:
                    out[c["id"]] = fid
    return out


def _empty():
    return {"cnt": {}, "frac": {}, "games": 0, "dates": []}


def _add_line(agg, stats, cats, pmap):
    played = False
    for c in cats:
        if c["pct"]:
            fa = stats.get(pmap.get(c["id"]))
            if fa and fa[1]:
                cur = agg["frac"].setdefault(c["id"], [0.0, 0.0])
                cur[0] += fa[0]; cur[1] += fa[1]
                played = True
        else:
            v = stats.get(c["id"])
            if v:
                agg["cnt"][c["id"]] = agg["cnt"].get(c["id"], 0) + v
                played = True
    return played


def aggregate(days, dates, cats, pmap, since=None):
    """Started-lineup totals per (team, player) and per team across `dates`."""
    players, teams = {}, {}
    for dt in dates:
        for tk, lines in (days.get(dt) or {}).items():
            t = teams.setdefault(tk, _empty())
            for ln in lines:
                if ln["pos"] in INACTIVE:
                    continue
                if since and (ln["pk"], tk) in since and dt < since[(ln["pk"], tk)]:
                    continue
                p = players.setdefault((tk, ln["pk"]), {**_empty(), "pk": ln["pk"], "name": ln["name"], "team": tk})
                if _add_line(p, ln["stats"], cats, pmap):
                    p["games"] += 1
                    p["dates"].append(dt)
                _add_line(t, ln["stats"], cats, pmap)
    return players, teams


def values_of(agg, cats):
    out = {}
    for c in cats:
        if c["pct"]:
            m, a = agg["frac"].get(c["id"], (0, 0))
            out[c["id"]] = (m / a) if a else None
        else:
            out[c["id"]] = agg["cnt"].get(c["id"], 0)
    return out


def per_game(agg):
    g = agg.get("games") or 1
    return {"cnt": {k: v / g for k, v in agg["cnt"].items()},
            "frac": {k: [v[0] / g, v[1] / g] for k, v in agg["frac"].items()}, "games": agg.get("games", 0)}


class Scorer:
    """Z-scores a player's totals against every started player-week in the league.
    % categories use volume-aware impact: makes minus league rate times attempts."""

    def __init__(self, lines, cats):
        self.cats, self.params = cats, {}
        lines = [l for l in lines if l["games"]]
        for c in cats:
            if c["pct"]:
                tm = sum(l["frac"].get(c["id"], (0, 0))[0] for l in lines)
                ta = sum(l["frac"].get(c["id"], (0, 0))[1] for l in lines)
                if not ta:
                    continue
                rate = tm / ta
                vals = [self._impact(l, c["id"], rate) for l in lines]
                self.params[c["id"]] = ("pct", rate, mean(vals), pstdev(vals) or 1)
            else:
                vals = [l["cnt"].get(c["id"], 0) for l in lines]
                if vals:
                    self.params[c["id"]] = ("cnt", None, mean(vals), pstdev(vals) or 1)

    @staticmethod
    def _impact(l, cid, rate):
        m, a = l["frac"].get(cid, (0, 0))
        return m - rate * a

    def z(self, agg):
        out = {}
        for c in self.cats:
            p = self.params.get(c["id"])
            if not p:
                continue
            kind, rate, mu, sd = p
            v = self._impact(agg, c["id"], rate) if kind == "pct" else agg["cnt"].get(c["id"], 0)
            out[c["id"]] = ((v - mu) / sd) * (1 if c["hb"] else -1)
        return out

    def value(self, agg):
        return round(sum(self.z(agg).values()), 2)


def _line(agg, cats):
    """Display line: counting totals and % with makes/attempts."""
    out = {}
    for c in cats:
        if c["pct"]:
            m, a = agg["frac"].get(c["id"], (0, 0))
            if a:
                out[c["id"]] = [round(m / a, 3), int(m), int(a)]
        else:
            out[c["id"]] = agg["cnt"].get(c["id"], 0)
    return out


def top_players(players, scorer, cats, n=5):
    rows = []
    for p in players.values():
        if not p["games"]:
            continue
        z = scorer.z(p)
        best = sorted(z, key=z.get, reverse=True)[:3]
        rows.append({"pk": p["pk"], "name": p["name"], "team": p["team"], "games": p["games"],
                     "value": round(sum(z.values()), 2), "line": _line(p, cats), "best": best})
    rows.sort(key=lambda r: -r["value"])
    return rows[:n]


def acquisitions(transactions, cutoff_ts):
    """(team, pk) -> latest add by that team before cutoff, if the player's latest move was that add."""
    latest = {}
    for t in sorted(transactions, key=lambda t: t["ts"]):
        if t["ts"] > cutoff_ts:
            continue
        latest[t["pk"]] = t
    return {(t["team"], pk): {"date": et_date(t["ts"]), "source": t["source"], "faab": t["faab"]}
            for pk, t in latest.items() if t["kind"] == "add"}


def _outcome(score):
    return 2 if score[0] > score[1] else 1 if score[0] == score[1] else 0


def top_plays(players, teams, pairs, cats, acq, scorer, n=5):
    """For each waiver pickup who played, remove his stats from his team's week and replay the matchup."""
    vals = {tk: values_of(agg, cats) for tk, agg in teams.items()}
    rows = []
    for a, b in pairs:
        for me, opp in ((a, b), (b, a)):
            if me not in teams or opp not in teams:
                continue
            base = [0, 0, 0]
            base_r = {}
            for c in cats:
                r = cmp_cat(vals[me][c["id"]], vals[opp][c["id"]], c["hb"], c["pct"])
                base_r[c["id"]] = r
                base[0 if r > 0 else 1 if r < 0 else 2] += 1
            for (tk, pk), p in players.items():
                if tk != me or not p["games"] or (tk, pk) not in acq:
                    continue
                cf = {"cnt": {k: v - p["cnt"].get(k, 0) for k, v in teams[me]["cnt"].items()},
                      "frac": {k: [v[0] - p["frac"].get(k, (0, 0))[0], v[1] - p["frac"].get(k, (0, 0))[1]]
                               for k, v in teams[me]["frac"].items()}}
                cfv = values_of(cf, cats)
                swung, score = [], [0, 0, 0]
                for c in cats:
                    r = cmp_cat(cfv[c["id"]], vals[opp][c["id"]], c["hb"], c["pct"])
                    score[0 if r > 0 else 1 if r < 0 else 2] += 1
                    if r < base_r[c["id"]]:
                        swung.append(c["id"])
                net = base[0] - score[0]  # categories he was worth, net of any he cost (FG% drag)
                if not swung or net <= 0:
                    continue
                rows.append({
                    "pk": pk, "name": p["name"], "team": me, "opp": opp, "games": p["games"], "net": net,
                    "acquired": acq[(tk, pk)], "swung": swung, "score": base, "without": score,
                    "flipped": _outcome(score) < _outcome(base), "decided": _outcome(base) == 2 and _outcome(score) < 2,
                    "value": scorer.value(p), "line": _line(p, cats),
                })
    rows.sort(key=lambda r: (r["flipped"], r["net"], r["value"]), reverse=True)
    return rows[:n]


def waiver_review(d, window, scorer, cats, pmap, extra_dates=(), pg_scorer=None):
    start, end = window["start"], window["end"]
    lo = datetime.fromisoformat(start).replace(tzinfo=ET).timestamp()
    hi = (datetime.fromisoformat(end).replace(tzinfo=ET) + timedelta(days=1)).timestamp()
    txns = [t for t in d.get("transactions", []) if lo <= t["ts"] < hi]
    moves = {}
    for t in sorted(txns, key=lambda t: t["ts"]):
        m = moves.setdefault((t["id"], t["team"]), {"date": et_date(t["ts"]), "team": t["team"], "adds": [], "drops": []})
        (m["adds"] if t["kind"] == "add" else m["drops"]).append(
            {"pk": t["pk"], "player": t["player"], "source": t["source"], "faab": t["faab"]})
    moves = sorted(moves.values(), key=lambda m: m["date"], reverse=True)
    dates = date_range(start, end)
    # production for the new team from the add date through the end of the week
    since = {(a["pk"], m["team"]): m["date"] for m in moves for a in m["adds"]}
    players, _ = aggregate(d["days"], dates, cats, pmap, since=since)
    for m in moves:
        for a in m["adds"]:
            p = players.get((m["team"], a["pk"]))
            a["games"] = p["games"] if p else 0
            a["value"] = scorer.value(p) if p and p["games"] else None
            a["pg"] = pg_scorer.value(per_game(p)) if pg_scorer and p and p["games"] else None
            a["line"] = _line(p, cats) if p else {}
    adds = [(m, a) for m in moves for a in m["adds"] if a.get("pg") is not None]
    pool = [x for x in adds if x[1]["games"] >= 2] or adds
    best = max(pool, key=lambda x: x[1]["pg"]) if pool else None
    # one man's trash: dropped this week, then started for someone else
    later = list(dates) + list(extra_dates)
    trash = None
    for m in moves:
        for dr in m["drops"]:
            for (tk, pk), p in aggregate(d["days"], [x for x in later if x >= m["date"]], cats, pmap)[0].items():
                if pk == dr["pk"] and tk != m["team"] and p["games"]:
                    v = pg_scorer.value(per_game(p)) if pg_scorer else scorer.value(p)
                    if trash is None or v > trash["value"]:
                        trash = {"player": dr["player"], "dropped_by": m["team"], "now": tk, "value": v,
                                 "games": p["games"], "line": _line(p, cats)}
    count = {}
    for m in moves:
        count[m["team"]] = count.get(m["team"], 0) + len(m["adds"])
    busiest = max(count, key=count.get) if count else None
    bids = [(m, a) for m in moves for a in m["adds"] if a.get("faab")]
    splash = max(bids, key=lambda x: x[1]["faab"]) if bids else None
    return {
        "week": window["week"], "start": start, "end": end, "moves": moves,
        "total_adds": sum(len(m["adds"]) for m in moves),
        "best_pickup": {"team": best[0]["team"], **best[1]} if best else None,
        "busiest": {"team": busiest, "adds": count[busiest]} if busiest else None,
        "trash": trash,
        "splash": {"team": splash[0]["team"], **splash[1]} if splash else None,
    }


def weekly_report(d):
    cats = d["cats"]
    pmap = pct_map(cats, d.get("frac") or {})
    out = {}
    for key, pairs in (("last", [(m["a"], m["b"]) for m in (d.get("results") or {}).get(max(d["results"]), [])] if d.get("results") else []),
                       ("current", [(m["a"], m["b"]) for m in d.get("matchups", [])])):
        w = (d.get("windows") or {}).get(key)
        if not w or w["end"] < w["start"]:
            continue
        dates = date_range(w["start"], w["end"])
        players, teams = aggregate(d["days"], dates, cats, pmap)
        if not players:
            continue
        scorer = Scorer(list(players.values()), cats)
        cutoff = (datetime.fromisoformat(w["end"]).replace(tzinfo=ET) + timedelta(days=1)).timestamp()
        acq = acquisitions(d.get("transactions", []), cutoff)
        out[key] = {"week": w["week"], "start": w["start"], "end": w["end"], "days": len(dates),
                    "top_players": top_players(players, scorer, cats, 10),
                    "top_plays": top_plays(players, teams, pairs, cats, acq, scorer)}
        if key == "last":
            cur = (d.get("windows") or {}).get("current")
            extra = date_range(cur["start"], cur["end"]) if cur and cur["end"] >= cur["start"] else []
            pg = Scorer([per_game(p) for p in players.values() if p["games"]], cats)
            out["waiver"] = waiver_review(d, w, scorer, cats, pmap, extra, pg)
    return out


def bench_from_day(d, day):
    """Yesterday's benched players who put up numbers (feeds the recap's bench blunder)."""
    out = {}
    for tk, lines in (d.get("days", {}).get(day) or {}).items():
        out[tk] = [{"player": l["name"], "stats": {k: v for k, v in l["stats"].items() if not isinstance(v, list)}}
                   for l in lines if l["pos"] == "BN" and any(v for v in l["stats"].values() if not isinstance(v, list))]
    return out
