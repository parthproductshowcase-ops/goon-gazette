/**
 * The Goon Gazette: picks backend (Google Apps Script, bound to the "Goon Gazette Picks" sheet).
 *
 * Setup (once): in the sheet, Extensions > Apps Script, replace everything with this file, Save.
 * Deploy > New deployment > type "Web app": Execute as "Me", Who has access "Anyone". Deploy,
 * authorize, copy the Web app URL. That URL goes in docs/data/polls.json as "endpoint".
 *
 * POST  {poll, who, reader, answers}  -> appends a row. Rejected after the poll locks.
 * GET   ?poll=<id>                    -> {filed:[who], count, locked, picks: latest per manager once locked}
 *
 * Lock times and the manager list come from goongazette.com/data/polls.json, so a new flash
 * poll needs no change here. Picks stay hidden until the lock so nobody copies homework.
 */
const CONFIG_URL = "https://goongazette.com/data/polls.json";
const SHEET = "responses";

function config_() {
  const cache = CacheService.getScriptCache();
  const hit = cache.get("cfg");
  if (hit) return JSON.parse(hit);
  const txt = UrlFetchApp.fetch(CONFIG_URL + "?t=" + Date.now()).getContentText();
  cache.put("cfg", txt, 300);
  return JSON.parse(txt);
}

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET);
  if (!sh) {
    sh = ss.insertSheet(SHEET);
    sh.appendRow(["received_at", "poll", "who", "reader", "answers"]);
  }
  return sh;
}

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

function rows_(poll) {
  const v = sheet_().getDataRange().getValues().slice(1);
  const latest = {};
  v.forEach(r => {
    if (r[1] !== poll) return;
    let ans = {};
    try { ans = JSON.parse(r[4]); } catch (e) {}
    latest[r[2]] = { who: r[2], at: new Date(r[0]).toISOString(), reader: r[3] || "", answers: ans,
                     edits: ((latest[r[2]] || {}).edits || 0) + (latest[r[2]] ? 1 : 0) };
  });
  return latest;
}

function doGet(e) {
  const cfg = config_();
  const id = (e.parameter.poll || "").slice(0, 60);
  const p = (cfg.polls || []).find(x => x.id === id);
  if (!p) return out_({ ok: false, error: "unknown poll" });
  const locked = Date.now() >= new Date(p.locks).getTime();
  const latest = rows_(id);
  const filed = Object.keys(latest);
  return out_({ ok: true, poll: id, locked, count: filed.length, filed,
                picks: locked ? Object.values(latest) : null });
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const cfg = config_();
    const b = JSON.parse(e.postData.contents || "{}");
    const p = (cfg.polls || []).find(x => x.id === b.poll);
    if (!p) return out_({ ok: false, error: "unknown poll" });
    const now = Date.now();
    if (now < new Date(p.opens).getTime()) return out_({ ok: false, error: "not open yet" });
    if (now >= new Date(p.locks).getTime()) return out_({ ok: false, locked: true, error: "picks are locked" });
    if (!(cfg.managers || []).some(m => m.id === b.who)) return out_({ ok: false, error: "unknown manager" });
    // keep only the questions this poll asks, trimmed
    const ans = {};
    (p.questions || []).forEach(q => {
      const v = b.answers && b.answers[q.id];
      if (v != null && v !== "") ans[q.id] = String(v).slice(0, q.max || 80);
    });
    const reader = String(b.reader || "").replace(/[^a-z0-9_-]/gi, "").slice(0, 24);
    sheet_().appendRow([new Date(), p.id, b.who, reader, JSON.stringify(ans)]);
    return out_({ ok: true, count: Object.keys(rows_(p.id)).length });
  } finally {
    lock.releaseLock();
  }
}
