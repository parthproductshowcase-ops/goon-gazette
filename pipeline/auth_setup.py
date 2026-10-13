"""One-time Yahoo login. Run on your laptop:

    YAHOO_CLIENT_ID=... YAHOO_CLIENT_SECRET=... python -m pipeline.auth_setup

It opens Yahoo's consent page, you paste back the code, and it prints the
refresh token to store as the GitHub secret YAHOO_REFRESH_TOKEN.
"""
import os
import webbrowser
import requests
from urllib.parse import urlencode

from .yahoo import AUTH_URL, TOKEN_URL


def main():
    cid = os.environ["YAHOO_CLIENT_ID"]
    secret = os.environ["YAHOO_CLIENT_SECRET"]
    redirect = os.environ.get("YAHOO_REDIRECT_URI") or "oob"
    url = f"{AUTH_URL}?{urlencode({'client_id': cid, 'redirect_uri': redirect, 'response_type': 'code'})}"
    print("\nOpen this URL and approve access.\n\n" + url + "\n")
    if redirect != "oob":
        print("Your browser will land on a page that fails to load. That's expected:")
        print("copy the value after 'code=' in the address bar (stop at any '&').\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    code = input("Paste code: ").strip()
    if "code=" in code:  # pasted the whole URL
        code = code.split("code=")[1].split("&")[0]
    r = requests.post(TOKEN_URL, auth=(cid, secret),
                      data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect},
                      timeout=30)
    r.raise_for_status()
    tok = r.json()
    print("\nYAHOO_REFRESH_TOKEN =\n" + tok["refresh_token"])
    print("\nAdd it in GitHub: repo Settings > Secrets and variables > Actions.")


if __name__ == "__main__":
    main()
