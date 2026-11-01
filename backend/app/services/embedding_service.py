from sentence_transformers import SentenceTransformer


class EmbeddingDimensionError(RuntimeError):
    """The loaded model's vectors do not have the configured size."""


class EmbeddingService:
    """Loads one sentence-transformer and embeds question and chunk text."""

    DEFAULT_MODEL = "Alibaba-NLP/gte-modernbert-base"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        revision: str | None = None,
        max_seq_length: int | None = None,
        expected_dimensions: int | None = None,
    ):
        self.model = SentenceTransformer(model_name, revision=revision)
        if max_seq_length:
            # Bounds CPU time per chunk. Chunks are ~2 KB, about 500-700 tokens of code, so 512 keeps
            # nearly all of each chunk; longer-context models would otherwise pad to thousands.
            current = self.model.max_seq_length or max_seq_length
            self.model.max_seq_length = min(current, max_seq_length)
        if expected_dimensions is not None:
            actual = self.model.get_sentence_embedding_dimension()
            if actual is not None and actual != expected_dimensions:
                raise EmbeddingDimensionError(
                    f"{model_name} produces {actual}-dimensional vectors but EMBEDDING_DIMENSIONS is "
                    f"{expected_dimensions}; set EMBEDDING_DIMENSIONS={actual}"
                )

    def generate_text_embedding(self, text: str) -> list[float]:
        embedding = self.model.encode(
            text,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return embedding.tolist()

    def generate_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        encoded = self.model.encode(
            texts,
            convert_to_numpy=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return [row.tolist() for row in encoded]
