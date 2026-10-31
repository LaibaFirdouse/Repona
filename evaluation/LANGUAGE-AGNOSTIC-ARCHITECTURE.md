# Repona language-agnostic architecture (Phase A + Phase B start)

**Status:** Incremental foundation landed. Universal support is **not** claimed.
**Commit policy:** No commit/tag/push in this task.

## Architecture changes

| Area | Change |
| --- | --- |
| IR contracts | `backend/app/ingestion/ir.py` — entities, relationship kinds, `ResolutionMethod`, `CapabilityLevel` 0–4, `RepositoryCapabilityReport` |
| Language adapters | `backend/app/ingestion/adapters/language.py` + `builtin.py` — registry; Tree-sitter Py/JS/TS at LEVEL 3; Go/Rust/Java/C# language-pack at LEVEL 2; source-only LEVEL 0 |
| Framework adapters | `backend/app/ingestion/adapters/framework.py` + builtin FastAPI, Next.js, Express, Spring Boot, Go net/http |
| Project context | `backend/app/ingestion/project_context.py` — manifests, path aliases (tsconfig rooted at manifest dir), deps, framework hints |
| Capability report | `backend/app/ingestion/capabilities.py` — built during assemble, attached to `GenerationDraft`, persisted on `IndexGeneration.capability_report_json`, and exposed to execution-trace planning through `CapabilityProfile` |
| Protocol links | `backend/app/ingestion/protocol_links.py` — HTTP client↔server correspondence as `protocol`, never as CALLS |
| Graph store | Accepts `ROUTES_TO` / `HANDLES` / `READS_FROM` / `WRITES_TO`; assemble emits verified HTTP protocol `ROUTES_TO` links, but not a complete framework-handler or ORM edge set |
| Graceful parse | Grammar download/cache failures → `not_parsed` source retention (no generation abort) |
| Assemble | Uses project-context aliases; produces canonical `IrDocument` parse products; resolves CALLS/IMPORTS from IR relationships; builds capability report and verified HTTP protocol links |

Investigation (`execution_trace.py`) remains catalogue-grounded and application-agnostic. The question and evidence select candidate stages; the indexed generation's capability profile then gates stage applicability and investigation strategies.

## Gap matrix (audit)

| Capability | Before | After Phase A/B-start | Remaining gap |
| --- | --- | --- | --- |
| Common IR types | Implicit assemble records only | Explicit IR + capability enums | Full IrEntity persistence path |
| Language adapters | Hardwired parse_file / grammars | Registry + levels | Per-language import resolvers beyond Py/JS/TS |
| Framework adapters | Retrieval-only route helpers | Detect + discover_routes registry | Persistence/DI/event adapters; graph ROUTES_TO writers |
| Config discovery | tsconfig-only in assemble | Multi-manifest project_context | Cargo/Go module path resolution; workspace packages |
| CALLS | Py/JS/TS conservative | Unchanged core; adapters can delegate | SCIP/LSP optional resolution |
| Cross-language | None | HTTP protocol links | OpenAPI/GraphQL/protobuf/Kafka matchers |
| Capability matrix | Technologies list only | Generation-scoped report persisted on IndexGeneration and consumed through CapabilityProfile | Broader adapter/facet coverage; capability-based RRF lane re-scoring |
| Execution tracer | Catalogue-grounded and question-led | Capability-aware planning from the active generation's profile; no app-specific path injection | Capability-based RRF lane re-scoring |
| Unsupported langs | Could fail generation on grammar lock | Source retained | Richer lexical symbol heuristics |

## Language support matrix

Levels: 0 source · 1 syntax · 2 structural/import · 3 semantic CALLS · 4 framework tracing (framework row).

| Language | Adapter | Level | Parser | Import resolve | CALLS | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Python | `python_treesitter` | 3 | yes | yes | conservative | Strongest path |
| JavaScript | `javascript_treesitter` | 3 | yes (+`.mjs`/`.cjs`) | relative + aliases | conservative | |
| TypeScript/TSX | `typescript_treesitter` | 3 | yes | tsconfig aliases | conservative | |
| Go | `go_language_pack` | 2 | language-pack | relative only | extract, limited resolve | |
| Rust | `rust_language_pack` | 2 | language-pack | relative only | limited | |
| Java | `java_language_pack` | 2 | language-pack | relative only | limited | |
| C# | `csharp_language_pack` | 2 | language-pack | relative only | limited | |
| COBOL (fixture) | none | 0 | unavailable / not_parsed | no | no | Still indexed as source |
| Other Linguist langs | via `parse_other` when grammar present | ≤2 | optional download | no dedicated | no dedicated | Fail closed to LEVEL 0 |

## Framework support matrix

| Framework | Detect | Routes | Persistence | Level when detected | Evidence basis |
| --- | --- | --- | --- | --- | --- |
| FastAPI | yes | decorator scan | no | 4 | requirements/pyproject + source |
| Next.js | yes | App Router file layout | no | 4 | package.json `next` |
| Express | yes | `app/router.METHOD` | no | 4 | package.json |
| Spring Boot | yes | `@*Mapping` | no | 4 | pom/gradle + Controllers |
| Go net/http | yes | `HandleFunc` | no | 2 | go.mod + `.go` |
| Django/Flask/Rails/Laravel/Axum/… | not yet | — | — | — | Phase C |
| SQLAlchemy/Prisma/EF | not yet as adapters | — | — | — | Heuristic stage cues only |

## Cross-language capabilities

| Mechanism | Status | Provenance |
| --- | --- | --- |
| HTTP path string ↔ backend route | Implemented (`protocol_links`) | `protocol` — not CALLS |
| OpenAPI / GraphQL / protobuf | Not implemented | — |
| Queue/topic names | Not implemented | — |
| Shared DB schema | Not implemented | — |

Demonstrated on `cross_stack_shipment`: React `fetch("/api/v1/shipments")` ↔ FastAPI `@router.post("/shipments")` under prefix `/api/v1`.

## Exact capabilities vs heuristics

| Claim | Exact / heuristic |
| --- | --- |
| Symbol spans from Tree-sitter | Exact (syntax) |
| IMPORTS/CALLS edge when unique candidate | Conservative static resolution |
| Ambiguous alias targets | Explicit unresolved (not guessed) |
| Framework route discovery | Heuristic / config (decorator & file layout) |
| HTTP cross-stack link | Protocol correspondence only |
| Capability LEVEL | Derived from adapter registration + parse status — not marketing “full support” |

## Unsupported / not claimed

- Full semantic support for Go/Rust/Java/C#/PHP/Ruby/Swift/…
- SCIP or LSP integration (planned optional; `semantic_index: none`)
- ORM WRITE/READ edges as first-class assemble output
- Universal execution tracing for every architecture
- Any StudyPlanner / app-specific production seeds

## Regression results by fixture

| Fixture | Focus | Result |
| --- | --- | --- |
| `execution_flow` | Next-style TS alias + CALLS chain | Green (`test_execution_trace` + latency smoke) |
| `fastapi_orders` | Python FastAPI CALLS | Covered by prior suite; assemble OK |
| `cross_stack_shipment` | TS↔Python HTTP + aliases | Green (`test_language_adapters`) |
| `go_warehouse` | Go index + route discover | Green |
| `spring_catalog` | Java controller routes | Green |
| `unsupported_cobol` | LEVEL 0 graceful | Green (not_parsed, still discoverable) |

Test command: `PYTHONPATH=backend pytest backend/tests/test_language_adapters.py backend/tests/test_execution_trace.py` → **34 passed**.

## Before / after retrieval evidence

| Concern | Before | After |
| --- | --- | --- |
| App-specific path injection | Removed in prior pass | Still absent; shipment questions do not mention StudyPlanner tokens |
| Alias resolution | Root tsconfig only | Manifest-directory-rooted aliases (monorepo `web/tsconfig.json`) |
| Capability visibility | Opaque | Persisted per-generation capability report, normalized into a planner profile for execution-trace asks |
| Cross-stack | Topic-similar retrieval only | Explicit protocol link helper for planners |

## Indexing / query latency impact

Local assemble (workers=1, fixture scale) after this change — wall times are tens of milliseconds on fixtures:

| Fixture | Assemble wall time (approx.) | Notable capability |
| --- | --- | --- |
| execution_flow | ~50 ms | typescript L3 |
| cross_stack_shipment | ~10 ms | python+typescript L3, fastapi detected |
| fastapi_orders | ~10 ms | python L3, fastapi detected |
| go_warehouse | ~3 ms | go L2, go_net_http |
| spring_catalog | ~4 ms | java L2, spring_boot (pom.xml now included) |
| unsupported_cobol | ~1 ms | cobol L0 |

The graph store accepts the additional relationship types; assembly emits verified HTTP protocol `ROUTES_TO` links but does not claim complete `HANDLES` or ORM relationship coverage. Adapters load only when `capabilities` / `adapters.builtin` are imported (assemble path). Query latency remains unchanged for non-execution-trace intents; execution-trace asks load the persisted generation capability profile and use it to gate stages and select strategies.

Manifest inclusion expanded (`pom.xml`, `Cargo.toml`, `pyproject.toml`, …) so configuration discovery is not blind to JVM/Rust/Python manifests.

## Remaining limitations

1. Capability-aware planning depends on the indexed generation's report. Legacy generations without one use a conservative unknown-facet fallback; re-indexing provides richer capability decisions.
2. Verified HTTP client↔server correspondence can produce `ROUTES_TO` edges; broader framework route/handler persistence, including `HANDLES`, remains incomplete.
3. Language-pack CALLS are extracted but lack language-specific resolvers (many stay unresolved — correct).
4. No OpenAPI/protobuf/event-bus linking yet.
5. SCIP/LSP not integrated.
6. PHP/Rails/Laravel/.NET ASP.NET adapters not registered.
7. Execution-trace planning consumes the persisted, normalized capability profile to gate stages and select strategies. Capability-based RRF lane re-scoring remains unimplemented.

## Recommended next adapters (Phase B/C)

1. Expand per-language and framework adapter coverage, capability evidence, and validation.
2. Expand framework route/handler graph emission and provenance beyond the verified HTTP client↔server `ROUTES_TO` links currently emitted.
3. OpenAPI + fetch/client binding for stronger cross-language HTTP.
4. SQLAlchemy / Prisma persistence adapters (`READS_FROM`/`WRITES_TO`).
5. Go module-aware import resolution; then Rust/Java package resolvers.
6. Optional SCIP indexer hook behind capability detection.
7. Express/NestJS fixture with independent names; ASP.NET + Laravel later.

## Acceptance checklist (honest)

1. Unknown repos discoverable without app assumptions — **yes** (catalogue + LEVEL 0 fallback).
2. Supported langs syntax extraction — **yes** for Py/JS/TS; partial for pack langs.
3. Conservative import/call resolution — **yes** (Py/JS/TS).
4. Framework adapters discover entrypoints — **partial** (detect + route scan; not full graph).
5. Cross-language contracts — **HTTP protocol only**.
6. Execution tracing adapts — **partial** (generation capability profile gates stages and strategies; deeper framework-specific tracing remains incomplete).
7. Unresolved stay explicit — **yes**.
8–9. Evidence/citations — **preserved** by not rewriting pack/citation paths.
10. Existing lazy/evidence tests — **execution_trace + adapter suites green**; latest full backend suite: **491 passed, 5 subtests passed** (3 deprecation warnings; rerun after this cleanup).
