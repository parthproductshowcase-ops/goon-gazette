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
  const draw = seasons => {
    el.innerHTML = link("index.html", "Today", "today")
      + seasons.map(s => link(`season.html#${s.season}`, esc(s.label), "s" + s.season)).join("")
      + link("history.html", "League history", "history");
  };
  draw([]);
  getJSON("data/history.json").then(h => { draw(h.seasons || []); if (h.sample) siteNav.sample = true; }).catch(() => {});
}
