"""Morning GroupMe post: tells the league Last Night is up.

Run by .github/workflows/groupme.yml after a push that changes docs/data/night.json
(the web morning edition) or after the API daily build. Stdlib only.

  python -m pipeline.announce --check            # prints date= and fresh= for the workflow
  python -m pipeline.announce --dry-run          # prints the message
  python -m pipeline.announce --wait 08:00       # just waits until 8am ET (no-op if later)
  python -m pipeline.announce --post             # posts now

Posting needs GROUPME_BOT_ID (the "GOON GAZETTE" bot). The bot ID is never printed.
"""
import argparse, datetime as dt, json, os, sys, time, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
NIGHT = Path(__file__).resolve().parent.parent / "docs" / "data" / "night.json"
# One-off extra note (e.g. a new feature), posted only on its ET date: {"date": "YYYY-MM-DD", "text": "..."}
NOTE = Path(__file__).resolve().parent.parent / "config" / "announce_note.json"
LINK = "goongazette.com"
WRAP = Path(__file__).resolve().parent.parent / "docs" / "data" / "wrap.json"


def wrap_line(now=None):
    """Link to the latest Weekly Wrap. Monday (the day it prints) gets the headline too."""
    try:
        w = json.loads(WRAP.read_text())
    except Exception:
        return ""
    head = ((w.get("copy") or {}).get("headline") or "").strip()
    today = (now or dt.datetime.now(ET)).date()
    if today.weekday() == 0 and head:
        return f"NEW WEEKLY WRAP (Week {w['week']}): {head} {LINK}/wrap.html"
    return f"Week {w['week']} Wrap: {LINK}/wrap.html"


def load():
    return json.loads(NIGHT.read_text())


def fresh(date, now=None):
    """A night is fresh if it's yesterday's games (ET) or newer; older means the paper is stale."""
    today = (now or dt.datetime.now(ET)).date()
    return dt.date.fromisoformat(date) >= today - dt.timedelta(days=1)


def extra_note(now=None):
    try:
        x = json.loads(NOTE.read_text())
    except Exception:
        return ""
    today = (now or dt.datetime.now(ET)).date().isoformat()
    return str(x.get("text") or "").strip() if x.get("date") == today else ""


def message(n, now=None):
    d = dt.date.fromisoformat(n["date"])
    head = f"LAST NIGHT IN THE GOONS ({d.strftime('%a')}, {d.strftime('%b')} {d.day})"
    notes = [x.strip() for x in (n.get("notes") or []) if str(x).strip()][:2]
    lines = [head] + notes + [f"Full damage: {LINK}"]
    wl = wrap_line(now)
    if wl:
        lines.append(wl)
    note = extra_note(now)
    if note:
        lines += ["", note]
    text = "\n".join(lines)
    return text if len(text) <= 1000 else text[:997] + "..."  # GroupMe caps at 1000


def wait_until(hhmm):
    h, m = map(int, hhmm.split(":"))
    now = dt.datetime.now(ET)
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    secs = (target - now).total_seconds()
    if secs > 0:
        print(f"waiting {int(secs // 60)} min until {hhmm} ET")
        sys.stdout.flush()
        time.sleep(secs)


def post(text):
    bot = os.environ.get("GROUPME_BOT_ID", "").strip()
    if not bot:
        sys.exit("GROUPME_BOT_ID is not set")
    req = urllib.request.Request("https://api.groupme.com/v3/bots/post",
                                 data=json.dumps({"bot_id": bot, "text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status not in (200, 201, 202):
            sys.exit(f"GroupMe returned {r.status}")
    print("posted")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--wait", default=None, help="HH:MM ET to hold the post until")
    a = ap.parse_args()
    n = load()
    if a.check:
        out = f"date={n['date']}\nfresh={'true' if fresh(n['date']) else 'false'}\n"
        print(out, end="")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(out)
        return
    if a.wait:
        wait_until(a.wait)  # the workflow pulls main again after this, then posts
        if not a.post:
            return
    if a.post:
        text = message(load())
        print(text)
        post(text)
        return
    print(message(n))


if __name__ == "__main__":
    main()
