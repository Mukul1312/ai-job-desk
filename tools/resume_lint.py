#!/usr/bin/env python3
"""
resume_lint.py — check a resume before it goes out, and strip draft placeholders.

Written after a live PDF went out carrying "[Add how often users accepted the
fix.]" in the body. A reviewer reads that as an unedited draft, and no amount of
good content further down recovers it. This is the pre-flight that would have
caught it.

    python tools/resume_lint.py path/to/Resume.docx
    python tools/resume_lint.py path/to/Resume.docx --fix
    python tools/resume_lint.py path/to/Resume.pdf

--fix removes placeholder runs from a .docx and tidies the spacing they leave
behind. It writes in place, so keep a backup; the script refuses to touch a file
Word has open rather than racing a save.

What it checks, and why each one is here rather than being folklore:

  placeholders   Draft markers that must never ship. Fatal.
  buzzwords      Words hiring managers name when asked how they spot generated
                 text ("delve", "leverage", "spearheaded", "results-driven").
  em-dashes      32% of hiring managers in a 2026 Resume Genius survey named
                 them as an AI tell. Not fatal, but worth knowing about.
  structure      Tables, text boxes, images, headers and multi-column layouts.
                 Greenhouse's own docs name columns and contact-in-header as
                 parse failures. Tables are contested - modern parsers mostly
                 cope - so they are reported, not condemned.
  headings       Lever matches sections by heading text, so "My Journey" can
                 cost you the whole Experience section.
  dates          "Month Year" parses; "Jan '22" often does not.
  size           Greenhouse refuses to parse over 2.5 MB, SmartRecruiters 2 MB.
  text layer     A PDF with no extractable text is an image to every parser.
"""

import re
import shutil
import sys
import zipfile
from pathlib import Path

GREY = "9A9A9A"  # the colour the resume template uses for draft notes

RUN_RE = re.compile(r"<w:r(?:\s[^>]*)?>(?:(?!</w:r>).)*?</w:r>", re.S)
TEXT_RE = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.S)
TAG_RE = re.compile(r"<[^>]+>")
PARA_RE = re.compile(r"<w:p(?:\s[^>]*)?>.*?</w:p>", re.S)

BUZZWORDS = [
    "delve", "realm", "intricate", "showcas", "pivotal", "leverag",
    "spearhead", "robust", "seamless", "cutting-edge", "passionate",
    "tapestry", "synerg", "holistic", "meticulous", "testament",
    "underscore", "furthermore", "moreover", "landscape", "navigate",
    "elevate", "empower", "crucial", "dynamic", "innovative",
    "state-of-the-art", "best-in-class", "proven track record",
    "results-driven", "detail-oriented", "team player", "utilize",
    "facilitate", "wealth of experience", "hit the ground running",
]

STANDARD_HEADINGS = {
    "summary", "experience", "work experience", "professional experience",
    "employment", "education", "skills", "technical skills", "projects",
    "publications", "certifications", "awards",
}

# Greenhouse 2.5 MB, SmartRecruiters 2 MB, most others 5 MB.
SIZE_WARN = 2 * 1024 * 1024


def _docx_xml(path):
    with zipfile.ZipFile(path) as z:
        return z.open("word/document.xml").read().decode("utf-8"), z.namelist()


def _plain_text(xml):
    return TAG_RE.sub("", xml.replace("</w:p>", "\n"))


# ─── checks ──────────────────────────────────────────────────────────────────

def find_placeholders(xml):
    """
    Draft markers, found two ways.

    Grey runs are how the template marks them, and that is authoritative. The
    bracket scan is the backstop for a placeholder someone typed in plain black
    text, which the colour check would miss entirely.
    """
    grey = [r for r in RUN_RE.findall(xml) if GREY in r]
    grey_text = "".join(TAG_RE.sub("", r) for r in grey)

    text = _plain_text(xml)
    bracketed = re.findall(r"\[[^\]\n]{1,80}\]", text)

    return grey, grey_text, bracketed


def check(path, verbose=True):
    """Run every check. Returns (fatal_count, warning_count)."""
    path = Path(path)
    fatal, warn = 0, 0
    say = print if verbose else (lambda *a, **k: None)

    say(f"\n{path.name}")
    say("=" * len(path.name))

    size = path.stat().st_size
    if size > SIZE_WARN:
        say(f"  WARN  {size/1024/1024:.1f} MB - over SmartRecruiters' 2 MB limit")
        warn += 1

    if path.suffix.lower() == ".pdf":
        try:
            from pdfminer.high_level import extract_text
        except ImportError:
            say("  SKIP  pdfminer not installed; cannot inspect PDF")
            return fatal, warn
        text = extract_text(str(path))
        if len(text.strip()) < 200:
            say("  FATAL no extractable text - this is an image to every parser")
            fatal += 1
        else:
            say(f"  ok    text layer present ({len(text.split())} words)")
        found = re.findall(r"\[[^\]\n]{1,80}\]", text)
        if found:
            say(f"  FATAL {len(found)} placeholder(s) still in the PDF:")
            for f in found:
                say(f"          {f}")
            fatal += 1
        else:
            say("  ok    no placeholders")
        hits = [b for b in BUZZWORDS if b in text.lower()]
        if hits:
            say(f"  WARN  buzzwords: {', '.join(hits)}")
            warn += 1
        else:
            say("  ok    no buzzwords")
        for label, pattern in (("email", r"[\w.+-]+@[\w-]+\.\w+"),
                               ("phone", r"[\+\d][\d\s\-()]{7,}")):
            if not re.search(pattern, text):
                say(f"  WARN  no {label} found in extracted text")
                warn += 1
        return fatal, warn

    if path.suffix.lower() != ".docx":
        say("  SKIP  not a .docx or .pdf")
        return fatal, warn

    xml, names = _docx_xml(path)
    text = _plain_text(xml)

    grey, grey_text, bracketed = find_placeholders(xml)
    if grey or bracketed:
        shown = bracketed or [grey_text]
        say(f"  FATAL {len(bracketed) or len(grey)} placeholder(s):")
        for b in dict.fromkeys(shown):
            say(f"          {b}")
        say("          run with --fix to remove them")
        fatal += 1
    else:
        say("  ok    no placeholders")

    hits = [b for b in BUZZWORDS if b in text.lower()]
    if hits:
        say(f"  WARN  buzzwords: {', '.join(hits)}")
        warn += 1
    else:
        say("  ok    no buzzwords")

    em = text.count("—")
    if em:
        say(f"  note  {em} em-dash(es) - named as an AI tell by 32% of "
            f"hiring managers")

    headers = [n for n in names if "header" in n or "footer" in n]
    if headers:
        say(f"  WARN  header/footer parts present ({len(headers)}) - "
            f"Greenhouse skips contact details placed there")
        warn += 1
    else:
        say("  ok    no header/footer (contact details sit in the body)")

    cols = re.findall(r'<w:cols[^>]*w:num="(\d+)"', xml)
    if any(int(c) > 1 for c in cols):
        say("  WARN  multi-column layout - Greenhouse names columns as a "
            "parse failure")
        warn += 1
    else:
        say("  ok    single column")

    tables = xml.count("<w:tbl>")
    boxes = xml.count("w:txbxContent")
    draws = xml.count("<w:drawing>")
    if boxes or draws:
        say(f"  WARN  text boxes: {boxes}, drawings: {draws} - parsers read "
            f"neither")
        warn += 1
    if tables:
        say(f"  note  {tables} table(s) - contested; modern parsers mostly cope")
    if not (boxes or draws or tables):
        say("  ok    no tables, text boxes or images")

    headings = {h.strip().lower() for h in re.findall(r"^([A-Z][A-Z \t&]{2,30})$",
                                                      text, re.M)}
    odd = [h for h in headings if h and h not in STANDARD_HEADINGS]
    if odd:
        say(f"  note  non-standard headings: {', '.join(sorted(odd)[:5])}")
    if headings & STANDARD_HEADINGS:
        say(f"  ok    standard headings "
            f"({', '.join(sorted(headings & STANDARD_HEADINGS)[:5])})")

    bad_dates = re.findall(r"\b[A-Z][a-z]{2}\s*'\d{2}\b", text)
    if bad_dates:
        say(f"  WARN  abbreviated dates ({bad_dates[0]}) - Lever can fail to "
            f"extract these; prefer 'Month Year'")
        warn += 1
    else:
        say("  ok    dates in Month Year form")

    return fatal, warn


# ─── fix ─────────────────────────────────────────────────────────────────────

def strip_placeholders(path):
    """
    Remove grey placeholder runs from a .docx, in place.

    Only the runs the template marked grey are removed. A bracket typed in
    ordinary text is reported by check() but left alone, because deleting black
    body text on a guess is not a thing a script should do to someone's resume.
    """
    path = Path(path)
    try:
        with open(path, "r+b"):
            pass
    except PermissionError:
        print(f"  LOCKED  {path.name} is open in Word - close it and re-run")
        return False

    with zipfile.ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist()}

    xml = parts["word/document.xml"].decode("utf-8")
    removed = 0

    def drop(match):
        nonlocal removed
        if GREY in match.group(0):
            removed += 1
            return ""
        return match.group(0)

    xml = RUN_RE.sub(drop, xml)
    if not removed:
        print(f"  {path.name}: nothing to remove")
        return False

    # Deleting a run leaves the spacing of its neighbours behind: "a team of "
    # + "[N]" + " interns" becomes "a team of  interns", and a bullet that ended
    # "...approves it. " keeps a trailing space. Repair both.
    def fix_para(match):
        para = match.group(0)
        texts = list(TEXT_RE.finditer(para))
        if not texts:
            return para
        values = [m.group(2) for m in texts]
        for i in range(len(values) - 1):
            if values[i].endswith(" ") and values[i + 1].startswith(" "):
                values[i + 1] = values[i + 1].lstrip(" ")
        values[-1] = values[-1].rstrip()
        values = [re.sub(r"  +", " ", v) for v in values]
        values = [re.sub(r"\s+([.,;:])", r"\1", v) for v in values]
        out, last = [], 0
        for m, v in zip(texts, values):
            out.append(para[last:m.start()])
            out.append(f"{m.group(1)}{v}{m.group(3)}")
            last = m.end()
        out.append(para[last:])
        return "".join(out)

    xml = PARA_RE.sub(fix_para, xml)
    parts["word/document.xml"] = xml.encode("utf-8")

    tmp = path.with_suffix(".docx.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in parts.items():
            z.writestr(name, blob)
    shutil.move(str(tmp), str(path))
    print(f"  {path.name}: removed {removed} placeholder run(s)")
    return True


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    do_fix = "--fix" in argv
    if not args:
        print(__doc__)
        return 2

    targets = []
    for a in args:
        p = Path(a)
        targets.extend(sorted(p.glob("*.docx")) if p.is_dir() else [p])

    if do_fix:
        print("Stripping placeholders")
        for t in targets:
            if t.suffix.lower() == ".docx":
                strip_placeholders(t)
        print()

    total_fatal = 0
    for t in targets:
        f, _ = check(t)
        total_fatal += f

    print()
    if total_fatal:
        print(f"{total_fatal} fatal issue(s). Do not send these yet.")
        return 1
    print("Clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
