# Repona

Repona indexes a Git repository and answers questions about the code in plain language. Each answer names the file and the lines it came from.

The page is for a public GitHub URL. The API can also index a private repository if you pass a token that can read its contents.

## Requirements

- Docker
- An API key for the model the container will call. The image does not include the Cursor, Claude, Codex, or Copilot command-line tools.

## Install

Put `CURSOR_BRIDGE_TOKEN` in `.env`. Start the host bridge, then start Compose. The API calls that bridge for answers.

```bash
cp .env.example .env
docker compose up -d --build
```

Compose creates a bridge network and names it `repo-intelligence-network`. The app, Postgres, Qdrant, and Neo4j all join that network. Inside it the app uses `postgres:5432`, `qdrant:6333`, and `bolt://neo4j:7687`. The page is published on host port 8000, which maps to container port 8000. Postgres is also on host port 5433, Qdrant on 6333, and Neo4j bolt on 7687.

Indexed repositories are stored in the `app_data` volume, mounted at `/app/data` in the container. Postgres, Qdrant, and Neo4j use their own named volumes. The first image build downloads the embedding model, so it takes a while. Startup does not download an Ollama model.

```bash
docker compose stop api
docker compose start api
docker compose logs api
docker compose ps
```

`docker compose stop api` leaves the three stores running. `docker compose start api` brings the API container back. Do not run `docker compose down -v`. That deletes the volumes.

To run the API on the host instead, stop the API container first, then start Python on another port if 8000 is still taken:

```bash
docker compose stop api
docker compose up -d postgres qdrant neo4j
python -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

The host process reads `.env`. `DATABASE_URL` must use `localhost` and port 5433. `NEO4J_URI` must use `localhost:7687`. Qdrant is `http://localhost:6333` when `QDRANT_URL` is unset. The host process stores git data and snapshots under `./data`. The container stores them in the `app_data` volume. Those directories are separate. `.env.example` sets `LLM_PROVIDER=cursor` for this host process. Without `.env`, it looks for Postgres on port 5432.

`GET /api/v1/health` returns `ok` when the process is up. It does not check the stores or the model.

## Use the page

Open http://127.0.0.1:8000 and paste a public GitHub URL. Indexing keeps that request open until the repository is ready. A small project takes a few minutes. The next message in the box is a question about that repository. The browser stores the repository id locally, so later questions go to the same one.

## Configuration

`LLM_PROVIDER` chooses the model for a host process. Allowed values are `cursor`, `claude`, `codex`, `copilot`, `opencode`, `zai`, `ollama`, and `openai`. Each one has a model name in `.env` (`CURSOR_MODEL`, `CLAUDE_MODEL`, and so on). `openai` needs `OPENAI_API_KEY`. `zai` needs `ZAI_API_KEY`. A missing key, a rejected key, a rate limit, a network failure, and a timeout all return HTTP 503 with `the model provider is unavailable`. The response does not include the key or the provider body. A failed provider is not replaced by another one.

The API container uses host networking and `LLM_PROVIDER=cursor`. It calls `CURSOR_BRIDGE_URL` (`http://127.0.0.1:8765`), a bridge on the host that holds the existing Cursor login. The bridge accepts the question packet only. The Cursor home is not mounted into the container. Postgres, Qdrant, and Neo4j stay in Docker and are reached through their published host ports.

The API does not call Ollama. A question can still name another provider, and that request is answered by the provider you name.

The bridge accepts `system_message`, `user_message`, `max_output_tokens`, and `model`. Any other field is rejected. A missing or wrong bearer token is rejected. `/health` returns only `{"status":"ok"}`. Compose does not start the bridge, so start it on the host before asking a question. The Cursor CLI in this run is `2026.10.01-e373342`, model `composer-2.5-fast`.

`--mode ask` and `--sandbox enabled` are set, and `--force` and `--yolo` are not. The CLI help still says `--print` can use write and shell tools. One probe with the same flags refused to create a file inside or outside its workspace, and neither file appeared. That does not prove every command or filesystem action is blocked.

A live explanation in the accepted run took about 14–28 seconds to generate after retrieval. Payroll, authentication, and the Hello-World dispatcher abstained before the model. The numbers are in `evaluation/cursor-baseline.json`.

A public repository needs no token. A private one needs two things, and only through the API:

- `CREDENTIAL_KEY` in `.env`, any long secret you choose. The app refuses to store a token when this is empty. Add it before you start the API.
- `access_token` on `POST /api/v1/repository`. For GitHub this is a personal access token with access to the repository contents. It is not an OpenAI or Cursor key.

`tracked_branch` is optional. Leave it off and Repona uses the default branch. The first successful index sticks to that branch. A later request for a different branch is rejected.

## API

Use the same `user_id` for the index call and later questions. Any string up to 36 characters is fine. The page generates one and stores it in the browser. There is no login.

Indexing blocks until the build finishes. `action` is `serve` when you can ask. `wait` means this commit is not ready yet, often because another request is still indexing it. Send the same request again.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/repository \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/pallets/itsdangerous","user_id":"local-user"}'
```

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"repository_id":"<id from the first response>","user_id":"local-user","question":"Which functions call derive_key?"}'
```

Read `answer` and `citations`. You can also set `provider` to `cursor`, `claude`, `codex`, `copilot`, `opencode`, `zai`, or `ollama`, and `model` to that provider's model id. Leave both off to use `.env`. `openai` is selected with `LLM_PROVIDER`, not with the `provider` field.

## What gets indexed

Repona shallow-fetches the tracked branch. It keeps source files for programming languages, plus JSON, Markdown, YAML, TOML, SQL, shell, Dockerfiles, `requirements.txt`, `go.mod`, `CMakeLists.txt`, Makefiles, `Gemfile`, and `Rakefile`. It skips `.git`, `node_modules`, `build`, `dist`, and lockfiles.

Declarations and calls are read when the language has a grammar. Python, JavaScript, and TypeScript use their own parser. Other languages use tree-sitter grammars, which download on first use. A language with no grammar is still stored as text, so a question can cite the file.

Postgres holds the catalog. Neo4j holds calls and dependencies. Qdrant holds embeddings from `all-MiniLM-L6-v2`. A question is searched by symbol name, by the words in the file, and by those embeddings. The model writes the answer from that evidence. Citations are the passages included in that evidence.

## Limits

Small public repositories are the intended size. A very large repository can take a long time, and the browser waits on the indexing request until it returns.

Every visitor to one running copy uses the same `LLM_PROVIDER`. The page does not choose a model per question.

`action: "wait"` means the commit is not ready to query. Repeat the request after indexing finishes. The page shows the message and does not poll.
