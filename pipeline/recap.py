"""Writes the daily front page with Claude. Falls back to a template if no API key."""
import json
import os
import requests

VOICE = """You write the front page of "The Goon Gazette", the daily paper of a 12-team Yahoo
fantasy basketball league (head-to-head, categories) called Ballinwithmygoons.
Voice: a sharp, funny sports columnist who watched every box score last night. Confident,
specific, a little mean, never cruel. No announcer shtick, no exclamation points.
The page has a headline, a one-sentence subhead, and exactly seven zingers. A zinger is a
single sentence aimed at one manager's team about what LAST NIGHT did to their matchup:
the player they benched who went off, the category they just lost the lead in, the pickup
that is quietly winning them the week, the blowout they are sleepwalking into. Name the
player and the category when FACTS give you one. The best zingers make the manager wince
and the other eleven screenshot it for the group chat.
Rules: roast lineup decisions, trades and luck, never people's looks, families, jobs or
identities. PG-13. No made-up stats: use only the numbers in FACTS. Refer to teams by team
name. Short sentences. People read this on their phone."""

SCHEMA = """Return ONLY JSON:
{"headline": "<=7 words, Title Case, a pun or a verdict",
 "subhead": "one sentence that explains the headline",
 "body": [],
 "zingers": [{"team": "<team name>", "line": "one sentence, <=22 words, about last night's impact on their matchup"}]  // exactly 7, seven different teams
}"""


def _facts(a):
    nm = lambda tk: a["teams"][tk]["name"]
    cat = {c["id"]: c["name"] for c in a["cats"]}
    f = {"date": a["date"], "weeks_played": a["weeks_played"]}
    if a["daily"]:
        f["team_of_the_day"] = nm(a["daily"]["best"])
        f["brick_of_the_day"] = nm(a["daily"]["worst"])
        f["category_leaders_yesterday"] = {cat[k]: [nm(v["team"]), v["value"]]
                                           for k, v in a["daily"]["leaders"].items()}
    f["power_top3"] = [[p["rank"], nm(p["team"]), p["move"]] for p in a["power"][:3]]
    f["power_bottom"] = [nm(p["team"]) for p in a["power"][-2:]]
    big_movers = [p for p in a["power"] if abs(p["move"]) >= 2]
    f["power_movers"] = [[nm(p["team"]), p["move"]] for p in big_movers]
    if a["luck"]:
        f["luckiest"] = [nm(a["luck"][0]["team"]), a["luck"][0]["luck"]]
        f["unluckiest"] = [nm(a["luck"][-1]["team"]), a["luck"][-1]["luck"]]
    f["punting"] = {nm(p["team"]): [cat[c] for c in p["cats"]] for p in a["punts"]}
    f["matchups"] = [{
        "teams": [nm(m["a"]), nm(m["b"])], "score": m["score"][:2],
        "swing_cats": [{"cat": cat[r["cat"]], "leader": nm(m["a"] if r["lead"] == "a" else m["b"])}
                       for r in m["cats"] if r["swing"]]}
        for m in a["matchups"]]
    if a.get("race"):
        rows = a["race"]["rows"]
        f["playoff_race"] = {"spots": a["race"]["playoff_teams"], "weeks_left": a["race"]["weeks_left"],
                             "bubble": [[nm(r["team"]), round(r["playoff_odds"] * 100)] for r in rows if .15 <= r["playoff_odds"] <= .85],
                             "favorite_for_top_seed": nm(max(rows, key=lambda r: r["top_seed_odds"])["team"])}
    if a.get("last_week"):
        L = a["last_week"]
        f["last_week"] = {"week": L["week"], "mvp": nm(L["mvp"]), "dud": nm(L["dud"]),
                          "upset": [nm(L["upset"]["winner"]), nm(L["upset"]["loser"])] if L["upset"] else None,
                          "robbed": nm(L["robbed"]) if L["robbed"] else None}
    P = a.get("players") or {}
    for k in ("last", "current"):
        if P.get(k):
            f[f"{k}_week_top_players"] = [[x["name"], nm(x["team"])] for x in P[k]["top_players"][:3]]
            f[f"{k}_week_waiver_plays"] = [[x["name"], nm(x["team"]), "swung the matchup" if x["flipped"] else f"worth {x['net']} cats"]
                                          for x in P[k]["top_plays"][:2]]
    if P.get("waiver") and P["waiver"].get("best_pickup"):
        f["pickup_of_the_week"] = [P["waiver"]["best_pickup"]["player"], nm(P["waiver"]["best_pickup"]["team"])]
    if a["bench"]:
        f["bench_blunders_last_night"] = [{"team": nm(b["team"]), "player": b["top_player"],
                                           "player_points": b["top_pts"], "points_left_on_bench": b["wasted_pts"]}
                                          for b in a["bench"][:3]]
    return f


def fallback(a):
    nm = lambda tk: a["teams"][tk]["name"]
    top = nm(a["power"][0]["team"])
    zingers = []
    for b in (a["bench"] or [])[:3]:
        zingers.append({"team": nm(b["team"]),
                        "line": f"Left {b['top_player']} on the bench for {b['top_pts']} points. That is {b['wasted_pts']} points nobody counted."})
    if a["daily"]:
        zingers.append({"team": nm(a["daily"]["worst"]), "line": "Worst box score in the league last night. Every category, same story."})
        zingers.append({"team": nm(a["daily"]["best"]), "line": "Best night in the league. Enjoy it, the schedule turns eventually."})
    if a["luck"]:
        zingers.append({"team": nm(a["luck"][-1]["team"]), "line": "Would be winning against most of the league. Drew the one team they can't beat."})
        zingers.append({"team": nm(a["luck"][0]["team"]), "line": "Luckiest schedule in the league. The record says contender; the categories say coin flip."})
    for pw in a["power"]:
        if abs(pw.get("move", 0)) >= 2 and len(zingers) < 7:
            up = pw["move"] > 0
            zingers.append({"team": nm(pw["team"]), "line": f"{'Up' if up else 'Down'} {abs(pw['move'])} spots in the power rankings this week. {'Somebody set their lineup.' if up else 'Somebody forgot to.'}"})
    sub = f"{top} leads the power rankings."
    if a["daily"]:
        sub = f"{nm(a['daily']['best'])} owned last night; {nm(a['daily']['worst'])} would like it struck from the record."
    return {"headline": f"{top} Runs the Town", "subhead": sub, "body": [], "zingers": zingers[:7]}


def _call(system, user, max_tokens=1200):
    """Claude call that returns parsed JSON, or None on any failure."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
                  "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": user}]},
            timeout=90,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json()["content"])
        out = json.loads(text[text.find("{"): text.rfind("}") + 1])
        assert out.get("headline") and out.get("body")
        return out
    except Exception as e:  # never let the newspaper kill the build
        print("Claude call failed, using fallback:", e)
        return None


def write(a):
    return _call(VOICE, "FACTS:\n" + json.dumps(_facts(a)) + "\n\n" + SCHEMA) or fallback(a)


# ---------- past seasons ----------
SEASON_VOICE = """You are the race commentator for "The Goon Gazette" Grand Prix, the season-in-review
archive of a 12-team Yahoo fantasy basketball league (head-to-head, categories) called
Ballinwithmygoons. Voice: kart-race commentator, fast and funny. The season is a grand prix,
the playoffs are the final laps, the champion took the checkered flag, last place spun out.
Rules: roast lineup decisions, trades and luck, never people's looks, families, jobs or
identities. PG-13. No made-up stats: use only the numbers in FACTS. Keep it short."""
SEASON_SCHEMA = """Return ONLY JSON:
{"headline": "<=8 words, ALL CAPS", "subhead": "one sentence",
 "body": ["3 short paragraphs: the champion's run, the biggest storyline, the cellar"]}"""


def _season_facts(sm):
    nm = lambda tk: sm["teams"][tk]["name"]
    mg = lambda tk: sm["teams"][tk]["manager"]
    st = {r["team"]: r for r in sm["standings"]}
    f = {"season": sm["label"],
         "champion": [nm(sm["champion"]), mg(sm["champion"]), "seed", st[sm["champion"]]["seed"]],
         "runner_up": [nm(sm["runner_up"]), mg(sm["runner_up"])],
         "last_place": [nm(sm["sacko"]), mg(sm["sacko"])],
         "regular_season_leader": nm(sm["reg_leader"]),
         "final_round": [[nm(g["a"]), nm(g["b"]), g["score"][:2]] for g in (sm["bracket"][-1]["games"] if sm["bracket"] else [])],
         "awards": [[a["title"], nm(a["team"]), a["note"]] for a in sm["awards"]]}
    return f


def season_fallback(sm):
    nm = lambda tk: sm["teams"][tk]["name"]
    st = {r["team"]: r for r in sm["standings"]}
    c, ru, sk = sm["champion"], sm["runner_up"], sm["sacko"]
    seed = st[c]["seed"]
    run = "from the top seed" if seed == 1 else f"as the No. {seed} seed" if seed else "through the bracket"
    body = [f"{nm(c)} won the {sm['label']} title {run}, beating {nm(ru)} when it counted."]
    if sm["reg_leader"] != c:
        body.append(f"{nm(sm['reg_leader'])} owned the regular season and went home without the trophy.")
    else:
        body.append("Wire to wire: best regular season, best playoffs, no notes.")
    body.append(f"{nm(sk)} finished last. The Sacko is theirs to keep.")
    head = f"{nm(c).upper()} TAKE THE CROWN" if len(nm(c)) < 22 else "A NEW KING IS CROWNED"
    return {"headline": head, "subhead": f"The {sm['label']} season in review.", "body": body}


def season_story(sm):
    return _call(SEASON_VOICE, "FACTS:\n" + json.dumps(_season_facts(sm)) + "\n\n" + SEASON_SCHEMA, 900) \
        or season_fallback(sm)


# ---------- league lore (history page intro) ----------
def lore(h):
    nm = h["people"]
    facts = {"trophy_case": [[t["label"], nm[t["champion"]["person"]], nm[t["sacko"]["person"]]] for t in h["trophies"]],
             "superlatives": [[s["title"], nm[s["person"]], s["note"]] for s in h["superlatives"]],
             "rivalries": [[nm[r["a"]], nm[r["b"]], f"{r['w']}-{r['l']}-{r['t']}"] for r in h["rivalries"]["lopsided"][:3]]}
    schema = """Return ONLY JSON: {"headline": "<=8 words, ALL CAPS", "subhead": "one sentence",
 "body": ["2 short paragraphs of league lore. Refer to managers by name."]}"""
    out = _call(SEASON_VOICE,
                "FACTS:\n" + json.dumps(facts) + "\n\n" + schema, 700)
    if out:
        return out
    top = h["table"][0]
    return {"headline": "THE BOOK OF GOONS",
            "subhead": f"{len(h['seasons'])} seasons of glory, grudges and last-place finishes.",
            "body": [f"{top['name']} sits on top of the all-time table with {top['titles']} "
                     f"title{'s' if top['titles'] != 1 else ''}. Everyone else is chasing."]}
