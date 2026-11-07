# remote-job-hunter

Finds remote AI/agent and software roles that I can **actually be hired into** from Kanpur, India — then ranks them by fit, working-hours overlap, and salary in LPA.

Most job scrapers answer *"does this JD match my skills?"*. That question is easy and mostly useless: a perfect match in San Francisco is a 0.5-hour overlap with my day and a company with no way to put me on payroll. This answers the three questions that actually decide it.

```
        fetch 9 boards
              │
         score skills          scorer.py   — fit, with evidence weighting
              │
       can they hire me?       enrich.py   — EOR signals, India postings, EPFO
              │
    timezone / salary / region  rank.py    — hard filters, then weighted ranking
              │
      ranked shortlist          report.py  — + which resume to send
```

---

## Quick start

```bash
cp .env.example .env          # optional keys; everything works without them
pip install -r requirements.txt

./run.sh                      # search, back up, deploy
```

`run.sh` is the whole loop. Useful variants:

```bash
./run.sh --offline            # replay today's snapshots: no network, no quota
./run.sh --no-deploy          # report only
./run.sh --new-only           # only what you have not seen before
```

Or drive the pipeline directly:

```bash
python src/main.py --profile-config mukul-profile.json \
  --profile-meta profiles/ai-agent-ml.json --output daily_report.md
```

No cron, no agent framework. Run it when you sit down to apply.

---

## The three filters

### 1. Working-hours overlap — `src/timezone.py`

My window is 09:00–20:00 IST (03:30–14:30 UTC). Every job's location resolves to an IANA timezone and the real overlap gets computed against assumed 9–6 local hours, DST included.

| Location | Overlap | Verdict |
|---|---|---|
| Berlin / Amsterdam (CET) | 6.5h | strong |
| Dubai | 9.0h | strong |
| Singapore | 6.5h | strong |
| London | 5.5h (GMT) / 6.5h (BST) | workable |
| Sydney | 3.5h | poor |
| New York | 0.5h | poor |
| San Francisco | 0.0h | poor |

Below `min_overlap_hours` the job is dropped. Worldwide/unknown locations are **not** penalised — absence of evidence isn't evidence of a bad fit.

### 2. Salary in LPA — `src/salary.py`

Every board quotes differently. All of it normalises to lakhs per annum so a 15 LPA floor means something:

```
"$120k-$150k USD"  → 106.8–133.5 LPA      "12-20 LPA"   → 12.0–20.0 LPA
"€80,000-€100,000" → 77.6–97.0 LPA        "₹18,00,000"  → 18.0 LPA
"1.2 crore"        → 120.0 LPA            "Not listed"  → passes (configurable)
```

Compared against the **top** of a range — a "10–20 LPA" band is still worth a conversation.

### 3. Can they hire in India? — `src/enrich.py`

The constraint that kills most applications, and it's never in the JD. Three layers, cheapest first:

| Layer | Signal | Cost |
|---|---|---|
| 1 | JD mentions Deel / EOR / "anywhere in the world" | free |
| 2 | Company has an India posting on any board we pull | free |
| 3 | **EPFO** — India's statutory provident-fund register | ~5¢ |

EPFO is authoritative: registration means the company legally employs people in India and files monthly returns naming a headcount.

**An EPFO hit is strong evidence. An EPFO miss proves nothing** — companies hiring Indians through an EOR leave no EPFO trace of their own. A miss only withholds a boost; it never disqualifies.

---

## Sources

| Board | Access | Notes |
|---|---|---|
| Remotive, RemoteOK, Jobicy, Himalayas, WorkingNomads | public | global remote |
| **gethired.devhub.co.in** | public API | Indian supply, ~15k jobs; descriptions are login-walled |
| **HN "Who is hiring"** | public Algolia API | startup/AI heavy, ~300 posts/month |
| **RemoteRocketship** | paid key | best server-side filters; skipped without `REMOTEROCKETSHIP_API_KEY` |
| Adzuna | free key | AU/NZ/SG/UK; skipped without keys |

> YC Work at a Startup returns **406 to non-browser clients**. Spoofing browser headers to get around a bot check isn't something this repo does, so HN's hiring thread covers the same startup pool instead.

---

## Stored data — `src/store.py`

RemoteRocketship costs money and is capped at **3,000 jobs / 500 requests per UTC day**. Re-fetching the same listings to re-run a scorer tweak burns that quota for nothing, so every fetch is snapshotted before scoring:

```
data/
  raw/2026-11-07/remoterocketship.json    one file per source, as fetched
                 gethired.json
  seen.json                               url -> first-seen date
```

| Behaviour | Why |
|---|---|
| `cache_hours: 20` on RemoteRocketship | re-running the pipeline reuses today's pull instead of re-buying it |
| `cache_hours: 0` on free sources | refetching costs nothing, so stay current |
| **Raw provider responses archived** | normalisation keeps ~a dozen fields and drops the rest; for a paid source that means a field nobody anticipated could only be recovered by buying the data again. `<source>.raw.json.gz` holds the response verbatim, deduplicated and gzipped |
| **Empty results are never cached** | a zero means quota/auth/outage, not "no jobs" — caching it would silently disable the source for the whole window |
| **Failed fetch falls back to the last good snapshot** | week-old postings are still open; nothing is not. Marked `STALE` in output |
| `seen.json` | marks each job **NEW** vs carried over, so you triage what you have not seen |

```bash
python src/main.py ... --offline      # replay snapshots, zero network calls (0.7s vs ~2min)
python src/main.py ... --new-only     # only jobs never seen in a previous run
python src/store.py                   # what is on disk
```

`--offline` is the one to use while tuning the scorer: re-rank historical pulls instantly without spending a single API call.

Snapshots are pruned after 30 days (`--prune-days`) and `data/` is gitignored — it contains paid API data.

---

## Data backup

`data/` is gitignored here and versioned in a **separate private repo**. Those
snapshots include paid, rate-capped RemoteRocketship data, so losing them means
re-buying quota already spent — and `--prune-days` deletes local copies on a
schedule.

```bash
cd data
git init -b main
gh repo create <you>/ai-job-desk-data --private
git remote add origin https://github.com/<you>/ai-job-desk-data.git
git add -A && git commit -m "data: first snapshot" && git push -u origin main
```

`run.sh` commits and pushes it on every run, **before** deploying — a failed
deploy must never cost you data the quota already paid for.

Private and separate on purpose: these files record which roles you are looking
at and which you have already seen. Nothing secret, but nobody else's business,
and at ~400 KB a day it would bloat a public history fast.

---

## Integration status

| Source | State | Notes |
|---|---|---|
| Remotive, RemoteOK, Jobicy, Himalayas, WorkingNomads | ✅ working | free, no key |
| HN "Who is hiring" | ✅ working | free Algolia API, ~300 posts/month |
| **gethired** (public) | ⚠️ partial | works keyless, but returns **no description field** — every job lands in *"Worth a look"* unscored |
| **gethired** (paid) | ⏸ blocked | credits exhausted |
| **RemoteRocketship** | ⏸ quota | 3,000 jobs / 500 requests per UTC day; resets midnight UTC |
| **Mindcase / EPFO** | ⏸ low balance | ~9¢ per company, results cached permanently |

### gethired — picking this back up later

The public `/api/jobs` endpoint needs no auth and is what runs today. It gives title, company, location, salary, experience and visa fields — but **no job description**, and `ai_summary` is null on every row (verified: 0 of 44). Without description text the scorer cannot grade fit, so these jobs are listed under *"Worth a look"* rather than ranked.

The paid tier exposes an **OAuth-protected MCP server**:

```
POST https://gethired.devhub.co.in/api/mcp
www-authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource/api/mcp"
authorization_servers: ["https://vehnycoyrmqdfywybboc.supabase.co/auth/v1"]   # Supabase Auth
```

Two separate routes, for different jobs:

1. **Pipeline** — put a Supabase JWT in `.env` as `GETHIRED_TOKEN`. `fetch_gethired` already sends it and already tries `description` → `job_description` → `jd` → `full_description` → `ai_summary` → synthesised stub. Token lives in browser localStorage under `sb-vehnycoyrmqdfywybboc-auth-token`, expires ~1h. **Unverified** — we never had credits to confirm the authenticated response actually includes descriptions. Test that first before building refresh handling.
2. **Conversational** — `claude mcp add --transport http gethired https://gethired.devhub.co.in/api/mcp`, needs an interactive session for OAuth. Gives semantic search and "job twins" to Claude; does **not** feed `src/search.py`.

### Quota discipline

RemoteRocketship bills **jobs returned, not unique jobs**, and the API carries no
geography — so each region is a separate request, and a role open to several of
them is charged once per region.

At twelve regions that was 1,200 jobs / 24 requests per run, of which only ~347
were unique: **~71% of the daily cap spent on duplicate rows**, and two runs a
day before lockout. The list is now five non-overlapping anchors — India, UK,
Germany (CET proxy for EU-wide listings), Singapore, UAE:

| | 12 regions | 5 regions |
|---|---|---|
| per run | 1,200 jobs / 24 req | **500 jobs / 10 req** |
| runs per day | 2 | **6** |

Adding neighbours mostly re-bills the same EU-wide roles Germany already
returns. Add one only when a run shows real listings being missed.

Use `--offline` for everything else — it replays snapshots in under a second
and spends nothing.

---

## Tuning

Everything lives in `mukul-profile.json` — no code changes needed.

```jsonc
"timezone":  { "min_overlap_hours": 4.0, "preferred_overlap_hours": 6.0 },
"salary":    { "min_lpa": 15, "accept_unlisted": true },
"region_weights": { "india": 1.0, "emea": 1.0, "apac": 1.0, "americas": 0.4 },
"india_hiring":   { "use_epfo": true, "boost_confirmed": 1.25 }
```

Raise `region_weights.emea` to push European roles up. Set `americas` to `0` to drop them entirely.

Domain targeting is in `profiles/ai-agent-ml.json` — `phrases` decide what gets fetched, `title_keywords` is the **positive gate** that keeps a staffing agency's "Senior Magento Developer" out of an AI search.

---

## How the fit score works

```
fit = 100 × √(coverage × evidence)

coverage = matched / required        how much of what they want you have
evidence = matched / 12  (capped)    how much you matched in absolute terms
```

The `evidence` term is the one that matters. Without it, a JD listing three skills you happen to know scores 100% and outranks a detailed, genuinely relevant role — coverage alone rewards vague job descriptions. A JD naming fewer than 5 recognisable skills isn't scored at all; it surfaces under *"Worth a look"* instead, which is where most Indian listings land since their descriptions aren't public.

---

## Status

Report-only. `apply.py` and `confirm.py` (Playwright auto-apply, 30-minute WhatsApp SKIP window) are kept but **dormant** — `auto_apply` is opt-in, and the LinkedIn flow needs its blind screening-question answering fixed before it's trustworthy.

### Next
- [ ] Add `REMOTEROCKETSHIP_API_KEY` and `MINDCASE_API_KEY` to `.env`
- [ ] Fill the `[Add ...]` placeholders in the resume variants
- [ ] Resume tailoring (`tailor.py`) currently writes an unmodified copy — see `write_tailored_docx`
- [ ] Telegram push if a daily run ever becomes worth automating

---

*Forked from [RajkiranVS/openclaw-remote-job-hunter](https://github.com/RajkiranVS/openclaw-remote-job-hunter) (QA-automation focus) and rebuilt around AI/agent roles, timezone-aware ranking, and India hiring feasibility.*
