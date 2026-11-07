#!/usr/bin/env python3
"""
store.py — Durable storage for fetched job data.

Two reasons this exists:

  1. RemoteRocketship costs money and is rate-capped (3000 jobs / 500 requests
     per UTC day). Re-fetching the same listings to re-run a scorer tweak is
     pure waste. Snapshots make a run reproducible offline and free.

  2. In a job search the useful question is "what is NEW since I last looked",
     not "what exists". That needs memory across runs.

Layout:

    data/
      raw/
        2026-11-07/
          remoterocketship.json      one file per source, as fetched
          gethired.json
      seen.json                      url -> first-seen date

Snapshots are the raw normalised dicts, before scoring or ranking, so a
re-run can apply new logic to old data.
"""
import json, shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
DATA_DIR = WORKSPACE / "data"
RAW_DIR = DATA_DIR / "raw"
SEEN_FILE = DATA_DIR / "seen.json"


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _dir_for(date_str):
    return RAW_DIR / date_str


def save_source(source, jobs, date_str=None):
    """
    Write one source's raw results for a date. Overwrites that day.

    Refuses to persist an empty result. A fetcher returning zero is almost
    always a failure — quota exhausted, auth expired, schema drift — and
    caching that zero would serve "no jobs" from this source for the whole
    cache window, turning a transient error into a silent outage.
    """
    if not jobs:
        return None
    date_str = date_str or _today()
    d = _dir_for(date_str)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_slug(source)}.json"
    payload = {
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(jobs),
        "jobs": [_jsonable(j) for j in jobs],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_source(source, date_str=None):
    """Read one source's snapshot for a date, or None."""
    date_str = date_str or _today()
    path = _dir_for(date_str) / f"{_slug(source)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_fresh(source, max_age_hours):
    """
    Most recent snapshot for `source` newer than max_age_hours, else None.

    max_age_hours <= 0 disables reuse, which is what free sources want:
    re-fetching them costs nothing and keeps results current.
    """
    if not max_age_hours or max_age_hours <= 0:
        return None
    if not RAW_DIR.exists():
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=float(max_age_hours))
    for day in sorted(RAW_DIR.iterdir(), reverse=True):
        if not day.is_dir():
            continue
        path = day / f"{_slug(source)}.json"
        if not path.exists():
            continue
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(blob.get("fetched_at", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts >= cutoff:
            # Belt and braces: an empty snapshot from an older version of this
            # file must never stand in for a live fetch.
            return blob if blob.get("jobs") else None
        return None          # newest is stale; older ones are staler
    return None


def load_latest_any(source, max_age_days=14):
    """
    Newest non-empty snapshot for `source` at any age within max_age_days.

    This is the fallback for when a live fetch fails — quota exhausted, API
    down, key expired. Yesterday's paid listings are far more useful than an
    empty result, and job postings stay open for weeks, so slightly stale data
    is still actionable. The caller is responsible for saying it is stale.
    """
    if not RAW_DIR.exists():
        return None
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    for day in sorted(RAW_DIR.iterdir(), reverse=True):
        if not day.is_dir() or day.name < cutoff:
            continue
        path = day / f"{_slug(source)}.json"
        if not path.exists():
            continue
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if blob.get("jobs"):
            return blob
    return None


def load_all_sources(date_str=None):
    """Every snapshot for a date, flattened. Used by --offline."""
    date_str = date_str or _today()
    d = _dir_for(date_str)
    if not d.exists():
        return []
    jobs = []
    for path in sorted(d.glob("*.json")):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            jobs.extend(blob.get("jobs", []))
        except Exception:
            continue
    return jobs


def latest_snapshot_date():
    if not RAW_DIR.exists():
        return None
    days = sorted((d.name for d in RAW_DIR.iterdir() if d.is_dir()), reverse=True)
    return days[0] if days else None


# ── "what is new" tracking ───────────────────────────────────────────────────

def load_seen():
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def mark_seen(jobs, seen=None):
    """
    Stamp each job with `is_new` and `first_seen`, then persist.

    A job is new the first time its URL appears. Called once per run after
    fetching, so the report can lead with what you have not triaged yet.
    """
    seen = load_seen() if seen is None else seen
    today = _today()
    new_count = 0
    for j in jobs:
        url = (j.get("url") or "").strip()
        if not url:
            j["is_new"], j["first_seen"] = False, today
            continue
        if url in seen:
            j["is_new"] = False
            j["first_seen"] = seen[url]
        else:
            seen[url] = today
            j["is_new"] = True
            j["first_seen"] = today
            new_count += 1
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
    return new_count


# ── housekeeping ─────────────────────────────────────────────────────────────

def prune(keep_days=30):
    """Drop snapshot folders older than keep_days. Descriptions are bulky."""
    if not RAW_DIR.exists():
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    removed = 0
    for day in RAW_DIR.iterdir():
        if day.is_dir() and day.name < cutoff:
            shutil.rmtree(day, ignore_errors=True)
            removed += 1
    return removed


def stats():
    """Summary for the CLI: what is on disk and roughly how big."""
    if not RAW_DIR.exists():
        return {"days": 0, "files": 0, "jobs": 0, "mb": 0.0}
    days = [d for d in RAW_DIR.iterdir() if d.is_dir()]
    files = [f for d in days for f in d.glob("*.json")]
    total_jobs, size = 0, 0
    for f in files:
        size += f.stat().st_size
        try:
            total_jobs += json.loads(f.read_text(encoding="utf-8")).get("count", 0)
        except Exception:
            pass
    return {"days": len(days), "files": len(files), "jobs": total_jobs,
            "mb": round(size / 1_048_576, 2)}


def _slug(name):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name).lower())


def _jsonable(job):
    """Sets (job_skills) are not JSON-serialisable; keep everything else."""
    return {k: (sorted(v) if isinstance(v, set) else v) for k, v in job.items()}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    s = stats()
    print(f"Snapshots: {s['days']} day(s), {s['files']} file(s), "
          f"{s['jobs']} jobs, {s['mb']} MB")
    latest = latest_snapshot_date()
    if latest:
        print(f"Latest:    {latest}")
        for f in sorted(_dir_for(latest).glob("*.json")):
            try:
                b = json.loads(f.read_text(encoding="utf-8"))
                print(f"  {f.stem:<22}{b.get('count', 0):>5} jobs   {b.get('fetched_at','')[:19]}")
            except Exception:
                pass
    seen = load_seen()
    print(f"Seen URLs: {len(seen)}")
