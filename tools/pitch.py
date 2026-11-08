#!/usr/bin/env python3
"""
pitch.py - turn a job description into the points to make for THAT company.

`facts.py --match-job` answers "which facts are relevant". This answers the
question that follows and decides an application:

    which resume, which three points to lead with, and what NOT to claim.

Built because the pipeline's own resume pick was wrong twice running - it reads
the job TITLE, so a cross-functional role called "Software Engineer" gets the
SWE resume even when the posting is about evaluation and agents. Measuring the
variants against the posting's own words beat the title both times.

    python tools/pitch.py <jd-file>
    python tools/pitch.py <jd-file> --company Streamhub --role "Full-Stack Engineer"
    python tools/pitch.py <jd-file> --write      # save into applications/<slug>/

The three sections, and why each exists:

  LEAD WITH   The facts that match, strongest first, with the bullet text ready
              to paste. Ranked by match AND by strength, because a fact that
              matches on one keyword and carries no number is worth less in a
              cover letter than one that matches on three and carries a measured
              figure.

  GAPS        Technical terms the posting uses that appear NOWHERE in facts/.
              This is the part no other tool here does. It is not a list of
              things to go and learn - it is a list of things to be ready to say
              "no, but" about, and to not quietly imply on a resume.

  DO NOT      Claims already ruled out in facts/ - `do_not_use`, any
              `_DO_NOT_CLAIM_*` field, and the know_before_claiming entries that
              name a colleague's work. These are the ones that end an interview,
              so they print on every pitch whether or not the posting asks.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
FACTS = WORKSPACE / "facts"
RESUME_DIR = Path("C:/Users/Mukul/Desktop/job-upgrade")
VARIANTS = {"ai": "Mukul_Dubey_Resume_AI", "ml": "Mukul_Dubey_Resume_ML",
            "swe": "Mukul_Dubey_Resume_SWE", "fde": "Mukul_Dubey_Resume_FDE",
            "frontend": "Mukul_Dubey_Resume_Frontend"}

STOP = set("""a an the and or but if then else for of to in on at by with without from into over under
about as is are was were be been being do does did doing have has had having will would can could should
may might must shall this that these those it its you your we our they their he she his her them us
not no yes so such own same than too very just also more most other some any each both few own
work working works job role team teams company companies product products platform platforms
experience experienced year years our you'll we're what who how why when where which while
build building built ship shipping shipped own owning owned design designing designed
strong good great excellent ability able comfortable familiar hands-on end-to-end
new like including include includes well across within using use used using""".split())

# Words that look like technology or practice names rather than prose. A term is
# only reported as a gap if it clears this bar, because "collaborative" being
# absent from facts/ is not a gap, and "Playwright" being absent is.
TECHY = re.compile(r"""^(?:
    [A-Z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*     # camelCase / PascalCase runs: ClickHouse, PostgreSQL
  | [a-zA-Z]+\.(?:js|py|ts|io|ai|sh)       # node.js, next.js
  | [a-zA-Z]{2,}\d+                        # k6, s3, ec2
  | (?:[A-Z]{2,})                          # ETL, SQL, API, CI, RAG, MCP
)$""", re.X)

KNOWN_TECH = set("""playwright cypress selenium jmeter gatling snowflake dbt airflow kafka spark
kubernetes docker terraform ansible grafana prometheus datadog sentry vllm ray temporal dspy litellm
pytorch tensorflow huggingface transformers langchain llamaindex pgvector postgres postgresql mysql
mongodb redis elasticsearch clickhouse bigquery redshift databricks sagemaker bedrock vertex
react vue angular svelte next nuxt remix django flask fastapi rails spring graphql grpc
aws azure gcp vercel netlify cloudflare heroku supabase firebase
typescript javascript python java scala golang rust ruby php kotlin swift elixir
pandas numpy scikit sklearn jupyter mlflow wandb dagster prefect
openapi swagger protobuf avro parquet
jenkins circleci github gitlab argocd helm istio
webflow hubspot salesforce marketo segment ga4 gtm mixpanel amplitude posthog""".split())


def load_projects():
    out = []
    for f in sorted(FACTS.glob("*.json")):
        try:
            out.append((f.name, json.loads(f.read_text(encoding="utf-8"))))
        except Exception as e:
            print(f"  !! {f.name}: {e}", file=sys.stderr)
    return out


def tokens(text):
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,}", (text or "").lower())
            if w not in STOP and len(w) > 2}


def fact_text(fact):
    parts = [fact.get("bullet", ""), fact.get("short", ""), fact.get("detail", ""),
             fact.get("headline", ""), " ".join(fact.get("keywords", [])),
             " ".join(fact.get("metrics", []))]
    return " ".join(p for p in parts if p)


def score_fact(fact, want):
    """Matched terms, weighting an explicit keyword above an incidental word."""
    kw = {k.lower() for k in fact.get("keywords", [])}
    body = tokens(fact_text(fact))
    hits_kw = sorted({k for k in kw if k in want or any(t in want for t in k.split())})
    hits_body = sorted((body & want) - set(hits_kw))
    score = 3 * len(hits_kw) + len(hits_body)
    if fact.get("strength") == "high":
        score += 3
    if fact.get("metrics"):
        score += 2
    return score, hits_kw, hits_body


def pick_resume(jd_text, projects=None):
    """Measure each variant against the posting. The title is not consulted.

    Ties are broken by how well the FACTS tagged to each tier match the posting,
    not alphabetically. Without that, a Voice AI Engineer role that tied 13-13
    between `swe` and `ai` picked `swe`, because "swe" sorts above "ai" - an
    arbitrary answer dressed up as a measurement.
    """
    want = tokens(jd_text)
    tier_fact_score = {}
    if projects:
        for _, proj in projects:
            for fact in proj["facts"]:
                sc, _, _ = score_fact(fact, want)
                for t in fact.get("tiers", []):
                    tier_fact_score[t] = tier_fact_score.get(t, 0) + sc
    out = []
    for tier, stem in VARIANTS.items():
        p = RESUME_DIR / f"{stem}.docx"
        if not p.exists():
            continue
        try:
            import zipfile
            xml = zipfile.ZipFile(p).read("word/document.xml").decode("utf-8")
        except Exception:
            continue
        text = " ".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, re.S))
        out.append((len(tokens(text) & want), tier_fact_score.get(tier, 0), tier))
    out.sort(reverse=True)
    return out


def find_gaps(jd_raw, projects):
    """Terms the posting leans on that appear nowhere in facts/.

    The corpus is tokenised at a 2-character floor rather than tokens()'s 3,
    because the whole point here is short technical acronyms - AI, CI, CD, UI,
    ML, QA. With the usual floor they can never match anything and every one of
    them is reported as a gap, which was the first version's behaviour and made
    the section untrustworthy.
    """
    blob = " ".join(fact_text(f) + " " + json.dumps(p)
                    for _, p in projects for f in p["facts"]).lower()
    corpus = set(re.findall(r"[a-z][a-z0-9+#.\-]{1,}", blob))
    gaps = Counter()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}", jd_raw):
        low = raw.lower().strip(".,;:")
        if low in STOP or len(low) < 2 or low in corpus:
            continue
        if low in KNOWN_TECH or TECHY.match(raw):
            gaps[raw if TECHY.match(raw) else low] += 1
    return gaps.most_common(18)


def collect_warnings(projects):
    out = []
    for name, p in projects:
        for item in p.get("do_not_use", []):
            out.append(("DO NOT USE", name, item.get("claim", ""), item.get("why", "")))
        for k, v in p.items():
            if k.startswith("_DO_NOT_CLAIM") and isinstance(v, dict):
                out.append(("DO NOT CLAIM", name, v.get("claim", ""), v.get("measured", "")))
        for item in (p.get("know_before_claiming") or {}).get("items", []):
            out.append(("SOMEONE ELSE'S", name, item.get("what", "")[:90],
                        f"written by {item.get('who_wrote_it', '?')}"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jd", help="file containing the job description text")
    ap.add_argument("--company", default="")
    ap.add_argument("--role", default="")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--write", action="store_true",
                    help="save to applications/<company>-<role>/pitch.md")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    jd_raw = Path(a.jd).read_text(encoding="utf-8", errors="replace")
    want = tokens(jd_raw)
    projects = load_projects()

    L = []
    w = L.append
    title = " - ".join(x for x in (a.company, a.role) if x) or Path(a.jd).stem
    w(f"# Pitch: {title}\n")
    w(f"Generated by `tools/pitch.py` from {len(projects)} project file(s). "
      f"The posting contributed {len(want)} distinct terms.\n")

    # resume
    ranked = pick_resume(jd_raw, projects)
    if ranked:
        best = ranked[0]
        w("## Resume to send\n")
        w(f"**{VARIANTS[best[2]]}.pdf**  ({best[2]})\n")
        w("| variant | terms shared | matching facts in this tier |")
        w("|---|---|---|")
        for n, fs, tier in ranked:
            mark = "  **<- send this**" if tier == best[2] else ""
            w(f"| {tier} | {n} | {fs}{mark} |")
        w("")
        w("Measured against the posting's own words. The job title is deliberately "
          "not consulted - it mispicked twice when it was.\n")

    # lead with
    scored = []
    for name, proj in projects:
        for fact in proj["facts"]:
            s, kw, body = score_fact(fact, want)
            if s > 0:
                scored.append((s, kw, body, proj, fact))
    scored.sort(key=lambda t: -t[0])

    w("## Lead with\n")
    if not scored:
        w("_Nothing in `facts/` matches this posting. That is itself the finding._\n")
    for s, kw, body, proj, fact in scored[:a.top]:
        w(f"### {fact['headline']}  `{fact['id']}`  ({fact.get('strength','?')}, score {s})\n")
        w(f"{fact['bullet']}\n")
        if fact.get("metrics"):
            w(f"- numbers: {', '.join(fact['metrics'][:4])}")
        if kw:
            w(f"- matched their vocabulary: {', '.join(kw[:8])}")
        if fact.get("attribution") != "mine":
            w(f"- **attribution: {fact.get('attribution')}** - know the boundary before leading with this")
        w("")

    # gaps
    gaps = find_gaps(jd_raw, projects)
    w("## Gaps - they ask, `facts/` has nothing\n")
    if gaps:
        w("Not a study list. These are the things to be ready to say "
          "\"no, but here is the nearest thing I have done\" about, and never to "
          "imply on a resume.\n")
        w("| term | times mentioned |")
        w("|---|---|")
        for term, n in gaps:
            w(f"| {term} | {n} |")
    else:
        w("_None found._")
    w("")

    # warnings
    warn = collect_warnings(projects)
    if warn:
        w("## Do not claim\n")
        w("Printed on every pitch regardless of the posting - these are the ones "
          "that end an interview.\n")
        for kind, src, what, why in warn[:14]:
            w(f"- **{kind}** - {what}" + (f"  \n  _{why[:120]}_" if why else ""))
        w("")

    text = "\n".join(L)
    print(text)

    if a.write:
        # Write beside the job description when one was given, so the pitch lands
        # in the folder that already holds the README and the letter. The first
        # version always minted a slug from company+role, which produced a
        # SECOND folder whenever the directory had been named by hand - three
        # duplicate pairs before anyone noticed.
        jd_dir = Path(a.jd).resolve().parent
        if jd_dir.parent.name == "applications":
            d = jd_dir
        else:
            slug = re.sub(r"[^a-z0-9]+", "-",
                          f"{a.company}-{a.role}".lower()).strip("-")[:60] or "pitch"
            d = WORKSPACE / "applications" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "pitch.md").write_text(text, encoding="utf-8")
        print(f"\nwritten to applications/{slug}/pitch.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
