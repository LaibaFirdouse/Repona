import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.code_chunk import CodeChunk
from app.services.embedding_service import EmbeddingService


class RetrievalService:
    """
    Retrieves the most relevant code chunks for a query.
    """

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.embedding_service = (
            embedding_service or EmbeddingService()
        )

    def generate_query_embedding(
        self,
        query: str,
    ) -> list[float]:
        return self.embedding_service.generate_text_embedding(query)

    def retrieve_chunks(
        self,
        repository_id: str,
        query: str,
        db: Session,
        top_k: int = 5,
    ) -> list[CodeChunk]:

        query_embedding = np.array(
            self.generate_query_embedding(query)
        )

        chunks = (
            db.execute(
                select(CodeChunk).where(
                    CodeChunk.repository_id == repository_id
                )
            )
            .scalars()
            .all()
        )

        if not chunks:
            return []

        scored_chunks = []

        for chunk in chunks:
            chunk_embedding = np.array(chunk.embedding)

            similarity = np.dot(
                query_embedding,
                chunk_embedding,
            ) / (
                np.linalg.norm(query_embedding)
                * np.linalg.norm(chunk_embedding)
            )

            scored_chunks.append(
                (similarity, chunk)
            )

        scored_chunks.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        return [
            chunk
            for _, chunk in scored_chunks[:top_k]
        ]