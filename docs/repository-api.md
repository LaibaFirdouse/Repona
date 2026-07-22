# Repository API Learning Guide

> Phase note: this document explains the first Repository API phase, where the endpoint returned a mock response. The current implementation now clones a repository and extracts metadata. See `repository-service-metadata.md` for the updated service behavior.

## 1. Phase Goal

This phase adds the first feature endpoint to the backend: a Repository API.

The goal is intentionally focused:

- Add `POST /repository`.
- Accept a JSON request body containing `repo_url`.
- Validate input with Pydantic.
- Return a mock response.
- Keep business logic out of the route.
- Introduce a service layer.
- Document every concept introduced.

This phase does **not** add AI, databases, Docker, background jobs, Git cloning, or real repository analysis.

## 2. What Was Implemented

The backend now supports this endpoint under the existing API prefix:

```text
POST /api/v1/repository
```

The route accepts:

```json
{
  "repo_url": "https://github.com/example/project"
}
```

It returns a mock response:

```json
{
  "repository_id": "mock-repository-001",
  "repo_url": "https://github.com/example/project",
  "status": "received",
  "message": "Repository submission received for future analysis."
}
```

The implementation follows this flow:

```text
Route
  -> Service
  -> Response
```

The route handles HTTP concerns. The service handles the business workflow, even though the workflow is still mocked in this phase.

## 3. Files Created or Updated

```text
backend/
  app/
    api/
      v1/
        router.py                  updated
        routes/
          repository.py            created

    schemas/
      repository.py                created

    services/
      __init__.py                  created
      repository_service.py        created

docs/
  repository-api.md                created
```

## 4. File-by-File Explanation

## 4.1 `backend/app/api/v1/router.py`

Purpose:

- Registers API route modules for version 1 of the backend API.

What changed:

- The repository route module was imported.
- The repository router was included in `api_router`.

Conceptually:

```text
api_router
  includes health routes
  includes repository routes
```

Why this matters:

`main.py` only includes one versioned router. Individual feature routes are collected inside `router.py`.

This keeps the app startup file small and keeps API organization centralized.

## 4.2 `backend/app/api/v1/routes/repository.py`

Purpose:

- Defines the HTTP endpoint for repository submission.

Endpoint:

```text
POST /repository
```

Because the app already mounts v1 routes under `/api/v1`, the full path is:

```text
POST /api/v1/repository
```

Responsibilities:

- Declare the HTTP method and path.
- Accept a validated request body.
- Declare the response model.
- Set the HTTP status code.
- Call the service layer.
- Return the service result.

What the route should not do:

- It should not clone a repository.
- It should not decide business rules.
- It should not contain database logic.
- It should not call AI models.
- It should not build complex workflows.

Important concept:

A route is the boundary between HTTP and the application. It should translate web requests into service calls.

## 4.3 `backend/app/schemas/repository.py`

Purpose:

- Defines the Pydantic models for repository request and response data.

Models added:

```text
RepositoryCreateRequest
RepositoryCreateResponse
```

### RepositoryCreateRequest

Represents the incoming request body.

Field:

```text
repo_url: HttpUrl
```

`HttpUrl` tells Pydantic that the value must be a valid HTTP or HTTPS URL.

If a client sends this:

```json
{
  "repo_url": "not-a-url"
}
```

FastAPI and Pydantic reject the request before the service runs.

### RepositoryCreateResponse

Represents the response returned to the client.

Fields:

```text
repository_id: str
repo_url: HttpUrl
status: str
message: str
```

Why response schemas matter:

- They make API responses predictable.
- They improve generated API documentation.
- They help frontend developers know what to expect.
- They reduce accidental response shape changes.

## 4.4 `backend/app/services/__init__.py`

Purpose:

- Marks `services` as a Python package.
- Creates a dedicated home for service-layer logic.

Why this folder exists:

The service layer is where application behavior begins to live.

For this project, service classes will eventually coordinate operations such as:

- Validating repository submission rules
- Creating repository records
- Starting analysis workflows
- Calling lower-level infrastructure adapters
- Returning application responses

This phase keeps the service simple and mocked.

## 4.5 `backend/app/services/repository_service.py`

Purpose:

- Contains the service responsible for repository submission behavior.

Class added:

```text
RepositoryService
```

Method added:

```text
create_repository(request)
```

Current behavior:

- Receives a validated `RepositoryCreateRequest`.
- Returns a `RepositoryCreateResponse`.
- Uses a mock repository ID.
- Does not save to a database.
- Does not clone the repository.
- Does not call AI.

Why this matters:

Even though the response is mocked, the architecture is real.

The route does not know how the repository submission is handled. It delegates to the service.

Later, the service can become more powerful without changing the route much.

## 4.6 `docs/repository-api.md`

Purpose:

- Documents this phase as a beginner-friendly learning guide.
- Explains routes, Pydantic, request validation, and the service layer.
- Captures architecture, flow, trade-offs, interview notes, and key takeaways.

## 5. Request Execution Flow

When a client calls the repository endpoint, the flow is:

```text
1. Client sends POST /api/v1/repository.
2. Request body contains repo_url.
3. FastAPI receives the request.
4. FastAPI matches the request to repository.py.
5. Pydantic validates the request body using RepositoryCreateRequest.
6. If repo_url is missing or invalid, FastAPI returns a validation error.
7. If the request is valid, create_repository() runs.
8. The route calls RepositoryService.create_repository().
9. The service creates a mock RepositoryCreateResponse.
10. The route returns the service response.
11. FastAPI serializes the response to JSON.
12. Client receives a 201 Created response.
```

Important lesson:

Validation happens before the service method runs.

That means the service can trust that `repo_url` has already passed the basic API validation rules.

## 6. Architecture Flow

The implemented feature follows this structure:

```text
HTTP Request
   |
   v
Route: repository.py
   |
   v
Pydantic Request Model: RepositoryCreateRequest
   |
   v
Service: RepositoryService
   |
   v
Pydantic Response Model: RepositoryCreateResponse
   |
   v
HTTP Response
```

This is the first step toward a clean backend architecture.

As the project grows, the service can later call deeper layers:

```text
Route
  -> Service
  -> Repository/Data Access later
  -> Background Job later
  -> Response
```

But those later layers are intentionally not part of this phase.

## 7. Routes Explained

A route maps an HTTP request to Python behavior.

A route defines:

- The HTTP method
- The URL path
- The request body type
- The response type
- The status code
- The function that runs

In this phase:

```text
POST /repository
```

means:

- `POST` because the client is submitting something new.
- `/repository` because the submitted resource is a repository.
- `201 Created` because the backend accepted a new repository submission.

Even though the response is mocked, the status code teaches the correct API design idea.

## 8. Pydantic Explained

Pydantic is a Python library for data validation and parsing.

FastAPI uses Pydantic heavily.

When a request comes in, Pydantic checks whether the data matches the declared model.

For example:

```text
repo_url: HttpUrl
```

means:

- The field is required.
- The value must look like a real HTTP or HTTPS URL.
- Invalid input should not reach the service layer.

Pydantic gives the backend strong input boundaries.

Without Pydantic, route handlers often become filled with manual checks like:

```text
if repo_url is missing, return error
if repo_url is not a URL, return error
if repo_url has wrong type, return error
```

With Pydantic, these checks are declared once in a model.

## 9. Request Validation Explained

Request validation protects the backend from bad input.

In this phase, validation checks that:

- The request body is valid JSON.
- `repo_url` exists.
- `repo_url` is a valid HTTP or HTTPS URL.

Example valid request:

```json
{
  "repo_url": "https://github.com/example/project"
}
```

Example invalid request:

```json
{
  "repo_url": "hello"
}
```

If invalid input is sent, FastAPI automatically returns a `422 Unprocessable Entity` response with details about the validation error.

The service does not need to manually handle this basic shape validation.

## 10. Service Layer Explained

The service layer contains application behavior.

A route asks:

```text
What HTTP request came in?
```

A service asks:

```text
What should the system do for this use case?
```

For this phase, the service returns a mock response.

Later, the service might:

- Check if the repository already exists.
- Validate repository size rules.
- Create a repository record in a database.
- Start a background analysis job.
- Return a real repository ID.

The route should not need to know those details.

That is the main reason to introduce a service layer early.

## 11. Why Keep Business Logic Out of Routes?

Routes are framework-facing code.

They should stay thin because:

- They are tied to HTTP.
- They are harder to reuse outside the API.
- They become messy if they contain workflows.
- They are less focused when they mix validation, business decisions, persistence, and response formatting.

A thin route is easier to read:

```text
Accept request
Call service
Return response
```

A service is easier to test because it can be called directly without needing an HTTP request.

## 12. Trade-Offs

## 12.1 Service Layer for a Mock Response

For a tiny mock endpoint, a service class may feel unnecessary.

Benefit:

- Introduces production architecture early.
- Keeps routes thin from the beginning.
- Makes future real behavior easier to add.

Cost:

- Adds one extra file and concept.

Decision:

Use the service layer now because this project is designed for learning scalable backend architecture.

## 12.2 `HttpUrl` Validation

Using `HttpUrl` is stricter than accepting a plain string.

Benefit:

- Bad URLs are rejected automatically.
- The service receives cleaner input.

Cost:

- Some unusual repository URL formats may be rejected until intentionally supported.

Decision:

Use `HttpUrl` for this phase because the accepted input is explicitly a URL.

## 12.3 Mock Response Instead of Real Persistence

The endpoint returns a fixed mock ID.

Benefit:

- Keeps the phase focused on API structure.
- Avoids adding databases too early.
- Allows frontend/API flow to be tested.

Cost:

- Data is not actually stored.
- The response is not unique per request.

Decision:

Return a mock response now. Add persistence in a later database phase.

## 13. Interview Notes

## 13.1 What Is a Route?

A route connects an HTTP method and URL path to backend behavior.

In FastAPI, routes are usually defined on an `APIRouter`.

## 13.2 Why Use Pydantic With FastAPI?

Pydantic validates and parses incoming data.

It reduces manual validation code and creates clear API contracts.

## 13.3 What Happens When Validation Fails?

FastAPI returns a `422 Unprocessable Entity` response before the route function executes successfully.

The service layer is not called with invalid input.

## 13.4 What Is a Service Layer?

A service layer contains application use-case logic.

It coordinates what the system should do after the route receives a valid request.

## 13.5 Why Not Put Everything in the Route?

Putting everything in the route makes the code harder to test, reuse, and maintain.

Thin routes and focused services scale better.

## 13.6 Why Return `201 Created`?

`201 Created` communicates that the server accepted a request to create or submit a new resource.

Even though this phase uses a mock response, the API design matches the intended future behavior.

## 14. Key Takeaways

- `POST /api/v1/repository` was added.
- The route accepts `repo_url` in the request body.
- Pydantic validates the request using `RepositoryCreateRequest`.
- The route delegates business behavior to `RepositoryService`.
- The service returns a mock `RepositoryCreateResponse`.
- Business logic stays out of the route.
- No AI, database, Docker, or repository analysis was added.
- This phase teaches the route -> service -> response pattern.

## 15. Recommended Next Phase

A good next phase would be testing the API.

Suggested next steps:

1. Add a test framework.
2. Add a test for valid repository submission.
3. Add a test for invalid `repo_url` validation.
4. Add a test for the health endpoint.
5. Document how to run tests.

Testing will make the route, Pydantic validation, and service boundary more concrete.
