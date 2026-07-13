"""Anchor evaluation runner.

Loads the fixed test cases, calls the Agent API once per case, runs four
independent graders over each response, and writes both a console summary and a
timestamped JSON report.

    python eval/run_eval.py --base-url http://localhost:8000
    python eval/run_eval.py --category factual --verbose

The report is the regression artefact: `eval/test_agent.py` re-runs the same
cases under pytest so a quality drop fails CI.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.graders import exact_match_grader, schema_grader  # noqa: E402
from eval.graders.llm_judge_grader import LlmJudgeGrader  # noqa: E402
from eval.graders.semantic_similarity_grader import SemanticGrader  # noqa: E402

TEST_CASES_PATH = ROOT / "eval" / "test_cases.json"
RESULTS_DIR = ROOT / "eval" / "results"

#: Categories whose cases pass by being *rejected* by the input guard, and
#: categories whose cases pass by the model declining to answer.
BLOCKED_CATEGORIES = {"adversarial"}
REFUSAL_CATEGORIES = {"out_of_scope"}
ALL_CATEGORIES = BLOCKED_CATEGORIES | REFUSAL_CATEGORIES | {"factual", "tool_use"}

def load_cases(path: Path = TEST_CASES_PATH) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path} must contain a non-empty list of test cases")
    return cases


def get_token(client: httpx.Client, base_url: str, role: str = "user") -> str:
    """Mint a token through the public auth endpoint."""
    response = client.post(
        f"{base_url.rstrip('/')}/auth/token",
        json={"username": "eval-runner", "role": role},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def run_case(
    client: httpx.Client,
    case: dict[str, Any],
    base_url: str,
    token: str,
    force_model: str | None,
) -> dict[str, Any]:
    """Call the API once and return a normalised record of what happened."""
    payload: dict[str, Any] = {"query": case["query"], "session_id": case["id"]}
    if force_model:
        payload["force_model"] = force_model

    started = time.perf_counter()
    try:
        response = client.post(
            f"{base_url.rstrip('/')}/query",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=300.0,
        )
    except httpx.HTTPError as exc:
        return {
            "id": case["id"],
            "category": case["category"],
            "query": case["query"],
            "error": f"{type(exc).__name__}: {exc}",
            "http_status": None,
            "wall_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    wall_ms = round((time.perf_counter() - started) * 1000, 2)
    record: dict[str, Any] = {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "http_status": response.status_code,
        "wall_ms": wall_ms,
    }

    try:
        body = response.json()
    except ValueError:
        record["error"] = "non-JSON response"
        record["raw"] = response.text[:300]
        return record

    if response.status_code != 200:
        # Blocked by the input guard, or a provider failure. The body is a
        # first-class outcome for the adversarial cases, not a test error.
        record["blocked"] = response.status_code == 400
        record["guardrail_flags"] = body.get("guardrail_flags", [])
        record["error_code"] = body.get("error")
        record["message"] = body.get("message")
        return record

    record["response"] = body
    record["answer"] = body.get("answer")
    record["model_used"] = body.get("model_used")
    record["sources"] = body.get("sources", [])
    record["tool_calls"] = [c.get("name") for c in body.get("tool_calls", [])]
    record["guardrail_flags"] = body.get("guardrail_flags", [])
    record["latency_ms"] = body.get("latency_ms")
    record["tokens_used"] = body.get("tokens_used")
    record["estimated_cost_usd"] = body.get("estimated_cost_usd")
    record["request_id"] = body.get("request_id")
    return record


def grade_case(
    case: dict[str, Any],
    record: dict[str, Any],
    semantic: SemanticGrader,
    judge: LlmJudgeGrader,
) -> dict[str, Any]:
    """Run every grader over one record.

    A blocked (400) response is a legitimate outcome for adversarial cases: the
    schema grader cannot apply, but the case still passes if the guardrail
    rejected it.
    """
    grades: dict[str, Any] = {}

    # Out-of-scope cases are refusal cases whether or not they say so, so the
    # category decides rather than a per-case flag that can drift.
    if case["category"] in REFUSAL_CATEGORIES:
        case = {**case, "expect_refusal": True}
    if record.get("http_status") != 200:
        blocked = bool(record.get("blocked"))
        expected_block = (
            bool(case.get("expect_blocked")) or case["category"] in BLOCKED_CATEGORIES
        )
        grades["guardrail"] = {
            "grader": "guardrail",
            "score": 1.0 if blocked == expected_block else 0.0,
            "passed": blocked == expected_block,
            "detail": {
                "http_status": record.get("http_status"),
                "error_code": record.get("error_code"),
                "guardrail_flags": record.get("guardrail_flags", []),
                "expected_block": expected_block,
            },
        }
        return {
            **record,
            "grades": grades,
            "passed": grades["guardrail"]["passed"],
            "answer": record.get("message", ""),
        }

    body = record["response"]
    grades["schema"] = schema_grader.grade(case, body).to_dict()
    grades["exact_match"] = exact_match_grader.grade(case, body).to_dict()
    grades["semantic_similarity"] = semantic.grade(case, body).to_dict()

    # The judge assesses grounding against the sources the answer actually
    # cited. Anchor deliberately does not return raw retrieved text in the API
    # response, so the citations are the available evidence - and the judge is
    # told that, rather than being left to guess what "grounded" means here.
    cited = record.get("sources") or []
    judge_case = {
        **case,
        "judge_context": (
            "Sources cited by the answer: " + "; ".join(cited)
            if cited
            else "The answer cited no sources."
        ),
    }
    grades["llm_judge"] = judge.grade(judge_case, body).to_dict()

    # A case passes when every applicable grader passes. The tool-use
    # expectation is checked separately because it is a different property.
    graded = [g for g in grades.values() if g.get("score") is not None]
    if not graded:
        # `all([])` is True, so a case nothing could score would otherwise be
        # reported as a pass with nothing actually checked.
        grades["no_applicable_graders"] = {
            "grader": "no_applicable_graders",
            "score": 0.0,
            "passed": False,
            "detail": {
                "reason": (
                    "No grader applied: the case declares no expected content and "
                    "expects neither a refusal nor a blocked request."
                )
            },
        }
        all_passed = False
    else:
        all_passed = all(g["passed"] for g in graded)

    expected_tool = case.get("expected_tool_call")
    if expected_tool:
        tool_ok = expected_tool in record.get("tool_calls", [])
        grades["tool_use"] = {
            "grader": "tool_use",
            "score": 1.0 if tool_ok else 0.0,
            "passed": tool_ok,
            "detail": {"expected": expected_tool, "observed": record.get("tool_calls", [])},
        }
        all_passed = all_passed and tool_ok

    return {**record, "grades": grades, "passed": all_passed}


def summarise(results: list[dict[str, Any]], elapsed_s: float) -> dict[str, Any]:
    """Aggregate scores, per-category pass rates, latency and cost."""
    graded = [r for r in results if "grades" in r]
    by_category: dict[str, list[bool]] = defaultdict(list)
    by_grader_pass: dict[str, list[bool]] = defaultdict(list)
    by_grader_score: dict[str, list[float]] = defaultdict(list)

    for record in graded:
        by_category[record["category"]].append(bool(record["passed"]))
        for grade in record["grades"].values():
            by_grader_pass[grade["grader"]].append(bool(grade["passed"]))
            if grade.get("score") is not None:
                by_grader_score[grade["grader"]].append(float(grade["score"]))

    latencies = [r["latency_ms"] for r in graded if isinstance(r.get("latency_ms"), (int, float))]
    wall_times = [r["wall_ms"] for r in graded if isinstance(r.get("wall_ms"), (int, float))]
    costs = [
        r["estimated_cost_usd"]
        for r in graded
        if isinstance(r.get("estimated_cost_usd"), (int, float))
    ]
    tokens = [r["tokens_used"] for r in graded if isinstance(r.get("tokens_used"), int)]

    return {
        "total_cases": len(results),
        "graded_cases": len(graded),
        "passed_cases": sum(1 for r in graded if r.get("passed")),
        "failed_cases": sum(1 for r in graded if not r.get("passed")),
        "error_cases": sum(1 for r in results if r.get("error")),
        "pass_rate": round(
            sum(1 for r in graded if r.get("passed")) / len(graded), 4
        )
        if graded
        else 0.0,
        "by_category": {
            category: {
                "total": len(outcomes),
                "passed": sum(1 for o in outcomes if o),
                "pass_rate": round(sum(1 for o in outcomes if o) / len(outcomes), 4),
            }
            for category, outcomes in sorted(by_category.items())
        },
        "by_grader": {
            grader: {
                "graded": len(by_grader_score.get(grader, [])),
                "pass_rate": round(sum(by_grader_pass[grader]) / len(by_grader_pass[grader]), 4)
                if by_grader_pass.get(grader)
                else None,
                "mean_score": round(statistics.fmean(by_grader_score[grader]), 4)
                if by_grader_score.get(grader)
                else None,
            }
            for grader in sorted(by_grader_pass)
        },
        "latency_ms": {
            "mean_api": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "median_api": round(statistics.median(latencies), 2) if latencies else 0.0,
            "p95_api": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2)
            if latencies
            else 0.0,
            "mean_wall": round(statistics.fmean(wall_times), 2) if wall_times else 0.0,
        },
        "cost": {
            "total_usd": round(sum(costs), 6),
            "mean_usd_per_query": round(statistics.fmean(costs), 8) if costs else 0.0,
        },
        "tokens": {"total": sum(tokens), "mean": round(statistics.fmean(tokens), 1) if tokens else 0.0},
        "models_used": _count(r.get("model_used") for r in graded),
        "tool_calls": _count(
            name for r in graded for name in (r.get("tool_calls") or [])
        ),
        "guardrail_flags": _count(
            flag for r in graded for flag in (r.get("guardrail_flags") or [])
        ),
        "wall_clock_s": round(elapsed_s, 2),
    }


def _count(values) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        if value:
            counts[str(value)] += 1
    return dict(sorted(counts.items()))


def print_summary(report: dict[str, Any], results: list[dict[str, Any]], verbose: bool) -> None:
    summary = report["summary"]
    print("\n" + "=" * 74)
    print("ANCHOR EVALUATION REPORT")
    print("=" * 74)
    print(f"  run at            : {report['generated_at']}")
    print(f"  target            : {report['base_url']}")
    print(f"  force_model       : {report.get('force_model') or '(router default)'}")
    print(f"  cases             : {summary['total_cases']}")
    print(
        f"  passed            : {summary['passed_cases']}/{summary['graded_cases']} "
        f"({summary['pass_rate'] * 100:.1f}%)"
    )
    if summary["error_cases"]:
        print(f"  transport errors  : {summary['error_cases']}")

    print("\n  By category:")
    for category, stats in summary["by_category"].items():
        print(f"    {category:<14} {stats['passed']}/{stats['total']}  ({stats['pass_rate'] * 100:.0f}%)")

    print("\n  By grader:")
    for grader, stats in summary["by_grader"].items():
        mean = f"{stats['mean_score']:.3f}" if stats["mean_score"] is not None else "n/a"
        rate = f"{stats['pass_rate'] * 100:.0f}%" if stats["pass_rate"] is not None else "n/a"
        print(f"    {grader:<20} pass {rate:>5}   mean {mean}")

    latency = summary["latency_ms"]
    cost = summary["cost"]
    print("\n  Measurements (from this run only):")
    print(f"    mean API latency     : {latency['mean_api']} ms")
    print(f"    median API latency   : {latency['median_api']} ms")
    print(f"    p95 API latency      : {latency['p95_api']} ms")
    print(f"    mean wall latency    : {latency['mean_wall']} ms")
    print(f"    total tokens         : {summary['tokens']['total']}")
    print(f"    total cost (USD)     : {cost['total_usd']}")
    print(f"    models used          : {summary['models_used'] or '{}'}")

    print("\n  Case results:")
    for record in results:
        mark = "PASS" if record.get("passed") else "FAIL"
        detail = ""
        if record.get("http_status") != 200:
            detail = f"http={record.get('http_status')} {record.get('error_code') or record.get('error', '')}"
        else:
            failed = [
                g for g in record.get("grades", {}).values() if not g.get("passed")
            ]
            detail = ", ".join(g["grader"] for g in failed) or "all graders passed"
        answer = (record.get("answer") or "").replace("\n", " ")
        print(f"    [{mark}] {record['id']:<9} {record['category']:<14} {detail}")
        if verbose:
            print(f"           Q: {record['query']}")
            print(f"           A: {answer[:220]}")

    print("\n" + "=" * 74)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Anchor evaluation suite.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--cases", default=str(TEST_CASES_PATH))
    parser.add_argument("--category", action="append", help="Only run these categories.")
    parser.add_argument("--force-model", default=None, help="Pin a provider, e.g. 'ollama'.")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--no-judge", action="store_true", help="Skip the LLM judge (it needs a model)."
    )
    parser.add_argument(
        "--regrade-from",
        help=(
            "Re-run the graders over a previous run's saved responses instead of "
            "querying the API again. Use after changing a grader to see its effect "
            "without re-paying for inference."
        ),
    )
    args = parser.parse_args(argv)

    cases = load_cases(Path(args.cases))
    if args.category:
        wanted = set(args.category)
        cases = [c for c in cases if c["category"] in wanted]
    if not cases:
        print("No test cases matched the filter.", file=sys.stderr)
        return 2

    semantic = SemanticGrader()
    judge = LlmJudgeGrader()

    started = time.perf_counter()
    results: list[dict[str, Any]] = []

    if args.regrade_from:
        previous = json.loads(Path(args.regrade_from).read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in cases}
        print(f"Re-grading {len(previous['results'])} saved responses from {args.regrade_from} ...")
        for record in previous["results"]:
            case = by_id.get(record["id"])
            if case is None:
                continue
            if record.get("http_status") != 200:
                # Blocked cases are decided by the guardrail outcome alone.
                results.append(
                    grade_case(case, record, semantic, _NullJudge())
                )
                continue
            results.append(grade_case(case, record, semantic, judge))
    else:
        print(f"Running {len(cases)} cases against {args.base_url} ...")
        with httpx.Client() as client:
            try:
                token = get_token(client, args.base_url)
            except Exception as exc:
                print(
                    f"Could not obtain a token from {args.base_url}/auth/token: {exc}\n"
                    "Is the Anchor API running? Start it with `docker compose up`.",
                    file=sys.stderr,
                )
                return 2

            for index, case in enumerate(cases, start=1):
                print(
                    f"  [{index}/{len(cases)}] {case['id']} ({case['category']}) ...", flush=True
                )
                record = run_case(client, case, args.base_url, token, args.force_model)
                results.append(
                    grade_case(
                        case, record, semantic, judge if not args.no_judge else _NullJudge()
                    )
                )

    elapsed = time.perf_counter() - started
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "force_model": args.force_model,
        "judge_enabled": not args.no_judge,
        "regraded_from": args.regrade_from,
        "summary": summarise(results, elapsed),
        "results": results,
    }

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = results_dir / f"{stamp}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print_summary(report, results, args.verbose)
    try:
        shown_path = out_path.relative_to(ROOT)
    except ValueError:
        # --results-dir may point outside the repository.
        shown_path = out_path
    print(f"  results written to: {shown_path}")

    summary = report["summary"]
    return 0 if summary["failed_cases"] == 0 and summary["error_cases"] == 0 else 1


class _NullJudge:
    """Stand-in used by --no-judge; reports 'not applicable' for every case."""

    name = "llm_judge"

    def grade(self, case, response):
        from eval.graders.base import GradeResult

        return GradeResult(grader=self.name, score=None, passed=True, detail={"mode": "disabled"})


if __name__ == "__main__":
    sys.exit(main())
