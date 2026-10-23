// Yahoo rest-of-season projections for every rostered player in league 39751.
// Paste into the browser pane on any basketball.fantasysports.yahoo.com page, then run:
//   await ggProj()        -> window.__proj = {as_of, rows: {name: [gp, fgm, fga, ftm, fta, tpm, tpa, pts, oreb, dreb, ast, st, blk, to]}}
// Totals for the remaining season ("Remaining Games (proj)", stat1=S_PSR). status=T = rostered players only.
// Read the result with JSON.stringify(window.__proj) and save it as archive/2026_proj.json.
async function ggProj() {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const num = s => { s = String(s).replace(/,/g, ""); return s === "-" || s === "" ? 0 : Number(s); };
  const rows = {};
  for (let c = 0; c < 400; c += 25) {
    const u = `/nba/39751/players?status=T&pos=P&cut_type=33&stat1=S_PSR&myteam=0&sort=AR&sdir=1&count=${c}`;
    const h = await (await fetch(u, {credentials: "include"})).text();
    const doc = new DOMParser().parseFromString(h, "text/html");
    const heads = [...doc.querySelectorAll("table thead tr:last-child th")].map(x => x.innerText.trim());
    const ix = k => heads.indexOf(k);
    const need = ["GP*", "FGM/A*", "FTM/A*", "3PTM", "3PTA*", "PTS", "OREB", "DREB", "AST", "ST", "BLK", "TO"];
    if (need.some(k => ix(k) < 0)) throw new Error("unexpected columns: " + heads.join("|"));
    const trs = [...doc.querySelectorAll("table tbody tr")];
    let added = 0;
    for (const r of trs) {
      const cs = [...r.cells].map(x => x.innerText.replace(/\s+/g, " ").trim());
      const a = r.querySelector("a.name, a[class*='name']");
      const nm = a ? a.textContent.trim() : null;
      if (!nm || rows[nm]) continue;
      const fg = cs[ix("FGM/A*")].split("/"), ft = cs[ix("FTM/A*")].split("/");
      rows[nm] = [num(cs[ix("GP*")]), num(fg[0]), num(fg[1]), num(ft[0]), num(ft[1]), num(cs[ix("3PTM")]), num(cs[ix("3PTA*")]),
                  num(cs[ix("PTS")]), num(cs[ix("OREB")]), num(cs[ix("DREB")]), num(cs[ix("AST")]), num(cs[ix("ST")]), num(cs[ix("BLK")]), num(cs[ix("TO")])];
      added++;
    }
    if (!added) break;
    await sleep(1500);
  }
  window.__proj = {as_of: new Date().toLocaleDateString("en-CA", {timeZone: "America/New_York"}), source: "Yahoo Remaining Games (proj)",
                   cols: ["gp", "fgm", "fga", "ftm", "fta", "tpm", "tpa", "pts", "oreb", "dreb", "ast", "st", "blk", "to"], rows};
  return Object.keys(rows).length;
}
