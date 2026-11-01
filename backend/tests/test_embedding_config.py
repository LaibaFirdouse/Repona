"""Embedding input prefix, configurable embedding model, and the stale-generation guard."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.config import settings
from app.storage.identity import (
    INPUT_RECIPE,
    display_prefix,
    embedding_config_fingerprint,
    embedding_prefix,
)


class EmbeddingPrefixTests(unittest.TestCase):
    def test_symbol_chunks_start_with_path_and_symbol(self) -> None:
        self.assertEqual(
            embedding_prefix("backend/app/ingestion/inclusion.py", "is_included", "relative_path, mode"),
            "backend/app/ingestion/inclusion.py :: is_included(relative_path, mode)",
        )

    def test_file_level_chunks_get_the_path(self) -> None:
        self.assertEqual(embedding_prefix("README.md", None, None), "README.md")

    def test_display_prefix_drops_the_path_the_prompt_already_shows(self) -> None:
        path = "src/pkg/signer.py"
        self.assertEqual(display_prefix(f"{path} :: Signer.sign(self, value)", path), "Signer.sign(self, value)")
        self.assertEqual(display_prefix(path, path), "")
        # Prefixes stored by older generations (no path) are shown unchanged.
        self.assertEqual(display_prefix("Signer.sign(self, value)", path), "Signer.sign(self, value)")

    def test_input_recipe_names_the_path(self) -> None:
        self.assertTrue(INPUT_RECIPE.startswith("relative_path :: "))


class EmbeddingSettingsTests(unittest.TestCase):
    def test_current_fingerprint_follows_settings(self) -> None:
        from app.services.refresh_service import current_embedding_fingerprint

        expected = embedding_config_fingerprint(
            settings.embedding_model, settings.embedding_model_revision, settings.embedding_dimensions
        )
        self.assertEqual(current_embedding_fingerprint(), expected)


class StaleGenerationGuardTests(unittest.TestCase):
    def test_vectors_are_used_only_for_generations_built_with_the_current_model(self) -> None:
        from app.retrieval.answer import AnswerService
        from app.services.refresh_service import current_embedding_fingerprint

        def fake_embed(_text):
            return [0.0]

        service = AnswerService(embed=fake_embed)
        current = SimpleNamespace(embedding_config_fingerprint=current_embedding_fingerprint())
        stale = SimpleNamespace(embedding_config_fingerprint="fingerprint-of-another-model")
        self.assertIs(service._embed_for(current), fake_embed)
        self.assertIsNone(service._embed_for(stale))

    def test_stale_generation_skips_the_vector_lane_without_touching_qdrant(self) -> None:
        from app.retrieval.answer import AnswerService

        calls: list = []
        service = AnswerService(embed=lambda text: calls.append(text) or [0.0], search=lambda *a: calls.append(a) or [])
        stale = SimpleNamespace(id="gen-1", embedding_config_fingerprint="fingerprint-of-another-model")
        self.assertEqual(service._vector_hits(None, stale, "how does signing work"), [])
        self.assertEqual(calls, [])


class _FakeModel:
    def __init__(self, dimensions: int, max_seq_length: int) -> None:
        self.dimensions = dimensions
        self.max_seq_length = max_seq_length

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimensions


class EmbeddingServiceTests(unittest.TestCase):
    def test_max_sequence_length_is_capped(self) -> None:
        from app.services import embedding_service

        with patch.object(embedding_service, "SentenceTransformer", return_value=_FakeModel(768, 8192)) as loader:
            service = embedding_service.EmbeddingService("some/model", max_seq_length=512, expected_dimensions=768)
        self.assertEqual(service.model.max_seq_length, 512)
        loader.assert_called_once_with("some/model", revision=None)

    def test_a_shorter_model_window_is_not_raised(self) -> None:
        from app.services import embedding_service

        with patch.object(embedding_service, "SentenceTransformer", return_value=_FakeModel(384, 256)):
            service = embedding_service.EmbeddingService("small/model", max_seq_length=512)
        self.assertEqual(service.model.max_seq_length, 256)

    def test_dimension_mismatch_fails_with_a_clear_message(self) -> None:
        from app.services import embedding_service

        with patch.object(embedding_service, "SentenceTransformer", return_value=_FakeModel(384, 256)):
            with self.assertRaises(embedding_service.EmbeddingDimensionError) as raised:
                embedding_service.EmbeddingService("small/model", expected_dimensions=768)
        self.assertIn("EMBEDDING_DIMENSIONS=384", str(raised.exception))

    def test_revision_is_passed_through(self) -> None:
        from app.services import embedding_service

        with patch.object(embedding_service, "SentenceTransformer", return_value=_FakeModel(768, 512)) as loader:
            embedding_service.EmbeddingService("some/model", revision="abc123")
        loader.assert_called_once_with("some/model", revision="abc123")


if __name__ == "__main__":
    unittest.main()
