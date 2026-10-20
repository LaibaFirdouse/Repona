# Public hosting profile

One shared deployment: **your Cursor API key** on the server, **no visitor login**, **no per-user keys**, and **per-IP rate limits** on index and ask. Visitors use the default demo page as-is.

This profile runs the **Cursor CLI inside the API process** (or container). It does **not** use the host bridge or a second deployment.

## Architecture

```text
Internet → Repona API :8000 (Docker on EC2)
              → `agent` CLI with CURSOR_API_KEY (ask + sandbox)
         → Neon (Postgres) + Neo4j Aura Free + Qdrant Cloud or Qdrant on the same VM
```

There is **no** `CURSOR_BRIDGE_URL` and **no** separate bridge systemd unit in this profile.

## Model and credentials

| Setting | Hosted value |
|---|---|
| `LLM_PROVIDER` | `cursor` |
| `APP_ENV` | `production` |
| `CURSOR_API_KEY` | User API key from [Cursor Dashboard → API Keys](https://cursor.com/dashboard) (`crsr_…`); **never** commit it |
| `CURSOR_MODEL` | e.g. `composer-2.5-fast` |
| `CURSOR_AGENT_BIN` | `agent` on `PATH`, or full path to the binary |
| `CURSOR_SANDBOX` | **`disabled`** in `docker-compose.hosted.yml` — Linux containers cannot start the Cursor sandbox (AppArmor); allowlist + `--trust` is used instead |
| `CURSOR_BRIDGE_URL` | **Unset or empty** |
| `CURSOR_BRIDGE_TOKEN` | **Unset** |

In `APP_ENV=production`, the API requires **`CURSOR_API_KEY`** whenever the bridge URL is unset. Local development may still use `agent login` or the host bridge without an API key.

The hosted image installs the [Cursor CLI](https://cursor.com/docs/cli/reference/authentication) at build time. The key is passed through the process environment to `agent`; visitors never see it.

All model usage is billed to **your** Cursor account. Visitors get an anonymous **client id** (cookie + browser storage), not login. Repos and chat are scoped to that id and removed after **`CLIENT_SESSION_TTL_HOURS`** (default **48**) without activity. IP rate limits still apply.

## Rate limiting

| Variable | Purpose | Suggested start |
|---|---|---|
| `RATE_LIMIT_ENABLED` | Turn limits on | `true` |
| `RATE_LIMIT_TRUST_PROXY` | Use `X-Forwarded-For` / `X-Real-IP` | `false` with direct :8000; `true` only behind a reverse proxy |
| `RATE_LIMIT_ASK_PER_MINUTE` | `POST /api/v1/ask` per IP | `12` |
| `RATE_LIMIT_INDEX_PER_HOUR` | `POST /api/v1/repository` per IP (includes index polling) | `360` |

Over-limit requests return **HTTP 429** with `Retry-After`. Counters are in-memory on each API instance.

## Server logs

The API keeps **in-memory** logs for about **24 hours** (no database or file persistence). Each instance only sees its own process.

| Where | How |
|---|---|
| Container / terminal stdout | `docker compose logs -f api` (local) or `docker compose -f docker-compose.hosted.yml logs -f repona-api` (EC2) |
| HTTP JSON | `GET /api/v1/logs?limit=200&level=INFO` — same host as the API, e.g. `http://127.0.0.1:8000/api/v1/logs` |
| OpenAPI | `/docs` → **logs** → `GET /api/v1/logs` |

Optional env:

| Variable | Default | Purpose |
|---|---|---|
| `SERVER_LOGS_ENABLED` | `true` | Turn logging and `/api/v1/logs` off when `false` |
| `SERVER_LOG_RETENTION_HOURS` | `24` | Drop buffered entries older than this |
| `SERVER_LOG_BUFFER_MAX` | `5000` | Cap in-memory entries per instance |
| `SERVER_LOG_LEVEL` | `INFO` | Minimum level for stdout and the buffer |
| `SERVER_LOGS_TOKEN` | empty | When set, require `Authorization: Bearer …` or header `X-Server-Logs-Token` on `/api/v1/logs` |

Request lines use logger `app.request` (`method`, `path`, `status`, `duration_ms`, client IP). Health checks are not logged.

## Data stores (free-tier friendly)

| Store | Suggested host | Env |
|---|---|---|
| PostgreSQL | Neon (see [`docs/NEON.md`](NEON.md)) or Supabase | Pooled `DATABASE_URL` from `neon deploy` |
| Neo4j | AuraDB Free | `NEO4J_URI=bolt+s://…`, `NEO4J_USER`, `NEO4J_PASSWORD` |
| Qdrant | Qdrant Cloud free cluster **or** Qdrant container on the VM | `QDRANT_URL`, `QDRANT_API_KEY` (Cloud) |
| Git + snapshots | EBS volume on the VM | Docker volume at `/app/data` |

## AWS compute (one EC2, one app image)

The repo ships a **single production image** that contains both the API and the Cursor **`agent`** CLI:

- **`Dockerfile.hosted`** — builds API + embeddings + `agent` on `PATH` for user `repona`
- **`docker-compose.hosted.yml`** — runs that image on **port 8000**
- **`.env.hosted.example`** — template for secrets on the VM

### EC2 checklist

1. **Instance:** **t3.small** or larger (2 GiB+ RAM for indexing).
2. **Security group:** inbound **8000** (and **22** from your IP). Nothing else public unless you add a proxy later.
3. **Software on the VM:** Docker Engine + Compose plugin only (no host `agent` install).
4. **Data:** Docker volume **`repona_data`** → `/app/data` (git + snapshots) on the instance EBS.

### Deploy on the instance

```bash
git clone https://github.com/LaibaFirdouse/Repona.git
cd Repona
cp .env.hosted.example .env
# Edit .env: CURSOR_API_KEY, DATABASE_URL, QDRANT_URL, QDRANT_API_KEY, NEO4J_*

docker compose -f docker-compose.hosted.yml up -d --build
docker compose -f docker-compose.hosted.yml ps
curl -sS http://127.0.0.1:8000/api/v1/health
```

The container publishes **8000** on the instance. **`agent`** runs inside the same container when a question is answered. Add **Caddy**, **nginx**, or **Cloudflare Tunnel** later if you want HTTPS on **443** without exposing uvicorn quirks directly.

### Image contents

| Layer | Purpose |
|---|---|
| Python 3.12 + app code + frontend | FastAPI, retrieval, indexing |
| `sentence-transformers` model | Embeddings at index time |
| Cursor CLI (`curl cursor.com/install`) | Ask-time generation via `CURSOR_API_KEY` |

Local dev still uses **`docker-compose.yml`** (host bridge). Hosted AWS uses **`docker-compose.hosted.yml`** only.

## Edge checklist

- HTTPS later: add a reverse proxy or Cloudflare Tunnel; then set `RATE_LIMIT_TRUST_PROXY=true`.
- AWS billing alarm.
- Rotate `CURSOR_API_KEY` from the dashboard if leaked.

## What this profile does not include

- User accounts or visitor-supplied API keys.
- The Cursor **Cloud Agents** REST API (`api.cursor.com/v1/agents`) — that API targets long-running cloud agents, not the evidence-packet ask flow Repona uses today.
- Strong protection against distributed abuse; IP limits are **cost control**, not auth.

## Reboot order

1. Managed databases (Neon, Aura, Qdrant Cloud).
2. On EC2: `docker compose -f docker-compose.hosted.yml up -d`.

## Ask latency (large repos)

| Variable | Default | Effect |
|---|---|---|
| `RETRIEVAL_LIGHT_SNAPSHOT_VALIDATE` | `true` | Checks snapshot `HEAD` only instead of re-reading every blob on each ask. |
| `RETRIEVAL_VECTOR_MAX_FILE_FILTER` | `256` | Drops the huge Qdrant `file_id` list for big generations; membership is verified in Postgres. |
| `EMBED_WARMUP_ON_STARTUP` | `true` | Loads the embedding model when the API starts so the first question is not cold. |

Co-locate Neon, Qdrant, and Neo4j in the same region as EC2 when you can. Run `npm run benchmark` before and after tuning.

## Reliable indexing on the VM

| Topic | Guidance |
|---|---|
| Visitor repos | Any **public** GitHub URL. Each browser session has its own client id, list, and chat. Large monorepos need more RAM (**t4g.medium+**) and longer index waits (the page polls up to **30 minutes**). |
| Dev benchmarks | `npm run benchmark` defaults to this monorepo URL for latency work — not enforced in the UI. |
| Shared Postgres | If you indexed on a laptop then moved to EC2, run **`npm run repair:catalog`** on the VM (or `scripts/repair_catalog.py --all --reset-expired-refresh`) so `git_dir` and snapshot paths point at `/app/data`. |
| Interrupted index | Wait up to **10 minutes** for the refresh lease to expire, or run repair with **`--reset-expired-refresh`**. The demo page **polls Index** while `action` is `wait`. |
| Stuck “newer commit is being indexed” | Usually means no **ready** generation for the current commit, or a refresh still holds the lease. Index again after repair; do not Ask until Index returns **`serve`**. |

After pulling a new API image:

```bash
git pull origin master
docker compose -f docker-compose.hosted.yml up -d --build
docker exec repona-api env PYTHONPATH=/app/backend python /app/scripts/repair_catalog.py --all --reset-expired-refresh
```

### Progressive lazy indexing (production)

| Step | Who | Action |
|---|---|---|
| Merge + ship code | GitHub | PR to `master`; CI green |
| Redeploy API | EC2 | Commands above (`--build` picks up new code) |
| Re-index | Operator / visitor | **Index** each repo again so the active generation is built with the new ingest + lazy flags. Old generations (e.g. pre-lazy commit) do not gain query-time materialisation retroactively. |
| Lazy env | `.env` on VM | Hosted compose defaults: `EMBED_CATALOGUE_BEFORE_ACTIVATE=false`, `PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST=false`, `LAZY_EMBED_ON_QUERY=true`. Override in `.env` if you need catalogue embed before activate. |
| Acceptance | Dev machine or VM | `npm run benchmark:lazy-validation` (optional smoke after re-index) |

Embedding provider outages are handled in ask (vector lane degrades); regression coverage is the `embedding_unavailable` lazy-validation case, not every question type.
