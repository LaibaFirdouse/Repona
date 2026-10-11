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
    blobs: list[SourceBlob] = []
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        meta, path_bytes = entry.split(b"\t", 1)
        mode, object_type, blob_id = meta.decode("utf-8").split(" ", 2)
        if object_type != "blob":
            continue
        payload = _git(git_dir, "cat-file", "blob", blob_id)
        blobs.append(
            SourceBlob(
                relative_path=path_bytes.decode("utf-8"),
                mode=mode,
                git_blob_id=blob_id,
                payload=payload,
            )
        )
    return blobs


def write_detached_tree(git_dir: Path, commit_sha: str, creating: Path) -> None:
    if creating.exists():
        shutil.rmtree(creating)
    creating.parent.mkdir(parents=True, exist_ok=True)
    _git(git_dir, "worktree", "add", "--detach", str(creating), commit_sha, text=False)
    for blob in read_commit_blobs(git_dir, commit_sha):
        if not is_included(blob.relative_path, blob.mode):
            continue
        target = creating / blob.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.payload)


def promote_tree(creating: Path, publish: Path) -> None:
    if publish.exists():
        raise SnapshotError(f"snapshot already exists: {publish}")
    creating.rename(publish)


def publish_worktree(git_dir: Path, commit_sha: str, destination: Path) -> None:
    creating = destination.with_name(destination.name + ".creating")
    write_detached_tree(git_dir, commit_sha, creating)
    promote_tree(creating, destination)


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
