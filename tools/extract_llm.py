#!/usr/bin/env python3
"""
extract_llm.py - layer 2. Read the real posting with a model, cache, compare.

Why this is worth paying for, measured on the held-out set
(`tests/heldout/README.md`): layer 1 and `claude-opus-5-5` disagreed ten times,
and **the model was right ten times out of ten** - four seniority words that
described somebody else, six postings naming a location that excludes India
without ever typing "India".

Why it runs on board text and not on the feed: RemoteRocketship rewrites 100% of
its descriptions into a bullet summary averaging 1,987 characters. eventfirst's
*"We value demonstrated skill over a fixed number of years of experience"* - the
sentence that makes the role reachable - is not in it. **Paying a model to read
a summary does not recover what the summary deleted.** So this tool fetches the
employer's own text first, and only runs on jobs where it can get it.

    python tools/extract_llm.py --fetch              # board APIs, free
    python tools/extract_llm.py --extract            # haiku, ~$0.16 for 321
    python tools/extract_llm.py --extract --limit 25 # try a slice first
    python tools/extract_llm.py --compare            # regex vs model

What it does NOT extract: `employment_type`, `tech_stack`, `salary`,
`company_size`, visa sponsorship. Those are tier A - the feed already supplies
them at 100% coverage, and on employment type the feed scores 89% against layer
1's 56%. Paying a model to re-derive them from prose costs money and accuracy.
See `docs/LLM-EXTRACTION-DESIGN.md` section 6b.
"""

import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# This block MUST come before `concurrent.futures`. tools/queue.py shadows the
# stdlib `queue` for any script run as `python tools/<x>.py`, because Python puts
# the script's own directory first on sys.path. concurrent.futures.thread does
# `import queue` at module load, so importing it above this line is already too
# late - ThreadPoolExecutor then dies on `queue.SimpleQueue`.
#
# Third time this has cost time. The real fix is renaming tools/queue.py; see
# docs/TODO.md.
_HERE = Path(__file__).parent.resolve()
sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != _HERE]
sys.path.insert(0, str(_HERE.parent / "src"))

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

WORKSPACE = _HERE.parent
TEXT_DIR = WORKSPACE / "data" / "jd_text"
INDEX = TEXT_DIR / "index.json"
CACHE = WORKSPACE / "jd_extract_cache.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-desk/1.0)"}

GH = re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)/jobs/(\d+)", re.I)
ASH = re.compile(r"ashbyhq\.com/([a-z0-9_.%-]+)/([0-9a-f-]{8,})", re.I)

# Platforms whose board API returns the posting body. Checked one by one against
# real URLs from data/raw/ - three that board.py can list do NOT return text and
# are deliberately absent:
#   personio  search.json has description: ""      (16 jobs)
#   bamboohr  /careers/list has no body; the detail JSON shape differs  (22)
#   breezy    /json lists postings but carries no body; HTML only       (14)
MORE = [
    ("lever",           re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)/([0-9a-f-]{8,})", re.I)),
    ("workable",        re.compile(r"apply\.workable\.com/([a-z0-9_-]+)/j/([A-Z0-9]+)", re.I)),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)/(\d+)", re.I)),
    ("recruitee",       re.compile(r"([a-z0-9_-]+)\.recruitee\.com/o/([a-z0-9-]+)", re.I)),
]

MODEL = "claude-haiku-5-5"
IN_RATE, OUT_RATE = 0.10, 0.50          # $ per 1M tokens


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------

def _load_rows():
    import glob
    rows, seen = [], set()
    for f in glob.glob(str(WORKSPACE / "data/raw/*/*.json")):
        try:
            raw = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in (raw if isinstance(raw, list) else raw.get("jobs", [])):
            if not isinstance(r, dict):
                continue
            u = r.get("url") or ""
            if u and u not in seen:
                seen.add(u)
                rows.append(r)
    return rows


def _resolve(r):
    blob = (r.get("applyUrl") or "") + " " + (r.get("url") or "")
    m = GH.search(blob)
    if m:
        return "greenhouse", m.group(1).lower(), m.group(2)
    a = ASH.search(blob)
    if a:
        return "ashby", urllib.parse.unquote(a.group(1)), a.group(2)
    for name, pat in MORE:
        m = pat.search(blob)
        if m:
            return name, urllib.parse.unquote(m.group(1)), m.group(2)
    return None, None, None


def _detag(raw):
    t = html.unescape(raw or "")
    t = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return "\n".join(l.strip() for l in re.sub(r"\n{3,}", "\n\n", t).split("\n")).strip()


def _urlkey(url):
    """A cache key that survives a restart.

    This used `abs(hash(url)) % 10**9`. Python randomises string hashing per
    process, so every --fetch renamed all 705 feed/schema.org entries and
    orphaned their cached extractions - measured as ZERO overlap between the
    cache and a freshly written index. Each run would silently re-pay ~$0.43
    to re-extract postings already sitting on disk.

    Exactly the failure store.save_raw and resolve.py's cache bug were written
    about, reintroduced here by reaching for the builtin.
    """
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _lever(slug):
    out = {}
    for j in _json("https://api.lever.co/v0/postings/%s?mode=json" % slug):
        # The body is split: descriptionPlain is the intro, `lists` holds the
        # requirements as structured bullets. Either alone is a third of the
        # posting.
        parts = [j.get("descriptionPlain") or ""]
        for L in (j.get("lists") or []):
            parts.append((L.get("text") or "") + "\n" + _detag(L.get("content")))
        parts.append(j.get("additionalPlain") or "")
        cat = j.get("categories") or {}
        out[j.get("id", "")] = {
            "text": "\n\n".join(p for p in parts if p.strip()),
            "title": j.get("text", ""),
            "location": ", ".join(x for x in [cat.get("location"), j.get("country")] if x)}
    return out


def _workable(slug):
    d = _json("https://apply.workable.com/api/v1/widget/accounts/%s?details=true" % slug)
    out = {}
    for j in d.get("jobs", []):
        out[j.get("shortcode", "")] = {
            "text": _detag(j.get("description")),
            "title": j.get("title", ""),
            "location": ", ".join(x for x in [j.get("city"), j.get("state"),
                                              j.get("country")] if x)}
    return out


def _smartrecruiters(slug):
    """Listing carries no body; each posting needs its own call."""
    out = {}
    listing = _json("https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100" % slug)
    for p in listing.get("content", []):
        pid = str(p.get("id"))
        try:
            det = _json("https://api.smartrecruiters.com/v1/companies/%s/postings/%s" % (slug, pid))
        except Exception:
            continue
        sec = (det.get("jobAd") or {}).get("sections") or {}
        text = "\n\n".join(_detag((sec.get(k) or {}).get("text"))
                           for k in ("jobDescription", "qualifications",
                                     "additionalInformation", "companyDescription"))
        loc = p.get("location") or {}
        out[pid] = {"text": text.strip(), "title": p.get("name", ""),
                    "location": ", ".join(x for x in [loc.get("city"), loc.get("region"),
                                                      loc.get("country")] if x)}
    return out


def _recruitee(slug):
    out = {}
    for o in _json("https://%s.recruitee.com/api/offers/" % slug).get("offers", []):
        text = "\n\n".join(_detag(o.get(k)) for k in ("description", "requirements", "highlight"))
        key = o.get("slug") or str(o.get("id"))
        locs = [l.get("name") for l in (o.get("locations") or []) if isinstance(l, dict)]
        out[key] = {"text": text.strip(), "title": o.get("title") or o.get("sharing_title", ""),
                    "location": ", ".join([o.get("location") or ""] + locs).strip(", ")}
    return out


EXTRA = {"lever": _lever, "workable": _workable,
         "smartrecruiters": _smartrecruiters, "recruitee": _recruitee}


def jsonld(url):
    """Read a posting out of schema.org JobPosting markup on any job page.

    Google Jobs indexing requires this markup, so most ATSs emit it whether or
    not they publish an API. One function covers the platforms with no usable
    endpoint - measured: workday 9,053 chars, jazzhr 22,094, teamtailor 7,115,
    breezy 6,145, personio 4,435, join.com 2,202 - plus a third of the one-off
    company career sites.

    Returns None rather than a guess. BambooHR and Zoho render client-side and
    emit nothing; those stay uncovered.
    """
    try:
        req = urllib.request.Request(url, headers=UA)
        page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception:
        return None
    best = None
    for m in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>",
                         page, re.S | re.I):
        try:
            d = json.loads(m.group(1).strip())
        except Exception:
            continue
        for o in (d if isinstance(d, list) else [d]):
            if not isinstance(o, dict):
                continue
            if o.get("@type") != "JobPosting" and "description" not in o:
                continue
            text = _detag(str(o.get("description") or ""))
            if len(text) < 400:
                continue
            loc = o.get("jobLocation") or {}
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = (loc or {}).get("address") or {}
            where = ", ".join(str(addr.get(k)) for k in
                              ("addressLocality", "addressRegion", "addressCountry")
                              if addr.get(k))
            if o.get("jobLocationType") == "TELECOMMUTE":
                where = (where + ", Remote").strip(", ")
            if best is None or len(text) > len(best["text"]):
                best = {"text": text, "title": o.get("title", ""), "location": where}
    return best


def _board(platform, slug):
    if platform in EXTRA:
        return EXTRA[platform](slug)
    if platform == "greenhouse":
        u = "https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % slug
    else:
        u = "https://api.ashbyhq.com/posting-api/job-board/%s" % urllib.parse.quote(slug)
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=45) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    out = {}
    for j in d.get("jobs", []):
        if platform == "greenhouse":
            out[str(j.get("id"))] = {
                "text": _detag(j.get("content")), "title": j.get("title", ""),
                "location": (j.get("location") or {}).get("name", "")}
        else:
            locs = [j.get("location", "")] + [
                s.get("location") for s in (j.get("secondaryLocations") or [])
                if isinstance(s, dict)]
            out[j.get("id", "")] = {
                "text": j.get("descriptionPlain") or _detag(j.get("descriptionHtml")),
                "title": j.get("title", ""),
                "location": ", ".join(x for x in locs if x)}
    return out


def cmd_fetch():
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    rows = _load_rows()
    targets, boards = {}, {}
    for r in rows:
        p, s, jid = _resolve(r)
        if not p:
            continue
        key = "%s-%s-%s" % (p, re.sub(r"[^a-z0-9]+", "", s.lower()), jid)
        targets[key] = {"platform": p, "slug": s, "job_id": jid,
                        "url": r.get("url", ""), "feed_title": r.get("title", ""),
                        "feed_company": r.get("company", "")}
        boards.setdefault((p, s), []).append(key)

    print("%d jobs on greenhouse/ashby across %d boards" % (len(targets), len(boards)))
    print("fetching each board once, not each job\n")

    def one(item):
        (p, s), keys = item
        try:
            return (p, s), keys, _board(p, s), None
        except Exception as e:
            return (p, s), keys, None, type(e).__name__

    index, got, dead = {}, 0, 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        for (p, s), keys, data, err in pool.map(one, list(boards.items())):
            if err:
                dead += 1
                continue
            for k in keys:
                jid = targets[k]["job_id"]
                hit = data.get(jid) or next(
                    (v for kk, v in data.items() if kk.startswith(jid[:12])), None)
                if not hit or len(hit["text"]) < 400:
                    continue
                (TEXT_DIR / (k + ".txt")).write_text(hit["text"], encoding="utf-8",
                                                     newline="\n")
                index[k] = dict(targets[k], title=hit["title"],
                                location=hit["location"], chars=len(hit["text"]))
                got += 1

    # Pass 2 - text already on disk. Only RemoteRocketship rewrites its
    # descriptions; HN hiring comments, Remotive, RemoteOK and WorkingNomads
    # carry the employer's own prose. Reading it costs nothing and no fetch.
    covered = {targets[k]["url"] for k in index}
    feed = 0
    for r in rows:
        u = r.get("url") or ""
        src = (r.get("source") or "").split("[")[0].strip()
        d = r.get("description") or ""
        if not u or u in covered or src == "RemoteRocketship" or len(d) < 600:
            continue
        key = "feed-" + re.sub(r"[^a-z0-9]+", "", (r.get("company") or "x").lower())[:18] \
              + "-" + _urlkey(u)
        (TEXT_DIR / (key + ".txt")).write_text(d, encoding="utf-8", newline="\n")
        index[key] = {"platform": "feed:" + src.lower(), "slug": src.lower(),
                      "job_id": "", "url": u, "title": r.get("title", ""),
                      "location": r.get("location", ""),
                      "feed_company": r.get("company", ""),
                      "feed_title": r.get("title", ""), "chars": len(d)}
        covered.add(u)
        feed += 1
    print("  feed rows carrying real prose: %d (no fetch needed)" % feed)

    # Pass 3 - schema.org markup for everything still uncovered.
    rest = [r for r in rows if (r.get("url") or "") and r["url"] not in covered
            and not (r.get("url") or "").startswith("https://news.ycombinator.com")]
    print("  trying schema.org JobPosting on %d remaining pages..." % len(rest))

    def jl(r):
        return r, jsonld(r.get("applyUrl") or r.get("url"))

    ld = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        for r, hit in pool.map(jl, rest):
            if not hit:
                continue
            u = r["url"]
            key = "ld-" + re.sub(r"[^a-z0-9]+", "", (r.get("company") or "x").lower())[:18] \
                  + "-" + _urlkey(u)
            (TEXT_DIR / (key + ".txt")).write_text(hit["text"], encoding="utf-8", newline="\n")
            index[key] = {"platform": "jsonld", "slug": urllib.parse.urlparse(u).netloc,
                          "job_id": "", "url": u,
                          "title": hit["title"] or r.get("title", ""),
                          "location": hit["location"] or r.get("location", ""),
                          "feed_company": r.get("company", ""),
                          "feed_title": r.get("title", ""), "chars": len(hit["text"])}
            ld += 1
    print("  schema.org yielded text for %d of %d (%d%%)"
          % (ld, len(rest), 100 * ld // max(len(rest), 1)))

    INDEX.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8", newline="\n")
    tot = sum(v["chars"] for v in index.values())
    print("  real text for %d of %d jobs   (%d boards unreachable)" % (got, len(targets), dead))
    if got:
        print("  %s chars total, %d average" % (format(tot, ","), tot // got))
        est = got * (1100 + tot / got) / 2.85 / 1e6 * IN_RATE + got * 450 / 1e6 * OUT_RATE
        print("  estimated extraction cost on %s: $%.2f" % (MODEL, est))
    return 0


# --------------------------------------------------------------------------
# Extract
# --------------------------------------------------------------------------

PROMPT = """Extract from this job posting. Reply with ONLY a JSON object:

{
  "experience_floor_years": <int or null>,
  "experience_quote": <verbatim sentence, or null>,
  "experience_negotiable": <true only if it says years matter less than
                            demonstrated skill>,
  "experience_negotiable_quote": <verbatim sentence, or null>,
  "seniority": <"intern"|"junior"|"mid"|"senior"|"staff"|"principal"|"lead"|"manager"|null>,
  "seniority_quote": <verbatim, or null>,
  "india_status": <"explicit_yes"|"explicit_no"|"unstated">,
  "india_quote": <verbatim sentence, or null>,
  "locations": [<each place or region the role may be done from, as written>],
  "required": [<skills/technologies stated as required>],
  "preferred": [<stated as nice-to-have>]
}

Rules that matter more than completeness:

- Quotes must be copied VERBATIM, character for character. Never paraphrase.
- Never infer. If the posting does not state it, use null or "unstated". An
  absent value is a correct answer.
- experience_floor_years: take the HIGHEST stated minimum. "5+ years, including
  1-2 on ML" has a floor of 5. Words count: "at least ten years" is 10.
- seniority comes from words in the posting or its title. A seniority word about
  SOMEONE ELSE - the manager you report to, a career ladder, the experience you
  must have had - does not set this role's seniority.
- india_status is "explicit_no" when the posting names locations that exclude
  India, even if it never says "India". "This role will be remote, and based in
  Ireland or U.K." is explicit_no.

JOB TITLE: {title}

POSTING BODY:
"""

QF = [("experience_quote", "experience_floor_years", None),
      ("experience_negotiable_quote", "experience_negotiable", False),
      ("seniority_quote", "seniority", None),
      ("india_quote", "india_status", "unstated")]


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


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def cmd_extract(limit=None, budget=None, effort=None, max_tokens=1600,
                order_urls=None, quiet=False):
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    client = _client()

    todo = [k for k in sorted(index) if k not in cache]
    if order_urls:
        # Spend the budget on the best jobs, not on fetch order. The
        # caller passes URLs in descending fit; anything it did not
        # rank goes last.
        rank_of = {u: n for n, u in enumerate(order_urls)}
        todo.sort(key=lambda k: rank_of.get(
            (index[k] or {}).get("url", ""), len(rank_of)))
    if limit:
        todo = todo[:limit]
    # Mutable one-element counter, exactly as enrich_epfo uses it: a spent budget
    # stops the stage instead of failing the run.
    left = [budget if budget is not None else len(todo)]
    if not quiet:
        print("%d cached, %d to extract, budget %d, effort %s, max_tokens %d\n"
              % (len(cache), len(todo), left[0], effort or "default(medium)",
                 max_tokens))

    tin = tout = 0
    rejected = 0
    for i, k in enumerate(todo, 1):
        if left[0] <= 0:
            print("  budget spent, stopping at %d" % (i - 1))
            break
        jd = (TEXT_DIR / (k + ".txt")).read_text(encoding="utf-8")
        kw = {}
        if effort:
            # claude-haiku-5-5 runs adaptive thinking by DEFAULT at effort
            # "medium". Thinking tokens are billed as output and are drawn from
            # max_tokens, which is why the first run cost 2x the estimate and
            # why 47 of 317 jobs spent their budget thinking and returned no
            # JSON. Extraction from a supplied document does not need
            # deliberation; "low" is the lever, since thinking cannot be
            # disabled on this model.
            kw["output_config"] = {"effort": effort}
        try:
            r = client.messages.create(
                model=MODEL, max_tokens=max_tokens,
                messages=[{"role": "user",
                           "content": PROMPT.replace("{title}", index[k]["title"]) + jd}],
                **kw)
        except TypeError:
            # anthropic 0.60.0 predates output_config as a named parameter.
            r = client.messages.create(
                model=MODEL, max_tokens=max_tokens,
                messages=[{"role": "user",
                           "content": PROMPT.replace("{title}", index[k]["title"]) + jd}],
                extra_body=kw)
        except Exception as e:
            print("  %-52s API %s" % (k[:52], type(e).__name__))
            continue
        left[0] -= 1
        tin += r.usage.input_tokens
        tout += r.usage.output_tokens
        raw = "".join(b.text for b in r.content if b.type == "text")
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            print("  %-52s no JSON" % k[:52])
            continue
        try:
            data = json.loads(m.group(0))
        except Exception:
            print("  %-52s bad JSON" % k[:52])
            continue

        hay = _norm(index[k]["title"] + "\n" + jd)
        bad = []
        for qf, vf, fallback in QF:
            q = data.get(qf)
            if q and _norm(q) not in hay:
                bad.append(qf)
                data[qf] = None
                data[vf] = fallback
        rejected += bool(bad)
        data["_model"] = MODEL
        data["_quotes_rejected"] = bad
        data["_usage"] = {"in": r.usage.input_tokens, "out": r.usage.output_tokens,
                          "effort": effort or "medium"}
        # Only a real outcome is cached. A skipped or failed job stays absent so
        # a later run retries it - resolve.py's bug, not repeated here.
        cache[k] = data
        if i % 25 == 0 or i == len(todo):
            CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8", newline="\n")
            if not quiet:
                print("  %3d/%d  $%.3f so far" % (i, len(todo),
                      tin / 1e6 * IN_RATE + tout / 1e6 * OUT_RATE))

    CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n",
                     encoding="utf-8", newline="\n")
    cost = tin / 1e6 * IN_RATE + tout / 1e6 * OUT_RATE
    if not quiet:
        print("\n  %d extracted, %d had a quote rejected" % (len(cache), rejected))
        print("  %s in / %s out tokens   $%.3f"
              % (format(tin, ","), format(tout, ","), cost))
    return {"attempted": len(todo), "rejected": rejected, "cost": cost,
            "in": tin, "out": tout, "cached_total": len(cache)}


# --------------------------------------------------------------------------
# Compare
# --------------------------------------------------------------------------

def cmd_compare():
    import jdextract as J
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    if not cache:
        print("nothing extracted yet")
        return 1

    fields = ["experience_floor_years", "experience_negotiable", "seniority", "india_status"]
    agree = {f: [0, 0] for f in fields}
    examples = {f: [] for f in fields}
    newly = []

    for k, llm in sorted(cache.items()):
        p = TEXT_DIR / (k + ".txt")
        if not p.exists():
            continue
        jd = p.read_text(encoding="utf-8")
        rx = J.extract(jd, index.get(k, {}).get("title", ""))
        for f in fields:
            a, b = rx.get(f), llm.get(f)
            agree[f][1] += 1
            if a == b:
                agree[f][0] += 1
            elif len(examples[f]) < 6:
                examples[f].append((k, a, b, llm.get(
                    {"experience_floor_years": "experience_quote",
                     "experience_negotiable": "experience_negotiable_quote",
                     "seniority": "seniority_quote",
                     "india_status": "india_quote"}[f])))
        if llm.get("india_status") == "explicit_no" and rx.get("india_status") != "explicit_no":
            newly.append(k)

    print("regex vs %s on %d jobs with real posting text\n" % (MODEL, agree[fields[0]][1]))
    print("  %-26s %s" % ("field", "they agree"))
    for f in fields:
        ok, n = agree[f]
        print("  %-26s %4d/%-4d  %3d%%" % (f, ok, n, 100 * ok // n if n else 0))

    neg = sum(1 for v in cache.values() if v.get("experience_negotiable"))
    print("\n  postings the model says make years negotiable: %d" % neg)
    for k, v in sorted(cache.items()):
        if v.get("experience_negotiable"):
            print("    %-46s %s" % (index.get(k, {}).get("title", k)[:46],
                  re.sub(r"\s+", " ", str(v.get("experience_negotiable_quote")))[:88]))

    print("\n  postings the model rules out that the regex called 'unstated': %d"
          % len(newly))
    for f in fields:
        if not examples[f]:
            continue
        print("\n  %s - first disagreements" % f)
        for k, a, b, q in examples[f]:
            print("    %-44s regex=%-12r llm=%r" % (k[:44], a, b))
            if q:
                print("      %s" % re.sub(r"\s+", " ", str(q))[:120])
    return 0


def main(argv):
    if "--fetch" in argv:
        return cmd_fetch()
    if "--extract" in argv:
        lim = bud = eff = None
        mt = 1600
        if "--limit" in argv:
            lim = int(argv[argv.index("--limit") + 1])
        if "--budget" in argv:
            bud = int(argv[argv.index("--budget") + 1])
        if "--effort" in argv:
            eff = argv[argv.index("--effort") + 1]
        if "--max-tokens" in argv:
            mt = int(argv[argv.index("--max-tokens") + 1])
        cmd_extract(lim, bud, eff, mt)
        return 0
    if "--compare" in argv:
        return cmd_compare()
    print(__doc__.strip())
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main(sys.argv))
