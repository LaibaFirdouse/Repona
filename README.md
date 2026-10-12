# Repona

Repona indexes a Git repository and answers questions about the code in plain language. Each answer names the file and the lines it came from.

The page is for a public GitHub URL. The API can also index a private repository if you pass a token that can read its contents.

## Requirements

- Docker, for Postgres, Qdrant, and Neo4j
- Python 3.12
- Git
- The model command named by `LLM_PROVIDER`, already installed and signed in on this machine

The API runs on the host so it can call that command. The three stores run in Docker.

## Install

```bash
docker compose up -d postgres qdrant neo4j
cp .env.example .env
python -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Copy `.env` before you start the app. The example file points Postgres at port 5433, which is the port Compose publishes. Without that file the app looks for Postgres on 5432 and will not connect.

The page is http://127.0.0.1:8000. Qdrant is on 6333. Neo4j bolt is on 7687. The example `.env` already has the Neo4j password Compose uses, and it sets the model to Cursor. Change `LLM_PROVIDER` in `.env` before startup if you want a different one, then restart the API after any later change.

`GET /api/v1/health` returns `ok` when the process is up. It does not check the stores or the model.

## Use the page

Open http://127.0.0.1:8000 and paste a public GitHub URL. Indexing keeps that request open until the repository is ready. A small project takes a few minutes. The next message in the box is a question about that repository. The browser stores the repository id locally, so later questions go to the same one.

## Configuration

`LLM_PROVIDER` chooses the model for every person using this copy of the app. Allowed values are `cursor`, `claude`, `codex`, `copilot`, `opencode`, `zai`, `ollama`, and `openai`. Each one has a model name in `.env` (`CURSOR_MODEL`, `CLAUDE_MODEL`, and so on). `openai` also needs `OPENAI_API_KEY`. `zai` needs `ZAI_API_KEY`. Ollama is expected at `http://localhost:11434` unless you change `OLLAMA_BASE_URL`.

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
