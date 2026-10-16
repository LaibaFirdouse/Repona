# Qdrant Cloud for Repona

Repona talks to Qdrant over its **HTTP REST API** (`vector_store.py`). Qdrant Cloud requires **`QDRANT_API_KEY`** on every request (`api-key` header).

## From the Qdrant Cloud console

1. Open your cluster (**Repona** or similar) → **Healthy**.
2. Open **Cluster UI** or **Connect** / **Details** and copy:
   - **Cluster URL** (HTTPS, usually port **6333**), e.g.  
     `https://xxxxxxxx.australia-southeast1-0.gcp.cloud.qdrant.io:6333`
   - **API key** (create under **Access** / **API keys** if you do not have one).
3. Add to **`.env`** (never commit):

```env
QDRANT_URL=https://YOUR-CLUSTER.australia-southeast1-0.gcp.cloud.qdrant.io:6333
QDRANT_API_KEY=your-api-key
```

Collections are created automatically on first **Index** (`ensure_collection`).

## Verify

```bash
npm run verify:qdrant
```

Expect `qdrant_ok collections=N` (N may be 0 before any index).

## Local Docker

For `docker compose` with the bundled Qdrant container, use:

```env
QDRANT_URL=http://127.0.0.1:6333
QDRANT_API_KEY=
```

Leave the API key empty for local Qdrant without auth.
