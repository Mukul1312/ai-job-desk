# Held-out set — 20 postings nobody had read

**Run 2026-11-09. Cost: $1.19** (three labelling passes; two were my own bugs).

## Why

`src/jdextract.py` scored 10/10 on the three fields that need comprehension. That
was not a measurement — `NEGOTIABLE` was written *from* MarcoPolo's and
eventfirst's postings and `INDIA_YES` from the same nineteen. Scoring a pattern
on the cases it was fitted to restates the fitting.

So: 20 postings sampled from 12 companies **none of which appear in
`tests/jd_extraction/`**, captured from their own board APIs, labelled by
`claude-opus-5-5` — which has never seen the regexes — then layer 1 run blind.

```bash
python tools/heldout.py --sample 20    # free
python tools/heldout.py --label        # ~$0.40
python tools/heldout.py --score        # free
```

## Result

```
field                      agrees with the independent label
experience_floor_years     20/20   100%
experience_negotiable      20/20   100%   <- but see below
seniority                  16/20    80%
india_status               14/20    70%
```

### `experience_floor_years` — the overfitting worry was wrong

**20/20 on postings the pattern never saw.** This is the field two roles were
ruled out on, and it generalises. The `REQUIRE_CUE` context test is doing real
work: it reads the floor out of a requirement and ignores the company's age, the
product's history and every other number on the page.

### `experience_negotiable` — 20/20 means nothing here

**Zero of the 20 postings carry the qualifier.** The score is twenty correct
*negatives*. The pattern is still completely untested on positives, and the two
postings that have one are the two it was written from.

This is unresolved. A targeted sample — postings that *do* say years matter less
— is the only thing that would settle it, and random sampling will not find them
at 1-in-827.

### `seniority` — four misses, one root cause

| posting | layer 1 | label | what it actually matched |
|---|---|---|---|
| Quality Assurance Engineer II | `intern` | null | *"Prior backend exposure (an **internship** or role where you wrote code…"* |
| Forward-Deployed Engineer (FDE) | `principal` | null | *"a single growth ladder from Associate through **Principal**"* |
| **Staff** Security Engineer, Enterprise AI | `senior` | `staff` | pattern is `\bstaff engineer\b`; the title says "Staff Security Engineer" |
| **Senior** Software Engineer, AI | `manager` | `senior` | *"reporting to the **Head of** Corporate Applications"* |

Three of the four read a seniority word **that is not about this role** — the
candidate's past, a career ladder, the manager you report to. The fourth is a
pattern too narrow for its own job title.

**This is the project's own thesis reproducing itself inside the tool built to
fix it.** `experience()` requires a cue in context and scored 20/20. `_first()`
is a bare keyword search — exactly what `scorer.py` does, and exactly what
`docs/LLM-EXTRACTION-DESIGN.md` §1 calls "the ceiling of keyword matching".

The fix is structural, not a longer pattern: **read the title first, and require
context before believing the body.**

### `india_status` — 70%, and it is the wrong field

All six misses are the same shape. Layer 1 said `unstated`; the posting named a
location that excludes India without ever saying "India":

```
"This role will be remote, and based in Ireland or U.K."
"This role will be remote, and based in Singapore."
"Location: This role will be office based in Taipei, Taiwan"
"an in-office presence in either our San Francisco or New York City office"
"work from anywhere for the respective region (Central & Eastern Europe)"
"We work remotely first (from Central & Eastern Europe)"
```

`INDIA_NO` catches "US only", "within the US/UK/EU", "no visa sponsorship" and
"must be based in X". It misses *"will be remote, and based in X"* and *"office
based in X"*, which is how postings actually say it — **30% of this sample.**

But widening the regex is the wrong response. These are **location statements**,
and location is a field `tools/board.py` already classifies well, from structured
board data, with a three-bucket honesty rule. `india_status` is trying to infer
reachability from prose while the structured answer sits in the next column.

## What this says about layer 2

**Not one of the ten disagreements needs a language model.**

- The floor field already generalises.
- The seniority misses need *context-aware* matching — the same discipline
  `experience()` already demonstrates.
- The india misses need a **join to location**, not comprehension.

Layer 2's remaining brief is genuinely small: `required` vs `preferred`,
multi-location reachability, and domain. That was the question this set was built
to answer, and the answer is **not yet**.

## Honest limits

- **A model is not ground truth.** Where it agreed with the regex, both could be
  wrong the same way — `employment_type` is the proven case, which is why it is
  not scored here at all.
- **The pool is Greenhouse and Ashby only**, skewing to funded US tech. BambooHR,
  Zoho and Personio postings — where the hand-typed cases came from — are absent.
- **Two of the three labelling runs were thrown away** because of bugs in this
  tool, not the extractor: the first did not give the labeller the job title that
  layer 1 receives, and the second validated quotes against the body only, so
  every label citing the title was rejected and nulled. Both inflated or deflated
  seniority. The numbers above are from the third run.
- `cases/*.txt` is gitignored — third-party posting text. `MANIFEST.json` records
  every URL so the set can be rebuilt.
