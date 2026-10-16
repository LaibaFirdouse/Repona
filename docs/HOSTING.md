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
| `CURSOR_BRIDGE_URL` | **Unset or empty** |
| `CURSOR_BRIDGE_TOKEN` | **Unset** |

In `APP_ENV=production`, the API requires **`CURSOR_API_KEY`** whenever the bridge URL is unset. Local development may still use `agent login` or the host bridge without an API key.

The hosted image installs the [Cursor CLI](https://cursor.com/docs/cli/reference/authentication) at build time. The key is passed through the process environment to `agent`; visitors never see it.

All model usage is billed to **your** Cursor account. The fixed `tester` user id is only for repository membership in Postgres, not authentication.

## Rate limiting

| Variable | Purpose | Suggested start |
|---|---|---|
| `RATE_LIMIT_ENABLED` | Turn limits on | `true` |
| `RATE_LIMIT_TRUST_PROXY` | Use `X-Forwarded-For` / `X-Real-IP` | `false` with direct :8000; `true` only behind a reverse proxy |
| `RATE_LIMIT_ASK_PER_MINUTE` | `POST /api/v1/ask` per IP | `12` |
| `RATE_LIMIT_INDEX_PER_HOUR` | `POST /api/v1/repository` per IP | `4` |

Over-limit requests return **HTTP 429** with `Retry-After`. Counters are in-memory on each API instance.

## Data stores (free-tier friendly)

| Store | Suggested host | Env |
|---|---|---|
| PostgreSQL | Neon or Supabase | `DATABASE_URL` with `sslmode=require`, database `repona` |
| Neo4j | AuraDB Free | `NEO4J_URI=bolt+s://…`, `NEO4J_USER`, `NEO4J_PASSWORD` |
| Qdrant | Qdrant Cloud free cluster **or** Qdrant container on the VM | `QDRANT_URL` |
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
# Edit .env: CURSOR_API_KEY, DATABASE_URL, QDRANT_URL, NEO4J_*

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
