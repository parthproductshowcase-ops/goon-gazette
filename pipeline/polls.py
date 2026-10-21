"""Manager picks from goongazette.com/predict.html (Google Apps Script backend).

    python -m pipeline.polls     # snapshot every locked poll to docs/data/picks.json

The build calls `snapshot()` and hands `facts(a)` to the recap writer, so zingers can quote
what each manager put on the record and grade it against the standings.
"""
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "docs" / "data" / "polls.json"
OUT = ROOT / "docs" / "data" / "picks.json"


def snapshot():
    """Fetch locked polls from the endpoint and keep them in picks.json. Never raises."""
    try:
        cfg = json.loads(CFG.read_text())
    except Exception:
        return {}
    have = json.loads(OUT.read_text()) if OUT.exists() else {}
    if cfg.get("endpoint"):
        for p in cfg.get("polls", []):
            try:
                r = requests.get(cfg["endpoint"], params={"poll": p["id"]}, timeout=20).json()
            except Exception as e:
                print("picks fetch failed", p["id"], e)
                continue
            if r.get("ok") and r.get("locked") and r.get("picks"):
                have[p["id"]] = {"title": p["title"], "locks": p["locks"], "questions": p["questions"], "picks": r["picks"]}
    if have:
        OUT.write_text(json.dumps(have, indent=1, ensure_ascii=False))
    return have


def _norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def facts(a, polls=None):
    """Picks in plain words for the recap prompt, keyed to team names where we can match managers."""
    polls = polls if polls is not None else (json.loads(OUT.read_text()) if OUT.exists() else {})
    if not polls:
        return None
    try:
        names = {m["id"]: m["name"] for m in json.loads(CFG.read_text())["managers"]}
    except Exception:
        names = {}
    team_of = {}
    for tk, t in (a.get("teams") or {}).items():
        for k in (t.get("manager"), names.get(_norm(t.get("manager", "")))):
            if k:
                team_of[_norm(k)] = t["name"]
    rank = {}
    for i, p in enumerate(a.get("power") or []):
        rank[a["teams"][p["team"]]["name"]] = i + 1
    out = []
    for pid, p in polls.items():
        qs = {q["id"]: q for q in p["questions"]}
        rows = []
        for r in p["picks"]:
            nm = names.get(r["who"], r["who"])
            row = {"manager": nm, "team": team_of.get(_norm(nm)) or team_of.get(_norm(r["who"]))}
            for k, v in r["answers"].items():
                q = qs.get(k)
                if not q:
                    continue
                if q["type"] == "manager":
                    v = names.get(v, v)
                elif q["type"] == "managers":
                    v = [names.get(x, x) for x in v.split("|") if x]
                elif q.get("pick", 1) > 1:
                    v = [x for x in v.split("|") if x]
                row[q["q"]] = v
            if row["team"] and "finish" in r["answers"] and row["team"] in rank:
                row["predicted_own_finish"] = int(r["answers"]["finish"])
                row["current_power_rank"] = rank[row["team"]]
            rows.append(row)
        out.append({"poll": p["title"], "locked": p["locks"], "picks": rows})
    return out


def context(season="2025"):
    """Last season's facts shown next to each question, so managers pick with receipts in hand.
    Writes cfg["context"] in polls.json. Only real archive data; nothing estimated."""
    cfg = json.loads(CFG.read_text())
    a = json.loads((ROOT / "archive" / f"{season}.json").read_text())
    h = json.loads((ROOT / "docs" / "data" / "history.json").read_text())
    ids = {_norm(m["name"]): m["id"] for m in cfg["managers"]}
    people = {}
    for row in h.get("table", []):
        mid = ids.get(_norm(row["name"]))
        if not mid:
            continue
        people[mid] = {"fin": [[x["label"], x["rank"]] for x in row["history"]],
                       "po": [bool(x.get("made_playoffs")) for x in row["history"]],
                       "titles": row["titles"], "sackos": row["sackos"],
                       "trades": row["trades"], "moves": row["moves"]}
    t = h["trophies"][0] if h.get("trophies") else None
    champ = ids.get(_norm(t["champion"]["person"].split(":", 1)[-1])) if t else None
    sacko = ids.get(_norm(t["sacko"]["person"].split(":", 1)[-1])) if t else None
    mgr_of = {}
    for tk, tm in a["teams"].items():
        nm = "Parth" if tm["manager"] == "parth s" else tm["manager"]
        mgr_of[tk] = ids.get(_norm(nm))
    # category leaders and trailers for the season (TO: fewer is better)
    cats = {}
    for c in a["cats"]:
        vals = [(v[c["id"]], tk) for tk, v in a["season_stats"].items() if v.get(c["id"]) is not None]
        if not vals:
            continue
        best = (min if c.get("hb") is False else max)(vals)[1]
        worst = (max if c.get("hb") is False else min)(vals)[1]
        cats[c["name"]] = {"best": mgr_of.get(best), "worst": mgr_of.get(worst)}
    top = {p["player"]: p["rank"] for p in a["top_players"]}
    pricey = sorted(a["draft_all"], key=lambda d: -(d.get("cost") or 0))[:10]
    cfg["context"] = {
        "season": a["label"], "champ": champ, "sacko": sacko, "people": people, "cats": cats,
        "pricey": [{"player": d["player"], "cost": d["cost"], "by": mgr_of.get(d["team"]), "rank": top.get(d["player"])} for d in pricey],
        "top": [{"player": p["player"], "rank": p["rank"], "cost": p.get("cost"), "by": mgr_of.get(p.get("drafted_by"))} for p in a["top_players"][:12]],
    }
    CFG.write_text(json.dumps(cfg, indent=1, ensure_ascii=False))
    return cfg["context"]


if __name__ == "__main__":
    import sys
    if "--context" in sys.argv:
        print(json.dumps(context(), indent=1, ensure_ascii=False)[:3000])
    else:
        print(json.dumps(snapshot(), indent=1)[:2000])
