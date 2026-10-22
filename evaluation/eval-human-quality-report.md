# Human-style index → ask evaluation

**Generated:** 2026-10-22 (local run; updated after `79df011f`)  
**Code:** `79df011f` on master (PR #17 + HTTP intent, catalog path stopterms, test-span filtering)  
**Index mode (primary):** lazy cold — `PROGRESSIVE_INDEXING=true`, `EMBED_CATALOGUE_BEFORE_ACTIVATE=false`  
**LLM:** `cursor` via bridge — use live bridge for prose; stub LLM used for sourcing/latency baselines.

## Infrastructure

| Component | Status |
|-----------|--------|
| Postgres (Neon) | OK |
| Qdrant Cloud | OK |
| Neo4j Cloud | OK |
| Cursor bridge `:8765` | Host process — start per README; raise `CURSOR_TIMEOUT` if retrieval + agent exceeds default |

## Harness

```bash
export PYTHONPATH=backend:scripts
# load .env
python scripts/eval_human_quality.py          # index + cold/warm asks + report
python scripts/eval_retrieval_only_rerun.py   # asks only on indexed repos (--tiers small,medium)
```

Artifacts (local, gitignored by default unless committed):

- `evaluation/eval-human-quality-latest.json` — full run output
- `evaluation/eval-human-stub-llm-latest.json` — stub-claim sourcing snapshot
- `evaluation/eval-human-retrieval-rerun.json` — retrieval-only rerun

## Retrieval fixes on master (not hardcoded repo paths)

- **Intent:** HTTP / request-handling questions → `execution_trace` (regex heuristics).
- **Catalog paths:** Stopword / min-length filter on path ilike terms (e.g. `make` → Makefile).
- **Evidence pack:** Omit `/tests/` and `test_*` file spans unless the question is about tests.
- **Substantive implementation:** “make sure / ensure / pick which code runs” triggers implementation mode.

## Stub-LLM sourcing (post-fix, warm index)

| Repo | Question | Sourcing notes |
|------|----------|----------------|
| itsdangerous | Tamper-evident signing | `timed.py`, `serializer.py`, `encoding.py`; **no test paths in pack** |
| flask | HTTP → which code runs | **`app.py` first**, `ctx.py`; intent `execution_trace`, `prefer_implementation` true |
| flask | Errors to caller | `app.py` |

## Still open (quality, not retrieval core)

- Occasional non-impl noise (`.github/` templates, docs Makefiles) in pack.
- Flask Q1 may still include `templating.py` after `app.py`.
- Large-tier Repona lazy index + full human eval sweep.
- End-to-end LLM quality scoring when bridge timeout allows.
