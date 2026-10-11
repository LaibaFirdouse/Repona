"""Write one generation's Neo4j nodes and edges.

Content nodes are merged by id. An existing node's identity properties stay as they are.
A relationship is updated only when its writer_epoch is missing or older.
"""

from __future__ import annotations

from app.ingestion.assemble import EdgeRecord, GenerationDraft
from app.storage.identity import edge_id

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


def write_graph(
    session,
    repository_id: str,
    source_revision_id_value: str,
    generation_id: str,
    draft: GenerationDraft,
    epoch: int,
) -> None:
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
    for directory in draft.directories:
        session.run(
            """
            MERGE (n:Directory {id: $id})
            ON CREATE SET n.repository_id = $repository_id, n.relative_path = $relative_path, n.name = $name
            """,
            id=directory.id,
            repository_id=repository_id,
            relative_path=directory.relative_path,
            name=directory.name,
        )
    for file in draft.files:
        session.run(
            """
            MERGE (n:File {id: $id})
            ON CREATE SET n.repository_id = $repository_id, n.relative_path = $relative_path
            """,
            id=file.id,
            repository_id=repository_id,
            relative_path=file.relative_path,
        )
    for symbol in draft.symbols:
        label = LABEL_BY_KIND[symbol.kind]
        session.run(
            f"""
            MERGE (n:{label} {{id: $id}})
            ON CREATE SET
                n.repository_id = $repository_id,
                n.kind = $kind,
                n.name = $name,
                n.qualified_name = $qualified_name,
                n.relative_path = $relative_path,
                n.start_line = $start_line,
                n.end_line = $end_line
            """,
            id=symbol.id,
            repository_id=repository_id,
            kind=symbol.kind,
            name=symbol.name,
            qualified_name=symbol.qualified_name,
            relative_path=symbol.relative_path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
        )
    for edge in draft.edges:
        _merge_edge(session, edge, generation_id, epoch)


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


def _merge_edge(session, edge: EdgeRecord, generation_id: str, epoch: int) -> None:
    if edge.relationship not in _EDGE_TYPES:
        raise ValueError(f"unknown relationship {edge.relationship}")
    session.run(
        f"""
        MATCH (source {{id: $source_id}}), (target {{id: $target_id}})
        MERGE (source)-[rel:{edge.relationship} {{edge_id: $edge_id}}]->(target)
        SET rel.index_generation_id = CASE
                WHEN rel.writer_epoch IS NULL OR rel.writer_epoch < $epoch THEN $generation_id
                ELSE rel.index_generation_id END,
            rel.mentioned_name = CASE
                WHEN rel.writer_epoch IS NULL OR rel.writer_epoch < $epoch THEN $mentioned_name
                ELSE rel.mentioned_name END,
            rel.resolution = CASE
                WHEN rel.writer_epoch IS NULL OR rel.writer_epoch < $epoch THEN $resolution
                ELSE rel.resolution END,
            rel.writer_epoch = CASE
                WHEN rel.writer_epoch IS NULL OR rel.writer_epoch < $epoch THEN $epoch
                ELSE rel.writer_epoch END
        """,
        source_id=edge.source_id,
        target_id=edge.target_id,
        edge_id=edge.edge_id,
        generation_id=generation_id,
        mentioned_name=edge.mentioned_name,
        resolution=edge.resolution,
        epoch=epoch,
    )
