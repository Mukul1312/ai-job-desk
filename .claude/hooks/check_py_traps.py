#!/usr/bin/env python3
"""Warn about the two traps this repo has already paid for.

Reads a Claude Code PostToolUse payload on stdin, prints a JSON object with a
systemMessage when a trap is present, and always exits 0 - this advises, it
never blocks.

1. `abs(hash(...))` used for anything persisted. Python randomises string
   hashing per process. In tools/extract_llm.py this renamed 705 cache keys on
   every --fetch, stranding 490 paid extractions with ZERO overlap and
   re-paying ~$0.43 a run, silently.

2. `concurrent.futures` imported in tools/*.py before that script strips its
   own directory from sys.path. tools/queue.py shadows the stdlib `queue`,
   which concurrent.futures imports at module load, so ThreadPoolExecutor dies
   with "module 'queue' has no attribute 'SimpleQueue'". Cost three tools an
   hour; the import must come AFTER the sys.path line.
"""
import json
import re
import sys
from pathlib import Path

# Advises, never blocks: always exits 0.


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    ti = payload.get("tool_input") or {}
    tr = payload.get("tool_response") or {}
    path = tr.get("filePath") or ti.get("file_path") or ""
    if not path.endswith(".py"):
        return 0
    try:
        src = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0

    warnings = []
    lines = src.split("\n")

    # Parsed, not grepped. A regex flagged the docstring in extract_llm.py that
    # DESCRIBES this bug - a checker that cannot tell code from prose about
    # code will be switched off within a day.
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "abs"
                    and node.args):
                continue
            inner = node.args[0]
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "hash"):
                warnings.append(
                    "%s:%d calls abs(hash(...)). Python randomises string "
                    "hashing per process - if this value is persisted, every "
                    "restart renames the key. This stranded 490 paid "
                    "extractions with zero overlap. Use "
                    "hashlib.sha1(x.encode()).hexdigest()[:12]."
                    % (Path(path).name, getattr(node, "lineno", 0)))
                break

    if Path(path).parent.name == "tools":
        imp = next((i for i, l in enumerate(lines, 1)
                    if re.match(r"\s*(from concurrent\.futures import|"
                                r"import concurrent\.futures)", l)), None)
        strip = next((i for i, l in enumerate(lines, 1)
                      if "sys.path[:]" in l), None)
        if imp and (strip is None or strip > imp):
            warnings.append(
                "%s:%d imports concurrent.futures %s. tools/queue.py shadows "
                "the stdlib `queue` for any script in tools/, and "
                "concurrent.futures imports it at module load, so "
                "ThreadPoolExecutor will die on queue.SimpleQueue. Strip this "
                "directory from sys.path FIRST - see the header of "
                "tools/board.py."
                % (Path(path).name, imp,
                   "with no sys.path strip" if strip is None
                   else "before the sys.path strip on line %d" % strip))

    if warnings:
        print(json.dumps({"systemMessage": "evidence-discipline: " +
                          " | ".join(warnings)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
