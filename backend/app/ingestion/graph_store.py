"""Write one generation's Neo4j nodes and edges.

Content nodes are merged by id. An existing node's identity properties stay as they are.
A relationship is updated only when its writer_epoch is missing or older.
"""

from __future__ import annotations

import time

from app.ingestion.assemble import GenerationDraft
from app.storage.identity import edge_id, without_nul

LABEL_BY_KIND = {
    "module": "Module",
    "class": "Class",
    "function": "Function",
    "method": "Method",
    "interface": "Symbol",
    "type": "Symbol",
    "enum": "Symbol",
    "constant": "Symbol",
}

_EDGE_TYPES = ("CONTAINS", "DECLARES", "IMPORTS", "DEPENDS_ON", "CALLS", "REFERENCES", "HAS_GENERATION")


def ensure_constraints(session) -> None:
    labels = ("Repository", "SourceRevision", "IndexGeneration", "Directory", "File", *set(LABEL_BY_KIND.values()))
    for label in labels:
        session.run(
            f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        )


def _touch(on_progress, renewed: float) -> float:
    if on_progress is None or time.monotonic() - renewed < 30:
        return renewed
    on_progress()
    return time.monotonic()


def write_graph(
    session,
    repository_id: str,
    source_revision_id_value: str,
    generation_id: str,
    draft: GenerationDraft,
    epoch: int,
    on_progress=None,
) -> None:
    renewed = time.monotonic()
    ensure_constraints(session)
    session.run(
        """
        MERGE (repository:Repository {id: $repository_id})
        MERGE (revision:SourceRevision {id: $revision_id})
        MERGE (repository)-[:HAS_SOURCE]->(revision)
        MERGE (generation:IndexGeneration {id: $generation_id})
        ON CREATE SET generation.repository_id = $repository_id
        MERGE (revision)-[link:HAS_GENERATION {index_generation_id: $generation_id}]->(generation)
        SET link.edge_id = CASE
                WHEN link.writer_epoch IS NULL OR link.writer_epoch < $epoch THEN $edge_id
                ELSE link.edge_id END,
            link.writer_epoch = CASE
                WHEN link.writer_epoch IS NULL OR link.writer_epoch < $epoch THEN $epoch
                ELSE link.writer_epoch END
        """,
        repository_id=repository_id,
        revision_id=source_revision_id_value,
        generation_id=generation_id,
        edge_id=str(edge_id("HAS_GENERATION", generation_id, source_revision_id_value, generation_id)),
        epoch=epoch,
    )
    renewed = _write_nodes(
        session,
        """
        UNWIND $rows AS row
        MERGE (n:Directory {id: row.id})
        ON CREATE SET n.repository_id = $repository_id, n.relative_path = row.relative_path, n.name = row.name
        """,
        [
            {"id": directory.id, "relative_path": directory.relative_path, "name": directory.name}
            for directory in draft.directories
        ],
        renewed,
        on_progress,
        repository_id=repository_id,
    )
    renewed = _write_nodes(
        session,
        """
        UNWIND $rows AS row
        MERGE (n:File {id: row.id})
        ON CREATE SET n.repository_id = $repository_id, n.relative_path = row.relative_path
        """,
        [{"id": file.id, "relative_path": file.relative_path} for file in draft.files],
        renewed,
        on_progress,
        repository_id=repository_id,
    )
    symbols_by_label: dict[str, list[dict]] = {}
    for symbol in draft.symbols:
        symbols_by_label.setdefault(LABEL_BY_KIND[symbol.kind], []).append(
            {
                "id": symbol.id,
                "kind": symbol.kind,
                "name": symbol.name,
                "qualified_name": symbol.qualified_name,
                "relative_path": symbol.relative_path,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
            }
        )
    for label, rows in symbols_by_label.items():
        renewed = _write_nodes(
            session,
            f"""
            UNWIND $rows AS row
            MERGE (n:{label} {{id: row.id}})
            ON CREATE SET
                n.repository_id = $repository_id,
                n.kind = row.kind,
                n.name = row.name,
                n.qualified_name = row.qualified_name,
                n.relative_path = row.relative_path,
                n.start_line = row.start_line,
                n.end_line = row.end_line
            """,
            rows,
            renewed,
            on_progress,
            repository_id=repository_id,
        )
    _write_edges(session, draft, generation_id, epoch, on_progress, renewed)


def _write_nodes(session, statement: str, rows: list[dict], renewed: float, on_progress, **common) -> float:
    batch = 800
    for start in range(0, len(rows), batch):
        renewed = _touch(on_progress, renewed)
        session.run(statement, rows=rows[start : start + batch], **common)
    return renewed


def _write_edges(session, draft: GenerationDraft, generation_id: str, epoch: int, on_progress, renewed: float) -> None:
    labels = {directory.id: "Directory" for directory in draft.directories}
    labels.update({file.id: "File" for file in draft.files})
    for symbol in draft.symbols:
        labels[symbol.id] = LABEL_BY_KIND[symbol.kind]
    labels[generation_id] = "IndexGeneration"
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for edge in draft.edges:
        if edge.relationship not in _EDGE_TYPES:
            raise ValueError(f"unknown relationship {edge.relationship}")
        source_label = labels.get(edge.source_id)
        target_label = labels.get(edge.target_id)
        if source_label is None or target_label is None:
            raise ValueError(f"edge {edge.edge_id} points outside this generation")
        grouped.setdefault((edge.relationship, source_label, target_label), []).append(
            {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "edge_id": edge.edge_id,
                "mentioned_name": without_nul(edge.mentioned_name),
                "resolution": edge.resolution,
            }
        )
    for (relationship, source_label, target_label), rows in grouped.items():
        statement = f"""
        UNWIND $rows AS row
        MATCH (source:{source_label} {{id: row.source_id}}), (target:{target_label} {{id: row.target_id}})
        MERGE (source)-[rel:{relationship} {{edge_id: row.edge_id}}]->(target)
        SET rel.index_generation_id = CASE
                WHEN rel.writer_epoch IS NULL OR rel.writer_epoch < $epoch THEN $generation_id
                ELSE rel.index_generation_id END,
            rel.mentioned_name = CASE
                WHEN rel.writer_epoch IS NULL OR rel.writer_epoch < $epoch THEN row.mentioned_name
                ELSE rel.mentioned_name END,
            rel.resolution = CASE
                WHEN rel.writer_epoch IS NULL OR rel.writer_epoch < $epoch THEN row.resolution
                ELSE rel.resolution END,
            rel.writer_epoch = CASE
                WHEN rel.writer_epoch IS NULL OR rel.writer_epoch < $epoch THEN $epoch
                ELSE rel.writer_epoch END
        """
        batch = 800
        for start in range(0, len(rows), batch):
            renewed = _touch(on_progress, renewed)
            session.run(
                statement,
                rows=rows[start : start + batch],
                generation_id=generation_id,
                epoch=epoch,
            )


SERVING_EDGE_FILTER = """
MATCH (generation:IndexGeneration {id: $index_generation_id})
MATCH ()-[rel]-()
WHERE rel.index_generation_id = generation.id
  AND rel.writer_epoch = generation.graph_epoch
"""


def delete_edges_outside_expected(session, generation_id: str, expected_edge_ids: list[str], epoch: int) -> None:
    session.run(
        """
        MATCH ()-[rel]->()
        WHERE rel.index_generation_id = $generation_id
          AND (
            NOT rel.edge_id IN $expected
            OR rel.writer_epoch IS NULL
            OR rel.writer_epoch < $epoch
          )
        DELETE rel
        """,
        generation_id=generation_id,
        expected=expected_edge_ids,
        epoch=epoch,
    )


def current_epoch_edge_ids(session, generation_id: str, epoch: int) -> set[str]:
    records = session.run(
        """
        MATCH ()-[rel]->()
        WHERE rel.index_generation_id = $generation_id
          AND rel.writer_epoch = $epoch
        RETURN rel.edge_id AS edge_id
        """,
        generation_id=generation_id,
        epoch=epoch,
    )
    return {record["edge_id"] for record in records}


def publish_graph_epoch(session, generation_id: str, epoch: int) -> None:
    session.run(
        """
        MATCH (generation:IndexGeneration {id: $generation_id})
        SET generation.graph_epoch = $epoch
        """,
        generation_id=generation_id,
        epoch=epoch,
    )
