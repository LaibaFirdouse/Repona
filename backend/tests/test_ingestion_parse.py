import unittest

from app.ingestion.assemble import SourceBlob, assemble_generation
from app.ingestion.parse import parse_file
from app.storage.identity import repository_id


REPO = str(repository_id("https://github.com/org/repo"))
GENERATION = "11111111-1111-4111-8111-111111111111"


def blob(path: str, payload: str, mode: str = "100644") -> SourceBlob:
    return SourceBlob(path, mode, "abc123", payload.encode("utf-8"))


class ParseTests(unittest.TestCase):
    def test_empty_supported_file_is_parsed_with_an_empty_module(self) -> None:
        parsed = parse_file("app.py", b"")
        self.assertEqual(parsed.parse_status, "parsed")
        self.assertEqual(len(parsed.symbols), 1)
        symbol = parsed.symbols[0]
        self.assertEqual(symbol.kind, "module")
        self.assertEqual((symbol.start_byte, symbol.end_byte), (0, 0))

    def test_syntax_error_with_no_recovery_has_no_module(self) -> None:
        parsed = parse_file("app.py", b"def (")
        self.assertEqual(parsed.parse_status, "syntax_error")
        self.assertIsNotNone(parsed.parse_message)
        self.assertEqual(parsed.symbols, [])

    def test_json_is_not_parsed(self) -> None:
        parsed = parse_file("package.json", b"{}")
        self.assertEqual(parsed.parse_status, "not_parsed")
        self.assertEqual(parsed.symbols, [])

    def test_python_method_is_not_parented_by_the_module(self) -> None:
        parsed = parse_file("app.py", b"class Box:\n    def open(self):\n        return 1\n")
        kinds = {symbol.kind: symbol for symbol in parsed.symbols}
        self.assertEqual(kinds["method"].qualified_name, "Box.open")
        self.assertEqual(kinds["method"].parent_index, parsed.symbols.index(kinds["class"]))
        self.assertIsNone(kinds["class"].parent_index)


class AssembleTests(unittest.TestCase):
    def test_resolved_call_and_import_become_one_edge_each(self) -> None:
        draft = assemble_generation(
            REPO,
            "a" * 40,
            [
                blob("pkg.py", "def helper():\n    return 1\n"),
                blob("app.py", "import pkg\n\ndef run():\n    helper()\n    helper()\n"),
            ],
            "f" * 64,
            GENERATION,
        )
        calls = [edge for edge in draft.edges if edge.relationship == "CALLS" and edge.mentioned_name == "helper"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].resolution, "resolved")
        imports = [edge for edge in draft.edges if edge.relationship == "IMPORTS"]
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0].resolution, "resolved")
        self.assertTrue(any(edge.relationship == "DEPENDS_ON" for edge in draft.edges))

    def test_unresolved_call_ends_at_the_file(self) -> None:
        draft = assemble_generation(
            REPO,
            "b" * 40,
            [blob("app.py", "def run():\n    missing()\n")],
            "f" * 64,
            GENERATION,
        )
        call = next(edge for edge in draft.edges if edge.relationship == "CALLS")
        file_id = next(item.id for item in draft.files if item.relative_path == "app.py")
        self.assertEqual(call.resolution, "unresolved")
        self.assertEqual(call.target_id, file_id)
        self.assertFalse(any(edge.relationship == "DEPENDS_ON" for edge in draft.edges))

    def test_module_symbol_has_no_chunk(self) -> None:
        draft = assemble_generation(
            REPO,
            "c" * 40,
            [blob("app.py", "def run():\n    return 1\n")],
            "f" * 64,
            GENERATION,
        )
        module_ids = {symbol.id for symbol in draft.symbols if symbol.kind == "module"}
        self.assertTrue(module_ids)
        self.assertFalse(any(chunk.symbol_id in module_ids for chunk in draft.chunks))
        self.assertTrue(any(chunk.kind == "symbol_body" for chunk in draft.chunks))


if __name__ == "__main__":
    unittest.main()
