"""Remove Neo4j nodes for a repository."""

from __future__ import annotations

from app.core.config import settings


def delete_repository_graph(repository_id: str) -> None:
    if not settings.neo4j_password:
        return
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    try:
        with driver.session() as session:
            session.run(
                "MATCH (n) WHERE n.repository_id = $repository_id DETACH DELETE n",
                repository_id=repository_id,
            )
            session.run(
                """
                MATCH (repo:Repository {id: $repository_id})-[:HAS_SOURCE]->(rev:SourceRevision)
                DETACH DELETE rev
                """,
                repository_id=repository_id,
            )
            session.run(
                "MATCH (n) WHERE n.id = $repository_id DETACH DELETE n",
                repository_id=repository_id,
            )
    finally:
        driver.close()
