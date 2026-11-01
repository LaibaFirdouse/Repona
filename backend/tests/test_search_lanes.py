"""Search lanes: full-text OR query, name-lane ordering, path-hint matching."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from app.retrieval.answer import _name_hits, _text_hits, retrieval_identifiers
from app.retrieval.catalog_context import _like_escape, catalog_path_filters


def _sql(expression) -> str:
    """Render with literal values. The pyformat dialect doubles '%' in literals; undo that."""
    compiled = expression.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return str(compiled).replace("%%", "%")


class TextLaneTests(unittest.TestCase):
    def test_question_words_are_ored_not_anded(self) -> None:
        db = MagicMock()
        chain = db.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value
        chain.all.return_value = [
            SimpleNamespace(chunk_id="c1", symbol_id="s1", file_id="f1"),
            SimpleNamespace(chunk_id="c2", symbol_id=None, file_id="f2"),
        ]
        hits = _text_hits(db, SimpleNamespace(id="gen-1"), ["How does Signer.sign derive the key?"])

        self.assertEqual([(hit.anchor_id, hit.chunk_id, hit.rank) for hit in hits], [("s1", "c1", 1), ("f2", "c2", 2)])
        filter_args = db.query.return_value.join.return_value.filter.call_args.args
        sql = " ".join(_sql(arg) for arg in filter_args)
        self.assertIn("to_tsquery('simple', 'signer:* | sign:* | derive:* | key')", sql)
        self.assertNotIn("plainto_tsquery", sql)

    def test_ranking_counts_distinct_words_before_ts_rank(self) -> None:
        db = MagicMock()
        _text_hits(db, SimpleNamespace(id="gen-1"), ["signer key"])
        order_args = db.query.return_value.join.return_value.filter.return_value.order_by.call_args.args
        coverage_sql = _sql(order_args[0])
        self.assertIn("CASE WHEN", coverage_sql)
        self.assertIn("to_tsquery('simple', 'signer:*')", coverage_sql)
        self.assertIn("to_tsquery('simple', 'key')", coverage_sql)
        self.assertIn("ts_rank", _sql(order_args[1]))

    def test_a_question_of_only_stopwords_does_not_query(self) -> None:
        db = MagicMock()
        self.assertEqual(_text_hits(db, SimpleNamespace(id="gen-1"), ["how does it work"]), [])
        db.query.assert_not_called()

    def test_a_plain_string_is_accepted(self) -> None:
        db = MagicMock()
        chain = db.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value
        chain.all.return_value = [SimpleNamespace(chunk_id="c1", symbol_id=None, file_id="f1")]
        hits = _text_hits(db, SimpleNamespace(id="gen-1"), "derive_key")
        self.assertEqual([hit.chunk_id for hit in hits], ["c1"])


class NameLaneTests(unittest.TestCase):
    def test_explicit_paths_then_symbols_then_derived_paths(self) -> None:
        db = MagicMock()
        symbols = db.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value
        symbols.all.return_value = [SimpleNamespace(id="sym-1"), SimpleNamespace(id="sym-2")]

        def fake_paths(_db, _generation, text):
            if "backend/app/ingestion/pipeline.py" in text:
                return [("file-derived", "chunk-derived")]
            if "inclusion.py" in text:
                return [("file-explicit", "chunk-explicit")]
            return []

        with patch("app.retrieval.answer._path_file_hits", side_effect=fake_paths):
            hits = _name_hits(
                db,
                SimpleNamespace(id="gen-1", parser_version="v1"),
                "What does inclusion.py do in is_included?",
                ["backend/app/ingestion/pipeline.py"],
            )
        self.assertEqual(
            [hit.anchor_id for hit in hits],
            ["file-explicit", "sym-1", "sym-2", "file-derived"],
        )
        self.assertEqual([hit.rank for hit in hits], [1, 2, 3, 4])

    def test_symbol_match_is_case_insensitive(self) -> None:
        db = MagicMock()
        symbols = db.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value
        symbols.all.return_value = []
        with patch("app.retrieval.answer._path_file_hits", return_value=[]):
            _name_hits(db, SimpleNamespace(id="gen-1", parser_version="v1"), "how does the signer work", None)
        filter_args = db.query.return_value.join.return_value.filter.call_args.args
        sql = " ".join(_sql(arg) for arg in filter_args)
        self.assertIn("lower(symbols.name) IN", sql)
        self.assertIn("'signer'", sql)
        # The old trigram match of a short name against the whole sentence is gone.
        self.assertNotIn("symbols.name %", sql)
        self.assertNotIn("symbols.qualified_name %", sql)

    def test_path_filters_contribute_only_their_file_stem(self) -> None:
        identifiers = retrieval_identifiers("x", ["backend/app/ingestion/inclusion.py", "src/utils/"])
        self.assertEqual(identifiers, ["inclusion", "utils"])


class CatalogPathFilterTests(unittest.TestCase):
    def test_like_escape_handles_wildcards(self) -> None:
        self.assertEqual(_like_escape("rate_limit"), "rate\\_limit")
        self.assertEqual(_like_escape("50%"), "50\\%")

    def test_words_match_file_name_stems_not_path_substrings(self) -> None:
        db = MagicMock()
        rows = db.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value
        rows.all.return_value = []
        catalog_path_filters(db, "gen-1", "How does inclusion decide things?")
        compiled = [
            " ".join(_sql(arg) for arg in call.args)
            for call in db.query.return_value.join.return_value.filter.call_args_list
        ]
        self.assertTrue(any("'%/inclusion.%'" in sql for sql in compiled))
        self.assertFalse(any("'%inclusion%'" in sql for sql in compiled))


if __name__ == "__main__":
    unittest.main()
