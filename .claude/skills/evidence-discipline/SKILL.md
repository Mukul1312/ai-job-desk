---
name: evidence-discipline
description: The working rules for this job-search codebase — check where a fact actually lives before changing logic, measure a defect's cost before rewriting it, and the specific traps (queue.py shadowing, hash() cache keys, haiku's default thinking, gitignored private paths, the shared RemoteRocketship quota). Load before changing src/, tools/, or anything that spends money.
---

# Evidence discipline

Rules earned by this repository, each from a measured failure. Read before
changing `src/`, `tools/`, or anything metered.

## 1. Check the source before debugging the logic

**Every significant defect here was a confident answer from a source that never
held the fact.** Not one was a reasoning failure.

| symptom | actual cause |
|---|---|
| UAE roles ranked first | the location was invented from the query region |
| regex missed the negotiable sentence | the sentence was not in the text it was handed |
| unreachable roles outranked reachable ones | a company fact applied to a role |
| AI roles sent the SWE resume | 1,200 chars of description outvoting the title |
| AI fintech roles vanished | `"finance"` as a bare substring |

Before changing a rule, ask **where does this fact actually live** and go there:

- `data/raw/*/remoterocketship.raw.json.gz` — the API's real response. The
  `.json` beside it is **our normalisation**; reading it and reporting what "the
  source supplies" has been wrong three times.
- the employer's own board (`tools/board.py`) — the feed shows what someone
  syndicated, the board shows everything
- the posting itself (`tools/extract_llm.py`) — RemoteRocketship rewrites 100%
  of its descriptions into bullets averaging 1,987 chars, deleting exactly the
  sentences that decide reachability
- the API's own headers (`X-JobLimit-Remaining`) rather than our inferred log

Then **make the claim carry its evidence**: every extracted field ships with the
verbatim sentence, and a quote not found in the source is a hard rejection, not
a warning. 5 rejections in 964 extractions — the contract holds.

## 2. Measure a defect's cost before rewriting it

Reading code tells you what *could* go wrong. Only the output tells you whether
it does.

Four genuine bugs in the title filter justified a rewrite on inspection.
Fetching the real text for 103 of the 347 removed jobs cost **$0.06** and showed
the rewrite would change nothing: zero were reachable *and* in-band.

The inverse is just as common: `experience_floor_years` scored **20/20** on a
held-out set and was **95%** at 317 postings, with nine of fourteen misses in
the expensive direction. **Never read a small-sample score as generalisation —
state the sample size beside it.**

## 3. Specific traps

**`tools/queue.py` shadows the stdlib `queue`.** Python puts a script's own
directory first on `sys.path`, `concurrent.futures` imports `queue`, and
`ThreadPoolExecutor` dies. Cost three tools an hour. Any `tools/*.py` wanting
threads must strip its own directory from `sys.path` **before** importing
`concurrent.futures` — see the header of `tools/board.py`.

**Never key a persisted cache on `hash()`.** It is randomised per process. This
stranded 490 paid extractions with zero overlap and would have re-paid ~$0.43
every run, silently. Use `hashlib.sha1(x.encode()).hexdigest()[:12]`.

**Only cache a real outcome.** A job skipped because a budget ran out must stay
absent so a later run retries it. `resolve.py` wrote `"unknown"` and poisoned
every later run.

**`claude-haiku-5-5` runs adaptive thinking by default at effort `medium`.**
Thinking bills as output and draws from `max_tokens`, and `display` defaults to
omitted so you never see it. Not setting `output_config: {"effort": "low"}` and
`max_tokens: 4000` cost 73% over estimate and silently broke 15% of a 317-job
run. `anthropic 0.60.0` has no `output_config` parameter — pass it via
`extra_body`.

**`facts/`, `prep/`, `docs/`, `applications/`, `applications.json` and
`*-profile.json` are gitignored and must never reach the public repo**
(`github.com/Mukul1312/ai-job-desk`). Back them up with
`python tools/backup_private.py`, which pushes to the private
`ai-job-desk-career`.

**RemoteRocketship's quota is shared** with the CLI and MCP server, capped per
UTC day, and other sessions may be spending it. Never call the RR API, touch
`data/raw/`, `src/search.py` or `config/` without checking. `itemsPerPage: 1`
plus `totalCount` sizes any filter for one job — and an unrecognised
`locationFilters` value is **dropped**, returning the unfiltered feed at full
price while looking like a successful search. Send a junk control to prove a
filter works at all.

## 4. Where facts come from, by field

The design lives in `src/jdfacts.py`. Do not add a field without deciding its
source, and do not pay a model for something already structured.

```
employment_type   the feed field    feed 89% vs regex 56%
experience_floor  regex             97%, cue-gated
negotiable        the model only    13 positives, 13 phrasings, regex found 0
india_status      the model only    regex called 148 excluded postings "unstated"
no-India employer the board sweep   not in the posting at all
```

A **drop requires `verification >= "posting"`.** A feed summary deleted the
deciding sentences, so its silence is not evidence. Absent extraction, absent
field and `"unstated"` all mean **keep**.

## 5. Reach is the constraint, not fit

1,529 jobs fetched yields **17** India-eligible inside the experience band. No
reordering creates an eighteenth. Of thirteen hand-ruled-out roles: nine on
geography, two on experience band, one on role type, **zero on capability**.

Large enterprises on Workday are **18%** India-eligible against **9%** for
funded startups; Hacker News is **1%**. It is legal infrastructure, not culture.
When asked to improve the shortlist, ask first whether the work adds *reachable*
roles or merely reorders the ones already found.

## 6. How this repo wants to be worked

Propose before changing — state the options with costs, let him choose, then
build it fully rather than a sketch. Flag a concern once with evidence, then
proceed on his decision. On résumés, claim team work at full scope; the one
carve-out is that **any number claimed must survive being asked about**.

```bash
./run.sh --offline --no-deploy      # rebuild from today's snapshots, no quota, no deploy
python tools/board.py --verdicts    # free, 332 boards, writes the definitive-no file
python tools/extract_llm.py --fetch # free; --extract costs ~$0.001/job
python tools/heldout.py --score     # free, re-check layer 1 on unseen postings
python tools/backup_private.py      # mirror the private material
```
