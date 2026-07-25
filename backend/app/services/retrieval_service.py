import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.code_chunk import CodeChunk
from app.services.embedding_service import EmbeddingService


class RetrievalService:
    """
    Retrieves the most semantically relevant code chunks
    for a user query using cosine similarity.
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
        """
        Generate an embedding for the user's question.
        """
        return self.embedding_service.generate_text_embedding(
            query
        )

    def cosine_similarity(
        self,
        embedding1: np.ndarray,
        embedding2: np.ndarray,
    ) -> float:
        """
        Compute cosine similarity between two embeddings.
        """

        denominator = (
            np.linalg.norm(embedding1)
            * np.linalg.norm(embedding2)
        )

        if denominator == 0:
            return 0.0

        return float(
            np.dot(
                embedding1,
                embedding2,
            )
            / denominator
        )

    def retrieve_chunks(
        self,
        repository_id: str,
        query: str,
        db: Session,
        top_k: int = 2,
    ) -> list[CodeChunk]:
        """
        Retrieve the Top-K most relevant code chunks
        for a given repository and query.
        """

        print("\n========== RETRIEVAL ==========")
        print(f"Question: {query}")

        query_embedding = np.array(
            self.generate_query_embedding(query)
        )

        print("Generated query embedding.")

        chunks = (
            db.execute(
                select(CodeChunk).where(
                    CodeChunk.repository_id == repository_id
                )
            )
            .scalars()
            .all()
        )

        print(f"Loaded {len(chunks)} chunks.")

        if not chunks:
            print("No chunks found.")
            return []

        scored_chunks = []

        for chunk in chunks:
            similarity = self.cosine_similarity(
                query_embedding,
                np.array(chunk.embedding),
            )

            scored_chunks.append(
                (
                    similarity,
                    chunk,
                )
            )

        scored_chunks.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        top_chunks = [
            chunk
            for _, chunk in scored_chunks[:top_k]
        ]

        print(f"Returning Top {len(top_chunks)} chunks.")

        return top_chunks