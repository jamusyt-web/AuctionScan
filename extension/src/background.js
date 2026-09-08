// Sweep engine.
//
// Walks your saved searches, triages every row for free, opens only what
// survives, scores it, and notifies you when something is worth buying. One
// tab at a time, jittered pauses, a hard cap per sweep -- it browses the way
// a person does, not the way a crawler does.

import { loadConfig } from "./config.js";
import { siteFor } from "./sites.js";

const SWEEP_ALARM = "auctionscan-sweep";
const SEEN_TTL_MS = 6 * 60 * 60 * 1000;   // re-check a car twice a day
const PAGE_TIMEOUT_MS = 30000;

// Resolvers for detail pages currently being awaited, keyed by tab id.
const pending = new Map();

chrome.runtime.onInstalled.addListener(async () => {
  const cfg = await loadConfig();
  await rescheduleAlarm(cfg);
});

chrome.runtime.onStartup.addListener(async () => {
  await rescheduleAlarm(await loadConfig());
});

export async function rescheduleAlarm(cfg) {
  await chrome.alarms.clear(SWEEP_ALARM);
  if (cfg.sweepEnabled) {
    chrome.alarms.create(SWEEP_ALARM, {
      periodInMinutes: Math.max(5, cfg.sweepIntervalMinutes || 30),
    });
  }
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== SWEEP_ALARM) return;
  const cfg = await loadConfig();
  if (cfg.sweepEnabled) await runSweep(cfg).catch(reportError);
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (msg.type === "detail" || msg.type === "detailFailed") {
      const resolve = pending.get(sender.tab?.id);
      if (resolve) { pending.delete(sender.tab.id); resolve(msg); }
      await saveDiagnostics(msg.diagnostics);
      // A car you opened yourself still counts as a find.
      if (msg.type === "detail") await recordDeal(msg.deal, msg.url, await loadConfig());
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "results") {
      const resolve = pending.get(sender.tab?.id);
      if (resolve) { pending.delete(sender.tab.id); resolve(msg); }
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "configChanged") {
      await rescheduleAlarm(await loadConfig());
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "runSweepNow") {
      const cfg = await loadConfig();
      runSweep(cfg).catch(reportError);
      sendResponse({ ok: true, started: true });
      return;
    }
    if (msg.type === "getState") {
      sendResponse(await chrome.storage.local.get(
        ["finds", "sweepStatus", "lastError", "diagnostics"]));
      return;
    }
    if (msg.type === "clearFinds") {
      await chrome.storage.local.set({ finds: [] });
      sendResponse({ ok: true });
      return;
    }
    sendResponse({ ok: false });
  })();
  return true;   // keep the channel open for the async reply
});

// --- the sweep -------------------------------------------------------------

async function runSweep(cfg) {
  const urls = (cfg.sweepUrls || []).filter(Boolean);
  if (!urls.length) {
    await setStatus({ running: false, note: "No saved searches configured." });
    return;
  }
  if ((await chrome.storage.local.get("sweepStatus")).sweepStatus?.running) return;

  const started = Date.now();
  await setStatus({ running: true, started, opened: 0, found: 0, note: "Starting" });

  let opened = 0, found = 0;
  try {
    for (const url of urls) {
      if (opened >= cfg.maxOpensPerSweep) break;
      await setStatus({ running: true, started, opened, found, note: `Scanning ${short(url)}` });

      const results = await visit(url, "results");
      await saveDiagnostics(results?.diagnostics);
      if (!results) {
        await setStatus({ running: true, started, opened, found,
          note: `No response from ${short(url)} -- page may not have rendered` });
        continue;
      }

      // Each candidate carries the URL that opens it, so there is no guessing
      // about which link belongs to which row.
      const links = (results.candidates || []).map((c) => c.href).filter(Boolean);
      const fresh = await filterSeen([...new Set(links)]);
      if (!links.length) {
        await setStatus({ running: true, started, opened, found,
          note: `${short(url)}: ${results.diagnostics?.links ?? 0} listings seen, ` +
                `none cleared triage` });
      }

      for (const link of fresh) {
        if (opened >= cfg.maxOpensPerSweep) break;
        await sleep(jitter(cfg.openDelayMs));
        const detail = await visit(link, "detail");
        await saveDiagnostics(detail?.diagnostics);
        opened += 1;
        await markSeen(link);
        if (detail?.deal) {
          const alerted = await recordDeal(detail.deal, link, cfg);
          if (alerted) found += 1;
        }
        await setStatus({ running: true, started, opened, found, note: `Opened ${opened}` });
      }
    }
    await setStatus({
      running: false, started, finished: Date.now(), opened, found,
      note: `Done -- opened ${opened}, ${found} worth a look`,
    });
  } catch (err) {
    await setStatus({ running: false, started, opened, found, note: `Stopped: ${err.message}` });
    throw err;
  }
}

/**
 * Open a URL, wait for its content script, close the tab.
 *
 * Hidden tabs are throttled and some single-page apps never paint in one. If
 * a page comes back empty we retry it once with the tab focused before
 * writing it off -- that turns a silent nothing into a real read.
 */
async function visit(url, expect) {
  const first = await visitOnce(url, expect, false);
  if (first && !emptyPage(first)) return first;
  const second = await visitOnce(url, expect, true);
  return second && !emptyPage(second) ? second : (second || first);
}

function emptyPage(msg) {
  const d = msg?.diagnostics;
  return !d || (d.textLength ?? 0) < 400;
}

async function visitOnce(url, expect, activate) {
  if (!siteFor(url)) return null;
  const tab = await chrome.tabs.create({ url, active: activate });
  try {
    return await new Promise((resolve) => {
      const timer = setTimeout(() => { pending.delete(tab.id); resolve(null); }, PAGE_TIMEOUT_MS);
      pending.set(tab.id, (msg) => {
        clearTimeout(timer);
        // "detailFailed" carries diagnostics but no deal: a miss to move past,
        // not an error to retry.
        resolve(msg.type === expect ? msg : (msg.type === "detailFailed" ? msg : null));
      });
    });
  } finally {
    await chrome.tabs.remove(tab.id).catch(() => {});
  }
}

/** Keep the last few page reads so the popup can show what was seen. */
async function saveDiagnostics(d) {
  if (!d) return;
  const { diagnostics = [] } = await chrome.storage.local.get("diagnostics");
  diagnostics.unshift({ ...d, at: Date.now() });
  await chrome.storage.local.set({ diagnostics: diagnostics.slice(0, 20) });
}

async function recordDeal(deal, url, cfg) {
  const worthIt = deal.score >= (cfg.alertScore ?? 25)
    && (!cfg.executableOnly || deal.executable);
  const { finds = [] } = await chrome.storage.local.get("finds");

  const key = deal.listing?.vin || url;
  const existing = finds.findIndex((f) => (f.listing?.vin || f.url) === key);
  const record = { ...deal, url, seenAt: Date.now(), alerted: worthIt };
  if (existing >= 0) finds[existing] = record; else finds.unshift(record);

  await chrome.storage.local.set({ finds: finds.slice(0, 300) });
  if (worthIt) await notify(record);
  return worthIt;
}

async function notify(find) {
  const l = find.listing || {};
  const name = [l.year, l.make, l.model, l.trim].filter(Boolean).join(" ") || "Vehicle";
  const acq = find.acquisition || {};
  const price = acq.price != null ? `$${round(acq.price)}` : "reserve unknown";
  await chrome.notifications.create(`auctionscan-${l.vin || Date.now()}`, {
    type: "basic",
    iconUrl: chrome.runtime.getURL("src/icon.png"),
    title: `${name} — $${round(find.headroom)} under your max`,
    message: `${acq.action || ""}\nPrice ${price} · max bid $${round(find.maxBid)}`,
    priority: 2,
  });
}

chrome.notifications.onClicked.addListener(async (id) => {
  const { finds = [] } = await chrome.storage.local.get("finds");
  const find = finds.find((f) => `auctionscan-${f.listing?.vin}` === id);
  if (find?.url) chrome.tabs.create({ url: find.url });
});

// --- helpers ---------------------------------------------------------------

async function filterSeen(links) {
  const { seen = {} } = await chrome.storage.local.get("seen");
  const now = Date.now();
  return links.filter((l) => !seen[l] || now - seen[l] > SEEN_TTL_MS);
}

async function markSeen(url) {
  const { seen = {} } = await chrome.storage.local.get("seen");
  seen[url] = Date.now();
  const cutoff = Date.now() - SEEN_TTL_MS * 4;
  for (const [k, t] of Object.entries(seen)) if (t < cutoff) delete seen[k];
  await chrome.storage.local.set({ seen });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const jitter = ([lo, hi] = [4000, 11000]) => lo + Math.random() * Math.max(0, hi - lo);
const round = (n) => Math.round(n || 0).toLocaleString("en-US");
const short = (u) => { try { return new URL(u).pathname.slice(0, 40); } catch { return u.slice(0, 40); } };

async function setStatus(sweepStatus) { await chrome.storage.local.set({ sweepStatus }); }
async function reportError(err) {
  await chrome.storage.local.set({ lastError: { message: String(err?.message || err), at: Date.now() } });
}
