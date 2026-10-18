"""Yahoo login that reads .env, saves the refresh token back to .env and
.yahoo_token.json, and tests the league API. Prints status only, never secrets.

    python -m pipeline.yahoo_login
"""
import json
import os
import webbrowser
from urllib.parse import urlencode

import requests

from .yahoo import AUTH_URL, TOKEN_URL

ENV = ".env"
REDIRECT = os.environ.get("YAHOO_REDIRECT_URI") or "https://localhost"


def load_env():
    vals = {}
    with open(ENV) as f:
        for line in f:
            k, _, v = line.rstrip("\n").partition("=")
            if k:
                vals[k] = v
    return vals


def save_env(vals):
    with open(ENV, "w") as f:
        for k, v in vals.items():
            f.write(f"{k}={v}\n")


def main():
    env = load_env()
    cid, secret = env["YAHOO_CLIENT_ID"], env["YAHOO_CLIENT_SECRET"]
    url = f"{AUTH_URL}?{urlencode({'client_id': cid, 'redirect_uri': REDIRECT, 'response_type': 'code'})}"
    print("\nOpening Yahoo. Approve access, then copy the code (or the full URL from the")
    print("address bar of the localhost page that fails to load.\n")
    print(url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    raw = input("Paste the code or localhost URL here, then Enter: ").strip()
    code = raw.split("code=")[1].split("&")[0] if "code=" in raw else raw
    r = requests.post(TOKEN_URL, auth=(cid, secret),
                      data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT},
                      timeout=30)
    print("token exchange:", r.status_code)
    if not r.ok:
        print(r.text[:300])
        return
    tok = r.json()
    env["YAHOO_REFRESH_TOKEN"] = tok["refresh_token"]
    save_env(env)
    with open(".yahoo_token.json", "w") as f:
        json.dump(tok, f)
    print("saved refresh token to .env and .yahoo_token.json")
    league = env.get("YAHOO_LEAGUE_ID", "39751")
    t = requests.get(f"https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games;game_codes=nba/leagues?format=json",
                     headers={"Authorization": "Bearer " + tok["access_token"]}, timeout=30)
    print("fantasy API:", t.status_code)
    if t.ok:
        try:
            games = t.json()["fantasy_content"]["users"]["0"]["user"][1]["games"]
            for i in range(games["count"]):
                g = games[str(i)]["game"]
                season = g[0]["season"]
                for j in range(g[1]["leagues"]["count"]):
                    lg = g[1]["leagues"][str(j)]["league"][0]
                    print(f"  {season}: {lg['name']} (league_id {lg['league_id']}, {lg['num_teams']} teams)")
        except Exception as e:
            print("  (could not parse league list:", e, ")")
    else:
        print(t.text[:300])


if __name__ == "__main__":
    main()
