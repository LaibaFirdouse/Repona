#!/usr/bin/env python3
"""HTTP smoke test: client session, index, ask, list, history, delete."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _request(
    opener: urllib.request.OpenerDirector,
    method: str,
    url: str,
    payload: dict | None = None,
    timeout: int = 600,
) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"detail": raw}
        return exc.code, parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repo-url", default="https://github.com/pallets/itsdangerous")
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--skip-delete", action="store_true")
    parser.add_argument("--ask-count", type=int, default=5)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    steps: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        steps.append((name, ok, detail))
        mark = "OK" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))

    status, body = _request(opener, "GET", f"{base}/api/v1/health", timeout=30)
    record("health", status == 200 and body.get("status") == "ok", str(status))

    status, body = _request(opener, "GET", f"{base}/api/v1/client", timeout=30)
    client_id = body.get("client_id") if status == 200 else ""
    record(
        "client session",
        status == 200 and len(client_id or "") == 36,
        client_id[:8] + "…" if client_id else str(body),
    )
    if not client_id:
        _summary(steps)
        return 1

    status, body = _request(
        opener,
        "POST",
        f"{base}/api/v1/repository",
        {"repo_url": args.repo_url, "user_id": "tester"},
        timeout=60,
    )
    record("reject legacy tester id", status == 400, body.get("detail", status))

    repository_id = ""
    if not args.skip_index:
        polls = 0
        action = ""
        while polls < 36:
            status, body = _request(
                opener,
                "POST",
                f"{base}/api/v1/repository",
                {"repo_url": args.repo_url, "user_id": client_id},
                timeout=600,
            )
            if status != 201:
                record("index", False, str(body))
                break
            action = body.get("action") or ""
            repository_id = body.get("repository_id") or repository_id
            if action == "serve":
                record("index", True, f"repo={repository_id[:8]}…")
                break
            if action != "wait":
                record("index", False, body.get("message", action))
                break
            polls += 1
            time.sleep(5)
        else:
            record("index", False, "timeout waiting for serve")
    else:
        status, body = _request(
            opener,
            "POST",
            f"{base}/api/v1/repository",
            {"repo_url": args.repo_url, "user_id": client_id},
            timeout=120,
        )
        if status == 201 and body.get("action") == "serve":
            repository_id = body.get("repository_id") or ""
            record("index lookup", True, repository_id[:8] + "…")
        else:
            record("index lookup", False, str(body))

    if not repository_id:
        _summary(steps)
        return 1

    status, body = _request(opener, "GET", f"{base}/api/v1/repositories?user_id={client_id}", timeout=60)
    repos = body.get("repositories") or []
    found = any(r.get("repository_id") == repository_id for r in repos)
    record("list repositories", status == 200 and found, f"count={len(repos)}")

    questions = [
        "Which functions call derive_key?",
        "What does URLSafeSerializer do?",
        "How does timed signature validation work?",
        "Where is BadSignature raised or handled?",
        "What module defines signing interfaces?",
    ][: max(1, args.ask_count)]

    served = 0
    for index, question in enumerate(questions, start=1):
        status, body = _request(
            opener,
            "POST",
            f"{base}/api/v1/ask",
            {"repository_id": repository_id, "user_id": client_id, "question": question},
            timeout=600,
        )
        ok = status == 200 and body.get("action") == "serve" and bool((body.get("answer") or "").strip())
        if ok:
            served += 1
        record(f"ask #{index}", ok, f"citations={len(body.get('citations') or [])}")

    status, body = _request(
        opener,
        "GET",
        f"{base}/api/v1/repositories/{repository_id}/messages?user_id={client_id}",
        timeout=60,
    )
    messages = body.get("messages") or []
    record(
        "chat history",
        status == 200 and len(messages) >= served,
        f"stored={len(messages)} served_asks={served}",
    )

    if not args.skip_delete:
        status, body = _request(
            opener,
            "DELETE",
            f"{base}/api/v1/repositories/{repository_id}?user_id={client_id}",
            timeout=120,
        )
        record("delete repository", status == 204, str(status))
        status, body = _request(opener, "GET", f"{base}/api/v1/repositories?user_id={client_id}", timeout=60)
        gone = not any(r.get("repository_id") == repository_id for r in (body.get("repositories") or []))
        record("list after delete", status == 200 and gone, f"count={len(body.get('repositories') or [])}")

    _summary(steps)
    return 0 if all(item[1] for item in steps) else 1


def _summary(steps: list[tuple[str, bool, str]]) -> None:
    failed = [name for name, ok, _ in steps if not ok]
    print()
    print(f"Steps: {len(steps)}  Failed: {len(failed)}")
    if failed:
        print("Failures:", ", ".join(failed))


if __name__ == "__main__":
    sys.exit(main())
