"""Build archive/<season>.json from data read off Yahoo's league web pages.

Stopgap while the Fantasy API returns 403. Raw page data lives in archive/_web/<season>.json
(collected in the browser); this turns it into the same shape history.fetch_missing writes,
then `python -m pipeline.history --recompute` builds the season pages.

    python -m pipeline.web_import 2025 2024

Files it writes carry "source": "yahoo-web". Once the API works, delete them and run
`python -m pipeline.history` to re-pull from the API (fetch_missing skips existing files).
"""
import json
import math
import re
import sys
from pathlib import Path

from . import history_stats as hs

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "archive"

CAT_IDS = {"FG%": "5", "FT%": "8", "3PTM": "10", "3PT%": "11", "PTS": "12", "OREB": "13",
           "REB": "15", "DREB": "14", "AST": "16", "ST": "17", "BLK": "18", "TO": "19", "DD": "27"}
ABBR = {"Field Goal Percentage": "FG%", "Free Throw Percentage": "FT%", "3-point Shots Made": "3PTM",
        "3-point Percentage": "3PT%", "Points Scored": "PTS", "Offensive Rebounds": "OREB",
        "Defensive Rebounds": "DREB", "Total Rebounds": "REB", "Assists": "AST", "Steals": "ST",
        "Blocked Shots": "BLK", "Turnovers": "TO", "Double-Doubles": "DD"}


def num(v):
    v = (v or "").replace(",", "").replace("$", "").replace("*", "").strip()  # * marks a tied category
    if v in ("", "-", "--"):
        return None
    return float(v) if "." in v else int(v)


def unpack(pk):
    """Expand the compact browser export into the page-table shape build() reads."""
    T = lambda xs: [{"t": x} for x in xs]
    return {
        "year": pk["year"], "lid": pk["lid"], "cats": pk["cats"], "playoffs": pk["playoffs"],
        "home": [T(["Rank", "Team", "W-L-T"])] + [[{"t": r[0]}, {"t": r[1], "h": "/t/" + str(r[2])}, {"t": r[3]}]
                                                  for r in pk["home"]],
        "teams": [T(["Team Name", "Manager", "Moves", "Trades"])] + [T(r) for r in pk["teams"]],
        "seasonStats": [[]] + [T(r) for r in pk["sstat"]],
        "draft": [T(["Pick", "Player", "Salary", "Team"])] + [
            [{"t": r[0]}, {"t": r[1], "h": f"/players/{r[2]}" if r[2] else None}, {"t": r[3]}, {"t": r[4]}]
            for r in pk["draft"]],
        "players": [[], T(["Players", "Roster Status"])] + [
            [{"t": r[0], "h": f"/players/{r[1]}" if r[1] else None}, {"t": r[2]}] for r in pk["players"]],
        "weeks": {wk: [{"mid1": str(m[0]), "mid2": str(m[1]), "table": [pk["mh"], [""] + m[2], [""] + m[3]]}
                       for m in ms] for wk, ms in pk["weeks"].items()},
    }


def build(raw):
    if "mh" in raw:
        raw = unpack(raw)
    year, lid = str(raw["year"]), raw["lid"]
    tk = lambda mid: f"{year}.l.{lid}.t.{mid}"
    # categories, in the league's order
    cats = []
    for part in re.findall(r"\(([^)]+)\)", raw["cats"]):
        name = {"3PTM": "3PTM"}.get(part, part)
        if name in CAT_IDS:
            cats.append({"id": CAT_IDS[name], "name": name, "hb": name != "TO", "pct": "%" in name})
    cid = {c["name"]: c["id"] for c in cats}
    po = re.match(r"(\d+) teams - Week (\d+)", raw["playoffs"])
    npo, po_start = int(po.group(1)), int(po.group(2))

    # teams: id from the standings links, manager/moves/trades from the Teams page
    home = raw["home"]
    hdr = [c["t"] for c in home[0]]
    name_to_mid, standings = {}, {}
    rows = []
    for r in home[1:]:
        name, mid = r[1]["t"], r[1]["h"].rstrip("/").split("/")[-1]
        name_to_mid[name] = mid
        w, l, t = (int(x) for x in r[hdr.index("W-L-T")]["t"].split("-"))
        rows.append((mid, int(r[0]["t"].lstrip("*").rstrip(".")), w, l, t))
    by_pct = sorted(rows, key=lambda x: (-(x[2] + .5 * x[4]) / max(1, x[2] + x[3] + x[4]), x[1]))
    seed = {x[0]: i for i, x in enumerate(by_pct, 1)}
    for mid, rank, w, l, t in rows:
        standings[tk(mid)] = {"rank": rank, "seed": seed[mid], "w": w, "l": l, "t": t}
    teams = {}
    th = [c["t"] for c in raw["teams"][0]]
    for r in raw["teams"][1:]:
        v = dict(zip(th, (c["t"] for c in r)))
        if v["Team Name"] not in name_to_mid:  # e.g. a "Co-Manager" row under a team
            continue
        mid = name_to_mid[v["Team Name"]]
        mgr = re.sub(r"(Commissioner|Co-Commissioner)$", "", v["Manager"]).strip()
        teams[tk(mid)] = {"name": v["Team Name"], "manager": mgr, "logo": None, "guid": None,
                          "moves": int(v.get("Moves") or 0), "trades": int(v.get("Trades") or 0)}

    def stat_row(header, row):
        out = {}
        for h, v in zip(header, row):
            if h in cid:
                out[cid[h]] = num(v)
        return out

    # weeks
    alive = {tk(m) for m, s in seed.items() if s <= npo}
    weeks = {}
    ties_in_playoffs = []
    for wk in sorted(raw["weeks"], key=int):
        wk_i, ms = int(wk), []
        for m in raw["weeks"][wk]:
            tbl = m["table"]
            header, ra, rb = tbl[0], tbl[1], tbl[2]
            a, b = tk(m["mid1"]), tk(m["mid2"])
            pa, pb = num(ra[-1]), num(rb[-1])
            a_st, b_st = stat_row(header, ra), stat_row(header, rb)
            # A "*" means equal at 3 decimals but not truly tied; Yahoo awarded it to someone.
            # The final category score says who, so nudge that side by a hair to match it.
            starred = [cid[h] for h, x in zip(header, ra) if h in cid and x.endswith("*")]
            if starred:
                wa = wb = 0
                for c in cats:
                    va, vb = a_st.get(c["id"]), b_st.get(c["id"])
                    if va is None or vb is None or va == vb:
                        continue
                    wa += (va > vb) == c["hb"]
                    wb += (va > vb) != c["hb"]
                for sid in starred:
                    hb = next(c["hb"] for c in cats if c["id"] == sid)
                    if pa > wa:
                        a_st[sid] = round(a_st[sid] + (3e-4 if hb else -3e-4), 4); wa += 1
                    elif pb > wb:
                        b_st[sid] = round(b_st[sid] + (3e-4 if hb else -3e-4), 4); wb += 1
            winner = a if pa > pb else b if pb > pa else None
            playoffs = wk_i >= po_start
            consolation = playoffs and not (a in alive and b in alive)
            if playoffs and winner is None:
                winner = a if standings[a]["seed"] < standings[b]["seed"] else b
                ties_in_playoffs.append((wk_i, teams[a]["name"], teams[b]["name"]))
            ms.append({"week": wk_i, "status": "postevent", "playoffs": playoffs, "consolation": consolation,
                       "winner": winner, "a": a, "b": b,
                       "a_stats": a_st, "b_stats": b_st})
        for m in ms:
            if m["playoffs"] and not m["consolation"]:
                alive.discard(m["b"] if m["winner"] == m["a"] else m["a"])
        weeks[wk_i] = ms

    # season category totals
    sh = [c["t"] for c in raw["seasonStats"][1]]
    season_stats = {}
    for r in raw["seasonStats"][2:]:
        mid = name_to_mid.get(r[1]["t"])
        if mid:
            season_stats[tk(mid)] = stat_row(sh, [c["t"] for c in r])

    # draft: every pick (auction: cost set; round = nomination block of 12)
    dh = [c["t"] for c in raw["draft"][0]]
    draft_all, pos_of, key_of = [], {}, {}
    for r in raw["draft"][1:]:
        v = dict(zip(dh, r))
        pick = int(v["Pick"]["t"].rstrip("."))
        m = re.match(r"(.+?) \(([A-Z]{2,4}) - ([^)]+)\)", v["Player"]["t"])
        name, pos = (m.group(1), m.group(3)) if m else (v["Player"]["t"], None)
        pid = re.search(r"/players/(\d+)", v["Player"]["h"] or "")
        pkey = f"nba.p.{pid.group(1)}" if pid else "name:" + name
        cost = num(v["Salary"]["t"]) if "Salary" in v else None
        draft_all.append({"pick": pick, "round": math.ceil(pick / 12), "cost": cost,
                          "team": tk(name_to_mid[v["Team"]["t"]]), "player_key": pkey, "player": name})
        pos_of[name], key_of[name] = pos, pkey
    draft = [{"pick": d["pick"], "team": d["team"], "player": d["player"]} for d in draft_all if d["round"] == 1]

    # top 12 by Yahoo's season rank, tagged with who drafted him and for what
    ph = [c["t"] for c in raw["players"][1]]
    by_key = {d["player_key"]: d for d in draft_all}
    top = []
    for r in raw["players"][2:14]:
        v = dict(zip(ph, (c["t"] for c in r)))
        name = v["Players"]
        link = dict(zip(ph, r))["Players"]["h"] or ""
        pid = re.search(r"/players/(\d+)", link)
        pkey = f"nba.p.{pid.group(1)}" if pid else key_of.get(name, "name:" + name)
        d = by_key.get(pkey)
        owner = name_to_mid.get(v.get("Roster Status", ""))
        top.append({"rank": len(top) + 1, "player_key": pkey, "player": name, "pos": pos_of.get(name),
                    "team": tk(owner) if owner else None,
                    "drafted_by": d["team"] if d else None, "cost": d["cost"] if d else None,
                    "pick": d["pick"] if d else None, "round": d["round"] if d else None})

    s = {"season": year, "label": hs.season_label(year), "league_key": f"{year}.l.{lid}",
         "name": "BallinWithMyGoons", "cats": cats, "end_week": max(weeks),
         "num_playoff_teams": npo, "playoff_start_week": po_start, "teams": teams,
         "standings": standings, "weeks": weeks, "season_stats": season_stats, "draft": draft,
         "top_players": top, "draft_all": draft_all, "source": "yahoo-web"}
    return s, ties_in_playoffs


def main():
    bundle = ARCHIVE / "_web" / "export.json"  # {"2025": {...}, "2024": {...}} from the browser
    if bundle.exists():
        for y, pk in json.loads(bundle.read_text()).items():
            (ARCHIVE / "_web" / f"{y}.json").write_text(json.dumps(pk))
    for year in sys.argv[1:]:
        raw = json.loads((ARCHIVE / "_web" / f"{year}.json").read_text())
        s, ties = build(raw)
        (ARCHIVE / f"{year}.json").write_text(json.dumps(s, indent=1))
        n = sum(len(v) for v in s["weeks"].values())
        print(f"wrote archive/{year}.json: {len(s['teams'])} teams, {len(s['weeks'])} weeks, {n} matchups, "
              f"{len(s['draft_all'])} picks, top {len(s['top_players'])}")
        for t in ties:
            print("  playoff tie broken by seed:", t)


if __name__ == "__main__":
    main()
