#!/usr/bin/env python3
"""
capture_jd.py - put the real posting text under the eval, not a retyping of it.

The problem this fixes, measured:

    RemoteRocketship rewrites 100% of its 827 descriptions into a bullet list,
    averaging 1,987 characters. It is a summary, not a capture.

    eventfirst's real posting says "We value demonstrated skill over a fixed
    number of years of experience." That sentence is the reason the role is
    reachable at all, and the whole reason NEGOTIABLE exists in jdextract.py.

    It is not in the archived description. Neither is "Berlin", "cover letter",
    "Trigger.dev" or "Redis".

So the archive cannot supply ground truth, and the six jd.txt files that did
exist were typed by hand from the page - which is how Streamhub's "Bengaluru, IN"
got lost and three seniority labels ended up recording a conclusion rather than
the text.

The board APIs return the posting in full. Measured against the same rows:

    ClickHouse frontend   archive 1,591 ch  ->  board API 4,129 ch
    GitLab Ruby           archive 1,930 ch  ->  board API 9,551 ch

    python tools/capture_jd.py            # capture, write cases/ + MANIFEST
    python tools/capture_jd.py --dry-run  # show what it would fetch

Fidelity is recorded per case in MANIFEST.json and never guessed at:

    board-api        the employer's own ATS, full text
    hand-paraphrase  typed from the page by a human; treat labels with care
"""

import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
CASES_DIR = WORKSPACE / "tests" / "jd_extraction" / "cases"
UA = {"User-Agent": "Mozilla/5.0 (compatible; job-desk/1.0)"}

# stem -> (platform, board slug, job id). Resolved by hand against each board and
# each posting's location, because the fuzzy matches were wrong: "gitlab backend"
# hits the Python role, not the Ruby one, and NewRocket lists four near-identical
# Forward Deployed roles in the UK alone.
BOARD_CASES = {
    "circuithub-applied-ai":        ("ashby", "circuithub", "52a918be-07d"),
    "clickhouse-clickstack":        ("ashby", "clickhouse", "03b2433e"),
    "clickhouse-swe-ts-frontend":   ("ashby", "clickhouse", "33313f14"),
    "cresta-ai-growth-engineer":    ("greenhouse", "cresta", "5476118008"),
    "gitlab-senior-backend-ruby":   ("greenhouse", "gitlab", "8682860002"),
    "gp-ai-engineer-ireland":       ("greenhouse", "globalizationpartners", "8034410003"),
    "gp-ai-engineer-uk":            ("greenhouse", "globalizationpartners", "8034413003"),
    "newrocket-fde-uk":             ("greenhouse", "highmetric", "6164664004"),
    "newrocket-senior-lead-fde-nl": ("greenhouse", "highmetric", "6166119004"),
    "sword-health-senior-ai-swe":   ("greenhouse", "swordhealth", "4422658009"),
}

# NewRocket lists two UK "Forward Deployed AI Engineer" postings that differ only
# in length (3,419 and 3,089 chars in the archive). Nothing written down says
# which one was examined, so the longer is used and the ambiguity is recorded
# here rather than hidden.
AMBIGUOUS = {"newrocket-fde-uk":
             "two near-identical UK postings exist (6164664004, 6164673004); "
             "the longer was taken"}

# No public board API: Gem, Zoho Recruit, BambooHR's list endpoint and Personio's
# search.json all omit the description. These keep their hand-typed text and are
# labelled as such.
PARAPHRASE_CASES = {
    "eventfirst-senior-fullstack":    "eventfirst-senior-fullstack-ts-ai",
    "marcopolo-ai-product-engineer":  "marcopolo-ai-product-engineer",
    "marrina-ai-engineer-saas":       "marrina-ai-engineer-saas",
    "shuru-voice-ai-engineer":        "shuru-voice-ai-engineer",
    "streamhub-fullstack-automation": "streamhub-fullstack-automation-ai-eval",
    "synthio-labs-swe-ai":            "synthio-labs-software-engineer-ai",
}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _detag(raw):
    """HTML -> text. Unescape FIRST, then strip tags.

    The other order leaves literal <div class="content-intro"> in the output,
    because the entities decode into tags after the stripper has already run.
    """
    t = html.unescape(raw or "")
    t = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return "\n".join(line.strip() for line in t.split("\n")).strip()


def fetch_ashby(slug, job_id):
    for j in _get("https://api.ashbyhq.com/posting-api/job-board/%s" % slug).get("jobs", []):
        if j.get("id", "").startswith(job_id):
            text = j.get("descriptionPlain") or _detag(j.get("descriptionHtml"))
            locs = [j.get("location", "")] + [
                s.get("location") for s in (j.get("secondaryLocations") or [])
                if isinstance(s, dict)]
            return {"text": text, "title": j.get("title", ""),
                    "location": ", ".join(x for x in locs if x),
                    "url": j.get("jobUrl", "")}
    return None


def fetch_greenhouse(slug, job_id):
    url = "https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % slug
    for j in _get(url).get("jobs", []):
        if str(j.get("id")) == str(job_id):
            return {"text": _detag(j.get("content")), "title": j.get("title", ""),
                    "location": (j.get("location") or {}).get("name", ""),
                    "url": j.get("absolute_url", "")}
    return None


FETCH = {"ashby": fetch_ashby, "greenhouse": fetch_greenhouse}


def main(argv):
    dry = "--dry-run" in argv
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    manifest, ok, failed = {}, 0, []

    for stem, (platform, slug, job_id) in sorted(BOARD_CASES.items()):
        try:
            got = FETCH[platform](slug, job_id)
        except Exception as e:
            got = None
            failed.append((stem, type(e).__name__))
        if not got or len(got["text"]) < 300:
            if not failed or failed[-1][0] != stem:
                failed.append((stem, "not found or too short"))
            print("  FAIL  %-32s %s/%s" % (stem, platform, slug))
            continue
        old = ""
        p = CASES_DIR / (stem + ".txt")
        if p.exists():
            old = p.read_text(encoding="utf-8")
        if not dry:
            p.write_text(got["text"], encoding="utf-8", newline="\n")
        delta = "" if not old else "  (was %d)" % len(old)
        print("  ok    %-32s %6d ch  %s%s"
              % (stem, len(got["text"]), got["location"][:30], delta))
        manifest[stem] = {"fidelity": "board-api", "platform": platform,
                          "slug": slug, "job_id": job_id, "url": got["url"],
                          "title": got["title"], "location": got["location"],
                          "chars": len(got["text"]),
                          "captured": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
        if stem in AMBIGUOUS:
            manifest[stem]["caveat"] = AMBIGUOUS[stem]
        ok += 1

    for stem, folder in sorted(PARAPHRASE_CASES.items()):
        src = WORKSPACE / "applications" / folder / "jd.txt"
        if not src.exists():
            print("  skip  %-32s no jd.txt in applications/%s" % (stem, folder))
            continue
        text = src.read_text(encoding="utf-8")
        if not dry:
            (CASES_DIR / (stem + ".txt")).write_text(text, encoding="utf-8", newline="\n")
        print("  para  %-32s %6d ch  (hand-typed, from applications/)" % (stem, len(text)))
        manifest[stem] = {"fidelity": "hand-paraphrase", "source": folder,
                          "chars": len(text),
                          "note": "no public board API for this ATS; labels "
                                  "derived from this text may not match the page"}

    if not dry:
        (CASES_DIR / "MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")

    expected = WORKSPACE / "tests" / "jd_extraction" / "expected"
    total = len(list(expected.glob("*.json"))) if expected.exists() else 0
    print("\n  %d real + %d paraphrase = %d of %d cases have text"
          % (ok, len(manifest) - ok, len(manifest), total))
    uncovered = sorted({p.stem for p in expected.glob("*.json")} - set(manifest))
    if uncovered:
        print("  no text for: " + ", ".join(uncovered))
    if failed:
        print("  failed: " + ", ".join("%s (%s)" % f for f in failed))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main(sys.argv))
