# Neon Postgres for Repona

Repona uses **SQLAlchemy 2** with the **`psycopg` (v3)** driver. Neon CLI writes a standard `postgresql://…` URL; the app normalizes it to `postgresql+psycopg://…` in `app.core.config`.

## CLI workflow

```bash
npx neon@latest init -y --org-id <org> --project-id <project> --branch production --services postgres
npx neon@latest deploy
```

`neon deploy` updates **`.env`** (not committed) with:

| Variable | Use |
|---|---|
| `DATABASE_URL` | **Pooled** connection — use for the API (default in Repona) |
| `DATABASE_URL_UNPOOLED` | Direct connection — migrations, `pg_dump`, admin |
| `NEON_BRANCH` | Active branch name |

Do not commit `.env`. Re-run `neon deploy` or `neon env pull` after branch changes.

## Verify from the repo root

```bash
npm run build
npm run verify:postgres
```

## Local Docker Postgres

When developing with `docker compose` and local Postgres on `:5433`, keep `DATABASE_URL` pointed at `127.0.0.1:5433/repona` in `.env` or use a separate env file for Neon-only runs.
