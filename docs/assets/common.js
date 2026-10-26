// Shared helpers for every Gazette page.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtStat = (v, pct) => v == null ? "–" : pct ? Number(v).toFixed(3).replace(/^0/, "") : Math.round(v).toLocaleString();
const fmtPct = v => v == null ? "–" : Number(v).toFixed(3).replace(/^0/, "");
const fmtLuck = v => v == null ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(1);
const ordinal = n => n + (["th","st","nd","rd"][(n % 100 > 10 && n % 100 < 14) ? 0 : (n % 10 < 4 ? n % 10 : 0)]);
const getJSON = url => fetch(url, {cache: "no-store"}).then(r => { if (!r.ok) throw new Error(url + " " + r.status); return r.json(); });

// Site-wide nav: Today, each archived season, History.
function siteNav(active) {
  const el = $("sitenav");
  if (!el) return;
  const link = (href, label, key) => `<a href="${href}"${key === active ? ' aria-current="page"' : ""}>${label}</a>`;
  let seasons = [], picks = null;
  const draw = () => {
    const cur = siteNav.locked
      ? `<a href="index.html#current" class="locked"${active === "today" ? ' aria-current="page"' : ""} aria-label="Current Season, locked until tip-off">${LOCK_SVG}<span>Current Season</span><em>Unlocks tonight</em><i class="chain c1" aria-hidden="true"></i><i class="chain c2" aria-hidden="true"></i></a>`
      : link("index.html#current", "Current Season", "today");
    const pk = picks ? `<a href="predict.html" class="picks${picks.open ? " open" : ""}"${active === "picks" ? ' aria-current="page"' : ""}>${picks.open ? "File Your Picks" : "Picks"}${picks.open ? `<em>Locks ${esc(picks.day)}</em>` : ""}</a>` : "";
    const bk = `<a href="block.html" class="block"${active === "block" ? ' aria-current="page"' : ""}>Trade Block</a>`;
    const nt = siteNav.locked ? "" : `<a href="night.html" class="night"${active === "night" ? ' aria-current="page"' : ""}>Last Night</a>`;
    const wr = siteNav.locked ? "" : `<a href="wrap.html" class="wrapn"${active === "wrap" ? ' aria-current="page"' : ""}>Weekly Wrap</a>`;
    el.innerHTML = nt + wr + cur + pk + bk
      + seasons.map(s => link(`season.html#${s.season}`, esc(s.label), "s" + s.season)).join("")
      + link("history.html", "League history", "history");
  };
  draw();
  getJSON("data/history.json").then(h => { seasons = h.seasons || []; draw(); if (h.sample) siteNav.sample = true; }).catch(() => {});
  getJSON("data/polls.json").then(c => {
    const now = Date.now(), ps = c.polls || [];
    const open = ps.find(p => new Date(p.opens) <= now && now < new Date(p.locks));
    if (open) picks = {open: true, day: new Date(open.locks).toLocaleDateString("en-US", {weekday: "short", timeZone: "America/New_York"})};
    else if (ps.some(p => new Date(p.locks) <= now)) picks = {open: false};
    if (picks) draw();
  }).catch(() => {});
  seasonLocked().then(l => { if (l !== siteNav.locked) { siteNav.locked = l; draw(); } });
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
