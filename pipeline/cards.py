"""Shareable cards: PNG images of a matchup, a Gauntlet verdict and last night's best line,
drawn with Pillow in the paper's network look, so a manager can post one straight into
the group chat. Written on every build and web publish into docs/cards/, with
docs/cards/index.json listing what exists. Run alone: python -m pipeline.cards [--sample]

No browser involved: the fonts are vendored Inter files (OFL) in pipeline/fonts/.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .stats import cmp_cat, team_id

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
OUT = ROOT / "docs" / "cards"
FONTS = ROOT / "pipeline" / "fonts"
ET = ZoneInfo("America/New_York")

W = H = 1080
INK, PAPER, CARD, RULE, MUTED, RED = "#121212", "#f2f2f2", "#ffffff", "#e3e3e3", "#6b6b6b", "#d7262d"
GOOD, MID, BAD, BLUE, MARK = "#1f9d55", "#e2a300", "#2f6fdf", "#d7262d", "#fff2a8"
BADGE = {"Legit": GOOD, "Gift": MID, "Robbed": BLUE, "Exposed": BAD}

_fonts = {}


def font(weight, size):
    key = (weight, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(FONTS / f"Inter-{weight}.ttf"), size)
    return _fonts[key]


def clean(s):
    """Inter has no emoji glyphs: drop symbols and private-use characters from names."""
    return re.sub(r"[\U0001F000-\U0001FFFF☀-➿️-]", "", str(s or "")).strip()


def fit(draw, text, f, max_w):
    """Trim text with an ellipsis so it fits max_w pixels at font f."""
    text = clean(text)
    if draw.textlength(text, font=f) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=f) > max_w:
        text = text[:-1].rstrip()
    return text + "…"


def wrap(draw, text, f, max_w, max_lines=3):
    words, lines, cur = clean(text).split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = fit(draw, lines[-1] + " " + " ".join(words[len(" ".join(lines).split()):]), f, max_w)
    return lines


def fmt(v, pct):
    if v is None:
        return "–"
    if isinstance(v, list):
        v = v[0]
    return f"{float(v):.3f}".lstrip("0") if pct else f"{round(float(v)):,}"


def frame(title, chip, sub=None):
    """White card on the grey page with the black masthead; returns (img, draw, y) with y the top of the body."""
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 132], fill=INK)
    d.text((48, 42), "THE GOON GAZETTE", font=font("ExtraBold", 44), fill="white")
    f = font("Bold", 26)
    cw = d.textlength(chip, font=f) + 40
    d.rounded_rectangle([W - 48 - cw, 46, W - 48, 94], radius=6, fill=RED)
    d.text((W - 48 - cw + 20, 56), chip, font=f, fill="white")
    y = 164
    d.text((48, y), title.upper(), font=font("ExtraBold", 34), fill=INK)
    if sub:
        d.text((48, y + 48), fit(d, sub, font("Medium", 24), W - 96), font=font("Medium", 24), fill=MUTED)
        y += 36
    d.rectangle([48, y + 56, W - 48, y + 60], fill=INK)
    # footer
    d.text((48, H - 70), "goongazette.com", font=font("Bold", 24), fill=INK)
    d.text((W - 48 - d.textlength("Fantasy data provided by Yahoo Fantasy", font=font("Medium", 20)), H - 66),
           "Fantasy data provided by Yahoo Fantasy", font=font("Medium", 20), fill=MUTED)
    return img, d, y + 84


def names(a):
    """Team name and manager display name per team key (display names from polls.json rosters)."""
    T = a.get("teams") or {}
    disp = {}
    try:
        pc = json.loads((DATA / "polls.json").read_text())
        by = {m["id"]: m["name"] for m in pc.get("managers", [])}
        for mid, r in (pc.get("rosters") or {}).get("teams", {}).items():
            for tk in T:
                if str(team_id(tk)) == str(r.get("team_id")) and mid in by:
                    disp[tk] = by[mid]
    except Exception:
        pass
    return {tk: (clean(t.get("name") or tk), clean(disp.get(tk) or t.get("manager") or "")) for tk, t in T.items()}


# ---------- 1. matchup ----------
def tier(m):
    if not any(c.get("a") or c.get("b") for c in m["cats"]):
        return "Not started", False
    gap, sw = abs(m["score"][0] - m["score"][1]), sum(1 for c in m["cats"] if c.get("swing"))
    if gap <= 1 and sw >= 1:
        return "Down to the wire", True
    if gap <= 1:
        return "Dead even", True
    if gap <= 3 and sw >= 2:
        return "Heating up", True
    if gap <= 3:
        return "Trading punches", True
    if gap <= 5:
        return "Slipping away", False
    return "Getting embarrassed", False


def matchup_card(a, m, NM, cats):
    wk = a.get("league", {}).get("current_week")
    tt, hot = tier(m)
    img, d, y = frame(f"The Fights  •  Week {wk}", f"THROUGH {through(a)}")
    d.text((48, y), tt.upper(), font=font("Bold", 24), fill=RED if hot else MUTED)
    y += 50
    # teams and score
    na, ma = NM[m["a"]]
    nb, mb = NM[m["b"]]
    s = m["score"]
    fs = font("ExtraBold", 96)
    score = f"{s[0]}–{s[1]}" + (f"–{s[2]}" if len(s) > 2 and s[2] else "")
    sw_ = d.textlength(score, font=fs)
    colw = (W - 96 - sw_ - 60) / 2
    fn = font("ExtraBold", 40)
    for lines, (nm, mg), x, right in ((wrap(d, na, fn, colw, 2), (na, ma), 48, False), (wrap(d, nb, fn, colw, 2), (nb, mb), W - 48, True)):
        yy = y
        for ln in lines:
            d.text((x - d.textlength(ln, font=fn) if right else x, yy), ln, font=fn, fill=INK)
            yy += 46
        d.text((x - d.textlength(mg, font=font("Medium", 24)) if right else x, yy + 4), mg, font=font("Medium", 24), fill=MUTED)
    d.text(((W - sw_) / 2, y - 6), score, font=fs, fill=INK)
    y += 150
    # clutch categories first, then the rest, up to 8 rows
    rows = [c for c in m["cats"] if c.get("swing")] + [c for c in m["cats"] if not c.get("swing")]
    rows = rows[:8]
    rh = 54
    d.rectangle([48, y, W - 48, y + 2], fill=RULE)
    fv, fc = font("Bold", 30), font("Medium", 26)
    for c in rows:
        cat = cats[c["cat"]]
        y += 8
        if c.get("swing"):
            d.rectangle([48, y, 54, y + rh - 16], fill=RED)
        va, vb = fmt(c.get("a"), cat["pct"]), fmt(c.get("b"), cat["pct"])
        d.text((72, y + 6), va, font=fv if c.get("lead") == "a" else fc, fill=INK if c.get("lead") == "a" else MUTED)
        cn = cat["name"]
        d.text(((W - d.textlength(cn, font=fc)) / 2, y + 6), cn, font=fc, fill=RED if c.get("swing") else MUTED)
        fb = fv if c.get("lead") == "b" else fc
        d.text((W - 72 - d.textlength(vb, font=fb), y + 6), vb, font=fb, fill=INK if c.get("lead") == "b" else MUTED)
        y += rh - 8
        d.rectangle([48, y, W - 48, y + 1], fill=RULE)
    if any(c.get("swing") for c in rows):
        d.text((48, y + 14), "Red categories are clutch: close enough to flip tonight.", font=font("Medium", 22), fill=MUTED)
    return img


# ---------- 2. gauntlet verdict ----------
def allplay(week, cats):
    L = week["lines"]
    teams = list(L)
    res = {}
    for t in teams:
        res[t] = {"vs": {}, "rec": [0, 0, 0], "cat": [0, 0, 0], "bycat": {c["id"]: [0, 0, 0] for c in cats}}
        for o in teams:
            if o == t:
                continue
            s = [0, 0, 0]
            for c in cats:
                r = cmp_cat(L[t].get(c["id"]), L[o].get(c["id"]), c["hb"], c["pct"])
                k = 0 if r > 0 else 1 if r < 0 else 2
                s[k] += 1
                res[t]["cat"][k] += 1
                res[t]["bycat"][c["id"]][k] += 1
            res[t]["vs"][o] = s
            res[t]["rec"][0 if s[0] > s[1] else 1 if s[1] > s[0] else 2] += 1
    return res


def gauntlet_card(a, week, R, me, NM, cats):
    n = len(R) - 1
    my = R[me]
    opp = (week.get("opp") or {}).get(me)
    real = my["vs"].get(opp) if opp else None
    apw = lambda r: r["rec"][0] + r["rec"][2] / 2
    order = sorted(R, key=lambda t: -apw(R[t]))
    rank = order.index(me) + 1
    v = None
    if real:
        won, lost = real[0] > real[1], real[1] > real[0]
        if won:
            v = "Legit" if apw(my) >= n / 2 else "Gift"
        elif lost:
            v = "Robbed" if apw(my) > n / 2 else "Exposed"
        else:
            v = "Even"
    w, l = my["rec"][0], my["rec"][1]
    roast = {
        "Legit": f"And beats {w} of {n}. No luck involved, unfortunately for everyone else.",
        "Gift": f"But loses to {l} of {n}. The schedule is doing the heavy lifting.",
        "Robbed": f"Yet beats {w} of {n}. Drew the wrong opponent at the wrong time.",
        "Exposed": f"And loses to {l} of {n}. Not unlucky. Just bad.",
        "Even": f"Beats {w} of {n}, loses to {l}. Perfectly mediocre.",
    }.get(v, "")
    sc = lambda s: f"{s[0]}-{s[1]}" + (f"-{s[2]}" if s[2] else "")
    live = week.get("live")
    img, d, y = frame(f"The Gauntlet  •  Week {week['week']}" + ("  •  Live" if live else "  •  Final"),
                      "EVERY TEAM VS EVERY TEAM", "Your schedule is luck. This is every team against every team, category by category.")
    nm, mg = NM[me]
    fn = font("ExtraBold", 44)
    d.text((48, y), fit(d, nm, fn, W - 96 - 260), font=fn, fill=INK)
    d.text((48, y + 52), mg, font=font("Medium", 26), fill=MUTED)
    if v and v != "Even":
        fb = font("ExtraBold", 28)
        bw = d.textlength(v.upper(), font=fb) + 44
        d.rounded_rectangle([W - 48 - bw, y + 4, W - 48, y + 58], radius=8, fill=BADGE[v])
        d.text((W - 48 - bw + 22, y + 16), v.upper(), font=fb, fill="white")
    y += 110
    d.text((48, y), f"Beats {w} of {n}" + (f", ties {my['rec'][2]}" if my["rec"][2] else ""), font=font("ExtraBold", 84), fill=INK)
    y += 108
    if real:
        up, down = real[0] > real[1], real[1] > real[0]
        on, _ = NM[opp]
        txt = (f"{'Up' if up else 'Down' if down else 'Tied'} {sc(real)} {'on' if up else 'to' if down else 'with'} {on}" if live
               else f"{'Beat' if up else 'Lost to' if down else 'Tied'} {on} {sc(real)}") + ". " + roast
    else:
        txt = roast or "No real matchup this week."
    fp = font("Medium", 30)
    for ln in wrap(d, txt, fp, W - 96, 2):
        d.text((48, y), ln, font=fp, fill=INK)
        y += 40
    y += 16
    # four facts
    bc = [(c, my["bycat"][c["id"]]) for c in cats]
    key = lambda s: s[0] + s[2] / 2
    best = max(bc, key=lambda x: key(x[1]))
    worst = min(bc, key=lambda x: key(x[1]))
    facts = [("All-play rank", f"{ordinal(rank)} of {n + 1}"), ("Category record", sc(my["cat"])),
             ("Owns", f"{best[0]['name']} {sc(best[1])}"), ("Bullied in", f"{worst[0]['name']} {sc(worst[1])}")]
    cw = (W - 96 - 3 * 16) / 4
    for i, (k, val) in enumerate(facts):
        x = 48 + i * (cw + 16)
        d.rectangle([x, y, x + cw, y + 104], fill=CARD, outline=RULE)
        d.text((x + 16, y + 14), k.upper(), font=font("Bold", 18), fill=MUTED)
        d.text((x + 16, y + 46), fit(d, val, font("ExtraBold", 30), cw - 32), font=font("ExtraBold", 30), fill=INK)
    y += 130
    # the field, as a strip of W/L/T chips in all-play order
    d.text((48, y), "VS THE FIELD", font=font("Bold", 20), fill=MUTED)
    y += 34
    chips = sorted([t for t in R if t != me], key=lambda o: -(my["vs"][o][0] - my["vs"][o][1]))
    f2 = font("Bold", 21)
    cw4 = (W - 96 - 3 * 10) / 4
    for i, o in enumerate(chips):
        x = 48 + (i % 4) * (cw4 + 10)
        yy = y + (i // 4) * 68
        s = my["vs"][o]
        res = "W" if s[0] > s[1] else "L" if s[1] > s[0] else "T"
        col = GOOD if res == "W" else BAD if res == "L" else MUTED
        on, _ = NM[o]
        d.rectangle([x, yy, x + cw4, yy + 60], fill=CARD, outline=RULE)
        d.rectangle([x, yy, x + 6, yy + 60], fill=col)
        d.text((x + 16, yy + 7), fit(d, on, f2, cw4 - 24), font=f2, fill=INK)
        d.text((x + 16, yy + 33), f"{res} {sc(s)}", font=f2, fill=col)
    return img


# ---------- 3. last night's best line ----------
def night_card(night, NM):
    stars = [s for s in (night.get("stars") or []) if s.get("name")]
    if not stars:
        return None
    TN = night.get("team_names") or {}
    for tk, v in TN.items():
        if tk not in NM:
            NM[tk] = (clean(v.get("name") if isinstance(v, dict) else v), clean(v.get("manager", "") if isinstance(v, dict) else ""))
    dt = datetime.strptime(night["date"], "%Y-%m-%d").strftime("%a, %b %-d")
    img, d, y = frame(f"Last Night  •  {dt}", "THE BEST LINES", "Ranked by value across all 11 categories. Rostered players only.")
    s = stars[0]
    nm, mg = NM.get(s["team"], (s["team"], ""))
    d.text((48, y), "1", font=font("ExtraBold", 120), fill=RED)
    x = 48 + d.textlength("1", font=font("ExtraBold", 120)) + 28
    fn = font("ExtraBold", 54)
    name = fit(d, s["name"], fn, W - 48 - x)
    tw = d.textlength(name, font=fn)
    d.rectangle([x - 8, y + 14, x + tw + 8, y + 78], fill=MARK)
    d.text((x, y + 12), name, font=fn, fill=INK)
    d.text((x, y + 88), fit(d, f"{nm}  •  {mg}" + ("  •  BENCHED" if s.get("benched") else ""), font("Medium", 26), W - 48 - x), font=font("Medium", 26), fill=RED if s.get("benched") else MUTED)
    y += 150
    # stat chips from the night's description
    chips = [c.strip() for c in (s.get("desc") or "").split(",") if c.strip()]
    x = 48
    fc = font("Bold", 28)
    for ch in chips[:6]:
        wdt = d.textlength(ch, font=fc) + 32
        if x + wdt > W - 48:
            x = 48
            y += 64
        d.rounded_rectangle([x, y, x + wdt, y + 52], radius=6, fill=INK)
        d.text((x + 16, y + 10), ch, font=fc, fill="white")
        x += wdt + 12
    y += 80
    val = f"+{s['value']:.1f} value"
    d.text((48, y), val, font=font("ExtraBold", 40), fill=GOOD if s["value"] > 0 else BAD)
    if s.get("result"):
        d.text((48 + d.textlength(val, font=font("ExtraBold", 40)) + 24, y + 10), clean(s["result"]), font=font("Medium", 26), fill=MUTED)
    y += 76
    d.rectangle([48, y, W - 48, y + 2], fill=RULE)
    y += 20
    for i, s in enumerate(stars[1:5], 2):
        nm, mg = NM.get(s["team"], (s["team"], ""))
        d.text((48, y), str(i), font=font("ExtraBold", 36), fill=MUTED)
        fn2 = font("ExtraBold", 32)
        d.text((100, y), fit(d, s["name"], fn2, 520), font=fn2, fill=INK)
        d.text((100, y + 40), fit(d, f"{nm}  •  {clean(s.get('desc') or '')}", font("Medium", 22), W - 148 - 140), font=font("Medium", 22), fill=MUTED)
        vv = f"+{s['value']:.1f}"
        d.text((W - 48 - d.textlength(vv, font=font("ExtraBold", 32)), y + 2), vv, font=font("ExtraBold", 32), fill=INK)
        y += 86
        d.rectangle([48, y - 12, W - 48, y - 11], fill=RULE)
        if y > H - 150:
            break
    return img


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def through(a):
    try:
        return datetime.strptime(a["date"], "%Y-%m-%d").strftime("%a, %b %-d").upper()
    except Exception:
        return "TODAY"


def write(a, night=None):
    """Draw every card for the current paper. Never raises past the caller's try."""
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    NM = names(a)
    cats = {c["id"]: c for c in a["cats"]}
    index = {"generated_at": datetime.now(ET).isoformat(timespec="minutes"), "date": a.get("date"),
             "week": a.get("league", {}).get("current_week"), "matchups": {}, "gauntlet": {}, "night": None}
    for m in a.get("matchups") or []:
        if m["a"] not in NM or m["b"] not in NM:
            continue
        f = f"matchup-{team_id(m['a'])}-{team_id(m['b'])}.png"
        matchup_card(a, m, NM, cats).save(OUT / f, optimize=True)
        index["matchups"][f"{team_id(m['a'])}-{team_id(m['b'])}"] = f
    weeks = (a.get("gauntlet") or {}).get("weeks") or []
    if weeks:
        wk = weeks[-1]
        R = allplay(wk, a["cats"])
        for me in R:
            if me not in NM:
                continue
            f = f"gauntlet-{team_id(me)}.png"
            gauntlet_card(a, wk, R, me, NM, a["cats"]).save(OUT / f, optimize=True)
            index["gauntlet"][str(team_id(me))] = f
        index["gauntlet_week"] = wk["week"]
    if night is None:
        try:
            night = json.loads((DATA / "night.json").read_text())
        except Exception:
            night = None
    if night and not night.get("sample"):
        img = night_card(night, NM)
        if img is not None:
            f = f"night-{night['date']}.png"
            img.save(OUT / f, optimize=True)
            index["night"] = f
            index["night_date"] = night["date"]
    (OUT / "index.json").write_text(json.dumps(index, indent=1))
    print("wrote", len(index["matchups"]), "matchup,", len(index["gauntlet"]), "gauntlet,", 1 if index["night"] else 0, "night cards")
    return index


if __name__ == "__main__":
    a = json.loads((DATA / "league.json").read_text())
    write(a)
