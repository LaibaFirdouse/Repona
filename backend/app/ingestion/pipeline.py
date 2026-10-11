"""Index one commit into PostgreSQL, and into Neo4j and Qdrant when those clients are supplied."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.ingestion.assemble import assemble_generation
from app.ingestion.graph_store import publish_graph_epoch, write_graph
from app.ingestion.materialize import publish_worktree, read_commit_blobs
from app.ingestion.persist import (
    GenerationWriteError,
    activate_generation,
    ensure_source_revision,
    insert_pending_generation,
    write_draft,
)
from app.ingestion.vector_store import ensure_collection, upsert_missing_points
from app.ingestion.versions import (
    CHUNKING_VERSION,
    INCLUSION_RULES_VERSION,
    LEXICAL_INDEX_VERSION,
    PARSER_VERSION,
)
from app.models.catalog import IndexGeneration
from app.models.repository import Repository
from app.storage.identity import (
    collection_name,
    embedding_config_fingerprint,
    index_generation_id,
    source_revision_id,
)


def ingest_commit(
    db: Session,
    *,
    repository_id: str,
    git_dir: Path,
    commit_sha: str,
    owner: str,
    embed,
    embedding_model: str,
    vector_dimensions: int,
    snapshot_root: Path,
    model_revision: str = "unversioned",
    qdrant_url: str | None = None,
    graph_session=None,
) -> str:
    repository = db.query(Repository).filter(Repository.id == repository_id).with_for_update().one()
    if repository.refresh_state == "running":
        raise GenerationWriteError("a refresh is already running for this repository")
    fingerprint = embedding_config_fingerprint(embedding_model, model_revision, vector_dimensions)
    revision_key = str(source_revision_id(repository_id, commit_sha))
    attempt = _next_attempt(db, revision_key, fingerprint)
    generation_key = str(
        index_generation_id(
            revision_key,
            PARSER_VERSION,
            CHUNKING_VERSION,
            LEXICAL_INDEX_VERSION,
            INCLUSION_RULES_VERSION,
            fingerprint,
            attempt,
        )
    )
    epoch = repository.refresh_epoch + 1
    repository.refresh_state = "running"
    repository.refresh_epoch = epoch
    repository.refresh_owner = owner
    repository.refresh_generation_id = generation_key
    revision = ensure_source_revision(
        db,
        repository_id,
        commit_sha,
        repository.tracked_branch,
        snapshot_path=None,
    )
    insert_pending_generation(
        db,
        repository_id,
        revision.id,
        generation_key,
        fingerprint,
        embedding_model,
        vector_dimensions,
        collection_name(embedding_model, vector_dimensions),
        attempt,
    )
    db.commit()

    try:
        snapshot_path = snapshot_root / revision.id
        publish_worktree(git_dir, commit_sha, snapshot_path)
        blobs = read_commit_blobs(git_dir, commit_sha)
        draft = assemble_generation(repository_id, commit_sha, blobs, fingerprint, generation_key)
        write_draft(db, repository_id, generation_key, fingerprint, draft)
        revision.snapshot_path = str(snapshot_path)
        revision.snapshot_state = "ready"
        if graph_session is not None:
            write_graph(graph_session, repository_id, revision.id, generation_key, draft, epoch)
            publish_graph_epoch(graph_session, generation_key, epoch)
        if qdrant_url is not None and draft.chunks:
            collection = collection_name(embedding_model, vector_dimensions)
            ensure_collection(qdrant_url, collection, vector_dimensions)
            vectors = {chunk.id: embed(chunk.lexical_text) for chunk in draft.chunks}
            upsert_missing_points(qdrant_url, collection, repository_id, draft, fingerprint, vectors)
        activate_generation(db, repository_id, generation_key, owner, epoch)
        db.commit()
    except Exception:
        db.rollback()
        _fail_if_owner(db, repository_id, generation_key, owner, epoch)
        raise
    return generation_key


def _next_attempt(db: Session, revision_key: str, fingerprint: str) -> int:
    attempts = (
        db.query(IndexGeneration.build_attempt)
        .filter(
            IndexGeneration.source_revision_id == revision_key,
            IndexGeneration.parser_version == PARSER_VERSION,
            IndexGeneration.chunking_version == CHUNKING_VERSION,
            IndexGeneration.embedding_config_fingerprint == fingerprint,
            IndexGeneration.lexical_index_version == LEXICAL_INDEX_VERSION,
            IndexGeneration.inclusion_rules_version == INCLUSION_RULES_VERSION,
        )
        .all()
    )
    if not attempts:
        return 1
    return max(row[0] for row in attempts) + 1


def _fail_if_owner(db: Session, repository_id: str, generation_id: str, owner: str, epoch: int) -> None:
    repository = db.query(Repository).filter(Repository.id == repository_id).with_for_update().one()
    if repository.refresh_owner != owner or repository.refresh_epoch != epoch:
        db.rollback()
        return
    generation = db.get(IndexGeneration, generation_id)
    if generation is not None and generation.status == "pending":
        generation.status = "failed"
        generation.error_message = "ingestion failed"
    repository.refresh_state = "idle"
    repository.refresh_generation_id = None
    repository.refresh_owner = None
    repository.refresh_lease_expires_at = None
    db.commit()
