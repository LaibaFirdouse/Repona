# Repona

Repona indexes a Git repository and answers questions about its code in plain language. Each answer includes citations identifying the source file and line range.

It combines symbol search, full-text search, vector retrieval, and code relationships to build evidence for repository questions. A configured language model generates the answer, and Repona validates its citations against the retrieved source.

Repona supports public GitHub repositories without a token and private repositories when supplied with a GitHub personal access token that can read their contents.

## Features

- **Hybrid retrieval:** Combines symbol-name search, full-text search, and semantic vector search.
- **Code intelligence:** Extracts declarations, function calls, and dependency relationships.
- **Graph-based context:** Uses Neo4j to store code relationships and dependencies.
- **Source-grounded answers:** Generates explanations from retrieved repository evidence.
- **Citation validation:** Checks that citations refer to retrieved source spans and quoted text appears in the cited source.
- **Grounded refusals:** When the asked behavior is not in the retrieved source, the model says so and cites what the repository does contain.
- **Generation-based indexing:** Keeps the previous ready generation active until a new generation is ready.
- **Local execution:** Runs the API and data services locally, with a host-side bridge connecting the API to the Cursor CLI.

## Architecture

```text
                         User
                           |
                           v
                  Web Interface
                  frontend/index.html
                           |
                           v
                  FastAPI Application
                      :8000
                           |
              +------------+------------+
              |                         |
              v                         v
       Repository Indexing        Question Answering
              |                         |
              v                         v
       Git Snapshot              Hybrid Retrieval
              |                         |
              |                +--------+--------+
              |                |        |        |
              |                v        v        v
              |             Postgres  Qdrant   Neo4j
              |                |        |        |
              |                +--------+--------+
              |                         |
              |                         v
              |                  Evidence Packet
              |                         |
              |                         v
              |                  Cursor Bridge
              |                  127.0.0.1:8765
              |                         |
              |                         v
              |                     Cursor CLI
              |                         |
              |                         v
              |                  Generated Answer
              |                         |
              +-------------------------+
                                        |
                                        v
                              Citation Validation
                                        |
                                        v
                                  Final Answer
```

The diagram shows the logical flow. The API, database services, and bridge run in separate processes or containers as described below.

### Technology stack

| Component | Technology | Responsibility |
|---|---|---|
| API and web interface | FastAPI, HTML | Repository indexing and question answering |
| Catalog | PostgreSQL | Repository records, chunks, dependencies, and generations |
| Vector retrieval | Qdrant | Semantic search over embedded chunks |
| Embeddings | `all-MiniLM-L6-v2` | 384-dimensional text embeddings |
| Code relationship graph | Neo4j | Call and dependency relationships |
| Source parsing | Tree-sitter | Extracting code structure |
| Answer generation | Cursor CLI through a host-side bridge | Generating answers from evidence |
| Runtime | Docker Compose and Python | Running the API, data services, and bridge |

## Requirements

- Docker and Docker Compose.
- Python 3.12 for the host-side bridge.
- Cursor CLI (`agent`) installed and authenticated.
- Sufficient disk space for repository snapshots and the embedding model.
- A GitHub personal access token for private repositories.

The Docker image does not include the Cursor, Claude, Codex, or Copilot command-line tools.

The accepted API configuration uses Cursor through the host bridge. The bridge uses your existing Cursor CLI login; the API container does not need access to your Cursor home directory.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/LaibaFirdouse/Repona.git
cd Repona
```

### 2. Configure environment variables

Create the environment file:

```bash
cp .env.example .env
```

Set `CURSOR_BRIDGE_TOKEN` to a strong, private token of at least 16 characters.

For private repositories, also set `CREDENTIAL_KEY` before starting the API. Use a separate long secret for encrypting stored repository credentials.

Keep `.env` private and never commit credentials.

### 3. Start the data services

```bash
docker compose up -d postgres qdrant neo4j
```

Compose creates the PostgreSQL database `repo_intelligence`, but the API uses a separate database named `repona`. Create it if it does not already exist:

```bash
docker exec repo-intelligence-postgres \
  psql -U postgres \
  -c 'CREATE DATABASE repona'
```

The API uses PostgreSQL on host port `5433`, Qdrant on `6333`, and Neo4j Bolt on `7687`.

### 4. Install and start the Cursor bridge

Create a Python virtual environment:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Authenticate the Cursor CLI if necessary:

```bash
agent login
agent status
```

Load the environment variables and start the bridge:

```bash
set -a
. ./.env
set +a

PYTHONPATH=backend \
  .venv/bin/python -m app.services.llm.cursor_bridge
```

Keep this terminal running.

The bridge listens on `127.0.0.1:8765`. It accepts authenticated requests containing the question packet and invokes the Cursor CLI using the host's existing login.

Compose does not start the bridge automatically.

### 5. Build and start the API

In another terminal:

```bash
docker compose up -d --build api
```

The first image build downloads the embedding model, so it may take longer.

The accepted API container uses host networking and `LLM_PROVIDER=cursor`. It reaches the host bridge at `127.0.0.1:8765`. Postgres, Qdrant, and Neo4j publish host ports `5433`, `6333`, and `7687`, and the API connects to `127.0.0.1` on those ports.

The host bridge must be running before model-backed questions can succeed.

### 6. Open Repona

Visit:

**http://127.0.0.1:8000**

Paste a GitHub repository URL and wait for indexing to finish. Then ask questions about the repository.

The browser stores the repository ID and user ID locally, so subsequent questions use the same repository.

## Configuration

### Model providers

`LLM_PROVIDER` selects the provider for a host process. Supported values are:

- `cursor`
- `claude`
- `codex`
- `copilot`
- `opencode`
- `zai`
- `ollama`
- `openai`

Each provider has its own model setting in `.env`, such as `CURSOR_MODEL` or `CLAUDE_MODEL`.

`openai` requires `OPENAI_API_KEY`, while `zai` requires `ZAI_API_KEY`.

The accepted API configuration uses Cursor through the host bridge. **The API does not call Ollama**, and Ollama settings have been removed from the API's configuration. An Ollama process may still be running independently on the host.

An `/api/v1/ask` request may set `provider` to `cursor`, `claude`, `codex`, `copilot`, `opencode`, `zai`, or `ollama`, and `model` to that provider's model ID. `openai` is valid as `LLM_PROVIDER` and is rejected as a request field. A request that omits both uses the configured provider. The web interface does not send either field.

If the selected provider fails because of a missing or rejected key, a rate limit, a network error, or a timeout, the API returns HTTP 503 with a generic provider-unavailable message. It does not silently switch to another provider.

### Cursor bridge

The API uses `CURSOR_BRIDGE_URL`, configured as:

```text
http://127.0.0.1:8765
```

The bridge accepts these fields:

- `system_message`
- `user_message`
- `max_output_tokens`
- `model`

Other fields are rejected. Requests without the correct bearer token are rejected. `CURSOR_BRIDGE_TOKEN` authenticates the API's call to the bridge. It does not authenticate a person using the page or the API.

The bridge's `/health` endpoint returns:

```json
{"status":"ok"}
```

The bridge invokes Cursor CLI in Ask mode with sandbox mode enabled. It does not use `--force` or `--yolo`.

The CLI help nevertheless states that print mode can access shell and write tools. A probe with the same flags did not create a file inside or outside its workspace, but this does not prove that every command or filesystem operation is blocked. Treat the bridge as a local development component, not as a fully security-isolated execution environment.

The accepted run used:

- Cursor CLI: `2026.10.01-e373342`
- Model: `composer-2.5-fast`

### Private repositories

Private repository indexing requires two separate credentials, supplied through the API:

1. **`CREDENTIAL_KEY`** in `.env`. The application refuses to store a repository access token when this is unset.
2. **`access_token`** in `POST /api/v1/repository`. For GitHub, this must be a personal access token with access to the repository contents.

The GitHub token is not a Cursor or model-provider API key.

### Tracked branch

`tracked_branch` is optional. If omitted, Repona uses the repository's default branch.

The first successful index establishes the tracked branch. A later request to change it is rejected.

## Using the API

Use the same `user_id` when indexing a repository and asking questions about it. Any string up to 36 characters is accepted. The web interface generates a value and stores it in the browser.

There is no user login. The bridge token does not change that: anyone who can reach port 8000 can index a repository and ask a question.

### Index a repository

```bash
curl -X POST http://127.0.0.1:8000/api/v1/repository \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/pallets/itsdangerous",
    "user_id": "local-user"
  }'
```

The response includes the repository ID and an action indicating whether the generation is ready to serve.

### Ask a question

Replace `<id from the first response>` with the returned repository ID:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{
    "repository_id": "<id from the first response>",
    "user_id": "local-user",
    "question": "Which functions call derive_key?"
  }'
```

Read the `answer` and `citations` fields in the response.

You can optionally set `provider` to `cursor`, `claude`, `codex`, `copilot`, `opencode`, `zai`, or `ollama`, and set `model` to that provider's model ID. If both are omitted, Repona uses the configured provider.

`openai` is selected through `LLM_PROVIDER`, not through the request's `provider` field.

### Indexing behavior

Indexing blocks until the build finishes. The action is:

- `serve`: the requested generation is ready to query.
- `wait`: the commit is not ready yet, often because another request is still indexing it.

When the response says `wait`, send the same request again. The page displays the result but does not poll automatically.

## What gets indexed

Repona shallow-fetches the tracked branch and creates a snapshot of the indexed commit.

It keeps supported programming-language files, plus JSON, Markdown, YAML, TOML, SQL, shell scripts, Dockerfiles, and dependency manifests such as:

- `requirements.txt`
- `package.json`
- `pyproject.toml`
- `go.mod`
- `CMakeLists.txt`
- Makefiles
- `Gemfile`
- `Rakefile`

It skips `.git`, `node_modules`, `build`, `dist`, and lockfiles.

Python, JavaScript, and TypeScript use dedicated parsing paths. Other languages use tree-sitter grammars when available. A language without a suitable grammar is still stored as text, allowing its contents to be retrieved and cited.

The indexed source snapshot is the authority for answering questions. Chunks contain byte ranges into that snapshot, and citations are mapped back to source locations.

### Storage responsibilities

| Store | Data |
|---|---|
| PostgreSQL | Repository catalog, members, generations, files, chunks, and detected dependencies |
| Qdrant | Embeddings of source chunks |
| Neo4j | Call and dependency relationships |
| Git directory and snapshot | Source for the indexed commit |

Qdrant uses `all-MiniLM-L6-v2` embeddings with 384 dimensions and cosine similarity.

A ready generation of the same commit is served without fetching again. A new commit requires a new generation. The new generation becomes active only after indexing finishes, so questions continue to use the previous ready generation until cutover.

The Docker setup stores indexed repositories in the `app_data` volume mounted at `/app/data`. The host process stores Git data and snapshots under `./data`. These are separate storage locations and are not automatically interchangeable.

## How answering works

1. Repona checks that the user is an active member of the repository.
2. It checks whether the tracked branch still points to the indexed commit.
3. It retrieves relevant evidence through symbol search, full-text search, and Qdrant vector search.
4. It combines the hits into a bounded evidence packet containing source spans and code relationships.
5. Repona sends the packet to the configured model provider. If retrieval found no code spans, the packet still lists indexed file paths so the model can explain what is missing.
6. Repona validates citations against the retrieved spans before returning the answer.

Package-specific questions also retrieve chunks mentioning the named package.

If the model returns no claim that cites the packet, the answer is a fixed refusal line rather than invented prose.

If the selected provider fails, Repona returns HTTP 503 rather than substituting another provider.

## Evaluation

The accepted Cursor baseline is recorded in:

`evaluation/cursor-baseline.json`

The live evaluation covered eight questions:

| Case | Result |
|---|---|
| Flask dispatch | Pass |
| `Signer.sign` | Pass |
| Signing followed by validation | Pass |
| `escape` → `_escape_inner` | Pass |
| Where `itsdangerous` is used | Pass |
| Payroll withholding | Correctly abstains (pre-model gate in that run) |
| Authentication | Correctly abstains (pre-model gate in that run) |
| Hello-World dispatcher | Correctly abstains (pre-model gate in that run) |

The five answerable cases used live Cursor generation. The three unsupported cases in that run abstained before the model. The current application calls the model for those questions too and answers from the retrieved packet when the topic is absent.

In the accepted run, model generation took approximately 14–28 seconds after retrieval. Retrieval can add significant latency; one live Flask request took approximately 48.9 seconds overall.

These measurements describe the recorded evaluation, not a guarantee of latency for every repository or question.

## Operations

Check the running services:

```bash
docker compose ps
docker compose logs api
```

Stop and restart the API without stopping the data services:

```bash
docker compose stop api
docker compose start api
```

`GET /api/v1/health` returns `ok` when the API process is running. It does not check PostgreSQL, Qdrant, Neo4j, or Cursor availability.

After a reboot, start the data services and API with Compose and start the host bridge separately. If the bridge is unavailable, model-backed questions return HTTP 503.

**Do not run `docker compose down -v` unless you intend to delete the persistent volumes and indexed data.**

## Limitations

- The application is designed for local use and does not provide user authentication. The bridge token only authenticates calls to the bridge.
- The web interface does not allow model selection per question.
- The API and host bridge must both be available for Cursor-backed answers.
- Indexing blocks until the requested generation is ready; large repositories can take substantially longer than small ones.
- The page does not automatically poll while indexing is in progress.
- Answer quality depends on retrieval coverage and the evidence included in the packet.
- The Cursor CLI sandbox has not been proven to block every possible command or filesystem operation.
- The host and container store indexed source data in separate locations.

## Project goal

Repona makes unfamiliar codebases easier to understand by connecting natural-language questions to indexed source code, structural relationships, and verifiable citations.

**The core principle: answers should be grounded in repository evidence, and insufficient evidence should lead to abstention rather than speculation.**
