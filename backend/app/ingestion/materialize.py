"""Read a commit from Git and publish a detached work tree of its blob bytes."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.ingestion.assemble import SourceBlob
from app.ingestion.inclusion import is_included


class SnapshotError(RuntimeError):
    pass


def read_commit_blobs(git_dir: Path, commit_sha: str) -> list[SourceBlob]:
    listing = _git(git_dir, "ls-tree", "-r", "-z", commit_sha)
    listed: list[tuple[str, str, str]] = []
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        meta, path_bytes = entry.split(b"\t", 1)
        mode, object_type, blob_id = meta.decode("utf-8").split(" ", 2)
        if object_type != "blob":
            continue
        listed.append((mode, blob_id, path_bytes.decode("utf-8")))
    payloads = _cat_file_batch(git_dir, [blob_id for _, blob_id, _ in listed])
    return [
        SourceBlob(relative_path=path, mode=mode, git_blob_id=blob_id, payload=payload)
        for (mode, blob_id, path), payload in zip(listed, payloads, strict=True)
    ]


def write_detached_tree(git_dir: Path, commit_sha: str, creating: Path) -> list[SourceBlob]:
    if creating.exists():
        shutil.rmtree(creating)
    creating.parent.mkdir(parents=True, exist_ok=True)
    _git(git_dir, "worktree", "add", "--detach", str(creating), commit_sha, text=False)
    blobs = read_commit_blobs(git_dir, commit_sha)
    for blob in blobs:
        if not is_included(blob.relative_path, blob.mode):
            continue
        target = creating / blob.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.payload)
    _ensure_gitfile(git_dir, creating)
    return blobs


def _ensure_gitfile(git_dir: Path, creating: Path) -> None:
    """Keep the detached work tree linked when the gitfile is removed during checkout."""
    admin = git_dir / "worktrees" / creating.name
    if not (admin / "HEAD").is_file():
        raise SnapshotError("detached work tree was not registered")
    gitfile = creating / ".git"
    gitfile.write_text(f"gitdir: {admin.resolve()}\n")
    (admin / "gitdir").write_text(str(gitfile.resolve()) + "\n")


def _refresh_publish_gitdir(publish: Path) -> None:
    gitfile = publish / ".git"
    if not gitfile.is_file():
        return
    line = gitfile.read_text().strip()
    if not line.startswith("gitdir:"):
        return
    admin = Path(line.split(":", 1)[1].strip())
    pointer = admin / "gitdir"
    if pointer.parent.is_dir():
        pointer.write_text(str(gitfile.resolve()) + "\n")


def promote_tree(creating: Path, publish: Path) -> None:
    if publish.exists():
        raise SnapshotError(f"snapshot already exists: {publish}")
    creating.rename(publish)
    _refresh_publish_gitdir(publish)


def adopt_or_promote_tree(
    git_dir: Path,
    commit_sha: str,
    creating: Path,
    publish: Path,
    blobs: list[SourceBlob],
) -> None:
    """Promote a creating work tree, or reuse an on-disk publish path from a prior crash."""
    from app.ingestion.validation import ChecklistError, validate_worktree_bytes

    if publish.exists():
        try:
            validate_worktree_bytes(git_dir, commit_sha, publish, blobs)
        except ChecklistError:
            shutil.rmtree(publish)
        else:
            if creating.exists():
                shutil.rmtree(creating)
            _refresh_publish_gitdir(publish)
            return
    promote_tree(creating, publish)


def publish_worktree(git_dir: Path, commit_sha: str, destination: Path) -> None:
    creating = destination.with_name(destination.name + ".creating")
    write_detached_tree(git_dir, commit_sha, creating)
    promote_tree(creating, destination)


def _cat_file_batch(git_dir: Path, blob_ids: list[str]) -> list[bytes]:
    """Read blob bytes in one git process. The bytes are the stored objects."""
    if not blob_ids:
        return []
    process = subprocess.Popen(
        ["git", f"--git-dir={git_dir}", "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate("".join(f"{blob_id}\n" for blob_id in blob_ids).encode())
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise SnapshotError(message or "git cat-file --batch failed")
    payloads: list[bytes] = []
    offset = 0
    for blob_id in blob_ids:
        newline = stdout.find(b"\n", offset)
        if newline < 0:
            raise SnapshotError(f"git cat-file batch ended before {blob_id}")
        header = stdout[offset:newline].decode("utf-8", errors="replace")
        offset = newline + 1
        parts = header.split(" ")
        if len(parts) < 2 or parts[1] == "missing":
            raise SnapshotError(f"missing blob {blob_id}")
        size = int(parts[2])
        payload = stdout[offset : offset + size]
        offset += size
        if len(payload) != size or offset >= len(stdout) or stdout[offset] != 10:
            raise SnapshotError(f"short blob {blob_id}")
        offset += 1
        payloads.append(payload)
    return payloads


def _git(git_dir: Path, *args: str, text: bool = False) -> bytes:
    completed = subprocess.run(
        ["git", f"--git-dir={git_dir}", *args],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise SnapshotError(message or f"git {' '.join(args)} failed")
    return completed.stdout
