#!/usr/bin/env python3
"""
report.py — Writes the ranked shortlist you actually read.

Every job shows WHY it ranked where it did: skill fit, timezone overlap,
normalised salary, whether the company can employ you in India, and which of
your resume variants to send. The point is a list you can act on in one pass,
not a wall of links.
"""
import datetime
import re

# Job text comes from third-party boards and lands in a file you later paste
# into an LLM. Strip instruction-shaped content and cap field lengths.
_INJECTION = re.compile(
    r'(?i)(ignore (?:all )?previous|disregard (?:all )?prior|new instructions?|'
    r'system prompt|forget (?:everything|all)|override|</?(?:system|assistant)>)')


def sanitize(text, limit=300):
    if not text:
        return ""
    return _INJECTION.sub("[removed]", str(text))[:limit]


def _bar(score, width=12):
    filled = max(0, min(width, round(score / 100 * width)))
    return "#" * filled + "." * (width - filled)


def _india_badge(india):
    return {"confirmed": "CONFIRMED", "likely": "LIKELY"}.get(
        (india or {}).get("confidence"), "unknown")


def generate_report(jobs, gap_analysis, profile_config, profile_meta,
                    output_path, stats=None, limit=40, unscored=None):
    today = datetime.date.today().strftime("%a, %d %b %Y")
    tz_cfg = profile_config.get("timezone", {})
    sal_cfg = profile_config.get("salary", {})
    variants = profile_config.get("resume_variants", {})

    L = [
        f"# Job Report — {today}",
        "",
        f"**Profile:** {profile_meta.get('label','')} · "
        f"{profile_config.get('name','')}",
        f"**Window:** {tz_cfg.get('available_from')}–{tz_cfg.get('available_to')} "
        f"{tz_cfg.get('home_tz','')} "
        f"(min {tz_cfg.get('min_overlap_hours')}h overlap)",
        f"**Salary floor:** {sal_cfg.get('min_lpa')} LPA",
        "",
    ]

    if stats:
        L += [
            f"**Pipeline:** {stats['total']} scored → **{stats['kept']} ranked**  ",
            f"dropped — {stats['dropped_unscored']} JD too thin to score, "
            f"{stats['dropped_timezone']} timezone, {stats['dropped_salary']} below floor",
            "",
        ]
        if stats.get("by_region"):
            reg = " · ".join(f"{k} {v}" for k, v in
                             sorted(stats["by_region"].items(), key=lambda x: -x[1]))
            L.append(f"**Regions:** {reg}")
        if stats.get("by_india"):
            ind = " · ".join(f"{k} {v}" for k, v in
                             sorted(stats["by_india"].items(), key=lambda x: -x[1]))
            L.append(f"**India-capable:** {ind}")
        L.append("")

    new_n = sum(1 for j in jobs if j.get("is_new"))
    L += ["---", "",
          f"## Shortlist — {len(jobs)} ranked, {new_n} new since last run", ""]

    if not jobs:
        L += ["_Nothing cleared the filters today._", "",
              "Try lowering `timezone.min_overlap_hours` or `salary.min_lpa` "
              "in your profile.", ""]
    else:
        L += ["| # | New | Role | Company | Score | Overlap | Salary | India | Resume |",
              "|---|---|---|---|---|---|---|---|---|"]
        for i, j in enumerate(jobs[:limit], 1):
            tz = j.get("tz") or {}
            oh = tz.get("overlap_hours")
            lpa = (j.get("salary_lpa") or {}).get("lpa")
            L.append(
                f"| {i} | {'NEW' if j.get('is_new') else ''} "
                f"| {sanitize(j.get('title',''), 60)} "
                f"| {sanitize(j.get('company',''), 28)} "
                f"| {j.get('final_score','')} "
                f"| {'—' if oh is None else f'{oh}h'} "
                f"| {'—' if lpa is None else f'{lpa} LPA'} "
                f"| {_india_badge(j.get('india'))} "
                f"| `{j.get('resume','?')}` |")
        L.append("")

    L += ["---", "", "## Detail", ""]
    for i, j in enumerate(jobs[:limit], 1):
        tz = j.get("tz") or {}
        sal = j.get("salary_lpa") or {}
        india = j.get("india") or {}
        score = j.get("final_score", 0)

        L.append(f"### {i}. {sanitize(j.get('title',''), 90)}")
        L.append(f"**{sanitize(j.get('company',''), 50)}** · "
                 f"{sanitize(j.get('source',''), 30)}")
        L.append("")
        L.append(f"`{_bar(min(score, 100))}` **{score}**  "
                 f"(skill fit {j.get('match_score','?')})")
        L.append("")

        oh = tz.get("overlap_hours")
        L.append(f"- **Timezone** — {tz.get('note','unknown')} "
                 f"({tz.get('verdict','?')}, region `{tz.get('region','?')}`)")
        if sal.get("lpa") is not None:
            L.append(f"- **Salary** — {sal['low_lpa']}–{sal['high_lpa']} LPA "
                     f"(from `{sanitize(sal.get('raw',''), 40)}`"
                     f"{', ' + sal['currency'] if sal.get('currency') else ''})")
        else:
            L.append("- **Salary** — not stated")
        badge = _india_badge(india)
        ev = india.get("evidence") or []
        L.append(f"- **Can hire in India** — {badge}"
                 + (f": {'; '.join(sanitize(e, 80) for e in ev[:3])}" if ev else
                    " — no public evidence either way (an EOR leaves no trace)"))
        L.append(f"- **Send** — `{j.get('resume','?')}` "
                 f"→ `{variants.get(j.get('resume',''), 'n/a')}`")
        if j.get("location"):
            L.append(f"- **Location** — {sanitize(j['location'], 70)}")
        if j.get("posted"):
            L.append(f"- **Posted** — {sanitize(j['posted'], 20)}")
        if j.get("first_seen"):
            L.append(f"- **First seen** — {sanitize(j['first_seen'], 12)}"
                     + ("  **(new today)**" if j.get("is_new") else ""))
        L.append(f"- **Apply** — {sanitize(j.get('url',''), 300)}")
        if j.get("rank_reasons"):
            L.append(f"- <sub>{' · '.join(j['rank_reasons'])}</sub>")
        L.append("")

    # ── relevant but ungradeable ──
    if unscored:
        L += ["---", "", "## Worth a look — too little JD text to score", ""]
        L += [f"_{len(unscored)} roles whose titles match your domain and which "
              "clear your timezone filter, but whose descriptions are not public "
              "(gethired hides them behind a login), so no fit score was possible. "
              "Most of your Indian supply lands here._", ""]
        L += ["| Role | Company | Location | Posted | Resume | Link |",
              "|---|---|---|---|---|---|"]
        for j in unscored[:limit]:
            L.append(
                f"| {sanitize(j.get('title',''), 55)} "
                f"| {sanitize(j.get('company',''), 26)} "
                f"| {sanitize(j.get('location',''), 32)} "
                f"| {sanitize(j.get('posted',''), 12)} "
                f"| `{j.get('resume','?')}` "
                f"| {sanitize(j.get('url',''), 200)} |")
        L.append("")

    # ── skill gaps ──
    L += ["---", "", "## Skill Gaps", ""]
    if gap_analysis.get("top_missing"):
        L.append(f"_From {gap_analysis.get('jobs_analysed', 0)} job descriptions._")
        L.append("")
        for label, key in (("Quick wins (days)", "quick_wins"),
                           ("Medium (weeks)", "medium"),
                           ("Long term (months)", "long_term")):
            if gap_analysis.get(key):
                L.append(f"**{label}:** " + ", ".join(
                    f"{s.title()} ({c})" for s, c in gap_analysis[key]))
        L.append("")
    else:
        L += ["_No significant gaps._", ""]

    report = "\n".join(L)
    # encoding is explicit: the default on Windows is cp1252 and this file
    # contains characters it cannot represent.
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"  Report written to {output_path}")
    return report
