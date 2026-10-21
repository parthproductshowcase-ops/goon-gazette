// Goon Gazette web scraper: run inside Claude's browser pane on a basketball.fantasysports.yahoo.com
// page while logged in (league 39751). Used until the Yahoo API opens.
//
// Step 1 (javascript_tool): paste this whole file, then on the last line call
//     ggScrape(["2026-10-21"]);   // the ET dates whose games are final
// It returns right away and keeps working in the page (about 2s per team page).
// Step 2: poll with  window.__gg && window.__gg.done  until true (wait ~20s per call).
// Step 3: JSON.stringify(window.__gg.out)  and save it as a file, then on the repo:
//     python -m pipeline.web_current --merge that_file.json
//
// Output: {days: {date: {team_id: [rows]}}, transactions: [...], moves: {team_id: {moves, trades}},
//          rosters: {team_id: {team, players, il}}}
// A row is only kept for a player who played (has makes/attempts), in day_cols order:
// slot, name, pid, nba, pos, result, fgm, fga, ftm, fta, tpm, tpa, pts, oreb, dreb, ast, st, blk, to
function ggScrape(dates) {
  const L = "/nba/39751";
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const get = async url => new DOMParser().parseFromString(await fetch(url, {credentials: "include"}).then(r => r.text()), "text/html");
  const num = s => (s === "-" || s === "" || s == null ? 0 : Number(String(s).replace(/^\./, "0.")));
  const frac = s => { const m = /^(\d+)\/(\d+)$/.exec(s || ""); return m ? [+m[1], +m[2]] : null; };
  const g = window.__gg = {done: false, err: null, out: {days: {}, transactions: [], moves: {}, rosters: {}}};
  (async () => {
    try {
      for (const date of dates) {
        g.out.days[date] = {};
        for (let id = 1; id <= 12; id++) {
          const d = await get(`${L}/${id}/team?date=${date}&stat1=S&stat2=D`);
          const rows = [];
          d.querySelectorAll("table#statTable0 tbody tr").forEach(tr => {
            const td = [...tr.querySelectorAll("td")].map(x => x.textContent.replace(/\s+/g, " ").trim());
            const a = tr.querySelector("a.name"); if (!a) return;
            const i = td.findIndex((c, k) => k > 1 && /^(\d+\/\d+|-\/-)$/.test(c));
            const fg = frac(td[i]); if (!fg) return;
            const ft = frac(td[i + 2]) || [0, 0];
            const pid = ((a.getAttribute("href") || "").match(/players\/(\d+)/) || [])[1] || "";
            const meta = td[1].match(/([A-Z]{2,4}) - ([A-Z,]+)/) || [];
            const res = td[1].match(/[WL], \d+-\d+ (@|vs) [A-Z]{2,4}/) || [];
            rows.push([td[0], a.textContent.trim(), pid, meta[1] || "", meta[2] || "", res[0] || "",
              fg[0], fg[1], ft[0], ft[1], num(td[i + 4]), num(td[i + 5]), num(td[i + 7]), num(td[i + 8]),
              num(td[i + 9]), num(td[i + 10]), num(td[i + 11]), num(td[i + 12]), num(td[i + 13])]);
          });
          g.out.days[date][id] = rows;
          await sleep(1500);
        }
      }
      // current rosters (today's lineup page), for the trade block
      for (let id = 1; id <= 12; id++) {
        const d = await get(`${L}/${id}`);
        const team = (d.title.match(/ - (.*?) \| Fantasy/) || [])[1] || "";
        const players = [], il = [];
        d.querySelectorAll("table#statTable0 tbody tr").forEach(tr => {
          const a = tr.querySelector("a.name"); if (!a) return;
          const slot = (tr.querySelector("td") || {}).textContent.trim();
          players.push(a.textContent.trim());
          if (/^IL/.test(slot)) il.push(a.textContent.trim());
        });
        g.out.rosters[id] = {team, players, il};
        await sleep(1500);
      }
      // transactions (newest first) and moves per team
      const t = await get(`${L}/transactions`);
      t.querySelectorAll("table tr").forEach(tr => {
        const team = tr.querySelector("a.Tst-team-name");
        const when = tr.querySelector(".F-timestamp");
        if (!team || !when) return;
        const tid = +((team.getAttribute("href") || "").match(/39751\/(\d+)/) || [])[1];
        const icons = [...tr.querySelectorAll("td:first-child span[title]")].map(s => s.title);
        const people = [...tr.querySelectorAll('td div.Pbot-xs')].map(div => ({
          name: (div.querySelector('a[href*="/players/"]:not(.playernote)') || {}).textContent?.trim() || "",
          how: (div.querySelector("h6") || {}).textContent?.trim() || ""}));
        g.out.transactions.push({team: tid, when: when.textContent.trim(), icons, people});
      });
      await sleep(1500);
      const m = await get(`${L}/teams`);
      m.querySelectorAll("table tbody tr").forEach(tr => {
        const a = tr.querySelector('a[href*="/nba/39751/"]');
        const tid = a ? +((a.getAttribute("href") || "").match(/39751\/(\d+)/) || [])[1] : 0;
        const cells = [...tr.querySelectorAll("td")].map(x => x.textContent.trim());
        const nums = cells.filter(c => /^\d+$/.test(c)).map(Number);  // waiver priority, moves, trades
        if (tid && nums.length >= 3) g.out.moves[tid] = {moves: nums[1], trades: nums[2]};
      });
    } catch (e) { g.err = String(e); }
    g.done = true;
  })();
  return "started";
}
