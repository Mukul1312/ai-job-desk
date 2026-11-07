#!/usr/bin/env python3
"""
scorer.py — Resume parsing + NLP match scoring.
Supports: .docx (Word) and .pdf resume formats.
Fixes:
  - jobs with 0 extractable skills → score=None (N/A)
  - jobs with <3 skill matches → score=None (too little signal)
  - PDF support via pymupdf
"""
import json, zipfile, re
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent / "config"

# A job description must name at least this many recognisable skills before we
# are willing to score it. Below this the "score" is noise: a JD that mentions
# three things you happen to know is not a 100% match, it is an empty JD.
MIN_SKILLS_FOR_SCORING = 5

# Matching this many of a JD's skills counts as full evidence. Tuned so a
# normal, well-written engineering JD (12-25 skills) can reach the top band,
# while a 5-skill stub cannot regardless of how many you match.
FULL_EVIDENCE_MATCHES = 12

def load_skill_keywords(domain):
    with open(CONFIG_DIR / "skill_keywords.json") as f:
        data = json.load(f)
    return [s.lower() for s in data.get(domain, [])]

def extract_resume_text(resume_path):
    """Extract raw text from .docx or .pdf resume."""
    path = Path(resume_path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _extract_docx(path)
    elif suffix == ".pdf":
        return _extract_pdf(path)
    else:
        print(f"  ⚠️  Unsupported resume format: {suffix}. Supported: .docx, .pdf")
        return ""

def _extract_docx(path):
    try:
        with zipfile.ZipFile(path) as z:
            with z.open('word/document.xml') as f:
                content = f.read().decode('utf-8')
        text = re.sub(r'<[^>]+>', ' ', content).lower()
        # Normalize multiple spaces/newlines to single space
        text = re.sub(r'\s+', ' ', text)
        # Normalize double spaces from XML splits
        text = re.sub(r' {2,}', ' ', text)
        # Expand pipe-separated lists: "functional | regression | api" 
        # → also emit "functional testing regression testing api testing"
        # by appending " testing" after each pipe-separated QA term
        qa_suffixes = ["testing", "test"]
        import re as _re
        # Find pipe-lists and expand with common suffixes
        def expand_pipe_list(m):
            terms = [t.strip() for t in m.group(0).split("|")]
            expanded = []
            for suffix in qa_suffixes:
                expanded.extend([f"{t} {suffix}" for t in terms if t])
            return m.group(0) + " " + " ".join(expanded)
        text = _re.sub(r'[\w\s\-]+(?:\s*\|\s*[\w\s\-]+){2,}', expand_pipe_list, text)
        return text
    except Exception as e:
        print(f"  Resume (.docx) parse error: {e}")
        return ""

def _extract_pdf(path):
    try:
        import fitz
        text = ""
        with fitz.open(str(path)) as doc:
            for page in doc:
                text += page.get_text()
        return text.lower()
    except ImportError:
        print("  ⚠️  pymupdf not installed. Run: pip install pymupdf --break-system-packages")
        return ""
    except Exception as e:
        print(f"  Resume (.pdf) parse error: {e}")
        return ""

def extract_known_skills(resume_text, skill_keywords):
    return {skill for skill in skill_keywords if skill in resume_text}

def extract_job_skills(description, skill_keywords):
    if not description:
        return set()
    text = description.lower()
    return {skill for skill in skill_keywords if skill in text}

def compute_match_score(job_skills, known_skills):
    """
    Fit score 0-100, or None when the JD carries too little signal to judge.

    Two terms, combined as a geometric mean so a job needs BOTH to score well:

      coverage = matched / required
          How much of what they asked for you actually have.

      evidence = matched / FULL_EVIDENCE_MATCHES  (capped at 1.0)
          How much you matched in absolute terms. This is the term the old
          formula lacked, and its absence is why a JD listing three skills you
          happen to know scored 100% and outranked a detailed, genuinely
          relevant role. Coverage alone rewards vague job descriptions.

    Worked example — the Pinterest marketing role in the old queue:
        job_skills={sql, python, java}, all three matched
        old: 3/3                        -> 100
        new: sqrt(1.00 * 3/12)          ->  50
    versus a real AI engineering JD listing 20 skills, 14 of them matched:
        old: 14/20                      ->  70
        new: sqrt(0.70 * 1.00)          ->  84
    """
    if len(job_skills) < MIN_SKILLS_FOR_SCORING:
        return None
    matched = len(job_skills & known_skills)
    if matched == 0:
        return 0
    coverage = matched / len(job_skills)
    evidence = min(matched / FULL_EVIDENCE_MATCHES, 1.0)
    return round(100 * (coverage * evidence) ** 0.5)

def _title_has(title_lower, keyword):
    """
    Keyword test that does not fire on fragments.

    Short keywords must match as whole words: a plain substring test for "ai"
    hits "Maintenance", "Trainer" and "Captain", which quietly readmits every
    job the positive gate was meant to exclude.
    """
    kw = keyword.lower().strip()
    if not kw:
        return False
    # Single words match on word boundaries, multi-word phrases as substrings.
    # Length is the wrong test: "agent" is five characters and still matches
    # inside "Magento"; "ai" matches inside "Maintenance".
    if " " not in kw:
        return re.search(r'\b' + re.escape(kw) + r'\w*\b', title_lower) is not None
    return kw in title_lower


def is_title_relevant(title, domain_keywords, exclude_profile_keywords=None,
                      require_title_keywords=None):
    """
    Filter out irrelevant jobs by title.

    require_title_keywords is the positive gate: the title must actually name
    the kind of role you want. Without it, any JD that merely *mentions* your
    keywords qualifies — which is how a staffing agency's "Senior Magento
    Developer" ends up at the top of an AI-agent search, because its JD lists
    twenty technologies and happens to say "AI" once.
    """
    title_lower = title.lower()
    # Profile-level hard excludes (e.g. devops, fullstack, platform engineer)
    if exclude_profile_keywords:
        for kw in exclude_profile_keywords:
            if kw.lower() in title_lower:
                return False
    if require_title_keywords:
        if not any(_title_has(title_lower, kw) for kw in require_title_keywords):
            return False
    # Always exclude these regardless of domain
    exclude_titles = [
        # Sales / Marketing / Finance / Legal / HR
        "sales executive", "sales manager", "marketing manager",
        "account executive", "account manager", "business development",
        "finance", "accounting", "legal", "hr ", "recruiter",
        "paid media", "retail", "customer success", "customer service",
        "site contracts", "bilingual", "controller", "copywriter",
        "content writer", "social media", "seo specialist", "brand manager",
        # Product / Project / Business
        "product manager", "program manager", "project manager",
        "business analyst", "scrum master", "agile coach",
        "operations manager", "technical writer", "documentation",
        # Entry level / Intern
        "intern,", "internship", "entry level", "entry-level",
        "junior ", "graduate ", "trainee", "fresh graduate",
        "0-2 years", "1-2 years", "no experience",
        # Other non-engineering
        "designer", "ux researcher", "data entry", "virtual assistant"
    ]
    if any(ex in title_lower for ex in exclude_titles):
        return False
    return True

def score_jobs(jobs, domain, resume_path, profile=None, profile_meta=None):
    skill_keywords = load_skill_keywords(domain)
    resume_text = extract_resume_text(resume_path)

    if not resume_text:
        print("  ⚠️  Resume text empty — scoring disabled")
        for job in jobs:
            job["job_skills"] = set()
            job["match_score"] = None
        return jobs, set()

    known_skills = extract_known_skills(resume_text, skill_keywords)
    print(f"  Known skills from resume: {len(known_skills)}")

    # Title relevance filter
    domain_keywords = skill_keywords
    before = len(jobs)
    exclude_profile_keywords = (profile or {}).get("exclude_title_keywords", [])
    # Positive gate, opt-in via the domain profile. Dramatically raises
    # precision: the title must name the role, not merely mention the tech.
    require = None
    if (profile or {}).get("require_title_match", True):
        require = (profile_meta or {}).get("title_keywords") or None
    jobs = [j for j in jobs
            if is_title_relevant(j.get("title", ""), domain_keywords,
                                 exclude_profile_keywords, require)]
    filtered = before - len(jobs)
    if filtered:
        print(f"  Title filter: removed {filtered} irrelevant jobs")

    for job in jobs:
        job_skills = extract_job_skills(job.get("description", ""), skill_keywords)
        job["job_skills"] = job_skills
        job["match_score"] = compute_match_score(job_skills, known_skills)

    # Sort: scored high→low, then unscored at bottom
    scored = sorted(
        [j for j in jobs if j["match_score"] is not None],
        key=lambda x: x["match_score"], reverse=True
    )
    unscored = [j for j in jobs if j["match_score"] is None]

    return scored + unscored, known_skills
