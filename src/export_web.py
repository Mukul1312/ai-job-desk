#!/usr/bin/env python3
"""
export_web.py — Turn a ranked run into JSON the web tracker can read.

The markdown report is fine for reading once. It cannot hold a decision: which
jobs you already looked at, which you applied to, what you thought. That state
belongs to you and has to survive the next run, so the page keeps it in the
artifact's database and reads only the job data from here.

Writes:
    web/public/data/index.json        days, newest first, with per-day counts
    web/public/data/jobs-<date>.json  that day's ranked jobs, trimmed for the browser

These are served as static files by Vercel; the site fetches them directly.

Descriptions are cut to a snippet — the full text can be 8KB a job and the
page never shows it. Keeping it would make the payload tens of megabytes.
"""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
WEB_DIR = WORKSPACE / "web" / "public" / "data"
SNIPPET_CHARS = 420


def _clean(text, limit=SNIPPET_CHARS):
    """Strip markup and instruction-shaped text from third-party job copy."""
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", str(text))
    t = re.sub(
        r"(?i)(ignore (?:all )?previous|disregard (?:all )?prior|"
        r"new instructions?|system prompt|forget (?:everything|all))",
        "[removed]", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def _job_row(job, rank):
    tz = job.get("tz") or {}
    sal = job.get("salary_lpa") or {}
    india = job.get("india") or {}
    return {
        "rank": rank,
        "id": job.get("url") or f"{job.get('company','')}|{job.get('title','')}",
        "title": _clean(job.get("title"), 140),
        "company": _clean(job.get("company"), 80),
        "source": _clean(job.get("source"), 40),
        "url": job.get("url") or "",
        "location": _clean(job.get("location"), 90),
        "posted": _clean(job.get("posted"), 20),
        "score": job.get("final_score"),
        "fit": job.get("match_score"),
        "overlap": tz.get("overlap_hours"),
        "region": tz.get("region"),
        "tzName": tz.get("tz"),
        "verdict": tz.get("verdict"),
        "lpa": sal.get("lpa"),
        "lpaLow": sal.get("low_lpa"),
        "lpaHigh": sal.get("high_lpa"),
        "india": india.get("confidence", "unknown"),
        "engagement": india.get("engagement") or "employment",
        "indiaWhy": [_clean(e, 90) for e in (india.get("evidence") or [])][:3],
        "resume": job.get("resume"),
        "tier": job.get("role_tier"),
        "isNew": bool(job.get("is_new")),
        "firstSeen": job.get("first_seen"),
        "stale": job.get("stale_from"),
        "snippet": _clean(job.get("description")),
        "scored": job.get("final_score") is not None,
    }


def export(ranked, unscored, stats, profile_config, profile_meta,
           gap_analysis=None, date_str=None):
    """Write one day's data and refresh the index. Returns the day's path."""
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    WEB_DIR.mkdir(parents=True, exist_ok=True)

    rows = [_job_row(j, i) for i, j in enumerate(ranked, 1)]
    # Unscored jobs cleared the same hard filters; they simply had no JD text
    # to grade. They carry the Indian supply, so they ship alongside rather
    # than being dropped.
    rows += [{**_job_row(j, None), "scored": False}
             for j in (unscored or [])]

    gaps = []
    if gap_analysis:
        for bucket in ("quick_wins", "medium", "long_term"):
            for skill, count in (gap_analysis.get(bucket) or []):
                gaps.append({"skill": skill, "count": count, "effort": bucket})

    payload = {
        "date": date_str,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": {
            "name": profile_config.get("name", ""),
            "label": profile_meta.get("label", ""),
            "window": f"{(profile_config.get('timezone') or {}).get('available_from','')}"
                      f"–{(profile_config.get('timezone') or {}).get('available_to','')}",
            "homeTz": (profile_config.get("timezone") or {}).get("home_tz", ""),
            "minLpa": (profile_config.get("salary") or {}).get("min_lpa"),
            "minOverlap": (profile_config.get("timezone") or {}).get("min_overlap_hours"),
        },
        "stats": stats or {},
        "gaps": gaps,
        "jobs": rows,
    }
    day_path = WEB_DIR / f"jobs-{date_str}.json"
    day_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    _refresh_index()
    return day_path


def _refresh_index():
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    days = []
    for f in sorted(WEB_DIR.glob("jobs-*.json"), reverse=True):
        try:
            blob = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        jobs = blob.get("jobs", [])
        days.append({
            "date": blob.get("date", f.stem.replace("jobs-", "")),
            "file": f.name,
            "total": len(jobs),
            "ranked": sum(1 for j in jobs if j.get("scored")),
            "new": sum(1 for j in jobs if j.get("isNew")),
            "generatedAt": blob.get("generatedAt", ""),
        })
    (WEB_DIR / "index.json").write_text(
        json.dumps({"days": days,
                    "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                   ensure_ascii=False),
        encoding="utf-8")
    return days


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    days = _refresh_index()
    print(f"web/index.json refreshed — {len(days)} day(s)")
    for d in days:
        print(f"  {d['date']}  {d['ranked']:>4} ranked / {d['total']:>4} total"
              f"  {d['new']:>4} new")
