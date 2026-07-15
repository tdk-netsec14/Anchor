"""Calibrate the semantic-similarity grader against a saved evaluation run.

Re-grades the answers already in ``eval/results/<timestamp>.json`` and prints
the score distribution for correct vs incorrect answers, so the threshold is
chosen from measurements rather than a guess.

    python scripts/calibrate_grader.py [path-to-results.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.graders.semantic_similarity_grader import SemanticGrader  # noqa: E402


def latest_results() -> Path:
    files = sorted((ROOT / "eval" / "results").glob("*.json"))
    if not files:
        raise SystemExit("No evaluation results found. Run eval/run_eval.py first.")
    return files[-1]


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    path = Path(args[0]) if args else latest_results()
    report = json.loads(path.read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in json.loads((ROOT / "eval" / "test_cases.json").read_text())}

    grader = SemanticGrader()
    rows: list[tuple[str, str, float | None, bool, bool]] = []

    for record in report["results"]:
        case = cases.get(record["id"])
        if not case or not case.get("expected_answer_contains"):
            continue
        body = record.get("response") or {}
        if not body.get("answer"):
            continue
        result = grader.grade(case, body)
        # "Truth" here is exact match: did the curated fact actually appear?
        truth = next(
            g["passed"]
            for g in record.get("grades", {}).values()
            if g["grader"] == "exact_match"
        )
        rows.append((record["id"], case["category"], result.score, truth, result.passed))

    if not rows:
        print("No scorable cases in that report.")
        return 1

    # A case the grader could not score (embedding model unavailable) carries a
    # None score; it must not be mixed into the statistics or formatted.
    scored = [r for r in rows if r[2] is not None]
    correct = [r[2] for r in scored if r[3]]
    wrong = [r[2] for r in scored if not r[3]]
    unscored = len(rows) - len(scored)

    print(f"\nRe-graded {len(rows)} cases from {path.name}")
    if unscored:
        print(f"  {unscored} case(s) could not be scored by the semantic grader.")
    if correct:
        print(
            f"  exact-match PASS : n={len(correct)}  min={min(correct):.3f}  "
            f"mean={sum(correct) / len(correct):.3f}"
        )
    if wrong:
        print(
            f"  exact-match FAIL : n={len(wrong)}  min={min(wrong):.3f}  "
            f"mean={sum(wrong) / len(wrong):.3f}"
        )
    if not correct and not wrong:
        print("  No graded cases; nothing to compare.")
        return 1

    print()
    print("  id         category       score   exact  new_pass")
    for case_id, category, score, truth, passed in sorted(
        rows, key=lambda r: (r[2] is None, r[2])
    ):
        shown = f"{score:>6.3f}" if score is not None else "   n/a"
        print(f"  {case_id:<10} {category:<14} {shown}  {str(truth):<6} {passed}")

    if correct and wrong:
        print()
        print(
            f"  Best separation: lowest correct = {min(correct):.3f}, "
            f"highest incorrect = {max(wrong):.3f}"
        )
        print(f"  Suggested threshold (midpoint): {(min(correct) + max(wrong)) / 2:.3f}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
