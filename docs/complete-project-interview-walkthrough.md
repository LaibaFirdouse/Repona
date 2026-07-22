# Complete Project Interview Walkthrough

## Purpose Of This Guide

This document is a staff-engineer-style walkthrough of the entire Repository Intelligence Agent project.

Use it as an interview revision guide. It explains what each folder and file does, why it belongs where it does, how execution reaches it, which files call it, what it calls, what design patterns are being used, what alternatives exist, and what production improvements matter.

This guide does not rewrite the project. It explains the project you have built.

## One-Sentence Project Summary

The Repository Intelligence Agent is a FastAPI backend that accepts a GitHub repository URL, clones the repository, extracts metadata, builds a source-code relationship graph in Neo4j, stores analysis reports in PostgreSQL, uses OpenAI to generate structured summaries and answers, and can run locally with Docker Compose.

## High-Level Architecture

The project uses a layered backend architecture:

```text
Client
  -> FastAPI route
  -> Pydantic request schema
  -> Service layer
  -> Repository cloning and metadata extraction
  -> OpenAI integration
  -> PostgreSQL persistence through SQLAlchemy
  -> Neo4j graph persistence
  -> Pydantic response schema
  -> Client
```

For repository QA, the flow is:

```text
Client question
  -> FastAPI /ask route
  -> PostgreSQL report lookup
  -> optional Neo4j graph retrieval
  -> OpenAI answer generation
  -> structured JSON answer
  -> Client
```

## Main Architectural Decisions

### Layered Architecture

The project separates responsibilities by layer:

- API layer handles HTTP.
- Schema layer defines request and response contracts.
- Service layer owns business workflows.
- CRUD layer owns database operations.
- Model layer defines SQL tables.
- DB layer creates sessions and engines.
- Core layer centralizes configuration.
- Docker files define runtime infrastructure.

Why this is useful:

- Each layer has a clear job.
- Tests can target layers independently.
- Routes stay small.
- Business logic is not trapped inside HTTP handlers.

Tradeoff:

Layered architecture is easy to learn and common in FastAPI apps. For very large systems, feature-based modules may scale better because all repository-related files can live together.

### Service-Oriented Application Layer

The service classes are the main units of business behavior:

- `RepositoryService`
- `OpenAISummaryService`
- `Neo4jGraphService`
- `RepositoryQAService`

Why this is useful:

- External effects are isolated.
- Routes can delegate work.
- Services can be tested with fake dependencies.

Tradeoff:

A service can become too large if it keeps accumulating responsibilities. `RepositoryService` is already the central orchestrator and would eventually benefit from smaller components such as a cloner, metadata extractor, and analysis pipeline.

### Polyglot Persistence

The project uses two databases:

- PostgreSQL for structured repository and report records.
- Neo4j for graph relationships between files, modules, and services.

Why this is useful:

- PostgreSQL is excellent for durable records and indexes.
- Neo4j is excellent for relationship traversal and graph-style queries.

Tradeoff:

Two databases create consistency and operations challenges. If PostgreSQL succeeds but Neo4j fails, the system needs retry or repair logic.

### Structured LLM Outputs

OpenAI is asked to return JSON, and that JSON is validated with Pydantic.

Why this is useful:

- API responses stay predictable.
- Invalid model output is caught early.
- The rest of the application can treat AI output as typed data.

Tradeoff:

LLMs can still return malformed or incomplete JSON. Production systems should use retries, schema-constrained outputs where available, robust validation, and fallback behavior.

## Root Folder Walkthrough

### `requirements.txt`

Why it exists:

This file lists Python dependencies needed by the backend.

Current dependencies:

- `fastapi`: web framework.
- `uvicorn[standard]`: ASGI server used to run FastAPI.
- `sqlalchemy`: ORM and database toolkit.
- `psycopg[binary]`: PostgreSQL driver.
- `openai`: OpenAI SDK.
- `neo4j`: Neo4j Python driver.

Why it belongs at the root:

The Dockerfile copies it before copying the backend so dependency installation can be cached during Docker builds.

How execution reaches it:

Docker uses it during image build:

```dockerfile
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt
```

Design pattern:

This is dependency declaration, not runtime logic.

Alternatives:

- Pin exact versions in `requirements.txt`.
- Use `requirements.in` plus `pip-tools`.
- Use Poetry.
- Use uv.

Production improvement:

Pin versions for reproducible builds. Unpinned dependencies are convenient early but risky in production.

Interview question:

Why should production dependencies be pinned?

Strong answer:

Pinned dependencies make deployments reproducible. Without pinned versions, a fresh build can install newer package versions and break behavior unexpectedly.

### `.env.example`

Why it exists:

This file documents environment variables used when running locally outside Docker.

Important values:

- `APP_NAME`
- `APP_ENV`
- `APP_VERSION`
- `API_V1_PREFIX`
- `DATABASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `OPENAI_TEMPERATURE`
- `OPENAI_MAX_OUTPUT_TOKENS`
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `DEBUG`

Why it belongs at the root:

Environment examples are project-level configuration references.

How execution reaches it:

The application does not automatically load this file. A developer uses it as a template for real environment variables.

Alternatives:

- Use Pydantic Settings with `.env` support.
- Use Docker Compose `env_file`.
- Use a secret manager in production.

Production improvement:

Never store real secrets in `.env.example`. Use secret managers for deployed environments.

### `.env.docker.example`

Why it exists:

This file documents the OpenAI variables commonly needed when running with Docker Compose.

Why it is smaller than `.env.example`:

Docker Compose already defines database and Neo4j values internally, so this file mainly focuses on OpenAI settings that the developer must provide.

How execution reaches it:

Docker Compose can read environment variables from the shell or an env file if the user chooses to pass one.

Interview question:

Why keep a separate Docker env example?

Strong answer:

Docker Compose often supplies service-to-service values like database hosts internally, while local development uses localhost. Separate examples reduce confusion.

### `.dockerignore`

Why it exists:

It prevents unnecessary or sensitive files from being sent to the Docker build context.

Important entries:

- `.git`: avoids copying repository history.
- `__pycache__`, `*.pyc`: avoids Python build artifacts.
- `.venv`, `venv`: avoids copying local virtual environments.
- `.env`: avoids copying secrets.
- `node_modules`, `build`, `dist`: avoids large generated outputs.
- `.vscode`: avoids editor-specific config.

Why it belongs at the root:

Docker reads `.dockerignore` from the build context root.

Production improvement:

Keep `.env`, credentials, local caches, and generated artifacts out of images.

Interview question:

What problem does `.dockerignore` solve?

Strong answer:

It reduces build context size, improves build speed, and prevents accidental inclusion of secrets or local-only files.

### `Dockerfile`

Why it exists:

It defines how to build the FastAPI API container image.

Important lines:

```dockerfile
FROM python:3.12-slim
```

This chooses a small Python base image.

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
```

These settings reduce bytecode files, improve log flushing, and reduce pip cache usage.

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
```

Git is installed because `RepositoryService` runs `git clone`.

```dockerfile
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt
```

Dependencies are installed before copying source code to improve Docker layer caching.

```dockerfile
COPY backend ./backend
WORKDIR /app/backend
```

The backend package is copied into the image and the working directory is set so `app.main:app` resolves correctly.

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

This starts the FastAPI application.

Why it belongs at the root:

It builds the whole backend service from the project root and needs access to `requirements.txt` and `backend`.

Production improvements:

- Run as a non-root user.
- Pin dependency versions.
- Consider a multi-stage build.
- Add healthcheck if deploying outside Compose.
- Use Gunicorn with Uvicorn workers for some production deployments.
- Avoid installing build tools unless needed.

Interview question:

Why does Docker use `0.0.0.0` instead of `localhost`?

Strong answer:

Inside a container, binding to `localhost` would only expose the server inside the container. Binding to `0.0.0.0` allows Docker port mapping to route host traffic to the container.

### `docker-compose.yml`

Why it exists:

It starts the local development stack:

- FastAPI API
- PostgreSQL
- Neo4j

Why it belongs at the root:

Docker Compose is a project-level orchestration file.

Services:

#### `api`

Builds from the local Dockerfile. It maps host port `8000` to container port `8000`.

Important environment values:

- `DATABASE_URL` points to `postgres`, not `localhost`.
- `NEO4J_URI` points to `bolt://neo4j:7687`.
- `OPENAI_API_KEY` is read from the host environment.

Why service names matter:

In a Docker Compose network, service names become DNS names. The API container can connect to PostgreSQL using hostname `postgres` and Neo4j using hostname `neo4j`.

#### `postgres`

Uses `postgres:16-alpine`. It persists data in `postgres_data`.

Important line:

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U postgres -d repo_intelligence"]
```

This helps Compose know when PostgreSQL is ready.

#### `neo4j`

Uses `neo4j:5-community`. It exposes:

- `7474` for Neo4j Browser.
- `7687` for the Bolt driver connection.

It persists data and logs in named volumes.

#### Volumes

- `postgres_data`
- `neo4j_data`
- `neo4j_logs`

Volumes keep database data after containers stop.

#### Network

`repo-intelligence-network` is a bridge network. It allows services to communicate by service name.

Production improvements:

- Do not expose database ports publicly.
- Do not use hardcoded credentials.
- Use secret management.
- Add resource limits.
- Add separate worker service when background jobs are introduced.
- Use managed databases in many production environments.

Interview question:

What is the difference between an image and a container?

Strong answer:

An image is the immutable package containing filesystem and startup instructions. A container is a running instance of an image with its own process, network, and filesystem layer.

## `docs` Folder Walkthrough

Why the folder exists:

The `docs` folder contains learning guides for each project phase. This is useful because the project is not only an application but also an educational backend architecture journey.

Why it belongs at the root:

Documentation should be easy to find and independent from runtime application code.

Files:

### `repository-intelligence-agent-architecture.md`

Explains the original architecture vision. It is the conceptual foundation.

Interview use:

Read this when asked to describe the system at a high level.

### `fastapi-project-skeleton.md`

Explains the initial FastAPI skeleton and why the folders were created.

Interview use:

Read this when asked how a FastAPI project is structured.

### `repository-api.md`

Explains the first repository API endpoint.

Interview use:

Read this when asked how request/response schemas and routes work.

### `repository-service-metadata.md`

Explains cloning and metadata extraction.

Interview use:

Read this when asked how repository analysis begins.

### `postgresql-sqlalchemy-integration.md`

Explains PostgreSQL and SQLAlchemy integration.

Interview use:

Read this when asked about relational persistence and ORM models.

### `openai-summary-integration.md`

Explains OpenAI-powered repository summaries.

Interview use:

Read this when asked how the system uses LLMs safely and structurally.

### `neo4j-graph-integration.md`

Explains graph storage and graph queries.

Interview use:

Read this when asked why Neo4j is used.

### `repository-qa-rag.md`

Explains the retrieval-augmented QA endpoint.

Interview use:

Read this when asked how the system answers questions using stored context.

### `docker-containerization.md`

Explains Dockerfile, Docker Compose, containers, images, volumes, and networks.

Interview use:

Read this when asked how the local stack runs.

### `staff-engineer-code-review.md`

Contains a staff-engineer review of architecture, scalability, security, performance, and production readiness.

Interview use:

Read this when asked what you would improve next.

### `complete-project-interview-walkthrough.md`

This document. It ties the whole project together as a complete interview revision guide.

Production improvement:

As the project grows, add a `docs/index.md` that links all phase documents in order.

## `backend` Folder Walkthrough

Why the folder exists:

`backend` contains the Python API application.

Why it belongs below root:

Keeping backend code in its own folder makes room for future frontend, infrastructure, scripts, tests, and deployment folders.

Possible alternatives:

- Put `app` directly at the root for a smaller project.
- Use `src/app` layout for packaging discipline.
- Use feature modules under `backend/app` for larger systems.

Production improvement:

Add sibling folders later:

```text
backend/tests
backend/alembic
backend/scripts
```

## `backend/app` Package Walkthrough

### `backend/app/__init__.py`

Why it exists:

It marks `backend/app` as a Python package.

Current content:

```python
"""Repository Intelligence Agent backend application package."""
```

Why it belongs here:

Python imports such as `from app.core.config import settings` rely on `app` being importable as a package.

How execution reaches it:

When Python imports anything under `app`, package initialization can pass through this file.

Design decision:

It contains only a docstring, which is appropriate. Package files should not perform side effects unless necessary.

Interview question:

Why do Python projects use `__init__.py`?

Strong answer:

It marks a directory as a package and can optionally define package-level exports. In modern Python, namespace packages exist, but `__init__.py` is still common for explicit package boundaries.

### `backend/app/main.py`

Why it exists:

This is the FastAPI application entry point.

Why it belongs in `app`:

The ASGI server imports `app.main:app`, so this file is the natural place to create and expose the FastAPI object.

Important imports:

```python
from fastapi import FastAPI
from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import create_database_tables
```

What they mean:

- `FastAPI` creates the application.
- `api_router` contains all versioned routes.
- `settings` provides app name, version, debug flag, and prefix.
- `create_database_tables` initializes SQL tables during startup.

Function: `create_app() -> FastAPI`

Why it exists:

It uses the application factory pattern. Instead of constructing the app only at module level, the logic is wrapped in a function.

What problem it solves:

- Easier testing.
- Clear app setup location.
- Future environment-specific app creation.

Important lines:

```python
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
)
```

This configures app metadata and debug behavior.

```python
app.include_router(api_router, prefix=settings.api_v1_prefix)
```

This mounts all v1 routes under `/api/v1` by default.

```python
@app.on_event("startup")
def on_startup() -> None:
    create_database_tables()
```

This creates database tables when the app starts.

Execution path:

- Uvicorn starts with `app.main:app`.
- Python imports `app/main.py`.
- `app = create_app()` runs.
- FastAPI app is configured.
- On startup, tables are created.

Calls:

- Calls `api_router` from `app.api.v1.router`.
- Calls `settings` from `app.core.config`.
- Calls `create_database_tables` from `app.db.session`.

Called by:

- Uvicorn through the Dockerfile command.
- Local development command if running `uvicorn app.main:app`.

Design patterns:

- Application factory.
- Central route registration.
- Startup hook.

Alternatives:

- Define `app = FastAPI()` directly without a function.
- Use FastAPI lifespan context instead of `on_event`.
- Use Alembic migrations instead of `create_all()`.

Production improvements:

- Replace `on_event` with lifespan because startup events are being superseded by lifespan patterns in modern FastAPI.
- Replace `create_database_tables()` with Alembic migrations.
- Add CORS, middleware, correlation IDs, and exception handlers.
- Add readiness and liveness endpoints.

Interview question:

Why use an application factory?

Strong answer:

It centralizes app construction, makes testing easier, and allows different app setups for different environments.

## Core Configuration

### `backend/app/core/__init__.py`

Why it exists:

It marks `core` as a package.

Why it belongs here:

`core` is for cross-cutting foundational concerns like configuration.

### `backend/app/core/config.py`

Why it exists:

It centralizes environment-based configuration.

Why it belongs in `core`:

Configuration is a foundational concern used by many layers: app startup, DB setup, OpenAI services, Neo4j services, and health responses.

Helper function: `_get_bool_env(name: str, default: bool) -> bool`

Why it exists:

Environment variables are strings. This converts strings like `true`, `yes`, `1`, and `on` into booleans.

Called by:

- `Settings.debug`

Production improvement:

Reject invalid boolean values instead of silently treating unknown strings as `False`.

Helper function: `_get_float_env(name: str, default: float) -> float`

Why it exists:

OpenAI temperature is numeric, but environment variables are strings.

Called by:

- `Settings.openai_temperature`

Production improvement:

Handle invalid values with a clear config error.

Helper function: `_get_int_env(name: str, default: int) -> int`

Why it exists:

OpenAI max output tokens must be an integer.

Called by:

- `Settings.openai_max_output_tokens`

Class: `Settings`

Why it exists:

It groups all application configuration in one immutable dataclass.

Important fields:

- `app_name`: display name used by FastAPI and health response.
- `app_env`: local/dev/prod indicator.
- `app_version`: app version.
- `api_v1_prefix`: default route prefix.
- `database_url`: SQLAlchemy connection URL.
- `openai_api_key`: secret key for OpenAI.
- `openai_model`: model name.
- `openai_temperature`: controls response randomness.
- `openai_max_output_tokens`: response size control.
- `neo4j_uri`: Bolt connection string.
- `neo4j_user`: Neo4j username.
- `neo4j_password`: Neo4j password.
- `debug`: FastAPI debug mode.

Important line:

```python
settings = Settings()
```

This creates a singleton-style settings object imported throughout the app.

Execution path:

- `main.py` imports `settings`.
- `db/session.py` imports `settings.database_url`.
- OpenAI services import OpenAI settings.
- Neo4j service imports Neo4j settings.
- Health route reads app metadata.

Design pattern:

- Centralized configuration.
- Singleton settings object.

Alternatives:

- Pydantic Settings.
- Dynaconf.
- Environment-specific config files.
- Dependency-injected config object.

Production improvements:

- Use `pydantic-settings` for validation.
- Fail fast if required production secrets are missing.
- Avoid `debug=True` by default in production.
- Support `.env` loading only for local development.
- Validate temperature range and max token range.

Interview question:

Why centralize config?

Strong answer:

It prevents scattered environment reads, makes configuration auditable, and gives the application one clear source of truth for runtime settings.

## API Layer Walkthrough

### `backend/app/api/__init__.py`

Why it exists:

It marks the `api` folder as a Python package.

Why it belongs here:

`api` contains HTTP-facing modules.

### `backend/app/api/v1/__init__.py`

Why it exists:

It marks API version 1 as a package.

Why version APIs:

Versioning lets the application evolve without breaking older clients.

Alternative:

Use unversioned routes early. But production APIs benefit from versioning.

### `backend/app/api/v1/router.py`

Why it exists:

It aggregates all v1 route modules into one router.

Important lines:

```python
from app.api.v1.routes import ask, health, repository
```

This imports route modules.

```python
api_router = APIRouter()
api_router.include_router(ask.router)
api_router.include_router(health.router)
api_router.include_router(repository.router)
```

This combines route modules into a single router mounted by `main.py`.

Called by:

- `main.py`

Calls:

- `ask.router`
- `health.router`
- `repository.router`

Design pattern:

- Router composition.
- API version aggregation.

Production improvements:

- Add route prefixes by domain, such as `/repositories`.
- Add dependencies at router level for authentication.
- Add tags and response metadata consistently.

Interview question:

Why have a router aggregator?

Strong answer:

It keeps application startup simple. `main.py` only includes one versioned router, while individual route modules remain focused.

### `backend/app/api/v1/routes/__init__.py`

Why it exists:

It marks the route directory as a package.

Why it belongs here:

Route modules live together under the API version.

### `backend/app/api/v1/routes/health.py`

Why it exists:

It exposes a lightweight health endpoint.

Function: `health_check() -> HealthResponse`

Why it exists:

It lets clients or operators confirm the API process is alive and returning basic metadata.

Important line:

```python
@router.get("/health", response_model=HealthResponse)
```

This registers `GET /api/v1/health`.

Execution path:

- Client calls `/api/v1/health`.
- FastAPI routes request to `health_check`.
- Function returns `HealthResponse`.

Calls:

- Reads `settings`.
- Returns `HealthResponse`.

Called by:

- FastAPI router.

Design pattern:

- Thin route handler.
- Response schema validation.

Production improvements:

- Split liveness and readiness.
- Readiness should check PostgreSQL and Neo4j connectivity.
- Avoid exposing too much environment detail publicly.

Interview question:

What is the difference between liveness and readiness?

Strong answer:

Liveness says the process is running. Readiness says the process can serve traffic because dependencies are available.

### `backend/app/api/v1/routes/repository.py`

Why it exists:

It defines the endpoint that starts repository analysis.

Important module-level objects:

```python
router = APIRouter(tags=["repository"])
repository_service = RepositoryService()
```

The router groups repository routes. The service instance handles business logic.

Function: `create_repository(...) -> RepositoryCreateResponse`

Why it exists:

It receives a repository URL from the client and delegates analysis to `RepositoryService`.

Important decorator:

```python
@router.post(
    "/repository",
    response_model=RepositoryCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
```

This registers `POST /api/v1/repository` and declares the response type.

Important parameter:

```python
request: RepositoryCreateRequest
```

FastAPI validates the request body using Pydantic.

Important dependency:

```python
db: Session = Depends(get_db)
```

FastAPI injects a SQLAlchemy session.

Calls:

- `RepositoryService.create_repository`
- `get_db`

Called by:

- FastAPI when a client sends `POST /api/v1/repository`.

Error handling:

```python
except RepositoryServiceError as error:
    raise HTTPException(status_code=400, detail=str(error))
```

This converts service errors into HTTP responses.

Design pattern:

- Controller/route delegates to service.
- Dependency injection through FastAPI.
- DTO/schema validation with Pydantic.

Alternatives:

- Put logic directly in route handler. Simpler but harder to test.
- Use dependency-injected service factory instead of module-level service.
- Return `202 Accepted` with a job ID instead of doing analysis synchronously.

Production improvements:

- Make analysis asynchronous.
- Return structured error codes.
- Use more precise HTTP statuses.
- Add authentication and authorization.
- Add rate limiting.

Interview question:

Why should routes stay thin?

Strong answer:

Thin routes keep HTTP concerns separate from business logic, making the core workflow easier to test and reuse.

### `backend/app/api/v1/routes/ask.py`

Why it exists:

It defines the endpoint for asking questions about an analyzed repository.

Important module-level objects:

```python
router = APIRouter(tags=["repository qa"])
qa_service = RepositoryQAService()
```

Function: `ask_repository_question(...) -> RepositoryQuestionResponse`

Why it exists:

It receives a repository question, loads relevant context through the service layer, and returns an AI-generated answer.

Important decorator:

```python
@router.post("/ask", response_model=RepositoryQuestionResponse)
```

This registers `POST /api/v1/ask`.

Calls:

- `RepositoryQAService.answer_question`
- `get_db`

Called by:

- FastAPI when a client sends `POST /api/v1/ask`.

Design pattern:

- RAG endpoint.
- Thin route.
- Service delegation.
- Pydantic response model.

Production improvements:

- Use `404` when repository is missing.
- Add user authorization so users cannot query repositories they do not own.
- Add question rate limits.
- Add audit logging for AI queries.

Interview question:

What makes this endpoint RAG-like?

Strong answer:

It retrieves stored repository context from PostgreSQL and optionally Neo4j before asking OpenAI to answer using that context.

## Database Layer Walkthrough

### `backend/app/db/__init__.py`

Why it exists:

It marks `db` as a package.

Why it belongs here:

The `db` folder owns database setup and session lifecycle.

### `backend/app/db/base.py`

Why it exists:

It defines the SQLAlchemy declarative base that all ORM models inherit from.

Class: `Base(DeclarativeBase)`

Why it exists:

SQLAlchemy uses the base class to collect metadata about tables.

Called by:

- `Repository` model.
- `AnalysisReport` model.
- `create_database_tables()` through `Base.metadata.create_all()`.

Design pattern:

- Declarative ORM mapping.

Production improvement:

Keep this file minimal. Later Alembic will import model metadata from this base for migrations.

Interview question:

What does SQLAlchemy's declarative base do?

Strong answer:

It provides a base class for ORM models and stores table metadata that SQLAlchemy can use to create tables or generate migrations.

### `backend/app/db/session.py`

Why it exists:

It creates the SQLAlchemy engine, session factory, table initialization function, and FastAPI database dependency.

Important line:

```python
engine = create_engine(settings.database_url, pool_pre_ping=True)
```

This creates the database engine. `pool_pre_ping=True` helps detect stale database connections before using them.

Important line:

```python
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
```

This creates a factory for SQLAlchemy sessions.

Function: `create_database_tables() -> None`

Why it exists:

It creates tables from SQLAlchemy model metadata during application startup.

Important line:

```python
import app.models
```

This import ensures model classes are registered with SQLAlchemy metadata before `create_all` runs.

Important line:

```python
Base.metadata.create_all(bind=engine)
```

This creates missing tables.

Function: `get_db() -> Generator[Session, None, None]`

Why it exists:

It is a FastAPI dependency that provides a database session per request.

Execution path:

- Route declares `db: Session = Depends(get_db)`.
- FastAPI calls `get_db`.
- `SessionLocal()` creates a session.
- Route/service uses session.
- `finally` closes session.

Called by:

- `repository.py`
- `ask.py`

Calls:

- `settings.database_url`
- `Base.metadata.create_all`
- SQLAlchemy engine/session APIs.

Design patterns:

- Unit-of-work-style session per request.
- Dependency injection.

Alternatives:

- Async SQLAlchemy engine and sessions.
- Repository pattern with injected session.
- Managed transaction middleware.

Production improvements:

- Use Alembic instead of `create_all`.
- Configure pool size and timeout.
- Add transaction management strategy.
- Add database health checks.
- Consider async database access if the whole stack becomes async.

Interview question:

Why close the DB session in `finally`?

Strong answer:

It guarantees the connection is returned to the pool even if the request raises an exception.

## Models Walkthrough

### `backend/app/models/__init__.py`

Why it exists:

It imports model classes so they are registered with SQLAlchemy metadata.

Important lines:

```python
from app.models.analysis_report import AnalysisReport
from app.models.repository import Repository

__all__ = ["AnalysisReport", "Repository"]
```

Why it matters:

When `create_database_tables()` imports `app.models`, these imports load both ORM models.

Production improvement:

As models grow, keep this file updated or use an explicit model import pattern for Alembic.

### `backend/app/models/repository.py`

Why it exists:

It defines the `repositories` table.

Class: `Repository(Base)`

Why it exists:

A repository is the durable identity of a submitted code repository.

Important fields:

```python
id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
```

Uses UUID strings as primary keys.

```python
repo_url: Mapped[str] = mapped_column(String(2048), unique=True, index=True, nullable=False)
```

Stores the repository URL and prevents duplicate repository records for the same URL.

```python
created_at` and `updated_at`
```

Track lifecycle timestamps.

```python
analysis_reports = relationship(... cascade="all, delete-orphan")
```

Links a repository to many analysis reports. If a repository is deleted, related reports are also deleted.

Called by:

- `RepositoryCRUD`
- SQLAlchemy metadata initialization.

Calls:

- `Base`
- SQLAlchemy column and relationship APIs.

Design pattern:

- ORM entity.
- One-to-many relationship.

Alternatives:

- Use PostgreSQL UUID type instead of string.
- Store normalized repository host/owner/name separately.
- Add commit SHA to distinguish analyses over time.

Production improvements:

- Add owner/user relationship.
- Add normalized repo fields.
- Add unique constraint on provider, owner, name, and commit SHA.
- Use timezone-aware timestamps.

Interview question:

Why is `repo_url` unique?

Strong answer:

It prevents duplicate repository rows for the same URL and lets repeated analyses attach reports to the same repository identity.

### `backend/app/models/analysis_report.py`

Why it exists:

It defines the `analysis_reports` table.

Class: `AnalysisReport(Base)`

Why it exists:

An analysis report stores the output of one repository analysis run.

Important fields:

```python
repository_id = mapped_column(ForeignKey("repositories.id"), index=True, nullable=False)
```

Links the report to its repository.

```python
status = mapped_column(String(50), nullable=False)
```

Stores report status. Currently set to `analyzed`.

```python
file_count` and `directory_count`
```

Store basic metrics.

```python
ignored_directories`, `technologies`, `directory_structure`, `summary`, `token_usage`
```

These are JSON columns storing flexible analysis data.

```python
repository = relationship(back_populates="analysis_reports")
```

Completes the relationship from report back to repository.

Called by:

- `RepositoryCRUD`
- SQLAlchemy metadata initialization.
- `RepositoryQAService` indirectly reads report fields through CRUD.

Design pattern:

- ORM entity.
- JSON document storage inside relational records.

Alternatives:

- Normalize technologies into a separate table.
- Store directory structure in object storage.
- Store LLM usage in a separate usage table.
- Add a separate `AnalysisJob` table.

Production improvements:

- Add indexes on status and created_at if querying by them.
- Add commit SHA.
- Add failure reason fields.
- Add graph status.
- Consider JSONB-specific indexing in PostgreSQL.

Interview question:

When is JSON in PostgreSQL a good idea?

Strong answer:

JSON is useful for flexible, nested data that is mostly read as a document. If fields need frequent filtering, joining, or constraints, normalized tables may be better.

## CRUD Layer Walkthrough

### `backend/app/crud/__init__.py`

Why it exists:

It marks `crud` as a package.

Why it belongs here:

CRUD modules contain database access logic.

### `backend/app/crud/repository_crud.py`

Why it exists:

It isolates SQLAlchemy queries and persistence operations for repositories and analysis reports.

Class: `RepositoryCRUD`

Why it exists:

It gives the service layer a simple API for database actions without spreading SQLAlchemy queries throughout services.

Function: `get_repository_by_id(db, repository_id)`

Why it exists:

Looks up a repository by primary key.

Called by:

- `RepositoryQAService.answer_question`

Function: `get_repository_by_url(db, repo_url)`

Why it exists:

Finds an existing repository row for a submitted URL.

Called by:

- `get_or_create_repository`

Function: `get_latest_analysis_report(db, repository_id)`

Why it exists:

Loads the newest analysis report for repository QA.

Important line:

```python
.order_by(AnalysisReport.created_at.desc()).first()
```

This picks the latest report.

Called by:

- `RepositoryQAService.answer_question`

Function: `get_or_create_repository(db, repo_url)`

Why it exists:

Avoids duplicate repository rows. If the repository exists, it returns it; otherwise it creates one.

Important line:

```python
db.flush()
```

Flush sends pending changes to the database so generated IDs are available before commit.

Called by:

- `save_repository_analysis`

Function: `create_analysis_report(...)`

Why it exists:

Creates an `AnalysisReport` row from analysis output.

Called by:

- `save_repository_analysis`

Function: `save_repository_analysis(...)`

Why it exists:

It coordinates repository creation/reuse and report creation in one database transaction.

Important lines:

```python
db.commit()
db.refresh(repository)
db.refresh(analysis_report)
```

Commit persists changes. Refresh reloads generated or updated fields.

Called by:

- `RepositoryService.create_repository`

Design patterns:

- Repository pattern or DAO-like database abstraction.
- Transaction boundary in CRUD method.

Alternatives:

- Keep transaction management in service layer.
- Use SQLAlchemy 2.0 select syntax.
- Use separate repositories for `Repository` and `AnalysisReport`.

Tradeoff:

Committing inside CRUD is simple. In larger systems, service-level transaction control is often better because one workflow may need multiple repository operations in one transaction.

Production improvements:

- Handle unique constraint race conditions.
- Add explicit transaction context.
- Add typed return models or DTOs.
- Use migrations and indexes.

Interview question:

What is the difference between `flush` and `commit`?

Strong answer:

`flush` sends pending SQL to the database within the current transaction but does not finalize it. `commit` finalizes the transaction and makes changes durable.

## Schema Layer Walkthrough

### `backend/app/schemas/__init__.py`

Why it exists:

It marks `schemas` as a package.

Why it belongs here:

Schemas define API contracts and internal structured data objects.

### `backend/app/schemas/health.py`

Class: `HealthResponse`

Why it exists:

Defines the response shape for the health endpoint.

Fields:

- `status`
- `app_name`
- `environment`
- `version`

Called by:

- `health.py`

Design pattern:

- Data transfer object.

Production improvement:

Add dependency status only to readiness endpoints, not public liveness endpoints.

### `backend/app/schemas/repository.py`

Why it exists:

It defines all Pydantic models related to repository analysis.

Class: `RepositoryCreateRequest`

Why it exists:

Validates input for `POST /repository`.

Important field:

```python
repo_url: HttpUrl
```

This ensures the user sends a syntactically valid URL.

Production improvement:

Add stricter rules: only HTTPS, trusted hosts, no private network targets.

Class: `DirectoryEntry`

Why it exists:

Represents one file or directory in the repository tree.

Important field:

```python
children: list["DirectoryEntry"]
```

This is recursive: a directory can contain more directory entries.

Class: `TechnologyDetection`

Why it exists:

Represents one detected technology, such as FastAPI, React, or TypeScript.

Fields:

- `name`
- `category`
- `source`

Class: `RepositoryMetadata`

Why it exists:

Groups extracted repository metadata.

Fields:

- file count
- directory count
- ignored directories
- technologies
- directory structure

Class: `RepositorySummary`

Why it exists:

Represents the structured summary generated by OpenAI.

Fields:

- `executive_summary`
- `main_technologies`
- `architecture_observations`
- `notable_directories`
- `next_steps`

Class: `TokenUsage`

Why it exists:

Tracks LLM token usage.

Why this matters:

Token usage is important for cost tracking, monitoring, and rate limiting.

Class: `RepositorySummaryResult`

Why it exists:

Bundles OpenAI summary and token usage.

Class: `RepositoryGraphStats`

Why it exists:

Reports graph size after Neo4j graph extraction/storage.

Class: `RepositoryCreateResponse`

Why it exists:

Defines the complete response returned by `POST /repository`.

Called by:

- `repository.py`
- `repository_service.py`
- `openai_summary_service.py`
- `neo4j_graph_service.py`

Design patterns:

- DTOs.
- Contract-first API design.
- Recursive model.
- Structured AI output validation.

Alternatives:

- Separate internal domain models from external API schemas.
- Use response projection models for smaller API responses.
- Store graph stats separately from repository response.

Production improvements:

- Limit response payload size.
- Add examples for OpenAPI docs.
- Add stricter field validation.
- Use enums for statuses and confidence values.

Interview question:

Why use Pydantic schemas instead of raw dictionaries?

Strong answer:

Pydantic validates data, documents API contracts, improves editor support, and lets FastAPI generate OpenAPI documentation.

### `backend/app/schemas/qa.py`

Why it exists:

It defines request and response contracts for repository question answering.

Class: `RepositoryQuestionRequest`

Why it exists:

Validates the repository ID and question.

Important fields:

```python
repository_id: str = Field(..., min_length=1)
question: str = Field(..., min_length=3, max_length=2000)
```

This prevents empty IDs and extremely large questions.

Class: `RepositoryQuestionAnswer`

Why it exists:

Represents the structured answer returned by OpenAI.

Fields:

- `answer`
- `confidence`
- `sources`
- `graph_context_used`

Class: `RepositoryQuestionResponse`

Why it exists:

Wraps the answer with repository ID, original question, and token usage.

Called by:

- `ask.py`
- `repository_qa_service.py`

Production improvements:

- Use enum for confidence.
- Add citations with stronger source identifiers.
- Add answer refusal or insufficient-context fields.
- Add moderation or safety checks for question input if exposed publicly.

Interview question:

Why limit question length?

Strong answer:

It protects the API and LLM budget from excessively large inputs and helps prevent denial-of-service or cost spikes.

## Service Layer Walkthrough

### `backend/app/services/__init__.py`

Why it exists:

It marks `services` as a package.

Why it belongs here:

Services contain business workflows and integrations.

### `backend/app/services/repository_service.py`

Why it exists:

It orchestrates the repository analysis workflow.

This is the most important business workflow in the project.

Imports and why they matter:

- `json`: parse package.json.
- `os`: walk directories.
- `subprocess`: run `git clone`.
- `tempfile`: create temporary clone directory.
- `Path`: filesystem path handling.
- `SQLAlchemyError`: catch database errors.
- `Session`: type hint for database sessions.
- `tomllib`: parse `pyproject.toml` on Python versions where available.
- `RepositoryCRUD`: persist repository analysis.
- Repository schemas: construct typed metadata and responses.
- `Neo4jGraphService`: build and store graph.
- `OpenAISummaryService`: generate AI summary.

Class: `RepositoryServiceError`

Why it exists:

It creates a service-specific exception type so routes can catch repository workflow errors without knowing every underlying library exception.

Class: `RepositoryService`

Why it exists:

It owns the full repository analysis use case.

Class variable: `ignored_directories`

Why it exists:

It avoids scanning noisy or huge folders such as `.git`, `node_modules`, `.venv`, and build outputs.

Production improvement:

Make ignore patterns configurable and support `.gitignore` parsing.

Function: `__init__(...)`

Why it exists:

It creates default dependencies while allowing dependency injection for tests.

Important design choice:

```python
self.repository_crud = repository_crud or RepositoryCRUD()
```

This lets tests inject fake CRUD, fake OpenAI service, or fake graph service.

Function: `create_repository(request, db)`

Why it exists:

This is the main use case: clone, analyze, summarize, persist, graph, respond.

Execution steps:

1. Convert Pydantic URL to string.
2. Create temporary directory.
3. Clone repository.
4. Extract metadata.
5. Build graph in memory.
6. Delete temporary directory when context exits.
7. Call OpenAI for summary.
8. Save analysis to PostgreSQL.
9. Store graph in Neo4j.
10. Return `RepositoryCreateResponse`.

Important line:

```python
with tempfile.TemporaryDirectory(prefix="repo-intelligence-") as temporary_directory:
```

This prevents cloned repositories from permanently accumulating on disk.

Important line:

```python
self.clone_repository(repo_url, repository_path)
```

This is where external Git network access happens.

Important line:

```python
graph = self.graph_service.build_graph(...)
```

Graph is built while the cloned files still exist.

Important tradeoff:

The graph is built before OpenAI and persistence, but stored in Neo4j after PostgreSQL. If Neo4j fails, PostgreSQL may already contain a report.

Function: `serialize_model(model)`

Why it exists:

Pydantic v1 used `.dict()`, while Pydantic v2 uses `.model_dump()`. This helper supports both.

Function: `serialize_model_list(models)`

Why it exists:

Converts a list of Pydantic models into JSON-serializable dictionaries for PostgreSQL JSON columns.

Function: `clone_repository(repo_url, destination)`

Why it exists:

Runs shallow `git clone` for the submitted repository.

Important line:

```python
"--depth", "1"
```

This makes a shallow clone, reducing clone time and disk usage.

Important line:

```python
timeout=120
```

This prevents clone from running forever.

Security concern:

The URL comes from user input. Production systems must restrict allowed protocols and hosts.

Function: `read_repository_metadata(repository_path)`

Why it exists:

Coordinates directory tree extraction, file counting, directory counting, and technology detection into one `RepositoryMetadata` object.

Function: `build_directory_structure(directory, repository_path)`

Why it exists:

Recursively creates a tree of `DirectoryEntry` objects.

Tradeoff:

Returning full directory trees is helpful for learning but can become huge for large repositories.

Function: `iter_visible_children(directory)`

Why it exists:

Returns sorted child paths that are not ignored.

Important detail:

Sorting by `(child.is_file(), child.name.lower())` puts directories before files because `False` sorts before `True`.

Function: `should_ignore(path)`

Why it exists:

Centralizes ignore logic.

Function: `count_files(repository_path)`

Why it exists:

Counts non-ignored files.

Function: `count_directories(repository_path)`

Why it exists:

Counts non-ignored directories.

Function: `detect_technologies(repository_path)`

Why it exists:

Looks for known dependency files and delegates to specific detectors.

Function: `detect_from_package_json(package_json_path)`

Why it exists:

Detects JavaScript/TypeScript ecosystem technologies.

Important idea:

It reads both `dependencies` and `devDependencies`.

Function: `detect_from_requirements(requirements_path)`

Why it exists:

Detects Python ecosystem technologies from `requirements.txt`.

Function: `detect_from_pyproject(pyproject_path)`

Why it exists:

Detects Python packaging and framework info from `pyproject.toml`.

Function: `deduplicate_technologies(detections)`

Why it exists:

Removes duplicate technology detections and sorts results for stable output.

Function: `build_repository_id(repo_url)`

Why it exists:

Builds a simple ID from repo URL, but it is currently unused because database UUIDs are used instead.

Production consideration:

Remove unused code or repurpose it only if deterministic IDs become a deliberate design decision.

Called by:

- `repository.py`

Calls:

- `RepositoryCRUD`
- `OpenAISummaryService`
- `Neo4jGraphService`
- filesystem APIs
- Git subprocess

Design patterns:

- Orchestrator service.
- Dependency injection.
- Adapter around Git subprocess.
- Data mapper from filesystem data into Pydantic schemas.

Alternatives:

- Background worker pipeline.
- Git library such as GitPython.
- Separate metadata extraction service.
- Event-driven pipeline with job status.

Production improvements:

- Move to async job queue.
- Add URL allowlist and SSRF protection.
- Add repository size limits.
- Add file size limits.
- Add structured logs around each step.
- Add retryable job states.
- Store commit SHA.
- Add tests for detectors.

Interview question:

Why use `--depth 1` when cloning?

Strong answer:

The system analyzes the current repository state, not full history. A shallow clone reduces bandwidth, disk usage, and latency.

### `backend/app/services/openai_summary_service.py`

Why it exists:

It owns the OpenAI call that converts repository metadata into a structured summary.

Class: `OpenAISummaryServiceError`

Why it exists:

It wraps OpenAI and parsing failures in a service-specific error.

Class: `OpenAISummaryService`

Why it exists:

It isolates prompt creation, OpenAI API calls, JSON parsing, and token usage extraction.

Function: `__init__(client=None)`

Why it exists:

Allows a real OpenAI client in production and a fake client in tests.

Function: `summarize_repository(repo_url, metadata)`

Why it exists:

Coordinates summary generation.

Execution steps:

1. Build system prompt.
2. Build user prompt.
3. Call OpenAI.
4. Parse JSON response into `RepositorySummary`.
5. Return `RepositorySummaryResult`.

Called by:

- `RepositoryService.create_repository`

Function: `build_system_prompt()`

Why it exists:

Defines the model's role and output rules.

Important instruction:

Return only valid JSON and base the answer only on provided metadata.

Function: `build_user_prompt(repo_url, metadata)`

Why it exists:

Packages repository metadata into a structured prompt payload.

Important line:

```python
"top_level_structure": [... metadata.directory_structure[:30]]
```

This limits prompt size by including only a sample of top-level structure.

Function: `serialize_directory_entry(entry, max_depth=2)`

Why it exists:

Limits nested directory structure depth and breadth for prompt safety.

Important line:

```python
for child in entry.children[:20]
```

Limits children per directory.

Function: `call_openai(system_prompt, user_prompt)`

Why it exists:

Makes the actual OpenAI SDK call.

Important line:

```python
if not settings.openai_api_key:
    raise OpenAISummaryServiceError("OPENAI_API_KEY is not configured.")
```

Fails clearly when OpenAI is not configured.

Important line:

```python
response_format={"type": "json_object"}
```

Asks the model to return JSON.

Function: `parse_summary(response_content)`

Why it exists:

Validates that the LLM response is valid JSON and matches `RepositorySummary`.

Function: `extract_token_usage(response)`

Why it exists:

Converts OpenAI usage metadata into the project's `TokenUsage` schema.

Design patterns:

- Gateway/service wrapper around external API.
- Structured output validation.
- Dependency injection.

Alternatives:

- Use OpenAI structured outputs with JSON schema.
- Use a prompt templating library.
- Use a queue for LLM calls.
- Cache summaries by repository commit SHA.

Production improvements:

- Add retries with backoff.
- Track cost by user/repository.
- Add model timeouts.
- Add prompt size estimation.
- Add fallback summary when OpenAI fails.
- Add redaction for sensitive repository content if code content is ever sent.

Interview question:

Why validate LLM output with Pydantic?

Strong answer:

LLM output is probabilistic. Pydantic turns it into a typed contract and catches invalid or incomplete responses before they affect downstream code.

### `backend/app/services/neo4j_graph_service.py`

Why it exists:

It builds an in-memory graph from repository source files, stores that graph in Neo4j, and retrieves graph context for QA.

Class: `FileNode`

Why it exists:

Represents one source file in the graph.

Fields:

- `path`: relative file path.
- `module`: top-level module or folder.
- `is_service`: whether file looks like a service.

Design choice:

It is a frozen dataclass, so file nodes are immutable after creation.

Class: `RepositoryGraph`

Why it exists:

Represents the full extracted graph before storage.

Fields:

- `files`: file nodes.
- `file_imports`: file-to-file import relationships.
- `module_uses`: module-to-module relationships.
- `services`: service file paths.

Function: `stats()`

Why it exists:

Creates `RepositoryGraphStats` for API response.

Class: `Neo4jGraphServiceError`

Why it exists:

Wraps graph and Neo4j failures in a service-specific error.

Class: `Neo4jGraphService`

Why it exists:

Owns graph extraction, graph storage, and graph retrieval.

Class variable: `source_file_extensions`

Why it exists:

Limits graph analysis to Python and JavaScript/TypeScript source files.

Class variable: `javascript_import_pattern`

Why it exists:

Extracts import-like paths from JavaScript and TypeScript files.

Tradeoff:

Regex is simple but not as accurate as a real parser.

Function: `__init__(driver=None)`

Why it exists:

Allows dependency injection of a Neo4j driver for tests.

Function: `build_graph(repository_path, ignored_directories)`

Why it exists:

Builds a `RepositoryGraph` from source files.

Execution steps:

1. Collect source files.
2. Build a lookup from relative path to file path.
3. Create `FileNode` entries.
4. Detect modules and service files.
5. Extract imports from each source file.
6. Resolve imports to local files.
7. Add file import edges.
8. Add module usage edges.

Called by:

- `RepositoryService.create_repository`

Function: `store_graph(repository_id, repo_url, graph)`

Why it exists:

Writes graph data to Neo4j.

Important line:

```python
GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
```

Creates a Neo4j connection.

Important line:

```python
session.execute_write(self.write_graph, repository_id, repo_url, graph)
```

Executes graph write logic inside a Neo4j write transaction.

Function: `query_repository_context(repository_id)`

Why it exists:

Reads graph context for repository QA.

Called by:

- `RepositoryQAService.answer_question`

Function: `collect_source_files(...)`

Why it exists:

Walks the repository and collects only relevant source files.

Function: `detect_module_name(relative_path)`

Why it exists:

Groups files into modules by top-level folder or filename.

Function: `is_service_file(file_path, relative_path)`

Why it exists:

Uses naming conventions to detect likely service files.

Function: `extract_imports(file_path)`

Why it exists:

Dispatches to language-specific import extractors.

Function: `extract_python_imports(file_path)`

Why it exists:

Uses Python's `ast` module to parse imports safely instead of regex.

Function: `extract_javascript_imports(file_path)`

Why it exists:

Uses regex to extract JS/TS import targets.

Function: `resolve_import(...)`

Why it exists:

Dispatches to Python or JavaScript import resolution.

Function: `resolve_python_import(...)`

Why it exists:

Maps Python import strings to local repository file paths.

Function: `resolve_javascript_import(...)`

Why it exists:

Maps relative JS/TS import strings to local files.

Important security line:

```python
if not candidate_base.is_relative_to(repository_path.resolve()):
    return None
```

This prevents resolved paths from escaping the repository root.

Function: `match_candidate_path(...)`

Why it exists:

Tries possible file paths for imports, including file extensions, `index` files, and `__init__.py`.

Function: `write_graph(transaction, repository_id, repo_url, graph)`

Why it exists:

Writes Neo4j nodes and relationships.

Important Cypher concepts:

- `MERGE` creates or matches existing nodes.
- `UNWIND` batches list data.
- Relationships include `CONTAINS`, `IMPORTS`, `USES`, and `IMPLEMENTED_IN`.

Function: `read_repository_context(transaction, repository_id)`

Why it exists:

Reads graph slices useful for LLM answering:

- imported files
- module uses
- services
- central files

Design patterns:

- Graph builder.
- Repository graph domain model.
- External database gateway.
- Transaction callback functions.

Alternatives:

- Use tree-sitter for multi-language parsing.
- Use language server protocol analysis.
- Store graph in PostgreSQL tables instead of Neo4j.
- Use static analysis tools per language.

Production improvements:

- Add Neo4j constraints and indexes.
- Batch very large graph writes.
- Add graph deletion/replacement strategy for reanalysis.
- Use real parsers for JS/TS.
- Add support for more languages.
- Add file size limits before parsing.
- Add tests for import resolution.

Interview question:

Why use Neo4j instead of only PostgreSQL?

Strong answer:

The data is relationship-heavy. Neo4j makes traversals like imports, module dependencies, and central files natural and efficient compared with deeply recursive joins.

### `backend/app/services/repository_qa_service.py`

Why it exists:

It implements the retrieval-augmented question answering workflow.

Class: `RepositoryQAServiceError`

Why it exists:

Wraps QA-specific failures.

Class: `RepositoryQAService`

Why it exists:

It loads stored repository context, optionally retrieves graph context, builds an LLM prompt, and returns a structured answer.

Class variable: `graph_keywords`

Why it exists:

It determines whether a question likely needs graph context.

Tradeoff:

Keyword routing is simple and explainable but imperfect. A classifier or query planner could be better later.

Function: `__init__(...)`

Why it exists:

Allows injected CRUD, graph service, and OpenAI client for tests.

Function: `answer_question(request, db)`

Why it exists:

This is the main QA use case.

Execution steps:

1. Load repository by ID.
2. Load latest analysis report.
3. Decide whether graph context is needed.
4. Query Neo4j if needed.
5. Build system prompt.
6. Build user prompt with retrieved context.
7. Call OpenAI.
8. Return `RepositoryQuestionResponse`.

Called by:

- `ask.py`

Calls:

- `RepositoryCRUD.get_repository_by_id`
- `RepositoryCRUD.get_latest_analysis_report`
- `Neo4jGraphService.query_repository_context`
- OpenAI chat completions

Function: `should_query_graph(question)`

Why it exists:

Determines whether to include Neo4j graph context.

Function: `build_system_prompt()`

Why it exists:

Constrains the assistant to answer only from retrieved context and return JSON.

Function: `build_user_prompt(...)`

Why it exists:

Packages question, repository metadata, analysis report, directory sample, and optional graph context into a JSON prompt.

Function: `call_llm(system_prompt, user_prompt)`

Why it exists:

Calls OpenAI and requests JSON output.

Function: `parse_answer(response_content)`

Why it exists:

Validates JSON output against `RepositoryQuestionAnswer`.

Function: `extract_token_usage(response)`

Why it exists:

Converts OpenAI token usage into the app schema.

Design patterns:

- RAG orchestration.
- Dependency injection.
- Structured output validation.
- Heuristic retrieval routing.

Alternatives:

- Always query graph context.
- Use embedding search over file chunks.
- Use a query classifier.
- Use hybrid retrieval: PostgreSQL metadata, Neo4j graph, vector search.

Production improvements:

- Add vector search for semantic code retrieval.
- Add conversation history if needed.
- Add user authorization.
- Add prompt length controls.
- Add answer caching.
- Add stronger citation model.
- Add LLM retry and timeout policy.

Interview question:

What does RAG mean here?

Strong answer:

The system retrieves stored repository context from PostgreSQL and Neo4j, then augments the LLM prompt with that context so the answer is grounded in known analysis data.

## Complete End-To-End Execution Flow

This section explains the full system from repository URL submission to final AI responses.

### Part 1: Starting The System With Docker

1. Developer runs Docker Compose.
2. Docker builds the API image from `Dockerfile`.
3. Docker starts PostgreSQL from `postgres:16-alpine`.
4. Docker starts Neo4j from `neo4j:5-community`.
5. Docker waits for health checks.
6. Docker starts the API container.
7. Uvicorn runs `app.main:app`.
8. `main.py` creates the FastAPI app.
9. Startup event calls `create_database_tables()`.
10. SQLAlchemy creates missing PostgreSQL tables.

Important Docker network detail:

The API container connects to `postgres` and `neo4j` by service name because all services share the Compose bridge network.

### Part 2: User Submits A Repository URL

1. Client sends `POST /api/v1/repository`.
2. Request body contains `repo_url`.
3. FastAPI validates request using `RepositoryCreateRequest`.
4. `repository.py` receives the typed request.
5. FastAPI injects a SQLAlchemy session using `get_db()`.
6. Route calls `RepositoryService.create_repository(request, db)`.

### Part 3: Repository Cloning

1. `RepositoryService` converts `repo_url` to string.
2. It creates a temporary directory.
3. It runs `git clone --depth 1 <repo_url> <destination>`.
4. Git downloads the repository into the temporary directory.
5. If Git is missing, times out, or fails, `RepositoryServiceError` is raised.

Production consideration:

This is the highest-risk area because it touches user-provided URLs and external network access.

### Part 4: Metadata Extraction

1. `read_repository_metadata()` starts metadata extraction.
2. `build_directory_structure()` recursively creates `DirectoryEntry` objects.
3. `count_files()` counts visible files.
4. `count_directories()` counts visible directories.
5. `detect_technologies()` looks for dependency files.
6. `detect_from_package_json()` detects Node, React, Vite, Next.js, etc.
7. `detect_from_requirements()` detects Python, FastAPI, SQLAlchemy, etc.
8. `detect_from_pyproject()` detects Python packaging and frameworks.
9. `deduplicate_technologies()` returns stable unique detections.
10. A `RepositoryMetadata` object is created.

### Part 5: Graph Construction

1. `RepositoryService` calls `Neo4jGraphService.build_graph()`.
2. `collect_source_files()` finds `.py`, `.js`, `.jsx`, `.ts`, and `.tsx` files.
3. Each file becomes a `FileNode`.
4. `detect_module_name()` assigns module names.
5. `is_service_file()` marks likely service files.
6. `extract_imports()` dispatches to Python or JS/TS parsing.
7. Python files are parsed with `ast`.
8. JS/TS files are scanned with regex.
9. `resolve_import()` maps import strings to local files.
10. The service records file import edges and module usage edges.
11. A `RepositoryGraph` is returned.

### Part 6: OpenAI Summary Generation

1. Temporary directory is cleaned up after metadata and graph are built.
2. `RepositoryService` calls `OpenAISummaryService.summarize_repository()`.
3. The summary service builds a system prompt.
4. It builds a user prompt containing repository metadata.
5. It calls OpenAI chat completions.
6. OpenAI returns JSON.
7. `parse_summary()` validates the JSON with `RepositorySummary`.
8. `extract_token_usage()` captures token usage.
9. `RepositorySummaryResult` is returned.

### Part 7: PostgreSQL Persistence

1. `RepositoryService` calls `RepositoryCRUD.save_repository_analysis()`.
2. CRUD checks whether repository URL already exists.
3. If not, it creates a `Repository` row.
4. It creates an `AnalysisReport` row with metadata, summary, and token usage.
5. It commits the transaction.
6. It refreshes ORM objects.
7. It returns repository and analysis report objects.

### Part 8: Neo4j Persistence

1. `RepositoryService` calls `Neo4jGraphService.store_graph()`.
2. The graph service creates a Neo4j driver.
3. It opens a session.
4. It runs `write_graph()` in a write transaction.
5. Cypher `MERGE` creates or updates repository, file, module, and service nodes.
6. Cypher `MERGE` creates graph relationships.
7. The driver closes.
8. `RepositoryGraphStats` is returned.

Important consistency note:

PostgreSQL commit happens before Neo4j graph storage. If Neo4j fails, the report exists but graph storage may be incomplete.

### Part 9: Repository API Response

1. `RepositoryService` builds `RepositoryCreateResponse`.
2. Response includes repository ID, analysis report ID, metadata, summary, token usage, and graph stats.
3. FastAPI validates the response model.
4. Client receives HTTP `201 Created`.

This is the first AI response: the repository summary generated by OpenAI.

### Part 10: User Asks A Follow-Up Question

1. Client sends `POST /api/v1/ask`.
2. Body includes `repository_id` and `question`.
3. FastAPI validates `RepositoryQuestionRequest`.
4. Route calls `RepositoryQAService.answer_question()`.
5. QA service loads repository from PostgreSQL.
6. QA service loads latest analysis report from PostgreSQL.
7. `should_query_graph()` checks whether the question needs graph context.
8. If yes, Neo4j is queried for imports, module dependencies, services, and central files.
9. QA service builds a prompt with retrieved context.
10. QA service calls OpenAI.
11. OpenAI returns JSON answer.
12. `parse_answer()` validates it with `RepositoryQuestionAnswer`.
13. FastAPI returns `RepositoryQuestionResponse`.

This is the final AI answer path for repository QA.

## Interview-Ready System Explanation

A concise explanation:

This project is a layered FastAPI backend for repository intelligence. The API accepts a repository URL, validates it with Pydantic, and delegates to a service layer. The service clones the repository with Git, extracts metadata from the filesystem, detects technologies from dependency files, builds a graph of source files and imports, generates an OpenAI summary, stores durable report data in PostgreSQL using SQLAlchemy, stores relationship data in Neo4j, and returns a typed response. A separate QA endpoint retrieves the latest analysis report and optional graph context, then uses OpenAI to answer questions in structured JSON.

## Common Interview Questions And Strong Answers

### Why FastAPI?

FastAPI gives automatic request validation, response validation, dependency injection, OpenAPI docs, and strong type-hint integration. It is a good choice for building clear backend APIs quickly.

### Why Pydantic?

Pydantic turns untrusted JSON into validated Python objects and documents the API contract. It is especially useful here because LLM output also needs validation.

### Why SQLAlchemy?

SQLAlchemy provides a mature ORM and database abstraction. It lets the project model repositories and analysis reports as Python classes while still using PostgreSQL as the durable store.

### Why PostgreSQL?

PostgreSQL is reliable for structured application data such as repositories, reports, timestamps, statuses, and JSON metadata.

### Why Neo4j?

Neo4j is useful for relationship-heavy questions like imports, module dependencies, central files, and service relationships.

### Why Docker Compose?

Docker Compose makes the local multi-service stack reproducible. It starts API, PostgreSQL, and Neo4j on a shared network with persistent volumes.

### What is the main bottleneck?

Repository analysis runs synchronously inside the API request. Cloning, parsing, graphing, LLM calls, and database writes can all be slow. Production should use background jobs.

### What is the main security risk?

User-provided repository URLs are passed to `git clone`. Production must restrict protocols and hosts, block private networks, sandbox analysis, and enforce resource limits.

### What is the main reliability issue?

PostgreSQL and Neo4j writes are separate. If one succeeds and the other fails, the system can become partially consistent. Production needs statuses, retries, and repair workflows.

### What design patterns are visible?

- Layered architecture.
- Service layer.
- Repository/CRUD pattern.
- Dependency injection.
- Application factory.
- DTO/schema pattern.
- Gateway pattern for OpenAI and Neo4j.
- RAG orchestration.

## Production Roadmap

Recommended order:

1. Add automated tests.
2. Pin dependencies.
3. Add Alembic migrations.
4. Add structured application errors.
5. Add repository URL allowlist and SSRF protection.
6. Add authentication and authorization.
7. Move repository analysis to background workers.
8. Add job status model.
9. Add observability: logs, metrics, traces, correlation IDs.
10. Add rate limits and LLM cost controls.
11. Add Neo4j indexes and constraints.
12. Add commit-SHA-based caching.
13. Add vector search for deeper code QA.

## Key Takeaways

- The project has a clean educational backend architecture.
- Routes are thin and delegate to services.
- Pydantic schemas create strong API contracts.
- SQLAlchemy models persist repository and report data in PostgreSQL.
- Neo4j stores relationship data that is useful for architecture questions.
- OpenAI integration is wrapped in services and validated with schemas.
- Docker Compose gives a practical local development environment.
- The next major production step is asynchronous job processing.
- The most important security topic is safe handling of user-provided repository URLs.
- The most important reliability topic is partial failure across PostgreSQL, Neo4j, Git, and OpenAI.
