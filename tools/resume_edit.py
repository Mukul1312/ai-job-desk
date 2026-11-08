#!/usr/bin/env python3
"""
resume_edit.py — replace a section's body text in a .docx, keeping its styling.

For the edits that are a whole paragraph rather than a word: rewriting a summary
so the numbers land in the first twenty seconds, swapping a headline for a
different role. Editing the XML keeps every variant formatted identically,
which hand-editing one file in Word quietly stops being true.

    python tools/resume_edit.py Resume.docx --section SUMMARY --set "New text."
    python tools/resume_edit.py Resume.docx --section SUMMARY --show

The paragraph's own style and the first run's character formatting are reused,
so the replacement inherits the font, size and colour that were already there.
Refuses to touch a file Word has open rather than racing a save.
"""

import re
import shutil
import sys
import zipfile
from pathlib import Path

PARA_RE = re.compile(r"<w:p(?:\s[^>]*)?>.*?</w:p>", re.S)
PPR_RE = re.compile(r"<w:pPr>.*?</w:pPr>", re.S)
RPR_RE = re.compile(r"<w:rPr>.*?</w:rPr>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


def _text_of(para_xml):
    return TAG_RE.sub("", para_xml.replace("<w:tab/>", "\t")).strip()


def _escape(text):
    """XML-escape body text. Apostrophes and ampersands both occur in resumes."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def _runs_for(text, rpr):
    """
    Text as runs, turning tabs into real <w:tab/> elements.

    Role and project headings put the date on the same line behind a tab stop
    ("Ticket to Lord's<tab>Apr 2026 - Jun 2026"). A tab is an element, not a
    character, so writing "\\t" as text would silently drop the alignment and
    leave the date jammed against the title.
    """
    out = []
    for i, chunk in enumerate(text.split("\t")):
        if i:
            out.append(f"<w:r>{rpr}<w:tab/></w:r>")
        if chunk:
            out.append(f'<w:r>{rpr}<w:t xml:space="preserve">'
                       f"{_escape(chunk)}</w:t></w:r>")
    return "".join(out)


def find_section_body(paras, heading):
    """
    Index of the heading paragraph and of the body paragraphs under it.

    A section runs from its heading to the next all-caps heading, so this works
    whether the body is one paragraph or several.
    """
    target = heading.strip().upper()
    head_idx = None
    for i, p in enumerate(paras):
        if _text_of(p).strip().upper() == target:
            head_idx = i
            break
    if head_idx is None:
        return None, []

    body = []
    for j in range(head_idx + 1, len(paras)):
        text = _text_of(paras[j])
        if not text:
            continue
        # Another all-caps heading ends the section.
        if text.isupper() and len(text) < 32:
            break
        body.append(j)
    return head_idx, body


RUN_RE = re.compile(r"<w:r(?:\s[^>]*)?>(?:(?!</w:r>).)*?</w:r>", re.S)


def build_paragraph(template_para, new_text):
    """
    One paragraph carrying new_text, wearing the template's formatting.

    Bullets in this template open with a bold lead-in phrase and continue in
    regular weight, as two runs. Wrap the lead in **asterisks** and it keeps
    that shape; leave them out and the paragraph becomes a single run styled
    like the original's first one.
    """
    ppr = PPR_RE.search(template_para)
    ppr_xml = ppr.group(0) if ppr else ""

    runs = RUN_RE.findall(template_para)
    rprs = []
    for r in runs:
        m = RPR_RE.search(r)
        rprs.append(m.group(0) if m else "")
    lead_rpr = rprs[0] if rprs else ""
    body_rpr = next((r for r in rprs[1:] if "<w:b/>" not in r), lead_rpr)

    bold = re.match(r"\s*\*\*(.+?)\*\*(.*)", new_text, re.S)
    if bold:
        lead, rest = bold.group(1), bold.group(2)
        # Keep the lead run bold even if the source paragraph had no bold run.
        if "<w:b/>" not in lead_rpr:
            lead_rpr = (lead_rpr.replace("<w:rPr>", "<w:rPr><w:b/><w:bCs/>")
                        if lead_rpr else "<w:rPr><w:b/><w:bCs/></w:rPr>")
        return (f"<w:p>{ppr_xml}"
                f"{_runs_for(lead, lead_rpr)}"
                f"{_runs_for(rest, body_rpr)}</w:p>")

    return f"<w:p>{ppr_xml}{_runs_for(new_text, lead_rpr)}</w:p>"


def replace_bullet(xml, needle, new_text):
    """
    Swap one bullet, found by a distinctive fragment of its current text.

    Returns (xml, old_text) or (xml, None) when the fragment matches no
    paragraph or more than one - replacing an ambiguous match would silently
    edit the wrong bullet.
    """
    paras = [m.group(0) for m in PARA_RE.finditer(xml)]
    hits = [p for p in paras if needle.lower() in _text_of(p).lower()]
    if len(hits) != 1:
        return xml, (f"{len(hits)} paragraphs matched" if hits
                     else "no paragraph matched")
    old = hits[0]
    return xml.replace(old, build_paragraph(old, new_text), 1), None


def add_bullet(xml, needle, new_text):
    """
    Insert a new bullet directly after the one matching `needle`.

    The tool could previously only replace, which meant adding a point cost an
    existing one. That is the wrong trade when a posting asks for something the
    resume genuinely covers and simply never mentioned - on a clinical-AI role
    asking for PII/PHI safeguards, the privacy work existed in facts/ and on no
    page, and dropping a good bullet to make room would have been a net loss.

    The new paragraph inherits the matched one's styling, so it is indented and
    weighted like a sibling rather than looking pasted in.

    Returns (xml, None) or (xml, reason).
    """
    paras = [m.group(0) for m in PARA_RE.finditer(xml)]
    hits = [p for p in paras if needle.lower() in _text_of(p).lower()]
    if len(hits) != 1:
        return xml, (f"{len(hits)} paragraphs matched" if hits
                     else "no paragraph matched")
    anchor_para = hits[0]
    made = build_paragraph(anchor_para, new_text)
    return xml.replace(anchor_para, anchor_para + made, 1), None


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2

    path = Path(argv[1])
    heading = None
    new_text = None
    show = "--show" in argv
    if "--section" in argv:
        heading = argv[argv.index("--section") + 1]
    if "--set" in argv:
        new_text = argv[argv.index("--set") + 1]
    if "--set-file" in argv:
        # Long replacement text is painful to quote on a shell - apostrophes in
        # "doctors' own edits" alone will defeat a naive PowerShell paste.
        src = Path(argv[argv.index("--set-file") + 1])
        new_text = " ".join(src.read_text(encoding="utf-8").split())

    needle = (argv[argv.index("--replace-bullet") + 1]
              if "--replace-bullet" in argv else None)
    after = (argv[argv.index("--add-bullet-after") + 1]
             if "--add-bullet-after" in argv else None)

    if not needle and not heading and not after:
        print("--section, --replace-bullet or --add-bullet-after is required")
        return 2
    if not show and new_text is None:
        print("--set / --set-file or --show is required")
        return 2

    with zipfile.ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    xml = parts["word/document.xml"].decode("utf-8")

    if after:
        try:
            with open(path, "r+b"):
                pass
        except PermissionError:
            print(f"LOCKED: {path.name} is open in Word. Close it and re-run.")
            return 1
        xml, err = add_bullet(xml, after, new_text)
        if err:
            print(f"Could not add: {err} for {after!r}")
            return 1
        parts["word/document.xml"] = xml.encode("utf-8")
        tmp = path.with_suffix(".docx.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name, blob in parts.items():
                z.writestr(name, blob)
        shutil.move(str(tmp), str(path))
        print("Added after %r:" % after)
        print("  " + new_text.replace("**", "")[:300])
        print()
        print(f"Written to {path.name}")
        return 0

    if needle:
        try:
            with open(path, "r+b"):
                pass
        except PermissionError:
            print(f"LOCKED: {path.name} is open in Word. Close it and re-run.")
            return 1

        before = [m.group(0) for m in PARA_RE.finditer(xml)]
        match = [p for p in before if needle.lower() in _text_of(p).lower()]
        xml, err = replace_bullet(xml, needle, new_text)
        if err:
            print(f"Could not replace: {err} for {needle!r}")
            return 1
        print(f"Bullet, before:\n  {_text_of(match[0])[:300]}\n")
        parts["word/document.xml"] = xml.encode("utf-8")
        tmp = path.with_suffix(".docx.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name, blob in parts.items():
                z.writestr(name, blob)
        shutil.move(str(tmp), str(path))
        print(f"Bullet, after:\n  {new_text.replace('**', '')[:300]}\n")
        print(f"Written to {path.name}")
        return 0

    paras = list(PARA_RE.finditer(xml))
    para_texts = [m.group(0) for m in paras]
    head_idx, body_idx = find_section_body(para_texts, heading)

    if head_idx is None:
        print(f"Section {heading!r} not found. Headings present:")
        for p in para_texts:
            t = _text_of(p)
            if t and t.isupper() and len(t) < 32:
                print(f"  {t}")
        return 1
    if not body_idx:
        print(f"Section {heading!r} has no body paragraphs.")
        return 1

    if show:
        print(f"{heading} ({len(body_idx)} paragraph(s)):\n")
        for j in body_idx:
            print(f"  {_text_of(para_texts[j])}\n")
        return 0

    try:
        with open(path, "r+b"):
            pass
    except PermissionError:
        print(f"LOCKED: {path.name} is open in Word. Close it and re-run.")
        return 1

    print(f"{heading}, before:\n  {_text_of(para_texts[body_idx[0]])}\n")

    replacement = build_paragraph(para_texts[body_idx[0]], new_text)

    # Rebuild: keep everything, swap the first body paragraph, drop any extras
    # the section had so a multi-paragraph summary collapses into the new one.
    out, last = [], 0
    drop = set(body_idx[1:])
    for idx, m in enumerate(paras):
        if idx == body_idx[0]:
            out.append(xml[last:m.start()])
            out.append(replacement)
            last = m.end()
        elif idx in drop:
            out.append(xml[last:m.start()])
            last = m.end()
    out.append(xml[last:])
    xml = "".join(out)

    parts["word/document.xml"] = xml.encode("utf-8")
    tmp = path.with_suffix(".docx.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in parts.items():
            z.writestr(name, blob)
    shutil.move(str(tmp), str(path))

    print(f"{heading}, after:\n  {new_text}\n")
    print(f"Written to {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
