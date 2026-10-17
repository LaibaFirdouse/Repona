"""Decisions for refresh leases, reader pins, serving, and work-tree recovery."""

from __future__ import annotations

from datetime import datetime, timedelta

LEASE = timedelta(minutes=10)
HEARTBEAT_STALE = timedelta(minutes=2)


def lease_deadline(now: datetime) -> datetime:
    return now + LEASE


def refresh_lease_held(lease_expires_at: datetime | None, heartbeat_at: datetime | None, now: datetime) -> bool:
    """True while an indexer is likely still running (lease valid and heartbeat fresh)."""
    if lease_expires_at is None or lease_expires_at <= now:
        return False
    if heartbeat_at is None:
        return True
    return (now - heartbeat_at) <= HEARTBEAT_STALE


def serving_action(upstream: str, remote_sha: str | None, active_sha: str | None) -> str:
    if upstream == "unauthorized":
        return "revoke"
    if upstream == "unavailable":
        return "unavailable"
    if upstream != "authorized":
        raise ValueError(f"unknown upstream result {upstream}")
    if remote_sha and active_sha and remote_sha == active_sha:
        return "serve"
    return "wait"


def classify_git_failure(status_code: int | None, permission_denied: bool, timed_out: bool) -> str:
    if permission_denied or status_code in {401, 403}:
        return "unauthorized"
    if timed_out or status_code in {429, 500, 502, 503, 504}:
        return "unavailable"
    if status_code is not None and 500 <= status_code <= 599:
        return "unavailable"
    return "unavailable"


def pin_may_insert(gc_claimed_at, snapshot_state: str, snapshot_path: str | None, expires_at: datetime, hard_deadline: datetime) -> bool:
    return (
        gc_claimed_at is None
        and snapshot_state == "ready"
        and snapshot_path is not None
        and expires_at <= hard_deadline
    )


def pin_may_renew(gc_claimed_at, snapshot_state: str, new_expires_at: datetime, hard_deadline: datetime) -> bool:
    return gc_claimed_at is None and snapshot_state == "ready" and new_expires_at <= hard_deadline


def gc_may_claim(
    status: str,
    generation_id: str,
    active_generation_id: str | None,
    refresh_generation_id: str | None,
    gc_claimed_at,
    snapshot_state: str,
    unexpired_pin: bool,
) -> bool:
    return (
        status in {"retired", "failed"}
        and generation_id not in {active_generation_id, refresh_generation_id}
        and gc_claimed_at is None
        and snapshot_state != "deleting"
        and not unexpired_pin
    )


def gc_may_delete_worktree(
    snapshot_path: str | None,
    other_live_generation: bool,
    is_active_or_refresh: bool,
    unexpired_pin: bool,
) -> bool:
    return (
        snapshot_path is not None
        and not other_live_generation
        and not is_active_or_refresh
        and not unexpired_pin
    )


def creation_recovery_action(publish_exists: bool, creating_exists: bool, validation_passed: bool) -> str:
    if publish_exists and not creating_exists and validation_passed:
        return "publish"
    return "discard"


def refresh_may_activate(refresh_state: str, refresh_generation_id: str | None, generation_id: str, refresh_owner: str | None, owner: str, refresh_epoch: int, epoch: int, lease_expires_at: datetime | None, now: datetime) -> bool:
    return (
        refresh_state == "running"
        and refresh_generation_id == generation_id
        and refresh_owner == owner
        and refresh_epoch == epoch
        and lease_expires_at is not None
        and lease_expires_at > now
    )
