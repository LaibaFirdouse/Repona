# Evidence integrity validation

Generated: `2026-10-29T07:42:10.962519+00:00`
Repo: `https://github.com/LaibaFirdouse/Repona`
HEAD: `0f3ad6698916dbcc23240dd5a55b7ad0ed8446db` (dirty=True)

## Commands

```bash
docker compose up -d postgres neo4j qdrant
```

```bash
DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5433/repona QDRANT_URL=http://127.0.0.1:6333 NEO4J_URI=bolt://127.0.0.1:7687 PROGRESSIVE_INDEXING=true EMBED_CATALOGUE_BEFORE_ACTIVATE=false PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST=false LAZY_EMBED_ON_QUERY=true PYTHONPATH=backend:scripts .venv/bin/python scripts/validate_evidence_integrity.py --force-index
```

## Suite: existing_or_initial_generation

- generation_id: `3fbc79ed-63e7-53e2-acbc-86288c28bff1`
- commit: `0f3ad6698916dbcc23240dd5a55b7ad0ed8446db`
- files/chunks/vectors: 221/1751/1751
- summary: **7/7** passed

| Case | Pass | Failed checks | Not evaluated | Intent |
|------|------|---------------|---------------|--------|
| `exact_method` | PASS* | — | citation_ids_in_final_prompt, citation_path_line_mapping | conceptual_search |
| `large_multipart` | PASS | — | — | execution_trace |
| `readme_name_collision` | PASS | — | — | execution_trace |
| `imports_false_positive` | PASS | — | — | execution_trace |
| `cross_file_trace` | PASS | — | — | execution_trace |
| `explicit_path_symbol` | PASS | — | — | execution_trace |
| `unavailable_evidence` | PASS | — | — | exact_lookup |

\* PASS with unevaluated claim/citation checks (no kept claims).

- `_answer_events` inventory: 0 chunk(s) kinds=[]

## Suite: fresh_lazy_generation

- generation_id: `50cd492c-caae-553d-8fd7-e4c952e7f4f6`
- commit: `0f3ad6698916dbcc23240dd5a55b7ad0ed8446db`
- files/chunks/vectors: 221/3241/3241
- summary: **7/7** passed

| Case | Pass | Failed checks | Not evaluated | Intent |
|------|------|---------------|---------------|--------|
| `exact_method` | PASS* | — | citation_ids_in_final_prompt, citation_path_line_mapping | conceptual_search |
| `large_multipart` | PASS | — | — | execution_trace |
| `readme_name_collision` | PASS | — | — | execution_trace |
| `imports_false_positive` | PASS* | — | citation_ids_in_final_prompt, citation_path_line_mapping | execution_trace |
| `cross_file_trace` | PASS* | — | citation_ids_in_final_prompt, citation_path_line_mapping | execution_trace |
| `explicit_path_symbol` | PASS* | — | citation_ids_in_final_prompt, citation_path_line_mapping | execution_trace |
| `unavailable_evidence` | PASS | — | — | exact_lookup |

\* PASS with unevaluated claim/citation checks (no kept claims).

- `_answer_events` inventory: 6 chunk(s) kinds=['symbol_part', 'symbol_part', 'symbol_part', 'symbol_part', 'symbol_part', 'symbol_part']

## Layers

- Evidence-packet all passed: `True`
- Claim/citation all passed (evaluated only): `True`
- Real-model prose validated: `False` (Deterministic mock harness only. Real-model prose quality was not validated.)

## Recommendation

Evidence-packet integrity passed on the live Repona index for both generations tested. Claim/citation mapping was evaluated only when the deterministic harness produced kept claims. Real-model prose quality remains an outstanding human/LLM spot-check before a stable release tag.
