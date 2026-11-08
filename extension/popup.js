/**
 * popup.js — show what has been recorded and hand it over as a file.
 *
 * Export writes a JSON file through a normal download. It does not upload
 * anything; src/compare_destinations.py reads that file from disk.
 */

const ask = (msg) =>
  new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));

function render(records) {
  const rows = Object.values(records || {});
  const submitted = rows.filter((r) => r.submitted);

  document.getElementById("submitted").textContent = submitted.length;
  document.getElementById("seen").textContent = rows.length;

  // Count by system, submitted applications first since those are the real
  // evidence; a form merely opened proves much less.
  const counts = {};
  for (const r of rows) {
    const k = r.ats || "unknown";
    counts[k] = counts[k] || { seen: 0, submitted: 0 };
    counts[k].seen += 1;
    if (r.submitted) counts[k].submitted += 1;
  }

  const table = document.getElementById("breakdown");
  const empty = document.getElementById("empty");
  const entries = Object.entries(counts).sort(
    (a, b) => b[1].submitted - a[1].submitted || b[1].seen - a[1].seen
  );

  table.innerHTML = "";
  if (!entries.length) {
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";

  for (const [kind, c] of entries) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = kind;
    const n = document.createElement("td");
    n.className = "r";
    n.textContent = c.submitted ? `${c.submitted} / ${c.seen}` : `- / ${c.seen}`;
    tr.append(name, n);
    table.append(tr);
  }
}

document.getElementById("export").addEventListener("click", async () => {
  const records = await ask({ kind: "export" });
  const payload = {
    exportedAt: new Date().toISOString(),
    note: "Recorded by the Job Application Recorder extension. Field values are never captured.",
    count: Object.keys(records || {}).length,
    applications: Object.values(records || {}),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `applications-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
});

document.getElementById("clear").addEventListener("click", async () => {
  // Deliberately two-step: the log is the only copy of this evidence.
  const btn = document.getElementById("clear");
  if (btn.dataset.armed !== "1") {
    btn.dataset.armed = "1";
    btn.textContent = "Really clear?";
    setTimeout(() => {
      btn.dataset.armed = "0";
      btn.textContent = "Clear";
    }, 4000);
    return;
  }
  await ask({ kind: "clear" });
  btn.dataset.armed = "0";
  btn.textContent = "Clear";
  render({});
});

(async () => render(await ask({ kind: "export" })))();
