"""Writes the daily front page with Claude. Falls back to a template if no API key."""
import json
import os
import requests

VOICE = """You write the Squawk Box for "The Goon Gazette", the daily paper of a 12-team Yahoo
fantasy basketball league (head-to-head, categories) called Ballinwithmygoons.
The whole point of the paper is to expose every manager's atrocities to the other eleven.
Voice: a ruthless beat writer who read every box score last night and is personally offended
by what he saw. Snappy, sharp, humiliating. Short declarative sentences. Name names. No
announcer shtick, no exclamation points, no hedging, no compliments unless they set up a
knife. Praise only goes to a team that is embarrassing someone else.
You produce exactly seven zingers, each one sentence, each aimed at a different team, about
what LAST NIGHT did to their matchup: the player left on the bench who went off, the category
lead they coughed up, the pickup carrying a lazy roster, the blowout they are sleepwalking
into, the lucky stiff who should be losing. Use the player's name and the category whenever
FACTS give you one. The test for every line: would the manager wince, and would the other
eleven screenshot it for the group chat.
Hard rules: roast lineups, trades, pickups, laziness and luck. Never looks, families, jobs,
health, money or identities. No slurs, PG-13. No made-up stats: use only numbers in FACTS.
Refer to teams by team name. Nothing longer than 22 words.
Matchup weeks run Monday to Sunday. If FACTS include last_night_was_final_day_of_week, last night
closed that week: lead with final_results (who won the week, by how much, who blew it on the last
day). Those are the opponents last night was played against; new_matchups_starting_today only
begin today, so never describe last night as a game against a new_matchups opponent.
If FACTS include manager_picks_on_record, use at most two of them: a manager whose prediction
is aging badly (picked himself 2nd and sits 10th, called a bust who is carrying a rival, or the
trash talk he signed). Quote picks exactly as recorded."""

SCHEMA = """Return ONLY JSON:
{"headline": "<=7 words, Title Case, the day's verdict",
 "subhead": "one sentence",
 "body": [],
 "zingers": [{"team": "<team name>", "line": "one sentence, <=22 words, about last night's impact on their matchup"}],  // exactly 7, seven different teams
 "night": ["5 notes for the Last Night page, <=30 words each, from last_night only: the best line, the worst brick, the bench blunder, the free agent who balled (say whether he is still on the wire), the night's winner and loser; name the player and the team; exact numbers from last_night only"]
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
    nw, L = a.get("night_week"), a.get("last_week")
    if nw and L and L.get("week") == nw and nw < a["league"]["current_week"]:
        # last night was the final day of a matchup week: the story is who won that week,
        # and the night's head-to-head is against that week's opponent, not the new one
        f["last_night_was_final_day_of_week"] = nw
        f["final_results"] = [{"teams": [nm(g["a"]), nm(g["b"])], "score": g["score"],
                               "winner": nm(g["winner"]) if g.get("winner") else "tie"} for g in L["games"]]
        f["new_matchups_starting_today"] = [m["teams"] for m in f.pop("matchups")]
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
    if a.get("picks"):
        f["manager_picks_on_record"] = a["picks"]
    if a.get("_night"):
        f["last_night"] = _night_facts(a["_night"], nm)
    return f


def _night_facts(n, nm):
    """What night.build worked out, trimmed for the prompt (no derived prose, just lines)."""
    def row(r):
        o = {"player": r["name"], "team": nm(r["team"]), "line": r.get("desc", "")}
        if r.get("benched"):
            o["benched"] = True
        return o
    f = {"best_lines": [row(r) for r in (n.get("stars") or [])[:4]],
         "bricks": [row(r) for r in (n.get("bricks") or [])[:3]],
         "left_on_the_bench": [row(r) for r in (n.get("bench") or [])[:2]],
         "free_agents_who_balled": [{"player": r["name"], "line": r.get("desc", ""),
                                     "now": r.get("owned_by") or "still on the wire"} for r in (n.get("free") or [])[:3]]}
    T = n.get("teams") or []
    f["best_team_night"] = [nm(T[0]["team"]), T[0]["night"]] if T else None
    played = [t for t in T if t.get("players")]
    f["worst_team_night"] = [nm(played[-1]["team"]), played[-1]["night"]] if played else None
    f["night_record_vs_opponent"] = {nm(t["team"]): {"vs": nm(t["opp"]) if t.get("opp") else None, "won_lost_tied": t["night"]}
                                     for t in T if t.get("opp")}
    idle = [nm(t["team"]) for t in T if not t.get("started")]
    if idle:
        f["teams_with_nobody_playing"] = idle
    return f


def fallback(a):
    nm = lambda tk: a["teams"][tk]["name"]
    top = nm(a["power"][0]["team"])
    zingers = []
    for b in (a["bench"] or [])[:3]:
        zingers.append({"team": nm(b["team"]),
                        "line": f"Benched {b['top_player']} for {b['top_pts']} points. {b['wasted_pts']} points rotting on the pine while you lost a category."})
    if a["daily"]:
        zingers.append({"team": nm(a["daily"]["worst"]), "line": "Worst box score in the league last night. Twelve teams, and you found the bottom."})
        zingers.append({"team": nm(a["daily"]["best"]), "line": "Best night in the league, which says more about the other eleven than about you."})
    if a["luck"]:
        zingers.append({"team": nm(a["luck"][-1]["team"]), "line": "Beats most of the league on paper and still loses. Unlucky is one word for it."})
        zingers.append({"team": nm(a["luck"][0]["team"]), "line": "Luckiest schedule in the league. The record is a lie and everybody knows it."})
    for pw in a["power"]:
        if abs(pw.get("move", 0)) >= 2 and len(zingers) < 7:
            up = pw["move"] > 0
            zingers.append({"team": nm(pw["team"]), "line": f"{'Up' if up else 'Down'} {abs(pw['move'])} spots in the power rankings this week. {'Somebody finally set a lineup.' if up else 'Somebody stopped setting one.'}"})
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
                  "max_tokens": max(max_tokens, 4000), "system": system,
                  # Sonnet 5.5 thinks by default and spent the whole output budget on a thinking
                  # block (Oct 28: stop_reason max_tokens, no text, so every page fell back to templates)
                  "thinking": {"type": "between_tools"},
                  "messages": [{"role": "user", "content": user}]},
            timeout=90,
        )
        if os.environ.get("GG_DEBUG"):
            print("claude http", r.status_code, r.text[:300])
        r.raise_for_status()
        body = r.json()
        text = "".join(b.get("text", "") for b in body["content"])
        if os.environ.get("GG_DEBUG"):
            print("claude stop_reason", body.get("stop_reason"), "blocks", [b.get("type") for b in body["content"]],
                  "usage", body.get("usage"))
            print("claude text[:800]:", text[:800])
        out = json.loads(text[text.find("{"): text.rfind("}") + 1])
        assert out.get("headline") or out.get("zingers") or out.get("body") or out.get("notes")
        return out
    except Exception as e:  # never let the newspaper kill the build
        print("Claude call failed, using fallback:", e)
        return None


def write(a, night=None):
    """Squawk Box (headline, subhead, 7 zingers) plus, when `night` (night.build's dict) is given,
    the 5 Last Night notes under "night". Falls back to templates without a key."""
    a = dict(a, _night=night) if night else a
    out = _call(VOICE, "FACTS:\n" + json.dumps(_facts(a)) + "\n\n" + SCHEMA, max_tokens=2000)
    if out and out.get("zingers"):  # one zinger per team, in the order written; drop stray keys
        seen, zs = set(), []
        for z in out["zingers"]:
            if isinstance(z, dict) and z.get("team") and z.get("line") and z["team"] not in seen:
                seen.add(z["team"]); zs.append({"team": z["team"], "line": z["line"]})
        out["zingers"] = zs[:7]
    return out or fallback(a)


# ---------- past seasons ----------
SEASON_VOICE = """You write the season obituaries and league lore for "The Goon Gazette", the paper of a
12-team Yahoo fantasy basketball league called Ballinwithmygoons. The paper exists to put
every manager's crimes on the record permanently. Voice: unfiltered, savage, funny, specific.
Short sentences. Debilitating zingers. The champion gets grudging respect and a jab; everyone
else gets the truth about how they lost.
Do NOT recite records, win percentages, luck numbers or category tallies. Find the insight
instead: a team winning the category it usually loses (or losing the one it owns), a player one
manager cut who burned him later, a team parked below the playoff line for months that snuck in
on the last day, a contender that collapsed down the stretch, an expensive draft pick that
vanished. One sharp number inside a sentence is fine when it lands the joke.
Hard rules: roast results, rosters, trades, pickups, laziness and luck. Never looks, families,
jobs, health, money or identities. No slurs, PG-13. No em dashes. No made-up facts: only what
is in FACTS."""
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
    idn = sm.get("identity") or {}
    f["category_identity"] = {nm(tk): {"usually_wins": v["strong"], "usually_loses": v["weak"]} for tk, v in idn.items()}
    race = sm.get("race") or {}
    npo = sm.get("num_playoff_teams") or 0
    if race.get("weeks") and npo:
        f["race"] = {nm(tk): {"weeks_in_playoff_spot": sum(1 for x in rk if x <= npo), "best": min(rk), "worst": max(rk),
                              "last_6_weeks": rk[-6:], "seed": st[tk]["seed"]} for tk, rk in race["ranks"].items()}
    f["closest_playoff_games"] = [{"round": g["round"], "a": nm(g["a"]), "b": nm(g["b"]), "score": g["score"],
                                   "winner": nm(g["winner"]),
                                   "categories": [[c["cat"], c["win"], c["close"]] for c in g["cats"]]}
                                  for g in sm.get("spicy", [])]
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


# ---------- evening edition: tonight's lineup card ----------
TONIGHT_VOICE = VOICE + """
This is the EVENING edition, written after lineups lock and before most games tip. Nothing has
happened yet tonight; you are calling it before it does. Write about what managers are about to
do to themselves: healthy players benched with a game tonight, injured or out players left in
the starting lineup, panicked adds, and the matchups that tonight will decide."""

TONIGHT_SCHEMA = """Return ONLY JSON:
{"headline": "<=6 words, Title Case",
 "zingers": [{"team": "<team name>", "line": "one sentence, <=22 words, about tonight's lineup or matchup"}]  // exactly 4, four different teams
}"""


def tonight_facts(t):
    nm = lambda tk: t["teams"][tk]["name"]
    cat = {c["id"]: c["name"] for c in t["cats"]}
    f = {"date": t["date"], "note": "all facts are about tonight's locked lineups and live matchup scores"}
    f["matchups"] = [{"teams": [nm(m["a"]), nm(m["b"])], "score": m["score"][:2],
                      "swing_cats": [{"cat": cat[r["cat"]], "leader": nm(m["a"] if r["lead"] == "a" else m["b"])}
                                     for r in m["cats"] if r["swing"]]} for m in t["matchups"]]
    f["lineups"] = {nm(tk): v for tk, v in t["lineups"].items()
                    if v["benched_with_game"] or v["starting_out"] or v["empty_slots"]}
    f["moves_today"] = [{"team": nm(x["team"]), x["kind"]: x["player"]} for x in t["moves_today"]][:12]
    return f


def tonight_fallback(t):
    nm = lambda tk: t["teams"][tk]["name"]
    z = []
    for tk, v in t["lineups"].items():
        if v["benched_with_game"]:
            p = v["benched_with_game"][0]
            z.append({"team": nm(tk), "line": f"{p} is healthy, has a game tonight, and is on your bench. Explain that to the group chat."})
        elif v["starting_out"]:
            p = v["starting_out"][0]
            z.append({"team": nm(tk), "line": f"{p['player']} is listed {p['status']} and still in your starting lineup. That is a zero you chose."})
        elif v["empty_slots"]:
            z.append({"team": nm(tk), "line": f"{v['empty_slots']} empty starting slot{'s' if v['empty_slots'] > 1 else ''} tonight. You are forfeiting minutes on purpose."})
        if len(z) >= 4:
            break
    return {"headline": "Tonight's Lineup Card", "zingers": z[:4]}


def tonight(t):
    out = _call(TONIGHT_VOICE, "FACTS:\n" + json.dumps(tonight_facts(t)) + "\n\n" + TONIGHT_SCHEMA, 700)
    return out or tonight_fallback(t)


# ---------- Tonight (the hourly evening page) ----------
TODAY_VOICE = VOICE + """
This is TONIGHT, an in-progress update written while games are still being played (the facts say
how many games are final, in progress and yet to tip, and the time). Write like a beat writer filing
from the arena at the hour: what is swinging right now, who is carrying or burying his team tonight,
category leads that just flipped, and benched players putting up numbers. Use "so far" and "right now"
framing; never declare a matchup decided unless the facts say every game is final. Only numbers from
the facts."""

TODAY_SCHEMA = """Return ONLY JSON:
{"headline": "<=7 words, Title Case, about tonight so far",
 "notes": ["one sentence, <=24 words", ...]  // exactly 5; name the player and the team; at least one about a category lead that flipped if any did, one about the best line so far, one about a manager's bench
}"""


def today_facts(t):
    nm = lambda tk: t["teams"][tk]["name"]
    cat = {c["id"]: c["name"] for c in t["cats"]}
    g = t.get("games") or []
    f = {"as_of": t["as_of"], "update": f"{t['update']} of {t['updates']} tonight",
         "games": {"final": sum(1 for x in g if x["state"] == "post"), "in_progress": sum(1 for x in g if x["state"] == "in"),
                   "not_started": sum(1 for x in g if x["state"] == "pre")} if g else "unknown"}
    f["matchups"] = [{"teams": [nm(m["a"]), nm(m["b"])], "score_now": m["score"][:2],
                      "score_last_update": m.get("prev_score", [None, None])[:2],
                      "swing_cats": [cat[r["cat"]] for r in m["cats"] if r["swing"]]} for m in t["matchups"]]
    f["lead_flips_since_last_update"] = [{"cat": cat[x["cat"]], "now_leads": nm(x["a"] if x["now"] == "a" else x["b"]),
                                          "took_it_from": nm(x["a"] if x["was"] == "a" else x["b"])} for x in t["flips"] if x["now"] != "tie"][:8]
    f["best_lines_so_far"] = [{"player": r["name"], "team": nm(r["team"]), "line": r["desc"], "benched": r["benched"]} for r in t["rows"][:8]]
    f["worst_lines_so_far"] = [{"player": r["name"], "team": nm(r["team"]), "line": r["desc"]} for r in t["rows"][-4:] if r["value"] < -2]
    f["benched_and_producing"] = [{"player": r["name"], "team": nm(r["team"]), "line": r["desc"]} for r in t["rows"] if r["benched"] and r["value"] > 2][:4]
    f["team_nights"] = [{"team": nm(x["team"]), "night_rank": x["rank"], "players_with_a_stat": x["played"]} for x in t["team_nights"]]
    return f


def today_fallback(t):
    nm = lambda tk: t["teams"][tk]["name"]
    cat = {c["id"]: c["name"] for c in t["cats"]}
    notes = []
    if t["rows"]:
        r = t["rows"][0]
        notes.append(f"{r['name']} has the line of the night so far for {nm(r['team'])}: {r['desc']}.")
    for x in t["flips"][:2]:
        if x["now"] != "tie":
            notes.append(f"{nm(x['a'] if x['now'] == 'a' else x['b'])} just took {cat[x['cat']]} from {nm(x['a'] if x['was'] == 'a' else x['b'])}.")
    for r in t["rows"]:
        if r["benched"] and r["value"] > 2:
            notes.append(f"{r['name']} is putting up {r['desc']} on {nm(r['team'])}'s bench. Someone check on that manager.")
            break
    close = [m for m in t["matchups"] if abs(m["score"][0] - m["score"][1]) <= 1]
    if close:
        m = close[0]
        notes.append(f"{nm(m['a'])} and {nm(m['b'])} are {m['score'][0]}-{m['score'][1]} with {m['swings']} categor{'y' if m['swings'] == 1 else 'ies'} inside a possession.")
    if t["rows"]:
        r = t["rows"][-1]
        if r["value"] < -2:
            notes.append(f"{r['name']} is actively hurting {nm(r['team'])}: {r['desc']}.")
    head = "Tonight, So Far" if not t.get("final") else "Tonight, Final"
    return {"headline": head, "notes": notes[:5]}


def today(t):
    out = _call(TODAY_VOICE, "FACTS:\n" + json.dumps(today_facts(t)) + "\n\n" + TODAY_SCHEMA, 900)
    if out and out.get("notes"):
        return {"headline": out.get("headline") or "Tonight, So Far", "notes": [str(n) for n in out["notes"]][:5]}
    return today_fallback(t)


if __name__ == "__main__":
    # Probe: run the real Squawk Box prompt against the committed paper and show what Claude sends back.
    #   GG_DEBUG=1 ANTHROPIC_API_KEY=... python -m pipeline.recap
    import pathlib
    os.environ.setdefault("GG_DEBUG", "1")
    data = pathlib.Path(__file__).resolve().parent.parent / "docs" / "data"
    a = json.loads((data / "league.json").read_text())
    night = json.loads((data / "night.json").read_text()) if (data / "night.json").exists() else None
    out = write(a, night)
    print("\nRESULT headline:", out.get("headline"))
    print("zingers:", len(out.get("zingers", [])), "night notes:", len(out.get("night", []) or []))
