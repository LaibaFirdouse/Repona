from sentence_transformers import SentenceTransformer


class EmbeddingService:
    """Loads one sentence-transformer and embeds question and chunk text."""

    DEFAULT_MODEL = "all-MiniLM-L6-v2"

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model = SentenceTransformer(model_name)

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
