# Neo4j Repository Graph Integration Guide

## 1. Phase Goal

This phase adds Neo4j graph storage to the Repository Intelligence Agent backend.

The backend now extracts repository graph facts from the cloned repository and stores them in Neo4j.

The graph focuses on:

- Files
- Imports between files
- Modules
- Module usage relationships
- Services

This phase creates graph relationships such as:

```text
(File)-[:IMPORTS]->(File)
(Module)-[:USES]->(Module)
```

The goal is to make repository structure easier to understand through relationships, not just rows and JSON blobs.

## 2. What Was Implemented

The repository analysis workflow now includes graph extraction and graph persistence.

Updated workflow:

```text
Repository URL
  -> RepositoryService
  -> Clone repository
  -> Extract metadata
  -> Extract graph facts
  -> Generate OpenAI summary
  -> Store repository and report in PostgreSQL
  -> Store code graph in Neo4j
  -> Return structured response with graph stats
```

The API response now includes a `graph` object with counts:

```json
{
  "graph": {
    "file_nodes": 10,
    "module_nodes": 3,
    "service_nodes": 2,
    "file_import_relationships": 7,
    "module_use_relationships": 2
  }
}
```

## 3. Files Created or Updated

```text
.env.example                                      updated
requirements.txt                                  updated

backend/app/core/config.py                        updated
backend/app/schemas/repository.py                 updated
backend/app/services/repository_service.py        updated
backend/app/services/neo4j_graph_service.py       created

docs/neo4j-graph-integration.md                   created
```

## 4. Architecture

The Neo4j integration is isolated in its own service:

```text
backend/app/services/neo4j_graph_service.py
```

High-level architecture:

```text
FastAPI Route
  |
  v
RepositoryService
  |
  |-- clone repository
  |-- extract metadata
  |-- build graph facts
  |-- generate OpenAI summary
  |-- store relational data in PostgreSQL
  |-- store graph data in Neo4j
  v
Response
```

The route still does not know how Neo4j works.

The OpenAI service still does not know how Neo4j works.

The CRUD layer still only stores PostgreSQL data.

Neo4j-specific behavior lives in the graph service.

## 5. Why Neo4j?

PostgreSQL is excellent for structured application records, such as repositories and analysis reports.

Neo4j is useful when relationships are the main thing you want to understand.

Repository understanding is relationship-heavy:

- Which file imports this file?
- Which modules depend on each other?
- Which services are central?
- What breaks if this file changes?
- Which areas are tightly coupled?
- What is the shortest path between two modules?

These questions are graph-shaped.

A graph database stores data as nodes and relationships, making traversal natural.

## 6. Nodes Explained

A node represents an entity in the graph.

In this phase, the graph uses these node labels:

```text
Repository
File
Module
Service
```

## 6.1 Repository Node

Represents the analyzed repository.

Example:

```text
(:Repository {id: "...", url: "https://github.com/example/project"})
```

Why it exists:

Every graph node should be scoped to one repository so multiple repositories can live in the same Neo4j database.

## 6.2 File Node

Represents a source file.

Example:

```text
(:File {repository_id: "...", path: "backend/app/main.py", module: "backend"})
```

Why it exists:

Files are the most concrete unit of repository structure.

The graph can answer questions such as:

```text
Which files does this file import?
Which files import this file?
Which module owns this file?
```

## 6.3 Module Node

Represents a high-level project area.

Current module detection rule:

- If a file is at the repository root, the module is the file stem.
- If a file is inside a directory, the module is the first path segment.

Examples:

```text
backend/app/main.py -> backend
frontend/src/App.tsx -> frontend
README.md -> README
```

This is intentionally simple. Later phases can improve module detection with language-specific rules.

## 6.4 Service Node

Represents a service-like source file.

Current service detection rule:

- File stem contains `service`, or
- File path contains `/services/`

Examples:

```text
backend/app/services/repository_service.py
src/user/UserService.ts
```

Why it exists:

Services often contain important application behavior. Highlighting them in the graph helps future analysis focus on orchestration and business workflows.

## 7. Relationships Explained

A relationship connects two nodes.

In this phase, the graph uses these relationships:

```text
(:Repository)-[:CONTAINS]->(:File)
(:Repository)-[:CONTAINS]->(:Module)
(:Module)-[:CONTAINS]->(:File)
(:File)-[:IMPORTS]->(:File)
(:Module)-[:USES]->(:Module)
(:Service)-[:IMPLEMENTED_IN]->(:File)
```

## 7.1 File imports File

Main requested relationship:

```text
(File)-[:IMPORTS]->(File)
```

Meaning:

One local source file imports another local source file.

Example:

```text
backend/app/main.py imports backend/app/api/v1/router.py
```

Graph form:

```text
(:File {path: "backend/app/main.py"})-[:IMPORTS]->(:File {path: "backend/app/api/v1/router.py"})
```

Why it matters:

Import relationships help answer impact analysis questions.

If a file changes, imported-by relationships show which files may be affected.

## 7.2 Module uses Module

Main requested relationship:

```text
(Module)-[:USES]->(Module)
```

Meaning:

A file in one module imports a file in another module.

Example:

```text
api module imports services module
```

Graph form:

```text
(:Module {name: "api"})-[:USES]->(:Module {name: "services"})
```

Why it matters:

Module relationships show architectural coupling at a higher level than individual files.

## 8. Cypher Explained

Cypher is Neo4j's graph query language.

SQL is used for relational databases.

Cypher is used for graph databases.

A simple Cypher pattern looks like this:

```cypher
(:File)-[:IMPORTS]->(:File)
```

This means:

```text
Find a File node connected by an IMPORTS relationship to another File node.
```

## 8.1 MERGE

The implementation uses `MERGE` heavily.

`MERGE` means:

```text
Find this pattern if it exists.
If it does not exist, create it.
```

Example:

```cypher
MERGE (node:File {repository_id: $repository_id, path: file.path})
```

Why `MERGE` is useful:

It prevents duplicate nodes when the same graph write runs more than once.

## 8.2 MATCH

`MATCH` finds existing graph patterns.

Example:

```cypher
MATCH (source:File {repository_id: $repository_id, path: item.source})
MATCH (target:File {repository_id: $repository_id, path: item.target})
MERGE (source)-[:IMPORTS]->(target)
```

This means:

```text
Find the source file.
Find the target file.
Create the import relationship if it does not already exist.
```

## 8.3 UNWIND

`UNWIND` turns a list into rows.

Example:

```cypher
UNWIND $files AS file
```

If `$files` contains 100 files, Neo4j processes one row per file.

This is useful for batch writes.

## 9. Code Extraction Strategy

The graph service extracts from source files with these extensions:

```text
.py
.js
.jsx
.ts
.tsx
```

It ignores the same heavy folders used by metadata extraction, such as:

```text
.git
node_modules
build
dist
venv
__pycache__
```

## 9.1 File Extraction

Every supported source file becomes a `File` node.

The service stores:

- Relative path
- Module name
- Whether the file looks like a service

## 9.2 Python Import Extraction

Python imports are parsed with Python's built-in `ast` module.

Why this matters:

`ast` parses Python syntax structurally. This is safer and more reliable than using string matching for Python imports.

Supported import forms:

```python
import app.services.repository_service
from app.schemas.repository import RepositoryMetadata
from .router import api_router
```

The service attempts to resolve these imports to local Python files.

## 9.3 JavaScript and TypeScript Import Extraction

JavaScript and TypeScript imports are extracted with a simple regular expression.

Supported patterns include:

```text
import x from "./module"
export x from "./module"
require("./module")
```

The service currently resolves local relative imports only, such as:

```text
./service
../utils/helper
```

It intentionally does not create `File -> imports -> File` relationships for package imports such as `react` or `express`, because those are external dependencies rather than local files.

## 9.4 Module Extraction

Modules are inferred from file paths.

Example:

```text
backend/app/services/repository_service.py -> backend
src/components/Button.tsx -> src
```

This is a beginner-friendly first version. Future versions can improve module extraction using package manifests, framework conventions, or language-specific project structures.

## 9.5 Service Extraction

A source file is considered a service when:

```text
file name contains service
or
path contains /services/
```

Service extraction helps identify files likely to contain application workflows.

## 10. File-by-File Explanation

## 10.1 `backend/app/core/config.py`

New settings:

```text
NEO4J_URI
NEO4J_USER
NEO4J_PASSWORD
```

These values tell the backend how to connect to Neo4j.

## 10.2 `.env.example`

New environment variables:

```text
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
```

The password is intentionally blank in the example. Real secrets should not be committed.

## 10.3 `requirements.txt`

New dependency:

```text
neo4j
```

This is the official Neo4j Python driver.

## 10.4 `backend/app/schemas/repository.py`

New schema:

```text
RepositoryGraphStats
```

Purpose:

- Returns counts of graph nodes and relationships written to Neo4j.

Fields:

```text
file_nodes
module_nodes
service_nodes
file_import_relationships
module_use_relationships
```

## 10.5 `backend/app/services/repository_service.py`

What changed:

- The service now owns a `Neo4jGraphService` dependency.
- It builds graph facts while the temporary repository clone still exists.
- It stores graph data after PostgreSQL creates the repository ID.
- It returns graph statistics in the API response.

Why graph extraction happens before the temporary folder is deleted:

The cloned repository exists only inside the temporary directory context. Once that context ends, the files are deleted. Graph facts must be extracted before cleanup.

Why graph storage happens after PostgreSQL storage:

Neo4j graph nodes need the stable `repository_id` from PostgreSQL so graph data and relational records can be linked.

## 10.6 `backend/app/services/neo4j_graph_service.py`

This is the main file for the Neo4j phase.

It contains:

```text
FileNode
RepositoryGraph
Neo4jGraphServiceError
Neo4jGraphService
```

## 11. Neo4jGraphService Function Explanations

## 11.1 FileNode

Purpose:

- Represents a file before it is written to Neo4j.

Fields:

```text
path
module
is_service
```

## 11.2 RepositoryGraph

Purpose:

- Holds extracted graph facts in memory.

Fields:

```text
files
file_imports
module_uses
services
```

The `stats()` method converts graph facts into `RepositoryGraphStats` for the API response.

## 11.3 Neo4jGraphServiceError

Purpose:

- Represents controlled graph-related failures.

Examples:

- Missing Neo4j password
- Neo4j connection failure
- Neo4j write failure

## 11.4 build_graph()

Purpose:

- Extracts graph facts from the cloned repository.

What it does:

```text
1. Collect source files.
2. Create file nodes.
3. Detect modules.
4. Detect services.
5. Extract imports.
6. Resolve imports to local files.
7. Create File IMPORTS File relationships.
8. Create Module USES Module relationships.
```

## 11.5 store_graph()

Purpose:

- Connects to Neo4j and writes extracted graph facts.

What it does:

```text
1. Validates Neo4j password exists.
2. Creates a Neo4j driver if one was not injected.
3. Opens a Neo4j session.
4. Runs graph writes in a transaction.
5. Closes the driver when owned by the service.
6. Returns graph statistics.
```

## 11.6 collect_source_files()

Purpose:

- Finds source files supported by the graph extractor.

It prunes ignored folders during traversal so large folders like `node_modules` are not scanned.

## 11.7 detect_module_name()

Purpose:

- Assigns a file to a module.

Current rule:

- Root file gets its own stem as module.
- Nested file uses its top-level folder as module.

## 11.8 is_service_file()

Purpose:

- Detects whether a file looks like a service.

This is heuristic-based. It is useful but not perfect.

## 11.9 extract_imports()

Purpose:

- Chooses the import extraction strategy based on file extension.

Python files use AST parsing.

JavaScript and TypeScript files use pattern matching.

## 11.10 extract_python_imports()

Purpose:

- Extracts imports from Python syntax trees.

This function does not execute Python code. It only parses source text.

## 11.11 extract_javascript_imports()

Purpose:

- Extracts import strings from JavaScript and TypeScript files.

This is a simple first version. It can be improved later with a JavaScript parser.

## 11.12 resolve_import()

Purpose:

- Routes import resolution to Python or JavaScript/TypeScript logic.

## 11.13 resolve_python_import()

Purpose:

- Attempts to map a Python import path to a local `.py` file.

Example:

```text
app.services.repository_service
```

may resolve to:

```text
app/services/repository_service.py
```

## 11.14 resolve_javascript_import()

Purpose:

- Attempts to map a relative JavaScript or TypeScript import to a local file.

Example:

```text
../services/userService
```

may resolve to:

```text
src/services/userService.ts
```

## 11.15 match_candidate_path()

Purpose:

- Checks possible file paths for an import target.

It tries common file extensions and index files.

## 11.16 write_graph()

Purpose:

- Contains the Cypher write queries.

It creates or updates:

- Repository nodes
- File nodes
- Module nodes
- Service nodes
- Contains relationships
- Imports relationships
- Uses relationships
- Implemented-in relationships

## 12. Example Graph

Imagine this repository structure:

```text
backend/
  api.py
  services/
    user_service.py
  models.py
```

If `api.py` imports `user_service.py`, the graph may include:

```text
(:Repository)-[:CONTAINS]->(:Module {name: "backend"})
(:Module {name: "backend"})-[:CONTAINS]->(:File {path: "backend/api.py"})
(:File {path: "backend/api.py"})-[:IMPORTS]->(:File {path: "backend/services/user_service.py"})
(:Service {path: "backend/services/user_service.py"})-[:IMPLEMENTED_IN]->(:File {path: "backend/services/user_service.py"})
```

If two top-level modules import each other, the graph can show:

```text
(:Module {name: "api"})-[:USES]->(:Module {name: "services"})
```

## 13. Useful Future Cypher Queries

Find files imported by a specific file:

```cypher
MATCH (:File {repository_id: $repository_id, path: $path})-[:IMPORTS]->(target:File)
RETURN target.path
```

Find files that import a specific file:

```cypher
MATCH (source:File)-[:IMPORTS]->(:File {repository_id: $repository_id, path: $path})
RETURN source.path
```

Find module dependencies:

```cypher
MATCH (source:Module {repository_id: $repository_id})-[:USES]->(target:Module)
RETURN source.name, target.name
```

Find services:

```cypher
MATCH (service:Service {repository_id: $repository_id})-[:IMPLEMENTED_IN]->(file:File)
RETURN service.path, file.module
```

Find highly imported files:

```cypher
MATCH (source:File)-[:IMPORTS]->(target:File {repository_id: $repository_id})
RETURN target.path, count(source) AS import_count
ORDER BY import_count DESC
```

## 14. Why Graph Databases Are Useful for Repository Understanding

Repositories are networks of relationships.

A relational database can store files and imports, but relationship traversal can become awkward as questions get deeper.

Graph databases are useful because they make relationship questions natural:

- What depends on this?
- What does this depend on?
- Which modules are connected?
- Which files are central?
- Which services sit between modules?
- How far apart are two parts of the system?

Neo4j is especially useful for impact analysis, dependency exploration, architecture visualization, and codebase navigation.

## 15. Trade-Offs

## 15.1 Simple Import Resolution

Current behavior:

- Python imports are parsed with AST.
- JavaScript and TypeScript imports are matched with regex.
- External package imports are ignored for file relationships.

Benefit:

- Simple and understandable.
- Good enough for first graph construction.

Cost:

- May miss dynamic imports.
- May miss alias-based imports.
- JavaScript/TypeScript parsing is not as robust as a real parser.

Future direction:

Use language-specific parsers for richer dependency extraction.

## 15.2 Simple Module Detection

Current behavior:

- Module is inferred from the top-level path segment.

Benefit:

- Easy to understand.
- Works reasonably for many repositories.

Cost:

- Not always semantically accurate.
- Some projects use nested packages or monorepo conventions.

Future direction:

Detect modules from package files, framework conventions, or explicit config.

## 15.3 Neo4j Write After PostgreSQL Commit

Current behavior:

- PostgreSQL stores the analysis report first.
- Neo4j stores the graph afterward.

Benefit:

- Neo4j can use the stable PostgreSQL repository ID.

Cost:

- If Neo4j fails after PostgreSQL succeeds, relational and graph storage may temporarily disagree.

Future direction:

Use background jobs, retry logic, and graph sync status fields.

## 15.4 Synchronous Graph Building

Current behavior:

- Graph extraction and Neo4j writes happen during the API request.

Benefit:

- Easy to follow in a learning project.

Cost:

- Large repositories may make requests slow.

Future direction:

Move repository analysis into a background worker.

## 16. Error Handling

Neo4j errors are wrapped in `Neo4jGraphServiceError`.

RepositoryService converts them into `RepositoryServiceError`.

The route converts service errors into HTTP responses.

Possible errors:

- Missing `NEO4J_PASSWORD`
- Neo4j is not running
- Invalid Neo4j credentials
- Network failure
- Cypher write failure

## 17. Security and Safety Notes

The graph extractor reads source files but does not execute repository code.

That is important.

Safe behavior in this phase:

- No repository code execution
- Ignored heavy folders
- Local import resolution only for file relationships
- Controlled Neo4j configuration through environment variables

Future safety improvements:

- Limit maximum files analyzed
- Limit maximum file size parsed
- Add repository size guardrails
- Run graph extraction in background workers
- Add retries and partial failure tracking

## 18. Interview Notes

## 18.1 What Is a Node?

A node is an entity in a graph.

Examples in this project:

- Repository
- File
- Module
- Service

## 18.2 What Is a Relationship?

A relationship connects two nodes and gives meaning to their connection.

Examples:

- File imports File
- Module uses Module
- Repository contains File

## 18.3 What Is Cypher?

Cypher is Neo4j's query language for creating and querying graph patterns.

It is designed around nodes and relationships.

## 18.4 Why Use Neo4j for Repository Intelligence?

Repository intelligence depends heavily on relationships.

Neo4j makes it easier to query dependencies, impact paths, module coupling, and architectural structure.

## 18.5 Why Keep PostgreSQL Too?

PostgreSQL stores durable application records well.

Neo4j stores relationship-heavy code structure well.

They serve different purposes.

## 18.6 Why Extract Services?

Services often contain application workflows and business logic.

Identifying service-like files helps future analysis focus on meaningful backend behavior.

## 19. Key Takeaways

- Neo4j integration was added through a dedicated graph service.
- The backend extracts files, imports, modules, and services.
- File-to-file imports are stored as `IMPORTS` relationships.
- Module-to-module usage is stored as `USES` relationships.
- Services are represented as `Service` nodes linked to their implementation files.
- Cypher is used to write graph data.
- Graph databases are valuable because repositories are relationship-heavy systems.
- The implementation is intentionally simple and can evolve with better parsers later.

## 20. Recommended Next Phase

A strong next phase would add graph retrieval endpoints.

Suggested next steps:

1. Add `GET /repositories/{repository_id}/graph`.
2. Add Cypher queries for dependency exploration.
3. Add tests for import extraction with fixture repositories.
4. Add graph sync status in PostgreSQL.
5. Move graph extraction into a background worker.
6. Add language-specific parsers for more accurate relationships.
