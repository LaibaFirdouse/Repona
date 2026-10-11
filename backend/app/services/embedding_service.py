from sentence_transformers import SentenceTransformer

from app.schemas import Chunk
from app.schemas import ChunkEmbedding


class EmbeddingService:
    """
    Generates semantic embeddings for repository chunks.

    Responsibilities:
    - Load the embedding model.
    - Generate embeddings for repository chunks.
    - Return ChunkEmbedding DTOs.

    Does NOT:
    - Save embeddings.
    - Retrieve embeddings.
    - Call an LLM.
    """

    DEFAULT_MODEL = "all-MiniLM-L6-v2"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
    ):
        self.model = SentenceTransformer(model_name)
    def generate_text_embedding(
        self,
        text: str,
    ) -> list[float]:
        """
        Generate an embedding for arbitrary text.
        """

        embedding = self.model.encode(
            text,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        return embedding.tolist()

    def generate_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts with the same model and the same settings as one text."""
        if not texts:
            return []
        encoded = self.model.encode(
            texts,
            convert_to_numpy=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return [row.tolist() for row in encoded]

    def generate_embedding(
        self,
        chunk: Chunk,
    ) -> ChunkEmbedding:
        """
        Generate an embedding for a single chunk.
        """

        embedding = self.model.encode(
            chunk.content,
            convert_to_numpy=True,
        )

        return ChunkEmbedding(
            chunk=chunk,
            embedding=embedding.tolist(),
        )

    def generate_embeddings(
        self,
        chunks: list[Chunk],
    ) -> list[ChunkEmbedding]:
        """
        Generate embeddings for multiple chunks using batch inference.
        """

        if not chunks:
            return []

        contents = [
            chunk.content
            for chunk in chunks
        ]

        embeddings = self.model.encode(
            contents,
            convert_to_numpy=True,
        )

        return [
            ChunkEmbedding(
                chunk=chunk,
                embedding=embedding.tolist(),
            )
            for chunk, embedding in zip(
                chunks,
                embeddings,
                strict=True,
            )
        ]