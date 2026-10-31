import unittest

from app.ingestion.assemble import SourceBlob, assemble_generation
from app.ingestion.inclusion import is_included
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

    def test_manifests_are_kept_as_text(self) -> None:
        self.assertTrue(is_included("requirements.txt", "100644"))
        self.assertTrue(is_included("go.mod", "100644"))
        self.assertEqual(parse_file("requirements.txt", b"flask\n").parse_status, "not_parsed")
        self.assertEqual(parse_file("go.mod", b"module example.com/demo\n").parse_status, "not_parsed")
        self.assertEqual(parse_file("CMakeLists.txt", b"cmake_minimum_required(VERSION 3.16)\n").parse_status, "not_parsed")

    def test_go_function_and_call_are_read(self) -> None:
        self.assertTrue(is_included("cmd/main.go", "100644"))
        source = "package main\nfunc answer() int { return 1 }\nfunc main() {\n\tanswer()\n}\n"
        parsed = parse_file("cmd/main.go", source.encode())
        self.assertEqual(parsed.parse_status, "parsed")
        functions = {symbol.qualified_name for symbol in parsed.symbols if symbol.kind == "function"}
        self.assertEqual(functions, {"answer", "main"})
        calls = [mention.mentioned_name for mention in parsed.mentions if mention.relationship == "CALLS"]
        self.assertEqual(calls, ["answer"])

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

    def test_go_call_resolves_to_the_function(self) -> None:
        draft = assemble_generation(
            REPO,
            "f" * 40,
            [blob("main.go", "package main\nfunc answer() int { return 1 }\nfunc main() {\n\tanswer()\n}\n")],
            "f" * 64,
            GENERATION,
        )
        call = next(edge for edge in draft.edges if edge.relationship == "CALLS")
        target = next(symbol.id for symbol in draft.symbols if symbol.qualified_name == "answer")
        self.assertEqual(call.mentioned_name, "answer")
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

    def test_js_path_alias_import_resolves_calls(self) -> None:
        draft = assemble_generation(
            REPO,
            "a" * 40,
            [
                blob("lib/helper.ts", "export function helper() {\n  return 1;\n}\n"),
                blob(
                    "app/api/run/route.ts",
                    'import { helper } from "@/lib/helper";\n\nexport async function POST() {\n  return helper();\n}\n',
                ),
            ],
            "f" * 64,
            GENERATION,
        )
        imports = [edge for edge in draft.edges if edge.relationship == "IMPORTS"]
        self.assertEqual(len(imports), 1)
        self.assertEqual(imports[0].resolution, "resolved")
        call = next(edge for edge in draft.edges if edge.relationship == "CALLS" and edge.mentioned_name == "helper")
        target = next(symbol.id for symbol in draft.symbols if symbol.qualified_name == "helper")
        self.assertEqual(call.resolution, "resolved")
        self.assertEqual(call.target_id, target)

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



class IrNormalizationTests(unittest.TestCase):
    def test_python_parse_round_trips_through_ir(self) -> None:
        from app.ingestion.ir import (
            ir_to_parsed_file,
            parsed_file_to_ir,
        )

        path = "app/services/orders.py"
        payload = (
            b"from app.db import save_order\n\n"
            b"def create_order():\n"
            b"    save_order()\n"
        )

        original = parse_file(path, payload)
        document = parsed_file_to_ir(
            path,
            original,
            adapter_id="python_treesitter",
        )

        self.assertEqual(document.relative_path, path)
        self.assertEqual(document.language, "python")
        self.assertTrue(document.entities)
        self.assertTrue(document.relationships)

        self.assertTrue(
            any(item.kind == "function" for item in document.entities)
        )
        self.assertTrue(
            any(item.kind == "CALLS" for item in document.relationships)
        )
        self.assertTrue(
            any(item.kind == "IMPORTS" for item in document.relationships)
        )

        self.assertTrue(
            all(
                item.provenance.adapter_id == "python_treesitter"
                for item in document.relationships
            )
        )

        restored = ir_to_parsed_file(document)
        self.assertEqual(restored, original)

    def test_ir_relationship_sources_reference_entities(self) -> None:
        from app.ingestion.ir import parsed_file_to_ir

        path = "app.py"
        parsed = parse_file(
            path,
            b"def run():\n    helper()\n",
        )
        document = parsed_file_to_ir(path, parsed)

        entity_ids = {entity.id for entity in document.entities}

        for relationship in document.relationships:
            self.assertIn(relationship.source_id, entity_ids)

    def test_syntax_error_round_trips_without_inventing_symbols(self) -> None:
        from app.ingestion.ir import ir_to_parsed_file, parsed_file_to_ir

        path = "broken.py"
        original = parse_file(path, b"def (")

        document = parsed_file_to_ir(path, original)
        restored = ir_to_parsed_file(document)

        self.assertEqual(original.parse_status, "syntax_error")
        self.assertEqual(restored, original)

    def test_unparsed_file_round_trips(self) -> None:
        from app.ingestion.ir import ir_to_parsed_file, parsed_file_to_ir

        path = "package.json"
        original = parse_file(path, b'{"name": "demo"}')

        document = parsed_file_to_ir(path, original)
        restored = ir_to_parsed_file(document)

        self.assertEqual(original.parse_status, "not_parsed")
        self.assertEqual(restored, original)



class AdapterDispatchTests(unittest.TestCase):
    def test_assembly_dispatches_through_registered_language_adapter(self) -> None:
        from unittest.mock import patch

        from app.ingestion.adapters import builtin
        from app.ingestion.adapters.language import language_registry

        builtin.register_builtin_adapters()

        adapter = language_registry.for_path("app.py", "python")
        self.assertIsNotNone(adapter)

        payload = b"def run():\n    helper()\n"

        with patch.object(
            adapter, "parse_ir", wraps=adapter.parse_ir
        ) as parse_spy:
            draft = assemble_generation(
                REPO,
                "9" * 40,
                [blob("app.py", payload.decode())],
                "f" * 64,
                GENERATION,
                workers=1,
            )

        parse_spy.assert_called_once_with("app.py", payload)
        self.assertEqual(draft.files[0].parse_status, "parsed")
        self.assertTrue(draft.symbols)



class AdapterIrContractTests(unittest.TestCase):
    def test_builtin_adapters_emit_ir_for_supported_languages(self) -> None:
        from app.ingestion.adapters import builtin as _builtin
        from app.ingestion.adapters.language import language_registry
        from app.ingestion.ir import IrDocument, ir_to_parsed_file

        _builtin.register_builtin_adapters()

        cases = {
            "demo.py": b"def run():\n    helper()\n",
            "demo.ts": b"export function run() { helper(); }\n",
            "main.go": b"package main\nfunc run() { helper() }\n",
            "Demo.java": b"class Demo { void run() { helper(); } }\n",
            "package.json": b'{"name": "demo"}',
        }

        for path, payload in cases.items():
            with self.subTest(path=path):
                adapter = language_registry.for_path(path, None)
                self.assertIsNotNone(adapter, path)

                document = adapter.parse_ir(path, payload)
                self.assertIsInstance(document, IrDocument)
                self.assertEqual(document.relative_path, path)

                restored = ir_to_parsed_file(document)
                original = adapter.parse(path, payload)

                self.assertEqual(restored, original)

                entity_ids = {entity.id for entity in document.entities}
                for relationship in document.relationships:
                    self.assertIn(relationship.source_id, entity_ids)

    def test_source_only_adapter_is_terminal_fallback(self) -> None:
        from app.ingestion.adapters import builtin as _builtin
        from app.ingestion.adapters.language import language_registry

        _builtin.register_builtin_adapters()

        adapter = language_registry.for_path("notes.unknown", None)

        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.id, "source_only")



class IrCanonicalFileProductTests(unittest.TestCase):
    def test_file_product_contains_ir_document(self) -> None:
        from app.ingestion.assemble import _build_file
        from app.ingestion.ir import IrDocument, ir_to_parsed_file

        payload = b"def run():\n    helper()\n"

        product = _build_file(
            REPO, "app.py", payload, "abc123", "f" * 64
        )

        self.assertIsInstance(product[1], IrDocument)
        self.assertEqual(
            ir_to_parsed_file(product[1]),
            parse_file("app.py", payload),
        )

    def test_legacy_cache_product_is_upgraded_to_ir(self) -> None:
        from app.ingestion.assemble import (
            _build_file,
            _normalize_cached_file_product,
        )
        from app.ingestion.ir import IrDocument, ir_to_parsed_file

        payload = b"def run():\n    helper()\n"
        current = _build_file(
            REPO, "app.py", payload, "abc123", "f" * 64
        )

        legacy_parsed = parse_file("app.py", payload)
        legacy_product = (
            current[0],
            legacy_parsed,
            *current[2:],
        )

        upgraded = _normalize_cached_file_product(
            "app.py", legacy_product
        )

        self.assertIsNotNone(upgraded)
        self.assertIsInstance(upgraded[1], IrDocument)
        self.assertEqual(
            ir_to_parsed_file(upgraded[1]),
            legacy_parsed,
        )

    def test_parallel_parse_cache_stores_ir_documents(self) -> None:
        import pickle
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from app.ingestion.checkpoint import ParseCache
        from app.ingestion.ir import IrDocument

        blobs = [
            blob("pkg/first.py", "def first():\n    return 1\n"),
            blob("pkg/second.py", "def second():\n    return 2\n"),
        ]

        with TemporaryDirectory() as directory:
            cache = ParseCache(Path(directory))

            draft = assemble_generation(
                REPO,
                "8" * 40,
                blobs,
                "f" * 64,
                GENERATION,
                workers=2,
                cache=cache,
            )

            cache_files = [
                path
                for path in Path(directory).rglob("*")
                if path.is_file()
            ]

            self.assertEqual(len(cache_files), 2)
            self.assertEqual(len(draft.files), 2)

            products = [
                pickle.loads(path.read_bytes())
                for path in cache_files
            ]

            self.assertTrue(
                all(
                    isinstance(product[1], IrDocument)
                    for product in products
                )
            )


if __name__ == "__main__":
    unittest.main()
