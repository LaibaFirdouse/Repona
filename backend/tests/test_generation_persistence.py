import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ingestion.assemble import GenerationDraft
from app.ingestion.persist import GenerationWriteError, write_draft
from app.models.catalog import IndexGeneration
from app.retrieval.capability_profile import load_capability_profile


class CapabilityReportPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        IndexGeneration.__table__.create(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    @staticmethod
    def make_generation(
        generation_id: str,
        repository_id: str,
    ) -> IndexGeneration:
        return IndexGeneration(
            id=generation_id,
            repository_id=repository_id,
            source_revision_id="revision-1",
            parser_version="test",
            chunking_version="test",
            embedding_model="test-model",
            vector_dimensions=3,
            embedding_input_version="test",
            embedding_config_fingerprint="a" * 64,
            lexical_index_version="test",
            inclusion_rules_version="test",
            build_attempt=1,
            qdrant_collection="test-collection",
            status="pending",
        )

    def test_report_survives_commit_and_fresh_session_reload(self) -> None:
        generation_id = "generation-1"
        repository_id = "repository-1"

        report = {
            "generation_id": "wrong-generation",
            "repository_id": "wrong-repository",
            "languages": [
                {
                    "language": "python",
                    "file_count": 3,
                    "level": 2,
                }
            ],
            "frameworks": [],
            "notes": ["database round-trip test"],
        }

        draft = GenerationDraft(capability_report=report)

        with self.SessionLocal() as db:
            db.add(self.make_generation(generation_id, repository_id))
            db.commit()

            write_draft(
                db,
                repository_id,
                generation_id,
                "a" * 64,
                draft,
            )
            db.commit()

        # Verify persistence through a genuinely fresh session.
        with self.SessionLocal() as fresh_db:
            generation = fresh_db.get(IndexGeneration, generation_id)

            self.assertIsNotNone(generation)

            profile = load_capability_profile(fresh_db, generation)

            self.assertEqual(profile.source, "report")
            self.assertEqual(profile.generation_id, generation_id)
            self.assertEqual(profile.repository_id, repository_id)
            self.assertEqual(profile.languages, {"python": 3})
            self.assertIn("database round-trip test", profile.notes)

    def test_missing_generation_is_rejected(self) -> None:
        draft = GenerationDraft(capability_report={"languages": []})

        with self.SessionLocal() as db:
            with self.assertRaisesRegex(
                GenerationWriteError,
                "does not exist",
            ):
                write_draft(
                    db,
                    "repository-1",
                    "missing-generation",
                    "a" * 64,
                    draft,
                )

    def test_repository_mismatch_is_rejected(self) -> None:
        generation_id = "generation-2"
        draft = GenerationDraft(capability_report={"languages": []})

        with self.SessionLocal() as db:
            db.add(self.make_generation(generation_id, "repository-1"))
            db.commit()

            with self.assertRaisesRegex(
                GenerationWriteError,
                "belongs to repository",
            ):
                write_draft(
                    db,
                    "repository-2",
                    generation_id,
                    "a" * 64,
                    draft,
                )


if __name__ == "__main__":
    unittest.main()
