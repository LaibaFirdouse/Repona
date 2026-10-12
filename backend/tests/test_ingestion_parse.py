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

    def test_parameter_name_is_not_a_reference(self) -> None:
        parsed = parse_file("app.py", b"def greet(user):\n    return helper(user)\n")
        references = [mention.mentioned_name for mention in parsed.mentions if mention.relationship == "REFERENCES"]
        calls = [mention.mentioned_name for mention in parsed.mentions if mention.relationship == "CALLS"]
        self.assertEqual(references, ["user"])
        self.assertEqual(calls, ["helper"])


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

    def test_self_call_resolves_to_the_same_class_method(self) -> None:
        draft = assemble_generation(
            REPO,
            "d" * 40,
            [blob("app.py", "class Box:\n    def open(self):\n        return 1\n    def run(self):\n        self.open()\n")],
            "f" * 64,
            GENERATION,
        )
        call = next(edge for edge in draft.edges if edge.relationship == "CALLS" and edge.mentioned_name == "self.open")
        target = next(symbol.id for symbol in draft.symbols if symbol.qualified_name == "Box.open")
        self.assertEqual(call.resolution, "resolved")
        self.assertEqual(call.target_id, target)

    def test_call_on_another_object_stays_unresolved(self) -> None:
        draft = assemble_generation(
            REPO,
            "e" * 40,
            [blob("app.py", "class Box:\n    def open(self):\n        return 1\n    def run(self, other):\n        other.open()\n")],
            "f" * 64,
            GENERATION,
        )
        call = next(edge for edge in draft.edges if edge.relationship == "CALLS")
        file_id = next(item.id for item in draft.files if item.relative_path == "app.py")
        self.assertEqual(call.resolution, "unresolved")
        self.assertEqual(call.target_id, file_id)

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

    def test_parallel_parse_keeps_serial_ids(self) -> None:
        blobs = [
            blob(f"pkg/file_{index:02d}.py", f"def item_{index}():\n    return {index}\n")
            for index in range(40)
        ]
        blobs.append(blob("pkg/use.py", "from pkg.file_01 import item_1\n\ndef run():\n    return item_1()\n"))
        serial = assemble_generation(REPO, "d" * 40, blobs, "f" * 64, GENERATION, workers=1)
        parallel = assemble_generation(REPO, "d" * 40, blobs, "f" * 64, GENERATION, workers=2)
        self.assertEqual([item.id for item in serial.files], [item.id for item in parallel.files])
        self.assertEqual([item.directory_id for item in serial.files], [item.directory_id for item in parallel.files])
        self.assertEqual([item.id for item in serial.symbols], [item.id for item in parallel.symbols])
        self.assertEqual([item.id for item in serial.chunks], [item.id for item in parallel.chunks])
        self.assertEqual([item.edge_id for item in serial.edges], [item.edge_id for item in parallel.edges])
        self.assertEqual(
            [(item.name, item.evidence_file_id, item.evidence_text) for item in serial.technologies],
            [(item.name, item.evidence_file_id, item.evidence_text) for item in parallel.technologies],
        )

    def test_saved_parse_matches_the_first_run(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from app.ingestion.checkpoint import ParseCache

        blobs = [
            blob(f"pkg/file_{index:02d}.py", f"def item_{index}():\n    return {index}\n")
            for index in range(40)
        ]
        blobs.append(blob("pkg/use.py", "from pkg.file_01 import item_1\n\ndef run():\n    return item_1()\n"))
        with TemporaryDirectory() as directory:
            cache = ParseCache(Path(directory))
            serial = assemble_generation(REPO, "e" * 40, blobs, "f" * 64, GENERATION, workers=1)
            first = assemble_generation(REPO, "e" * 40, blobs, "f" * 64, GENERATION, workers=2, cache=cache)
            stored = [path for path in Path(directory).rglob("*") if path.is_file()]
            second = assemble_generation(REPO, "e" * 40, blobs, "f" * 64, GENERATION, workers=2, cache=cache)
            self.assertEqual(len(stored), 41)
        for draft in (first, second):
            self.assertEqual([item.id for item in serial.files], [item.id for item in draft.files])
            self.assertEqual([item.directory_id for item in serial.files], [item.directory_id for item in draft.files])
            self.assertEqual([item.id for item in serial.symbols], [item.id for item in draft.symbols])
            self.assertEqual([item.id for item in serial.chunks], [item.id for item in draft.chunks])
            self.assertEqual([item.edge_id for item in serial.edges], [item.edge_id for item in draft.edges])


if __name__ == "__main__":
    unittest.main()
