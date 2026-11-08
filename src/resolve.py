#!/usr/bin/env python3
"""
resolve.py — work out where an application actually goes.

A listing URL is not an application URL. An HN "Who is hiring" comment lives on
news.ycombinator.com but asks you to email a person; a GetHired row links out to
the company's own board. Until that gap is closed, detect_apply_tier() only ever
sees the aggregator, returns "form" for every job, and the Greenhouse, Lever,
Ashby and Workday handlers in apply.py never run at all.

Closing it also answers a question the pipeline could not otherwise answer:
which applicant tracking systems are actually in the pipeline, and how many
roles are really just an email to a person. That distribution decides where
any apply automation is worth building, so it is recorded rather than guessed.

Two passes, cheapest first:

  1. text  — read apply links and addresses straight out of the listing body.
             Free, works offline, and enough for most HN and RemoteOK rows.
  2. fetch — load the listing page and follow the outbound apply link.
             Networked, rate limited, and cached permanently. Needed for
             aggregators like GetHired whose descriptions are auto-generated
             summaries with no link in them.

Nothing here submits anything. It only records where an application would go.
"""

import json, re, time, urllib.request, urllib.error
from pathlib import Path
from urllib.parse import urlparse, urljoin

WORKSPACE = Path(__file__).parent.parent
CACHE_FILE = WORKSPACE / "apply_destinations_cache.json"

# Be polite and identifiable without naming the user. A contact address does
# not belong in a User-Agent.
USER_AGENT = "Mozilla/5.0 (compatible; openclaw-remote-job-hunter/1.0)"

FETCH_TIMEOUT = 20
FETCH_DELAY = 0.6          # seconds between network fetches
DEFAULT_FETCH_BUDGET = 60  # per run; aggregators are the bulk of the work


# ─── Destination classification ──────────────────────────────────────────────

# Ordered: the first match wins, so put the specific before the generic.
ATS_PATTERNS = [
    ("greenhouse",     ("greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io")),
    ("lever",          ("lever.co", "jobs.lever.co", "hire.lever.co")),
    ("ashby",          ("ashbyhq.com", "jobs.ashbyhq.com")),
    ("workday",        ("myworkdayjobs.com", "workday.com", "wd1.", "wd3.", "wd5.")),
    ("smartrecruiters",("smartrecruiters.com",)),
    ("workable",       ("workable.com", "apply.workable.com")),
    ("taleo",          ("taleo.net", "taleo.com")),
    ("bamboohr",       ("bamboohr.com", "bamboohr.co.uk")),
    ("jazzhr",         ("applytojob.com", "jazzhr.com")),
    ("breezy",         ("breezy.hr",)),
    ("recruitee",      ("recruitee.com",)),
    ("teamtailor",     ("teamtailor.com",)),
    ("rippling",       ("rippling.com", "ats.rippling.com")),
    ("personio",       ("personio.de", "jobs.personio.com")),
    ("jobvite",        ("jobvite.com",)),
    ("icims",          ("icims.com",)),
    ("pinpoint",       ("pinpointhq.com",)),
    ("comeet",         ("comeet.com", "comeet.co")),
    ("dayforce",       ("dayforcehcm.com",)),
    ("manatal",        ("manatal.com",)),
    ("yc_waas",        ("ycombinator.com/companies", "workatastartup.com")),
    ("wellfound",      ("wellfound.com", "angel.co")),
    ("linkedin",       ("linkedin.com/jobs", "linkedin.com/easy-apply")),
    ("notion_form",    ("notion.site", "notion.so")),
    ("tally",          ("tally.so",)),
    ("typeform",       ("typeform.com",)),
    ("airtable",       ("airtable.com",)),
    ("fillout",        ("fillout.com",)),
    ("google_form",    ("docs.google.com/forms", "forms.gle")),
]

# Where a listing lives. Finding one of these is not a resolution — it is the
# thing we are trying to resolve away from.
AGGREGATOR_HOSTS = (
    "news.ycombinator.com", "gethired.devhub.co.in", "remoteok.com",
    "remoteok.io", "remotive.com", "workingnomads.com", "himalayas.app",
    "weworkremotely.com", "remoterocketship.com",
)

# Links that appear in listing bodies but never lead to an application form.
NOISE_HOSTS = (
    "twitter.com", "x.com", "github.com", "youtube.com", "youtu.be",
    "medium.com", "substack.com", "crunchbase.com", "glassdoor.com",
    "facebook.com", "instagram.com", "t.me", "discord.gg", "discord.com",
    "calendly.com", "news.ycombinator.com", "en.wikipedia.org",
    "google.com/maps", "goo.gl/maps", "apple.com", "play.google.com",
)

# Addresses that are placeholders, vendor noise, or the sender rather than a
# destination.
EMAIL_NOISE = re.compile(
    r"(?i)(example|yourname|your\.name|someone|user|test|noreply|no-reply|"
    r"donotreply|sentry\.io|wixpress|\.png|\.jpg|\.jpeg|\.gif|\.svg|\.webp)"
)

# "jobs@" beats "info@" when a post offers both.
EMAIL_PREFERRED = re.compile(
    r"(?i)^(jobs|job|careers|career|hiring|hire|recruiting|recruitment|"
    r"apply|applications|talent|people|hr|work|join|team)[\w.+-]*@"
)

URL_RE = re.compile(r"""(?i)\bhttps?://[^\s<>"'\)\]\},;]+""")
EMAIL_RE = re.compile(r"(?i)\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")

# "email us at", "apply here", "send your CV to" — a link or address sitting
# near one of these is far more likely to be the destination.
APPLY_CUE = re.compile(
    r"(?i)\b(apply|application|email|e-mail|send|write|reach|contact|"
    r"resume|cv|hiring|job post|job description|more info|details|"
    r"interested|get in touch|drop us|drop me)\b"
)


def classify(url):
    """Name the system behind an apply URL, or 'direct' / 'unknown'."""
    if not url:
        return "unknown"
    low = url.lower()
    if low.startswith("mailto:"):
        return "email"
    for kind, needles in ATS_PATTERNS:
        if any(n in low for n in needles):
            return kind
    host = urlparse(low).netloc
    if host and not any(a in host for a in AGGREGATOR_HOSTS):
        return "direct"          # the company's own site
    return "unknown"


def _is_noise(url):
    low = url.lower()
    return any(n in low for n in NOISE_HOSTS)


def _strip_trailing_punct(url):
    """Prose runs links into sentences: 'apply at https://x.co/jobs.' """
    return url.rstrip(".,;:!?)]}>\"'")


def _urls_in(text):
    return [_strip_trailing_punct(u) for u in URL_RE.findall(text or "")]


def _emails_in(text):
    out = []
    for e in EMAIL_RE.findall(text or ""):
        if EMAIL_NOISE.search(e):
            continue
        out.append(e)
    return out


def _best_email(emails):
    """Prefer a hiring address over a general one, else first seen."""
    if not emails:
        return None
    for e in emails:
        if EMAIL_PREFERRED.match(e):
            return e
    return emails[0]


def _rank_apply_urls(urls):
    """
    Sort candidate URLs by how likely each is to be an application page.

    A known ATS always wins. After that, a path that says 'careers' or 'apply'
    beats a bare homepage, which is usually just the company being mentioned.
    """
    scored = []
    for u in urls:
        if _is_noise(u):
            continue
        kind = classify(u)
        low = u.lower()
        if kind in ("unknown",):
            continue
        score = 0
        if kind not in ("direct", "email"):
            score += 100                      # a real ATS link
        if re.search(r"(?i)/(careers?|jobs?|apply|hiring|join|openings?|vacanc)", low):
            score += 20
        if re.search(r"(?i)(careers?|jobs|apply|hiring)\.", low):
            score += 15
        path = urlparse(low).path.strip("/")
        if not path:
            score -= 10                       # bare homepage
        scored.append((score, u, kind))
    scored.sort(key=lambda t: -t[0])
    return scored


# ─── Pass 0: the source already told us ──────────────────────────────────────

# Names a source may use for an applicant tracking system, mapped onto ours.
PLATFORM_ALIASES = {
    "greenhouse": "greenhouse", "lever": "lever", "ashby": "ashby",
    "ashbyhq": "ashby", "workday": "workday", "workable": "workable",
    "smartrecruiters": "smartrecruiters", "taleo": "taleo",
    "bamboohr": "bamboohr", "jazzhr": "jazzhr", "breezy": "breezy",
    "breezyhr": "breezy", "recruitee": "recruitee", "teamtailor": "teamtailor",
    "rippling": "rippling", "personio": "personio", "jobvite": "jobvite",
    "icims": "icims", "pinpoint": "pinpoint", "comeet": "comeet",
    "dayforce": "dayforce", "manatal": "manatal", "wellfound": "wellfound",
    "ycombinator": "yc_waas", "workatastartup": "yc_waas",
    "linkedin": "linkedin", "notion": "notion_form", "tally": "tally",
    "typeform": "typeform", "airtable": "airtable", "fillout": "fillout",
    "email": "email",
}


def from_declared(job):
    """
    Use a platform the source named outright.

    GetHired's API returns a `platform` field on every row. That is a better
    answer than anything we could infer, and it costs nothing — its job pages
    are a Next.js shell whose markup carries no apply link at all, so a fetch
    would burn a request and still come back empty.
    """
    declared = (job.get("ats_platform") or "").strip().lower()
    if not declared:
        return None
    kind = PLATFORM_ALIASES.get(declared)
    if not kind:
        # An unrecognised name is still a real signal — keep it rather than
        # discarding it, so new platforms show up in the summary by name.
        kind = re.sub(r"[^a-z0-9_]+", "_", declared) or None
    if not kind:
        return None
    return {"apply_url": job.get("url") or "", "apply_kind": kind,
            "apply_via": "declared"}


# ─── Pass 1: read the listing body ───────────────────────────────────────────

def from_text(job):
    """
    Resolve from the listing text alone. Free and offline-safe.

    Returns a dict, or None when the body names no destination.
    """
    text = " ".join(str(job.get(k) or "") for k in ("description", "snippet", "text"))
    if not text.strip():
        return None

    ranked = _rank_apply_urls(_urls_in(text))
    if ranked and ranked[0][0] >= 100:
        score, url, kind = ranked[0]
        return {"apply_url": url, "apply_kind": kind, "apply_via": "text"}

    email = _best_email(_emails_in(text))
    if email:
        return {"apply_url": f"mailto:{email}", "apply_kind": "email",
                "apply_via": "text"}

    if ranked:                                # careers page, no ATS named
        score, url, kind = ranked[0]
        if score > 0:
            return {"apply_url": url, "apply_kind": kind, "apply_via": "text"}

    return None


# ─── Pass 2: follow the listing ──────────────────────────────────────────────

def _fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "text" not in ctype:
            return None, r.geturl()
        raw = r.read(600_000)                 # listings are small; cap the read
        charset = r.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), r.geturl()


ANCHOR_RE = re.compile(r"""(?is)<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""")
TAG_RE = re.compile(r"(?s)<[^>]+>")


def from_fetch(url):
    """
    Load the listing page and find where its apply button points.

    Looks at anchors first — an aggregator's "Apply" link is an anchor, and its
    visible text usually says so — then falls back to any ATS URL on the page.
    """
    try:
        html_text, final_url = _fetch(url)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        return {"apply_error": f"{type(e).__name__}: {e}"[:160]}
    except Exception as e:                    # noqa: BLE001 - never kill a run
        return {"apply_error": f"{type(e).__name__}: {e}"[:160]}

    if not html_text:
        # Not HTML. If the listing redirected somewhere known, that is the answer.
        kind = classify(final_url)
        if kind not in ("unknown",) and final_url != url:
            return {"apply_url": final_url, "apply_kind": kind, "apply_via": "redirect"}
        return {"apply_error": "non-html response"}

    # A redirect straight onto an ATS settles it without reading the body.
    kind = classify(final_url)
    if kind not in ("unknown", "direct") and final_url != url:
        return {"apply_url": final_url, "apply_kind": kind, "apply_via": "redirect"}

    candidates = []
    for href, label in ANCHOR_RE.findall(html_text):
        if href.startswith(("#", "javascript:")):
            continue
        absolute = urljoin(final_url, href)
        if absolute.lower().startswith("mailto:"):
            addr = absolute[7:].split("?")[0]
            if not EMAIL_NOISE.search(addr):
                candidates.append((absolute, TAG_RE.sub(" ", label)))
            continue
        if not absolute.lower().startswith("http"):
            continue
        candidates.append((absolute, TAG_RE.sub(" ", label)))

    # Anchors whose visible text invites an application come first.
    cued = [u for u, label in candidates if APPLY_CUE.search(label or "")]
    ranked = _rank_apply_urls(cued)
    if ranked and ranked[0][0] >= 100:
        return {"apply_url": ranked[0][1], "apply_kind": ranked[0][2],
                "apply_via": "fetch:anchor"}

    ranked_all = _rank_apply_urls([u for u, _ in candidates])
    if ranked_all and ranked_all[0][0] >= 100:
        return {"apply_url": ranked_all[0][1], "apply_kind": ranked_all[0][2],
                "apply_via": "fetch:link"}

    # No ATS anywhere on the page — take a hiring address if one is offered.
    body = TAG_RE.sub(" ", html_text)
    email = _best_email(_emails_in(body))
    if email:
        return {"apply_url": f"mailto:{email}", "apply_kind": "email",
                "apply_via": "fetch:email"}

    if ranked:
        return {"apply_url": ranked[0][1], "apply_kind": ranked[0][2],
                "apply_via": "fetch:anchor"}
    if ranked_all:
        return {"apply_url": ranked_all[0][1], "apply_kind": ranked_all[0][2],
                "apply_via": "fetch:link"}
    return {"apply_error": "no apply link found on page"}


# ─── Cache ───────────────────────────────────────────────────────────────────

def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as e:                # noqa: BLE001
            print(f"  resolve: unreadable cache, starting fresh ({e})")
    return {}


def save_cache(cache):
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    except Exception as e:                    # noqa: BLE001
        print(f"  resolve: could not write cache: {e}")


# ─── Orchestration ───────────────────────────────────────────────────────────

def resolve_one(job, cache, allow_fetch=True, budget=None):
    """
    Attach apply_url / apply_kind / apply_via to one job.

    budget is a one-element list acting as a mutable counter, so a run cannot
    spend more network calls than it was given.
    """
    listing_url = job.get("url") or ""

    cached = cache.get(listing_url)
    if cached:
        job.update(cached)
        job["apply_cached"] = True
        return job

    found = from_declared(job) or from_text(job)

    # Did we actually get to try the expensive pass? If not, a miss is "not yet
    # attempted", not "no destination" — caching it would make a --no-fetch run
    # or an exhausted budget permanently poison every later run.
    fetch_attempted = False
    if not found and allow_fetch and listing_url.lower().startswith("http"):
        if budget is None or budget[0] > 0:
            if budget is not None:
                budget[0] -= 1
            fetch_attempted = True
            found = from_fetch(listing_url)
            time.sleep(FETCH_DELAY)

    if not found:
        found = {"apply_kind": "unresolved"}

    # A resolution that lands back on the aggregator has resolved nothing —
    # unless the source itself named the platform, in which case the aggregator
    # URL is just where we start from and the kind is already known.
    if found.get("apply_url") and found.get("apply_via") != "declared":
        host = urlparse(found["apply_url"].lower()).netloc
        if host and any(a in host for a in AGGREGATOR_HOSTS):
            found = {"apply_kind": "unresolved",
                     "apply_error": "resolved to the aggregator itself"}

    found.setdefault("apply_kind", "unresolved")

    # Cache a real outcome: a destination we found, or a fetch that genuinely
    # came back empty. An untried job stays uncached so the next run retries it.
    if found.get("apply_kind") != "unresolved" or fetch_attempted:
        cache[listing_url] = found

    job.update(found)
    job["apply_cached"] = False
    return job


def resolve_all(jobs, allow_fetch=True, fetch_budget=DEFAULT_FETCH_BUDGET,
                verbose=True):
    """
    Resolve a list of jobs in place. Returns a {kind: count} summary.

    Text resolution runs for everything. Fetching is budgeted and spent on the
    jobs text could not resolve, highest ranked first — those are the ones most
    likely to be applied to.
    """
    cache = load_cache()
    budget = [fetch_budget if allow_fetch else 0]
    before_budget = budget[0]

    # Spend the network budget where it matters: best jobs first.
    order = sorted(
        range(len(jobs)),
        key=lambda i: -(jobs[i].get("final_score") or jobs[i].get("score") or 0),
    )
    for i in order:
        resolve_one(jobs[i], cache, allow_fetch=allow_fetch, budget=budget)

    save_cache(cache)

    summary = {}
    for j in jobs:
        summary[j.get("apply_kind", "unresolved")] = \
            summary.get(j.get("apply_kind", "unresolved"), 0) + 1

    if verbose:
        spent = before_budget - budget[0]
        resolved = sum(v for k, v in summary.items() if k != "unresolved")
        print(f"  Apply destinations: {resolved}/{len(jobs)} resolved "
              f"({spent} page fetches)")
        for kind, n in sorted(summary.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>4}  {kind}")

    return summary


if __name__ == "__main__":
    import sys
    # Resolve a stored snapshot without touching the main run.
    #   python src/resolve.py data/raw/2026-11-07
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not src or not src.exists():
        print("usage: python src/resolve.py <snapshot-dir> [--no-fetch] [--budget N]")
        raise SystemExit(2)
    allow = "--no-fetch" not in sys.argv
    budget = DEFAULT_FETCH_BUDGET
    if "--budget" in sys.argv:
        budget = int(sys.argv[sys.argv.index("--budget") + 1])
    all_jobs = []
    for path in sorted(src.glob("*.json")):
        if path.name.endswith(".raw.json"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        recs = data.get("jobs", data) if isinstance(data, dict) else data
        for r in recs:
            r["_source_file"] = path.stem
        all_jobs.extend(recs)
    print(f"Resolving {len(all_jobs)} listings from {src} "
          f"(fetch={'on' if allow else 'off'}, budget={budget})")
    resolve_all(all_jobs, allow_fetch=allow, fetch_budget=budget)
