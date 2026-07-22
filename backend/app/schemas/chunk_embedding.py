from dataclasses import dataclass

from app.schemas.chunk import Chunk


@dataclass(slots=True)
class ChunkEmbedding:
    """
    Represents a repository chunk together with its embedding vector.
    """

    chunk: Chunk
    embedding: list[float]