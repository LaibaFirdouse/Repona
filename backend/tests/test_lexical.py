import unittest

from app.ingestion.lexical import (
    code_search_text,
    identifier_parts,
    is_compound_identifier,
    lexical_query_words,
    tsquery_term,
)


class IdentifierPartsTests(unittest.TestCase):
    def test_camel_case_keeps_the_joined_word(self) -> None:
        self.assertEqual(identifier_parts("deriveKey"), ["derive", "key", "derivekey"])

    def test_snake_and_dotted_names_split_on_separators(self) -> None:
        self.assertEqual(identifier_parts("derive_key"), ["derive", "key"])
        self.assertEqual(identifier_parts("self.signer.sign"), ["self", "signer", "sign"])

    def test_acronym_runs_split_before_the_next_word(self) -> None:
        self.assertEqual(identifier_parts("HTTPServer"), ["http", "server", "httpserver"])

    def test_compound_detection(self) -> None:
        for token in ("deriveKey", "derive_key", "a.b", "HTTPServer", "_private_name"):
            self.assertTrue(is_compound_identifier(token), token)
        for token in ("signer", "Signer", "HTTP", "__init__", "_x"):
            self.assertFalse(is_compound_identifier(token), token)


class CodeSearchTextTests(unittest.TestCase):
    def test_appends_parts_of_compound_identifiers(self) -> None:
        text = "value = self.signer.sign(deriveKey(secret_key))"
        searchable = code_search_text(text)
        self.assertTrue(searchable.startswith(text))
        appended = searchable[len(text):].split()
        for word in ("signer", "sign", "derive", "key", "derivekey", "secret"):
            self.assertIn(word, appended)

    def test_each_part_is_appended_once(self) -> None:
        searchable = code_search_text("a.signer.sign(x)\nb.signer.sign(y)")
        appended = searchable.split("\n")[-1].split()
        self.assertEqual(appended.count("signer"), 1)

    def test_plain_text_is_unchanged(self) -> None:
        text = "return value if ready else None"
        self.assertEqual(code_search_text(text), text)
        self.assertEqual(code_search_text(""), "")


class LexicalQueryWordsTests(unittest.TestCase):
    def test_stopwords_are_dropped_from_prose(self) -> None:
        words = lexical_query_words(["How does Repona decide which files get indexed?"])
        self.assertEqual(words, ["repona", "decide", "indexed"])

    def test_compound_identifier_parts_are_kept_even_if_generic(self) -> None:
        words = lexical_query_words("Where is handle_request called from Signer.sign?")
        self.assertEqual(words[:4], ["handle", "request", "called", "signer"])
        self.assertIn("sign", words)
        self.assertNotIn("where", words)

    def test_words_are_distinct_across_queries_and_capped(self) -> None:
        words = lexical_query_words(["signer key", "signer salt", "alpha beta gamma delta epsilon"], limit=4)
        self.assertEqual(words, ["signer", "key", "salt", "alpha"])

    def test_short_and_numeric_tokens_are_skipped(self) -> None:
        self.assertEqual(lexical_query_words(["an id of 42 or io"]), [])

    def test_noise_parts_are_dropped(self) -> None:
        self.assertEqual(lexical_query_words(["self.store"]), ["store"])


class TsqueryTermTests(unittest.TestCase):
    def test_three_letter_words_stay_exact(self) -> None:
        self.assertEqual(tsquery_term("key"), "key")
        self.assertEqual(tsquery_term("api"), "api")

    def test_longer_words_become_lightly_stemmed_prefixes(self) -> None:
        self.assertEqual(tsquery_term("indexed"), "index:*")
        self.assertEqual(tsquery_term("parsing"), "pars:*")
        self.assertEqual(tsquery_term("classes"), "class:*")
        self.assertEqual(tsquery_term("files"), "file:*")
        self.assertEqual(tsquery_term("signer"), "signer:*")

    def test_short_stems_are_not_cut(self) -> None:
        self.assertEqual(tsquery_term("uses"), "uses:*")
        self.assertEqual(tsquery_term("sign"), "sign:*")

    def test_terms_are_valid_tsquery_operands(self) -> None:
        for word in lexical_query_words(["What does Signer.sign(value) return for HTTPServer's 'derive_key'?"]):
            term = tsquery_term(word)
            self.assertRegex(term, r"^[a-z0-9]+(:\*)?$")


if __name__ == "__main__":
    unittest.main()
