# Evidence integrity validation

Generated: `2026-10-28T19:11:22.134517+00:00`
Repo: `https://github.com/LaibaFirdouse/Repona`
HEAD: `30180e5681836adf5955aa90f26e9fa8475438a8` (dirty=True)

## Commands

```bash
docker compose up -d postgres neo4j qdrant
```

```bash
DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5433/repona QDRANT_URL=http://127.0.0.1:6333 NEO4J_URI=bolt://127.0.0.1:7687 PROGRESSIVE_INDEXING=true EMBED_CATALOGUE_BEFORE_ACTIVATE=false PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST=false LAZY_EMBED_ON_QUERY=true PYTHONPATH=backend:scripts .venv/bin/python scripts/validate_evidence_integrity.py --force-index
```

## Suite: existing_or_initial_generation

- generation_id: `5deb5528-d838-51bf-8553-1ca0497df130`
- commit: `30180e5681836adf5955aa90f26e9fa8475438a8`
- files/chunks/vectors: 214/1663/290
- summary: **7/7** passed

| Case | Pass | Failed checks | Intent |
|------|------|---------------|--------|
| `exact_method` | PASS | — | conceptual_search |
| `large_multipart` | PASS | — | execution_trace |
| `readme_name_collision` | PASS | — | execution_trace |
| `imports_false_positive` | PASS | — | execution_trace |
| `cross_file_trace` | PASS | — | execution_trace |
| `explicit_path_symbol` | PASS | — | execution_trace |
| `unavailable_evidence` | PASS | — | exact_lookup |

- `_answer_events` inventory: 5 chunk(s) kinds=['symbol_part', 'symbol_part', 'symbol_part', 'symbol_part', 'symbol_part']

## Suite: fresh_lazy_generation

- generation_id: `620b1e4f-07b3-547e-90b5-32c9fdbed277`
- commit: `30180e5681836adf5955aa90f26e9fa8475438a8`
- files/chunks/vectors: 214/1524/0
- summary: **7/7** passed

| Case | Pass | Failed checks | Intent |
|------|------|---------------|--------|
| `exact_method` | PASS | — | conceptual_search |
| `large_multipart` | PASS | — | execution_trace |
| `readme_name_collision` | PASS | — | execution_trace |
| `imports_false_positive` | PASS | — | execution_trace |
| `cross_file_trace` | PASS | — | execution_trace |
| `explicit_path_symbol` | PASS | — | execution_trace |
| `unavailable_evidence` | PASS | — | exact_lookup |

- `_answer_events` inventory: 0 chunk(s) kinds=[]

## Recommendation

Evidence-integrity suite passed on the live Repona index for both generations tested. Suitable for a release-candidate tag after normal CI (unit suites already green) and a human spot-check of answer prose.
