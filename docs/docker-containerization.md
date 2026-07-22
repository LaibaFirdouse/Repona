# Docker Containerization Learning Guide

## 1. Phase Goal

This phase containerizes the Repository Intelligence Agent backend and its infrastructure dependencies.

The Docker setup runs:

- FastAPI backend
- PostgreSQL
- Neo4j

Created files:

```text
Dockerfile
docker-compose.yml
.dockerignore
.env.docker.example

docs/docker-containerization.md
```

This phase does not change the application logic. It packages and runs the existing backend with the services it already depends on.

## 2. What Was Implemented

The project now has a Docker-based local environment.

The stack contains three main services:

```text
api
postgres
neo4j
```

High-level architecture:

```text
Host machine
  |
  v
Docker Compose
  |
  |-- api container running FastAPI on port 8000
  |-- postgres container running PostgreSQL on port 5432
  |-- neo4j container running Neo4j HTTP on port 7474 and Bolt on port 7687
  |
  v
Docker network: repo-intelligence-network
```

The API container talks to PostgreSQL and Neo4j using service names:

```text
postgres
neo4j
```

Inside Docker Compose, service names act like internal DNS names.

## 3. Files Created

## 3.1 Dockerfile

Purpose:

- Defines how to build the FastAPI backend image.

The Dockerfile:

```text
1. Starts from python:3.12-slim.
2. Sets Python runtime environment variables.
3. Installs git.
4. Copies requirements.txt.
5. Installs Python dependencies.
6. Copies the backend source code.
7. Sets backend as the working directory.
8. Exposes port 8000.
9. Runs uvicorn.
```

Why Git is installed:

The RepositoryService clones repositories using the `git` command. The container must include Git or repository analysis will fail.

Why `python:3.12-slim` is used:

It is smaller than the full Python image while still being beginner-friendly and compatible with this backend.

## 3.2 docker-compose.yml

Purpose:

- Defines and runs the multi-container local development stack.

Services defined:

```text
api
postgres
neo4j
```

It also defines:

```text
volumes
networks
healthchecks
ports
environment variables
```

## 3.3 .dockerignore

Purpose:

- Excludes files and folders from the Docker build context.

Examples ignored:

```text
.git
__pycache__
.venv
.env
node_modules
build
dist
```

Why it matters:

Docker sends the build context to the Docker engine. Ignoring unnecessary files makes builds faster, smaller, and safer.

## 3.4 .env.docker.example

Purpose:

- Shows optional environment variables that can be supplied to Docker Compose.

Current values:

```text
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0.2
OPENAI_MAX_OUTPUT_TOKENS=800
```

Why OpenAI is not hardcoded:

API keys are secrets. They should come from environment variables and should not be committed with real values.

## 4. Dockerfile Explained

File:

```text
Dockerfile
```

## 4.1 Base Image

```dockerfile
FROM python:3.12-slim
```

An image is a packaged filesystem and runtime template.

This line says:

```text
Build our backend image starting from the official Python 3.12 slim image.
```

## 4.2 Python Environment Variables

```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
```

Meaning:

- `PYTHONDONTWRITEBYTECODE=1`: avoids writing `.pyc` files.
- `PYTHONUNBUFFERED=1`: prints logs immediately.
- `PIP_NO_CACHE_DIR=1`: avoids keeping pip cache in the image.

These are common container-friendly Python settings.

## 4.3 Working Directory

```dockerfile
WORKDIR /app
```

This sets the current directory inside the image.

Commands after this run from `/app` unless changed later.

## 4.4 Installing Git

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
```

The backend needs Git because repository analysis runs `git clone`.

The cleanup step removes package manager metadata to keep the image smaller.

## 4.5 Installing Python Dependencies

```dockerfile
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt
```

Docker copies dependencies first so dependency installation can be cached between builds when only source code changes.

## 4.6 Copying Source Code

```dockerfile
COPY backend ./backend
```

This copies the backend application into the image.

## 4.7 Running FastAPI

```dockerfile
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`uvicorn` runs the FastAPI app.

`--host 0.0.0.0` is important in containers. It tells Uvicorn to listen on all network interfaces inside the container, allowing Docker port mapping to work.

## 5. docker-compose.yml Explained

File:

```text
docker-compose.yml
```

Docker Compose is used because this application needs more than one container.

Instead of manually starting FastAPI, PostgreSQL, and Neo4j one by one, Compose starts the whole stack from one file.

## 5.1 api Service

The `api` service builds from the local Dockerfile:

```yaml
api:
  build:
    context: .
    dockerfile: Dockerfile
```

It maps container port 8000 to host port 8000:

```yaml
ports:
  - "8000:8000"
```

This means:

```text
Host:      http://localhost:8000
Container: port 8000
```

The API service receives environment variables for:

- FastAPI app settings
- PostgreSQL connection
- OpenAI settings
- Neo4j connection

Important internal URLs:

```text
DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/repo_intelligence
NEO4J_URI=bolt://neo4j:7687
```

Inside Compose, `postgres` and `neo4j` are service names. The API container should not use `localhost` for these dependencies because `localhost` would mean the API container itself.

## 5.2 postgres Service

The PostgreSQL service uses:

```yaml
image: postgres:16-alpine
```

It creates:

```text
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=repo_intelligence
```

Port mapping:

```yaml
5432:5432
```

This lets local tools connect to PostgreSQL at:

```text
localhost:5432
```

The API container connects internally at:

```text
postgres:5432
```

## 5.3 neo4j Service

The Neo4j service uses:

```yaml
image: neo4j:5-community
```

Authentication:

```text
NEO4J_AUTH=neo4j/repo-intelligence-password
```

Ports:

```text
7474: Neo4j Browser HTTP UI
7687: Bolt driver protocol
```

You can open the browser UI at:

```text
http://localhost:7474
```

The API container connects internally at:

```text
bolt://neo4j:7687
```

## 6. Images Explained

An image is a blueprint for a container.

It contains:

- Operating system files
- Runtime dependencies
- Application dependencies
- Application code
- Default startup command

Examples in this project:

```text
repo-intelligence-api image built from Dockerfile
postgres:16-alpine image pulled from Docker Hub
neo4j:5-community image pulled from Docker Hub
```

Beginner mental model:

```text
Image = recipe or template
Container = running instance of that recipe
```

## 7. Containers Explained

A container is a running process created from an image.

In this project:

```text
repo-intelligence-api container runs FastAPI
repo-intelligence-postgres container runs PostgreSQL
repo-intelligence-neo4j container runs Neo4j
```

Containers are isolated from the host and from each other, but Docker Compose connects them through a shared network.

## 8. Volumes Explained

A volume stores data outside the container lifecycle.

This matters because containers can be deleted and recreated.

Without volumes:

```text
Delete PostgreSQL container -> lose database data
Delete Neo4j container -> lose graph data
```

With volumes:

```text
Delete container -> data remains in Docker volume
Start new container -> data is reused
```

Volumes in this project:

```text
postgres_data
neo4j_data
neo4j_logs
```

## 8.1 postgres_data

Stores PostgreSQL database files.

Mounted at:

```text
/var/lib/postgresql/data
```

## 8.2 neo4j_data

Stores Neo4j graph data.

Mounted at:

```text
/data
```

## 8.3 neo4j_logs

Stores Neo4j logs.

Mounted at:

```text
/logs
```

## 9. Networks Explained

A Docker network lets containers communicate.

This project defines:

```text
repo-intelligence-network
```

The API, PostgreSQL, and Neo4j containers all join this network.

Because they share a network, the API can use service names as hostnames:

```text
postgres
neo4j
```

Important beginner lesson:

Inside a container, `localhost` means that same container. It does not mean another service. For service-to-service communication in Compose, use the service name.

## 10. Healthchecks Explained

PostgreSQL and Neo4j have healthchecks.

Healthchecks tell Docker Compose whether a service is ready.

PostgreSQL healthcheck:

```text
pg_isready -U postgres -d repo_intelligence
```

Neo4j healthcheck:

```text
cypher-shell -u neo4j -p repo-intelligence-password 'RETURN 1'
```

The API uses `depends_on` with `condition: service_healthy` so it waits for PostgreSQL and Neo4j to become healthy before starting.

This reduces startup race conditions.

## 11. docker compose Explained

Docker Compose is a tool for defining and running multi-container applications.

Common commands:

```bash
docker compose up --build
```

Builds the API image if needed and starts all services.

```bash
docker compose up -d --build
```

Starts services in detached mode.

```bash
docker compose logs -f api
```

Streams API logs.

```bash
docker compose ps
```

Shows running service status.

```bash
docker compose down
```

Stops and removes containers and the network.

```bash
docker compose down -v
```

Stops containers and also removes volumes. This deletes PostgreSQL and Neo4j data.

Use `down -v` carefully.

## 12. Local Execution Flow

A typical local flow is:

```text
1. Set OPENAI_API_KEY in your shell or local environment.
2. Run docker compose up --build.
3. Docker builds the API image.
4. Docker pulls PostgreSQL and Neo4j images if needed.
5. PostgreSQL starts and creates repo_intelligence database.
6. Neo4j starts with configured credentials.
7. Healthchecks pass.
8. FastAPI starts.
9. FastAPI startup creates SQLAlchemy tables.
10. The API is available at http://localhost:8000.
```

Useful URLs:

```text
FastAPI health: http://localhost:8000/api/v1/health
FastAPI docs:   http://localhost:8000/docs
Neo4j Browser:  http://localhost:7474
```

Neo4j login:

```text
Username: neo4j
Password: repo-intelligence-password
```

## 13. How This Fits the Current Backend

The backend needs three external capabilities:

```text
PostgreSQL for repository and analysis report storage
Neo4j for repository graph storage and retrieval
OpenAI for summaries and QA answers
```

Docker Compose provides PostgreSQL and Neo4j locally.

OpenAI remains external because it is a hosted API. The API key is passed into the container as an environment variable.

## 14. Trade-Offs

## 14.1 Docker Compose vs Manual Setup

Manual setup means installing PostgreSQL and Neo4j directly on your machine.

Benefit:

- Fewer Docker concepts at first.

Cost:

- More machine-specific setup.
- Harder to reset.
- Harder for another developer to reproduce.

Docker Compose makes the environment repeatable.

## 14.2 Named Volumes vs Temporary Containers

Named volumes preserve database data.

Benefit:

- Data survives container recreation.

Cost:

- Schema changes can leave old data behind during early development.

When the schema changes and there are no migrations yet, you may need:

```bash
docker compose down -v
```

That resets the databases.

## 14.3 Slim Python Image

`python:3.12-slim` is smaller than the full image.

Benefit:

- Smaller image.
- Faster pulls.

Cost:

- Some system packages are missing by default.

That is why Git is explicitly installed.

## 14.4 Hardcoded Local Neo4j Password in Compose

The compose file uses a local development password:

```text
repo-intelligence-password
```

Benefit:

- Easy local setup.

Cost:

- Not appropriate for production.

Future production deployments should use secret management.

## 15. Interview Notes

## 15.1 What Is a Docker Image?

A Docker image is a packaged template containing everything needed to run software.

## 15.2 What Is a Container?

A container is a running instance of an image.

## 15.3 Why Use Docker Compose?

Docker Compose runs multiple related containers together from one YAML file.

It is useful when an app needs services like databases, queues, or graph stores.

## 15.4 Why Use Volumes?

Volumes persist data outside container lifecycles.

They are required for databases if you do not want data to disappear when containers are recreated.

## 15.5 Why Use Networks?

Networks let containers communicate using service names.

In this project, the API talks to `postgres` and `neo4j` through the Compose network.

## 15.6 Why Install Git in the API Image?

The backend clones repositories during analysis. That requires the `git` executable inside the container.

## 15.7 Why Not Put OpenAI in Docker Compose?

OpenAI is an external API, not a local container in this setup.

The backend calls OpenAI over the network using `OPENAI_API_KEY`.

## 16. Key Takeaways

- The app is now containerized with Docker.
- FastAPI runs in the `api` container.
- PostgreSQL runs in the `postgres` container.
- Neo4j runs in the `neo4j` container.
- Docker Compose coordinates the full local stack.
- Volumes preserve PostgreSQL and Neo4j data.
- A shared Docker network lets services communicate by name.
- Healthchecks help start services in a safer order.
- The API container includes Git because repository analysis needs `git clone`.
- OpenAI remains external and is configured through environment variables.

## 17. Recommended Next Phase

A strong next phase would improve production readiness.

Suggested next steps:

1. Add Alembic migrations instead of startup table creation.
2. Add a non-root container user.
3. Add test containers for integration tests.
4. Add Makefile or task commands for common Docker workflows.
5. Add separate compose overrides for development and production.
6. Move secrets into a secret manager for real deployments.
