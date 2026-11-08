#!/usr/bin/env python3
"""
track.py — the application tracker, replacing the Notion Role Opportunities database.

Built because the Notion version recorded 65 researched roles and zero applications.
Every row said "Not Applied", no row ever had an applied date, and by the time anyone
looked again both of the two roles rated Strong had closed. The research was right and
the applying never happened, so this tool is built around that failure rather than
around cataloguing.

Three consequences of that, visible in the commands:

  * `due` is the default. Running the tool with no arguments answers "what should I do
    today", not "what have I collected".
  * Every tracked role has an age. A role sitting at `interested` for weeks is the
    thing that went wrong last time, so it is surfaced, loudly, rather than filed.
  * `applied` is the only status that needs a date, and the tool refuses to let a role
    reach it without one.

    python tools/track.py                      # what needs doing today
    python tools/track.py --import-seed        # pull in the 65 researched roles
    python tools/track.py --from-report 5      # add the top 5 of today's pipeline
    python tools/track.py --add <url> --company X --role Y
    python tools/track.py --set <id> applied
    python tools/track.py --list [--status s]
    python tools/track.py --stats

Statuses, in order: interested -> applied -> screening -> interviewing -> offer,
with rejected / closed / dropped as terminal. `closed` means the posting died before
you applied, which is the outcome this tool exists to prevent.
"""

import json
import re
import sys
from datetime import datetime, timezone, date
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
STORE = WORKSPACE / "applications.json"
SEED = WORKSPACE / "data" / "roles-seed-notion.json"

ACTIVE = ["interested", "applied", "screening", "interviewing", "offer"]
TERMINAL = ["rejected", "closed", "dropped"]
STATUSES = ACTIVE + TERMINAL

# How long a role may sit in a status before it is nagging you about it.
STALE_DAYS = {"interested": 3, "applied": 14, "screening": 10, "interviewing": 10, "offer": 7}


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _days(iso):
    if not iso:
        return None
    try:
        return (date.fromisoformat(str(iso)[:10]) - date.today()).days * -1
    except ValueError:
        return None


def load():
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  applications.json is unreadable ({e}); refusing to overwrite it.")
            raise SystemExit(1)
    return {"_comment": "Application tracker. Personal - gitignored.", "applications": []}


def save(db):
    STORE.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")


def mkid(company, role):
    s = f"{company}-{role}".lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:48]


def find(db, needle):
    """Substring match on the id or either url, so you can paste what you have.

    apply_url is searched too because after clicking through to a form, the
    apply url is what is in the address bar - and being unable to mark a role
    applied with the url you are looking at is exactly the friction that keeps
    the tracker empty.
    """
    needle = needle.lower()
    hits = [a for a in db["applications"]
            if needle in a["id"]
            or needle in (a.get("url") or "").lower()
            or needle in (a.get("apply_url") or "").lower()]
    return hits


def add(db, *, company, role, url="", apply_url="", source="manual", fit="",
        notes="", status="interested"):
    """
    `url` is the LISTING url and is the join key to everything else.

    The pipeline, the resolver cache and data/seen.json all key on the listing
    url, and web/public/data/jobs-*.json uses it verbatim as a job's id. Storing
    anything else here severs the tracker from all of them - which is exactly
    what happened: this function used to be called with `applyUrl or url`, and
    only 2 of 66 rows could be matched back to the pipeline as a result.

    `apply_url` is where the form actually lives. Keep it, because it is what
    you click - but never key on it. One listing can resolve to several apply
    urls over its life (a board switches ATS, a redirect changes), while the
    listing url is what both sides already agree on.
    """
    aid = mkid(company, role)
    for a in db["applications"]:
        if a["id"] == aid:
            return a, False
    rec = {
        "id": aid, "company": company, "role": role, "url": url,
        "apply_url": apply_url,
        "source": source, "fit": fit, "status": status,
        "added": _today(), "status_changed": _today(),
        "applied_on": None, "notes": notes, "history": [f"{_today()} added as {status}"],
    }
    db["applications"].append(rec)
    return rec, True


def set_status(db, needle, status, note=""):
    if status not in STATUSES:
        print(f"  unknown status {status!r}. One of: {', '.join(STATUSES)}")
        return 1
    hits = find(db, needle)
    if len(hits) != 1:
        print(f"  {len(hits)} matches for {needle!r}" if hits else f"  no match for {needle!r}")
        for h in hits[:8]:
            print(f"    {h['id']}")
        return 1
    a = hits[0]
    old = a["status"]
    a["status"] = status
    a["status_changed"] = _today()
    # An applied date is the whole point of the tracker; never let it be implied.
    if status == "applied" and not a["applied_on"]:
        a["applied_on"] = _today()
    a["history"].append(f"{_today()} {old} -> {status}" + (f" ({note})" if note else ""))
    if note:
        a["notes"] = (a.get("notes", "") + " | " + note).strip(" |")
    print(f"  {a['id']}: {old} -> {status}")
    return 0


def cmd_due(db):
    """What needs doing. The default, deliberately."""
    rows = [a for a in db["applications"] if a["status"] in ACTIVE]
    if not rows:
        print("Nothing tracked. Try --import-seed or --from-report N.")
        return 0

    overdue, fine = [], 0
    for a in rows:
        age = _days(a["status_changed"]) or 0
        if age >= STALE_DAYS.get(a["status"], 14):
            overdue.append((age, a))
        else:
            fine += 1
    overdue.sort(key=lambda t: -t[0])

    interested = [a for a in rows if a["status"] == "interested"]
    print(f"\n{len(rows)} active · {len(interested)} not yet applied · {len(overdue)} need attention\n")

    if interested:
        print("NOT YET APPLIED — this is the one that cost you two Strong targets")
        for a in sorted(interested, key=lambda a: -(_days(a["added"]) or 0))[:12]:
            age = _days(a["added"]) or 0
            flag = "  <-- " + ("STALE" if age >= 3 else "") if age >= 3 else ""
            print(f"  {age:>3}d  {a['company'][:22]:<22} {a['role'][:40]:<40}{flag}")
            if a.get("url"):
                print(f"        {a['url'][:92]}")
        print()

    chase = [(d, a) for d, a in overdue if a["status"] != "interested"]
    if chase:
        print("WAITING TOO LONG — chase or close")
        for d, a in chase[:10]:
            print(f"  {d:>3}d in {a['status']:<12} {a['company'][:22]:<22} {a['role'][:34]}")
        print()
    if fine:
        print(f"({fine} tracked and still within their window)")
    return 0


def cmd_stats(db):
    rows = db["applications"]
    if not rows:
        print("Nothing tracked yet.")
        return 0
    from collections import Counter
    c = Counter(a["status"] for a in rows)
    print(f"\n{len(rows)} tracked\n")
    for s in STATUSES:
        if c.get(s):
            print(f"  {c[s]:>4}  {s}")
    applied = [a for a in rows if a["applied_on"]]
    print(f"\n  applications actually sent: {len(applied)}")
    if applied:
        print(f"  most recent: {max(a['applied_on'] for a in applied)}")
    else:
        print("  most recent: never")
        print("\n  The Notion database this replaced ended the same way: 65 researched, 0 sent.")
    closed = [a for a in rows if a["status"] == "closed"]
    if closed:
        print(f"\n  {len(closed)} closed before you applied - the cost of waiting")
    return 0


def cmd_list(db, status=None):
    rows = [a for a in db["applications"] if not status or a["status"] == status]
    rows.sort(key=lambda a: (STATUSES.index(a["status"]), a["company"]))
    for a in rows:
        age = _days(a["status_changed"])
        print(f"  [{a['status']:<12}] {str(age) + 'd':>5}  {a['company'][:22]:<22} "
              f"{a['role'][:42]:<42} {a['id']}")
    print(f"\n  {len(rows)} shown")
    return 0


def cmd_import_seed(db):
    if not SEED.exists():
        print(f"  no seed at {SEED}")
        return 1
    data = json.loads(SEED.read_text(encoding="utf-8"))
    added = skipped = 0
    for r in data["roles"]:
        if not r.get("company") or not r.get("role"):
            continue
        # Anything Notion already knew was dead comes in as closed, not interested -
        # importing 13 dead roles as live work would be the wrong kind of backlog.
        was_closed = "Closed" in (r.get("status") or "") or not r.get("live_at_verification")
        status = "closed" if was_closed else "interested"
        rec, is_new = add(
            db, company=r["company"], role=r["role"], url=r.get("url", ""),
            source="notion-seed-2026-09", fit=r.get("fit", ""), status=status,
            notes=f"{r.get('location','')} · {r.get('experience_req','')} · {r.get('compensation','')}".strip(" ·"),
        )
        if is_new:
            rec["added"] = r.get("date_added") or rec["added"]
            rec["history"] = [f"{rec['added']} researched in Notion as {r.get('fit','')}"]
            if status == "closed":
                rec["history"].append("2026-11-08 imported already closed - never applied to")
            added += 1
        else:
            skipped += 1
    print(f"  imported {added}, skipped {skipped} already present")
    print("  NOTE: these urls were last verified 2026-09-05. Re-check before applying.")
    return 0


def cmd_from_report(db, n):
    """Pull the top N of today's pipeline straight into the tracker."""
    web = sorted((WORKSPACE / "web" / "public" / "data").glob("jobs-*.json"), reverse=True)
    if not web:
        print("  no exported pipeline data found - run src/main.py first")
        return 1
    data = json.loads(web[0].read_text(encoding="utf-8"))
    jobs = sorted(data["jobs"], key=lambda j: -(j.get("score") or 0))[:n]
    added = 0
    for j in jobs:
        _, is_new = add(db, company=j.get("company", "?"), role=j.get("title", "?"),
                        url=j.get("url", ""),
                        apply_url=j.get("applyUrl", ""),
                        source=f"pipeline {web[0].stem[-10:]}",
                        fit=f"score {j.get('score')}",
                        notes=f"{j.get('location','')} · resume: {j.get('resume','')} · via {j.get('applyKind','?')}")
        added += is_new
    print(f"  added {added} of the top {n} from {web[0].name}")
    return 0


def main(argv):
    db = load()
    a = argv[1:]

    if "--import-seed" in a:
        r = cmd_import_seed(db); save(db); return r
    if "--from-report" in a:
        i = a.index("--from-report")
        n = int(a[i + 1]) if len(a) > i + 1 and a[i + 1].isdigit() else 10
        r = cmd_from_report(db, n); save(db); return r
    if "--add" in a:
        i = a.index("--add")
        url = a[i + 1] if len(a) > i + 1 else ""
        get = lambda f, d="": a[a.index(f) + 1] if f in a and len(a) > a.index(f) + 1 else d
        rec, is_new = add(db, company=get("--company", "?"), role=get("--role", "?"),
                          url=url, notes=get("--notes"))
        print(f"  {'added' if is_new else 'already tracked'}: {rec['id']}")
        save(db); return 0
    if "--set" in a:
        i = a.index("--set")
        if len(a) < i + 3:
            print("  usage: --set <id-or-url-fragment> <status> [note]")
            return 2
        note = a[i + 3] if len(a) > i + 3 else ""
        r = set_status(db, a[i + 1], a[i + 2], note); save(db); return r
    if "--stats" in a:
        return cmd_stats(db)
    if "--list" in a:
        st = a[a.index("--status") + 1] if "--status" in a else None
        return cmd_list(db, st)

    return cmd_due(db)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
