"""Run golden extraction cases and print an accuracy report.

Usage: python -m evals.run [--md report.md]
This measures the rule-based (demo-mode) extractor only. It is not a live-mode eval.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from khata.extract import detect_lang, extract

TODAY = date(2026, 11, 19)
HERE = Path(__file__).parent
SETS = {name: json.loads((HERE / f"{name}.json").read_text("utf-8")) for name in ("golden", "dev2", "blind")}
LABELS = {
    "golden": "golden (developed against, expect overfit)",
    "dev2": "dev2 (was held-out v1: scored 6/15 = 40% before fixes, then tuned on)",
    "blind": "blind (written before running, never tuned on)",
}
CASES = SETS["golden"]


def check(case: dict) -> list[str]:
    e = extract(case["text"], TODAY)
    errs = []
    for f in ("intent", "customer", "paise"):
        if getattr(e, f) != case[f]:
            errs.append(f"{f}: got {getattr(e, f)!r}, want {case[f]!r}")
    if "due_offset" in case:
        want = (TODAY + timedelta(days=case["due_offset"])).isoformat()
        if e.due_date != want:
            errs.append(f"due_date: got {e.due_date}, want {want}")
    if "entry_offset" in case:
        want = (TODAY + timedelta(days=case["entry_offset"])).isoformat()
        if e.entry_date != want:
            errs.append(f"entry_date: got {e.entry_date}, want {want}")
    return errs


def _goes_to_confirm(text: str) -> bool:
    e = extract(text, TODAY)
    return e.intent in ("add_credit", "add_payment") and e.customer is not None and e.paise is not None


def run(cases: list[dict] = CASES) -> tuple[int, int, list[tuple[str, list[str]]], dict]:
    fails, by_lang = [], {}
    for c in cases:
        errs = check(c)
        lang = detect_lang(c["text"])
        ok, n = by_lang.get(lang, (0, 0))
        by_lang[lang] = (ok + (not errs), n + 1)
        if errs:
            fails.append((c["text"], errs))
    return len(cases) - len(fails), len(cases), fails, by_lang


def report() -> str:
    lines = ["# Extraction eval (rule-based demo mode)", "",
             "Exact match on intent + customer + amount (+ dates where labelled).", ""]
    for name, cases in SETS.items():
        ok, n, fails, by_lang = run(cases)
        label = LABELS[name]
        lines += [f"## {label}: **{ok}/{n}** ({100 * ok / n:.0f}%)", "",
                  "| language | correct |", "|---|---|"]
        lines += [f"| {k} | {a}/{b} |" for k, (a, b) in sorted(by_lang.items())]
        unsafe = [t for t, _ in fails if _goes_to_confirm(t)]
        lines += ["", f"Wrong entry proposed straight to the confirm step: **{len(unsafe)}/{n}**. "
                  "Other failures make the bot ask a follow-up question instead."]
        if fails:
            lines += ["", "Failures:", ""] + [f"- `{t}`: {'; '.join(e)}" for t, e in fails]
        lines.append("")
    lines += ["All cases are hand-written text. Real voice transcripts will score lower."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    md = report()
    print(md)
    if "--md" in sys.argv:
        Path(sys.argv[sys.argv.index("--md") + 1]).write_text(md, "utf-8")
