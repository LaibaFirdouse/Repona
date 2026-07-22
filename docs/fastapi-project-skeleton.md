# FastAPI Project Skeleton Learning Guide

## 1. Phase Goal

This phase creates the first runnable backend skeleton for the Repository Intelligence Agent.

The goal is intentionally small:

- Create the backend folder structure.
- Create the FastAPI application entry point.
- Configure FastAPI in a clean way.
- Add a health endpoint.
- Add environment-based configuration.
- Document every file and concept introduced.

This phase does **not** add AI, databases, Docker, background workers, repository analysis, authentication, or frontend code.

## 2. What Was Implemented

The project now has a minimal FastAPI backend structure:

```text
REPO-aNA/
  .env.example
  requirements.txt

  backend/
    app/
      __init__.py
      main.py

      api/
        __init__.py
        v1/
          __init__.py
          router.py
          routes/
            __init__.py
            health.py

      core/
        __init__.py
        config.py

      schemas/
        __init__.py
        health.py

  docs/
    repository-intelligence-agent-architecture.md
    fastapi-project-skeleton.md
```

The backend currently exposes one endpoint:

```text
GET /api/v1/health
```

Its purpose is to confirm that the backend application is alive and can return a predictable response.

## 3. Why This Skeleton Matters

A backend project can become messy very quickly if every route, setting, schema, and service is placed in one file.

This skeleton introduces simple boundaries early:

- `main.py` starts the app.
- `api/` contains HTTP route wiring.
- `routes/` contains endpoint definitions.
- `schemas/` contains request and response shapes.
- `core/` contains shared application configuration.
- `docs/` contains learning and architecture notes.

Even though the current backend is tiny, the structure can grow without needing a painful rewrite.

## 4. File-by-File Explanation

## 4.1 `.env.example`

Purpose:

- Documents environment variables used by the backend.
- Gives developers a safe template for local configuration.
- Avoids committing real secrets.

Current values:

```text
APP_NAME=Repository Intelligence Agent API
APP_ENV=local
APP_VERSION=0.1.0
API_V1_PREFIX=/api/v1
DEBUG=true
```

Important concept:

An `.env.example` file is committed to the repository. A real `.env` file is usually created locally and ignored by Git because it may contain secrets.

This project does not contain secrets yet, but introducing the pattern early is a production-quality habit.

## 4.2 `requirements.txt`

Purpose:

- Lists Python dependencies needed for this phase.
- Allows a developer to install FastAPI and Uvicorn.

Current dependencies:

```text
fastapi
uvicorn[standard]
```

FastAPI provides the web framework.

Uvicorn is the ASGI server that runs the FastAPI app locally.

Important concept:

FastAPI defines the application. Uvicorn runs it.

## 4.3 `backend/app/__init__.py`

Purpose:

- Marks `backend/app` as a Python package.
- Allows imports such as `from app.core.config import settings`.

Important concept:

In Python, `__init__.py` helps organize folders as importable packages. Even when the file is almost empty, it makes package boundaries explicit for beginners.

## 4.4 `backend/app/main.py`

Purpose:

- Creates the FastAPI application.
- Applies application-level settings.
- Registers the versioned API router.
- Exposes the `app` object used by Uvicorn.

Current responsibilities:

```text
1. Import FastAPI.
2. Import the API router.
3. Import environment settings.
4. Build the FastAPI app in create_app().
5. Attach API routes under the configured prefix.
6. Expose app = create_app().
```

Important concept:

The project uses a `create_app()` function instead of placing all setup directly at module level.

This pattern is useful because it makes app creation easier to test and easier to extend later with middleware, exception handlers, startup events, or routers.

## 4.5 `backend/app/api/__init__.py`

Purpose:

- Marks the API folder as a Python package.
- Groups HTTP-related code.

Important concept:

The API layer should focus on HTTP concerns, not business logic.

Examples of HTTP concerns:

- Routes
- Request validation
- Response models
- Status codes
- Dependency injection

## 4.6 `backend/app/api/v1/__init__.py`

Purpose:

- Marks version 1 of the API as a package.
- Creates a clear place for `/api/v1` routes.

Important concept:

API versioning protects clients from breaking changes later.

If the backend eventually needs a redesigned API, a future `v2` folder can be added without immediately deleting `v1`.

## 4.7 `backend/app/api/v1/router.py`

Purpose:

- Creates the version 1 API router.
- Includes route modules under one shared router.

Current behavior:

```text
api_router = APIRouter()
api_router.include_router(health.router)
```

Important concept:

A router is a collection of routes.

Instead of registering every endpoint directly in `main.py`, the app registers one versioned router. That router can then include many route modules as the project grows.

This keeps `main.py` small and prevents route registration from becoming scattered.

## 4.8 `backend/app/api/v1/routes/__init__.py`

Purpose:

- Marks the route folder as a package.
- Provides a dedicated home for endpoint modules.

Future route modules may include:

- `repositories.py`
- `analysis_jobs.py`
- `questions.py`
- `graphs.py`

This phase only includes `health.py`.

## 4.9 `backend/app/api/v1/routes/health.py`

Purpose:

- Defines the health endpoint.
- Returns basic application status.

Endpoint:

```text
GET /api/v1/health
```

Response shape:

```json
{
  "status": "ok",
  "app_name": "Repository Intelligence Agent API",
  "environment": "local",
  "version": "0.1.0"
}
```

Important concept:

A health endpoint is a simple endpoint used to verify that the application is running.

Health endpoints are useful for:

- Local development checks
- Deployment checks
- Load balancer checks
- Monitoring systems
- Smoke tests

This endpoint does not check databases, AI services, Docker, or external systems because those are intentionally not part of this phase.

## 4.10 `backend/app/core/__init__.py`

Purpose:

- Marks `core` as a Python package.
- Provides a home for shared backend concerns.

Future `core` responsibilities may include:

- Configuration
- Logging
- Error handling
- Security helpers
- Middleware setup

This phase only adds configuration.

## 4.11 `backend/app/core/config.py`

Purpose:

- Reads backend settings from environment variables.
- Provides a single `settings` object used by the app.

Current settings:

- `app_name`
- `app_env`
- `app_version`
- `api_v1_prefix`
- `debug`

Important concept:

Configuration should not be hardcoded throughout the app.

Instead of writing the app name, version, or API prefix in many files, the project reads those values from one place.

The file uses a frozen dataclass:

```text
@dataclass(frozen=True)
class Settings:
```

A frozen dataclass means the settings object should not be changed after it is created. This keeps configuration predictable during runtime.

The `_get_bool_env()` helper converts environment variable text into a Python boolean.

For example:

```text
DEBUG=true
```

becomes:

```text
True
```

## 4.12 `backend/app/schemas/__init__.py`

Purpose:

- Marks the schemas folder as a package.
- Groups API request and response models.

Important concept:

Schemas define the shape of data crossing the API boundary.

They help FastAPI:

- Validate data
- Generate OpenAPI docs
- Serialize responses
- Show clear contracts to frontend developers

## 4.13 `backend/app/schemas/health.py`

Purpose:

- Defines the response model for the health endpoint.

Current schema:

```text
HealthResponse
```

Fields:

- `status`
- `app_name`
- `environment`
- `version`

Important concept:

The route uses `response_model=HealthResponse` so FastAPI knows exactly what the endpoint should return.

This improves documentation and protects the API from accidentally returning inconsistent data.

## 4.14 `docs/fastapi-project-skeleton.md`

Purpose:

- Documents this implementation phase.
- Explains every file created.
- Captures beginner-friendly architecture, flow, trade-offs, interview notes, and key takeaways.

This document should remain a long-term reference as the project grows.

## 5. Request Execution Flow

When a client calls the health endpoint, the flow is:

```text
1. Client sends GET /api/v1/health.
2. Uvicorn receives the HTTP request.
3. Uvicorn passes the request to the FastAPI app.
4. FastAPI matches the request against registered routes.
5. The /api/v1 prefix comes from settings.api_v1_prefix.
6. The /health route is found in health.py.
7. health_check() runs.
8. health_check() reads values from settings.
9. A HealthResponse object is returned.
10. FastAPI serializes the response to JSON.
11. Client receives the health response.
```

The important architecture lesson is that each file has a small role.

`main.py` does not know the details of the health response.

`health.py` does not know how the whole app starts.

`config.py` does not know anything about HTTP.

`schemas/health.py` only defines the response contract.

## 6. How to Run Later

This phase does not require running the backend, but the skeleton is designed to be runnable after dependencies are installed.

Typical local commands from the repository root would be:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r ..\requirements.txt
uvicorn app.main:app --reload
```

Then the health endpoint would be available at:

```text
http://127.0.0.1:8000/api/v1/health
```

FastAPI documentation would be available at:

```text
http://127.0.0.1:8000/docs
```

These commands are documented for learning, but no Docker, database, or AI service is needed.

## 7. FastAPI Concepts Introduced

## 7.1 FastAPI App

The FastAPI app is the central web application object.

It holds:

- Routes
- Metadata
- Middleware later
- Exception handlers later
- OpenAPI documentation

In this project, the app is created in `create_app()`.

## 7.2 Router

A router groups related endpoints.

Routers help keep the app modular.

Instead of one giant file with every endpoint, each feature can have its own route module.

## 7.3 Response Model

A response model tells FastAPI what shape the response should have.

Benefits:

- Clear API contract
- Automatic docs
- Output validation
- Easier frontend integration

## 7.4 Environment Variables

Environment variables allow configuration to change without editing source code.

Examples:

- Local development can use `APP_ENV=local`.
- Production can use `APP_ENV=production`.
- Tests can use `APP_ENV=test`.

This keeps the same code usable across different environments.

## 8. Architecture Principles Used

## 8.1 Thin Entry Point

`main.py` is intentionally small.

A small entry point makes the application easier to understand and maintain.

## 8.2 Versioned API

Routes are mounted under `/api/v1`.

This prepares the backend for future API evolution.

## 8.3 Explicit Schemas

The health endpoint returns a schema instead of a loose dictionary.

This teaches the habit of defining API contracts clearly.

## 8.4 Centralized Configuration

Settings live in one place.

This avoids scattering environment variable reads across the codebase.

## 8.5 No Premature Features

This phase avoids AI, databases, Docker, authentication, and background jobs.

That is intentional. A strong backend grows in clear phases.

## 9. Trade-Offs

## 9.1 More Folders Than a Tiny Demo

A single-file FastAPI app would be shorter.

Trade-off:

- Single file is faster at the very beginning.
- Modular structure is easier to grow and learn from.

Decision:

Use a small modular structure because this project is meant to teach production-quality backend architecture.

## 9.2 Simple Config Instead of Pydantic Settings

This phase uses Python standard library environment reads instead of adding a dedicated settings library.

Trade-off:

- Standard library config is simple and beginner-friendly.
- Pydantic Settings can provide richer validation later.

Decision:

Start simple. Upgrade configuration later when the app has more settings and stronger validation needs.

## 9.3 Health Endpoint Only

Only one endpoint was added.

Trade-off:

- The app does not do much yet.
- The project now has a clean foundation for future features.

Decision:

Add the smallest endpoint that proves the backend starts and routes are wired correctly.

## 10. Interview Notes

## 10.1 Why Have a Health Endpoint?

A health endpoint lets humans and systems confirm that the service is alive.

In production, health endpoints are often used by load balancers, deployment systems, and monitoring tools.

## 10.2 Why Keep `main.py` Small?

A small `main.py` keeps startup logic separate from feature logic.

This makes the application easier to test and extend.

## 10.3 Why Use Routers?

Routers let teams organize endpoints by feature and version.

They prevent the app from turning into one giant route file.

## 10.4 Why Use Schemas?

Schemas make API contracts explicit.

They help the backend and frontend agree on request and response shapes.

## 10.5 Why Use Environment Variables?

Environment variables let the same code run in different environments without modification.

This is a standard production practice.

## 11. Key Takeaways

- The backend now has a minimal FastAPI skeleton.
- The app is configured through environment settings.
- The API is versioned under `/api/v1`.
- The health endpoint confirms the service is alive.
- Schemas define response contracts.
- The structure avoids AI, databases, Docker, and unnecessary complexity.
- This foundation is ready for the next learning phase.

## 12. Recommended Next Phase

The next phase should focus on developer workflow and testing.

Suggested next steps:

1. Add a test framework.
2. Add a test for the health endpoint.
3. Add basic error handling conventions.
4. Add structured logging.
5. Document the local run workflow in the README.

The project should continue growing one small, understandable phase at a time.
