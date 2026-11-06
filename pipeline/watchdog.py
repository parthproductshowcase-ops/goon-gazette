"""Watchdog: fails loudly (GitHub failure email) when a run that should have happened didn't, or shipped
broken. A skipped schedule, a disabled cron-job.org job or a template fallback sends no email by itself
(Oct 29 and Nov 6 mornings, and the Lineup Card that silently stopped after Oct 28), so this checks the
committed files instead of trusting the runs.

  python -m pipeline.watchdog --check morning   # about 6:15am ET: paper out, status clean, last night's Tonight finished
  python -m pipeline.watchdog --check evening   # about 7:25pm ET: Lineup Card and the first Tonight update are in
Both also check the live site serves the committed paper (a failed Pages deploy leaves it stale).

Exit code 1 with one ::error:: annotation per problem; exit 0 with a ::notice:: when everything is fine.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(__file__).resolve().parent.parent / "docs" / "data"
ET = ZoneInfo("America/New_York")


def load(name):
    try:
        return json.loads((DATA / f"{name}.json").read_text())
    except (OSError, ValueError):
        return {}


def games_on(date):
    """Number of NBA games on an ET date; None if ESPN can't be reached (then assume there were games)."""
    try:
        from .today import nba_games
        g = nba_games(date)
        return len(g) if g else None
    except Exception:
        return None


def live_site():
    """The repo can be right while the site is stale: on Nov 6 the 10:03am paper sat undeployed for 50 minutes
    because GitHub Pages deploys timed out. Compare the live edition.json with the committed one."""
    import time
    import requests
    mine = load("edition")
    try:
        r = requests.get("https://goongazette.com/data/edition.json", params={"t": int(time.time())}, timeout=20)
        r.raise_for_status()
        live = r.json()
    except Exception as e:
        return [f"goongazette.com unreachable or broken: {e}"]
    if live.get("generated_at") != mine.get("generated_at"):
        return [f"Site is stale: live paper generated {live.get('generated_at')}, repo has {mine.get('generated_at')}. "
                "The Pages deploy failed or is stuck; check 'pages build and deployment' runs and re-run, or push an empty commit."]
    return []


def morning(now):
    probs = []
    yday = (now - timedelta(days=1)).date().isoformat()
    ed, st = load("edition"), load("status")
    if ed.get("sample") or ed.get("date") != yday:
        probs.append(f"Morning paper missing: edition.json is dated {ed.get('date')!r}, expected {yday}. "
                     "Check the cron-job.org 'Gazette morning paper' job is enabled, then dispatch daily.yml.")
    if st and not st.get("ok", True):
        probs.append(f"Morning build failed: {st.get('error', 'no error text')}")
    for w in st.get("warnings") or []:
        probs.append(f"Morning paper warning: {w}")
    n = games_on(yday)
    if n != 0:
        tn, td = load("tonight"), load("today")
        if tn.get("date") != yday:
            probs.append(f"Last night's Lineup Card never ran: tonight.json is dated {tn.get('date')!r}, expected {yday}. "
                         "Check the cron-job.org 'Gazette Lineup Card' job.")
        elif tn.get("by") == "template":
            probs.append("Last night's Lineup Card shipped template copy (Claude call failed).")
        if td.get("date") != yday:
            probs.append(f"Tonight never ran last night: today.json is dated {td.get('date')!r}, expected {yday}. "
                         "Check the cron-job.org 'Gazette Tonight' job.")
        else:
            if not td.get("final"):
                probs.append(f"Tonight stopped before the last game went final: last update {td.get('update')} "
                             f"of {td.get('updates')} at {td.get('as_of')}.")
            if td.get("by") == "template":
                probs.append("Tonight's last update shipped template copy (Claude call failed).")
    return probs


def evening(now):
    probs = []
    today = now.date().isoformat()
    if games_on(today) == 0:
        return probs
    tn, td = load("tonight"), load("today")
    if tn.get("date") != today:
        probs.append(f"Lineup Card missing: tonight.json is dated {tn.get('date')!r}, expected {today}. "
                     "Check the cron-job.org 'Gazette Lineup Card' job (6:55pm), then dispatch evening.yml with edition=card.")
    elif tn.get("by") == "template":
        probs.append("Lineup Card shipped template copy (Claude call failed).")
    if td.get("date") != today:
        probs.append(f"Tonight has not started: today.json is dated {td.get('date')!r}, expected {today}. "
                     "Check the cron-job.org 'Gazette Tonight' job (7:05pm).")
    elif td.get("by") == "template":
        probs.append("Tonight's latest update shipped template copy (Claude call failed).")
    return probs


def main():
    check = sys.argv[sys.argv.index("--check") + 1] if "--check" in sys.argv else "morning"
    now = datetime.now(ET)
    probs = (morning(now) if check == "morning" else evening(now)) + live_site()
    for p in probs:
        print(f"::error::{p}")
    if probs:
        sys.exit(1)
    print(f"::notice::Watchdog ({check}) {now:%Y-%m-%d %H:%M} ET: all clear")


if __name__ == "__main__":
    main()
