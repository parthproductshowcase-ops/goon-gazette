/**
 * The Goon Gazette backend (Google Apps Script, bound to the "Goon Gazette Picks" sheet).
 * Handles Picks (predictions) and the Trade Block.
 *
 * First setup: in the sheet, Extensions > Apps Script, replace everything with this file, Save.
 * Deploy > New deployment > type "Web app": Execute as "Me", Who has access "Anyone".
 * Updating later: paste the new file, Save, then Deploy > Manage deployments > pencil icon >
 * Version: "New version" > Deploy. That keeps the same URL.
 *
 * Trade Block setup (once):
 *   1. Pick "makePins" in the function menu at the top and click Run. It writes a private
 *      "pins" tab with a 4-digit PIN per manager. Send each manager theirs. Rerun to reset all;
 *      or edit a PIN in the tab and run "loadPins".
 *   2. Chat alerts: Project Settings (gear) > Script Properties > Add:
 *        CHAT_KIND = groupme | discord | slack
 *        CHAT_TARGET = GroupMe bot ID, or the Discord/Slack webhook URL
 *      Then pick "testChat" and Run to post a test line.
 *
 * Picks:  POST {poll, who, reader, answers}          GET ?poll=<id>
 * Block:  POST {action:"block_add", who, pin, player, note, reader}
 *         POST {action:"block_remove", who, pin, player, reader}
 *         POST {action:"pin_check", who, pin}
 *         GET  ?block=1  -> {listings:[{who, player, note, since}], recent:[...], max}
 * About:  POST {action:"contact", name, email, message, reader}  -> {ok}
 *         Logs to the "contact" tab and emails CONTACT_TO (the sheet owner's Gmail, since the
 *         web app executes as "Me"). The first deploy after adding this asks for Gmail permission.
 *
 * Lock times, managers, rosters and the block cap come from goongazette.com/data/polls.json.
 * When polls.json has rosters for a manager, he can only list players on that roster.
 */
const CONFIG_URL = "https://goongazette.com/data/polls.json";
const SHEET = "responses";
const BLOCK = "block";
const SITE = "goongazette.com/block.html";
const CONTACT = "contact";
const CONTACT_TO = "shahpv6@gmail.com";
const CONTACT_MAX = 250;

function config_() {
  const cache = CacheService.getScriptCache();
  const hit = cache.get("cfg");
  if (hit) return JSON.parse(hit);
  const txt = UrlFetchApp.fetch(CONFIG_URL + "?t=" + Date.now()).getContentText();
  cache.put("cfg", txt, 300);
  return JSON.parse(txt);
}

function tab_(name, header) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.appendRow(header);
  }
  return sh;
}
function sheet_() { return tab_(SHEET, ["received_at", "poll", "who", "reader", "answers"]); }
function blockSheet_() { return tab_(BLOCK, ["at", "who", "action", "player", "note", "reader"]); }
function contactSheet_() { return tab_(CONTACT, ["at", "name", "email", "message", "reader"]); }

// About page form: log it, email it. Light abuse guard: 300 chars of message max, 5 a day per sender.
function contactPost_(b) {
  const name = String(b.name || "").trim().slice(0, 80);
  const email = String(b.email || "").trim().slice(0, 120);
  const message = String(b.message || "").trim().slice(0, CONTACT_MAX + 50);
  const reader = String(b.reader || "").slice(0, 24);
  if (!name || !message) return { ok: false, error: "name and message are required" };
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return { ok: false, error: "that email doesn't look right" };
  if (String(b.website || "")) return { ok: true };  // honeypot field: bots fill it, people never see it
  const sh = contactSheet_();
  const today = new Date().toDateString();
  const mine = sh.getDataRange().getValues().slice(1).filter(r => new Date(r[0]).toDateString() === today && String(r[2]) === email && email);
  if (mine.length >= 5) return { ok: false, error: "that's enough messages for one day" };
  sh.appendRow([new Date(), name, email, message, reader]);
  try {
    MailApp.sendEmail({
      to: CONTACT_TO,
      replyTo: email || undefined,
      subject: "Goon Gazette message from " + name,
      body: name + (email ? " <" + email + ">" : "") + (reader ? " (reader tag " + reader + ")" : "") + " wrote:\n\n" + message + "\n\nSent from goongazette.com/about.html"
    });
  } catch (err) {
    return { ok: true, mailed: false };  // logged in the sheet even if Gmail refused
  }
  return { ok: true, mailed: true };
}

function out_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

/* ---------- Picks ---------- */

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

function picksGet_(cfg, id) {
  const p = (cfg.polls || []).find(x => x.id === id);
  if (!p) return { ok: false, error: "unknown poll" };
  const locked = Date.now() >= new Date(p.locks).getTime();
  const latest = rows_(id);
  const filed = Object.keys(latest);
  return { ok: true, poll: id, locked, count: filed.length, filed, picks: locked ? Object.values(latest) : null };
}

function picksPost_(cfg, b) {
  const p = (cfg.polls || []).find(x => x.id === b.poll);
  if (!p) return { ok: false, error: "unknown poll" };
  const now = Date.now();
  if (now < new Date(p.opens).getTime()) return { ok: false, error: "not open yet" };
  if (now >= new Date(p.locks).getTime()) return { ok: false, locked: true, error: "picks are locked" };
  if (!(cfg.managers || []).some(m => m.id === b.who)) return { ok: false, error: "unknown manager" };
  const ans = {};
  (p.questions || []).forEach(q => {
    const v = b.answers && b.answers[q.id];
    if (v != null && v !== "") ans[q.id] = String(v).slice(0, q.max || 80);
  });
  const reader = String(b.reader || "").replace(/[^a-z0-9_-]/gi, "").slice(0, 24);
  sheet_().appendRow([new Date(), p.id, b.who, reader, JSON.stringify(ans)]);
  return { ok: true, count: Object.keys(rows_(p.id)).length };
}

/* ---------- PINs ---------- */

function pins_() {
  try { return JSON.parse(PropertiesService.getScriptProperties().getProperty("PINS") || "{}"); }
  catch (e) { return {}; }
}
function pinOk_(who, pin) {
  const pins = pins_();
  return !!pins[who] && String(pin || "") === String(pins[who]);
}

// Run once from the editor. Writes a private "pins" tab and stores the PINs for checking.
function makePins() {
  const cfg = config_();
  const used = {};
  const pins = {};
  cfg.managers.forEach(m => {
    let p;
    do { p = String(Math.floor(1000 + Math.random() * 9000)); } while (used[p]);
    used[p] = 1;
    pins[m.id] = p;
  });
  PropertiesService.getScriptProperties().setProperty("PINS", JSON.stringify(pins));
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName("pins") || ss.insertSheet("pins");
  sh.clear();
  sh.appendRow(["manager_id", "manager", "pin", "personal link"]);
  cfg.managers.forEach(m => sh.appendRow([m.id, m.name, pins[m.id], "https://" + SITE + "?r=" + m.id]));
}

// Run after editing a PIN by hand in the "pins" tab.
function loadPins() {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("pins");
  const pins = {};
  sh.getDataRange().getValues().slice(1).forEach(r => { if (r[0]) pins[r[0]] = String(r[2]); });
  PropertiesService.getScriptProperties().setProperty("PINS", JSON.stringify(pins));
}

/* ---------- Chat alerts ---------- */

function chat_(text) {
  const props = PropertiesService.getScriptProperties();
  const kind = (props.getProperty("CHAT_KIND") || "").toLowerCase();
  const target = props.getProperty("CHAT_TARGET") || "";
  if (!kind || !target) return false;
  let url = target, body;
  if (kind === "groupme") { url = "https://api.groupme.com/v3/bots/post"; body = { bot_id: target, text }; }
  else if (kind === "discord") body = { content: text };
  else if (kind === "slack") body = { text };
  else return false;
  try {
    UrlFetchApp.fetch(url, { method: "post", contentType: "application/json", payload: JSON.stringify(body), muteHttpExceptions: true });
    return true;
  } catch (e) { return false; }
}

function testChat() {
  chat_("The Goon Gazette trade wire is live. Every player that hits the block, or slinks off it, gets announced here. " + SITE);
}

/* ---------- Trade Block ---------- */

function listings_() {
  const v = blockSheet_().getDataRange().getValues().slice(1);
  const on = {};    // key who|player(lower) -> listing
  const recent = [];
  v.forEach(r => {
    const who = r[1], action = r[2], player = String(r[3]), key = who + "|" + player.toLowerCase();
    const at = new Date(r[0]).toISOString();
    if (action === "add") on[key] = { who, player, note: r[4] || "", since: at };
    else if (action === "remove") delete on[key];
    recent.push({ at, who, action, player, note: r[4] || "" });
  });
  return { listings: Object.values(on).sort((a, b) => a.since < b.since ? 1 : -1), recent: recent.slice(-20).reverse() };
}

function norm_(s) {
  return String(s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function blockGet_(cfg) {
  const L = listings_();
  return { ok: true, max: (cfg.block && cfg.block.max) || 2, listings: L.listings, recent: L.recent };
}

function blockPost_(cfg, b) {
  const max = (cfg.block && cfg.block.max) || 2;
  const m = (cfg.managers || []).find(x => x.id === b.who);
  if (!m) return { ok: false, error: "unknown manager" };
  if (!pinOk_(b.who, b.pin)) return { ok: false, error: "wrong PIN" };
  if (b.action === "pin_check") return { ok: true };
  let player = String(b.player || "").replace(/\s+/g, " ").trim().slice(0, 40);
  if (!player) return { ok: false, error: "no player" };
  // with this season's rosters loaded, a manager can only list his own players
  const roster = cfg.rosters && cfg.rosters.teams && cfg.rosters.teams[b.who];
  if (roster && b.action === "block_add") {
    const hit = (roster.players || []).find(n => norm_(n) === norm_(player));
    if (!hit) return { ok: false, error: player + " isn't on your roster" };
    player = hit;
  }
  const reader = String(b.reader || "").replace(/[^a-z0-9_-]/gi, "").slice(0, 24);
  const L = listings_().listings;
  const mine = L.filter(x => x.who === b.who);
  const here = mine.find(x => x.player.toLowerCase() === player.toLowerCase());
  if (b.action === "block_add") {
    if (here) return { ok: false, error: player + " is already on your block" };
    if (mine.length >= max) return { ok: false, error: "you already have " + max + " on the block. Pull one first" };
    const other = L.find(x => x.player.toLowerCase() === player.toLowerCase());
    if (other) return { ok: false, error: player + " is already on someone else's block" };
    const note = String(b.note || "").replace(/\s+/g, " ").trim().slice(0, 60);
    blockSheet_().appendRow([new Date(), b.who, "add", player, note, reader]);
    chat_("ON THE BLOCK: " + m.name + " is shopping " + player + "." + (note ? " Wants: " + note + "." : "") + " Make an offer: " + SITE);
  } else if (b.action === "block_remove") {
    if (!here) return { ok: false, error: player + " isn't on your block" };
    blockSheet_().appendRow([new Date(), b.who, "remove", here.player, "", reader]);
    chat_("OFF THE BLOCK: " + m.name + " pulled " + here.player + ". Either a deal got done or nobody bit. " + SITE);
  } else {
    return { ok: false, error: "unknown action" };
  }
  return blockGet_(cfg);
}

/* ---------- Routing ---------- */

function doGet(e) {
  const cfg = config_();
  if (e.parameter.block) return out_(blockGet_(cfg));
  return out_(picksGet_(cfg, (e.parameter.poll || "").slice(0, 60)));
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const cfg = config_();
    const b = JSON.parse(e.postData.contents || "{}");
    if (b.action === "contact") return out_(contactPost_(b));
    if (b.action === "block_add" || b.action === "block_remove" || b.action === "pin_check") return out_(blockPost_(cfg, b));
    return out_(picksPost_(cfg, b));
  } finally {
    lock.releaseLock();
  }
}
