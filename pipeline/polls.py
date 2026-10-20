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
                row[q["q"]] = names.get(v, v) if q["type"] == "manager" else v
            if row["team"] and "finish" in r["answers"] and row["team"] in rank:
                row["predicted_own_finish"] = int(r["answers"]["finish"])
                row["current_power_rank"] = rank[row["team"]]
            rows.append(row)
        out.append({"poll": p["title"], "locked": p["locks"], "picks": rows})
    return out


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=1)[:2000])
