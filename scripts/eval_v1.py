"""v1 release-gate eval: index a few repositories through the HTTP API and score real answers.

Standard library only; it talks to a running Repona server (API, data stores and a real LLM
provider), so it measures exactly what a user gets.

    python scripts/eval_v1.py --base-url http://127.0.0.1:8000 \
        --output evaluation/v1-eval-latest.json --markdown evaluation/v1-eval-latest.md

Metrics per repository and overall:
- answered:     answerable questions that got an answer other than a fixed refusal line
- expected_hit: answerable questions where a citation points at one of the expected files
- absent_ok:    "not in this repo" questions answered with a refusal or an explicit absence
                statement (heuristic; review those answers by hand)
- latency:      median and p90 seconds per question, retrieval plus model

Token counts are not exposed by the API; read them from the server logs if needed.
Run it before and after a change and compare the two reports.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Fixed refusal lines from backend/app/retrieval/packet.py (NO_CITED_CLAIM, INSUFFICIENT_EVIDENCE).
REFUSAL_LINES = frozenset(
    {
        "The repository evidence did not produce a cited claim.",
        "The retrieved evidence is not sufficient to explain this.",
    }
)
_ABSENCE = re.compile(
    r"\b(does not|doesn't|do not|don't|is not|isn't|no (?:code|evidence|implementation|support|mention)|"
    r"not (?:present|found|implemented|supported|included|part of)|nothing in|absent|there is no|there are no)\b",
    re.I,
)
DEFAULT_QUESTIONS = Path(__file__).resolve().parents[1] / "evaluation" / "v1-eval-questions.json"


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def _request(base_url: str, method: str, path: str, body: dict | None, timeout: float) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        retry_after = error.headers.get("Retry-After") if error.headers else None
        if error.code == 429 and retry_after:
            detail = f"{detail} (retry after {retry_after}s)"
        raise ApiError(error.code, detail) from error


def _with_rate_limit_retry(call, *, attempts: int = 5):
    for attempt in range(attempts):
        try:
            return call()
        except ApiError as error:
            if error.status != 429 or attempt == attempts - 1:
                raise
            match = re.search(r"retry after (\d+)s", error.detail)
            time.sleep(int(match.group(1)) + 1 if match else 15)
    raise AssertionError("unreachable")


def client_id(base_url: str) -> str:
    return str(_request(base_url, "GET", "/api/v1/client", None, 30)["client_id"])


def index_repository(base_url: str, user_id: str, repo_url: str, *, timeout: float, poll_seconds: float) -> dict:
    """POST /repository until the generation is ready to serve. Indexing blocks inside the call."""
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    while True:
        remaining = max(30.0, deadline - time.monotonic())
        body = _with_rate_limit_retry(
            lambda: _request(
                base_url,
                "POST",
                "/api/v1/repository",
                {"repo_url": repo_url, "user_id": user_id},
                remaining,
            )
        )
        if body.get("action") == "serve":
            body["index_seconds"] = round(time.monotonic() - started, 1)
            return body
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{repo_url} was not ready after {timeout:.0f}s: {body.get('message')}")
        time.sleep(poll_seconds)


def ask(base_url: str, user_id: str, repository_id: str, question: str, *, timeout: float, provider: str | None, model: str | None) -> dict:
    payload: dict = {"repository_id": repository_id, "user_id": user_id, "question": question}
    if provider:
        payload["provider"] = provider
    if model:
        payload["model"] = model
    for _ in range(5):
        body = _with_rate_limit_retry(lambda: _request(base_url, "POST", "/api/v1/ask", payload, timeout))
        if body.get("action") != "wait":
            return body
        time.sleep(20)
    return body


def _cited_paths(response: dict) -> list[str]:
    return [str(item.get("path") or "") for item in response.get("citations") or []]


def _path_matches(cited: str, expected: str) -> bool:
    cited = cited.strip("/")
    expected = expected.strip("/")
    return cited == expected or cited.endswith("/" + expected) or expected.endswith("/" + cited)


def score(question: dict, response: dict | None, error: str | None) -> dict:
    answer = "" if response is None else str(response.get("answer") or "").strip()
    refused = not answer or answer in REFUSAL_LINES
    cited = [] if response is None else _cited_paths(response)
    expected = list(question.get("expected_files") or [])
    absent = bool(question.get("expect_absent"))
    record = {
        "id": question["id"],
        "question": question["question"],
        "expect_absent": absent,
        "error": error,
        "refused": refused if error is None else None,
        "answer_chars": len(answer),
        "cited_paths": sorted(set(cited)),
        "expected_files": expected,
        "answer": answer,
    }
    if absent:
        record["absent_ok"] = error is None and (refused or bool(_ABSENCE.search(answer)))
    else:
        record["expected_hit"] = error is None and any(_path_matches(path, item) for path in cited for item in expected)
    return record


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 1)


def summarize(records: list[dict]) -> dict:
    answerable = [item for item in records if not item["expect_absent"]]
    absent = [item for item in records if item["expect_absent"]]
    latencies = [item["latency_seconds"] for item in records if item.get("latency_seconds") is not None]

    def rate(items: list[dict], predicate) -> str:
        if not items:
            return "n/a"
        hits = sum(1 for item in items if predicate(item))
        return f"{hits}/{len(items)}"

    return {
        "questions": len(records),
        "errors": sum(1 for item in records if item["error"]),
        "answered": rate(answerable, lambda item: item["error"] is None and not item["refused"]),
        "expected_hit": rate(answerable, lambda item: item.get("expected_hit")),
        "absent_ok": rate(absent, lambda item: item.get("absent_ok")),
        "latency_median_seconds": round(statistics.median(latencies), 1) if latencies else None,
        "latency_p90_seconds": _percentile(latencies, 0.9),
    }


def write_markdown(path: Path, report: dict) -> None:
    lines = [
        "# v1 eval report",
        "",
        f"- Base URL: `{report['base_url']}`",
        f"- Started: {report['started_at']}",
        f"- Provider/model: {report.get('provider') or 'server default'} / {report.get('model') or 'default'}",
        "",
        "| Repository | Questions | Errors | Answered | Expected file cited | Absent handled | Median s | p90 s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for repo in report["repositories"]:
        summary = repo["summary"]
        lines.append(
            f"| {repo['id']} | {summary['questions']} | {summary['errors']} | {summary['answered']} | "
            f"{summary['expected_hit']} | {summary['absent_ok']} | {summary['latency_median_seconds']} | "
            f"{summary['latency_p90_seconds']} |"
        )
    overall = report["overall"]
    lines.append(
        f"| **overall** | {overall['questions']} | {overall['errors']} | {overall['answered']} | "
        f"{overall['expected_hit']} | {overall['absent_ok']} | {overall['latency_median_seconds']} | "
        f"{overall['latency_p90_seconds']} |"
    )
    lines += ["", "## Misses", ""]
    for repo in report["repositories"]:
        for item in repo["results"]:
            missed = item["error"] or item["refused"] or (
                not item["expect_absent"] and not item.get("expected_hit")
            ) or (item["expect_absent"] and not item.get("absent_ok"))
            if not missed:
                continue
            reason = item["error"] or ("refused" if item["refused"] else "wrong or no expected file")
            lines.append(f"- **{item['id']}** ({reason}): {item['question']}")
            lines.append(f"  - cited: {', '.join(item['cited_paths']) or 'nothing'}")
    lines += ["", "Absent-topic scoring is a regex heuristic; read those answers in the JSON report.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--repos", nargs="*", help="Only these repository ids from the questions file")
    parser.add_argument("--output", type=Path, default=Path("evaluation/v1-eval-latest.json"))
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument("--provider", default=None, help="Optional /ask provider override")
    parser.add_argument("--model", default=None, help="Optional /ask model override")
    parser.add_argument("--index-timeout", type=float, default=3600.0)
    parser.add_argument("--ask-timeout", type=float, default=900.0)
    parser.add_argument("--ask-interval", type=float, default=6.0, help="Seconds between asks (default limit is 12/min)")
    parser.add_argument("--poll-seconds", type=float, default=20.0)
    args = parser.parse_args()

    spec = json.loads(args.questions.read_text(encoding="utf-8"))
    repositories = [repo for repo in spec["repositories"] if not args.repos or repo["id"] in args.repos]
    if not repositories:
        print("no repositories selected", file=sys.stderr)
        return 2

    report: dict = {
        "base_url": args.base_url,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "provider": args.provider,
        "model": args.model,
        "questions_file": str(args.questions),
        "repositories": [],
    }
    user_id = client_id(args.base_url)
    all_records: list[dict] = []
    for repo in repositories:
        print(f"[{repo['id']}] indexing {repo['repo_url']} ...", flush=True)
        try:
            indexed = index_repository(
                args.base_url, user_id, repo["repo_url"], timeout=args.index_timeout, poll_seconds=args.poll_seconds
            )
        except (ApiError, RuntimeError, OSError) as error:
            print(f"[{repo['id']}] indexing failed: {error}", file=sys.stderr)
            records = [score(question, None, f"index failed: {error}") for question in repo["questions"]]
            report["repositories"].append({"id": repo["id"], "index": None, "results": records, "summary": summarize(records)})
            all_records.extend(records)
            continue
        print(f"[{repo['id']}] ready in {indexed['index_seconds']}s (commit {str(indexed.get('commit_sha'))[:12]})", flush=True)
        records: list[dict] = []
        for question in repo["questions"]:
            started = time.monotonic()
            response, error = None, None
            try:
                response = ask(
                    args.base_url,
                    user_id,
                    indexed["repository_id"],
                    question["question"],
                    timeout=args.ask_timeout,
                    provider=args.provider,
                    model=args.model,
                )
            except (ApiError, OSError) as exc:
                error = str(exc)
            record = score(question, response, error)
            record["latency_seconds"] = round(time.monotonic() - started, 1)
            records.append(record)
            outcome = record["error"] or ("refused" if record["refused"] else "answered")
            hit = record.get("expected_hit", record.get("absent_ok"))
            print(f"  {question['id']}: {outcome}, hit={hit}, {record['latency_seconds']}s", flush=True)
            time.sleep(args.ask_interval)
        report["repositories"].append(
            {
                "id": repo["id"],
                "index": {key: indexed.get(key) for key in ("repository_id", "commit_sha", "generation_id", "index_seconds")},
                "results": records,
                "summary": summarize(records),
            }
        )
        all_records.extend(records)

    report["overall"] = summarize(all_records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.markdown:
        write_markdown(args.markdown, report)
    print(json.dumps(report["overall"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
