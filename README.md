# Repository Intelligence Backend

A high-performance REST API for indexing and natural-language querying of code repositories. This system combines **Retrieval-Augmented Generation (RAG)** with a **Neo4j knowledge graph** and a **PostgreSQL vector store** to answer developer questions about code.

Given a GitHub URL, the backend clones the repo, extracts metadata and a code knowledge graph, chunks and embeds the source code, and persists it. When asked a question (e.g. *"How is SidebarProvider implemented?"*), it generates a query embedding, retrieves relevant code snippets and graph context, constructs a prompt, and invokes a local LLM (Ollama, using the `phi3:mini` model) to produce a structured JSON answer including explanation, confidence, and sources.

> For a deep dive into internals - architecture diagrams, project structure, RAG pipeline, prompt construction, and database queries - see [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## Key Features

- **RAG over code**: Semantic search (via Sentence-Transformers) finds relevant code chunks in PostgreSQL. A Neo4j graph provides structural context (files, classes, imports).
- **LLM integration**: Ollama handles generation with strict JSON output.
- **FastAPI backend**: Built on FastAPI with Uvicorn ASGI server. Interactive OpenAPI docs auto-generated at `/docs` and `/redoc`.
- **Robust parsing**: Strips markdown fences and uses `JSONDecoder.raw_decode()` to parse the first JSON object from the LLM response, handling extra output gracefully.
- **Debugging and reliability**: Detailed logging and stop-sequences in the Ollama call to catch timeouts or invalid JSON.

---

## Tech Stack

- **Language & Frameworks**: Python 3.12, FastAPI, Uvicorn
- **Databases**: PostgreSQL (vector store), Neo4j (code knowledge graph)
- **ML / AI**: Sentence-Transformers (`all-MiniLM-L6-v2`) for embeddings, Ollama (`phi3:mini`) for generation
- **Infrastructure**: Docker & Docker Compose

---

## Getting Started

### Prerequisites

- Docker & Docker Compose
- [Ollama](https://ollama.com) installed locally (or reachable over the network)

### 1. Clone the repo

```bash
git clone https://github.com/LaibaFirdouse/Repona
cd Repona
```

### 2. Configure environment variables

Create a `.env` file in the project root:

| Variable | Description | Example |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://postgres:password@postgres:5432/repo_db` |
| `NEO4J_URI` | Neo4j Bolt URI | `bolt://neo4j:7687` |
| `NEO4J_USERNAME` | Neo4j admin username | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j admin password | `password` |
| `OLLAMA_BASE_URL` | Base URL for Ollama API | `http://host.docker.internal:11434` |
| `OLLAMA_MODEL` | Ollama model to use | `phi3:mini` |
| `EMBEDDING_MODEL` | (Optional) SentenceTransformer model | `all-MiniLM-L6-v2` |

### 3. Pull the Ollama model & start the daemon

```bash
ollama pull phi3:mini
ollama serve
```

### 4. Launch all services

```bash
docker compose up --build
```

This starts the FastAPI app, PostgreSQL, Neo4j, and Ollama. The API binds to `0.0.0.0:8000` by default.

### 5. Explore the API

Visit `http://localhost:8000/docs` (Swagger UI) or `http://localhost:8000/redoc` for interactive API documentation.

---

## Usage

### Index a repository

```bash
curl -X POST http://localhost:8000/api/v1/repository \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/owner/repository.git"}'
```

**Response:**
```json
{"repository_id": "d6f1c2a3-1234-4567-89ab-cdef01234567"}
```

### Ask a question about the repo

```bash
curl -X POST http://localhost:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{
    "repository_id": "d6f1c2a3-1234-4567-89ab-cdef01234567",
    "question": "How is SidebarProvider implemented?"
  }'
```

**Response:**
```json
{
  "repository_id": "d6f1c2a3-1234-4567-89ab-cdef01234567",
  "question": "How is SidebarProvider implemented?",
  "answer": "SidebarProvider is implemented in `components/ui/sidebar.tsx`. It defines a context and `useSidebar` hook to manage the sidebar state across the app.",
  "confidence": "high",
  "sources": ["components/ui/sidebar.tsx"],
  "graph_context_used": true,
  "token_usage": {
    "prompt_tokens": 812,
    "completion_tokens": 143,
    "total_tokens": 955
  }
}
```

---

## Example Questions to Try

**Code understanding (RAG):**
- "How is SidebarProvider implemented?"
- "Where is SidebarTrigger rendered?"
- "Explain the mobile sidebar layout."
- "How does authentication flow work in this app?"
- "Where is the Firebase configuration?"

**Structural / graph questions:**
- "Which files import the AuthContext module?"
- "Which classes inherit from BaseService?"
- "How many files are there in the repository?"
- "List the largest directories by file count."

**Stats about the index:**
- "How many code chunks were generated?"
- "How many repositories are indexed?"
- "What programming languages are used in the repo?"

---

## Troubleshooting

- **Slow responses / timeouts** - Default HTTP timeout is 120s; large repos may need 300s+. `phi3:mini` is used specifically to keep generation fast.
- **"Connection refused"** - Make sure `OLLAMA_BASE_URL` is correct and Ollama is running (`ollama serve`). Test with `curl http://localhost:11434/api/chat`.
- **Invalid or empty answers** - Check container logs; the app logs the raw LLM response before parsing to help pinpoint the issue.

For a full breakdown of internals, logging, and JSON-parsing safeguards, see [ARCHITECTURE.md](./ARCHITECTURE.md).