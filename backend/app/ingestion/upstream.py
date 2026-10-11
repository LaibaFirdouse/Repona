"""Ask GitHub or GitLab for a branch head without storing the caller's token."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.ingestion.coordination import classify_git_failure


@dataclass(frozen=True)
class Probe:
    upstream: str
    sha: str | None
    branch: str | None = None


def ls_remote(url: str, branch: str, token: str, runner=subprocess.run, timeout: int = 30) -> Probe:
    _reject_branch(branch)
    command = _command(url, token, "ls-remote", url, f"refs/heads/{branch}")
    return _probe(command, runner, timeout)


def ls_remote_head(url: str, token: str, runner=subprocess.run, timeout: int = 30) -> Probe:
    """Read the repository's default branch. A public URL needs no token."""
    command = _command(url, token, "ls-remote", "--symref", url, "HEAD")
    try:
        completed = _run(command, runner, timeout)
    except subprocess.TimeoutExpired:
        return Probe("unavailable", None)
    if completed.returncode != 0:
        return Probe(_failure(completed), None)
    sha = _sha(completed.stdout)
    branch = _default_branch(completed.stdout)
    if sha is None or branch is None:
        return Probe("unavailable", None)
    return Probe("authorized", sha, branch)


def fetch_branch(git_dir: Path, url: str, branch: str, token: str, runner=subprocess.run, timeout: int = 1800) -> str:
    _reject_branch(branch)
    git_dir.mkdir(parents=True, exist_ok=True)
    if not (git_dir / "HEAD").exists():
        init = runner(["git", "init", "--bare", str(git_dir)], check=False, capture_output=True, timeout=timeout)
        if init.returncode != 0:
            raise RuntimeError(_stderr(init) or "git init failed")
    ref = f"refs/heads/{branch}"
    command = _command(url, token, f"--git-dir={git_dir}", "fetch", "--depth", "1", url, f"+{ref}:{ref}")
    completed = _run(command, runner, timeout)
    if completed.returncode != 0:
        raise RuntimeError(_stderr(completed) or "git fetch failed")
    shown = runner(
        ["git", f"--git-dir={git_dir}", "rev-parse", ref],
        check=False,
        capture_output=True,
        timeout=timeout,
    )
    sha = shown.stdout.decode("utf-8", errors="replace").strip()
    if shown.returncode != 0 or len(sha) not in {40, 64}:
        raise RuntimeError(_stderr(shown) or "fetched branch has no commit")
    return sha


def _command(url: str, token: str, *git_args: str) -> list[str]:
    if token and token in url:
        raise ValueError("the token must not be part of the repository URL")
    command = ["git", "-c", "credential.helper="]
    if token:
        command.extend(["-c", f"http.extraHeader=Authorization: Bearer {token}"])
    command.extend(git_args)
    return command


def _probe(command: list[str], runner, timeout: int) -> Probe:
    try:
        completed = _run(command, runner, timeout)
    except subprocess.TimeoutExpired:
        return Probe("unavailable", None)
    if completed.returncode == 0:
        sha = _sha(completed.stdout)
        if sha is None:
            return Probe("unavailable", None)
        return Probe("authorized", sha)
    return Probe(_failure(completed), None)


def _failure(completed) -> str:
    text = _stderr(completed).lower()
    permission = any(
        phrase in text
        for phrase in (
            "authentication failed",
            "permission denied",
            "could not read username",
            "terminal prompts disabled",
            "repository not found",
            "403",
            "401",
        )
    )
    status_code = 401 if "401" in text else 403 if "403" in text or permission else None
    return classify_git_failure(status_code, permission, timed_out=False)


def _run(command: list[str], runner, timeout: int):
    if runner is subprocess.run:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout,
            env=environment,
        )
    return runner(command, check=False, capture_output=True, timeout=timeout)


def _default_branch(stdout: bytes) -> str | None:
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("ref:"):
            continue
        ref = line.split("\t", 1)[0][4:].strip()
        prefix = "refs/heads/"
        if not ref.startswith(prefix):
            continue
        branch = ref[len(prefix) :]
        try:
            _reject_branch(branch)
        except ValueError:
            return None
        return branch
    return None


def _sha(stdout: bytes) -> str | None:
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        sha = line.split("\t", 1)[0].strip().lower()
        if len(sha) in {40, 64} and all(character in "0123456789abcdef" for character in sha):
            return sha
    return None


def _stderr(completed) -> str:
    return completed.stderr.decode("utf-8", errors="replace").strip()


def _reject_branch(branch: str) -> None:
    if not branch or branch.startswith("-") or any(part in {"", ".", ".."} for part in branch.split("/")):
        raise ValueError("tracked branch is not a branch name")
