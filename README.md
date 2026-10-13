# The Goon Gazette

Daily tabloid for the Ballinwithmygoons Yahoo fantasy league (12 teams, 11-cat H2H).
A GitHub Action pulls Yahoo every morning, computes the stats, has Claude write the front page,
and commits `docs/data/league.json`. GitHub Pages serves `docs/`.

## What's on the page (current season, the home page)
- **Headline**: AI recap and team zingers (template fallback if no Claude key)
- **This Week**: live matchups with swing cats, plus Top 5 players and Top 5 waiver plays so far
- **Victory Road**: standings with the playoff line, cats back, and playoff / No. 1 seed odds from
  2,000 simulations of the remaining schedule
- **Last Week**: final scores, MVP / dud / upset / closest / robbed, Top 5 players and Top 5 plays
- **Waiver Wire**: every add and drop last week, graded per game; pickup of the week, one man's trash,
  busiest GM, biggest FAAB bid
- **Trends**: power rank by week, heating up / ice cold, weekly all-play sparklines

Top players are ranked by z-score across your categories (started slots only; FG%/FT% weighted by
attempts). A "play" is a waiver pickup whose stats, removed from his team's week, flip categories
or the matchup.

Daily roster stat lines are cached in `cache/days/` (one file per day, 12 calls each), so a week of
history costs nothing after the first pull.

## Past seasons + league history
- `season.html#2025` per season: champion, recap, final standings (with on-paper rank and luck),
  playoff bracket, awards, single-week record book, first-round draft.
- `history.html`: the rafters (every champ), all-time table, finishes grid, rivalries,
  head-to-head lookup, all-time record book, superlatives.
- Yahoo gives each season its own league ID; the backfill follows the "renew" chain back 5
  finished seasons. Each one is fetched once into `archive/<season>.json` (about 25 calls per
  season) and committed, so it never hits Yahoo again. Season recaps are written by Claude once
  and cached in the archive.
- Teams rename every year, so history is keyed by manager. The first run prints every manager
  found. If someone shows up twice (new Yahoo account) or should display differently, edit
  `config/managers.json` and run `python -m pipeline.history --recompute`.
- Your Yahoo account must have been in the league those seasons to read them.

Categories, including "lower is better" ones like TO, come from Yahoo's league settings, so any
custom set works with no code change.

## Setup (about 30 min)
1. **Yahoo app**: developer.yahoo.com/apps/create. Type: confidential client. Redirect URI: `oob`
   (if the form rejects it, use `https://localhost`). API permissions: Fantasy Sports, Read.
   Copy the client ID and secret.
2. **Login once, on your laptop**:
   ```
   pip install requests
   YAHOO_CLIENT_ID=xxx YAHOO_CLIENT_SECRET=yyy python -m pipeline.auth_setup
   ```
   Approve, paste the code, copy the refresh token it prints.
3. **League ID**: the number in your league URL, `basketball.fantasysports.yahoo.com/nba/<ID>`.
4. **GitHub**: new repo, push this folder. Settings > Secrets and variables > Actions, add
   `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, `YAHOO_REFRESH_TOKEN`, `YAHOO_LEAGUE_ID`,
   `ANTHROPIC_API_KEY` (console.anthropic.com).
5. **Pages**: Settings > Pages > Deploy from branch > `main` / `docs`.
6. Actions tab > The Goon Gazette > **Run workflow** to test. The first run also backfills history
   (a minute or two); after that it only checks for newly finished seasons. Then post the link in the chat.

## Local
```
python -m pipeline.build --sample      # fake league, no credentials
python -m pipeline.history --sample    # fake 5-season history
python -m http.server -d docs 8000     # open localhost:8000
```

## Tuning
- Voice: `VOICE` in `pipeline/recap.py`
- Power blend / punt threshold / swing thresholds: `pipeline/stats.py`
- Before the draft the site shows a "tip-off is coming" page automatically.
