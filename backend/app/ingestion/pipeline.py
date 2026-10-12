"""Index one commit into PostgreSQL, and into Neo4j and Qdrant when those clients are supplied."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ingestion.assemble import assemble_generation
from app.ingestion.checkpoint import ParseCache
from app.ingestion.claims import (
    abort_snapshot_claim,
    claim_snapshot,
    publish_snapshot_claim,
    recover_creating_snapshot,
    recover_deleting_snapshot,
)
from app.ingestion.coordination import lease_deadline
from app.ingestion.graph_store import current_epoch_edge_ids, delete_edges_outside_expected, publish_graph_epoch, write_graph
from app.ingestion.inclusion import is_included
from app.ingestion.materialize import promote_tree, read_commit_blobs, write_detached_tree
from app.ingestion.persist import (
    GenerationWriteError,
    activate_generation,
    ensure_source_revision,
    insert_pending_generation,
    write_draft,
)
from app.ingestion.validation import (
    ChecklistError,
    edge_set_matches,
    generation_link_is_valid,
    point_is_valid,
    validate_worktree_bytes,
)
from app.ingestion.vector_store import collection_config, ensure_collection, fetch_point_payloads, upsert_missing_points
from app.ingestion.versions import (
    CHUNKING_VERSION,
    INCLUSION_RULES_VERSION,
    LEXICAL_INDEX_VERSION,
    PARSER_VERSION,
)
from app.models.catalog import GenerationChunk, GenerationDirectory, GenerationFile, IndexGeneration, SourceRevision
from app.models.repository import Repository
from app.storage.identity import (
    collection_name,
    edge_id,
    embedding_config_fingerprint,
    index_generation_id,
    point_id,
    source_revision_id,
    without_nul,
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
    now = datetime.utcnow()
    repository = db.query(Repository).filter(Repository.id == repository_id).with_for_update().one()
    if repository.refresh_state == "running":
        if repository.refresh_lease_expires_at and repository.refresh_lease_expires_at > now:
            raise GenerationWriteError("a refresh is already running for this repository")
        raise GenerationWriteError("an expired refresh must be adopted before another build starts")
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
    repository.refresh_heartbeat_at = now
    repository.refresh_lease_expires_at = lease_deadline(now)
    revision = ensure_source_revision(
        db,
        repository_id,
        commit_sha,
        repository.tracked_branch,
        snapshot_path=None,
    )
    if revision.snapshot_state == "deleting":
        recover_deleting_snapshot(revision, owner, now)
        if revision.snapshot_state == "deleting":
            raise GenerationWriteError("work tree deletion is still in progress")
    if revision.snapshot_state == "creating":
        recover_creating_snapshot(revision, owner, now, git_dir, commit_sha)
    snapshot_mode = claim_snapshot(revision, owner, snapshot_root, now)
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
    repository.refresh_generation_id = generation_key
    db.commit()
    _finish_generation(
        db,
        repository_id=repository_id,
        revision_key=revision_key,
        generation_key=generation_key,
        owner=owner,
        epoch=epoch,
        git_dir=git_dir,
        commit_sha=commit_sha,
        snapshot_mode=snapshot_mode,
        fingerprint=fingerprint,
        snapshot_root=snapshot_root,
        embed=embed,
        embedding_model=embedding_model,
        vector_dimensions=vector_dimensions,
        qdrant_url=qdrant_url,
        graph_session=graph_session,
    )
    return generation_key


def complete_owned_refresh(
    db: Session,
    *,
    repository_id: str,
    owner: str,
    git_dir: Path,
    embed,
    embedding_model: str,
    vector_dimensions: int,
    snapshot_root: Path,
    qdrant_url: str | None,
    graph_session,
) -> str:
    """Finish the pending generation after this worker has adopted its expired lease."""
    now = datetime.utcnow()
    repository = db.query(Repository).filter(Repository.id == repository_id).with_for_update().one()
    if repository.refresh_state != "running" or repository.refresh_owner != owner:
        raise GenerationWriteError("refresh owner no longer holds this generation")
    if repository.refresh_lease_expires_at is None or repository.refresh_lease_expires_at <= now:
        raise GenerationWriteError("refresh lease has expired")
    generation_key = repository.refresh_generation_id
    generation = db.get(IndexGeneration, generation_key)
    if generation is None or generation.status != "pending":
        raise GenerationWriteError("pending generation is missing")
    revision = db.get(SourceRevision, generation.source_revision_id)
    if revision.snapshot_state == "deleting":
        raise GenerationWriteError("work tree deletion is still in progress")
    if revision.snapshot_state == "creating":
        recover_creating_snapshot(revision, owner, now, git_dir, revision.commit_sha)
    if revision.snapshot_state == "ready" and revision.snapshot_path:
        snapshot_mode = "reuse"
    else:
        snapshot_mode = claim_snapshot(revision, owner, snapshot_root, now)
    epoch = repository.refresh_epoch
    db.commit()
    _finish_generation(
        db,
        repository_id=repository_id,
        revision_key=revision.id,
        generation_key=generation_key,
        owner=owner,
        epoch=epoch,
        git_dir=git_dir,
        commit_sha=revision.commit_sha,
        snapshot_mode=snapshot_mode,
        fingerprint=generation.embedding_config_fingerprint,
        snapshot_root=snapshot_root,
        embed=embed,
        embedding_model=embedding_model,
        vector_dimensions=vector_dimensions,
        qdrant_url=qdrant_url,
        graph_session=graph_session,
    )
    return generation_key


def _finish_generation(
    db: Session,
    *,
    repository_id: str,
    revision_key: str,
    generation_key: str,
    owner: str,
    epoch: int,
    git_dir: Path,
    commit_sha: str,
    snapshot_mode: str,
    fingerprint: str,
    snapshot_root: Path | None = None,
    embed=None,
    embedding_model: str,
    vector_dimensions: int,
    qdrant_url: str | None,
    graph_session,
) -> None:
    try:
        revision = db.get(SourceRevision, revision_key)
        if snapshot_mode == "create":
            creating = Path(revision.creating_path)
            publish = Path(revision.publish_path)
            created_blobs = write_detached_tree(git_dir, commit_sha, creating)
            validate_worktree_bytes(git_dir, commit_sha, creating, created_blobs)
            promote_tree(creating, publish)
            publish_snapshot_claim(revision, owner)
            db.commit()
            revision = db.get(SourceRevision, revision_key)
            blobs = created_blobs
        else:
            blobs = read_commit_blobs(git_dir, commit_sha)
        validate_worktree_bytes(git_dir, commit_sha, Path(revision.snapshot_path), blobs)
        _renew_lease(db, repository_id, owner, epoch, datetime.utcnow())
        cache = None
        if snapshot_root is not None:
            cache = ParseCache(Path(snapshot_root).parent / "parse-cache" / repository_id)
        draft = assemble_generation(
            repository_id,
            commit_sha,
            blobs,
            fingerprint,
            generation_key,
            on_progress=lambda: _renew_lease(db, repository_id, owner, epoch, datetime.utcnow()),
            cache=cache,
        )
        renew = lambda: _renew_lease(db, repository_id, owner, epoch, datetime.utcnow())
        write_draft(db, repository_id, generation_key, fingerprint, draft, on_progress=renew)
        renew()
        if _lost_ownership(db, repository_id, owner, epoch):
            raise GenerationWriteError("refresh owner no longer holds this generation")
        if graph_session is None:
            raise ChecklistError("neo4j session is required")
        write_graph(graph_session, repository_id, revision.id, generation_key, draft, epoch, on_progress=renew)
        expected = {item.edge_id for item in draft.edges}
        expected.add(str(edge_id("HAS_GENERATION", generation_key, revision.id, generation_key)))
        delete_edges_outside_expected(graph_session, generation_key, list(expected), epoch)
        present = current_epoch_edge_ids(graph_session, generation_key, epoch)
        if not edge_set_matches(present, expected):
            raise ChecklistError("neo4j edge set does not match this generation")
        _assert_catalog(db, generation_key, fingerprint, draft, blobs)
        if draft.chunks:
            if qdrant_url is None:
                raise ChecklistError("qdrant is required when the generation has chunks")
            collection = collection_name(embedding_model, vector_dimensions)
            ensure_collection(qdrant_url, collection, vector_dimensions)
            renew_embed = lambda: _renew_lease(db, repository_id, owner, epoch, datetime.utcnow())
            for start in range(0, len(draft.chunks), 256):
                part = draft.chunks[start : start + 256]
                vectors = _embed_chunks(embed, part, renew_embed)
                upsert_missing_points(
                    qdrant_url,
                    collection,
                    repository_id,
                    draft,
                    fingerprint,
                    vectors,
                    chunks=part,
                )
            _assert_points(qdrant_url, collection, repository_id, generation_key, fingerprint, vector_dimensions, draft, db)
        publish_graph_epoch(graph_session, generation_key, epoch)
        _renew_lease(db, repository_id, owner, epoch, datetime.utcnow())
        activate_generation(db, repository_id, generation_key, owner, epoch, datetime.utcnow())
        db.commit()
    except Exception:
        db.rollback()
        revision = db.get(SourceRevision, revision_key)
        if revision is not None and revision.snapshot_state == "creating" and revision.creation_owner == owner:
            abort_snapshot_claim(revision, owner)
            db.commit()
        _fail_if_owner(db, repository_id, generation_key, owner, epoch)
        raise


def _embed_chunks(embed, chunks, renew) -> dict[str, list[float]]:
    """Embed chunk text with the same model. A batch is several texts, not a different model."""
    many = getattr(embed, "many", None)
    vectors: dict[str, list[float]] = {}
    if many is None:
        for index, chunk in enumerate(chunks):
            if index and index % 100 == 0:
                renew()
            vectors[chunk.id] = [float(value) for value in embed(without_nul(chunk.lexical_text))]
        return vectors
    for start in range(0, len(chunks), 32):
        if start:
            renew()
        batch = chunks[start : start + 32]
        encoded = many([without_nul(chunk.lexical_text) for chunk in batch])
        if len(encoded) != len(batch):
            raise ChecklistError("the embedding model returned a different number of vectors")
        for chunk, vector in zip(batch, encoded, strict=True):
            vectors[chunk.id] = [float(value) for value in vector]
    return vectors


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


def adopt_expired_refresh(db: Session, repository_id: str, owner: str, now: datetime | None = None) -> int:
    current = now or datetime.utcnow()
    repository = db.query(Repository).filter(Repository.id == repository_id).with_for_update().one()
    if repository.refresh_state != "running" or repository.refresh_generation_id is None:
        raise GenerationWriteError("there is no refresh to adopt")
    if repository.refresh_lease_expires_at and repository.refresh_lease_expires_at > current:
        raise GenerationWriteError("the refresh lease is still held")
    repository.refresh_epoch += 1
    repository.refresh_owner = owner
    repository.refresh_heartbeat_at = current
    repository.refresh_lease_expires_at = lease_deadline(current)
    db.commit()
    return repository.refresh_epoch


def _renew_lease(db: Session, repository_id: str, owner: str, epoch: int, now: datetime) -> None:
    repository = db.query(Repository).filter(Repository.id == repository_id).with_for_update().one()
    if repository.refresh_owner != owner or repository.refresh_epoch != epoch:
        raise GenerationWriteError("refresh owner no longer holds this generation")
    if repository.refresh_lease_expires_at and repository.refresh_lease_expires_at <= now:
        raise GenerationWriteError("refresh lease has expired")
    repository.refresh_heartbeat_at = now
    repository.refresh_lease_expires_at = lease_deadline(now)
    db.flush()


def _lost_ownership(db: Session, repository_id: str, owner: str, epoch: int) -> bool:
    repository = db.get(Repository, repository_id)
    return repository.refresh_owner != owner or repository.refresh_epoch != epoch


def _assert_catalog(db: Session, generation_id: str, fingerprint: str, draft, blobs) -> None:
    included = [blob for blob in blobs if is_included(blob.relative_path, blob.mode)]
    file_links = (
        db.query(func.count(GenerationFile.file_id))
        .filter(GenerationFile.index_generation_id == generation_id)
        .scalar()
    )
    if file_links != len(included):
        raise ChecklistError("generation file set does not match the included tree")
    directories = db.query(GenerationDirectory.directory_id).filter(GenerationDirectory.index_generation_id == generation_id).all()
    if {row[0] for row in directories} != {item.id for item in draft.directories}:
        raise ChecklistError("generation directories do not match the included tree")
    link_ids = {
        row[0]
        for row in db.query(GenerationChunk.chunk_id)
        .filter(GenerationChunk.index_generation_id == generation_id)
        .yield_per(1000)
    }
    if link_ids != {item.id for item in draft.chunks}:
        raise ChecklistError("generation chunks do not match the parsed spans")
    bad = (
        db.query(GenerationChunk.chunk_id)
        .filter(
            GenerationChunk.index_generation_id == generation_id,
            (GenerationChunk.embedding_config_fingerprint != fingerprint) | (GenerationChunk.search_vector.is_(None)),
        )
        .first()
    )
    if bad is not None:
        raise ChecklistError("lexical row does not match this generation")


def _assert_points(qdrant_url, collection, repository_id, generation_id, fingerprint, dimensions, draft, db: Session) -> None:
    size, distance = collection_config(qdrant_url, collection)
    payloads_checked = 0
    link_rows = db.query(
        GenerationChunk.chunk_id,
        GenerationChunk.embedding_config_fingerprint,
        GenerationChunk.embedding_input_hash,
    ).filter(GenerationChunk.index_generation_id == generation_id).all()
    links = {row.chunk_id: row for row in link_rows}
    files = {
        row[0]
        for row in db.query(GenerationFile.file_id).filter(GenerationFile.index_generation_id == generation_id).all()
    }
    identifiers = [str(point_id(chunk.id, chunk.embedding_input_hash, fingerprint)) for chunk in draft.chunks]
    for start in range(0, len(draft.chunks), 256):
        part = list(draft.chunks[start : start + 256])
        part_ids = identifiers[start : start + 256]
        payloads = fetch_point_payloads(qdrant_url, collection, part_ids)
        for chunk, identifier in zip(part, part_ids, strict=True):
            payload = payloads.get(identifier)
            if payload is None or not point_is_valid(
                payload,
                chunk_id_value=chunk.id,
                repository_id=repository_id,
                file_id_value=chunk.file_id,
                parser_version=PARSER_VERSION,
                chunking_version=CHUNKING_VERSION,
                span_hash=chunk.span_hash,
                embedding_input_hash=chunk.embedding_input_hash,
                fingerprint=fingerprint,
                point_identifier=identifier,
                collection_size=size,
                collection_distance=distance,
                expected_size=dimensions,
            ):
                raise ChecklistError(f"point for chunk {chunk.id} failed validation")
            link = links[chunk.id]
            if not generation_link_is_valid(
                link_exists=True,
                link_fingerprint=link.embedding_config_fingerprint,
                link_input_hash=link.embedding_input_hash,
                payload_fingerprint=payload["embedding_config_fingerprint"],
                payload_input_hash=payload["embedding_input_hash"],
                generation_fingerprint=fingerprint,
                file_linked=chunk.file_id in files,
            ):
                raise ChecklistError(f"generation link for chunk {chunk.id} failed validation")
            payloads_checked += 1
    if payloads_checked != len(draft.chunks):
        raise ChecklistError("generation chunks do not match the parsed spans")


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
