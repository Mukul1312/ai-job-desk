#!/usr/bin/env python3
"""
board.py - ask a company's own board what it actually has open.

The feed shows you the postings somebody chose to syndicate. The board shows you
everything. On 2026-11-08 five boards were checked by hand and four changed the
answer the pipeline had given:

    company        pipeline   board   India-scoped
    GitLab                4     248            33
    ClickHouse            8     201             9
    NewRocket             8      48            12
    G-P                   4      63             8
    Sword Health          4      72             0   <- a definitive no, also useful

Thirty-three reachable GitLab roles existed for weeks and the pipeline showed
four, none of them India. No ranking fix finds those; only asking the board does.

Every endpoint here is unauthenticated and needs no key.

    python tools/board.py gitlab                 # one company
    python tools/board.py https://jobs.ashbyhq.com/livekit/abc123
    python tools/board.py --known                # every company we have a slug for
    python tools/board.py gitlab --all           # don't filter to engineering
    python tools/board.py gitlab --json

On the three buckets: a role is reported as INDIA only when a location names
India or an Indian city. "APAC", "Global" and "Remote" go to MAYBE, never to
INDIA. That asymmetry is deliberate and is the same one enrich.py documents for
EPFO - a hit is evidence, a miss proves nothing. A false India claim costs an
evening and teaches distrust of the whole layer; a miss costs one listing.
"""

import glob
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# `tools/queue.py` shadows the stdlib `queue` whenever a script in this folder is
# run directly, because Python puts the script's own directory first on the import
# path. concurrent.futures imports queue transitively, so ThreadPoolExecutor dies
# with "module 'queue' has no attribute 'SimpleQueue'".
#
# docs/TODO.md called this "harmless today". It stopped being harmless here, on
# the first tool in tools/ that wanted threads. Drop our own directory from the
# path before importing anything that needs the real one.
_HERE = Path(__file__).parent.resolve()
sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != _HERE]
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

WORKSPACE = _HERE.parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-desk/1.0)"}
TIMEOUT = 30


# --------------------------------------------------------------------------
# Slug resolution
#
# A board URL contains the slug. We never guess one: "clickhouse" is not
# ClickHouse's Greenhouse slug and "newrocket" is not NewRocket's Ashby slug,
# and both guesses return a 404 that looks exactly like "no open roles".
# --------------------------------------------------------------------------

URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?([a-z0-9_-]+)", re.I)),
    ("ashby",      re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)", re.I)),
    ("ashby",      re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([a-z0-9_.-]+)", re.I)),
    ("lever",      re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I)),
    ("personio",   re.compile(r"([a-z0-9_-]+)\.jobs\.personio\.(?:com|de)", re.I)),
    ("bamboohr",   re.compile(r"([a-z0-9_-]+)\.bamboohr\.com", re.I)),
    ("breezy",     re.compile(r"([a-z0-9_-]+)\.breezy\.hr", re.I)),
]


def slug_from_url(url):
    for platform, pat in URL_PATTERNS:
        m = pat.search(url or "")
        if m:
            return platform, m.group(1).lower()
    return None, None


def known_slugs():
    """Every board slug we have ever seen a URL for, from the raw archives.

    Reads data/raw/ rather than the normalised snapshot. The normalisation drops
    applyUrl on some sources, and the apply URL is where the slug lives.
    """
    found = {}
    files = glob.glob(str(WORKSPACE / "data/raw/*/*.json"))
    files += [str(WORKSPACE / "applications.json")]
    for f in files:
        try:
            raw = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = raw if isinstance(raw, list) else raw.get("jobs", raw.get("applications", []))
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            company = (r.get("company") or "").strip()
            for key in ("applyUrl", "apply_url", "url"):
                platform, slug = slug_from_url(r.get(key) or "")
                if slug:
                    found.setdefault((platform, slug), company or slug)
                    break
    return found


# --------------------------------------------------------------------------
# Adapters. Each returns a list of normalised dicts. Six platforms, six
# completely different shapes for "where is this job".
# --------------------------------------------------------------------------

def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _clean(seq):
    """Dedupe, drop blanks, keep order."""
    out, seen = [], set()
    for s in seq:
        s = re.sub(r"\s+", " ", str(s or "")).strip(" ,")
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def fetch_greenhouse(slug):
    d = _get("https://boards-api.greenhouse.io/v1/boards/%s/jobs" % slug)
    jobs = []
    for j in d.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        # Greenhouse packs multiple offices into one string with "||" or ";"
        parts = re.split(r"\|\||;|\sor\s", loc)
        jobs.append({"title": j.get("title", ""), "locations": _clean(parts),
                     "url": j.get("absolute_url", ""), "department": "",
                     "updated": (j.get("updated_at") or "")[:10]})
    return jobs


def fetch_ashby(slug):
    d = _get("https://api.ashbyhq.com/posting-api/job-board/%s" % slug)
    jobs = []
    for j in d.get("jobs", []):
        # secondaryLocations is the whole point. A role listed as "North America"
        # with APJ and EMEA secondaries is three roles' worth of reach, and
        # reading only `location` throws two of them away.
        locs = [j.get("location", "")]
        for s in j.get("secondaryLocations") or []:
            locs.append(s.get("location") if isinstance(s, dict) else s)
        jobs.append({"title": j.get("title", ""), "locations": _clean(locs),
                     "url": j.get("jobUrl") or j.get("applyUrl", ""),
                     "department": j.get("department") or j.get("team") or "",
                     "updated": (j.get("publishedAt") or "")[:10]})
    return jobs


def fetch_lever(slug):
    d = _get("https://api.lever.co/v0/postings/%s?mode=json" % slug)
    jobs = []
    for j in d:
        cat = j.get("categories") or {}
        locs = [cat.get("location")] + list(cat.get("allLocations") or [])
        locs.append(j.get("country"))
        jobs.append({"title": j.get("text", ""), "locations": _clean(locs),
                     "url": j.get("hostedUrl", ""), "department": cat.get("department") or "",
                     "updated": ""})
    return jobs


def fetch_personio(slug):
    d = _get("https://%s.jobs.personio.com/search.json" % slug)
    jobs = []
    for j in d:
        locs = [j.get("office")] + list(j.get("offices") or [])
        jobs.append({"title": j.get("name", ""), "locations": _clean(locs),
                     "url": "https://%s.jobs.personio.com/job/%s" % (slug, j.get("id")),
                     "department": j.get("department") or "", "updated": ""})
    return jobs


def fetch_bamboohr(slug):
    d = _get("https://%s.bamboohr.com/careers/list" % slug)
    jobs = []
    for j in d.get("result", []):
        ats = j.get("atsLocation") or {}
        loc = j.get("location") or {}
        locs = [ats.get("country"), ats.get("state"), ats.get("city"),
                loc.get("city"), loc.get("state")]
        if j.get("isRemote"):
            locs.append("Remote")
        jobs.append({"title": j.get("jobOpeningName", ""), "locations": _clean(locs),
                     "url": "https://%s.bamboohr.com/careers/%s" % (slug, j.get("id")),
                     "department": j.get("departmentLabel") or "", "updated": ""})
    return jobs


def fetch_breezy(slug):
    d = _get("https://%s.breezy.hr/json" % slug)
    jobs = []
    for j in d:
        locs = []
        for L in (j.get("locations") or [j.get("location")] or []):
            if not isinstance(L, dict):
                continue
            locs += [(L.get("country") or {}).get("name"), L.get("city")]
            if L.get("is_remote"):
                locs.append("Remote")
        jobs.append({"title": j.get("name", ""), "locations": _clean(locs),
                     "url": j.get("url", ""),
                     "department": j.get("department") or "", "updated": (j.get("published_date") or "")[:10]})
    return jobs


FETCHERS = {"greenhouse": fetch_greenhouse, "ashby": fetch_ashby, "lever": fetch_lever,
            "personio": fetch_personio, "bamboohr": fetch_bamboohr, "breezy": fetch_breezy}


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

INDIA = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|pune|mumbai|chennai|kolkata"
    r"|new delhi|delhi ncr|noida|gurgaon|gurugram|ahmedabad|jaipur|kanpur"
    r"|trivandrum|thiruvananthapuram|kochi|coimbatore|indore|chandigarh)\b", re.I)

# Could include India, does not say so. Never promoted to INDIA - see the module
# docstring on why the asymmetry runs this way. APAC and APJ stay here because
# India is genuinely inside them; EMEA and NAMER are not, so they live below.
MAYBE = re.compile(
    r"\b(apac|apj|asia[- ]pacific|asia|global|worldwide|anywhere"
    r"|work from anywhere|remote)\b", re.I)

# A concrete somewhere-else. Without this, "Remote, United States" lands in
# MAYBE on the strength of the word "remote" and buries the real maybes: GitLab
# alone produced 99 of them, nearly all US and EMEA roles.
NOT_INDIA = re.compile(
    r"\b(united states|usa|u\.s\.|canada|united kingdom|england|scotland|ireland"
    r"|germany|france|netherlands|belgium|poland|spain|portugal|italy|austria"
    r"|switzerland|sweden|norway|denmark|finland|czech|romania|bulgaria|greece"
    r"|australia|new zealand|japan|korea|china|singapore|malaysia|indonesia"
    r"|philippines|vietnam|thailand|taiwan|hong kong"
    r"|brazil|mexico|argentina|chile|colombia|costa rica|peru"
    r"|israel|turkey|egypt|nigeria|kenya|south africa|morocco"
    r"|emea|namer|north america|latam|south america|europe|united arab emirates"
    r"|\buae\b|saudi|qatar"
    # Bare "US" and US cities/state codes. Safe to add because INDIA is tested
    # first: a role listing both Bangalore and Austin is already an India hit.
    r"|\bus\b|\bu\.s\b"
    r"|san francisco|new york|los angeles|seattle|boston|chicago|austin|denver"
    r"|atlanta|miami|dallas|houston|phoenix|portland|san diego|san jose"
    r"|london|berlin|paris|amsterdam|dublin|madrid|barcelona|lisbon|munich"
    r"|toronto|vancouver|montreal|sydney|melbourne|tokyo|seoul|tel aviv"
    r"|\b(?:ca|ny|tx|fl|wa|ma|il|co|ga|nc|va|az|or|nj|pa|oh|mi|mn|md|dc|ut|nv)\b"
    r")\b", re.I)

# A board returns the whole company. Without this, GitLab prints 248 rows of
# which most are sales, support and field marketing.
ENGINEERING = re.compile(
    r"\b(engineer|engineering|developer|development|software|backend|back[- ]end"
    r"|frontend|front[- ]end|fullstack|full[- ]stack|architect|sre|devops"
    r"|platform|infrastructure|data|machine learning|\bml\b|\bai\b|llm"
    r"|scientist|technical|programmer|forward deployed|solutions? engineer)\b", re.I)


def _profile_excludes():
    # *-profile.json is gitignored and personal; config/profile.json is the
    # checked-in fallback. Globbed rather than named so this works for anyone.
    candidates = sorted(WORKSPACE.glob("*-profile.json")) + [WORKSPACE / "config/profile.json"]
    for p in candidates:
        if p.exists():
            try:
                return [k.lower() for k in
                        json.loads(p.read_text(encoding="utf-8")).get("exclude_title_keywords", [])]
            except Exception:
                pass
    return []


EXCLUDES = _profile_excludes()


def classify(job):
    blob = " | ".join(job["locations"])
    if INDIA.search(blob):
        return "india"
    # Order matters. A named country wins over the word "remote" sitting next to
    # it, so "Remote, Poland" is a no rather than a maybe.
    if NOT_INDIA.search(blob):
        return "no"
    if MAYBE.search(blob):
        return "maybe"
    return "no"


def is_engineering(job):
    t = (job["title"] or "").lower()
    if any(x and x in t for x in EXCLUDES):
        return False
    return bool(ENGINEERING.search(job["title"] + " " + (job["department"] or "")))


def survey(platform, slug):
    jobs = FETCHERS[platform](slug)
    for j in jobs:
        j["reach"] = classify(j)
        j["eng"] = is_engineering(j)
    return jobs


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def show(label, platform, slug, jobs, show_all=False):
    eng = [j for j in jobs if j["eng"]] if not show_all else jobs
    india = [j for j in eng if j["reach"] == "india"]
    maybe = [j for j in eng if j["reach"] == "maybe"]

    print("\n%s  ·  %s/%s" % (label, platform, slug))
    print("  %d open  ·  %d %s  ·  %d India-scoped  ·  %d maybe"
          % (len(jobs), len(eng), "total" if show_all else "engineering",
             len(india), len(maybe)))

    for bucket, rows in (("INDIA", india), ("MAYBE (location does not say)", maybe)):
        if not rows:
            continue
        print("\n  " + bucket)
        for j in rows[:40]:
            print("    %s" % j["title"][:58].ljust(59) + ", ".join(j["locations"])[:40])
            print("      %s" % j["url"])
        if len(rows) > 40:
            print("    ... and %d more" % (len(rows) - 40))
    if not india and not maybe:
        print("\n  nothing India-scoped. That is an answer too - it rules the company out")
        print("  rather than leaving it to be re-examined in three weeks.")


def cmd_known(show_all=False, workers=12):
    index = known_slugs()
    print("%d board slug(s) recoverable from the archives" % len(index))
    print("querying with %d workers - a 404 means a stale or wrong slug, not an"
          " empty board\n" % workers)

    def one(item):
        (platform, slug), company = item
        try:
            return company, platform, slug, survey(platform, slug), None
        except Exception as e:
            return company, platform, slug, None, type(e).__name__

    items = sorted(index.items(), key=lambda kv: kv[1].lower())
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, items))

    print("  %-26s %-11s %6s %6s %7s %7s" % ("company", "platform", "open", "eng", "india", "maybe"))
    print("  " + "-" * 68)
    totals = {"india": 0, "maybe": 0}
    hits, dead = [], 0
    for company, platform, slug, jobs, err in results:
        if err:
            dead += 1
            continue
        eng = [j for j in jobs if j["eng"]]
        india = [j for j in eng if j["reach"] == "india"]
        maybe = [j for j in eng if j["reach"] == "maybe"]
        totals["india"] += len(india)
        totals["maybe"] += len(maybe)
        if not jobs:
            continue
        flag = "  <--" if india else ""
        print("  %-26s %-11s %6d %6d %7d %7d%s"
              % ((company or slug)[:26], platform, len(jobs), len(eng), len(india), len(maybe), flag))
        if india:
            hits.append((company or slug, platform, slug, india))
    if dead:
        print("\n  %d slug(s) did not answer. Those are worth re-resolving from a"
              " fresh apply URL." % dead)
    print("\n  %d India-scoped engineering role(s) across %d company board(s)."
          % (totals["india"], len(hits)))
    print("  %d more whose location does not say either way." % totals["maybe"])
    for company, platform, slug, india in hits:
        print("\n  %s" % company)
        for j in india[:12]:
            print("    %s  %s" % (j["title"][:54].ljust(55), ", ".join(j["locations"])[:34]))
            print("      %s" % j["url"])
    return 0


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    show_all = "--all" in argv
    as_json = "--json" in argv

    if "--known" in argv:
        return cmd_known(show_all)

    if not args:
        print(__doc__.strip())
        return 1

    target = args[0]
    if "://" in target or "." in target:
        platform, slug = slug_from_url(target)
        if not slug:
            print("could not read a board slug out of that URL")
            return 1
        label = slug
    else:
        index = known_slugs()
        match = [(p, s, c) for (p, s), c in index.items()
                 if target.lower() in c.lower() or target.lower() == s]
        if not match:
            print("no board slug on file for %r." % target)
            print("Pass a board URL instead and it will be read from that, or run --known")
            print("to see every company a slug is recoverable for.")
            return 1
        platform, slug, label = match[0]

    try:
        jobs = survey(platform, slug)
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        print("%s/%s unreachable: %s" % (platform, slug, e))
        print("A 404 here usually means the slug is wrong, not that the board is empty.")
        return 1

    if as_json:
        print(json.dumps(jobs, indent=2))
        return 0
    show(label, platform, slug, jobs, show_all)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main(sys.argv))
