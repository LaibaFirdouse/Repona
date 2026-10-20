# Controlled benchmark summary (2026-10-20, v2)

**Provenance:** `git_head=bb18f6f7…`, dirty working tree (~40 files). Each mode clears Qdrant repo points first (cold vector store).

## Three cold-start modes (same commit, subprocess-isolated env)

| Mode | Env flags | Time to queryable | Full ingest | Catalogue embed | Qdrant assert |
|------|-----------|-------------------|-------------|-----------------|---------------|
| **lazy_cold** | progressive on, **no** pre-activate embed, no deferred ingest embed | **~20.0 s** | **~20.6 s** | skipped | skipped (0 ready vectors) |
| **progressive_cold** | progressive on, embed catalogue before activate, complete deferred in ingest | **~56.9 s** | **~96.8 s** | ~31 s (model in aggregate) | ~11.7 s |
| **full_cold** | progressive **off**, embed all chunks before activate | **~78.5 s** | **~79.1 s** | ~39 s | ~11.5 s |

Artifacts: `evaluation/benchmark-controlled-{lazy_cold,progressive_cold,full_cold}-*.json` and `*-run.log`.

## What changed in implementation

1. **`vector_status`** on `generation_chunks` (`pending` | `ready`) — Postgres row ≠ Qdrant vector.
2. **`EMBED_CATALOGUE_BEFORE_ACTIVATE=false`** — activate after catalogue + graph without dense vectors.
3. **Query-time embed** — `lazy_vectors.ensure_chunk_vectors` / materialize + embed on ask; content-addressed reuse unchanged.
4. **Router** — `expand_vector_lane` when lexical/name evidence is weak and vectors exist or lazy embed is enabled.
5. **Ingest** — proportional Qdrant assert (only `vector_status=ready`); skip upsert batches with 0 points; separate catalog vs deferred embed stats (no double-count in aggregates).
6. **Neo4j** — edge/node UNWIND batch size 400 → 800.

## Interpretation

- **Lazy cold queryability (~20 s)** is the first measurement that matches the strategic goal: structural + lexical index without mandatory catalogue embedding.
- **Progressive cold (~57 s queryable)** still pays **~31 s** to embed the catalogue before activation — next step is to default that off for Level-1 activation.
- **Full cold (~79 s)** embeds all chunks in one pass (non-progressive); faster wall clock than the earlier **326 s** cold run partly because ingest no longer double-passes deferred batches and timers are scoped correctly — still not apples-to-apples with the old 405 s “warm” baseline.

## Acceptance criteria (before declaring lazy/progressive complete)

| Criterion | Target / check |
|-----------|----------------|
| Availability | Cold lazy index queryable in ~20 s (this environment) |
| Correctness | Questions on unembedded regions retrieve cited source (`npm run benchmark:lazy-validation`) |
| Lazy embedding | First query may add vectors; repeat query reuses (`vectors_added` → 0 on repeat) |
| Fallback | Ask succeeds with embed provider stubbed out (`run_embed_unavailable` cases) |
| Router quality | Deterministic vs semantic vs intelligent modes (`npm run benchmark:router-correctness`) |
| Reproducibility | Controlled modes clear Qdrant; record `git_head`, dirty flag, separate cold/warm |

## Validation run (2026-10-20 v2, after Symbol path + scoring fixes)

**Artifacts:** `evaluation/benchmark-lazy-validation-latest.json`, `evaluation/benchmark-router-correctness-latest.json`

**Cold vector reset:** clears Qdrant repo points and sets all `generation_chunks.vector_status=pending` before cases (999 chunks on last run).

| Case | First pass | Notes |
|------|------------|--------|
| exact_symbol | yes | symbol+lexical; cold lazy embed on repeat |
| conceptual | yes | **full** path recall (`answer.py` + `packet.py`); lazy embed 1 point |
| cross_file_trace | yes | pipeline + graph_store |
| deferred_symbol_body | yes | materialised `_write_edges` body |
| outside_catalogue_file | yes | `scripts/benchmark.py` |
| semantic_deferred_lazy | **no** | vector lane runs but no on-demand embed evidence yet (strict case) |
| embedding_unavailable | yes | no internal embed error; **provider-outage** sub-run passes with vector lane + fallback |

**Unit tests:** `backend/tests/test_lazy_vectors.py` (Symbol→File path, ensure_chunk_vectors, cache mismatch re-embed).

## Validation run (2026-10-20, local, index at `bb18f6f7…` + dirty tree)

**Lazy retrieval (`benchmark-lazy-validation-latest.json`):** all 6 cases **passed** (exit 0).

| Case | First ask (ms) | Repeat (ms) | Notes |
|------|----------------|-------------|--------|
| exact_symbol | ~3.3k | ~3.3k | lanes symbol+lexical |
| conceptual | ~2.3k | ~2.3k | AnswerService / pack evidence |
| cross_file_trace | ~11.8k | ~11.5k | pipeline + graph_store |
| deferred_symbol_body | ~2.0k | ~2.0k | graph_store `_write_edges` |
| outside_catalogue_file | ~2.2k | ~2.2k | `scripts/benchmark.py` (in HEAD index) |
| embedding_unavailable | ~6.6k | — | embed stubbed; vector_store path + fallback |

**Lazy cold re-run:** time to queryable **~26.1 s**, catalogue embed skipped; bundled lazy asks **ok** after benchmark client-id / membership fix.

**Router correctness:** mean path recall@k **0.5** per mode on 4-case suite; conceptual + path_lookup miss without vector lane (expected on pre-lazy-index commit). Re-run after indexing commit that includes new scripts.

Fixes applied during run: benchmark membership (`ensure_benchmark_repository_access`), `_indexed_file_spans` `raw_bytes` int, lazy upsert `GenerationDraft` files, evidence fallback trace.

## Next

- **Commit + push** and re-index at new SHA so router cases can target `benchmark_controlled.py` / `lazy_vectors.py`.
- Track B: Neo4j / Qdrant assert profiling with **exclusive** wall timers.
