/**
 * background.js — store what the content script sees.
 *
 * Everything lives in chrome.storage.local. Nothing is sent anywhere: there is
 * no fetch in this file and no host the extension talks to. The only way data
 * leaves is the Export button in the popup, which writes a file you choose.
 *
 * Records are keyed by apply URL so revisiting a half-finished application
 * updates the existing row instead of creating a second one. A submit is
 * sticky: once an application has been submitted, a later visit to the same
 * page cannot flip it back to unsubmitted.
 */

const STORE_KEY = "applications";
const MAX_RECORDS = 2000;

async function readAll() {
  const bag = await chrome.storage.local.get(STORE_KEY);
  return bag[STORE_KEY] || {};
}

async function writeAll(records) {
  const keys = Object.keys(records);
  if (keys.length > MAX_RECORDS) {
    // Drop the oldest first. This is a measurement log, not an archive.
    keys
      .sort((a, b) => (records[a].seenAt || "").localeCompare(records[b].seenAt || ""))
      .slice(0, keys.length - MAX_RECORDS)
      .forEach((k) => delete records[k]);
  }
  await chrome.storage.local.set({ [STORE_KEY]: records });
}

function keyFor(record) {
  return (record.applyUrl || "").split("?")[0];
}

async function upsert(record, { submitted }) {
  const key = keyFor(record);
  if (!key) return;

  const records = await readAll();
  const existing = records[key];

  if (existing) {
    existing.lastSeen = new Date().toISOString();
    existing.visits = (existing.visits || 1) + 1;
    // Never un-submit: the first submission is the fact worth keeping.
    if (submitted && !existing.submitted) {
      existing.submitted = true;
      existing.submittedAt = record.submittedAt;
      existing.submittedVia = record.submittedVia;
    }
    // A later visit may carry a referrer the first one lacked.
    if (!existing.listingUrl && record.listingUrl) {
      existing.listingUrl = record.listingUrl;
    }
    if (record.fieldCount > (existing.fieldCount || 0)) {
      existing.fieldCount = record.fieldCount;
      existing.fields = record.fields;
      existing.hasFileUpload = record.hasFileUpload;
    }
  } else {
    records[key] = { ...record, visits: 1, lastSeen: record.seenAt };
  }

  await writeAll(records);
  await refreshBadge(records);
}

async function refreshBadge(records) {
  const all = records || (await readAll());
  const submitted = Object.values(all).filter((r) => r.submitted).length;
  await chrome.action.setBadgeText({
    text: submitted ? String(submitted) : "",
  });
  await chrome.action.setBadgeBackgroundColor({ color: "#2563eb" });
}

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  (async () => {
    if (msg && msg.kind === "seen") {
      await upsert(msg.record, { submitted: false });
    } else if (msg && msg.kind === "submitted") {
      await upsert(msg.record, { submitted: true });
    } else if (msg && msg.kind === "export") {
      respond(await readAll());
      return;
    } else if (msg && msg.kind === "clear") {
      await chrome.storage.local.set({ [STORE_KEY]: {} });
      await refreshBadge({});
    }
    respond({ ok: true });
  })();
  return true; // keep the message channel open for the async reply
});

chrome.runtime.onInstalled.addListener(() => refreshBadge());
chrome.runtime.onStartup.addListener(() => refreshBadge());
