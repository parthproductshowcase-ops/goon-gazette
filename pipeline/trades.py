"""Trade Lab: category needs, partner fits and deals that help both sides.

    build(d, polls) -> dict     # d is the dict web_current.build_d / build.py make; polls = polls.json
    python -m pipeline.trades   # build from archive/2026_web.json and write labs/trades.json (full) and docs/data/trades.json (public)

Oct 22: the full Lab (suggested deals) stays unpublished in labs/. The public page docs/trade.html is the
Trade Analyzer plus Know Your Needs, fed by docs/data/trades.json = public(t), which carries no deals.

Model (all numbers come from real lines, nothing invented):
  * Each player's per-game line = this season's games blended with a prior, K games of prior weight.
    Prior = his 2025-26 per-game line (archive/2026_prior.json); rookies and anyone missing get a
    replacement-level line (players ranked 151-200 last season), so one hot night can't make a star.
  * A team's week = its top N_ACTIVE healthy players by value, each at GPW games. IL players sit.
  * Strength = expected category wins against every other team (all-play, 0 to 11).
  * A deal is listed when both teams gain expected category wins and the value gap is small.
    2-for-1 deals give the side that frees a spot a replacement-level waiver add.
"""
from itertools import combinations
from statistics import mean, pstdev
import json
from pathlib import Path

from .night import key

ROOT = Path(__file__).resolve().parents[1]
PRIOR = ROOT / "archive" / "2026_prior.json"
PROJ = ROOT / "archive" / "2026_proj.json"   # Yahoo "Remaining Games (proj)", refreshed every Monday
POLLS = ROOT / "docs" / "data" / "polls.json"
OUT = ROOT / "labs" / "trades.json"   # full Lab incl. suggested deals: unpublished, labs/ is not served
PUBLIC = ROOT / "docs" / "data" / "trades.json"   # public Trade Analyzer: no suggested deals

K = 10            # prior weight in games
GPW = 3.5         # games per player per week
N_ACTIVE = 10     # starters counted per team
MIN_GAIN = 0.15   # each side must gain this many expected category wins
MAX_GAP = 1.5     # max trade-value gap (z units) for a listed deal
G0 = 15           # last season's line is shrunk toward replacement by gp / (gp + G0)
MIN_GAMES = 3     # a player with no prior line needs this many games before he's dealt

# category id -> field; % cats are (makes, attempts)
CNT = {"10": "tpm", "12": "pts", "13": "oreb", "14": "dreb", "16": "ast", "17": "st", "18": "blk", "19": "to"}
PCT = {"5": ("fgm", "fga"), "8": ("ftm", "fta"), "11": ("tpm", "tpa")}
FIELDS = ["fgm", "fga", "ftm", "fta", "tpm", "tpa", "pts", "oreb", "dreb", "ast", "st", "blk", "to"]
LOWER = {"19"}    # turnovers: fewer wins
FRAC = {"9004003": ("fgm", "fga"), "9007006": ("ftm", "fta"), "9001002": ("tpm", "tpa")}
STAT = {"10": "tpm", "12": "pts", "13": "oreb", "14": "dreb", "16": "ast", "17": "st", "18": "blk", "19": "to"}


# ---------- player lines ----------
def _season_lines(d):
    """{name_key: {name, n, sum{field}}} from every game this season, any slot."""
    out = {}
    for byteam in (d.get("days") or {}).values():
        for lines in byteam.values():
            for ln in lines:
                s = ln["stats"]
                fga = (s.get("9004003") or [0, 0])[1]
                fta = (s.get("9007006") or [0, 0])[1]
                played = fga or fta or any(s.get(c) for c in STAT)
                if not played:
                    continue
                p = out.setdefault(key(ln["name"]), {"name": ln["name"], "n": 0, "sum": dict.fromkeys(FIELDS, 0.0)})
                p["n"] += 1
                for fid, (m, a) in FRAC.items():
                    v = s.get(fid) or [0, 0]
                    if fid == "9001002":
                        p["sum"]["tpa"] += v[1]
                    else:
                        p["sum"][m] += v[0]; p["sum"][a] += v[1]
                for cid, f in STAT.items():
                    p["sum"][f] += s.get(cid) or 0
    return out


def _prior(pr):
    """{name_key: per-game line} plus the replacement-level line."""
    rows = list(pr["per_game"].items())
    # replacement: players 151-200 in last season's rank order, real games only
    rep_rows = [v for _, v in rows[150:200] if v["gp"] >= 20] or [v for _, v in rows[-50:]]
    rep = {f: round(mean(r[f] for r in rep_rows), 4) for f in FIELDS}
    by = {}
    for n, v in rows:
        w = v["gp"] / (v["gp"] + G0)   # 5 games last year is a rumor, 70 is a résumé
        by[key(n)] = {f: w * v[f] + (1 - w) * rep[f] for f in FIELDS}
        by[key(n)]["gp"] = v["gp"]
    return by, rep


def _lookup(by, name):
    k = key(name)
    if k in by:
        return by[k]
    parts = k.split()
    if len(parts) == 2 and not parts[0].endswith("."):
        ab = f"{parts[0][0]}. {parts[1]}"
        if ab in by:
            return by[ab]
    return None


def project(name, season, prior, rep):
    """Blended per-game line and a note on what it's built from."""
    cur = _lookup(season, name)
    base = _lookup(prior, name)
    src = "last season" if base else "replacement"
    base = base or rep
    n = cur["n"] if cur else 0
    line = {}
    for f in FIELDS:
        tot = (cur["sum"][f] if cur else 0.0) + K * base[f]
        line[f] = round(tot / (n + K), 4)  # the page reruns this math from the JSON; same numbers both sides
    return line, n, src


# ---------- values ----------
class Valuer:
    """Per-game z across a player pool. % cats use makes minus pool rate times attempts."""

    def __init__(self, lines):
        self.p = {}
        for cid, f in CNT.items():
            vals = [l[f] for l in lines]
            self.p[cid] = ("cnt", f, mean(vals), pstdev(vals) or 1)
        for cid, (m, a) in PCT.items():
            rate = sum(l[m] for l in lines) / max(1e-9, sum(l[a] for l in lines))
            vals = [l[m] - rate * l[a] for l in lines]
            self.p[cid] = ("pct", (m, a, rate), mean(vals), pstdev(vals) or 1)

    def z(self, l):
        out = {}
        for cid, (kind, f, mu, sd) in self.p.items():
            v = l[f] if kind == "cnt" else l[f[0]] - f[2] * l[f[1]]
            out[cid] = round(((v - mu) / sd) * (-1 if cid in LOWER else 1), 3)
        return out


# ---------- teams ----------
MODE = {"gpw": GPW, "skip_il": True}   # Yahoo mode: lines are rest-of-season totals, gpw 1, injured players count via their projected games


def team_week(players):
    """Team totals from the top N_ACTIVE players by value (weekly in prior mode, rest of season in Yahoo mode)."""
    pool = [p for p in players if not (MODE["skip_il"] and p.get("il"))]
    top = sorted(pool, key=lambda p: -p["value"])[:N_ACTIVE]
    t = dict.fromkeys(FIELDS, 0.0)
    for p in top:
        for f in FIELDS:
            t[f] += p["line"][f] * MODE["gpw"]
    out = {cid: t[f] for cid, f in CNT.items()}
    for cid, (m, a) in PCT.items():
        out[cid] = t[m] / t[a] if t[a] else 0
    return out


def _beats(a, b, cid):
    if abs(a - b) < 1e-9:
        return 0.5
    return 1.0 if (a < b if cid in LOWER else a > b) else 0.0


def allplay(weeks, tk, cats):
    """Expected category wins per matchup (0 to 11) for team tk against everyone else."""
    others = [o for o in weeks if o != tk]
    return sum(_beats(weeks[tk][c], weeks[o][c], c) for o in others for c in cats) / len(others)


def ranks(weeks, cats):
    out = {tk: {} for tk in weeks}
    for c in cats:
        order = sorted(weeks, key=lambda t: weeks[t][c], reverse=c not in LOWER)
        for i, tk in enumerate(order):
            out[tk][c] = i + 1
    return out


# ---------- deals ----------
def _swap(roster, give, get):
    names = {p["name"] for p in give}
    return [p for p in roster if p["name"] not in names] + list(get)


def evaluate(rosters, weeks, a, b, give_a, give_b, rep_player, cats):
    """(gain_a, gain_b, new_weeks) for a giving give_a to b and b giving give_b to a."""
    ra = _swap(rosters[a], give_a, give_b)
    rb = _swap(rosters[b], give_b, give_a)
    if len(give_a) > len(give_b):
        ra = ra + [rep_player] * (len(give_a) - len(give_b))
    if len(give_b) > len(give_a):
        rb = rb + [rep_player] * (len(give_b) - len(give_a))
    w = dict(weeks)
    w[a], w[b] = team_week(ra), team_week(rb)
    return allplay(w, a, cats) - allplay(weeks, a, cats), allplay(w, b, cats) - allplay(weeks, b, cats), w


def find_deals(rosters, weeks, rep_player, cats):
    teams = sorted(rosters)
    found = []
    for a, b in combinations(teams, 2):
        A = [p for p in rosters[a] if p.get("dealable")]
        B = [p for p in rosters[b] if p.get("dealable")]
        options = [((x,), (y,)) for x in A for y in B]
        options += [((x1, x2), (y,)) for x1, x2 in combinations(A, 2) for y in B
                    if abs(x1["tv"] + x2["tv"] - y["tv"]) <= MAX_GAP + 2]
        options += [((x,), (y1, y2)) for x in A for y1, y2 in combinations(B, 2)
                    if abs(y1["tv"] + y2["tv"] - x["tv"]) <= MAX_GAP + 2]
        for ga, gb in options:
            va, vb = sum(p["tv"] for p in ga), sum(p["tv"] for p in gb)
            if abs(va - vb) > MAX_GAP:
                continue
            ca, cb = sum(p.get("cost") or 1 for p in ga), sum(p.get("cost") or 1 for p in gb)
            if abs(ca - cb) > max(10, 0.4 * max(ca, cb)):
                continue  # has to pass the smell test in the group chat, not just the model
            da, db, _ = evaluate(rosters, weeks, a, b, ga, gb, rep_player, cats)
            if da >= MIN_GAIN and db >= MIN_GAIN:
                found.append({"a": a, "b": b, "give_a": [p["name"] for p in ga], "give_b": [p["name"] for p in gb],
                              "gain_a": round(da, 2), "gain_b": round(db, 2), "gap": round(va - vb, 2),
                              "score": round(min(da, db) - 0.05 * abs(va - vb), 3)})
    found.sort(key=lambda x: -x["score"])
    return found


def pick(deals, limit=12, per_pair=1, per_player=1, per_team=3):
    """Spread the list out: no pair, player or team hogs it."""
    out, pairs, players, teams = [], {}, {}, {}
    for d in deals:
        pk = (d["a"], d["b"])
        names = d["give_a"] + d["give_b"]
        if pairs.get(pk, 0) >= per_pair or any(players.get(n, 0) >= per_player for n in names):
            continue
        if teams.get(d["a"], 0) >= per_team or teams.get(d["b"], 0) >= per_team:
            continue
        out.append(d)
        pairs[pk] = pairs.get(pk, 0) + 1
        for n in names:
            players[n] = players.get(n, 0) + 1
        teams[d["a"]] = teams.get(d["a"], 0) + 1
        teams[d["b"]] = teams.get(d["b"], 0) + 1
        if len(out) >= limit:
            break
    return out


# ---------- build ----------
def weeks_left(d):
    """Regular-season weeks still to play, counting what's left of the current week."""
    cur = (d.get("league") or {}).get("current_week") or 1
    reg = d.get("reg_end") or 18
    return round(max(0.0, (reg - cur) + (1 - (d.get("week_progress") or 0))), 2)


def _mgr_by_team(polls):
    return {str(v["team_id"]): mid for mid, v in polls["rosters"]["teams"].items()}


def build(d, polls, prior=None):
    pr = prior or json.loads(PRIOR.read_text())
    cats = [c["id"] for c in d["cats"]]
    names = {c["id"]: c["name"] for c in d["cats"]}
    season = _season_lines(d)
    pbase, rep = _prior(pr)
    cost = {key(r[0]): r[2] for r in pr.get("draft", [])}

    # team key -> roster from polls.json (manager id -> team_id -> web team key)
    tk_of = {}
    for tk in d["teams"]:
        tk_of[tk.rsplit(".", 1)[-1]] = tk
    R = polls["rosters"]["teams"]
    raw = {}
    for mid, t in R.items():
        tk = tk_of.get(str(t["team_id"]))
        if not tk:
            continue
        il = set(t.get("il") or [])
        raw[tk] = [{"name": n, "il": n in il} for n in t["players"]]

    # project every rostered player, value against the rostered pool
    for ps in raw.values():
        for p in ps:
            p["line"], p["games"], p["src"] = project(p["name"], season, pbase, rep)
    proj = json.loads(PROJ.read_text()) if PROJ.exists() else None
    ros = bool(proj)
    MODE.update(gpw=1 if ros else GPW, skip_il=not ros)
    if ros:
        # Yahoo per-game averages times each player's projected remaining games. Players Yahoo hasn't projected
        # (unprojected rookies, anyone missing from the list) use this season's games once they have 3+, else the
        # fallback line above, at the median projected games.
        yrows = {key(n): v for n, v in proj["rows"].items() if v[0] > 0}
        gp_med = sorted(v[0] for v in yrows.values())[len(yrows) // 2]
        for ps in raw.values():
            for p in ps:
                v = yrows.get(key(p["name"]))
                if v:
                    gp = v[0]
                    per = {f: v[i + 1] / gp for i, f in enumerate(FIELDS)}
                    p["src"] = "yahoo"
                else:
                    gp = gp_med
                    cur = _lookup(season, p["name"])
                    if cur and cur["n"] >= MIN_GAMES:
                        per = {f: cur["sum"][f] / cur["n"] for f in FIELDS}
                        p["src"] = "season"
                    else:
                        per = p["line"]
                        p["src"] = "replacement" if p["src"] == "replacement" else "last season"
                p["gp"] = round(gp, 1)
                p["line"] = {f: round(per[f] * gp, 2) for f in FIELDS}   # rest-of-season totals
        rep = {f: round(rep[f] * gp_med, 2) for f in FIELDS}
    pool = [p["line"] for ps in raw.values() for p in ps]
    val = Valuer(pool)
    for ps in raw.values():
        for p in ps:
            p["z"] = val.z(p["line"])
            p["value_x"] = round(sum(p["z"].values()), 5)  # exact enough that the page picks the same starters
            p["value"] = p["value_x"]
    # market value: auction price mapped onto the stat-value scale by rank, fading as games pile up
    allp = [p for ps in raw.values() for p in ps]
    by_val = sorted((p["value"] for p in allp), reverse=True)
    priced = sorted((p for p in allp if cost.get(key(p["name"]))), key=lambda p: -cost[key(p["name"])])
    for i, p in enumerate(priced):
        p["market"] = by_val[min(i, len(by_val) - 1)]
    for p in allp:
        w = 0.5 + 0.5 * min(1, p["games"] / 30)
        if ros:   # Yahoo's projection already prices in role and health; draft price plays no part
            p["tv"] = round(p["value"], 2)
        else:
            p["tv"] = round(w * p["value"] + (1 - w) * p["market"], 2) if "market" in p else p["value"]
        p["cost"] = cost.get(key(p["name"]))
        p["dealable"] = (ros or not p["il"]) and (p["src"] != "replacement" or p["games"] >= MIN_GAMES)
    rep_player = {"name": "Waiver add", "il": False, "line": rep, "z": val.z(rep)}
    rep_player["value"] = rep_player["tv"] = round(sum(rep_player["z"].values()), 5)

    weeks = {tk: team_week(ps) for tk, ps in raw.items()}
    rk = ranks(weeks, cats)
    strength = {tk: round(allplay(weeks, tk, cats), 2) for tk in weeks}

    deals_all = find_deals(raw, weeks, rep_player, cats)
    deals = pick(deals_all)
    # best deal per team (from the full list, so every manager gets one if any exists)
    best = {}
    for dl in deals_all:
        for side in ("a", "b"):
            best.setdefault(dl[side], dl)

    def explain(dl):
        """Which categories move for each side under the deal."""
        _, _, w = evaluate(raw, weeks, dl["a"], dl["b"],
                           [p for p in raw[dl["a"]] if p["name"] in dl["give_a"]],
                           [p for p in raw[dl["b"]] if p["name"] in dl["give_b"]], rep_player, cats)
        nr = ranks(w, cats)
        out = {}
        for side in ("a", "b"):
            tk = dl[side]
            moves = [{"cat": c, "from": rk[tk][c], "to": nr[tk][c]} for c in cats if nr[tk][c] != rk[tk][c]]
            moves.sort(key=lambda m: m["to"] - m["from"])
            out[side] = moves
        return out

    for dl in deals + [x for x in best.values() if x not in deals]:
        dl["moves"] = explain(dl)

    # partner fit: best both-sides gain for every pair
    pairs = {}
    for dl in deals_all:
        pk = (dl["a"], dl["b"])
        if pk not in pairs:
            pairs[pk] = dl
    partners = sorted(pairs.values(), key=lambda x: -x["score"])[:8]

    # trade counts this season and last two
    hist = {}
    for yr in ("2024", "2025"):
        f = ROOT / "archive" / f"{yr}.json"
        if f.exists():
            a = json.loads(f.read_text())
            for t in a["teams"].values():
                hist.setdefault(t["manager"], {})[yr] = t.get("trades", 0)

    mname = {m["id"]: m["name"] for m in polls.get("managers", [])}
    mid_of = {tk_of.get(str(t["team_id"])): mid for mid, t in R.items()}
    teams = {}
    for tk, t in d["teams"].items():
        ps = sorted(raw.get(tk, []), key=lambda p: -p["value"])
        r = rk.get(tk, {})
        order = sorted(cats, key=lambda c: r[c])
        teams[tk] = {"name": t["name"], "manager": mname.get(mid_of.get(tk), t["manager"]), "mid": mid_of.get(tk), "trades": t.get("trades", 0), "moves": t.get("moves", 0),
                     "past_trades": hist.get(t["manager"], {}) or hist.get(mname.get(mid_of.get(tk)), {}), "strength": strength.get(tk),
                     "power": next((i + 1 for i, k in enumerate(sorted(strength, key=lambda k: -strength[k])) if k == tk), None),
                     "ranks": r, "strong": order[:3], "weak": order[-3:][::-1],
                     "week": {c: round(weeks[tk][c], 3 if c in PCT else 1) for c in cats} if tk in weeks else {},
                     "best": best.get(tk)}

    players = {}
    for tk, ps in raw.items():
        for p in ps:
            players[p["name"]] = {"team": tk, "il": p["il"], "gp": p.get("gp"), "value": p["value_x"], "tv": p["tv"], "dealable": p["dealable"], "games": p["games"], "src": p["src"],
                                  "cost": cost.get(key(p["name"])),
                                  "z": {c: round(v, 2) for c, v in p["z"].items()},
                                  "line": {f: round(v, 4) for f, v in p["line"].items()}}

    # for every player: the three teams whose holes he fills best (powers Block matches)
    for n, p in players.items():
        fits = []
        for tk, t in teams.items():
            if tk == p["team"] or not t["ranks"]:
                continue
            need = sum(max(0, p["z"].get(c, 0)) * (t["ranks"][c] - 6.5) / 5.5 for c in cats if t["ranks"][c] > 6)
            if need > 0:
                fits.append({"team": tk, "fit": round(need, 2), "cats": [c for c in t["weak"] if p["z"].get(c, 0) > 0.3]})
        p["fits"] = sorted(fits, key=lambda f: -f["fit"])[:3]

    return {"date": d["date"], "season_games": max((s["n"] for s in season.values()), default=0),
            "model": {"k": K, "active": N_ACTIVE, "min_gain": MIN_GAIN, "max_gap": MAX_GAP,
                      "prior": pr.get("per_game_season"), "weeks_left": weeks_left(d),
                      "ros": ros, "gpw": MODE["gpw"], "proj_as_of": proj["as_of"] if proj else None,
                      "tv_gap": round(pstdev([p["value"] for ps in raw.values() for p in ps]), 2)},
            "cats": [{"id": c["id"], "name": c["name"], "lower": c["id"] in LOWER, "pct": c["id"] in PCT} for c in d["cats"]],
            "replacement": {"value": rep_player["value"], "line": {f: round(v, 4) for f, v in rep.items()}, "z": rep_player["z"]},
            "teams": teams, "players": players, "deals": deals, "partners": partners,
            "deal_count": len(deals_all)}


def targets(t, n=3):
    """For each team: the category holes to fix and the teams that fit, by ranks alone.
    A partner is strong (top 4) where you're weak (bottom 4), and the reverse is what you can offer back.
    Each partner also gets the two players on his roster who carry your weak categories."""
    teams, players = t["teams"], t["players"]
    cats = [c["id"] for c in t["cats"]]
    untouch = {}
    for nm, p in players.items():
        untouch.setdefault(p["team"], []).append((p["tv"], nm))
    untouch = {k: {nm for _, nm in sorted(v, reverse=True)[:3]} for k, v in untouch.items()}

    def carriers(tk, cs):
        """Two players on tk who carry categories cs, skipping his top 3 (nobody's moving those)."""
        if not cs:
            return []
        rows = sorted(((nm, sum(max(0, p["z"].get(c, 0)) for c in cs)) for nm, p in players.items()
                       if p["team"] == tk and p.get("dealable") and nm not in untouch.get(tk, ())), key=lambda x: -x[1])
        return [nm for nm, v in rows[:2] if v > 0.3]

    out = {}
    for a, ta in teams.items():
        ra = ta["ranks"]
        if not ra:
            continue
        holes = sorted([c for c in cats if ra[c] >= 7], key=lambda c: -ra[c])[:4]
        surplus = sorted([c for c in cats if ra[c] <= 5], key=lambda c: ra[c])[:4]
        fits = []
        for b, tb in teams.items():
            rb = tb["ranks"]
            if b == a or not rb:
                continue
            they_have = [c for c in holes if rb[c] <= 4]
            you_have = [c for c in surplus if rb[c] >= 8]
            score = sum((ra[c] - rb[c]) for c in they_have) + sum((rb[c] - ra[c]) for c in you_have)
            if not they_have:
                continue
            fits.append({"team": b, "score": score, "they_have": they_have, "you_have": you_have,
                         "mutual": bool(you_have), "ask": carriers(b, they_have), "dangle": carriers(a, you_have)})
        fits.sort(key=lambda f: (not f["mutual"], -f["score"]))
        out[a] = {"holes": holes, "surplus": surplus, "partners": fits[:n]}
    return out


def public(t):
    """What goongazette.com gets: the model's inputs and team needs, never a suggested deal."""
    keep_p = ("team", "il", "value", "tv", "dealable", "games", "gp", "src", "cost", "z", "line")
    drop_t = ("best",)
    return {"date": t["date"], "season_games": t["season_games"], "model": t["model"], "cats": t["cats"],
            "replacement": t["replacement"],
            "teams": {k: {f: v for f, v in x.items() if f not in drop_t} for k, x in t["teams"].items()},
            "players": {n: {f: p[f] for f in keep_p} for n, p in t["players"].items()},
            "targets": targets(t)}


def write(d, polls=None):
    polls = polls or json.loads(POLLS.read_text())
    t = build(d, polls)
    OUT.write_text(json.dumps(t, ensure_ascii=False, separators=(",", ":")))
    PUBLIC.write_text(json.dumps(public(t), ensure_ascii=False, separators=(",", ":")))
    print("wrote trades", t["date"], len(t["deals"]), "deals of", t["deal_count"], "(public analyzer: no deals)")
    return t


def main():
    from . import web_current
    d = web_current.build_d(json.loads(web_current.SRC.read_text()))
    write(d)


if __name__ == "__main__":
    main()
