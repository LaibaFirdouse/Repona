# Repona

Repona indexes a Git repository and answers questions about the code in plain language. Each answer names the file and the lines it came from.

The page is for a public GitHub URL. The API can also index a private repository if you pass a token that can read its contents.

## Requirements

- Docker
- An API key for the model the container will call. The image does not include the Cursor, Claude, Codex, or Copilot command-line tools.

## Install

Put `OPENAI_API_KEY` in `.env`. Compose passes that value into the app container. The container uses OpenAI unless you set `CONTAINER_LLM_PROVIDER` to `zai` or `ollama`.

```bash
cp .env.example .env
docker compose up -d --build
```

The app, Postgres, Qdrant, and Neo4j join `repo-intelligence-network`. Inside that network the app reaches them as `postgres`, `qdrant`, and `neo4j`. The page is published on http://127.0.0.1:8000. Postgres is also on host port 5433, Qdrant on 6333, and Neo4j bolt on 7687.

Indexed repositories are stored in the `app_data` volume. The first image build downloads the embedding model, so it takes a while.

To run the API on the host instead, so it can call a local model command, start only the stores and point `.env` at `localhost`:

```bash
docker compose up -d postgres qdrant neo4j
python -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

That host process needs Python 3.12 and Git. `.env.example` uses port 5433 and `LLM_PROVIDER=cursor`. Without `.env`, the process looks for Postgres on port 5432. Do not run this process and the `api` container at the same time. Both bind port 8000.

`GET /api/v1/health` returns `ok` when the process is up. It does not check the stores or the model.

## Use the page

Open http://127.0.0.1:8000 and paste a public GitHub URL. Indexing keeps that request open until the repository is ready. A small project takes a few minutes. The next message in the box is a question about that repository. The browser stores the repository id locally, so later questions go to the same one.

## Configuration

`LLM_PROVIDER` chooses the model for every person using this copy of the app. Allowed values are `cursor`, `claude`, `codex`, `copilot`, `opencode`, `zai`, `ollama`, and `openai`. Each one has a model name in `.env` (`CURSOR_MODEL`, `CLAUDE_MODEL`, and so on). `openai` also needs `OPENAI_API_KEY`. `zai` needs `ZAI_API_KEY`. Ollama is expected at `http://localhost:11434` unless you change `OLLAMA_BASE_URL`.

The container reads `CONTAINER_LLM_PROVIDER`, default `openai`, plus `OPENAI_API_KEY`, `OPENAI_MODEL`, `ZAI_API_KEY`, and `CREDENTIAL_KEY` from the project `.env`. Its database addresses are set in `docker-compose.yml` and point at the other services on `repo-intelligence-network`, not at `localhost`.

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
