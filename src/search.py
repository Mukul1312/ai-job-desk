#!/usr/bin/env python3
"""
search.py — Multi-platform job fetcher.
Loads platform config from config/platforms.json.
v1.3.0: Added RealWFA, WorkingNomads, RemoteAI, Remote100K
"""
import json, urllib.request, urllib.parse, urllib.error, html, re
from pathlib import Path
try:
    import xml.etree.ElementTree as ET
except:
    pass

CONFIG_DIR = Path(__file__).parent.parent / "config"

def load_platforms():
    with open(CONFIG_DIR / "platforms.json") as f:
        return json.load(f)

def safe_str(val, default=""):
    if val is None:
        return default
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return str(val)


HARD_EXCLUDE_PHRASES = [
    "must be located in", "must reside in", "us only", "usa only",
    "united states only", "requires us work authorization",
    "must be authorized to work in the us", "us citizen",
    "us permanent resident", "requires security clearance",
    "us security clearance", "must be in", "onsite", "on-site", "on site",
    "new york only", "san francisco only", "seattle only",
    "california only", "texas only"
]

# Locations that explicitly indicate Work From Anywhere
WFA_INDICATORS = [
    "worldwide", "anywhere", "global", "work from anywhere", "wfa",
    "international", "no location restriction", "remote worldwide",
    "fully remote", "100% remote"
]

# Country/region-restricted patterns that are NOT WFA (for WFA-strict mode)
REGION_RESTRICTED = [
    "australia", "new zealand", "anz", "united kingdom", "uk only",
    "europe only", "eu only", "germany", "france", "spain", "ireland",
    "canada only", "brazil", "india only", "uae only", "dubai only",
    "philippines", "poland", "czech", "austria", "netherlands"
]

def is_location_ok(location, wfa_strict=False):
    if not location:
        return True
    loc = location.lower().strip()
    if any(phrase in loc for phrase in HARD_EXCLUDE_PHRASES):
        return False
    if wfa_strict:
        if any(ind in loc for ind in WFA_INDICATORS):
            return True
        if any(r in loc for r in REGION_RESTRICTED):
            return False
    return True

def is_wfa(location):
    """Returns True if the job is explicitly WFA/Worldwide."""
    if not location or location.strip() == "":
        return True
    loc = location.lower().strip()
    return any(ind in loc for ind in WFA_INDICATORS)

def fetch_remotive(config, domain):
    category = config["category_map"].get(domain, domain)
    url = f"{config['base_url']}?category={category}&limit=100"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            jobs = data.get("jobs", [])
            print(f"  Remotive [{category}]: {len(jobs)} jobs")
            return [{
                "title": html.unescape(safe_str(job.get("title", ""))),
                "company": html.unescape(safe_str(job.get("company_name", ""))),
                "salary": safe_str(job.get("salary", "")) or "Not listed",
                "url": safe_str(job.get("url", "")),
                "location": safe_str(job.get("candidate_required_location", "Worldwide")),
                "posted": safe_str(job.get("publication_date", ""))[:10],
                "tags": ", ".join(job.get("tags", [])[:5]),
                "description": safe_str(job.get("description", "")),
                "source": "Remotive"
            } for job in jobs]
    except Exception as e:
        print(f"  Remotive error: {e}")
        return []

def fetch_jobicy(config, domain):
    tags = config["tag_map"].get(domain, [])
    results = []
    seen_ids = set()
    for tag in tags:
        url = f"{config['base_url']}?count=20&tag={tag}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                jobs = data.get("jobs", [])
                print(f"  Jobicy [{tag}]: {len(jobs)} jobs")
                for job in jobs:
                    jid = str(job.get("id", ""))
                    if jid in seen_ids:
                        continue
                    seen_ids.add(jid)
                    job_loc = html.unescape(safe_str(job.get("jobGeo", "")))
                    if not is_location_ok(job_loc, wfa_strict=True):
                        continue
                    results.append({
                        "title": html.unescape(safe_str(job.get("jobTitle", ""))),
                        "company": html.unescape(safe_str(job.get("companyName", ""))),
                        "salary": "Not listed",
                        "url": safe_str(job.get("url", "")),
                        "location": safe_str(job.get("jobGeo", "Worldwide")),
                        "posted": safe_str(job.get("pubDate", ""))[:10],
                        "tags": safe_str(job.get("jobIndustry", "")),
                        "description": html.unescape(safe_str(job.get("jobDescription", ""))),
                        "source": "Jobicy"
                    })
        except Exception as e:
            print(f"  Jobicy [{tag}] error: {e}")
    return results

def fetch_remoteok(config, phrases):
    url = config["base_url"]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            jobs = [j for j in data if isinstance(j, dict) and j.get("position")]
            print(f"  RemoteOK: {len(jobs)} total jobs")
            results = []
            for job in jobs:
                position = html.unescape(safe_str(job.get("position", "")))
                tags = " ".join(job.get("tags", []))
                if not any(p in (position + " " + tags).lower() for p in phrases):
                    continue
                sal_min = job.get("salary_min") or 0
                sal_max = job.get("salary_max") or 0
                try:
                    sal_min = int(sal_min) if sal_min else 0
                    sal_max = int(sal_max) if sal_max else 0
                except:
                    sal_min = sal_max = 0
                salary_str = f"${sal_min//1000}K-${sal_max//1000}K USD" if sal_min and sal_max else \
                             f"${sal_min//1000}K+ USD" if sal_min else "Not listed"
                job_loc = safe_str(job.get("location", ""))
                if not is_location_ok(job_loc):
                    continue
                results.append({
                    "title": position,
                    "company": html.unescape(safe_str(job.get("company", ""))),
                    "salary": salary_str,
                    "url": safe_str(job.get("apply_url") or job.get("url", "")),
                    "location": safe_str(job.get("location", "Worldwide")),
                    "posted": "",
                    "tags": ", ".join(job.get("tags", [])[:5]),
                    "description": safe_str(job.get("description", "")),
                    "source": "RemoteOK"
                })
            print(f"  RemoteOK: {len(results)} relevant matches")
            return results
    except Exception as e:
        print(f"  RemoteOK error: {e}")
        return []

def fetch_wwr(config, domain, phrases):
    import re
    feeds = config["feeds"].get(domain, [])
    results = []
    for feed_url in feeds:
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                content = r.read().decode("utf-8")
            root = ET.fromstring(content)
            items = root.findall(".//item")
            matched = 0
            for item in items:
                title_el = item.find("title")
                link_el = item.find("link")
                region_el = item.find("region")
                pubdate_el = item.find("pubDate")
                desc_el = item.find("description")
                if title_el is None:
                    continue
                raw_title = html.unescape(safe_str(title_el.text, ""))
                if ": " in raw_title:
                    company, job_title = raw_title.split(": ", 1)
                else:
                    company, job_title = "", raw_title
                if not any(p in (job_title + " " + company).lower() for p in phrases):
                    continue
                desc = re.sub(r'<[^>]+>', ' ', safe_str(desc_el.text if desc_el is not None else ""))
                results.append({
                    "title": job_title.strip(),
                    "company": company.strip(),
                    "salary": "Not listed",
                    "url": safe_str(link_el.text if link_el is not None else ""),
                    "location": safe_str(region_el.text if region_el is not None else "Worldwide"),
                    "posted": safe_str(pubdate_el.text if pubdate_el is not None else "")[:16],
                    "tags": "",
                    "description": desc,
                    "source": "WeWorkRemotely"
                })
                matched += 1
            feed_name = feed_url.split('/')[-1].replace('.rss', '')
            print(f"  WWR [{feed_name}]: {matched} matches from {len(items)} items")
        except Exception as e:
            print(f"  WWR error [{feed_url}]: {e}")
    return results

def fetch_himalayas(config, phrases):
    """Fetch Himalayas jobs across multiple pages (API caps at 20/page)."""
    base_url = config["base_url"]
    pages = config.get("pages", 5)
    page_size = 20
    results = []
    total_fetched = 0
    for page in range(pages):
        offset = page * page_size
        url = f"{base_url}?limit={page_size}&offset={offset}"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Accept": "application/json"
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                jobs = data.get("jobs", [])
                if not jobs:
                    break
                total_fetched += len(jobs)
                for job in jobs:
                    title = html.unescape(safe_str(job.get("title", "")))
                    if not any(p in title.lower() for p in phrases):
                        continue
                    loc_list = job.get("locationRestrictions", [])
                    location = safe_str(loc_list) if loc_list else "Worldwide"
                    if loc_list and not is_wfa(location):
                        continue
                    sal_min = job.get("minSalary") or 0
                    sal_max = job.get("maxSalary") or 0
                    try:
                        sal_min = int(sal_min) if sal_min else 0
                        sal_max = int(sal_max) if sal_max else 0
                    except:
                        sal_min = sal_max = 0
                    salary_str = f"${sal_min//1000}K-${sal_max//1000}K" if sal_min and sal_max else \
                                 f"${sal_min//1000}K+" if sal_min else "Not listed"
                    results.append({
                        "title": title,
                        "company": html.unescape(safe_str(job.get("companyName", ""))),
                        "salary": salary_str,
                        "url": safe_str(job.get("applicationLink") or job.get("guid", "")),
                        "location": location,
                        "posted": safe_str(job.get("pubDate", ""))[:10],
                        "tags": "",
                        "description": html.unescape(safe_str(job.get("description", ""))),
                        "source": "Himalayas"
                    })
        except Exception as e:
            print(f"  Himalayas [page {page}] error: {e}")
            break
    print(f"  Himalayas: {len(results)} matches from {total_fetched} jobs ({pages} pages)")
    return results


# ── v1.3.0: New platform fetchers ─────────────────────────────────────────────

def fetch_realworkfromanywhere(config, domain, phrases):
    """
    realworkfromanywhere.com — hand-curated, every listing verified WFA.
    Uses category-specific RSS feeds (rss.xml).
    """
    import re
    feeds = config.get("feeds", {}).get(domain, [])
    results = []
    seen_urls = set()
    for feed_url in feeds:
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                content = r.read().decode("utf-8", errors="replace")
            root = ET.fromstring(content)
            items = root.findall(".//item")
            matched = 0
            for item in items:
                title_el   = item.find("title")
                link_el    = item.find("link")
                desc_el    = item.find("description")
                pubdate_el = item.find("pubDate")
                if title_el is None:
                    continue
                title    = html.unescape(safe_str(title_el.text, ""))
                link     = safe_str(link_el.text if link_el is not None else "")
                if link in seen_urls:
                    continue
                seen_urls.add(link)
                desc_raw = safe_str(desc_el.text if desc_el is not None else "")
                desc     = re.sub(r'<[^>]+>', ' ', desc_raw)
                # Extract company from "Title at Company" pattern in title
                company = "See listing"
                if " at " in title:
                    parts = title.rsplit(" at ", 1)
                    title   = parts[0].strip()
                    company = parts[1].strip()
                results.append({
                    "title":       html.unescape(title),
                    "company":     html.unescape(company),
                    "salary":      "Not listed",
                    "url":         link,
                    "location":    "Worldwide (WFA)",
                    "posted":      safe_str(pubdate_el.text if pubdate_el is not None else "")[:16],
                    "tags":        "",
                    "description": desc,
                    "source":      "RealWFA"
                })
                matched += 1
            feed_name = feed_url.split("/")[-2]
            print(f"  RealWFA [{feed_name}]: {matched} jobs from {len(items)} items")
        except Exception as e:
            pass  # XML errors suppressed (malformed feed)
    return results


def fetch_workingnomads(config, domain, phrases):
    """
    workingnomads.com — JSON API at /api/exposed_jobs/.
    Filters client-side by title/description against domain phrases.
    """
    import re
    url = config.get("api_url", "https://www.workingnomads.com/api/exposed_jobs/")
    tags = config.get("category_tags", {}).get(domain, [])
    if not tags:
        tags = phrases
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            jobs = json.loads(r.read())
        results = []
        for job in jobs:
            title   = html.unescape(safe_str(job.get("title", "")))
            company = html.unescape(safe_str(job.get("company_name", "") or job.get("company", "")))
            desc    = re.sub(r'<[^>]+>', ' ', safe_str(job.get("description", "")))
            combined = (title + " " + desc).lower()
            if not any(t in combined for t in tags):
                continue
            # Skip if location-restricted
            loc = safe_str(job.get("location", "Worldwide"))
            if not is_location_ok(loc, wfa_strict=True):
                continue
            results.append({
                "title":       title,
                "company":     company,
                "salary":      "Not listed",
                "url":         safe_str(job.get("url", "")),
                "location":    "Worldwide",
                "posted":      safe_str(job.get("pub_date", "") or job.get("created_at", ""))[:10],
                "tags":        safe_str(job.get("tags", "")),
                "description": desc,
                "source":      "WorkingNomads"
            })
        print(f"  WorkingNomads: {len(results)} matches from {len(jobs)} jobs")
        return results
    except Exception as e:
        print(f"  WorkingNomads error: {e}")
        return []


def fetch_remoteai(config, phrases):
    """
    remoteai.io — dedicated AI/ML job board RSS.
    Every listing is AI/ML — no category filtering needed.
    """
    import re
    url = config.get("base_url", "https://remoteai.io/rss")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            content = r.read().decode("utf-8", errors="replace")
        root = ET.fromstring(content)
        items = root.findall(".//item")
        results = []
        for item in items:
            title_el   = item.find("title")
            link_el    = item.find("link")
            desc_el    = item.find("description")
            pubdate_el = item.find("pubDate")
            if title_el is None:
                continue
            title    = html.unescape(safe_str(title_el.text, ""))
            desc_raw = safe_str(desc_el.text if desc_el is not None else "")
            desc     = re.sub(r'<[^>]+>', ' ', desc_raw)
            if not any(p in (title + " " + desc).lower() for p in phrases):
                continue
            if any(p in desc.lower() for p in HARD_EXCLUDE_PHRASES):
                continue
            company = "See listing"
            for sep in [" — ", " - ", " at ", " @ "]:
                if sep in title:
                    parts   = title.split(sep, 1)
                    title   = parts[0].strip()
                    company = parts[1].strip()
                    break
            results.append({
                "title":       html.unescape(title),
                "company":     html.unescape(company),
                "salary":      "Not listed",
                "url":         safe_str(link_el.text if link_el is not None else ""),
                "location":    "Worldwide",
                "posted":      safe_str(pubdate_el.text if pubdate_el is not None else "")[:16],
                "tags":        "ai, ml",
                "description": desc,
                "source":      "RemoteAI"
            })
        print(f"  RemoteAI: {len(results)} matches from {len(items)} items")
        return results
    except Exception as e:
        print(f"  RemoteAI error: {e}")
        return []


def fetch_remote100k(config, phrases):
    """
    remote100k.com — manually vetted $100K+ remote roles only.
    Strong signal — low noise, high quality listings.
    """
    import re
    url = config.get("base_url", "https://remote100k.com/rss")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            content = r.read().decode("utf-8", errors="replace")
        root = ET.fromstring(content)
        items = root.findall(".//item")
        results = []
        for item in items:
            title_el   = item.find("title")
            link_el    = item.find("link")
            desc_el    = item.find("description")
            pubdate_el = item.find("pubDate")
            if title_el is None:
                continue
            title    = html.unescape(safe_str(title_el.text, ""))
            desc_raw = safe_str(desc_el.text if desc_el is not None else "")
            desc     = re.sub(r'<[^>]+>', ' ', desc_raw)
            combined = (title + " " + desc).lower()
            if not any(p in combined for p in phrases):
                continue
            if any(p in combined for p in HARD_EXCLUDE_PHRASES):
                continue
            company = "See listing"
            for sep in [" — ", " - ", " at ", " @ "]:
                if sep in title:
                    parts   = title.split(sep, 1)
                    title   = parts[0].strip()
                    company = parts[1].strip()
                    break
            results.append({
                "title":       html.unescape(title),
                "company":     html.unescape(company),
                "salary":      "$100K+",
                "url":         safe_str(link_el.text if link_el is not None else ""),
                "location":    "Worldwide",
                "posted":      safe_str(pubdate_el.text if pubdate_el is not None else "")[:16],
                "tags":        "",
                "description": desc,
                "source":      "Remote100K"
            })
        print(f"  Remote100K: {len(results)} matches from {len(items)} items")
        return results
    except Exception as e:
        print(f"  Remote100K error: {e}")
        return []


def fetch_indeed_eu(phrases, regions=None):
    """
    Indeed Europe RSS feeds for Netherlands and Finland QA jobs.
    indeed.com provides RSS for job searches.
    """
    import re, urllib.parse
    if regions is None:
        regions = [
            ("netherlands", "nl", "Netherlands"),
            ("finland",     "fi", "Finland"),
        ]
    results = []
    # Core QA search terms for Indeed
    search_terms = ["qa automation engineer", "test automation engineer", "sdet", "tosca automation"]
    for term in search_terms[:2]:  # limit to avoid rate limiting
        for country_key, country_code, country_name in regions:
            encoded = urllib.parse.quote(term)
            url = f"https://www.indeed.com/rss?q={encoded}&l={country_name}&radius=100&sort=date"
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                with urllib.request.urlopen(req, timeout=15) as r:
                    content = r.read().decode("utf-8", errors="replace")
                root = ET.fromstring(content)
                items = root.findall(".//item")
                for item in items:
                    title_el   = item.find("title")
                    link_el    = item.find("link")
                    desc_el    = item.find("description")
                    pubdate_el = item.find("pubDate")
                    if title_el is None:
                        continue
                    title    = html.unescape(safe_str(title_el.text, ""))
                    desc_raw = safe_str(desc_el.text if desc_el is not None else "")
                    desc     = re.sub(r'<[^>]+>', ' ', desc_raw)
                    combined = (title + " " + desc).lower()
                    if not any(p in combined for p in phrases):
                        continue
                    # Require visa sponsorship signal
                    visa_signals = ["visa", "sponsor", "relocation", "work permit", "permit"]
                    if not any(v in combined for v in visa_signals):
                        continue
                    results.append({
                        "title":       html.unescape(title),
                        "company":     "See listing",
                        "salary":      "Not listed",
                        "url":         safe_str(link_el.text if link_el is not None else ""),
                        "location":    country_name,
                        "posted":      safe_str(pubdate_el.text if pubdate_el is not None else "")[:16],
                        "tags":        "visa sponsorship",
                        "description": desc,
                        "source":      f"Indeed [{country_name}]",
                        "eu_sponsored": True
                    })
            except Exception as e:
                print(f"  Indeed EU [{country_name}] error: {e}")
    print(f"  Indeed EU: {len(results)} visa-sponsored matches")
    return results


def fetch_arbeitnow_eu(phrases, regions=None):
    """Arbeitnow free API - Europe jobs with visa sponsorship"""
    import urllib.request, json, html
    candidates = []
    try:
        all_raw = []
        for page in range(1, 6):
            url = f"https://www.arbeitnow.com/api/job-board-api?visa_sponsorship=true&page={page}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
            page_jobs = data.get("data", [])
            if not page_jobs:
                break
            all_raw += page_jobs
        jobs = all_raw
        print(f"  Arbeitnow EU: {len(jobs)} total visa-sponsored jobs (5 pages)")
        phrase_lower = [p.lower() for p in phrases]
        for job in jobs:
            title = html.unescape(str(job.get("title", "")))
            desc  = html.unescape(str(job.get("description", "")))
            tags  = " ".join(job.get("tags", []))
            # Title+tags only — avoids false matches on "automation" in DevOps JDs
            text  = f"{title} {tags}".lower()
            if any(p in text for p in phrase_lower):
                candidates.append({
                    "title":    title,
                    "company":  html.unescape(str(job.get("company_name", ""))),
                    "salary":   "Not listed",
                    "url":      str(job.get("url", "")),
                    "location": str(job.get("location", "Europe (Visa Sponsored)")),
                    "posted":   str(job.get("created_at", ""))[:10],
                    "tags":     tags,
                    "description": desc[:500],
                    "source":   "Arbeitnow [EU Visa]",
                    "eu_sponsored": True
                })
        print(f"  Arbeitnow EU: {len(candidates)} phrase matches")
    except Exception as e:
        print(f"  Arbeitnow EU error: {e}")
    return candidates


def fetch_eu_visa_sponsored(profile_config, phrases):
    """
    EU Visa Sponsored fallback — Netherlands/Finland focus.
    Searches Remotive, Jobicy, WorkingNomads, Indeed EU, Relocate.me, EuropeRemoteJobs.
    Only triggered when WFA matches are below threshold.
    """
    import re
    eu_config = profile_config.get("eu_fallback", {})
    if not eu_config.get("enabled"):
        return []

    allowed_regions = eu_config.get("allowed_regions", [
        "netherlands", "finland", "amsterdam", "helsinki", "nl", "fi"
    ])

    platforms = load_platforms()
    candidates = []

    # ── Remotive ──────────────────────────────────────────────────────────
    try:
        remotive_cfg = platforms.get("remotive", {})
        domain = profile_config.get("domain", "")
        category = remotive_cfg.get("category_map", {}).get(domain, domain)
        url = f"{remotive_cfg['base_url']}?category={category}&limit=100"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            for job in data.get("jobs", []):
                loc = safe_str(job.get("candidate_required_location", "")).lower()
                if not any(r in loc for r in allowed_regions):
                    continue
                desc  = safe_str(job.get("description", "")).lower()
                title = safe_str(job.get("title", "")).lower()
                if not any(p in title + " " + desc for p in phrases):
                    continue
                candidates.append({
                    "title":   html.unescape(safe_str(job.get("title", ""))),
                    "company": html.unescape(safe_str(job.get("company_name", ""))),
                    "salary":  safe_str(job.get("salary", "")) or "Not listed",
                    "url":     safe_str(job.get("url", "")),
                    "location": safe_str(job.get("candidate_required_location", "")),
                    "posted":  safe_str(job.get("publication_date", ""))[:10],
                    "tags":    ", ".join(job.get("tags", [])[:5]),
                    "description": safe_str(job.get("description", "")),
                    "source":  "Remotive [EU]",
                    "eu_sponsored": True
                })
    except Exception as e:
        print(f"  EU Remotive error: {e}")

    # ── Jobicy ────────────────────────────────────────────────────────────
    try:
        jobicy_cfg = platforms.get("jobicy", {})
        tags = jobicy_cfg.get("tag_map", {}).get(profile_config.get("domain", ""), [])
        seen_ids = set()
        for tag in tags[:3]:
            url = f"{jobicy_cfg['base_url']}?count=20&tag={tag}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                for job in data.get("jobs", []):
                    jid = str(job.get("id", ""))
                    if jid in seen_ids:
                        continue
                    seen_ids.add(jid)
                    loc   = html.unescape(safe_str(job.get("jobGeo", ""))).lower()
                    if not any(r in loc for r in allowed_regions):
                        continue
                    title = html.unescape(safe_str(job.get("jobTitle", ""))).lower()
                    desc  = html.unescape(safe_str(job.get("jobDescription", ""))).lower()
                    if not any(p in title + " " + desc for p in phrases):
                        continue
                    candidates.append({
                        "title":   html.unescape(safe_str(job.get("jobTitle", ""))),
                        "company": html.unescape(safe_str(job.get("companyName", ""))),
                        "salary":  "Not listed",
                        "url":     safe_str(job.get("url", "")),
                        "location": safe_str(job.get("jobGeo", "")),
                        "posted":  safe_str(job.get("pubDate", ""))[:10],
                        "tags":    safe_str(job.get("jobIndustry", "")),
                        "description": html.unescape(safe_str(job.get("jobDescription", ""))),
                        "source":  "Jobicy [EU]",
                        "eu_sponsored": True
                    })
    except Exception as e:
        print(f"  EU Jobicy error: {e}")

    # ── Specialist EU boards ──────────────────────────────────────────────
    candidates += fetch_arbeitnow_eu(phrases, allowed_regions)
    candidates += fetch_adzuna(phrases)

    print(f"  EU Visa Sponsored fallback: {len(candidates)} matches "
          f"(Netherlands/Finland)")
    return candidates


def fetch_gulf_anz_jobs(phrases):
    """Jobicy geo-filtered: UAE/Dubai + Australia + New Zealand — visa-sponsored QA roles"""
    import urllib.request, json, html
    candidates = []
    regions = [
        ("united-arab-emirates", "UAE/Dubai"),
        ("australia",            "Australia"),
        ("new-zealand",          "New Zealand"),
    ]
    phrase_lower = [p.lower() for p in phrases]
    for geo, label in regions:
        for tag in ["qa", "testing", "automation"]:
            try:
                url = f"https://jobicy.com/api/v2/remote-jobs?count=50&geo={geo}&tag={tag}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.loads(r.read().decode())
                jobs = data.get("jobs", [])
                for job in jobs:
                    title = html.unescape(str(job.get("jobTitle", "")))
                    text  = f"{title} {job.get('jobIndustry','')}".lower()
                    if any(p in text for p in phrase_lower):
                        candidates.append({
                            "title":    title,
                            "company":  html.unescape(str(job.get("companyName", ""))),
                            "salary":   f"{job.get('annualSalaryMin','')}–{job.get('annualSalaryMax','')} {job.get('salaryCurrency','')}".strip("– ") or "Not listed",
                            "url":      str(job.get("url", "")),
                            "location": str(job.get("jobGeo", label)),
                            "posted":   str(job.get("pubDate", ""))[:10],
                            "tags":     str(job.get("jobIndustry", "")),
                            "description": html.unescape(str(job.get("jobDescription", "")))[:500],
                            "source":   f"Jobicy [{label}]",
                            "eu_sponsored": True
                        })
            except Exception:
                pass
    # Deduplicate by URL
    seen = set()
    unique = []
    for j in candidates:
        if j["url"] not in seen:
            seen.add(j["url"])
            unique.append(j)
    print(f"  Gulf+ANZ jobs: {len(unique)} matches (UAE/Australia/NZ)")
    return unique


def fetch_adzuna(phrases):
    """Adzuna API — UAE + Australia + New Zealand QA roles with visa sponsorship"""
    import urllib.request, urllib.parse, json, html, os

    app_id  = os.getenv("ADZUNA_APP_ID", "").strip()
    app_key = os.getenv("ADZUNA_APP_KEY", "").strip()
    if not (app_id and app_key):
        print("  Adzuna: skipped (set ADZUNA_APP_ID / ADZUNA_APP_KEY in .env)")
        return []

    # country code -> label
    markets = [
        ("au", "Australia"),
        ("nz", "New Zealand"),
        ("sg", "Singapore"),
        ("gb", "UK"),
    ]

    # Software QA only — excludes food/manufacturing/civil QA noise
    title_phrases = [
        "qa automation", "test automation",
        "qa engineer", "qa lead", "qa manager", "qa analyst",
        "sdet", "automation engineer", "automation lead",
        "test engineer", "test lead", "test manager",
        "selenium", "playwright", "tosca", "cypress",
        "software tester", "software quality",
        "automation test", "quality assurance engineer",
        "quality assurance lead", "quality assurance manager",
        "quality assurance analyst"
    ]

    candidates = []
    phrase_lower = [p.lower() for p in phrases]

    for country, label in markets:
        for keyword in ["qa automation", "test automation", "quality assurance"]:
            try:
                params = urllib.parse.urlencode({
                    "app_id":         app_id,
                    "app_key":        app_key,
                    "results_per_page": 20,
                    "what":           keyword,
                    "content-type":   "application/json",
                })
                url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?{params}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.loads(r.read().decode())
                jobs = data.get("results", [])
                for job in jobs:
                    title = html.unescape(str(job.get("title", "")))
                    # Title-only match to avoid false positives
                    title_lower = title.lower()
                    if any(p in title_lower for p in title_phrases):
                        desc = html.unescape(str(job.get("description", "")))
                        salary_min = job.get("salary_min", "")
                        salary_max = job.get("salary_max", "")
                        salary = f"{salary_min}–{salary_max}".strip("–") if salary_min or salary_max else "Not listed"
                        candidates.append({
                            "title":       title,
                            "company":     html.unescape(str(job.get("company", {}).get("display_name", ""))),
                            "salary":      salary,
                            "url":         str(job.get("redirect_url", "")),
                            "location":    str(job.get("location", {}).get("display_name", label)),
                            "posted":      str(job.get("created", ""))[:10],
                            "tags":        keyword,
                            "description": desc[:500],
                            "source":      f"Adzuna [{label}]",
                            "eu_sponsored": True
                        })
            except Exception as e:
                print(f"  Adzuna [{label}/{keyword}] error: {e}")

    # Deduplicate by URL
    seen = set()
    unique = []
    for j in candidates:
        if j["url"] not in seen and j["title"]:
            seen.add(j["url"])
            unique.append(j)

    print(f"  Adzuna Gulf+ANZ: {len(unique)} matches (UAE/AU/NZ)")
    return unique



# ══════════════════════════════════════════════════════════════════════════════
# v2.0 — Sources added for the AI/agent + India/EMEA/APAC search.
#
# gethired   : Indian board, public JSON API, no key.
# HN hiring  : monthly "Ask HN: Who is hiring?" thread via the public Algolia
#              API. Stands in for YC Work at a Startup, which returns 406 to
#              non-browser clients (we do not spoof headers to get around that).
# RemoteRocketship : paid API, skipped cleanly when no key is present.
# ══════════════════════════════════════════════════════════════════════════════

GETHIRED_API = "https://gethired.devhub.co.in/api/jobs"
GETHIRED_JOB = "https://gethired.devhub.co.in/jobs/{id}"

# gethired reports bare salary numbers; infer the currency from the location.
_CUR_BY_COUNTRY = [
    ("india", "INR"), ("united states", "USD"), ("canada", "CAD"),
    ("united kingdom", "GBP"), ("england", "GBP"),
    ("singapore", "SGD"), ("australia", "AUD"),
    ("germany", "EUR"), ("netherlands", "EUR"), ("france", "EUR"),
    ("spain", "EUR"), ("ireland", "EUR"), ("portugal", "EUR"),
]


def _gethired_currency(locations):
    loc = " ".join(locations).lower() if locations else ""
    for key, cur in _CUR_BY_COUNTRY:
        if key in loc:
            return cur
    return None


def fetch_gethired(phrases, max_pages=6, page_size=500):
    """
    gethired.devhub.co.in — public cursor-paginated JSON API (~15k jobs).

    The API ignores search/filter params (verified: `search`, `q` and `remote`
    all return the same unfiltered page), so we page through newest-first and
    filter client-side. Descriptions are not public — `ai_summary` when present,
    otherwise a synthesised stub, which the scorer will correctly mark N/A.
    """
    import os
    results, cursor, seen = [], None, set()
    scanned = 0
    phrase_lower = [p.lower() for p in phrases]

    # The public endpoint returns no description at all, which is why every
    # gethired job ends up unscoreable. A paid account's Supabase JWT unlocks
    # the full record; set GETHIRED_TOKEN to use it. Tokens expire in about an
    # hour, so a 401 here means "refresh it", not "wrong token".
    token = os.getenv("GETHIRED_TOKEN", "").strip()
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for _ in range(max_pages):
        url = f"{GETHIRED_API}?limit={page_size}"
        if cursor:
            url += f"&cursor={urllib.parse.quote(cursor)}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as e:
            print(f"  gethired error: {e}")
            break

        batch = data.get("data", [])
        if not batch:
            break
        scanned += len(batch)

        for job in batch:
            jid = str(job.get("id", ""))
            if not jid or jid in seen:
                continue
            title = html.unescape(safe_str(job.get("job_name", ""))).strip()
            if not title:
                continue
            # Authenticated responses may carry a real JD under one of several
            # names; fall back to ai_summary, then to a synthesised stub.
            summary = ""
            for field in ("description", "job_description", "jd",
                          "full_description", "ai_summary"):
                val = job.get(field)
                if val:
                    summary = html.unescape(safe_str(val))
                    break
            if not any(p in (title + " " + summary).lower() for p in phrase_lower):
                continue
            seen.add(jid)

            locs = job.get("locations") or []
            exp_min = job.get("experience_min")
            desc = summary or (
                f"{title} at {job.get('company_name','')}. "
                f"Location: {', '.join(locs) or 'unspecified'}. "
                f"Experience: {safe_str(job.get('experience','')) or 'unspecified'}. "
                f"Type: {safe_str(job.get('job_type',''))}."
            )
            results.append({
                "title": title,
                "company": html.unescape(safe_str(job.get("company_name", ""))),
                "salary": safe_str(job.get("salary_range", "")) or "Not listed",
                "salary_min": job.get("salary_min"),
                "salary_max": job.get("salary_max"),
                "salary_currency": _gethired_currency(locs),
                "url": GETHIRED_JOB.format(id=jid),
                "location": ", ".join(locs) if locs else "Not specified",
                "posted": safe_str(job.get("created_at", ""))[:10],
                "tags": safe_str(job.get("job_type", "")),
                "description": desc,
                "experience_min": exp_min,
                "visa_requirement": job.get("visa_requirement"),
                "source": "GetHired [India]",
            })

        cursor = data.get("nextCursor")
        if not cursor:
            break

    print(f"  GetHired: {len(results)} matches from {scanned} scanned")
    return results


# ── HN "Ask HN: Who is hiring?" ──────────────────────────────────────────────

HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM = "https://news.ycombinator.com/item?id={id}"

_HN_FIELD = {
    "location": re.compile(r'location\s*[:\-]\s*([^|\n]{2,60})', re.I),
    "remote": re.compile(r'remote\s*[:\-]\s*([^|\n]{2,40})', re.I),
    "tech": re.compile(r'(?:tech|stack|technologies)\s*[:\-]\s*([^|\n]{2,120})', re.I),
}


# "REMOTE (EEA)" and "Remote - US only" are restrictions, not worldwide roles.
# Treating them as unrestricted inflates the timezone score and, worse, hides
# that the employer cannot hire outside that bloc.
_HN_SCOPE = re.compile(
    r'(?i)\bremote\b[\s(\[-]*'
    r'(eea|eu|emea|europe[a-z]*|uk|united kingdom|us|usa|united states|'
    r'north america|latam|apac|asia|anywhere|worldwide|global|'
    r'cet|cest|est|pst|gmt|utc[+\-0-9]*)')
_HN_ONLY = re.compile(
    r'(?i)\b(eea|eu|emea|europe|uk|us|usa|united states|canada|latam|apac|'
    r'india|germany|netherlands|poland|spain|portugal)\b[ -]*only')


def _clean_company(seg):
    """
    Posters often append their URL to the company name — "Distru (
    https://distru.com )". Keep the name, drop the link and any empty
    brackets it leaves behind.
    """
    s = re.sub(r'https?://\S+|www\.\S+', ' ', str(seg or ''))
    s = re.sub(r'[\(\[]\s*[\)\]]', ' ', s)          # brackets the URL emptied
    s = re.sub(r'\s+', ' ', s).strip(' -–—|,;:')
    # Only drop a dangling bracket, never a balanced one: "MixRank (YC S11)"
    # must keep its batch tag.
    if s.count("(") > s.count(")"):
        s = s.rstrip(" (")
    if s.count(")") > s.count("("):
        s = s.lstrip(" )")
    return s[:60].strip() or "See post"


def _hn_location(text, parts, low):
    """
    Best-effort location from a free-form HN hiring post.

    Posts rarely use a "Location:" field; most encode it in the pipe header
    ("Acme | Role | REMOTE (EEA)"). Defaulting any post containing the word
    "remote" to Worldwide is wrong often enough to matter, so look for an
    explicit scope first and only fall back when there is genuinely none.
    """
    m = _HN_FIELD["location"].search(text)
    if m and m.group(1).strip():
        return m.group(1).strip()[:70]

    m = _HN_ONLY.search(text)
    if m:
        return f"{m.group(1).upper()} only"

    # A pipe segment that names remoteness plus a region is the usual form.
    for p in parts[1:6]:
        if _HN_SCOPE.search(p):
            return p.strip()[:70]
    m = _HN_SCOPE.search(text)
    if m:
        return f"Remote ({m.group(1)})"

    mr = _HN_FIELD["remote"].search(text)
    if mr and mr.group(1).strip():
        return mr.group(1).strip()[:70]

    # Genuinely unqualified "remote" — treat as worldwide, which is the
    # honest reading when the poster named no restriction at all.
    return "Worldwide" if "remote" in low else "Not specified"


def fetch_hn_hiring(phrases, max_posts=400):
    """
    The monthly Ask HN hiring thread — startup-heavy, AI-heavy, remote-friendly.

    Free and unblocked via Algolia's public API. Posts are free text with a
    loose "Company | Role | Location | Remote" convention, so parsing is
    best-effort: the whole comment becomes the description and the scorer
    reads skills straight out of it.
    """
    import re as _re
    results = []
    try:
        req = urllib.request.Request(
            f"{HN_SEARCH}?tags=story,author_whoishiring&hitsPerPage=1",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            hits = json.loads(r.read().decode()).get("hits", [])
        if not hits:
            print("  HN hiring: no thread found")
            return []
        thread = hits[0]
        tid, ttitle = thread["objectID"], thread.get("title", "")
    except Exception as e:
        print(f"  HN hiring error: {e}")
        return []

    try:
        req = urllib.request.Request(
            f"https://hn.algolia.com/api/v1/search?tags=comment,story_{tid}"
            f"&hitsPerPage={max_posts}",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            comments = json.loads(r.read().decode()).get("hits", [])
    except Exception as e:
        print(f"  HN hiring comments error: {e}")
        return []

    phrase_lower = [p.lower() for p in phrases]
    try:
        story_id = int(tid)
    except (TypeError, ValueError):
        story_id = None

    for c in comments:
        # Only top-level posts are job ads. Replies ("is this remote?",
        # "stop emailing me") and candidate self-pitches are not, and they
        # match the same keywords, so they must be dropped structurally.
        if story_id is not None and c.get("parent_id") != story_id:
            continue

        raw = c.get("comment_text") or ""
        if not raw:
            continue
        text = html.unescape(_re.sub(r'<[^>]+>', ' ', raw))
        text = _re.sub(r'\s+', ' ', text).strip()
        if len(text) < 120:
            continue
        low = text.lower()
        if not any(p in low for p in phrase_lower):
            continue

        # A real posting carries hiring structure: a pipe-delimited header,
        # or an explicit field, or an apply link. Self-pitches rarely do.
        has_structure = (
            text.count("|") >= 2
            or _HN_FIELD["location"].search(text)
            or _re.search(r'(apply|hiring|careers|jobs?)\b', low)
        )
        if not has_structure:
            continue
        # First person means a candidate advertising themselves, not a job.
        if _re.match(r"\s*(i'm|i am|my name)\b", low):
            continue

        # Convention is "Company | Role | Location | ...". Take the first
        # segment as company and the first later segment that looks like a
        # role (not a location/salary field) as the title.
        parts = [p.strip() for p in _re.split(r'[|\n]', text) if p.strip()]
        # Not every post leads with the company; some open with "Location: ...".
        # Taking parts[0] blindly produces companies called "Location: Spain".
        company = next(
            (_clean_company(p) for p in parts[:3]
             if not _re.match(r'(?i)(location|remote|salary|comp|visa|stack|'
                              r'tech|role|position|about)\s*[:\-]', p)
             and not p.lower().startswith(("http", "www."))),
            "See post")

        def _usable(p):
            if not (4 < len(p) < 80):
                return False
            if p.lower().startswith(("http", "www.")):
                return False
            return not _re.match(
                r'(?i)(location|remote|salary|comp|visa|stack|tech|email|apply)\s*[:\-]', p)

        # Posts do not agree on field order, so prefer a segment that actually
        # names a role over one that merely sits in the right position —
        # otherwise "Acme | Berlin, NYC | Engineer" titles the job "Berlin, NYC".
        cands = [p for p in parts[1:6] if _usable(p)]
        role_re = _re.compile(
            r'(?i)\b(engineer|developer|scientist|architect|lead|manager|'
            r'designer|researcher|analyst|sre|devops|founding|head of|intern)\b')
        title = next((p for p in cands if role_re.search(p)),
                     cands[0] if cands else company[:80])

        location = _hn_location(text, parts, low)

        results.append({
            "title": title,
            "company": company,
            "salary": "Not listed",
            "url": HN_ITEM.format(id=c.get("objectID", tid)),
            "location": location,
            "posted": safe_str(c.get("created_at", ""))[:10],
            "tags": "hn, startup",
            "description": text[:4000],
            "source": f"HN [{ttitle[-14:-1] if ttitle else 'hiring'}]",
        })

    print(f"  HN hiring: {len(results)} matches from {len(comments)} posts")
    return results


# ── RemoteRocketship (paid API) ──────────────────────────────────────────────

# Trailing slash is required. Without it the host answers 308 to /jobs/ and
# urllib will not replay a POST body across a redirect, so the call silently
# yields nothing. Their published docs omit the slash.
RR_ENDPOINT = "https://www.remoterocketship.com/api/openclaw/jobs/"


class _RRQuotaExhausted(Exception):
    """Account-wide stop condition (429/401/403) — not a per-region failure."""



def fetch_remoterocketship(phrases, profile_config, profile_meta, max_pages=4):
    """
    RemoteRocketship — the best filtering of any board here, but it needs a
    paid subscription key. Without REMOTEROCKETSHIP_API_KEY we skip quietly
    rather than failing the run.

    Server-side filters come from profile_meta["remoterocketship"], so you tune
    the query in JSON instead of here. Caps: 50 jobs/request, 3000 jobs and
    500 requests per UTC day.
    """
    import os
    key = os.getenv("REMOTEROCKETSHIP_API_KEY", "").strip()
    if not key:
        print("  RemoteRocketship: skipped (set REMOTEROCKETSHIP_API_KEY in .env)")
        return []

    rr = dict(profile_meta.get("remoterocketship", {}))
    sal = profile_config.get("salary", {})
    fx = sal.get("fx_to_inr", {})
    min_lpa = float(sal.get("min_lpa", 0) or 0)
    # Their minSalaryFilter is USD; convert the LPA floor across.
    usd_rate = float(fx.get("USD", 89.0))
    min_usd = int(min_lpa * 100_000 / usd_rate) if min_lpa else 0

    # One request per region. The API returns no geography of its own, so the
    # only honest location for a result is the single region we asked about.
    # Querying all regions at once and labelling every row with the whole list
    # makes every job look India-eligible and 9h-overlapping, which is a
    # fabricated signal, not a generous one.
    regions = rr.get("locationFilters") or [None]
    results, seen_urls = [], set()
    raw_records = []
    done = 0
    for region in regions:
        try:
            results.extend(_rr_fetch_region(key, rr, region, min_usd,
                                            max_pages, seen_urls, raw_records))
            done += 1
        except _RRQuotaExhausted as e:
            print(f"  RemoteRocketship: stopping after {done}/{len(regions)} "
                  f"regions — {e}")
            break

    # Archive what the provider actually sent, before normalisation drops
    # the fields this pipeline does not use yet. Quota is the scarce thing
    # here, not disk: a field nobody anticipated must never cost a re-fetch.
    if raw_records:
        try:
            import store
            p = store.save_raw("remoterocketship", raw_records)
            if p:
                kb = p.stat().st_size // 1024
                print(f"  RemoteRocketship: archived raw response ({kb} KB gz)")
        except Exception as e:
            print(f"  RemoteRocketship: raw archive failed ({e})")

    dupes = len(raw_records) - len(results)
    print(f"  RemoteRocketship: {len(results)} unique jobs across {done} region(s)"
          + (f" — {len(raw_records)} billed, {dupes} duplicate" if dupes > 0 else ""))
    return results


def _rr_fetch_region(key, rr, region, min_usd, max_pages, seen_urls, raw_sink=None):
    """Page through one RemoteRocketship region. See fetch_remoterocketship."""
    results, page = [], 1
    while page <= max_pages:
        filters = {
            "page": page,
            "itemsPerPage": 50,
            "showRemoteJobs": True,
            "showHybridJobs": False,
            "showOnsiteJobs": False,
            "sortBy": "DateAdded",
            "hideGhostJobsFilter": "true",
            "showJobsWithoutSalaryWithMinSalaryFilter": True,
        }
        for k in ("jobTitleFilters", "techStackFilters", "seniorityFilters",
                  "keywordFilters", "excludedKeywordFilters",
                  "employmentTypeFilters"):
            if rr.get(k):
                filters[k] = rr[k]
        if region:
            filters["locationFilters"] = [region]
        if min_usd:
            filters["minSalaryFilter"] = min_usd

        body = json.dumps({"filters": filters,
                           "includeJobDescription": True}).encode()
        hdrs = {"Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "remote-job-hunter"}
        try:
            req = urllib.request.Request(RR_ENDPOINT, data=body,
                                         method="POST", headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                # 307/308 preserve the method and body; urllib does not replay
                # them, so follow once by hand rather than losing the source.
                if e.code not in (307, 308):
                    raise
                target = e.headers.get("Location", "")
                if not target:
                    raise
                target = urllib.parse.urljoin(RR_ENDPOINT, target)
                req = urllib.request.Request(target, data=body,
                                             method="POST", headers=hdrs)
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode()).get("message", "")
            except Exception:
                pass
            print(f"  RemoteRocketship HTTP {e.code}: {detail or e.reason}")
            # Quota and auth apply to the whole account, so retrying the other
            # 11 regions just prints the same error 11 more times.
            if e.code in (401, 403, 429):
                raise _RRQuotaExhausted(detail or e.reason)
            break
        except Exception as e:
            print(f"  RemoteRocketship error: {e}")
            break

        openings = data.get("jobOpenings", [])
        if not openings:
            break
        if raw_sink is not None:
            raw_sink.extend(openings)

        for j in openings:
            comp = j.get("company") or {}
            seniority = next((lbl for key_, lbl in [
                ("isLead", "lead"), ("isSenior", "senior"), ("isMidLevel", "mid"),
                ("isJunior", "junior"), ("isEntryLevel", "entry")] if j.get(key_)), "")
            desc = " ".join(filter(None, [
                safe_str(j.get("roleDescription", "")),
                safe_str(j.get("roleRequirements", "")),
                safe_str(j.get("jobDescriptionSummary", "")),
                " ".join(j.get("techStack") or []),
            ]))

            # salaryRange is an object, not a string. It carries USD-normalised
            # bounds, which beats re-parsing a formatted range.
            sr = j.get("salaryRange")
            sal_min = sal_max = None
            sal_cur = "USD"
            sal_text = "Not listed"
            if isinstance(sr, dict):
                sal_min = sr.get("minSalaryAsUSD") or sr.get("min")
                sal_max = sr.get("maxSalaryAsUSD") or sr.get("max")
                sal_cur = "USD" if sr.get("minSalaryAsUSD") else (
                    sr.get("currencyCode") or "USD")
                if sal_min or sal_max:
                    sym = sr.get("currencySymbol", "$")
                    sal_text = f"{sym}{sr.get('min','?')}-{sym}{sr.get('max','?')} " \
                               f"{sr.get('currencyCode','')} {sr.get('salaryType','')}".strip()
            elif sr:
                sal_text = safe_str(sr)

            # The API returns no geography — it is a remote-only board — so the
            # honest label is the region we asked it to filter on, not a
            # pretend city. Timezone scoring treats this as unrestricted.
            location = region or "Worldwide (remote)"

            results.append({
                "title": html.unescape(safe_str(j.get("roleTitle", ""))),
                "company": html.unescape(safe_str(comp.get("name", ""))),
                "salary": sal_text,
                "salary_min": sal_min,
                "salary_max": sal_max,
                "salary_currency": sal_cur,
                "url": safe_str(j.get("url", "")),
                "location": location,
                "posted": safe_str(j.get("created_at", ""))[:10],
                "tags": ", ".join((j.get("techStack") or [])[:6]),
                "description": desc,
                "seniority": seniority,
                # A role demanding fluent German in Berlin is a non-starter;
                # that was being discarded at fetch time.
                "required_languages": j.get("requiredLanguages") or [],
                "employment_type": safe_str(j.get("employmentType", "")),
                "experience_months": j.get("experienceRequirementsMonthsOfExperience"),
                "degree_required": safe_str(
                    j.get("educationRequirementsCredentialCategory", "")),
                "experience_in_place_of_education": j.get("experienceInPlaceOfEducation"),
                "valid_until": safe_str(j.get("validUntilDate", "")),
                "benefits": safe_str(j.get("benefits", ""))[:600],
                "company_linkedin": safe_str(comp.get("linkedInURL", "")),
                "company_site": safe_str(comp.get("homePageURL", "")),
                "company_size": safe_str(comp.get("employeeRange", "")),
                "company_industry": safe_str(comp.get("industry", "")),
                "company_funding": safe_str(comp.get("totalFundingAmount", "")),
                "company_founded": comp.get("foundedYear"),
                "sponsors_h1b": comp.get("sponsorsH1B"),
                "sponsors_uk_visa": comp.get("sponsorsUKSkilledWorkerVisa"),
                "source": "RemoteRocketship",
            })

        pg = data.get("pagination") or {}
        if not pg.get("hasNextPage"):
            break
        page += 1

    # A role open to several of our regions comes back more than once; keep
    # the first (regions are listed most-relevant first in the profile).
    deduped = []
    for j in results:
        u = j.get("url")
        if u and u in seen_urls:
            continue
        if u:
            seen_urls.add(u)
        deduped.append(j)
    return deduped


def _dedupe_jobs(jobs):
    """First occurrence of each URL wins; untitled rows are dropped."""
    seen, unique = set(), []
    for j in jobs:
        url = j.get("url")
        if url and url not in seen and j.get("title"):
            seen.add(url)
            unique.append(j)
    return unique


def fetch_all(domain, phrases, profile_config, profile_meta=None, offline=False):
    """
    Pull every enabled source.

    Each source is snapshotted to data/raw/<date>/<source>.json as it is
    fetched, and a source whose platform config sets `cache_hours` is served
    from that snapshot while it is fresh. This is what keeps paid, rate-capped
    data (RemoteRocketship: 3000 jobs / 500 requests per UTC day) from being
    re-bought every time the scorer is tweaked.

    offline=True replays the latest snapshots and makes no network calls.
    """
    import store

    platforms = load_platforms()
    profile_meta = profile_meta or {}
    all_jobs = []

    if offline:
        date = store.latest_snapshot_date()
        if not date:
            print("  offline: no snapshots on disk — run once online first")
            return []
        jobs = store.load_all_sources(date)
        print(f"  offline: replayed {len(jobs)} jobs from {date}")
        return _dedupe_jobs(jobs)

    def sourced(name, fn):
        """Serve from a fresh snapshot when allowed, else fetch and save."""
        cfg = platforms.get(name, {})
        blob = store.load_fresh(name, cfg.get("cache_hours", 0))
        if blob is not None:
            age = blob.get("fetched_at", "")[:16].replace("T", " ")
            print(f"  {name}: {blob.get('count', 0)} jobs from snapshot ({age})")
            return blob.get("jobs", [])
        jobs = fn()
        if jobs:
            try:
                store.save_source(name, jobs)
            except Exception as e:
                print(f"  {name}: snapshot not saved ({e})")
            return jobs

        # Empty means the fetch failed (quota, auth, outage) far more often
        # than it means "no jobs today". Fall back to the last good snapshot
        # at any age: week-old postings are still open, nothing is not.
        stale = store.load_latest_any(name)
        if stale:
            age = stale.get("fetched_at", "")[:10]
            print(f"  {name}: live fetch empty — using snapshot from {age} "
                  f"({stale.get('count', 0)} jobs, STALE)")
            for j in stale.get("jobs", []):
                j["stale_from"] = age
            return stale.get("jobs", [])
        return jobs

    # ── Primary: WFA platforms ────────────────────────────────────────────
    if platforms.get("remotive", {}).get("enabled"):
        all_jobs += sourced("remotive", lambda: fetch_remotive(platforms["remotive"], domain))
    if platforms.get("jobicy", {}).get("enabled"):
        all_jobs += sourced("jobicy", lambda: fetch_jobicy(platforms["jobicy"], domain))
    if platforms.get("remoteok", {}).get("enabled"):
        all_jobs += sourced("remoteok", lambda: fetch_remoteok(platforms["remoteok"], phrases))
    if platforms.get("weworkremotely", {}).get("enabled"):
        all_jobs += sourced("weworkremotely", lambda: fetch_wwr(platforms["weworkremotely"], domain, phrases))
    if platforms.get("himalayas", {}).get("enabled"):
        all_jobs += sourced("himalayas", lambda: fetch_himalayas(platforms["himalayas"], phrases))
    if platforms.get("realworkfromanywhere", {}).get("enabled") and profile_config.get("realwfa_enabled", True):
        all_jobs += fetch_realworkfromanywhere(platforms["realworkfromanywhere"], domain, phrases)
    if platforms.get("workingnomads", {}).get("enabled"):
        all_jobs += sourced("workingnomads", lambda: fetch_workingnomads(platforms["workingnomads"], domain, phrases))
    if platforms.get("remoteai", {}).get("enabled"):
        all_jobs += fetch_remoteai(platforms["remoteai"], phrases)
    if platforms.get("remote100k", {}).get("enabled"):
        all_jobs += fetch_remote100k(platforms["remote100k"], phrases)

    # ── v2.0 sources ──────────────────────────────────────────────────────
    if platforms.get("gethired", {}).get("enabled"):
        all_jobs += sourced("gethired", lambda: fetch_gethired(
            phrases,
            max_pages=platforms["gethired"].get("max_pages", 6),
            page_size=platforms["gethired"].get("page_size", 500)))
    if platforms.get("hn_hiring", {}).get("enabled"):
        all_jobs += sourced("hn_hiring", lambda: fetch_hn_hiring(
            phrases, max_posts=platforms["hn_hiring"].get("max_posts", 400)))
    if platforms.get("remoterocketship", {}).get("enabled"):
        all_jobs += sourced("remoterocketship", lambda: fetch_remoterocketship(
            phrases, profile_config, profile_meta,
            max_pages=platforms["remoterocketship"].get("max_pages", 4)))

    # ── Deduplicate WFA jobs ──────────────────────────────────────────────
    unique = _dedupe_jobs(all_jobs)
    seen = {j["url"] for j in unique}

    # ── EU Fallback: trigger if WFA matches below threshold ───────────────
    eu_config = profile_config.get("eu_fallback", {})
    if eu_config.get("enabled"):
        threshold = eu_config.get("min_wfa_threshold", 8)
        if len(unique) < threshold:
            print(f"\n  ⚠️  Only {len(unique)} WFA matches — triggering EU visa-sponsored fallback (threshold: {threshold})")
            eu_jobs = fetch_eu_visa_sponsored(profile_config, phrases)
            for j in eu_jobs:
                if j["url"] not in seen and j["title"]:
                    seen.add(j["url"])
                    unique.append(j)
        else:
            print(f"\n  ✅ {len(unique)} WFA matches — EU fallback not needed")

    print(f"\n  Total unique jobs: {len(unique)}")
    return unique
