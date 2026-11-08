# Job Application Recorder

A Chrome extension that records **where your job applications actually go**.

It observes. It does not fill a field, click a button, submit a form, or send
anything over the network.

## Why this exists

`src/resolve.py` predicts where each application goes by reading the listing.
That prediction has never been checked against reality. This extension records
what really happened, and `src/compare_destinations.py` puts the two side by
side.

Until that comparison exists, every claim about which applicant tracking systems
matter to this job search is an inference from listing text — including the ones
in the project README.

## Install

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. Click **Load unpacked** and choose this `extension/` folder

The toolbar icon shows a badge with the number of applications you have
submitted so far.

## Use

Apply to jobs normally. Nothing changes about how you work.

When you want to check the predictions:

1. Click the extension icon, then **Export JSON**
2. Run the comparison:

```bash
python src/compare_destinations.py ~/Downloads/applications-2026-11-15.json
```

You get: how often the prediction was right, which misses repeat (those are
resolver bugs worth fixing), what you actually applied through, and which form
fields come up most often across real applications.

Give it a week or two of normal applying before reading much into it. Ten
applications is an anecdote.

## What is recorded

For each application page:

- the URL, host, and detected system (ashby, greenhouse, email, direct, ...)
- the listing you came from, when the browser passes a referrer
- page title and a guess at the company
- each form field's **name, id, label, type, and whether it is required**
- whether you submitted, and when

## What is not recorded

**Field values are never read.** Not your name, not your email, not your
answers, not your salary expectation. The extension stores what a form *asks
for*, never what you *wrote*. That is all the comparison needs.

There is no `fetch` anywhere in this extension and no server it talks to.
Everything sits in `chrome.storage.local` until you export it yourself.

## Permissions, honestly

`host_permissions: ["<all_urls>"]` looks alarming and is worth understanding.

Applications live on arbitrary company domains — `careers.somestartup.io` is as
likely as `jobs.ashbyhq.com` — so there is no useful list of sites to restrict
to. The content script therefore loads everywhere, and the first thing it does
is check whether the page looks like an application form. On anything else it
stops within milliseconds and records nothing. See `shouldRecord()` in
`content.js`.

If that trade is not one you want, the alternative is to narrow `matches` in
`manifest.json` to the ATS hosts in `ats-patterns.js`. You will lose the
company-run forms, which are currently 38 of the pipeline's listings.

## Files

| File | Purpose |
|---|---|
| `manifest.json` | Extension manifest (MV3) |
| `content.js` | Detects application pages, observes fields, notices submits |
| `background.js` | Stores records, dedupes by URL, keeps the badge count |
| `popup.html` / `popup.js` | Status, export, clear |
| `ats-patterns.js` | **Generated.** Do not edit. |

`ats-patterns.js` is generated from `ATS_PATTERNS` in `src/resolve.py` so the
extension and the resolver cannot disagree about what counts as which system.
If they drifted, the comparison would measure the drift instead of the
predictions. After changing those patterns:

```bash
python src/gen_extension_patterns.py
```

## Limits worth knowing

- **Referrers are often missing.** Many sites set `rel="noreferrer"`, so the
  link back to the listing is lost and the comparison falls back to matching on
  the apply URL, then on host alone. `compare_destinations.py` reports which
  method it used for each match.
- **Submit detection is best-effort.** It watches form `submit` events and
  clicks on buttons that read like "Submit" or "Apply". A form that posts
  through a custom handler with an unusual button label can be missed.
- **Multi-step forms** (Workday in particular) may record an intermediate step
  as the submission.
- Applications sent by **email** are invisible to this — it only sees browser
  pages. That channel still has to be counted by hand.

---

*Navigation: [`docs/SESSION-2026-11-08.md`](../docs/SESSION-2026-11-08.md) for what to do next · [`docs/DECISIONS.md`](../docs/DECISIONS.md) for standing choices · [`README.md`](../README.md) for how it fits together.*
