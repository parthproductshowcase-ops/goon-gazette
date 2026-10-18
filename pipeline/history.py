"""Past seasons + all-time history.

    python -m pipeline.history              # fetch any missing finished seasons, then recompute
    python -m pipeline.history --recompute  # no Yahoo calls; rebuild pages from archive/
    python -m pipeline.history --sample     # fake 5-season history

Finished seasons never change, so each one is pulled from Yahoo once into
archive/<season>.json and committed. The daily workflow runs this too: it costs a few
calls to walk the season chain and picks up the new season the morning after it ends.
Edit config/managers.json to merge a manager's identities or set display names, then
run --recompute.
"""
import json
import os
import sys
from pathlib import Path

from . import history_stats as hs, recap

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "archive"
OUT = ROOT / "docs" / "data"
N_SEASONS = 5


def _top_with_draft(y, lk):
    """Top 12 players at season's end, each tagged with who drafted him and for what."""
    draft_all = y.draft_results(lk)
    by_key = {d["player_key"]: d for d in draft_all}
    top = y.top_players(lk, 12)
    for t in top:
        d = by_key.get(t["player_key"])
        t["drafted_by"] = d["team"] if d else None
        t["cost"] = d["cost"] if d else None
        t["pick"] = d["pick"] if d else None
        t["round"] = d["round"] if d else None
    return top, draft_all


def fetch_missing(n=N_SEASONS):
    from .yahoo import Yahoo, load_dotenv
    load_dotenv()
    y = Yahoo(os.environ["YAHOO_CLIENT_ID"], os.environ["YAHOO_CLIENT_SECRET"],
              os.environ["YAHOO_REFRESH_TOKEN"])
    lk = y.league_key(os.environ["YAHOO_LEAGUE_ID"])
    ARCHIVE.mkdir(exist_ok=True)
    got = 0
    while lk and got < n:
        meta = y.meta(lk)
        prev = meta["renew"].replace("_", ".l.") if meta.get("renew") else None
        if not meta["is_finished"]:  # the live season; history starts with the one before it
            lk = prev
            continue
        got += 1
        path = ARCHIVE / f"{meta['season']}.json"
        if path.exists():
            old = json.loads(path.read_text())
            if "top_players" not in old:
                try:
                    old["top_players"], old["draft_all"] = _top_with_draft(y, lk)
                    path.write_text(json.dumps(old, indent=1))
                    print("added top players to", meta["season"])
                except Exception as e:
                    print("top players fetch failed:", e)
            lk = prev
            continue
        print("fetching", meta["season"], lk)
        cats = y.cats(lk)
        ids = {c["id"] for c in cats}
        teams, standings = y.teams_and_standings(lk)
        weeks = {wk: y.scoreboard(lk, wk, ids) for wk in range(meta["start_week"], meta["end_week"] + 1)}
        try:
            draft = y.draft_first_round(lk)
        except Exception as e:
            print("draft fetch failed:", e)
            draft = []
        try:
            top, draft_all = _top_with_draft(y, lk)
        except Exception as e:
            print("top players fetch failed:", e)
            top, draft_all = [], []
        s = {"season": meta["season"], "label": hs.season_label(meta["season"]), "league_key": lk,
             "name": meta["name"], "cats": cats, "end_week": meta["end_week"],
             **y.playoff_settings(lk), "teams": teams, "standings": standings, "weeks": weeks,
             "season_stats": y.team_stats(lk, ids, "season"), "draft": draft,
             "top_players": top, "draft_all": draft_all}
        path.write_text(json.dumps(s, indent=1))
        lk = prev
    if got < n:
        print(f"only found {got} finished seasons in the league's history")


def load_archives(n=N_SEASONS):
    files = sorted(ARCHIVE.glob("[0-9]*.json"), key=lambda p: p.stem, reverse=True)[:n]
    out = []
    for f in files:
        s = json.loads(f.read_text())
        s["weeks"] = {int(k): v for k, v in s["weeks"].items()}
        s["_path"] = f
        out.append(s)
    return out


def compute(archives, cfg, write_back=True, sample=False):
    (OUT / "seasons").mkdir(parents=True, exist_ok=True)
    summaries = []
    for s in archives:
        sm = hs.season_summary(s, cfg)
        sm["sample"] = sample
        if not sm["recap"]:  # written once, cached in the archive so it isn't re-billed
            sm["recap"] = s["recap"] = recap.season_story(sm)
            if write_back:
                path = s.pop("_path")
                path.write_text(json.dumps(s, indent=1))
        (OUT / "seasons" / f"{sm['season']}.json").write_text(json.dumps(sm, indent=1))
        summaries.append(sm)
    h = hs.history(archives, summaries, cfg)
    h["sample"] = sample
    lore_path = ARCHIVE / "_lore.json"
    key = ",".join(f"{t['season']}:{t['champion']['person']}" for t in h["trophies"])
    cached = json.loads(lore_path.read_text()) if lore_path.exists() else {}
    if cached.get("key") == key:
        h["lore"] = cached["lore"]
    else:
        h["lore"] = recap.lore(h)
        if write_back:
            lore_path.write_text(json.dumps({"key": key, "lore": h["lore"]}, indent=1))
    (OUT / "history.json").write_text(json.dumps(h, indent=1))
    print("wrote", len(summaries), "seasons + history.json")


def main():
    cfg_path = ROOT / "config" / "managers.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    if "--sample" in sys.argv:
        from .sample import make_history
        archives = make_history()
        compute(archives, cfg, write_back=False, sample=True)
        return
    if "--recompute" not in sys.argv:
        fetch_missing()
    archives = load_archives()
    if not archives:
        print("no finished seasons archived yet")
        return
    compute(archives, cfg)
    # print the identity table so managers.json can be filled in
    seen = {}
    for s in archives:
        for t in s["teams"].values():
            seen.setdefault(hs.person_of(t, cfg), set()).add(f"{s['season']}:{t.get('manager')}")
    print("\nmanagers found (merge duplicates in config/managers.json):")
    for pid, names in seen.items():
        print(" ", pid, sorted(names))


if __name__ == "__main__":
    main()
