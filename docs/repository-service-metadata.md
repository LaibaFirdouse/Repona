# RepositoryService Metadata Extraction Guide

## 1. Phase Goal

This phase replaces the previous mock repository response with a real metadata extraction service.

Given a GitHub repository URL, the backend now:

- Clones the repository into a temporary local folder.
- Reads the visible directory structure.
- Ignores heavy or generated folders such as `.git`, `node_modules`, and `build`.
- Counts files.
- Counts directories.
- Detects technologies from manifest files such as `package.json`, `requirements.txt`, and `pyproject.toml`.
- Returns structured metadata through the API response.

This phase still does **not** call an LLM, use a database, add Docker, run repository code, or persist anything permanently.

## 2. Updated API Behavior

Endpoint:

```text
POST /api/v1/repository
```

Request body:

```json
{
  "repo_url": "https://github.com/example/project"
}
```

Response shape:

```json
{
  "repository_id": "repo-project",
  "repo_url": "https://github.com/example/project",
  "status": "analyzed",
  "message": "Repository metadata extracted successfully.",
  "metadata": {
    "file_count": 42,
    "directory_count": 8,
    "ignored_directories": [".git", "build", "node_modules"],
    "technologies": [
      {
        "name": "FastAPI",
        "category": "backend framework",
        "source": "requirements.txt"
      }
    ],
    "directory_structure": [
      {
        "name": "backend",
        "path": "backend",
        "kind": "directory",
        "children": []
      }
    ]
  }
}
```

The exact counts and structure depend on the submitted repository.

## 3. Files Updated

```text
backend/app/api/v1/routes/repository.py
backend/app/schemas/repository.py
backend/app/services/repository_service.py

docs/repository-service-metadata.md
```

## 4. Architecture

The feature still follows the same high-level architecture:

```text
Route
  -> Service
  -> Response
```

The important difference is that the service now performs real work.

Detailed flow:

```text
HTTP request
  -> FastAPI route
  -> Pydantic request validation
  -> RepositoryService
  -> Git clone
  -> Directory traversal
  -> Technology detection
  -> Pydantic response model
  -> JSON response
```

The route does not clone the repository, walk files, or detect technologies. Those responsibilities belong to the service layer.

## 5. Request Execution Flow

When a client submits a repository URL, the backend does this:

```text
1. Client sends POST /api/v1/repository.
2. FastAPI validates the request body with RepositoryCreateRequest.
3. The route calls RepositoryService.create_repository().
4. The service creates a temporary folder.
5. The service runs git clone with depth 1.
6. The service reads repository metadata.
7. The service builds a directory tree while skipping ignored folders.
8. The service counts visible files and directories.
9. The service checks known manifest files for technologies.
10. The service returns RepositoryCreateResponse.
11. FastAPI serializes the response to JSON.
12. The temporary clone is deleted automatically.
```

The temporary folder is important. It means cloned repositories do not stay on disk after the request finishes.

## 6. Schema Changes

## 6.1 RepositoryCreateRequest

Purpose:

- Represents the incoming request body.
- Requires `repo_url`.
- Uses Pydantic's `HttpUrl` type for URL validation.

Why it matters:

The service should receive a valid URL-shaped value, not arbitrary text.

## 6.2 DirectoryEntry

Purpose:

- Represents one file or directory in the repository tree.

Fields:

- `name`: file or directory name.
- `path`: path relative to the repository root.
- `kind`: either `directory` or `file`.
- `children`: nested entries for directories.

Why it matters:

This gives the frontend a structured tree instead of a plain text dump.

## 6.3 TechnologyDetection

Purpose:

- Represents one detected technology.

Fields:

- `name`: technology name, such as `FastAPI` or `React`.
- `category`: the kind of technology, such as `backend framework`.
- `source`: the manifest file where it was detected.

Why it matters:

A technology detection should explain where the conclusion came from.

## 6.4 RepositoryMetadata

Purpose:

- Groups all extracted metadata for the repository.

Fields:

- `file_count`
- `directory_count`
- `ignored_directories`
- `technologies`
- `directory_structure`

Why it matters:

The API response stays organized as the metadata grows.

## 6.5 RepositoryCreateResponse

Purpose:

- Represents the full API response.

New behavior:

- The response now includes a `metadata` object.
- The `status` is now `analyzed` when metadata extraction succeeds.
- The response is no longer just a mock acknowledgment.

## 7. Repository Route Explained

File:

```text
backend/app/api/v1/routes/repository.py
```

The route function is still named:

```text
create_repository()
```

Responsibilities:

- Accept a validated request model.
- Call the repository service.
- Return the service response.
- Convert service errors into HTTP errors.

The route catches `RepositoryServiceError` and returns a `400 Bad Request` response.

This is HTTP translation, not business logic. The route is not deciding how repository analysis works.

## 8. RepositoryService Explained Function by Function

File:

```text
backend/app/services/repository_service.py
```

## 8.1 RepositoryServiceError

Purpose:

- Represents a known service-level failure.

Examples:

- Git is not installed.
- The repository cannot be cloned.
- The clone operation times out.

Why it exists:

The service needs a clear way to report expected failures to the route. The route can then translate those failures into HTTP responses.

## 8.2 create_repository()

Purpose:

- Main service entry point for repository submission.

What it does:

```text
1. Converts the Pydantic URL into a string.
2. Creates a temporary directory.
3. Clones the repository into that directory.
4. Reads metadata from the cloned repository.
5. Builds and returns RepositoryCreateResponse.
```

Why it matters:

This method coordinates the use case. It is the service-level workflow for repository metadata extraction.

## 8.3 clone_repository()

Purpose:

- Clones a Git repository into a local destination folder.

What it does:

- Runs `git clone --depth 1`.
- Captures command output.
- Applies a timeout.
- Raises `RepositoryServiceError` if cloning fails.

Why `--depth 1` is used:

The service only needs the current file tree for this phase. A shallow clone avoids downloading full commit history, making the operation faster and lighter.

Trade-off:

A shallow clone is good for metadata extraction, but it is not enough for future features that need commit history.

## 8.4 read_repository_metadata()

Purpose:

- Collects all metadata from the cloned repository.

What it calls:

- `build_directory_structure()`
- `count_files()`
- `count_directories()`
- `detect_technologies()`

Why it exists:

It keeps metadata assembly in one place and makes the main workflow easier to read.

## 8.5 build_directory_structure()

Purpose:

- Builds a nested tree of files and directories.

What it does:

- Reads visible children of a directory.
- Creates `DirectoryEntry` objects.
- Recursively processes child directories.
- Keeps paths relative to the repository root.

Why recursion is used:

Directory trees are naturally recursive. A directory can contain files and more directories, and each child directory follows the same pattern.

## 8.6 iter_visible_children()

Purpose:

- Returns the visible children of one directory.

What it does:

- Reads immediate files and folders.
- Filters ignored folders.
- Sorts directories before files.
- Sorts names alphabetically.

Why it matters:

This creates predictable output for the API response and avoids returning noisy generated folders.

## 8.7 should_ignore()

Purpose:

- Decides whether a path should be ignored.

Current rule:

- Ignore a path if it is a directory and its name is in the ignored directory set.

Ignored examples:

- `.git`
- `node_modules`
- `build`
- `dist`
- `__pycache__`

Why it matters:

Repository metadata should focus on source code and project files, not dependencies, caches, or build outputs.

## 8.8 count_files()

Purpose:

- Counts files that are not inside ignored directories.

What it does:

- Walks the repository tree.
- Prunes ignored directories during traversal.
- Adds the number of visible files.

Why pruning matters:

Skipping ignored folders during traversal is more efficient than walking everything and filtering afterward.

This is especially important for `node_modules`, which can contain thousands of files.

## 8.9 count_directories()

Purpose:

- Counts visible directories that are not ignored.

What it does:

- Walks the repository tree.
- Removes ignored directory names before descending.
- Counts only visible directories.

Why it matters:

Directory count gives a rough signal of project size and structure.

## 8.10 detect_technologies()

Purpose:

- Coordinates technology detection across known manifest files.

Files checked:

- `package.json`
- `requirements.txt`
- `pyproject.toml`

Why it exists:

Each manifest has a different format. This method gives the service one place to gather all detection results.

## 8.11 detect_from_package_json()

Purpose:

- Detects JavaScript and TypeScript ecosystem technologies.

What it looks for:

- `Node.js` when `package.json` exists.
- `React`
- `Next.js`
- `Vue`
- `Angular`
- `Express`
- `Fastify`
- `NestJS`
- `Vite`
- `TypeScript`

Why it matters:

`package.json` is the main manifest for Node.js projects.

If the file is malformed or unreadable, the function returns no detections instead of crashing the request.

## 8.12 detect_from_requirements()

Purpose:

- Detects Python technologies from `requirements.txt`.

What it looks for:

- `Python` when `requirements.txt` exists.
- `FastAPI`
- `Django`
- `Flask`
- `SQLAlchemy`
- `Pytest`
- `Pandas`

Why it matters:

Many Python projects still declare dependencies in `requirements.txt`.

## 8.13 detect_from_pyproject()

Purpose:

- Detects Python technologies from `pyproject.toml`.

What it looks for:

- `Python` when `pyproject.toml` exists.
- `Poetry`
- `Setuptools`
- `FastAPI`
- `Django`
- `Flask`

Why it matters:

Modern Python projects commonly use `pyproject.toml` for packaging and dependency metadata.

The function uses Python's TOML parser when available. If the runtime does not support it, the service still detects Python from the file's presence.

## 8.14 deduplicate_technologies()

Purpose:

- Removes duplicate technology detections.

Why it exists:

A technology may appear in more than one place. Deduplication keeps the response cleaner and easier to read.

The function sorts detections so output remains stable.

## 8.15 build_repository_id()

Purpose:

- Builds a simple local identifier from the repository URL.

Example:

```text
https://github.com/example/project
```

becomes:

```text
repo-project
```

Important limitation:

This is not a database ID and is not guaranteed to be globally unique. It is acceptable for this phase because there is no persistence yet.

## 9. Technology Detection Strategy

This phase uses manifest-based detection.

That means the service does not inspect every source file to infer technologies. It looks for common dependency files and reads known dependency names.

Benefits:

- Simple to understand.
- Fast compared with deep code analysis.
- Good enough for first metadata extraction.
- Easy to extend with more manifest files later.

Limitations:

- It may miss technologies not listed in manifests.
- It may detect dependencies that are installed but barely used.
- It does not understand framework configuration deeply yet.

Future manifest files to consider:

- `go.mod`
- `Cargo.toml`
- `pom.xml`
- `build.gradle`
- `composer.json`
- `Gemfile`
- `.csproj`

## 10. Ignore Strategy

The service ignores directories that are usually not useful for source metadata.

Current ignored directories include:

```text
.git
.next
.pytest_cache
.ruff_cache
.venv
__pycache__
build
dist
node_modules
venv
```

Why these are ignored:

- `.git` contains repository internals.
- `node_modules` contains installed dependencies and can be huge.
- `build` and `dist` usually contain generated output.
- Cache and virtual environment folders are not source code.

This keeps responses smaller, faster, and more relevant.

## 11. Error Handling

The service raises `RepositoryServiceError` for expected clone-related failures.

The route converts this into:

```text
400 Bad Request
```

Examples:

- Invalid or unreachable repository URL.
- Private repository without access.
- Git command failure.
- Git command timeout.
- Git not installed on the machine.

This phase does not add a global error handling system yet. That can come later.

## 12. Security and Safety Notes

The service clones repositories but does not execute repository code.

That is important.

Reading files is much safer than running unknown code from the internet.

Current safety boundaries:

- Shallow clone only.
- Temporary directory cleanup.
- Ignored dependency/build folders.
- No script execution.
- No LLM call.
- No database persistence.

Future safety improvements:

- Restrict clone size.
- Restrict allowed hosts to GitHub.
- Add request timeout at API level.
- Limit directory tree depth.
- Limit response size.
- Add background jobs for long-running analysis.
- Add authentication before private repository support.

## 13. Trade-Offs

## 13.1 Synchronous Clone in an API Request

Current behavior:

- The request waits while the repository is cloned and inspected.

Benefit:

- Easy to understand for this learning phase.
- No worker queue needed yet.

Cost:

- Large repositories can make the request slow.
- Production systems should avoid long-running request handlers.

Future direction:

Move cloning and analysis into a background job.

## 13.2 Temporary Clone Only

Current behavior:

- The repository is deleted after metadata extraction.

Benefit:

- No disk cleanup problem.
- No persistence complexity.

Cost:

- The same repository must be cloned again for every request.
- Results are not saved.

Future direction:

Store metadata in a database and manage repository workspaces intentionally.

## 13.3 Manifest-Based Technology Detection

Current behavior:

- Detection is based on known manifest files.

Benefit:

- Simple, deterministic, and explainable.

Cost:

- Not as deep as full code analysis.

Future direction:

Add language parsers and richer source-code analysis.

## 13.4 Git CLI Instead of a Python Git Library

Current behavior:

- The service calls the installed `git` command.

Benefit:

- No new Python dependency.
- Uses the standard Git behavior developers already know.

Cost:

- Requires Git to be installed on the machine.
- Error handling depends on subprocess behavior.

Future direction:

Keep Git CLI if it remains enough, or introduce a Git library if deeper Git operations become necessary.

## 14. Interview Notes

## 14.1 Why Keep Cloning in the Service Layer?

Cloning is part of the repository submission use case. The route should not know how cloning works.

The route should only receive HTTP input, call the service, and return HTTP output.

## 14.2 Why Ignore `node_modules` and `.git`?

These folders are usually large and not useful for source-level metadata.

Ignoring them improves performance and keeps the response focused.

## 14.3 Why Use Structured Metadata?

Structured metadata is easier for clients to consume than plain text.

A frontend can render file counts, technology tags, and directory trees directly from structured JSON.

## 14.4 Why Avoid LLMs Here?

This phase is deterministic.

Before asking an LLM to explain a repository, the backend should first learn how to collect reliable facts about that repository.

## 14.5 Why Use Temporary Directories?

Temporary directories make cleanup automatic.

They are useful when the system needs short-lived local files during request processing.

## 14.6 What Would Change in Production?

In production, repository analysis should likely run asynchronously.

The API would accept a repository URL, create an analysis job, return a job ID, and let a worker clone and inspect the repository in the background.

## 15. Key Takeaways

- `RepositoryService` now performs real metadata extraction.
- The backend clones repositories with `git clone --depth 1`.
- The service reads directory structure without returning ignored folders.
- The service counts visible files and directories.
- Technology detection is based on manifest files.
- The route stays thin and delegates work to the service.
- No LLM, database, Docker, or code execution was added.
- This phase introduces deterministic repository intelligence before AI.

## 16. Recommended Next Phase

A strong next phase would be testing and guardrails.

Suggested next steps:

1. Add tests for technology detection using local fixture folders.
2. Add tests for ignored directory behavior.
3. Add tests for invalid clone errors.
4. Add a maximum directory depth or response-size limit.
5. Move cloning into a background job when the project introduces workers.
