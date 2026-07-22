# PostgreSQL and SQLAlchemy Integration Guide

## 1. Phase Goal

This phase adds PostgreSQL persistence to the Repository Intelligence Agent backend.

The backend now stores:

- Repository records
- Analysis reports

This means repository metadata no longer exists only in the HTTP response. After a repository is cloned and analyzed, the extracted metadata is saved through SQLAlchemy models and a CRUD layer.

This phase does **not** add Docker, Alembic migrations, authentication, background workers, Neo4j, or an LLM.

## 2. What Was Implemented

New database architecture:

```text
Route
  -> Service
  -> CRUD Layer
  -> SQLAlchemy ORM Models
  -> PostgreSQL
```

Updated request flow:

```text
POST /api/v1/repository
  -> validate request with Pydantic
  -> clone repository
  -> extract metadata
  -> store Repository
  -> store AnalysisReport
  -> return stored IDs and metadata
```

The API response now includes:

```text
repository_id
analysis_report_id
metadata
```

The IDs come from the database models, not from a mock string.

The important architecture shift in this phase is persistence. The system no longer treats repository analysis as a one-time calculation that disappears after the HTTP response. It now records the submitted repository and the analysis result in PostgreSQL through a clear database boundary.

## 2.1 Architecture Diagram

```text
Client
  |
  v
FastAPI Route
  |
  | receives RepositoryCreateRequest
  | receives database session from Depends(get_db)
  v
RepositoryService
  |
  | clones repository
  | extracts metadata
  | converts Pydantic metadata to JSON-safe dictionaries
  v
RepositoryCRUD
  |
  | gets or creates Repository row
  | creates AnalysisReport row
  | commits transaction
  v
SQLAlchemy ORM Models
  |
  v
PostgreSQL Tables
```

This keeps responsibilities separated:

- The route owns HTTP concerns.
- The service owns the use case workflow.
- The CRUD layer owns database operations.
- The models describe database tables.
- PostgreSQL stores durable application state.

## 3. Files Created or Updated

```text
.env.example                                      updated
requirements.txt                                  updated

backend/app/main.py                               updated
backend/app/core/config.py                        updated
backend/app/api/v1/routes/repository.py           updated
backend/app/schemas/repository.py                 updated
backend/app/services/repository_service.py        updated

backend/app/db/__init__.py                        created
backend/app/db/base.py                            created
backend/app/db/session.py                         created

backend/app/models/__init__.py                    created
backend/app/models/repository.py                  created
backend/app/models/analysis_report.py             created

backend/app/crud/__init__.py                      created
backend/app/crud/repository_crud.py               created

docs/postgresql-sqlalchemy-integration.md         created
```

## 4. Why PostgreSQL Now?

Before this phase, the backend recalculated repository metadata on every request and returned it immediately.

That is useful for learning the extraction flow, but it is not enough for a real system.

A Repository Intelligence Agent needs persistence because:

- Cloning repositories can be slow.
- Directory traversal can be expensive for large projects.
- Technology detection should not repeat unnecessarily.
- Users need to view previous analysis results.
- Future features need historical reports.
- Background jobs need durable state.
- The frontend needs stable IDs to request existing data.

PostgreSQL gives the backend a durable source of truth.

## 5. Why SQLAlchemy?

SQLAlchemy is a popular Python database toolkit and ORM.

It is useful because it provides:

- Python classes that map to database tables.
- Database sessions for controlled reads and writes.
- Query building without writing raw SQL everywhere.
- Connection pooling.
- Transaction handling.
- Support for PostgreSQL and many other databases.
- A clean path toward migrations with Alembic later.

SQLAlchemy lets the app work with Python objects while still storing data in PostgreSQL.

## 6. What ORM Means

ORM means **Object-Relational Mapping**.

Relational databases store data in tables:

```text
repositories
analysis_reports
```

Python applications usually work with objects:

```text
Repository
AnalysisReport
```

An ORM maps between those two worlds.

Example mental model:

```text
Python class       Database table
Repository     ->  repositories
AnalysisReport ->  analysis_reports

Python attribute   Database column
repo_url       ->  repo_url
file_count     ->  file_count
created_at     ->  created_at
```

With an ORM, the backend can create a `Repository` object and SQLAlchemy knows how to insert it into the `repositories` table.

## 6.1 ORM vs Raw SQL

Without an ORM, the application might build SQL statements manually, such as:

```text
INSERT INTO repositories (id, repo_url) VALUES (...)
```

With SQLAlchemy ORM, the application creates model objects and lets SQLAlchemy translate them into SQL.

This does not mean SQL disappears. SQLAlchemy still talks to PostgreSQL using SQL under the hood. The benefit is that most application code can work with Python objects while SQLAlchemy handles repetitive database mapping details.

Raw SQL can still be useful for advanced reporting or highly optimized queries later. For this phase, ORM models are clearer and more beginner-friendly.

## 6.2 Core SQLAlchemy Terms

Important terms introduced in this phase:

- **Engine**: the object that manages database connectivity.
- **Session**: a unit of work used to query, add, commit, rollback, and close database interactions.
- **Model**: a Python class mapped to a database table.
- **Column**: a database field mapped to a Python attribute.
- **Relationship**: an ORM connection between two models.
- **Foreign key**: a database constraint that links a row in one table to a row in another table.
- **Transaction**: a group of database operations that succeed or fail together.

## 7. Database Configuration

File:

```text
backend/app/core/config.py
```

New setting:

```text
DATABASE_URL
```

Default value:

```text
postgresql+psycopg://postgres:postgres@localhost:5432/repo_intelligence
```

This connection string means:

- Use PostgreSQL.
- Use the `psycopg` driver.
- Username is `postgres`.
- Password is `postgres`.
- Host is `localhost`.
- Port is `5432`.
- Database name is `repo_intelligence`.

Why this belongs in configuration:

The database URL changes between local development, testing, staging, and production. It should not be hardcoded inside services or models.

## 8. Environment Template

File:

```text
.env.example
```

New variable:

```text
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/repo_intelligence
```

Why it exists:

`.env.example` teaches developers which environment variables the app expects without committing real secrets.

## 9. Dependencies

File:

```text
requirements.txt
```

New dependencies:

```text
sqlalchemy
psycopg[binary]
```

`sqlalchemy` provides the ORM and database toolkit.

`psycopg[binary]` provides the PostgreSQL driver used by SQLAlchemy to communicate with PostgreSQL.

## 10. Database Base

File:

```text
backend/app/db/base.py
```

Purpose:

- Defines the shared SQLAlchemy declarative base.

The base class is what ORM models inherit from.

Why it matters:

SQLAlchemy uses this base to collect metadata about all tables. That metadata is later used to create database tables.

## 11. Database Session

File:

```text
backend/app/db/session.py
```

Purpose:

- Creates the SQLAlchemy engine.
- Creates the session factory.
- Provides a FastAPI database dependency.
- Creates tables during startup for this beginner phase.

Important pieces:

```text
engine
SessionLocal
get_db()
create_database_tables()
```

## 11.1 engine

The engine manages database connectivity.

It knows where PostgreSQL is and how to connect to it.

## 11.2 SessionLocal

`SessionLocal` creates database sessions.

A session represents a conversation with the database.

The backend uses sessions to:

- Query records.
- Add records.
- Commit changes.
- Roll back failed work.
- Close connections after use.

## 11.3 get_db()

`get_db()` is a FastAPI dependency.

It opens a database session for one request and closes it when the request finishes.

This pattern prevents routes from manually opening and closing database connections.

Request-level session lifecycle:

```text
1. Request enters FastAPI.
2. FastAPI sees db: Session = Depends(get_db).
3. get_db() creates a SessionLocal instance.
4. The route receives the session.
5. The route passes the session to RepositoryService.
6. The service passes the session to RepositoryCRUD.
7. CRUD performs database work.
8. Request finishes.
9. get_db() closes the session.
```

The route does not need to know how the session is created. It only declares that it needs one.

## 11.4 create_database_tables()

This function creates tables from SQLAlchemy models.

It is called when the FastAPI app starts.

Trade-off:

This is beginner-friendly, but production systems usually use Alembic migrations instead of automatic table creation.

## 12. FastAPI Startup Hook

File:

```text
backend/app/main.py
```

What changed:

- The app now calls `create_database_tables()` during startup.

Why it exists:

The database tables need to exist before the API can store repositories and analysis reports.

Beginner note:

If PostgreSQL is not running or the database does not exist, app startup can fail. That is expected until a local PostgreSQL instance is configured.

## 13. Repository Model

File:

```text
backend/app/models/repository.py
```

Purpose:

- Represents the `repositories` table.

Fields:

- `id`: unique repository ID.
- `repo_url`: submitted repository URL.
- `created_at`: when the record was created.
- `updated_at`: when the record was last updated.

Table mapping:

```text
Repository model
  -> repositories table

id
  -> String(36), primary key, generated with uuid4

repo_url
  -> String(2048), unique, indexed, required

created_at
  -> DateTime, required, default current UTC time

updated_at
  -> DateTime, required, updates when the row changes
```

Why `repo_url` is unique:

The same repository URL should map to one repository record. If the same URL is submitted again, the backend reuses the existing repository row and creates a new analysis report for that repository.

Why `repo_url` is indexed:

The CRUD layer looks up repositories by URL. An index helps PostgreSQL find matching rows faster as the table grows.

Relationship:

```text
Repository has many AnalysisReport records.
```

The relationship is expressed in Python as:

```text
analysis_reports
```

That means a `Repository` object can be connected to a list of related `AnalysisReport` objects.

The cascade rule means that if a repository row is deleted through the ORM, its related analysis reports are also deleted. This prevents orphaned reports with no repository.

Why this matters:

One repository can be analyzed many times over its lifetime.

For example:

- Initial analysis
- Re-analysis after new commits
- Re-analysis after detection logic improves

## 14. AnalysisReport Model

File:

```text
backend/app/models/analysis_report.py
```

Purpose:

- Represents the `analysis_reports` table.

Fields:

- `id`: unique analysis report ID.
- `repository_id`: foreign key to the repository.
- `status`: analysis status, currently `analyzed`.
- `file_count`: number of visible files.
- `directory_count`: number of visible directories.
- `ignored_directories`: stored JSON list.
- `technologies`: stored JSON list.
- `directory_structure`: stored JSON tree.
- `created_at`: when the report was created.

Table mapping:

```text
AnalysisReport model
  -> analysis_reports table

id
  -> String(36), primary key, generated with uuid4

repository_id
  -> ForeignKey("repositories.id"), indexed, required

status
  -> String(50), required

file_count
  -> Integer, required

directory_count
  -> Integer, required

ignored_directories
  -> JSON, required

technologies
  -> JSON, required

directory_structure
  -> JSON, required

created_at
  -> DateTime, required, default current UTC time
```

Why JSON columns are used:

The technology list and directory tree are nested structures. In this early phase, storing them as JSON keeps the design simple and avoids creating many extra tables before the access patterns are known.

Later, if the backend needs advanced querying over individual files, symbols, or relationships, the design can evolve. PostgreSQL can continue storing report summaries, while Neo4j can store deep code relationships.

Relationship:

```text
AnalysisReport belongs to one Repository.
```

This relationship is enforced by `repository_id`, which points back to `repositories.id`.

Why store analysis reports separately:

The repository is the stable entity. An analysis report is a result generated at a point in time.

Separating them makes history possible.

## 15. CRUD Layer

File:

```text
backend/app/crud/repository_crud.py
```

CRUD means:

```text
Create
Read
Update
Delete
```

This file contains database operations for repositories and analysis reports.

Why CRUD exists as its own layer:

The service should not be full of SQLAlchemy query details. The service should describe the workflow. The CRUD layer should describe the persistence operations.

This makes both files easier to understand:

```text
RepositoryService
  -> What should happen?

RepositoryCRUD
  -> How is it stored or loaded?
```

## 15.1 get_repository_by_url()

Purpose:

- Finds an existing repository record by URL.

Why it exists:

If the same repository is submitted again, the backend should reuse the existing repository row instead of creating duplicates.

## 15.2 get_or_create_repository()

Purpose:

- Returns an existing repository if it exists.
- Creates a new repository if it does not exist.

Why it exists:

This keeps the service focused on workflow while the CRUD layer handles database details.

## 15.3 create_analysis_report()

Purpose:

- Creates a new analysis report for a repository.

Why it exists:

Every analysis run should produce its own report. This makes it possible to track analysis history later.

## 15.4 save_repository_analysis()

Purpose:

- Coordinates the full persistence operation.

What it does:

```text
1. Get or create Repository.
2. Create AnalysisReport.
3. Commit the transaction.
4. Refresh database objects.
5. Return stored objects.
```

Why commit happens here:

The CRUD function owns the database write operation for this use case. It commits after both records are prepared so repository and report persistence succeed together.

Transaction concept:

```text
Start unit of work
  -> get or create repository
  -> create analysis report
  -> commit
End unit of work
```

If committing fails, the service catches `SQLAlchemyError`, rolls back the database session, and raises `RepositoryServiceError`.

This matters because partial writes are dangerous. The backend should not pretend analysis was stored if only half of the database work succeeded.

## 16. RepositoryService Updates

File:

```text
backend/app/services/repository_service.py
```

The service now receives a SQLAlchemy `Session` from the route.

Updated flow:

```text
1. Clone repository.
2. Extract metadata.
3. Serialize Pydantic metadata for JSON storage.
4. Save Repository and AnalysisReport through CRUD.
5. Return response with database IDs.
```

Important functions introduced or changed:

## 16.1 __init__()

Purpose:

- Creates or receives a `RepositoryCRUD` instance.

Why it exists:

This makes the service easier to test later because a fake CRUD object could be injected.

## 16.2 create_repository()

Purpose:

- Coordinates cloning, metadata extraction, persistence, and response creation.

What changed:

- It now accepts `db`.
- It now saves data through the CRUD layer.
- It now returns the stored `repository_id` and `analysis_report_id`.

## 16.3 serialize_model_list()

Purpose:

- Converts Pydantic models into plain dictionaries for JSON database storage.

Why it exists:

SQLAlchemy JSON columns store JSON-compatible data such as dictionaries and lists. Pydantic models need to be converted first.

Why this function checks for `model_dump()`:

Pydantic v2 uses `model_dump()` as the preferred way to turn a model into a dictionary. Older Pydantic versions used `.dict()`. The helper supports both shapes, which makes the service a little more tolerant while the project is still evolving.

Example transformation:

```text
TechnologyDetection(name="FastAPI", category="backend framework", source="requirements.txt")

becomes

{
  "name": "FastAPI",
  "category": "backend framework",
  "source": "requirements.txt"
}
```

That dictionary can be stored inside a SQLAlchemy JSON column.

## 16.4 Database Error Handling

The service catches `SQLAlchemyError` around the persistence call.

Current behavior:

```text
1. Something goes wrong while saving.
2. The service rolls back the database session.
3. The service raises RepositoryServiceError.
4. The route converts that service error into an HTTP response.
```

This keeps database-specific exceptions from leaking directly into the API response.

The beginner-friendly lesson:

Services can know that persistence failed, but clients should receive a controlled API error instead of raw database internals.

## 17. Repository Route Updates

File:

```text
backend/app/api/v1/routes/repository.py
```

What changed:

- The route now depends on `get_db()`.
- FastAPI injects a database session into the route.
- The route passes the session to the service.

The route still does not contain business logic.

It handles:

- HTTP path
- Request model
- Response model
- Status code
- Database dependency injection
- HTTP error translation

The service handles repository workflow.

The CRUD layer handles database operations.

Dependency injection line to understand:

```text
db: Session = Depends(get_db)
```

This tells FastAPI:

```text
Before calling this route, run get_db() and give the route its yielded database session.
```

Dependency injection is useful because routes can declare what they need without manually constructing every dependency.

## 18. Response Schema Update

File:

```text
backend/app/schemas/repository.py
```

What changed:

- `RepositoryCreateResponse` now includes `analysis_report_id`.

Why it matters:

The client can now refer to a specific stored analysis report.

This will become useful when the backend adds endpoints such as:

```text
GET /api/v1/repositories/{repository_id}
GET /api/v1/analysis-reports/{analysis_report_id}
```

Response model responsibility:

The database models are not returned directly from the route. The route returns `RepositoryCreateResponse`, which is a Pydantic schema.

This is important because database models and API responses have different responsibilities:

```text
SQLAlchemy model
  -> persistence shape

Pydantic schema
  -> API contract shape
```

Keeping them separate prevents the API from accidentally exposing internal database details.

## 19. Why Store Repositories Instead of Recalculating Every Request?

Recalculating every request is simple, but it does not scale.

Problems with recalculating:

- Cloning is slow.
- Large repositories can take time to traverse.
- Repeated requests waste CPU, disk, and network.
- Users cannot view previous results.
- The frontend has no stable IDs for navigation.
- Future background jobs need durable state.
- Future LLM workflows need stored repository context.

Benefits of storing:

- Faster future reads.
- Analysis history.
- Stable repository IDs.
- Stable report IDs.
- Better user experience.
- Lower repeated compute cost.
- Foundation for dashboards and async jobs.

The key backend lesson:

Expensive deterministic work should usually be stored once and reused, especially when users may ask for it again.

## 20. Trade-Offs

## 20.1 Automatic Table Creation vs Migrations

Current approach:

- `Base.metadata.create_all()` creates tables on startup.

Benefit:

- Beginner-friendly.
- No migration tool needed yet.
- Easy to see models become tables.

Cost:

- Not enough for production schema evolution.
- Does not manage complex changes safely.

Future direction:

Add Alembic migrations.

## 20.2 JSON Columns for Metadata

Current approach:

- Store technologies and directory structure as JSON.

Benefit:

- Simple and flexible.
- Good for early metadata storage.
- Avoids over-modeling nested structures too soon.

Cost:

- Harder to query deeply than normalized tables.
- Large directory trees can make rows heavy.

Future direction:

Keep summary metadata in PostgreSQL and move code relationships to Neo4j later.

## 20.3 Synchronous Analysis and Persistence

Current approach:

- API request clones, analyzes, and stores before responding.

Benefit:

- Easy to understand.
- No background worker needed yet.

Cost:

- Slow requests for large repositories.
- Harder to scale under traffic.

Future direction:

Move clone and analysis work into a background job.

## 20.4 Unique Repository URL

Current approach:

- `repo_url` is unique.

Benefit:

- Prevents duplicate repository rows for the same URL.

Cost:

- URL normalization may need improvement later.
- Different URL formats could point to the same repository.

Future direction:

Normalize GitHub owner/name and store them separately.

## 21. Interview Notes

## 21.1 Why Use SQLAlchemy?

SQLAlchemy gives Python applications a mature way to work with relational databases while keeping database access organized and testable.

## 21.2 What Is an ORM?

An ORM maps Python classes to database tables and Python objects to database rows.

It lets developers work with objects while still storing data relationally.

## 21.3 Why Add a CRUD Layer?

The CRUD layer keeps SQLAlchemy queries out of routes and services.

Routes should handle HTTP.

Services should coordinate use cases.

CRUD should handle database operations.

## 21.4 Why Store Analysis Reports?

Analysis reports are outputs of repository analysis.

Storing them avoids repeated work and creates a history of what the system discovered.

## 21.5 Why Separate Repository and AnalysisReport?

A repository is the thing being analyzed.

An analysis report is the result of one analysis run.

One repository can have many analysis reports.

## 21.6 What Is a Database Session?

A database session is a unit of work with the database.

It tracks objects, performs queries, commits changes, rolls back failures, and closes resources.

## 22. Key Takeaways

- PostgreSQL persistence was added through SQLAlchemy.
- `Repository` stores the submitted repository URL.
- `AnalysisReport` stores extracted metadata.
- The database session is injected into the route with FastAPI dependencies.
- The service coordinates analysis and persistence.
- The CRUD layer owns database operations.
- SQLAlchemy ORM maps Python classes to database tables.
- Stored reports prevent expensive recalculation on every request.
- This is still a learning-phase implementation; migrations should come later.

## 23. Recommended Next Phase

The next phase should add database migrations and retrieval endpoints.

Suggested next steps:

1. Add Alembic.
2. Generate an initial migration for repositories and analysis reports.
3. Add `GET /repositories/{repository_id}`.
4. Add `GET /analysis-reports/{analysis_report_id}`.
5. Add tests with a temporary test database.
