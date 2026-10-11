"""Index one local git repository and record the eight evaluation questions.

The records say whether the needed evidence reached the packet, whether citation
ids verify, whether the fixed no-claim line was used, and how long retrieval and
the model took. Claim text is stored for a person to read. It is not scored.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from app.core.config import PROJECT_ROOT, settings
from app.db.session import SessionLocal, create_database_tables
from app.evaluation.record import case_record
from app.ingestion.claims import insert_reader_pin
from app.ingestion.coordination import lease_deadline
from app.ingestion.pipeline import ingest_commit
from app.ingestion.secrets import seal_token
from app.ingestion.upstream import Probe
from app.models.catalog import GenerationFile, IndexGeneration, SourceRevision
from app.models.repository import Repository, RepositoryCredential, RepositoryMember, User
from app.retrieval.answer import AnswerService, Cancelled
from app.services.llm.base import LLMProviderError
from app.services.refresh_service import EMBEDDING_MODEL, MODEL_REVISION, VECTOR_DIMENSIONS, RefreshOutcome, RefreshService, _embed
from app.storage.identity import normalize_repository_url, repository_id

EVAL_URL = "https://example.com/repona/eval-fixture"
USER_ID = "eval-user"
OWNER = "eval-owner"
STALE_POINT_ID = "11111111-1111-1111-1111-111111111111"


def questions() -> list[dict]:
    return [
        {
            "id": "exact_function",
            "question": "issue_token",
            "needed": [{"kind": "span", "qualified_name": "issue_token"}],
        },
        {
            "id": "conceptual",
            "question": "How does a returning visitor remain signed in?",
            "needed": [{"kind": "span", "qualified_name": "renew_session"}],
        },
        {
            "id": "callers_and_callees",
            "question": "What calls issue_token and what does issue_token call?",
            "needed": [
                {"kind": "span", "qualified_name": "open_gate"},
                {"kind": "span", "qualified_name": "lookup_account"},
            ],
        },
        {
            "id": "dependencies",
            "question": "Which libraries does this project depend on?",
            "needed": [{"kind": "technology", "name": "flask"}],
        },
        {
            "id": "unanswerable",
            "question": "Where is the payroll tax table calculated?",
            "needed": [],
        },
        {
            "id": "split_symbol",
            "question": "split_report",
            "needed": [{"kind": "span", "qualified_name": "split_report", "text_marker": "SPLIT_MARKER_TAIL"}],
        },
        {
            "id": "stale_points",
            "question": "Which routine paints the violet ledger?",
            "needed": [{"kind": "span", "qualified_name": "rank_target"}],
        },
    ]


def main() -> None:
    root = Path(PROJECT_ROOT) / "data" / "evaluation"
    repo = root / "fixture"
    snapshot_root = root / "snapshots"
    repo.mkdir(parents=True, exist_ok=True)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    commit = _commit_fixture(repo)
    create_database_tables()
    db = SessionLocal()
    driver = None
    try:
        from neo4j import GraphDatabase

        if not settings.neo4j_password:
            raise SystemExit("NEO4J_PASSWORD is not configured")
        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
        graph = driver.session()
        try:
            generation_id = _ready_generation(db, commit) or _index(db, repo / ".git", commit, snapshot_root, graph)
        finally:
            graph.close()
        records = []
        destination = Path(PROJECT_ROOT) / "evaluation" / "records.json"
        for case in questions():
            if case["id"] == "stale_points":
                _insert_stale_point(db, generation_id, _embed(case["question"]))
            records.append(_ask(db, generation_id, commit, case))
            _write_records(destination, commit, generation_id, records)
        records.append(_ask_during_refresh(db, generation_id))
        _write_records(destination, commit, generation_id, records)
    finally:
        db.close()
        if driver is not None:
            driver.close()
    print(destination)
    for record in records:
        print(
            f"{record['id']}: action={record['action']} "
            f"needed_reached={record['needed_reached']} "
            f"citations={record['citation_ids_verify']} "
            f"no_claim_line={record['no_claim_line']} "
            f"retrieval_s={record['retrieval_seconds']} model_s={record['model_seconds']}"
        )


def _write_records(destination: Path, commit: str, generation_id: str, records: list[dict]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "repository_url": normalize_repository_url(EVAL_URL),
                "commit_sha": commit,
                "generation_id": generation_id,
                "cases": records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _ask(db, generation_id: str, commit: str, case: dict) -> dict:
    repository = _repository(db)
    generation = db.get(IndexGeneration, generation_id)
    revision = db.get(SourceRevision, generation.source_revision_id)
    now = datetime.utcnow()
    deadline = lease_deadline(now)
    pin = insert_reader_pin(
        db,
        repository_id=repository.id,
        generation_id=generation_id,
        source_revision_id_value=revision.id,
        expires_at=deadline,
        hard_deadline=deadline,
    )
    db.commit()
    outcome = RefreshOutcome(
        "serve",
        repository.id,
        repository.normalized_url,
        repository.tracked_branch,
        commit,
        generation_id,
        "The active generation matches this commit.",
        pin.id,
    )
    trace: dict = {}
    try:
        answered = AnswerService().answer(db, case["question"], outcome, trace)
    except (LLMProviderError, Cancelled) as error:
        return case_record(
            case_id=case["id"],
            question=case["question"],
            needed=case["needed"],
            packet_spans=trace.get("packet_spans") or [],
            packet_technologies=trace.get("packet_technologies") or [],
            claims=[],
            citations=[],
            citations_valid=False,
            message="",
            retrieval_seconds=trace.get("retrieval_seconds"),
            model_seconds=trace.get("model_seconds"),
            action="serve",
            error=str(error),
        )
    return case_record(
        case_id=case["id"],
        question=case["question"],
        needed=case["needed"],
        packet_spans=trace.get("packet_spans") or [],
        packet_technologies=trace.get("packet_technologies") or [],
        claims=answered.claims,
        citations=answered.citations,
        citations_valid=answered.citations_valid,
        message=answered.message,
        retrieval_seconds=trace.get("retrieval_seconds"),
        model_seconds=trace.get("model_seconds"),
        action="serve",
    )


def _ask_during_refresh(db, generation_id: str) -> dict:
    repository = _repository(db)
    _member(db, repository.id)
    now = datetime.utcnow()
    repository.refresh_state = "running"
    repository.refresh_owner = OWNER
    repository.refresh_lease_expires_at = now + timedelta(minutes=10)
    db.commit()
    started = perf_counter()

    def probe(url: str, branch: str, token: str) -> Probe:
        return Probe("authorized", "a" * 40)

    outcome = RefreshService(probe=probe, credential_key="eval-key").ask(db, repository.id, USER_ID)
    elapsed = perf_counter() - started
    return case_record(
        case_id="question_during_refresh",
        question="issue_token",
        needed=[],
        packet_spans=[],
        packet_technologies=[],
        claims=[],
        citations=[],
        citations_valid=False,
        message=outcome.message,
        retrieval_seconds=elapsed,
        model_seconds=None,
        action=outcome.action,
    )


def _ready_generation(db, commit: str) -> str | None:
    repository = (
        db.query(Repository)
        .filter(Repository.normalized_url == normalize_repository_url(EVAL_URL))
        .one_or_none()
    )
    if repository is None or repository.active_generation_id is None:
        return None
    generation = db.get(IndexGeneration, repository.active_generation_id)
    revision = None if generation is None else db.get(SourceRevision, generation.source_revision_id)
    if generation is None or revision is None or generation.status != "ready" or revision.commit_sha != commit:
        return None
    return generation.id


def _index(db, git_dir: Path, commit: str, snapshot_root: Path, graph) -> str:
    normalized = normalize_repository_url(EVAL_URL)
    repo_uuid = str(repository_id(normalized))
    existing = db.get(Repository, repo_uuid)
    if existing is None:
        db.add(
            Repository(
                id=repo_uuid,
                normalized_url=normalized,
                provider="github",
                tracked_branch="main",
                git_dir=str(git_dir),
                refresh_state="idle",
                refresh_epoch=0,
            )
        )
        db.commit()
    else:
        existing.git_dir = str(git_dir)
        existing.refresh_state = "idle"
        existing.refresh_owner = None
        existing.refresh_lease_expires_at = None
        db.commit()
    return ingest_commit(
        db,
        repository_id=repo_uuid,
        git_dir=git_dir,
        commit_sha=commit,
        owner=OWNER,
        embed=_embed,
        embedding_model=EMBEDDING_MODEL,
        vector_dimensions=VECTOR_DIMENSIONS,
        snapshot_root=snapshot_root,
        model_revision=MODEL_REVISION,
        qdrant_url=settings.qdrant_url,
        graph_session=graph,
    )


def _insert_stale_point(db, generation_id: str, vector: list[float]) -> None:
    generation = db.get(IndexGeneration, generation_id)
    file_id = (
        db.query(GenerationFile.file_id)
        .filter(GenerationFile.index_generation_id == generation_id)
        .order_by(GenerationFile.file_id)
        .first()
        .file_id
    )
    body = json.dumps(
        {
            "points": [
                {
                    "id": STALE_POINT_ID,
                    "vector": vector,
                    "payload": {
                        "chunk_id": "stale-point",
                        "repository_id": generation.repository_id,
                        "file_id": file_id,
                        "parser_version": generation.parser_version,
                        "chunking_version": generation.chunking_version,
                        "embedding_config_fingerprint": generation.embedding_config_fingerprint,
                        "span_hash": "stale-span",
                        "embedding_input_hash": "stale-input",
                    },
                }
            ]
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.qdrant_url}/collections/{generation.qdrant_collection}/points?wait=true",
        data=body,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def _member(db, repository_id_value: str) -> None:
    if db.get(User, USER_ID) is None:
        db.add(User(id=USER_ID))
        db.flush()
    member = (
        db.query(RepositoryMember)
        .filter(RepositoryMember.user_id == USER_ID, RepositoryMember.repository_id == repository_id_value)
        .one_or_none()
    )
    if member is None:
        db.add(RepositoryMember(user_id=USER_ID, repository_id=repository_id_value, role="owner", status="active"))
    credential = (
        db.query(RepositoryCredential)
        .filter(RepositoryCredential.user_id == USER_ID, RepositoryCredential.repository_id == repository_id_value)
        .one_or_none()
    )
    sealed = seal_token("eval-token", "eval-key")
    if credential is None:
        db.add(
            RepositoryCredential(
                id=str(uuid4()),
                user_id=USER_ID,
                repository_id=repository_id_value,
                provider="github",
                token_ciphertext=sealed,
            )
        )
    else:
        credential.token_ciphertext = sealed
    db.commit()


def _repository(db) -> Repository:
    return db.query(Repository).filter(Repository.normalized_url == normalize_repository_url(EVAL_URL)).one()


def _commit_fixture(repo: Path) -> str:
    if not (repo / ".git").exists():
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "dev@example.com")
        _git(repo, "config", "user.name", "Dev")
    (repo / "auth.py").write_text(
        "def lookup_account(name):\n"
        "    return {'name': name}\n\n"
        "def issue_token(name):\n"
        "    account = lookup_account(name)\n"
        "    return account['name'] + '-token'\n\n"
        "def open_gate(name):\n"
        "    return issue_token(name)\n",
        encoding="utf-8",
    )
    (repo / "session.py").write_text(
        "def renew_session(holder):\n"
        "    holder['cookie'] = holder.get('cookie', 0) + 1\n"
        "    return holder\n\n"
        "def rank_target():\n"
        "    return 17\n",
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    (repo / "report.py").write_text(_split_source(), encoding="utf-8")
    _git(repo, "add", "auth.py", "session.py", "requirements.txt", "report.py")
    if _git(repo, "status", "--porcelain"):
        _git(repo, "commit", "-m", "evaluation fixture")
    return _git(repo, "rev-parse", "HEAD").decode().strip()


def _split_source() -> str:
    lines = ["def split_report():", "    value = 0"]
    while len("\n".join(lines).encode("utf-8")) < 17000:
        lines.append("    value = value + 1")
    lines.append('    marker = "SPLIT_MARKER_TAIL"')
    lines.append("    return marker")
    return "\n".join(lines) + "\n"


def _git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return completed.stdout


if __name__ == "__main__":
    main()
