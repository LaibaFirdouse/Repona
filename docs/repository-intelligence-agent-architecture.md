# Repository Intelligence Agent Architecture Guide

## 1. What We Are Building

We are designing a **Repository Intelligence Agent** from scratch.

The system will eventually allow a user to connect or upload a software repository and ask questions such as:

- What does this repository do?
- Where is authentication implemented?
- Which files are most important?
- How does data flow through the backend?
- What modules are risky or tightly coupled?
- What changed between two versions?
- Which tests cover this feature?

At this phase, we are **not writing application code**. We are designing the backend architecture so the project can grow cleanly while remaining beginner-friendly.

The long-term goal is to learn:

- Backend architecture
- FastAPI
- AI agent design
- Production-quality engineering practices
- Modular and scalable system design

## 2. High-Level Architecture

The backend should be organized as a layered, modular system.

A good mental model is:

```text
Frontend
   |
   v
FastAPI API Layer
   |
   v
Application / Use Case Layer
   |
   v
Domain Layer
   |
   v
Infrastructure Layer
   |
   +--> PostgreSQL
   +--> Neo4j
   +--> File storage
   +--> LLM provider
   +--> Background workers
```

Each layer has a specific responsibility. This separation makes the project easier to understand, test, modify, and scale.

The most important rule is:

**Business decisions should not be trapped inside web routes, database queries, or LLM calls.**

Instead, the system should place each responsibility in the layer where it belongs.

## 3. Architectural Goals

### 3.1 Beginner-Friendly

The architecture should be easy to learn from. That means:

- Folder names should be clear.
- Each layer should have one obvious job.
- Code should be grouped by responsibility, not randomly by file type.
- New developers should be able to trace a request from API route to response.

### 3.2 Modular

The system should be split into modules that can evolve independently.

Examples of future modules:

- Repository ingestion
- Code parsing
- Embedding generation
- Graph construction
- Question answering
- Agent orchestration
- User authentication
- Billing or usage tracking

### 3.3 Scalable

The first version can be simple, but the design should allow future growth.

Scalability includes:

- Adding background jobs later
- Moving heavy processing out of API requests
- Supporting multiple database systems
- Replacing one LLM provider with another
- Adding observability, caching, and rate limiting

### 3.4 Production-Oriented

Even while learning, the project should introduce production habits early:

- Clear boundaries
- Configuration management
- Dependency injection
- Error handling
- Logging
- Testing strategy
- Database migrations
- API versioning
- Docker-based local development

## 4. Responsibility of Every Layer

## 4.1 Frontend Layer

The frontend is the user-facing application.

Responsibilities:

- Display forms, repository lists, chat panels, and analysis results.
- Send HTTP requests to the backend.
- Show loading states while repository analysis or agent responses are running.
- Render errors in a user-friendly way.
- Keep user interaction smooth.

The frontend should not know how repository analysis works internally. It should only know which backend endpoints are available and what data they expect.

Example frontend actions:

- Submit a repository URL.
- Ask a question about a repository.
- View repository analysis status.
- Browse discovered files, modules, or relationships.

## 4.2 API Layer

The API layer is implemented with FastAPI.

Responsibilities:

- Define HTTP endpoints.
- Validate incoming request bodies, query parameters, and path parameters.
- Convert HTTP requests into application use case calls.
- Convert application results into HTTP responses.
- Handle authentication and authorization checks at the boundary.
- Return consistent errors.

The API layer should be thin.

It should not contain heavy business logic such as:

- How to clone a repository
- How to parse source code
- How to build a graph
- How to decide which LLM prompt to use
- How to store analysis results

Those decisions belong deeper in the system.

## 4.3 Schema / DTO Layer

Schemas define the shape of data entering and leaving the API.

In a FastAPI project, these are usually Pydantic models.

Responsibilities:

- Validate request data.
- Document API inputs and outputs.
- Prevent internal domain objects from leaking directly into HTTP responses.
- Provide clear contracts between frontend and backend.

DTO means **Data Transfer Object**. A DTO is a simple object designed to carry data across a boundary.

Example boundaries:

- Frontend to backend
- API layer to application layer
- Application layer to external service

Schemas help beginners because they make the API contract visible and explicit.

## 4.4 Application / Use Case Layer

The application layer coordinates work.

Responsibilities:

- Represent user actions as use cases.
- Coordinate domain services, repositories, infrastructure adapters, and external services.
- Enforce workflow rules.
- Manage transaction boundaries when needed.
- Decide what happens first, second, and third.

Example use cases:

- Submit repository for analysis.
- Get repository analysis status.
- Ask a question about a repository.
- Retrieve file dependency graph.
- Re-run analysis after repository changes.

This layer answers the question:

**What does the system do when the user performs this action?**

It does not need to know low-level details such as the exact SQL query or the HTTP format of an LLM provider.

## 4.5 Domain Layer

The domain layer contains the core business concepts.

Responsibilities:

- Define the important concepts in the system.
- Express domain rules.
- Keep business logic independent from frameworks.
- Provide stable models and services that are not tied to FastAPI, PostgreSQL, Neo4j, or any LLM vendor.

Important domain concepts may include:

- Repository
- Commit
- Branch
- File
- Symbol
- Module
- Dependency
- CodeChunk
- AnalysisJob
- AgentRun
- Question
- Answer

The domain layer should be boring in the best way: stable, clear, and mostly independent from technology choices.

## 4.6 Infrastructure Layer

The infrastructure layer talks to the outside world.

Responsibilities:

- Database access
- Graph database access
- File system operations
- Git operations
- LLM provider calls
- Vector database calls if added later
- Message broker or queue integration
- External API clients

This layer contains implementation details.

For example, the application layer may say:

**Save this repository analysis result.**

The infrastructure layer decides how that happens in PostgreSQL, Neo4j, or object storage.

## 4.7 Persistence Layer

Persistence is a part of infrastructure focused on storing and retrieving data.

Responsibilities:

- Store users, repositories, jobs, and metadata in PostgreSQL.
- Store repository relationships in Neo4j.
- Manage database sessions and transactions.
- Run migrations.
- Provide repository classes that hide database details from the application layer.

The word **repository** can be confusing here because our product also analyzes software repositories.

In backend architecture, a **repository class** is a data access abstraction. It hides database details behind methods that the application layer can call.

Example conceptually:

- RepositoryAnalysisStore saves analysis metadata.
- CodeGraphStore saves file and dependency relationships.
- UserStore loads user records.

## 4.8 Agent Layer

The agent layer handles AI reasoning workflows.

Responsibilities:

- Decide which tools the AI agent can use.
- Build prompts from repository context.
- Retrieve relevant code chunks or graph nodes.
- Coordinate multi-step reasoning.
- Call the LLM through an abstraction.
- Track agent runs, intermediate steps, and final answers.

The agent layer should not directly depend on a single LLM vendor everywhere. Instead, it should use an interface so the project can later support providers such as OpenAI, Azure OpenAI, Anthropic, local models, or others.

The agent layer is where concepts like tools, memory, retrieval, planning, and execution will eventually live.

## 4.9 Background Worker Layer

Repository analysis can be slow.

Tasks like cloning a repository, parsing thousands of files, generating embeddings, and building a graph should not block an HTTP request.

Responsibilities:

- Run long tasks outside the API request cycle.
- Process queued jobs.
- Update job status.
- Retry failed work.
- Support scheduled or asynchronous processing.

In the first learning version, background jobs can be simple. Later, the project may use Celery, RQ, Dramatiq, Arq, or another worker system.

## 4.10 Configuration Layer

Configuration controls environment-specific values.

Responsibilities:

- Read environment variables.
- Store database URLs.
- Store LLM provider settings.
- Store logging levels.
- Store feature flags.
- Keep secrets out of source code.

Examples of configuration values:

- App environment: local, staging, production
- PostgreSQL connection URL
- Neo4j connection URL
- LLM API base URL
- LLM model name
- Max repository size
- Allowed file extensions

## 4.11 Observability Layer

Observability helps developers understand what the system is doing.

Responsibilities:

- Logging
- Metrics
- Tracing
- Error reporting
- Request IDs
- Timing slow operations

For an AI-backed system, observability is especially important because LLM calls can be slow, expensive, and unpredictable.

Important things to observe later:

- Request duration
- Token usage
- LLM latency
- Failed repository analysis jobs
- Number of files parsed
- Graph build duration
- Retrieval quality

## 5. Proposed Folder Structure

The project should eventually use a structure similar to this:

```text
repo-intelligence-agent/
  README.md
  docs/
    repository-intelligence-agent-architecture.md
    api-design.md
    database-design.md
    agent-design.md
    deployment-guide.md

  backend/
    app/
      main.py

      api/
        v1/
          routes/
          dependencies/

      core/
        config/
        logging/
        security/
        errors/

      schemas/
        requests/
        responses/

      application/
        use_cases/
        services/
        commands/
        queries/

      domain/
        models/
        services/
        policies/
        events/

      infrastructure/
        persistence/
          postgres/
          neo4j/
        llm/
        git/
        filesystem/
        messaging/

      agents/
        tools/
        prompts/
        workflows/
        memory/

      workers/
        jobs/
        tasks/

      tests/
        unit/
        integration/
        contract/

    migrations/
    scripts/

  frontend/
    src/

  docker/
    postgres/
    neo4j/

  docker-compose.yml
  .env.example
```

This is a design structure, not generated application code.

## 6. Why Each Folder Exists

## 6.1 docs/

Stores long-form learning and architecture documentation.

Why it exists:

- Keeps architectural decisions visible.
- Helps beginners review concepts later.
- Gives future contributors context.
- Separates explanation from implementation.

Suggested future documents:

- API design guide
- Database design guide
- Agent design guide
- Deployment guide
- Testing strategy

## 6.2 backend/

Contains the backend application.

Why it exists:

- Keeps backend code separate from frontend code.
- Makes deployment boundaries clearer.
- Allows independent backend tooling, tests, and dependencies.

## 6.3 backend/app/

Contains the FastAPI application package.

Why it exists:

- Groups all backend runtime code under one importable application package.
- Keeps the project organized as it grows.

## 6.4 backend/app/main.py

The future FastAPI entry point.

Why it exists:

- Creates the FastAPI app instance.
- Registers routers.
- Configures middleware.
- Starts application-level setup.

This file should stay small. If it grows too large, responsibilities should move into dedicated modules.

## 6.5 backend/app/api/

Contains HTTP API definitions.

Why it exists:

- Groups route handlers by API version and feature area.
- Keeps web concerns separate from business logic.
- Makes API versioning easier.

## 6.6 backend/app/api/v1/

Contains version 1 of the API.

Why it exists:

- Allows future breaking changes in v2 without breaking existing clients.
- Makes API evolution explicit.

## 6.7 backend/app/api/v1/routes/

Contains endpoint route modules.

Why it exists:

- Keeps route handlers grouped by feature.
- Makes it easy to find endpoints.

Possible future route groups:

- repositories
- analysis_jobs
- questions
- graphs
- health

## 6.8 backend/app/api/v1/dependencies/

Contains FastAPI dependencies.

Why it exists:

- Centralizes dependency injection logic.
- Helps route handlers remain small.
- Provides database sessions, authenticated users, services, or configuration objects.

## 6.9 backend/app/core/

Contains cross-cutting backend concerns.

Why it exists:

- Centralizes foundational behavior used across the app.
- Avoids duplicating setup code in feature modules.

## 6.10 backend/app/core/config/

Contains configuration loading.

Why it exists:

- Keeps environment variables organized.
- Prevents secrets from being hardcoded.
- Makes local, staging, and production settings easier to manage.

## 6.11 backend/app/core/logging/

Contains logging setup.

Why it exists:

- Ensures consistent logs across the app.
- Makes debugging easier.
- Supports structured logs later.

## 6.12 backend/app/core/security/

Contains security-related helpers.

Why it exists:

- Centralizes authentication and authorization logic.
- Avoids scattering token parsing and permission checks across routes.

## 6.13 backend/app/core/errors/

Contains shared error definitions and error handling.

Why it exists:

- Makes error responses consistent.
- Separates business errors from HTTP formatting.
- Helps frontend developers handle failures reliably.

## 6.14 backend/app/schemas/

Contains API schemas and DTOs.

Why it exists:

- Defines request and response shapes.
- Improves API documentation.
- Keeps external contracts separate from internal domain models.

## 6.15 backend/app/application/

Contains use cases and application services.

Why it exists:

- Coordinates workflows.
- Keeps route handlers thin.
- Provides a clear place for user-facing actions.

This is one of the most important folders for learning backend architecture.

## 6.16 backend/app/application/use_cases/

Contains one class or function per user action.

Why it exists:

- Makes behavior easy to find.
- Encourages small, focused workflows.
- Improves testability.

Example future use cases:

- SubmitRepositoryForAnalysis
- GetAnalysisStatus
- AskRepositoryQuestion
- BuildRepositoryGraph

## 6.17 backend/app/application/services/

Contains application-level coordination services.

Why it exists:

- Shares workflow logic across multiple use cases when needed.
- Prevents duplication.
- Keeps orchestration separate from domain rules.

## 6.18 backend/app/application/commands/

Contains write-oriented request objects.

Why it exists:

- Represents operations that change system state.
- Supports a clear command/query separation later.

Example commands:

- SubmitRepositoryCommand
- StartAnalysisCommand
- AskQuestionCommand

## 6.19 backend/app/application/queries/

Contains read-oriented request objects.

Why it exists:

- Represents operations that fetch data without changing state.
- Makes read workflows explicit.

Example queries:

- GetRepositoryQuery
- ListAnalysisJobsQuery
- GetGraphQuery

## 6.20 backend/app/domain/

Contains core business concepts.

Why it exists:

- Protects important logic from framework and infrastructure changes.
- Keeps business rules stable.
- Makes the system easier to reason about.

## 6.21 backend/app/domain/models/

Contains domain entities and value objects.

Why it exists:

- Represents meaningful concepts in the repository intelligence domain.
- Keeps the vocabulary of the system explicit.

## 6.22 backend/app/domain/services/

Contains domain services.

Why it exists:

- Holds domain logic that does not naturally belong to one model.
- Keeps business rules independent from databases and APIs.

## 6.23 backend/app/domain/policies/

Contains business rules and decision policies.

Why it exists:

- Makes rules explicit and testable.
- Keeps conditional decisions out of route handlers.

Example future policies:

- RepositorySizePolicy
- FileInclusionPolicy
- AnalysisRetryPolicy

## 6.24 backend/app/domain/events/

Contains domain events.

Why it exists:

- Allows the system to record important things that happened.
- Helps decouple workflows later.

Example events:

- RepositorySubmitted
- AnalysisCompleted
- AgentAnswerGenerated

## 6.25 backend/app/infrastructure/

Contains adapters to external systems.

Why it exists:

- Keeps technology-specific details out of the domain and application layers.
- Makes external dependencies replaceable.

## 6.26 backend/app/infrastructure/persistence/

Contains database-related implementation.

Why it exists:

- Separates persistence from business workflows.
- Gives PostgreSQL and Neo4j their own implementation areas.

## 6.27 backend/app/infrastructure/persistence/postgres/

Contains PostgreSQL-specific persistence code.

Why it exists:

- Stores relational data such as users, repositories, jobs, and audit records.
- Keeps SQLAlchemy models, database sessions, and relational repositories grouped together.

## 6.28 backend/app/infrastructure/persistence/neo4j/

Contains Neo4j-specific persistence code.

Why it exists:

- Stores graph relationships between files, symbols, modules, and dependencies.
- Keeps graph queries separate from relational database logic.

## 6.29 backend/app/infrastructure/llm/

Contains LLM provider adapters.

Why it exists:

- Centralizes calls to external AI models.
- Makes it easier to switch providers.
- Keeps prompts and model calls out of route handlers.

## 6.30 backend/app/infrastructure/git/

Contains Git-related operations.

Why it exists:

- Cloning, fetching, and checking out repositories are infrastructure concerns.
- Git details should not leak into business workflows.

## 6.31 backend/app/infrastructure/filesystem/

Contains file system access.

Why it exists:

- Reading repository files is an external system interaction.
- Centralizing file access makes it easier to add security checks and limits.

## 6.32 backend/app/infrastructure/messaging/

Contains queue or message broker integration.

Why it exists:

- Allows background workers to receive jobs.
- Keeps queue technology replaceable.

## 6.33 backend/app/agents/

Contains AI agent behavior.

Why it exists:

- Keeps agent workflows separate from generic application services.
- Provides a dedicated home for prompts, tools, memory, and reasoning flows.

## 6.34 backend/app/agents/tools/

Contains tools the agent can use.

Why it exists:

- Defines safe actions available to the agent.
- Allows the agent to query repository metadata, code chunks, graph data, or previous analysis.

## 6.35 backend/app/agents/prompts/

Contains prompt templates.

Why it exists:

- Makes prompts reviewable and versionable.
- Keeps prompt design separate from Python logic.

## 6.36 backend/app/agents/workflows/

Contains multi-step agent workflows.

Why it exists:

- Defines how the agent retrieves context, reasons, calls tools, and produces answers.
- Keeps AI orchestration understandable.

## 6.37 backend/app/agents/memory/

Contains agent memory abstractions.

Why it exists:

- Supports storing previous interactions, repository summaries, and useful context.
- Keeps memory strategy explicit instead of hidden inside prompt strings.

## 6.38 backend/app/workers/

Contains background worker logic.

Why it exists:

- Keeps slow tasks out of HTTP request handlers.
- Supports scalable processing of repository analysis jobs.

## 6.39 backend/app/workers/jobs/

Contains job definitions.

Why it exists:

- Defines units of work that can be queued and tracked.

## 6.40 backend/app/workers/tasks/

Contains executable task handlers.

Why it exists:

- Executes the actual background work.
- Keeps job definitions separate from execution logic.

## 6.41 backend/app/tests/

Contains backend tests.

Why it exists:

- Protects behavior as the system grows.
- Helps beginners learn through examples.
- Supports refactoring with confidence.

## 6.42 backend/app/tests/unit/

Contains small tests for isolated logic.

Why it exists:

- Tests domain models, policies, and services without external systems.
- Runs quickly.

## 6.43 backend/app/tests/integration/

Contains tests involving real infrastructure or realistic adapters.

Why it exists:

- Verifies that database, API, and service boundaries work together.

## 6.44 backend/app/tests/contract/

Contains API contract tests.

Why it exists:

- Protects the agreement between frontend and backend.
- Helps prevent accidental breaking changes.

## 6.45 backend/migrations/

Contains database migrations.

Why it exists:

- Tracks schema changes over time.
- Allows local, staging, and production databases to evolve consistently.

## 6.46 backend/scripts/

Contains developer and operational scripts.

Why it exists:

- Provides repeatable commands for setup, maintenance, or local workflows.
- Keeps one-off helper logic out of the application package.

## 6.47 frontend/

Contains the future user interface.

Why it exists:

- Separates client-side code from backend code.
- Allows the frontend to evolve independently.

## 6.48 docker/

Contains Docker-specific setup files.

Why it exists:

- Keeps container configuration organized.
- Allows custom PostgreSQL or Neo4j initialization later.

## 6.49 docker-compose.yml

Defines local development services.

Why it exists:

- Lets developers start the backend dependencies with one command.
- Makes local environments consistent.

## 6.50 .env.example

Documents required environment variables.

Why it exists:

- Shows developers what configuration values are needed.
- Avoids committing real secrets.

## 7. Request Flow from Frontend to Backend

This section explains how a typical request should move through the system.

## 7.1 Example: Submit Repository for Analysis

A user enters a repository URL in the frontend and clicks submit.

The flow should be:

```text
1. Frontend sends HTTP request to FastAPI.
2. API route validates the request schema.
3. API route calls the application use case.
4. Use case checks business rules.
5. Use case stores repository and job metadata in PostgreSQL.
6. Use case queues a background analysis job.
7. API returns a response containing the job ID and status.
8. Background worker clones and analyzes the repository.
9. Worker stores relational metadata in PostgreSQL.
10. Worker stores code relationships in Neo4j.
11. Frontend polls or subscribes for job status updates.
```

Important lesson:

The API should return quickly. Heavy repository analysis should happen in the background.

## 7.2 Example: Ask a Question About a Repository

A user asks: "Where is authentication handled?"

The flow should be:

```text
1. Frontend sends the question to the backend.
2. API route validates the request.
3. API route calls an application use case.
4. Use case loads repository metadata from PostgreSQL.
5. Use case asks retrieval services for relevant context.
6. Retrieval may query Neo4j for relationships.
7. Retrieval may query stored chunks or embeddings.
8. Agent workflow builds a prompt with selected context.
9. LLM adapter sends the request to the model provider.
10. Agent receives the model response.
11. Use case stores the question, answer, and run metadata.
12. API returns the answer to the frontend.
```

Important lesson:

The LLM should not receive the entire repository blindly. The backend should retrieve targeted context first.

## 7.3 Example: View Repository Graph

A user opens a graph visualization.

The flow should be:

```text
1. Frontend requests graph data for a repository.
2. API validates repository ID and query options.
3. Application query use case loads graph data.
4. Neo4j adapter runs graph queries.
5. Use case converts graph results into response-friendly data.
6. API returns nodes and edges.
7. Frontend renders the graph.
```

Important lesson:

Neo4j should serve relationship-heavy queries. PostgreSQL should not be forced to act like a graph database when graph traversal becomes central to the product.

## 8. Where PostgreSQL Fits Later

PostgreSQL will be the main relational database.

It should store structured, transactional data such as:

- Users
- Organizations or workspaces
- Repository records
- Repository analysis jobs
- Job statuses
- Agent runs
- Questions and answers
- API usage records
- Audit logs
- Billing-related records later

PostgreSQL is good for:

- Data integrity
- Transactions
- Relational queries
- Filtering and sorting
- Reporting
- Durable application state

PostgreSQL should live behind repository abstractions in:

```text
backend/app/infrastructure/persistence/postgres/
```

The application layer should not directly write SQL. It should depend on persistence interfaces or repository classes.

## 9. Where Neo4j Fits Later

Neo4j will be the graph database.

It should store relationships such as:

- File imports file
- Function calls function
- Class extends class
- Module depends on module
- Route uses service
- Service uses repository
- Test covers source file
- Symbol defined in file

Neo4j is good for:

- Relationship traversal
- Dependency graphs
- Impact analysis
- Path queries
- Visualizing connections
- Finding highly connected components

Neo4j should live behind graph persistence adapters in:

```text
backend/app/infrastructure/persistence/neo4j/
```

A key design principle:

Use PostgreSQL for application records and Neo4j for code relationships.

Do not force one database to do every job.

## 10. Where Docker Fits Later

Docker will provide consistent local development and deployment environments.

Docker can run:

- FastAPI backend
- PostgreSQL
- Neo4j
- Redis or another queue backend later
- Background worker
- Frontend development server

In local development, Docker Compose can start multiple services together.

Future local development flow:

```text
1. Developer copies .env.example to .env.
2. Developer starts Docker Compose.
3. PostgreSQL starts.
4. Neo4j starts.
5. Backend starts.
6. Worker starts.
7. Frontend starts.
8. Developer opens the app locally.
```

Docker helps avoid the common problem of "it works on my machine" by making the environment repeatable.

## 11. Where the LLM Fits Later

The LLM is an external reasoning engine used by the agent.

It should help with:

- Summarizing repositories
- Explaining files
- Answering questions
- Planning analysis steps
- Interpreting graph results
- Generating natural-language explanations

The LLM should not own the system architecture.

A production-quality design treats the LLM as one replaceable dependency behind an adapter.

The LLM adapter should live in:

```text
backend/app/infrastructure/llm/
```

Agent workflows should live in:

```text
backend/app/agents/
```

This separation matters:

- The infrastructure LLM adapter knows how to call a provider.
- The agent workflow knows when and why to call the model.
- The application use case knows which user action is being handled.

## 12. Repository Analysis Pipeline

A future repository analysis pipeline may look like this:

```text
1. Receive repository URL.
2. Validate repository access and size limits.
3. Create repository record.
4. Create analysis job.
5. Queue background job.
6. Clone repository into temporary storage.
7. Detect language and framework.
8. Walk file tree.
9. Ignore generated, binary, vendor, and irrelevant files.
10. Parse source files.
11. Extract symbols, imports, routes, functions, classes, and dependencies.
12. Chunk code for retrieval.
13. Generate summaries and embeddings if needed.
14. Store metadata in PostgreSQL.
15. Store relationships in Neo4j.
16. Mark analysis job as completed or failed.
```

This pipeline should be built gradually. The first version does not need to do everything.

## 13. Agent Workflow Design

A future question-answering agent may follow this workflow:

```text
1. Receive user question.
2. Classify question type.
3. Load repository metadata.
4. Retrieve relevant files, symbols, chunks, and graph relationships.
5. Select tools if more context is needed.
6. Build a prompt with context and instructions.
7. Call the LLM.
8. Validate or post-process the response.
9. Store the agent run.
10. Return answer with references.
```

The agent should be tool-aware but constrained.

Good agent tools might include:

- Search repository files
- Get file summary
- Get symbol references
- Query dependency graph
- Get analysis status
- Retrieve related code chunks

The agent should not have unlimited access to everything. Tool boundaries make behavior safer, easier to test, and easier to debug.

## 14. API Design Concepts

The backend should expose clear HTTP endpoints.

Possible future endpoints:

```text
GET    /api/v1/health
POST   /api/v1/repositories
GET    /api/v1/repositories
GET    /api/v1/repositories/{repository_id}
POST   /api/v1/repositories/{repository_id}/analysis-jobs
GET    /api/v1/analysis-jobs/{job_id}
POST   /api/v1/repositories/{repository_id}/questions
GET    /api/v1/repositories/{repository_id}/graph
```

These are design examples, not generated implementation.

Good API design principles:

- Use nouns for resources.
- Use HTTP methods consistently.
- Return predictable response shapes.
- Include useful error messages.
- Version APIs when clients depend on them.
- Keep long-running work asynchronous.

## 15. Error Handling Strategy

The project should distinguish between different error types.

Examples:

- Validation error: user sent invalid input.
- Authentication error: user is not logged in.
- Authorization error: user lacks permission.
- Not found error: requested resource does not exist.
- Conflict error: action conflicts with current state.
- External service error: LLM, Git, database, or queue failed.
- Internal error: unexpected backend failure.

The API layer should convert internal errors into consistent HTTP responses.

The application and domain layers should not be filled with raw HTTP exceptions. That would make business logic too dependent on FastAPI.

## 16. Testing Strategy

Testing should grow with the project.

Recommended testing layers:

- Unit tests for domain rules and policies.
- Unit tests for application use cases with fake dependencies.
- Integration tests for database adapters.
- Contract tests for API schemas and response shapes.
- End-to-end tests for critical workflows later.

Beginner-friendly rule:

Start by testing the most important business behavior, not every private helper.

Good first tests later:

- Repository size policy rejects oversized repositories.
- Submit repository use case creates an analysis job.
- Analysis status endpoint returns the expected response shape.
- Agent question use case includes repository context.

## 17. Security and Safety Notes

Repository intelligence systems need careful boundaries.

Important future concerns:

- Do not execute arbitrary repository code during analysis.
- Limit repository size.
- Ignore binary and generated files.
- Sanitize file paths.
- Protect secrets in uploaded repositories.
- Avoid leaking private repository content to unauthorized users.
- Keep LLM API keys out of source control.
- Rate limit expensive endpoints.
- Track token usage.
- Restrict agent tools.

A repository analysis system should inspect code, not blindly run it.

## 18. Trade-Offs

## 18.1 Modular Architecture vs Simplicity

A layered architecture introduces more folders and concepts.

Benefit:

- Easier to scale and maintain.
- Better testing boundaries.
- Cleaner learning path for production systems.

Cost:

- More structure to understand at first.
- More files than a tiny demo project.

Decision:

Use modular architecture, but keep each layer simple and well documented.

## 18.2 PostgreSQL and Neo4j vs One Database

Using two databases increases operational complexity.

Benefit:

- PostgreSQL handles transactional app data well.
- Neo4j handles graph relationships naturally.

Cost:

- More infrastructure to run.
- More integration points to test.

Decision:

Start with PostgreSQL for core records. Add Neo4j when relationship queries become important enough to justify it.

## 18.3 LLM Early vs LLM Later

Adding an LLM too early can make the system feel magical but hard to debug.

Benefit of adding later:

- Core repository analysis can be tested first.
- The agent has better structured context to use.
- Failures are easier to isolate.

Decision:

Design for the LLM now, but implement deterministic repository analysis foundations first.

## 18.4 Background Workers vs Synchronous Requests

Synchronous requests are easier to understand at first.

Background workers are better for slow work.

Decision:

Keep simple local flows early, but design repository analysis as an asynchronous job from the beginning.

## 19. Interview Notes

These are useful talking points for backend interviews.

### 19.1 Why Use Layers?

Layering separates responsibilities.

A route handler should not clone repositories, parse files, call an LLM, write database rows, and format responses all in one place.

Layering improves:

- Testability
- Maintainability
- Replaceability
- Debuggability
- Team collaboration

### 19.2 What Is the Application Layer?

The application layer contains use cases.

It answers:

**What should happen when the user performs an action?**

It coordinates work but does not handle low-level details.

### 19.3 What Is the Domain Layer?

The domain layer represents the core business concepts and rules.

It should be independent from frameworks and databases where possible.

### 19.4 Why Keep FastAPI Routes Thin?

Thin routes make the backend easier to test and change.

Routes should mostly handle HTTP concerns:

- Receive request
- Validate input
- Call use case
- Return response

### 19.5 Why Use PostgreSQL?

PostgreSQL is reliable for structured application data, transactions, and relational queries.

### 19.6 Why Use Neo4j?

Neo4j is useful when relationships are the main thing being queried, such as dependencies between files, symbols, and modules.

### 19.7 Why Use Background Jobs?

Repository analysis can take seconds or minutes. HTTP requests should not stay open for long-running work.

A job system lets the backend accept work quickly and process it asynchronously.

### 19.8 Why Abstract the LLM Provider?

LLM providers change. Models, APIs, prices, and capabilities evolve.

An adapter makes it easier to switch providers without rewriting the whole agent.

## 20. Phase 1 Implementation Boundary

This phase introduces architecture only.

What is included:

- High-level system design
- Layer responsibilities
- Folder structure design
- Request flow explanation
- Future placement of PostgreSQL, Neo4j, Docker, and LLM
- Trade-offs and learning notes

What is intentionally not included:

- FastAPI source code
- Database models
- Docker files
- Agent implementation
- LLM prompts
- Tests
- Running application

This boundary is important. Good engineering starts with knowing what problem a phase is solving.

## 21. Key Takeaways

- Keep API routes thin.
- Put workflows in the application layer.
- Put business concepts in the domain layer.
- Put external system details in the infrastructure layer.
- Use PostgreSQL for relational application data.
- Use Neo4j for code relationship graphs when needed.
- Use Docker to make local development repeatable.
- Treat the LLM as a replaceable dependency.
- Run slow repository analysis in background workers.
- Build the project in small phases.
- Keep documentation current as the architecture evolves.

## 22. Recommended Next Phase

The next phase should be project scaffolding.

Suggested next steps:

1. Create the backend project folder.
2. Add FastAPI dependencies.
3. Add a health endpoint.
4. Add configuration loading.
5. Add basic logging.
6. Add test setup.
7. Document how to run the backend locally.

The goal of the next phase should be a tiny but production-shaped FastAPI app, not a full repository intelligence system all at once.
