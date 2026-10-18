"""Thin Yahoo Fantasy API client + parsers that normalize Yahoo's nested JSON
into the plain internal format the stats layer uses (see build.py docstring)."""
import os
import time
import requests

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
API = "https://fantasysports.yahooapis.com/fantasy/v2"


# ---------- Yahoo JSON helpers ----------
def flat(x):
    """Yahoo returns objects as lists of one-key dicts (sometimes nested). Merge them."""
    if isinstance(x, dict):
        return x
    out = {}
    for el in x or []:
        if isinstance(el, list):
            out.update(flat(el))
        elif isinstance(el, dict):
            out.update(el)
    return out


def coll(d, key):
    """Yahoo collections look like {'0': {key: ...}, '1': {...}, 'count': n}."""
    if not d:
        return []
    if isinstance(d, list):
        d = flat(d)
    idx = sorted((k for k in d if str(k).isdigit()), key=int)
    return [d[k][key] for k in idx if isinstance(d[k], dict) and key in d[k]]


def parse_val(v):
    if v is None:
        return None
    v = str(v).strip()
    if v in ("", "-", "-/-"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def stat_map(stats_list, cat_ids):
    out = {}
    for s in stats_list or []:
        s = s.get("stat", s)
        sid = str(s.get("stat_id"))
        if sid in cat_ids:
            out[sid] = parse_val(s.get("value"))
    return out


# ---------- client ----------
TOKEN_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".yahoo_token.json")


def load_dotenv(path=None):
    """Load KEY=VALUE lines from .env (repo root) into os.environ without overriding
    values already set. Safe to call when the file is missing."""
    path = path or os.path.join(os.path.dirname(TOKEN_CACHE), ".env")
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


class Yahoo:
    """Logs in once per run. The access token (valid ~1 hour) and any rotated refresh token
    are cached in .yahoo_token.json, so the history and build steps share one login instead
    of each spending the refresh token."""

    def __init__(self, client_id, client_secret, refresh_token):
        self.cid, self.secret, self.refresh_token = client_id, client_secret, refresh_token
        self.seed = refresh_token  # the token we were given; the cache is only trusted if it grew from this one
        self.access = None
        try:
            import json
            with open(TOKEN_CACHE) as f:
                c = json.load(f)
            # Ignore a cache left over from a different app or a different login.
            if c.get("client_id") == self.cid and c.get("seed") == self.seed:
                if c.get("refresh_token"):
                    self.refresh_token = c["refresh_token"]
                if c.get("access_token") and c.get("expires_at", 0) > time.time() + 120:
                    self.access = c["access_token"]
        except (OSError, ValueError):
            pass

    def _refresh(self, _retry=True):
        import json
        r = requests.post(
            TOKEN_URL,
            auth=(self.cid, self.secret),
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token,
                  "redirect_uri": os.environ.get("YAHOO_REDIRECT_URI") or "oob"},
            timeout=30,
        )
        if r.status_code != 200:
            # A stale cached token: fall back to the one we were given, once.
            if _retry and self.refresh_token != self.seed:
                self.refresh_token = self.seed
                return self._refresh(_retry=False)
            try:
                err = r.json()
                reason = f"{err.get('error')}: {err.get('error_description')}"
            except ValueError:
                reason = r.text[:200]
            raise RuntimeError(f"Yahoo login failed ({r.status_code}) {reason}")
        j = r.json()
        self.access = j["access_token"]
        self.refresh_token = j.get("refresh_token", self.refresh_token)
        try:
            with open(TOKEN_CACHE, "w") as f:
                json.dump({"client_id": self.cid, "seed": self.seed,
                           "access_token": self.access, "refresh_token": self.refresh_token,
                           "expires_at": time.time() + int(j.get("expires_in", 3600))}, f)
        except OSError:
            pass

    def get(self, path):
        if not self.access:
            self._refresh()
        r = None
        for attempt in range(4):
            r = requests.get(f"{API}/{path}", params={"format": "json"},
                             headers={"Authorization": f"Bearer {self.access}"}, timeout=30)
            if r.status_code == 401:
                self._refresh()
                continue
            if r.status_code in (429, 500, 502, 503, 999):
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()["fantasy_content"]
        r.raise_for_status()

    # --- endpoints ---
    def league_key(self, league_id):
        g = flat(self.get("game/nba")["game"])
        return f"{g['game_key']}.l.{league_id}"

    def meta(self, lk):
        m = flat(self.get(f"league/{lk}")["league"])
        return {
            "name": m.get("name"), "season": m.get("season"),
            "current_week": int(m.get("current_week") or 1),
            "start_week": int(m.get("start_week") or 1),
            "end_week": int(m.get("end_week") or 1),
            "num_teams": int(m.get("num_teams") or 0),
            "draft_status": m.get("draft_status"),
            "url": m.get("url"),
            "is_finished": str(m.get("is_finished", "0")) == "1",
            # previous season's league, as "<game_key>_<league_id>"
            "renew": m.get("renew") or None,
        }

    def playoff_settings(self, lk):
        s = flat(flat(self.get(f"league/{lk}/settings")["league"])["settings"])
        return {"num_playoff_teams": int(s.get("num_playoff_teams") or 0),
                "playoff_start_week": int(s.get("playoff_start_week") or 99)}

    def draft_first_round(self, lk):
        lg = flat(self.get(f"league/{lk}/draftresults")["league"])
        picks = [p for p in coll(lg.get("draft_results"), "draft_result")
                 if str(p.get("round")) == "1" and p.get("player_key")]
        names = {}
        if picks:
            keys = ",".join(p["player_key"] for p in picks)
            pl = flat(self.get(f"league/{lk}/players;player_keys={keys}")["league"])
            for p in coll(pl.get("players"), "player"):
                p = flat(p)
                nm = p.get("name", {})
                names[p["player_key"]] = nm.get("full") if isinstance(nm, dict) else str(nm)
        return [{"pick": int(p["pick"]), "team": p["team_key"],
                 "player": names.get(p["player_key"], p["player_key"])} for p in picks]

    def draft_results(self, lk):
        """Every pick: pick, round, cost (auction) or None, team, player_key."""
        lg = flat(self.get(f"league/{lk}/draftresults")["league"])
        out = []
        for p in coll(lg.get("draft_results"), "draft_result"):
            if not p.get("player_key"):
                continue
            cost = p.get("cost")
            out.append({"pick": int(p.get("pick") or 0), "round": int(p.get("round") or 0),
                        "cost": int(cost) if cost not in (None, "") else None,
                        "team": p["team_key"], "player_key": p["player_key"]})
        return out

    def top_players(self, lk, n=12):
        """Top n players by Yahoo's season rank for this league, with the team that holds them."""
        lg = flat(self.get(f"league/{lk}/players;sort=AR;sort_type=season;count={n};out=ownership")["league"])
        out = []
        for i, p in enumerate(coll(lg.get("players"), "player"), 1):
            p = flat(p)
            nm = p.get("name", {})
            own = flat(p.get("ownership") or {})
            out.append({"rank": i, "player_key": p.get("player_key"),
                        "player": nm.get("full") if isinstance(nm, dict) else str(nm),
                        "pos": p.get("display_position"),
                        "team": own.get("owner_team_key") if own.get("ownership_type") == "team" else None})
        return out

    def cats(self, lk):
        lg = flat(self.get(f"league/{lk}/settings")["league"])
        s = flat(lg["settings"])
        out = []
        for st in s["stat_categories"]["stats"]:
            st = st["stat"]
            if str(st.get("is_only_display_stat", "0")) == "1":
                continue  # e.g. FGM/A shown next to FG%, not scored
            name = st.get("display_name") or st.get("abbr") or st.get("name")
            out.append({
                "id": str(st["stat_id"]),
                "name": name,
                "hb": str(st.get("sort_order", "1")) == "1",  # 0 = lower is better (TO)
                "pct": "%" in name,
            })
        return out

    def teams_and_standings(self, lk):
        lg = flat(self.get(f"league/{lk}/standings")["league"])
        teams, standings = {}, {}
        for t in coll(flat(lg["standings"])["teams"], "team"):
            t = flat(t)
            tk = t["team_key"]
            mgr = flat(t.get("managers", [{}])[0].get("manager", {})) if t.get("managers") else {}
            logo = (t.get("team_logos") or [{}])[0].get("team_logo", {}).get("url")
            teams[tk] = {"name": t.get("name"), "manager": mgr.get("nickname", ""), "logo": logo,
                         "guid": mgr.get("guid"),
                         "moves": int(t.get("number_of_moves") or 0),
                         "trades": int(t.get("number_of_trades") or 0)}
            ts = t.get("team_standings", {})
            ot = ts.get("outcome_totals", {})
            standings[tk] = {
                "seed": int(ts.get("playoff_seed") or 0) or None,
                "rank": int(ts.get("rank") or 0),
                "w": int(ot.get("wins") or 0), "l": int(ot.get("losses") or 0),
                "t": int(ot.get("ties") or 0),
            }
        return teams, standings

    def scoreboard(self, lk, week, cat_ids):
        lg = flat(self.get(f"league/{lk}/scoreboard;week={week}")["league"])
        sb = lg["scoreboard"]
        out = []
        for mcoll in coll(sb, "matchups"):
            for m in coll(mcoll, "matchup"):
                tms = [flat(t) for t in coll(m["0"]["teams"], "team")]
                if len(tms) != 2:
                    continue
                out.append({
                    "week": int(m.get("week", week)), "status": m.get("status"),
                    "playoffs": str(m.get("is_playoffs", "0")) == "1",
                    "consolation": str(m.get("is_consolation", "0")) == "1",
                    "winner": m.get("winner_team_key"),
                    "a": tms[0]["team_key"], "b": tms[1]["team_key"],
                    "a_stats": stat_map(tms[0].get("team_stats", {}).get("stats"), cat_ids),
                    "b_stats": stat_map(tms[1].get("team_stats", {}).get("stats"), cat_ids),
                })
        return out

    def team_stats(self, lk, cat_ids, kind="season", date=None):
        q = f"type={kind}" + (f";date={date}" if date else "")
        lg = flat(self.get(f"league/{lk}/teams/stats;{q}")["league"])
        out = {}
        for t in coll(lg["teams"], "team"):
            t = flat(t)
            out[t["team_key"]] = stat_map(t.get("team_stats", {}).get("stats"), cat_ids)
        return out

    def benched(self, tk, date, cat_ids):
        """Players on the bench who put up numbers on `date`."""
        tm = flat(self.get(f"team/{tk}/roster;date={date}/players/stats;type=date;date={date}")["team"])
        roster = tm.get("roster", {})
        out = []
        for pcoll in coll(roster, "players"):
            for p in coll(pcoll, "player"):
                p = flat(p)
                pos = flat(p.get("selected_position", [])).get("position")
                if pos != "BN":
                    continue
                stats = stat_map(p.get("player_stats", {}).get("stats"), cat_ids)
                if any(v for v in stats.values()):
                    nm = p.get("name", {})
                    out.append({"player": nm.get("full") if isinstance(nm, dict) else str(nm),
                                "stats": stats})
        return out


# ---------- player-level data (waiver review, top players, top plays) ----------
def parse_frac(v):
    """'45/98' -> [45, 98] (makes/attempts)."""
    try:
        a, b = str(v).split("/")
        return [float(a), float(b)]
    except ValueError:
        return None


def frac_stats(lk_settings_stats):
    """Display-only stats shaped like makes/attempts (FGM/A, FTM/A), keyed by id."""
    out = {}
    for st in lk_settings_stats:
        st = st["stat"]
        name = st.get("display_name") or st.get("name") or ""
        if str(st.get("is_only_display_stat", "0")) == "1" and "/" in name:
            out[str(st["stat_id"])] = name
    return out


def _yahoo_frac_ids(self, lk):
    s = flat(flat(self.get(f"league/{lk}/settings")["league"])["settings"])
    return frac_stats(s["stat_categories"]["stats"])


def _game_weeks(self, game_key):
    g = self.get(f"game/{game_key}/game_weeks")["game"]
    gw = flat(g).get("game_weeks")
    return {int(w["week"]): {"start": w["start"], "end": w["end"]} for w in coll(gw, "game_week")}


def _roster_day(self, tk, date, cat_ids, frac_ids):
    """Every rostered player's stat line for one day, with lineup slot."""
    tm = flat(self.get(f"team/{tk}/roster;date={date}/players/stats;type=date;date={date}")["team"])
    out = []
    for pcoll in coll(tm.get("roster", {}), "players"):
        for p in coll(pcoll, "player"):
            p = flat(p)
            pos = flat(p.get("selected_position", [])).get("position")
            stats = {}
            for s in p.get("player_stats", {}).get("stats", []):
                s = s.get("stat", s)
                sid = str(s.get("stat_id"))
                if sid in cat_ids:
                    stats[sid] = parse_val(s.get("value"))
                elif sid in frac_ids:
                    stats[sid] = parse_frac(s.get("value"))
            nm = p.get("name", {})
            out.append({"pk": p.get("player_key"), "name": nm.get("full") if isinstance(nm, dict) else str(nm),
                        "pos": pos, "stats": stats})
    return out


def _transactions(self, lk, count=200):
    lg = flat(self.get(f"league/{lk}/transactions;types=add,drop;count={count}")["league"])
    out = []
    for t in coll(lg.get("transactions"), "transaction"):
        meta = t[0] if isinstance(t, list) else t
        rest = flat(t[1:]) if isinstance(t, list) else {}
        if meta.get("status") not in (None, "successful"):
            continue
        for p in coll(rest.get("players"), "player"):
            pm = flat(p[0]) if isinstance(p, list) else flat(p)
            td = p[1].get("transaction_data") if isinstance(p, list) and len(p) > 1 else pm.get("transaction_data")
            td = td[0] if isinstance(td, list) else td or {}
            nm = pm.get("name", {})
            out.append({
                "id": meta.get("transaction_key"), "ts": int(meta.get("timestamp") or 0),
                "kind": td.get("type"),  # add | drop
                "team": td.get("destination_team_key") if td.get("type") == "add" else td.get("source_team_key"),
                "source": td.get("source_type"),  # freeagents | waivers | team
                "faab": int(meta.get("faab_bid")) if meta.get("faab_bid") else None,
                "pk": pm.get("player_key"),
                "player": nm.get("full") if isinstance(nm, dict) else str(nm),
            })
    return out


Yahoo.frac_ids = _yahoo_frac_ids
Yahoo.game_weeks = _game_weeks
Yahoo.roster_day = _roster_day
Yahoo.transactions = _transactions
