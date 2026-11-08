# JD extraction evals

Ground truth for `src/jdextract.py`, built from nineteen postings read by hand on
2026-11-08/09 and written up in `applications/`.

```
expected/<case>.json    the fields, hand-read from the real posting
```

Run: `python src/jdextract.py --eval`

## Two honest weaknesses in this eval

**Only 6 of 18 cases have source text.** The other twelve have ground truth but
no saved posting, so they cannot be scored. Capturing a `jd.txt` for each is the
cheapest improvement available.

**The 6 that do have text are paraphrases, not the real postings.** They were
typed from the originals while writing the application folders, which means the
eval is partly testing against a summary. Streamhub's lost "Bengaluru, IN"
entirely, which is why its `india_status` passed for the wrong reason. **Replace
them with the real page text before trusting any number here above 90%.**

## A finding about ground truth itself

Three seniority labels were originally wrong - Streamhub, MarcoPolo and Synthio
were labelled `mid`/`senior` when **none of those postings contains a seniority
word at all.** The labels recorded a conclusion rather than the text, and the
extractor returning `None` was the more correct answer.

The rule, learned the hard way: **expected values must be what the posting says,
not what the reader concluded.** A field the posting leaves unstated is `null`,
and an eval that punishes `null` teaches the extractor to guess - which is the
failure this whole layer exists to prevent.

Marrina is the counter-example worth keeping: its posting states "Minimum
Experience: Mid-level", so `mid` is correct there and was restored.

---

## Update, 2026-11-09 — the first weakness is now mostly fixed

`cases/` holds the posting text. `tools/capture_jd.py` fetches it from each
employer's **own board API**, so ten of the eighteen cases are now the real
posting rather than a retyping:

```
ClickHouse frontend   archive 1,591 ch  ->  4,129 ch
GitLab Ruby           archive 1,930 ch  ->  7,401 ch
NewRocket FDE UK      archive 3,419 ch  -> 15,032 ch
```

### Why the archives could not be used

RemoteRocketship **rewrites 100% of its 827 descriptions** into a bullet list
averaging 1,987 characters. It is a summary, not a capture. eventfirst's real
posting says *"We value demonstrated skill over a fixed number of years of
experience"* — the sentence `NEGOTIABLE` exists to catch, and the reason that
role is reachable at all. It is **not in the archived description.** Nor are
"Berlin", "cover letter", "Trigger.dev" or "Redis".

### The text is not committed

`cases/*.txt` is gitignored. It is ten employers' copyrighted posting text and
republishing it is not ours to do. `cases/MANIFEST.json` **is** committed and
records the platform, slug, job id and URL of every case, so anyone can rebuild
the corpus:

```bash
python tools/capture_jd.py
```

Six cases stay hand-typed because their ATS has no public API — Gem, Zoho
Recruit, BambooHR's list endpoint and Personio's `search.json` all omit the
description. The manifest labels those `hand-paraphrase`, and the eval reports
real and hand columns separately. A miss on a paraphrase says as much about the
retyping as about the extractor.

### What the real text immediately revealed

**The old 6/6 scores were flattered.** The retyped files had the job title baked
into the first line; a board API returns the body alone. `cmd_eval` was calling
`extract(text)` with no title, so seniority scored **6/6 on paraphrases and 1/10
on real text**. Passing the title — as the pipeline always did — took it to 6/10.

```
field                     overall      real    hand
experience_floor_years    16/16 100%   10/10   6/6
seniority                 12/16  75%    6/10   6/6
employment_type            9/16  56%    3/10   6/6
india_status              15/16  93%   10/10   5/6
experience_negotiable     16/16 100%   10/10   6/6
```

**`employment_type` is the honest failure, and it may not be the extractor's.**
"Full-time" is ATS *metadata* — Ashby returns `employmentType: "FullTime"` as a
field — and frequently appears nowhere in the body. The ground truth says `fte`
because the page displayed it in its header. So the label records chrome the
eval never feeds in. Either the label is wrong or the extractor should read the
structured field instead of regexing the prose; it should not be "fixed" by
making the regex guess.

That is the same mistake as the one in the section above: **ground truth written
from what a human saw, not from what the text says.**

Two cases still have no text at all — `binance-accelerator-ai-dev-tools` and
`reap-tech-lead-security`. Neither employer's posting was findable on a public
board API.

One more thing worth re-checking: the captured GitLab posting is titled
*"Backend Engineer (Ruby), Plan: Spec-Driven Development"* and located **Remote,
United Kingdom** — while `applications/_not-applied/gitlab-senior-backend-ruby.md`
records it as Poland and the ground truth labels it `senior`. The real title
contains no seniority word.
