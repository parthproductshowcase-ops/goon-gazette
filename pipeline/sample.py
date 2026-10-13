"""Fake 12-team, 11-cat league so the site and stats can be built without Yahoo."""
import random

CATS = [  # (id, name, higher_is_better, pct, weekly mean, weekly sd)
    ("5", "FG%", True, True, .475, .018), ("8", "FT%", True, True, .785, .03),
    ("10", "3PTM", True, False, 52, 9), ("12", "PTS", True, False, 590, 55),
    ("13", "OREB", True, False, 55, 9), ("15", "REB", True, False, 225, 22),
    ("16", "AST", True, False, 140, 18), ("17", "ST", True, False, 40, 7),
    ("18", "BLK", True, False, 28, 7), ("19", "TO", False, False, 75, 9),
    ("27", "DD", True, False, 9, 3),
]
NAMES = ["Splash Bros & Sons", "Ankle Breakers Anon", "The Load Managers", "Punt Intended",
         "Glass Cleaners", "Free Throw Tragedies", "Dribble Trouble", "Bench Mob Mafia",
         "Triple Double Trouble", "Waiver Wire Warriors", "Tank Commanders", "Corner Three Kings"]
HANDLES = ["dub_d", "kj", "rocco", "nish", "tbone", "sam", "priya", "marcus", "dev", "ari", "jo", "luis"]
PLAYERS = ["Bench Guy Jr.", "Rookie McBucket", "Sixth Man Sal", "Big Ten Barry", "Corner Carl"]


def make(seed=7):
    rnd = random.Random(seed)
    cats = [{"id": i, "name": n, "hb": hb, "pct": p} for i, n, hb, p, _, _ in CATS]
    tks = [f"466.l.1.t.{i}" for i in range(1, 13)]
    teams = {tk: {"name": NAMES[i], "manager": HANDLES[i], "logo": None} for i, tk in enumerate(tks)}
    # each team has a persistent tilt per category; a couple of teams punt something
    tilt = {tk: {c[0]: rnd.gauss(0, .7) for c in CATS} for tk in tks}
    tilt[tks[3]]["8"] = -2.2   # Punt Intended punts FT%
    tilt[tks[3]]["19"] = -1.8  # ...and turnovers (bad: more TOs)
    tilt[tks[4]]["10"] = -2.0  # Glass Cleaners punt threes

    def week_line(tk, scale=1.0, noise=1.0):
        out = {}
        for i, n, hb, p, mu, sd in CATS:
            # for TO a "bad" tilt means more turnovers
            t = tilt[tk][i] if hb else -tilt[tk][i]
            v = rnd.gauss(mu + t * sd, sd * noise)
            out[i] = round(min(max(v, .3), .95), 3) if p else max(0, round(v * scale))
        return out

    # round-robin schedule for a 20-week regular season
    rot = tks[:]
    schedule = {}
    for w in range(1, 21):
        schedule[w] = [[rot[i], rot[-1 - i]] for i in range(6)]
        rot = [rot[0]] + [rot[-1]] + rot[1:-1]
    CUR = 8
    from . import players as P
    days, txns, windows = _world(rnd, tks, CUR, "2026-11-30")
    pmap = P.pct_map(cats, FRAC)
    team_week = lambda w: {tk: P.values_of(agg, cats) for tk, agg in
                           P.aggregate(days, P.date_range(w["start"], w["end"]), cats, pmap)[1].items()}
    weekly, results = {}, {}
    for w in range(1, CUR):
        weekly[w] = team_week(windows["last"]) if w == CUR - 1 else {tk: week_line(tk) for tk in tks}
        results[w] = [{"a": a, "b": b, "a_stats": weekly[w][a], "b_stats": weekly[w][b]} for a, b in schedule[w]]
    standings = {tk: {"w": 0, "l": 0, "t": 0} for tk in tks}
    for w in weekly:
        for a, b in schedule[w]:
            for i, n, hb, p, *_ in CATS:
                va, vb = weekly[w][a][i], weekly[w][b][i]
                if va == vb:
                    standings[a]["t"] += 1; standings[b]["t"] += 1
                elif (va > vb) == hb:
                    standings[a]["w"] += 1; standings[b]["l"] += 1
                else:
                    standings[b]["w"] += 1; standings[a]["l"] += 1
    ranked = sorted(tks, key=lambda t: -(standings[t]["w"] + .5 * standings[t]["t"]))
    for r, tk in enumerate(ranked, 1):
        standings[tk]["rank"] = r

    season = {}
    for tk in tks:
        season[tk] = {}
        for i, n, hb, p, *_ in CATS:
            vals = [weekly[w][tk][i] for w in weekly]
            season[tk][i] = round(sum(vals) / len(vals), 3) if p else sum(vals)

    cw = team_week(windows["current"])
    matchups = [{"week": CUR, "a": a, "b": b, "a_stats": cw[a], "b_stats": cw[b]} for a, b in schedule[CUR]]
    yday = windows["current"]["end"]
    daily = team_week({"start": yday, "end": yday})
    world = {"days": days, "transactions": txns, "windows": windows, "frac": FRAC}
    bench = P.bench_from_day(world, yday)

    recap = {  # hand-written example of the Claude voice, matched to this fake data
        "headline": "KINGS LAND THE COMBO",
        "subhead": "Corner Three Kings won last week, won last night, and now sit No. 1 in the power rankings.",
        "body": [
            "Corner Three Kings would have beaten 10 of 11 teams last week, then went out and took the night too. "
            "Even with Rookie McBucket's 29 points stuck on the bench. That's a flex.",
            "Triple Double Trouble sits fourth in the standings and the sims still give them only a one-in-three "
            "shot at the playoffs. The record is writing checks the categories can't cash.",
            "Punt Intended beat zero teams in all-play last week. Zero. The punt is now the whole strategy.",
        ],
        "zingers": [
            {"team": "Bench Mob Mafia", "line": "Sixth in the standings, near-lock in the sims. Quietly dangerous."},
            {"team": "Tank Commanders", "line": "Lost a week they would have won against 8 teams. Brutal draw."},
            {"team": "Triple Double Trouble", "line": "Enjoy fourth place while it lasts."},
        ],
    }
    return {
        "recap": recap,
        "sample": True, "date": "2026-12-08",
        "results": results, "schedule": {w: schedule[w] for w in range(CUR, 21)},
        "num_playoff_teams": 6, "reg_end": 20, "week_progress": 0.43,
        "league": {"name": "Ballinwithmygoons", "season": "2026", "current_week": CUR,
                   "start_week": 1, "end_week": 21, "num_teams": 12},
        "cats": cats, "teams": teams, "standings": standings, "weekly": weekly,
        "matchups": matchups, "season": season, "daily": daily, "bench": bench, **world,
    }


# ---------- fake player-level world (rosters, daily lines, transactions) ----------
FIRST = ["Jalen", "Marcus", "Tyrese", "Devin", "Luka", "Bam", "Zion", "Cade", "Evan", "Scottie", "Franz", "Jaren",
         "Desmond", "Mikal", "Alperen", "Anthony", "Tyler", "Jamal", "Dejounte", "Myles", "Keegan", "Walker",
         "Josh", "Jabari", "Paolo", "Jarrett", "Coby", "Ausar", "Dyson", "Amen", "Trey", "Brandon", "Shai", "Kris",
         "Immanuel", "Deni", "Herb", "Nic", "Derrick", "Onyeka", "Naz", "Jonas", "Aaron", "Kevin", "Rui", "Isaiah"]
LAST = ["Brooks", "Carter", "Okafor", "Vance", "Holloway", "Pryor", "Mbeki", "Sutton", "Ramirez", "Lindqvist",
        "Adeyemi", "Castellano", "Whitfield", "Kowalski", "Baptiste", "Nakamura", "Oduya", "Fontaine", "Reyes",
        "Hargrove", "Delacroix", "Ibarra", "Mensah", "Larkin", "Abernathy", "Quinlan", "Tavares", "Gutierrez",
        "Ellison", "Marsh", "Novak", "Pemberton", "Achebe", "Rourke", "Sorensen", "Valdez", "Wexler", "Yilmaz"]
SLOTS = ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util", "BN", "BN", "BN"]
FRAC = {"9004003": "FGM/A", "9007006": "FTM/A"}


def _game_line(rnd, sk):
    """One NBA game for a player with skill dict sk."""
    fga = max(2, round(rnd.gauss(sk["usage"], 3)))
    fgm = min(fga, max(0, round(fga * rnd.gauss(sk["fg"], .09))))
    fta = max(0, round(rnd.gauss(sk["usage"] * .3, 2)))
    ftm = min(fta, max(0, round(fta * rnd.gauss(sk["ft"], .12))))
    tpm = min(fgm, max(0, round(rnd.gauss(sk["three"], 1.3))))
    pts = 2 * fgm + tpm + ftm
    reb = max(0, round(rnd.gauss(sk["reb"], 2.5)))
    ast = max(0, round(rnd.gauss(sk["ast"], 2.2)))
    line = {"9004003": [fgm, fga], "9007006": [ftm, fta], "10": tpm, "12": pts,
            "13": max(0, min(reb, round(rnd.gauss(sk["reb"] * .25, 1)))), "15": reb, "16": ast,
            "17": max(0, round(rnd.gauss(sk["stl"], .9))), "18": max(0, round(rnd.gauss(sk["blk"], .9))),
            "19": max(0, round(rnd.gauss(sk["usage"] * .14, 1.1)))}
    line["27"] = int(sum(x >= 10 for x in (pts, reb, ast)) >= 2)
    return line


def _world(rnd, tks, cur_week, lw_start):
    from datetime import date, timedelta
    pid = iter(range(1, 999))
    used = set()

    def player(star=1.0):
        while True:
            nm = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
            if nm not in used:
                used.add(nm)
                break
        big = rnd.random() < .4
        return {"pk": f"466.p.{next(pid)}", "name": nm, "sk": {
            "usage": rnd.uniform(9, 19) * star, "fg": rnd.uniform(.50, .60) if big else rnd.uniform(.41, .49),
            "ft": rnd.uniform(.62, .74) if big else rnd.uniform(.76, .91), "three": rnd.uniform(0, .8) if big else rnd.uniform(1, 3.4) * star,
            "reb": rnd.uniform(7, 12) * star if big else rnd.uniform(3, 6), "ast": rnd.uniform(1.5, 3.5) if big else rnd.uniform(3, 7.5) * star,
            "stl": rnd.uniform(.5, 1.6), "blk": rnd.uniform(1, 2.4) if big else rnd.uniform(.2, .7)}}

    rosters = {tk: [player(rnd.uniform(.85, 1.25)) for _ in SLOTS] for tk in tks}
    lw = [(date.fromisoformat(lw_start) + timedelta(days=i)).isoformat() for i in range(7)]
    cw = [(date.fromisoformat(lw_start) + timedelta(days=7 + i)).isoformat() for i in range(2)]
    ts = lambda d, h=12: int(__import__("datetime").datetime.fromisoformat(d + f"T{h:02d}:00:00-05:00").timestamp())
    txns, tid = [], iter(range(1, 999))
    # earlier-season pickups already starting for their teams
    for tk in rnd.sample(tks, 8):
        p = rosters[tk][rnd.randint(2, 9)]
        txns.append({"id": f"t{next(tid)}", "ts": ts("2026-11-1" + str(rnd.randint(0, 9))), "kind": "add", "team": tk,
                     "source": rnd.choice(["freeagents", "waivers"]), "faab": None, "pk": p["pk"], "player": p["name"]})
    # moves during last week: (team, day index, star factor) with a drop each
    joins = {}
    leaves = {}
    for tk, di, star in zip(rnd.sample(tks, 7), [0, 1, 1, 2, 3, 4, 5], [1.35, 1.1, .95, 1.25, .9, 1.15, 1.0]):
        newp = player(star)
        slot = rnd.randint(3, 9)
        old = rosters[tk][slot]
        rosters[tk][slot] = newp
        joins[(tk, newp["pk"])] = lw[di]
        leaves[(tk, old["pk"])] = (lw[di], old)
        src = rnd.choice(["waivers", "freeagents"])
        bid = rnd.choice([3, 7, 12, 21, 35]) if src == "waivers" else None
        k = f"t{next(tid)}"
        txns.append({"id": k, "ts": ts(lw[di], 9), "kind": "add", "team": tk, "source": src, "faab": bid, "pk": newp["pk"], "player": newp["name"]})
        txns.append({"id": k, "ts": ts(lw[di], 9), "kind": "drop", "team": tk, "source": "team", "faab": None, "pk": old["pk"], "player": old["name"]})
    # one man's trash: a dropped player gets scooped and starts for someone else
    (dtk, dpk), (ddate, dold) = next(iter(leaves.items()))
    taker = next(t for t in tks if t != dtk)
    pickday = lw[min(6, lw.index(ddate) + 1)]
    slot = 9
    gone = rosters[taker][slot]
    rosters[taker][slot] = dold
    joins[(taker, dold["pk"])] = pickday
    leaves[(taker, gone["pk"])] = (pickday, gone)
    k = f"t{next(tid)}"
    txns.append({"id": k, "ts": ts(pickday, 10), "kind": "add", "team": taker, "source": "freeagents", "faab": None, "pk": dold["pk"], "player": dold["name"]})
    txns.append({"id": k, "ts": ts(pickday, 10), "kind": "drop", "team": taker, "source": "team", "faab": None, "pk": gone["pk"], "player": gone["name"]})

    days = {}
    for dt in lw + cw:
        days[dt] = {}
        for tk in tks:
            lines = []
            roster = list(rosters[tk]) + [old for (t, _), (d0, old) in leaves.items() if t == tk and dt < d0]
            for i, p in enumerate(roster):
                if joins.get((tk, p["pk"]), "") > dt:
                    continue
                if (tk, p["pk"]) in leaves and dt >= leaves[(tk, p["pk"])][0]:
                    continue
                pos = SLOTS[i] if i < len(SLOTS) else "BN"
                stats = _game_line(rnd, p["sk"]) if rnd.random() < .55 else {}
                lines.append({"pk": p["pk"], "name": p["name"], "pos": pos, "stats": stats})
            days[dt][tk] = lines
    windows = {"last": {"week": cur_week - 1, "start": lw[0], "end": lw[-1]},
               "current": {"week": cur_week, "start": cw[0], "end": cw[-1]}}
    return days, txns, windows


# ---------- fake past seasons ----------
PEOPLE = [("dub_d", .9), ("kj", .6), ("rocco", .3), ("nish", -.2), ("tbone", .1), ("sam", -.6),
          ("priya", .7), ("marcus", -.1), ("dev", .4), ("ari", -.4), ("jo", -.8), ("luis", .0)]
POOL = ["Splash Bros & Sons", "Ankle Breakers Anon", "The Load Managers", "Punt Intended",
        "Glass Cleaners", "Free Throw Tragedies", "Dribble Trouble", "Bench Mob Mafia",
        "Triple Double Trouble", "Waiver Wire Warriors", "Tank Commanders", "Corner Three Kings",
        "Brick City", "Pick & Roll Tide", "Euro Step Brothers", "The Process", "Moneyball Mavs",
        "Zone Defense", "Fadeaway Gang", "Box Out Bandits", "Lob City Leftovers", "Hack-a-Team",
        "Iso Joes", "Foul Trouble"]
STARS = ["Big Man One", "Point God", "Wing Wizard", "Stretch Five", "Two Way Tony", "Glass Eater",
         "Floor General", "Microwave Mike", "Rim Protector", "Sniper Steve", "Unicorn", "Dunk Don"]


def make_history(seed=11):
    rnd = random.Random(seed)
    cats = [{"id": i, "name": n, "hb": hb, "pct": p} for i, n, hb, p, _, _ in CATS]
    out = []
    for season in range(2025, 2020, -1):
        people = list(PEOPLE)
        if season <= 2022:  # 'luis' joined in 2023, replacing 'old_mike'
            people[11] = ("old_mike", -.5)
        tks = [f"{season - 1600}.l.9.t.{i}" for i in range(1, 13)]
        names = rnd.sample(POOL, 12)
        teams = {tk: {"name": names[i], "manager": people[i][0], "guid": "G_" + people[i][0].upper(),
                      "moves": rnd.randint(18, 95), "trades": rnd.choice([0, 0, 1, 1, 2, 3, 5])}
                 for i, tk in enumerate(tks)}
        skill = {tk: {c[0]: people[i][1] * .8 + rnd.gauss(0, .6) for c in CATS} for i, tk in enumerate(tks)}

        def line(tk, noise=1.0):
            o = {}
            for i, n, hb, p, mu, sd in CATS:
                t = skill[tk][i] if hb else -skill[tk][i]
                v = rnd.gauss(mu + t * sd, sd * noise)
                o[i] = round(min(max(v, .3), .95), 3) if p else max(0, round(v))
            return o

        def play(a, b):
            m = {"a": a, "b": b, "a_stats": line(a), "b_stats": line(b),
                 "playoffs": False, "consolation": False, "winner": None}
            sc = [0, 0]
            for i, n, hb, p, *_ in CATS:
                va, vb = m["a_stats"][i], m["b_stats"][i]
                if va != vb:
                    sc[0 if (va > vb) == hb else 1] += 1
            m["winner"] = a if sc[0] > sc[1] else b if sc[1] > sc[0] else None
            return m, sc

        weeks, rec = {}, {tk: [0, 0, 0] for tk in tks}
        for wk in range(1, 17):
            order = tks[:]
            rnd.shuffle(order)
            weeks[wk] = []
            for a, b in zip(order[::2], order[1::2]):
                m, sc = play(a, b)
                weeks[wk].append(m)
                tie = 11 - sc[0] - sc[1]
                rec[a][0] += sc[0]; rec[a][1] += sc[1]; rec[a][2] += tie
                rec[b][0] += sc[1]; rec[b][1] += sc[0]; rec[b][2] += tie
        seeds = sorted(tks, key=lambda t: -(rec[t][0] + .5 * rec[t][2]))

        def po(a, b, wk):
            m, _ = play(a, b)
            m["playoffs"] = True
            if m["winner"] is None:
                m["winner"] = a  # higher seed wins ties
            weeks.setdefault(wk, []).append(m)
            return m["winner"], (b if m["winner"] == a else a)

        s1, s2, s3, s4, s5, s6 = seeds[:6]
        w36, l36 = po(s3, s6, 17)
        w45, l45 = po(s4, s5, 17)
        f1, sl1 = po(s1, w45, 18)
        f2, sl2 = po(s2, w36, 18)
        champ, runner = po(f1, f2, 19)
        order = [champ, runner, sl1, sl2, l36, l45] + seeds[6:]
        standings = {tk: {"rank": order.index(tk) + 1, "seed": seeds.index(tk) + 1,
                          "w": rec[tk][0], "l": rec[tk][1], "t": rec[tk][2]} for tk in tks}
        season_stats = {}
        for tk in tks:
            season_stats[tk] = {}
            for i, n, hb, p, *_ in CATS:
                vals = [m[x][i] for ms in weeks.values() for m in ms if not m["playoffs"]
                        for x, k in (("a_stats", "a"), ("b_stats", "b")) if m[k] == tk]
                season_stats[tk][i] = round(sum(vals) / len(vals), 3) if p else sum(vals)
        draft_order = tks[:]
        rnd.shuffle(draft_order)
        draft = [{"pick": i + 1, "team": tk, "player": STARS[(i + season) % 12]} for i, tk in enumerate(draft_order)]
        out.append({"season": str(season), "label": f"{season}–{str(season + 1)[-2:]}", "name": "Ballinwithmygoons",
                    "cats": cats, "end_week": 19, "num_playoff_teams": 6, "playoff_start_week": 17,
                    "teams": teams, "standings": standings, "weeks": weeks,
                    "season_stats": season_stats, "draft": draft, "recap": None})
    return out
