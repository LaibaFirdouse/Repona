# Progressive indexing and query router — implementation notes

## Baseline (pre-change, from forensic audit)

| Metric | Large Repona re-index |
|--------|------------------------|
| `ingest_commit_total` | ~405 s |
| `write_draft_postgres` | ~121 s |
| Summed `qdrant_embed_batch` | ~235 s |
| Ask (stub LLM) | ~14.4 s |

See `evaluation/REPONA-PERFORMANCE-FORENSIC-AUDIT.md` and `evaluation/benchmark-large-index-latest.json`.

## Architecture summary

1. **Progressive indexing** (`PROGRESSIVE_INDEXING=true`, default): eager parse writes symbols, graph edges, and catalogue chunks (`file_overview`, `non_code`, `import_block`). Symbol bodies are held in `GenerationDraft.deferred_chunks`, postgres-linked after **early activate** commit, then embedded with reuse.
2. **Lazy retrieval**: fused symbol anchors without `symbol_body` rows are materialised from the snapshot via `chunk_materialize.materialize_symbol_chunks`.
3. **Embedding reuse**: `embedding_reuse.embed_chunks_with_reuse` checks Qdrant point id + payload (`embedding_input_hash`, `span_hash`, fingerprint) before calling the embedder; reuses stored vectors when valid.
4. **PostgreSQL**: `generation_chunks` inserts use batched `executemany` SQL with `to_tsvector('simple', :lexical)` (see `persist._bulk_insert_generation_chunks`).
5. **Query router**: deterministic `route_question()` selects lanes and lexical variants; optional LLM planner (`QUERY_PLANNER_ENABLED`) returns validated `QueryPlanV1` JSON with single retry via `parse_planner_json`.
6. **Lexical fallback**: `_text_hits` tries complementary queries until `LEXICAL_MIN_HITS` (default 2) or exhausts variants.

## Configuration

| Env | Default | Purpose |
|-----|---------|---------|
| `PROGRESSIVE_INDEXING` | `true` | Defer symbol chunk postgres + embed until after activate |
| `QUERY_PLANNER_ENABLED` | `false` | LLM structured plan |
| `QUERY_PLANNER_MIN_COMPLEXITY` | `14` | Word + identifier threshold for planner |
| `LEXICAL_MIN_HITS` | `2` | Lexical lane fallback threshold |

## Controlled comparison (required for cold vs warm claims)

The 74 s large-index run had **embed_cache_misses=0** — it measures warm Qdrant reuse, not cold embedding cost.

Use:

```bash
npm run benchmark:controlled:cold   # deletes repo Qdrant points, then force re-index
npm run benchmark:controlled:warm   # re-index without clearing vectors
npm run benchmark:controlled:lazy   # PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST=false; ask twice
```

Each mode records `git_head`, dirty working tree, env flags, and `ingest.time_to_queryable_ms`.

## Post-change measurements (warm re-index, 2026-10-20)

Large Repona @ same commit, **warm** Qdrant:

```bash
# Baseline comparison: set PROGRESSIVE_INDEXING=false and re-index once
PROGRESSIVE_INDEXING=false npm run benchmark:large-index
PROGRESSIVE_INDEXING=true npm run benchmark:large-index
npm run benchmark:detailed
```

Expect: lower time-to-`early_activate_commit`, fewer embed calls on re-index (cache hits in trace), reduced `write_draft_postgres` row count on first pass.

## Router → LLM planner guidance

| Query class | Default lanes | Invoke planner when enabled |
|-------------|---------------|-----------------------------|
| Path / file | symbol, lexical | No |
| Exact identifier | symbol, lexical | No |
| Execution trace | symbol, graph, lexical | Yes if complexity ≥ min |
| Debugging | symbol, lexical, vector | Rarely |
| Conceptual | symbol, lexical, vector | Only long / multi-part questions |
| Architecture | all lanes | Sometimes |

## Risks

- Early commit during progressive ingest leaves a **ready** generation if deferred embedding fails; vector coverage may be incomplete until background batch finishes or lazy materialisation runs on ask.
- Bulk `generation_chunks` insert uses `ON CONFLICT DO NOTHING`; duplicate append attempts are safe but hide conflicts.
- LLM planner remains off by default; malformed JSON falls back to deterministic routing.
