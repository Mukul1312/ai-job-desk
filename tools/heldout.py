#!/usr/bin/env python3
"""
heldout.py - score layer 1 on postings nobody has read.

Why this exists. `src/jdextract.py` scores 10/10 on the three fields that need
comprehension:

    experience_floor_years  10/10
    experience_negotiable   10/10
    india_status            10/10

**That is not a measurement.** `NEGOTIABLE` was written from MarcoPolo's and
eventfirst's postings. `INDIA_YES` was written from the same nineteen. Scoring
a pattern on the cases it was fitted to restates the fitting.

So: sample postings from boards nobody has looked at, capture the employer's own
text, label them without reference to the regexes, and run layer 1 blind.

    python tools/heldout.py --sample 20     # pick and capture      (free)
    python tools/heldout.py --label         # label with a model    (~$0.50)
    python tools/heldout.py --score         # layer 1 vs the labels (free)

### On the labels

The labeller is `claude-opus-5-5`, which has never seen the regexes. Every field
must carry a sentence copied verbatim from the posting, and a quote that is not
found in the source is rejected outright rather than warned about.

**A model is not ground truth.** Where it agrees with the regex, both could be
wrong the same way - `employment_type` is the known case, since neither can read
a field that lives in ATS metadata rather than the body. The deliverable is the
**disagreement list**: the handful of postings where the two methods differ, which
is a short enough list for a human to adjudicate and is where the information is.

### Honest limits of the sample

Companies used in the eval are excluded entirely, not just their postings -
same-company postings share boilerplate, and a second GitLab role would not be
held out in any meaningful sense. The pool is Greenhouse and Ashby only, which
skews toward funded US tech; postings on BambooHR, Zoho and Personio are absent
and those are where the hand-typed cases came from.
"""

import json
import os
import random
import re
import sys
import urllib.request
from pathlib import Path

_HERE = Path(__file__).parent.resolve()
sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != _HERE]
sys.path.insert(0, str(_HERE.parent / "src"))

WORKSPACE = _HERE.parent
OUT = WORKSPACE / "tests" / "heldout"
CASES = OUT / "cases"
LABELS = OUT / "labels"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-desk/1.0)"}

# Every company that contributed a case to tests/jd_extraction/. Excluded whole,
# not per-posting: sibling postings share boilerplate and would leak.
USED = {"gitlab", "clickhouse", "cresta", "swordhealth", "globalizationpartners",
        "highmetric", "circuithub", "binance"}

POOL = [
    ("greenhouse", "togetherai"), ("greenhouse", "coinbase"), ("greenhouse", "affirm"),
    ("greenhouse", "canonical"), ("greenhouse", "smartsheet"), ("greenhouse", "sentinelone"),
    ("greenhouse", "twilio"), ("greenhouse", "talkdesk"), ("greenhouse", "telnyx"),
    ("greenhouse", "yipitdata"), ("greenhouse", "bloomreach"), ("greenhouse", "singlestore"),
    ("ashby", "livekit"), ("ashby", "deepgram"), ("ashby", "ElevenLabs"),
    ("ashby", "cartesia"), ("ashby", "openai"), ("ashby", "chainalysis"),
    ("ashby", "clickup"), ("ashby", "coder"), ("ashby", "sardine"),
    ("ashby", "constructor"), ("ashby", "wand-ai"), ("ashby", "certa"),
]

ENGINEERING = re.compile(
    r"\b(engineer|developer|software|backend|frontend|fullstack|full[- ]stack"
    r"|architect|sre|platform|infrastructure|machine learning|\bml\b|\bai\b"
    r"|data|scientist|forward deployed)\b", re.I)
EXCLUDE = re.compile(r"\b(manager|director|head of|vp|recruit|sales|marketing"
                     r"|intern|president|counsel|designer)\b", re.I)


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _detag(raw):
    import html
    t = html.unescape(raw or "")
    t = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return "\n".join(l.strip() for l in re.sub(r"\n{3,}", "\n\n", t).split("\n")).strip()


def harvest(platform, slug):
    out = []
    if platform == "greenhouse":
        d = _get("https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % slug)
        for j in d.get("jobs", []):
            out.append({"title": j.get("title", ""), "id": str(j.get("id")),
                        "location": (j.get("location") or {}).get("name", ""),
                        "url": j.get("absolute_url", ""),
                        "text": _detag(j.get("content"))})
    else:
        d = _get("https://api.ashbyhq.com/posting-api/job-board/%s" % slug)
        for j in d.get("jobs", []):
            locs = [j.get("location", "")] + [
                s.get("location") for s in (j.get("secondaryLocations") or [])
                if isinstance(s, dict)]
            out.append({"title": j.get("title", ""), "id": j.get("id", ""),
                        "location": ", ".join(x for x in locs if x),
                        "url": j.get("jobUrl", ""),
                        "text": j.get("descriptionPlain") or _detag(j.get("descriptionHtml"))})
    return out


def cmd_sample(n, seed=1312):
    CASES.mkdir(parents=True, exist_ok=True)
    pool = [(p, s) for p, s in POOL if s.lower() not in USED]
    rng = random.Random(seed)
    rng.shuffle(pool)

    # Harvest from a fixed number of BOARDS, not until a posting count is hit.
    # Stopping on posting count ends after the first large board (Canonical alone
    # has 119 usable), and the per-company cap then leaves almost nothing.
    boards = max(10, n)
    found = []
    for platform, slug in pool[:boards]:
        try:
            jobs = harvest(platform, slug)
        except Exception as e:
            print("  skip %-16s %s" % (slug, type(e).__name__))
            continue
        good = [j for j in jobs
                if ENGINEERING.search(j["title"]) and not EXCLUDE.search(j["title"])
                and len(j["text"]) > 1200]
        print("  %-12s %-16s %3d postings, %3d usable" % (platform, slug, len(jobs), len(good)))
        for j in good:
            j["platform"], j["slug"] = platform, slug
            found.append(j)

    rng.shuffle(found)
    picked, seen_co = [], {}
    for j in found:                      # at most 2 per company, for spread
        if seen_co.get(j["slug"], 0) >= 2:
            continue
        seen_co[j["slug"]] = seen_co.get(j["slug"], 0) + 1
        picked.append(j)
        if len(picked) >= n:
            break

    man = {}
    for i, j in enumerate(picked, 1):
        stem = "%02d-%s-%s" % (i, j["slug"].lower(),
                               re.sub(r"[^a-z0-9]+", "-", j["title"].lower()).strip("-")[:34])
        (CASES / (stem + ".txt")).write_text(j["text"], encoding="utf-8", newline="\n")
        man[stem] = {k: j[k] for k in ("title", "location", "url", "platform", "slug", "id")}
        man[stem]["chars"] = len(j["text"])
    (OUT / "MANIFEST.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8", newline="\n")
    print("\n  %d postings captured to %s" % (len(man), CASES))
    print("  companies: %s" % ", ".join(sorted(seen_co)))
    return 0


PROMPT = """Read this job posting and extract what it says about who it will hire.

Reply with ONLY a JSON object, no prose, no markdown fence:

{
  "experience_floor_years": <int or null>,
  "experience_quote": <the sentence stating it, or null>,
  "experience_negotiable": <true if the posting says years matter less than
                            demonstrated skill, else false>,
  "experience_negotiable_quote": <that sentence, or null>,
  "seniority": <"intern"|"junior"|"mid"|"senior"|"staff"|"principal"|"lead"|"manager"|null>,
  "seniority_quote": <the words stating it, or null>,
  "india_status": <"explicit_yes" if the posting says the role can be done from
                   India or an Indian city, "explicit_no" if it excludes India,
                   "unstated" otherwise>,
  "india_quote": <that sentence, or null>
}

Rules that matter more than completeness:

- Every quote must be copied VERBATIM from the posting, character for character.
  Do not paraphrase, do not fix punctuation, do not join sentences.
- If the posting does not state something, use null (or "unstated"). Never infer
  it from the job title, the company, or what is likely. An absent value is a
  correct answer.
- For experience_floor_years take the HIGHEST stated minimum. "5+ years, with
  1-2 years in ML" has a floor of 5, not 1. Numbers written as words count:
  "at least ten years" is 10.
- seniority must come from words in the posting OR ITS TITLE. A posting that
  never says a seniority word is null, even if the role is obviously senior.
  The title is part of the posting: "Senior Backend Engineer" states "senior".
  A seniority word describing someone ELSE - the manager you report to, the team
  you join - does not set the role's own seniority.

JOB TITLE: {title}

POSTING BODY:
"""


def _client():
    env = WORKSPACE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    import anthropic
    return anthropic.Anthropic()


QUOTE_FIELDS = [("experience_quote", "experience_floor_years"),
                ("experience_negotiable_quote", "experience_negotiable"),
                ("seniority_quote", "seniority"),
                ("india_quote", "india_status")]


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def cmd_label(model="claude-opus-5-5"):
    LABELS.mkdir(parents=True, exist_ok=True)
    client = _client()
    man = json.loads((OUT / "MANIFEST.json").read_text(encoding="utf-8"))
    tin = tout = 0
    rejected = []
    for stem in sorted(man):
        p = LABELS / (stem + ".json")
        if p.exists():
            print("  have  %s" % stem)
            continue
        jd = (CASES / (stem + ".txt")).read_text(encoding="utf-8")
        prompt = PROMPT.replace("{title}", man[stem].get("title", "")) + jd
        r = client.messages.create(model=model, max_tokens=1500,
                                   messages=[{"role": "user", "content": prompt}])
        tin += r.usage.input_tokens
        tout += r.usage.output_tokens
        raw = "".join(b.text for b in r.content if b.type == "text")
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            rejected.append((stem, "no JSON"))
            print("  REJECT %s  no JSON in reply" % stem)
            continue
        try:
            data = json.loads(m.group(0))
        except Exception as e:
            rejected.append((stem, str(e)[:40]))
            print("  REJECT %s  %s" % (stem, e))
            continue

        # Quote validation. A quote not in the source is a hard failure - the
        # whole point of the contract. Drop the field and its quote, keep the rest.
        #
        # The source is the title AND the body. Validating against the body alone
        # rejected every seniority quote that cited the title ("Senior Software
        # Engineer"), nulling correct labels and scoring layer 1 against them.
        hay = _norm(man[stem].get("title", "") + "\n" + jd)
        bad = []
        for qf, vf in QUOTE_FIELDS:
            q = data.get(qf)
            if q and _norm(q) not in hay:
                bad.append(qf)
                data[qf] = None
                data[vf] = None if vf != "india_status" else "unstated"
                if vf == "experience_negotiable":
                    data[vf] = False
        data["_model"] = model
        data["_quotes_rejected"] = bad
        p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8", newline="\n")
        flag = ("  QUOTES REJECTED: " + ",".join(bad)) if bad else ""
        print("  ok    %-42s floor=%-5s sen=%-8s india=%-12s%s"
              % (stem[:42], data.get("experience_floor_years"),
                 data.get("seniority"), data.get("india_status"), flag))

    cost = tin / 1e6 * 4 + tout / 1e6 * 20
    print("\n  %d in / %d out tokens   ~$%.3f" % (tin, tout, cost))
    if rejected:
        print("  %d rejected: %s" % (len(rejected), rejected))
    return 0


def cmd_score():
    import jdextract as J
    man = json.loads((OUT / "MANIFEST.json").read_text(encoding="utf-8"))
    fields = ["experience_floor_years", "experience_negotiable", "seniority", "india_status"]
    agree = {f: [0, 0] for f in fields}
    rows, disagreements = [], []

    for stem in sorted(man):
        p = LABELS / (stem + ".json")
        if not p.exists():
            continue
        want = json.loads(p.read_text(encoding="utf-8"))
        jd = (CASES / (stem + ".txt")).read_text(encoding="utf-8")
        got = J.extract(jd, man[stem]["title"])
        row = {"stem": stem}
        for f in fields:
            w, g = want.get(f), got.get(f)
            agree[f][1] += 1
            if w == g:
                agree[f][0] += 1
            else:
                disagreements.append((stem, f, g, w, want.get(
                    {"experience_floor_years": "experience_quote",
                     "experience_negotiable": "experience_negotiable_quote",
                     "seniority": "seniority_quote",
                     "india_status": "india_quote"}[f])))
            row[f] = (g, w)
        rows.append(row)

    if not rows:
        print("  no labels yet - run --label")
        return 1

    print("layer 1 against %d held-out postings it was never fitted to\n" % len(rows))
    print("  %-44s %s" % ("posting", "floor   negot   senior  india"))
    for r in rows:
        cells = []
        for f in fields:
            g, w = r[f]
            cells.append(("ok" if g == w else "X").ljust(7))
        print("  %-44s %s" % (r["stem"][:44], "".join(cells)))

    print("\n  %-26s %s" % ("field", "agrees with the independent label"))
    for f in fields:
        ok, n = agree[f]
        print("  %-26s %2d/%-3d  %3d%%" % (f, ok, n, 100 * ok // n if n else 0))

    print("\n  DISAGREEMENTS - %d. This is the list worth a human eye." % len(disagreements))
    for stem, f, g, w, q in disagreements:
        print("\n  %s" % stem)
        print("    %-24s layer1=%-14r label=%r" % (f, g, w))
        if q:
            print("    label's quote: %s" % re.sub(r"\s+", " ", str(q))[:150])
    return 0


def main(argv):
    if "--sample" in argv:
        i = argv.index("--sample")
        n = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 20
        return cmd_sample(n)
    if "--label" in argv:
        return cmd_label()
    if "--score" in argv:
        return cmd_score()
    print(__doc__.strip())
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main(sys.argv))
