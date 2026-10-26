// Shared helpers for every Gazette page.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtStat = (v, pct) => v == null ? "–" : pct ? Number(v).toFixed(3).replace(/^0/, "") : Math.round(v).toLocaleString();
const fmtPct = v => v == null ? "–" : Number(v).toFixed(3).replace(/^0/, "");
const fmtLuck = v => v == null ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(1);
const ordinal = n => n + (["th","st","nd","rd"][(n % 100 > 10 && n % 100 < 14) ? 0 : (n % 10 < 4 ? n % 10 : 0)]);
const getJSON = url => fetch(url, {cache: "no-store"}).then(r => { if (!r.ok) throw new Error(url + " " + r.status); return r.json(); });

// Site-wide nav. Primary row: Last Night, This Week, Weekly Wrap (plus a yellow Picks pill while a poll is open).
// Everything else (Picks after lock, Trade Block, past seasons, League history) sits behind "More" on phones
// and shows as plain links on the right on desktop. One accent color: the active page is red, the rest black.
function siteNav(active) {
  const el = $("sitenav");
  if (!el) return;
  const cur = k => k === active ? ' aria-current="page"' : "";
  const link = (href, label, key, cls) => `<a href="${href}"${cls ? ` class="${cls}"` : ""}${cur(key)}>${label}</a>`;
  let seasons = [], picks = null, who = null, tonight = null;
  const draw = () => {
    const paper = siteNav.locked
      ? `<a href="index.html#current" class="locked"${cur("today")} aria-label="Current Season, locked until tip-off">${LOCK_SVG}<span>Current Season</span><em>Unlocks tonight</em><i class="chain c1" aria-hidden="true"></i><i class="chain c2" aria-hidden="true"></i></a>`
      : link("index.html#current", "This Week", "today");
    // while tonight's page is live, "Tonight" takes Last Night's slot and Last Night moves under More
    const tn = tonight && !siteNav.locked ? `<a href="today.html" class="tonight"${cur("today")}>Tonight${tonight.final ? "" : "<i aria-hidden=\"true\"></i>"}</a>` : "";
    const ln = siteNav.locked ? "" : link("night.html", "Last Night", "night");
    const main = (tn || ln) + paper + (siteNav.locked ? "" : link("wrap.html", "Weekly Wrap", "wrap"));
    const pkOpen = picks && picks.open ? `<a href="predict.html" class="picks open"${cur("picks")}>File Your Picks<em>Locks ${esc(picks.day)}</em></a>` : "";
    const more = [
      tn ? ln : "",
      link("block.html", "Trade Block", "block"),
      link("top.html", "The Fifty", "top"),
      picks && !picks.open ? link("predict.html", "Picks", "picks") : "",
      link("history.html", "League history", "history"),
      ...seasons.map(s => link(`season.html#${s.season}`, esc(s.label), "s" + s.season, "sub")),
      link("about.html", "About", "about")
    ].join("");
    const open = el.classList.contains("open");
    el.innerHTML = `<div class="nav1">${main}${pkOpen}${who ? `<span class="who"><i>You</i>${esc(who)}</span>` : ""}<button type="button" class="more" aria-expanded="${open}" aria-controls="nav2">More</button></div><div class="nav2" id="nav2">${more}</div>`;
    const btn = el.querySelector(".more"), setOpen = v => { el.classList.toggle("open", v); btn.setAttribute("aria-expanded", v); };
    btn.addEventListener("click", e => { e.stopPropagation(); setOpen(!el.classList.contains("open")); });
    if (!siteNav.closer) { siteNav.closer = true; document.addEventListener("click", e => { if (!el.contains(e.target)) setOpen(false); }); document.addEventListener("keydown", e => { if (e.key === "Escape") setOpen(false); }); }
  };
  draw();
  getJSON("data/history.json").then(h => { seasons = h.seasons || []; draw(); if (h.sample) siteNav.sample = true; }).catch(() => {});
  getJSON("data/polls.json").then(c => {
    const now = Date.now(), ps = c.polls || [];
    const open = ps.find(p => new Date(p.opens) <= now && now < new Date(p.locks));
    if (open) picks = {open: true, day: new Date(open.locks).toLocaleDateString("en-US", {weekday: "short", timeZone: "America/New_York"})};
    else if (ps.some(p => new Date(p.locks) <= now)) picks = {open: false};
    try { const R = c.rosters && c.rosters.teams[localStorage.getItem("gg_who")]; if (R && R.team) who = R.team; } catch (e) {}
    draw();
  }).catch(() => {});
  seasonLocked().then(l => { if (l !== siteNav.locked) { siteNav.locked = l; draw(); } });
  tonightLive().then(t => { if (t) { tonight = t; draw(); } });
}
// today.json counts as tonight while its game date is the current NBA night (anything before 5am ET is still "tonight").
function tonightLive() {
  if (!tonightLive.p) tonightLive.p = getJSON("data/today.json").then(t => {
    if (!t || t.sample) return null;
    const now = new Date(Date.now() - 5 * 3600 * 1000).toLocaleDateString("en-CA", {timeZone: "America/New_York"});
    return t.date === now ? t : null;
  }).catch(() => null);
  return tonightLive.p;
}
// The reader's own Yahoo team id (from the name they signed on Picks or the Trade Block), or null.
function myTeamId(c) {
  try { const R = c.rosters && c.rosters.teams[localStorage.getItem("gg_who")]; return R ? R.team_id : null; } catch (e) { return null; }
}

// The current season stays in chains until the first real (non-sample) build lands.
const LOCK_SVG = `<svg class="lock-ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10V8a5 5 0 0 1 10 0v2h1.5A1.5 1.5 0 0 1 20 11.5v9A1.5 1.5 0 0 1 18.5 22h-13A1.5 1.5 0 0 1 4 20.5v-9A1.5 1.5 0 0 1 5.5 10zm2 0h6V8a3 3 0 0 0-6 0zm3 4a1.6 1.6 0 0 0-.8 3V19h1.6v-2a1.6 1.6 0 0 0-.8-3z"/></svg>`;
// goongazette.com/?preview opens the current-season pages on sample data for this tab only
const PREVIEW = (() => {
  try {
    if (new URLSearchParams(location.search).has("preview")) sessionStorage.setItem("gg_preview", "1");
    return sessionStorage.getItem("gg_preview") === "1";
  } catch (e) { return new URLSearchParams(location.search).has("preview"); }
})();
function seasonLocked() {
  if (PREVIEW) return Promise.resolve(false);
  if (!seasonLocked.p) seasonLocked.p = getJSON("data/edition.json").then(e => !!e.sample).catch(() => false);
  return seasonLocked.p;
}

// Story paragraphs as bullets, with team and manager names bolded.
function storyList(paras, names) {
  const keys = [...new Set((names || []).filter(Boolean))].sort((a, b) => b.length - a.length);
  const bold = txt => {
    let h = esc(txt);
    keys.forEach(k => { const e = esc(k).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); h = h.replace(new RegExp(e, "g"), `<b>${esc(k)}</b>`); });
    return h;
  };
  return (paras || []).length ? `<ul class="story-list">${paras.map(t => `<li>${bold(t)}</li>`).join("")}</ul>` : "";
}

// ALL-CAPS headlines from older recaps -> Title Case (small words lowered, first word kept).
function titleCase(t) {
  t = String(t || "");
  if (t !== t.toUpperCase()) return t;
  const small = new Set(["of","the","and","a","an","in","on","for","to","vs","at","by","or"]);
  return t.toLowerCase().split(/(\s+)/).map((w, i) => /\s/.test(w) ? w : (i > 0 && small.has(w) ? w : w.charAt(0).toUpperCase() + w.slice(1))).join("");
}

// Readership tracking (GoatCounter: no cookies, no banner). Pageviews plus a few events:
//   tab/<id>         a home tab was opened
//   view/lineup-card the evening Lineup Card was on screen
//   reader/<name>    a tagged manager showed up (once per device per ET day)
// Personal links look like goongazette.com/?r=name. The tag is kept on that device,
// so later visits from it count too, and the ?r= is removed from the address bar
// so a forwarded link doesn't tag someone else.
const GC_CODE = "goongazette";
const gc = (() => {
  const q = [];
  const local = /^(localhost|127\.|0\.0\.0\.0|\[::1\])/.test(location.hostname) || location.protocol === "file:";
  const store = { get: k => { try { return localStorage.getItem(k); } catch (e) { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} } };
  const etDay = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const flush = () => { while (q.length && window.goatcounter && window.goatcounter.count) window.goatcounter.count(q.shift()); };
  const send = o => { q.push(o); flush(); };
  const event = (path, title) => send({ path, title: title || path, event: true });

  // pick up and strip a personal tag
  const params = new URLSearchParams(location.search);
  const tag = (params.get("r") || "").toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 24);
  if (tag) {
    store.set("gg_reader", tag);
    params.delete("r");
    const qs = params.toString();
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash);
  }
  // No link tag yet: fall back to the name this device signed with on Picks or the Trade Block
  // (gg_who). A link tag, once set, always wins, so a shared device keeps its first owner.
  let reader = store.get("gg_reader");
  if (!reader) {
    const who = (store.get("gg_who") || "").toLowerCase();
    if (/^[a-z0-9_-]{1,24}$/.test(who)) { store.set("gg_reader", who); reader = who; }
  }

  if (!local) {
    window.goatcounter = { no_onload: true };
    const s = document.createElement("script");
    s.async = true;
    s.src = "https://gc.zgo.at/count.js";
    s.dataset.goatcounter = `https://${GC_CODE}.goatcounter.com/count`;
    s.onload = flush;
    document.head.appendChild(s);
  }

  send({ path: location.pathname.replace(/index\.html$/, "") || "/" });
  if (reader) {
    const k = "gg_reader_day";
    if (store.get(k) !== etDay()) { store.set(k, etDay()); event("reader/" + reader, reader); }
  }
  const seen = new Set();
  return {
    event,
    once: (path, title) => { if (!seen.has(path)) { seen.add(path); event(path, title); } },
  };
})();

// The goon posse: small original cartoon goons (western outfits), used on the site masthead and the Weekly Wrap nameplate.
// Colors come from CSS vars --wx-accent, --wx-poster, --wx-face set by whoever hosts them.
const GOON = {
  hats: {
    cowboy: `<ellipse cx="30" cy="17" rx="20" ry="4"/><path d="M19 17c0-9 4-13 11-13s11 4 11 13c-3 1-7 2-11 2s-8-1-11-2z"/><path d="M20 15c3 1 7 2 10 2s7-1 10-2" stroke="var(--wx-accent)" stroke-width="2" fill="none"/>`,
    tall: `<ellipse cx="30" cy="18" rx="17" ry="3.5"/><path d="M21 18V3c0-2 18-2 18 0v15z"/><path d="M21 14h18" stroke="var(--wx-accent)" stroke-width="2.5"/>`,
    bowler: `<ellipse cx="30" cy="18" rx="14" ry="3"/><path d="M19 18c0-8 5-12 11-12s11 4 11 12z"/>`,
    sombrero: `<ellipse cx="30" cy="18" rx="25" ry="4.5"/><path d="M23 18c1-9 4-14 7-14s6 5 7 14z"/><path d="M24 14h12" stroke="var(--wx-accent)" stroke-width="2"/>`,
    band: `<path d="M18 22c0-3 24-3 24 0v3c-8-2-16-2-24 0z" fill="var(--wx-accent)"/><path d="M42 23l6-3-2 6z" fill="var(--wx-accent)"/>`
  },
  face: {
    bandana: `<path d="M19 30c5 3 17 3 22 0l-3 9c-3 3-6 4-8 4s-5-1-8-4z" fill="var(--wx-accent)"/><g fill="var(--wx-poster)" opacity=".6"><circle cx="26" cy="35" r="1"/><circle cx="34" cy="35" r="1"/><circle cx="30" cy="38" r="1"/></g>`,
    shades: `<rect x="20" y="25" width="9" height="5" rx="2"/><rect x="31" y="25" width="9" height="5" rx="2"/><path d="M29 27h2"/><path d="M26 36c2 1 6 1 8 0" stroke="currentColor" stroke-width="1.6" fill="none"/>`,
    stache: `<path d="M22 31c3-3 6-2 8 0 2-2 5-3 8 0-3 3-5 2-8 1-3 1-5 2-8-1z"/>`,
    scowl: `<path d="M26 37c2-2 6-2 8 0" stroke="currentColor" stroke-width="1.8" fill="none"/>`
  },
  prop: {
    ball: `<circle cx="51" cy="54" r="7" fill="var(--wx-accent)"/><path d="M44 54h14M51 47v14M46 49c3 3 3 7 0 10M56 49c-3 3-3 7 0 10" stroke="currentColor" stroke-width="1" fill="none"/>`,
    loot: `<path d="M45 50c-3 4-4 13 6 13s9-9 6-13c-1-2-3-3-3-3l2-3h-10l2 3s-2 1-3 3z"/><text x="51" y="60" text-anchor="middle" font-size="8" font-weight="800" fill="var(--wx-poster)" font-family="Inter,sans-serif">$</text>`,
    lasso: `<ellipse cx="52" cy="52" rx="7" ry="5" fill="none" stroke="var(--wx-accent)" stroke-width="2"/><path d="M46 54c-2 3-3 6-3 9" stroke="var(--wx-accent)" stroke-width="2" fill="none"/>`,
    cards: `<rect x="45" y="47" width="9" height="12" rx="1.5" fill="var(--wx-poster)" stroke="currentColor" stroke-width="1.2" transform="rotate(-12 49 53)"/><rect x="49" y="46" width="9" height="12" rx="1.5" fill="var(--wx-poster)" stroke="currentColor" stroke-width="1.2" transform="rotate(10 53 52)"/><text x="53.5" y="55" text-anchor="middle" font-size="7" fill="var(--wx-accent)" font-family="Inter,sans-serif">A</text>`,
    none: ``
  }
};
function goon(i) {
  const H = ["cowboy", "tall", "bowler", "sombrero", "band"], F = ["bandana", "shades", "stache", "scowl"], P = ["ball", "loot", "lasso", "cards", "none"];
  const h = H[i % 5], f = F[(i + Math.floor(i / 4)) % 4], pr = P[(i * 3 + 1) % 5];
  const wide = i % 3 === 1 ? 4 : 0, flip = i % 2 ? ' transform="translate(60 0) scale(-1 1)"' : "";
  return `<svg viewBox="0 0 60 80" class="g${i % 4}"><g${flip} fill="currentColor" stroke="currentColor" stroke-width="0">
    <path d="M22 66l-2 11h8l1-11zM33 66l1 11h8l-2-11z"/>
    <path d="M${16 - wide} 70c0-16 5-26 14-26s${14 + wide} 10 ${14 + wide} 26z"/>
    <path d="M44 52c4 0 6 2 6 4" stroke="currentColor" stroke-width="5" stroke-linecap="round" fill="none"/>
    <path d="M17 52c-3 3-4 6-3 10" stroke="currentColor" stroke-width="5" stroke-linecap="round" fill="none"/>
    <circle cx="30" cy="30" r="13" fill="var(--wx-face)" stroke="currentColor" stroke-width="2.4"/>
    <g fill="currentColor"><circle cx="25.5" cy="28" r="1.8"/><circle cx="34.5" cy="28" r="1.8"/><path d="M22 23.5l6 2.2M38 23.5l-6 2.2" stroke="currentColor" stroke-width="2.2"/></g>
    ${GOON.face[f]}${GOON.hats[h]}${GOON.prop[pr]}</g></svg>`;
}
// Masthead posse on every page: a row of goons under "The Goon Gazette"
(function mastGoons() {
  const np = document.querySelector("header.mast .nameplate");
  if (!np || np.querySelector(".mast-goons") || document.getElementById("wx-goons")) return;  // the Wrap has its own posse
  const row = document.createElement("div");
  row.className = "mast-goons";
  row.setAttribute("aria-hidden", "true");
  row.innerHTML = Array.from({length: 12}, (_, i) => goon(i)).join("");
  np.appendChild(row);
})();
