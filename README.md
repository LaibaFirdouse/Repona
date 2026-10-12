# Repona

Paste a public GitHub URL, then ask a question about that code. You get a short answer in plain language, plus the file and lines it came from.

Open http://127.0.0.1:8000. A GitHub link indexes that repository. Anything else is a question about the last one you indexed. A public repository needs no token. A private one needs a GitHub token that can read the contents, passed to the API.

## What it does

It fetches the default branch and keeps source files for each programming language, plus JSON, Markdown, YAML, TOML, SQL, shell, Dockerfiles, and `requirements.txt`. It skips `.git`, `node_modules`, `build`, `dist`, and lockfiles. Declarations and calls are read from each programming language that has a grammar. A language with no grammar is still kept as source text, so a question can cite the file.

Postgres holds the catalog, Neo4j holds the call graph, and Qdrant holds the embeddings (`all-MiniLM-L6-v2`). A question is searched by name, by the words in the text, and by meaning. The model writes the answer from that evidence.

The model is the one named by `LLM_PROVIDER` on the machine running the app. The page has no picker. Set it to the tool you already have signed in: `cursor`, `claude`, `codex`, `copilot`, `opencode`, `zai`, `ollama`, or `openai`. Restart after you change it. Everyone who uses that copy gets the same model.

A small public repository is the comfortable size. A very large one can take a long time.

## Run it

You need Docker, Python 3.12, and the model command from `LLM_PROVIDER` installed and signed in on this machine. The API runs on the host so it can call that command. Postgres, Qdrant, and Neo4j run in Docker.

```bash
docker compose up -d postgres qdrant neo4j
cp .env.example .env
python -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=backend .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Postgres is on port 5433. The page is on port 8000. `.env.example` already points at those services and at Cursor. Change `LLM_PROVIDER` before you start the app if you want a different model.

## Ask from the API

Indexing waits until the repository is ready. `action` is `serve` when you can ask.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/repository \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/pallets/itsdangerous","user_id":"local-user"}'
```

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"repository_id":"<id>","user_id":"local-user","question":"Which functions call derive_key?"}'
```

Read `answer` and `citations`. An API call can also set `provider` to `cursor`, `claude`, `codex`, `copilot`, `opencode`, `zai`, or `ollama`, and `model` to that provider's model id. Leave both off to use `.env`.
