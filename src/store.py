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
import gzip, json, shutil
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


def save_raw(source, records, date_str=None, key="id"):
    """
    Archive the provider's own response, before any normalisation.

    The normalised job shape keeps the dozen fields the pipeline uses today
    and drops the rest — benefits, employmentType, requiredLanguages,
    experienceRequirementsMonthsOfExperience, most of company{}. For a free
    board that is fine; refetching costs nothing. For a paid, quota-capped
    source it means a field nobody thought to keep can only be recovered by
    buying the data a second time.

    Deduplicated by `key` because the same job comes back once per region
    query, and gzipped because full descriptions are bulky and this is
    committed to a git repo daily.
    """
    if not records:
        return None
    date_str = date_str or _today()
    d = _dir_for(date_str)
    d.mkdir(parents=True, exist_ok=True)

    seen, unique = set(), []
    for r in records:
        k = r.get(key) if isinstance(r, dict) else None
        if k is not None:
            if k in seen:
                continue
            seen.add(k)
        unique.append(r)

    path = d / f"{_slug(source)}.raw.json.gz"
    payload = {
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "returned": len(records),
        "unique": len(unique),
        "records": unique,
    }
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def load_raw(source, date_str=None):
    """Read an archived provider response back."""
    date_str = date_str or _today()
    path = _dir_for(date_str) / f"{_slug(source)}.raw.json.gz"
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


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
        if path.name.endswith(".raw.json"):
            continue
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

# ── paid-quota learning log ──────────────────────────────────────────────────
#
# Which regions are worth their quota is an empirical question, and the answer
# will drift as the market and the profile change. Rather than re-deciding it
# from memory every few weeks, every paid request records what it cost and
# what it produced, so the config can be tuned from evidence.

QUOTA_LOG = DATA_DIR / "quota_log.jsonl"


def log_quota(rows):
    """Append one record per region per run. Never raises: logging must not
    be able to fail a run that already spent the quota."""
    if not rows:
        return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with QUOTA_LOG.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({"ts": stamp, "date": _today(), **r},
                                   ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  quota log not written: {e}")


def read_quota_log():
    if not QUOTA_LOG.exists():
        return []
    out = []
    try:
        for line in QUOTA_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def quota_report():
    """
    Per-region: what it cost and what survived to the shortlist.

    `ranked per 100 billed` is the number that should drive the region list.
    A region returning plenty of jobs that never rank is paying for noise;
    one returning few that nearly all rank is earning its place.
    """
    rows = read_quota_log()
    if not rows:
        return "  no paid runs logged yet"

    agg = {}
    for r in rows:
        a = agg.setdefault(r.get("region") or "?",
                           {"runs": 0, "billed": 0, "unique": 0, "ranked": 0})
        a["runs"] += 1
        a["billed"] += int(r.get("billed") or 0)
        a["unique"] += int(r.get("unique") or 0)
        a["ranked"] += int(r.get("ranked") or 0)

    lines = [f"  {'region':<24}{'runs':>5}{'billed':>8}{'unique':>8}"
             f"{'ranked':>8}{'dup%':>7}{'ranked/100':>12}"]
    lines.append("  " + "-" * 72)
    for name, a in sorted(agg.items(), key=lambda x: -x[1]["ranked"]):
        dup = 0 if not a["billed"] else round(
            100 * (a["billed"] - a["unique"]) / a["billed"])
        per100 = 0 if not a["billed"] else round(100 * a["ranked"] / a["billed"], 1)
        lines.append(f"  {name[:23]:<24}{a['runs']:>5}{a['billed']:>8}"
                     f"{a['unique']:>8}{a['ranked']:>8}{dup:>6}%{per100:>12}")
    tot = {k: sum(a[k] for a in agg.values()) for k in ("billed", "unique", "ranked")}
    lines.append("  " + "-" * 72)
    lines.append(f"  {'TOTAL':<24}{'':>5}{tot['billed']:>8}{tot['unique']:>8}"
                 f"{tot['ranked']:>8}")
    return "\n".join(lines)


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
    raws = [f for d in days for f in d.glob("*.raw.json.gz")]
    total_jobs, size = 0, 0
    for f in files:
        size += f.stat().st_size
        try:
            total_jobs += json.loads(f.read_text(encoding="utf-8")).get("count", 0)
        except Exception:
            pass
    raw_size = sum(f.stat().st_size for f in raws)
    return {"days": len(days), "files": len(files), "jobs": total_jobs,
            "mb": round(size / 1_048_576, 2),
            "raw_files": len(raws), "raw_mb": round(raw_size / 1_048_576, 2)}


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
    if s.get("raw_files"):
        print(f"Raw API:   {s['raw_files']} archive(s), {s['raw_mb']} MB "
              f"(gzipped provider responses, nothing dropped)")
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
    rows = read_quota_log()
    if rows:
        print()
        print("Paid-source efficiency (cumulative):")
        print(quota_report())
