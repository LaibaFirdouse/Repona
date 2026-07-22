# OpenAI Repository Summary Integration Guide

## 1. Phase Goal

This phase integrates OpenAI into the Repository Intelligence Agent backend.

The backend now follows this workflow:

```text
Repository URL
  -> RepositoryService
  -> Clone repository
  -> Extract metadata
  -> Generate prompt
  -> OpenAI
  -> Structured summary
  -> Store summary in PostgreSQL
  -> Return structured response
```

This phase adds AI summarization, but it still keeps the architecture modular and beginner-friendly.

The system does **not** use autonomous agents yet. It makes one controlled OpenAI call using repository metadata.

## 2. What Was Implemented

The repository endpoint now returns both deterministic metadata and an AI-generated structured summary.

The response includes:

```text
repository_id
analysis_report_id
metadata
summary
token_usage
```

The summary is also stored in PostgreSQL inside the `analysis_reports` table.

## 3. Files Created or Updated

```text
.env.example                                      updated
requirements.txt                                  updated

backend/app/core/config.py                        updated
backend/app/schemas/repository.py                 updated
backend/app/models/analysis_report.py             updated
backend/app/crud/repository_crud.py               updated
backend/app/services/repository_service.py        updated
backend/app/services/openai_summary_service.py    created

docs/openai-summary-integration.md                created
```

## 4. Architecture

The new architecture is:

```text
FastAPI Route
  |
  v
RepositoryService
  |
  |-- clone repository
  |-- extract metadata
  |-- call OpenAISummaryService
  |-- save repository analysis
  v
RepositoryCRUD
  |
  v
PostgreSQL
```

The OpenAI-specific work is isolated in:

```text
backend/app/services/openai_summary_service.py
```

This keeps the repository service from becoming overloaded with prompt construction and OpenAI SDK details.

## 5. Execution Flow

When a client calls `POST /api/v1/repository`, the flow is:

```text
1. FastAPI validates the request body with Pydantic.
2. The route receives a SQLAlchemy database session.
3. The route calls RepositoryService.create_repository().
4. RepositoryService clones the repository into a temporary folder.
5. RepositoryService extracts deterministic metadata.
6. RepositoryService calls OpenAISummaryService.summarize_repository().
7. OpenAISummaryService builds a system prompt.
8. OpenAISummaryService builds a user prompt from repository metadata.
9. OpenAISummaryService calls OpenAI.
10. OpenAI returns JSON content.
11. OpenAISummaryService validates the JSON into RepositorySummary.
12. RepositoryService stores metadata, summary, and token usage through CRUD.
13. PostgreSQL stores Repository and AnalysisReport records.
14. FastAPI returns the structured response.
```

The important lesson is that AI is one step in the workflow. It does not replace validation, deterministic metadata extraction, persistence, or API contracts.

## 6. Configuration

File:

```text
backend/app/core/config.py
```

New settings:

```text
OPENAI_API_KEY
OPENAI_MODEL
OPENAI_TEMPERATURE
OPENAI_MAX_OUTPUT_TOKENS
```

Environment template:

```text
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0.2
OPENAI_MAX_OUTPUT_TOKENS=800
```

Why configuration matters:

- API keys should never be hardcoded.
- Model choice should be changeable per environment.
- Temperature should be adjustable without editing code.
- Output token limits help control cost and response size.

If `OPENAI_API_KEY` is missing, the OpenAI summary service raises a controlled service error.

## 7. Dependency

File:

```text
requirements.txt
```

New dependency:

```text
openai
```

This package provides the official OpenAI Python SDK used to call the chat completions API.

## 8. Schema Updates

File:

```text
backend/app/schemas/repository.py
```

New schemas:

```text
RepositorySummary
TokenUsage
RepositorySummaryResult
```

## 8.1 RepositorySummary

Purpose:

- Defines the structured summary returned by OpenAI and returned by the API.

Fields:

```text
executive_summary
main_technologies
architecture_observations
notable_directories
next_steps
```

Why it matters:

The backend does not return a loose paragraph. It returns structured data that the frontend can render predictably.

## 8.2 TokenUsage

Purpose:

- Captures token usage reported by OpenAI.

Fields:

```text
prompt_tokens
completion_tokens
total_tokens
```

Why it matters:

Token usage affects cost, latency, and observability. Tracking it early is a production-quality habit.

## 8.3 RepositorySummaryResult

Purpose:

- Groups the summary and token usage together inside the OpenAI service boundary.

This lets the repository service receive one clear result object from the AI step.

## 8.4 RepositoryCreateResponse

Updated response fields:

```text
summary
token_usage
```

The API now returns deterministic metadata and AI-generated summary data together.

## 9. Database Model Updates

File:

```text
backend/app/models/analysis_report.py
```

New fields:

```text
summary: JSON
token_usage: JSON
```

Why summary is stored:

- The summary can be shown again without calling OpenAI again.
- The system keeps a historical record of what the model generated.
- Future users can compare summaries across analysis runs.
- Future endpoints can retrieve previous reports quickly.

Why token usage is stored:

- Helps understand model cost.
- Helps debug unusually expensive requests.
- Helps future rate limiting and usage dashboards.

Beginner note:

Because this project does not use Alembic migrations yet, adding columns to an existing database table may require resetting the local database or adding migrations in a future phase.

## 10. CRUD Updates

File:

```text
backend/app/crud/repository_crud.py
```

Updated functions:

```text
create_analysis_report()
save_repository_analysis()
```

They now accept:

```text
summary
token_usage
```

The CRUD layer stores these values in the `analysis_reports` table.

Important architecture point:

The CRUD layer does not call OpenAI. It only stores data it receives.

## 11. RepositoryService Updates

File:

```text
backend/app/services/repository_service.py
```

New behavior:

```text
1. Extract metadata.
2. Generate OpenAI summary from metadata.
3. Store metadata and summary.
4. Return metadata and summary.
```

## 11.1 __init__()

The service now accepts:

```text
repository_crud
summary_service
```

Why this matters:

These dependencies can be replaced in tests later. For example, a fake summary service could return a fixed summary without calling OpenAI.

## 11.2 create_repository()

This is now the full orchestration method.

It coordinates:

- Git clone
- Metadata extraction
- Summary generation
- Database persistence
- API response construction

It still does not build the OpenAI request itself. That responsibility belongs to `OpenAISummaryService`.

## 11.3 serialize_model()

Purpose:

- Converts one Pydantic model into a plain dictionary.

Why it exists:

SQLAlchemy JSON columns need JSON-compatible values. Pydantic models must be converted before storage.

## 11.4 serialize_model_list()

Purpose:

- Converts a list of Pydantic models into a list of dictionaries.

This is used for technologies and directory structure.

## 12. OpenAISummaryService Explained Function by Function

File:

```text
backend/app/services/openai_summary_service.py
```

## 12.1 OpenAISummaryServiceError

Purpose:

- Represents controlled failures from the OpenAI summary step.

Examples:

- Missing API key
- OpenAI request failure
- Empty model response
- Invalid JSON response
- Response does not match expected summary shape

## 12.2 __init__()

Purpose:

- Allows an OpenAI client to be injected.

Why it matters:

Injecting a client makes the service easier to test later without making real network calls.

## 12.3 summarize_repository()

Purpose:

- Main entry point for AI summary generation.

What it does:

```text
1. Build system prompt.
2. Build user prompt.
3. Call OpenAI.
4. Parse JSON response.
5. Return summary and token usage.
```

## 12.4 build_system_prompt()

Purpose:

- Defines the model's role and behavior rules.

Current intent:

- Act like a senior backend engineer.
- Return only valid JSON.
- Avoid markdown.
- Be concise and beginner-friendly.
- Use only the provided metadata.

This is a system prompt because it tells the model how to behave across the task.

## 12.5 build_user_prompt()

Purpose:

- Builds the task-specific prompt from repository metadata.

It includes:

- Repository URL
- File count
- Directory count
- Detected technologies
- Top-level directory structure
- Required JSON response shape

This is a user prompt because it contains the actual task and data for this request.

## 12.6 serialize_directory_entry()

Purpose:

- Converts directory tree entries into JSON-friendly dictionaries for the prompt.

Why it limits depth and children:

Repositories can be large. Sending a full directory tree can waste tokens and increase cost. The prompt includes a compact view instead.

Current limits:

- Top-level entries are capped.
- Nested child entries are capped.
- Depth is limited.

## 12.7 call_openai()

Purpose:

- Sends the prompts to OpenAI.

Important parameters:

```text
model
temperature
max_tokens
response_format
messages
```

The service asks for JSON output using:

```text
response_format={"type": "json_object"}
```

This improves the chance that the model returns parseable structured data.

## 12.8 parse_summary()

Purpose:

- Parses the model response as JSON.
- Validates it against `RepositorySummary`.

Why it matters:

LLM output should not be blindly trusted. The backend checks that the response has the expected shape before returning or storing it.

## 12.9 extract_token_usage()

Purpose:

- Reads token usage from the OpenAI response.

If usage data is missing, the service returns zero values instead of crashing.

## 13. Prompt Engineering

Prompt engineering means designing the instructions and context sent to the model so it produces useful, reliable output.

In this phase, prompt engineering includes:

- Giving the model a role.
- Telling it to return only JSON.
- Giving it repository metadata.
- Providing the exact required JSON shape.
- Limiting the directory structure to control token cost.
- Asking it to base the answer only on provided data.

Good prompt engineering is not magic wording. It is careful interface design between backend data and model behavior.

## 14. System Prompt

The system prompt defines high-level behavior.

Current system prompt intent:

```text
You are a senior backend engineer analyzing repository metadata.
Return only valid JSON.
Do not include markdown.
Be concise, practical, and beginner-friendly.
Base your answer only on the metadata provided.
```

Why it matters:

The system prompt gives the model stable rules that apply to the whole task.

In backend terms, the system prompt is like a behavior contract for the model call.

## 15. User Prompt

The user prompt contains request-specific data.

Current user prompt includes:

- Repository URL
- File count
- Directory count
- Technology detections
- Directory structure sample
- Required JSON schema shape

Why it matters:

The user prompt changes for each repository. It gives the model the facts it needs to produce a summary.

## 16. Temperature

Temperature controls how creative or varied the model output can be.

Lower temperature:

- More predictable
- Less creative
- Better for structured backend workflows

Higher temperature:

- More varied
- More creative
- More likely to produce unexpected wording or structure

Current default:

```text
OPENAI_TEMPERATURE=0.2
```

Why this value:

Repository summaries should be consistent and structured. A low temperature is a good fit.

## 17. Token Usage

Tokens are chunks of text processed by the model.

OpenAI reports usage as:

```text
prompt_tokens
completion_tokens
total_tokens
```

## 17.1 prompt_tokens

Tokens used by the input messages.

This includes:

- System prompt
- User prompt
- Repository metadata sent to the model

## 17.2 completion_tokens

Tokens used by the model's output.

This is the generated summary.

## 17.3 total_tokens

Total tokens used:

```text
prompt_tokens + completion_tokens
```

Why token usage matters:

- It affects cost.
- It affects latency.
- It helps detect oversized prompts.
- It helps guide prompt optimization.

## 18. Why Store the Summary?

The summary is stored instead of regenerated every time because:

- OpenAI calls cost money.
- OpenAI calls add latency.
- The same report should be reproducible later.
- Users may want to view old reports.
- Stored summaries make future retrieval endpoints faster.
- Stored token usage supports cost tracking.

This follows a general backend principle:

If work is expensive and the result is useful later, store the result.

## 19. Trade-Offs

## 19.1 Calling OpenAI During the Request

Current behavior:

- The API request waits for cloning, metadata extraction, OpenAI, and database storage.

Benefit:

- Simple to understand.
- Response contains everything immediately.

Cost:

- Slower API responses.
- More failure points during one request.
- Not ideal for large repositories or production traffic.

Future direction:

Move analysis and summary generation into a background job.

## 19.2 JSON Response Format

Current behavior:

- The service requests JSON from OpenAI and validates it with Pydantic.

Benefit:

- Structured response.
- Easier frontend rendering.
- Safer than free-form text.

Cost:

- Model can still sometimes return invalid or incomplete JSON.
- Backend must handle parse and validation failures.

Future direction:

Use stricter structured output APIs or retry logic.

## 19.3 Metadata-Only Prompt

Current behavior:

- The model sees metadata, not full source code.

Benefit:

- Smaller prompt.
- Lower cost.
- Lower risk of exposing too much code.
- Faster response.

Cost:

- Summary is high-level.
- Model cannot deeply explain implementation details yet.

Future direction:

Add retrieval over selected files or chunks when deeper explanations are needed.

## 19.4 Storing Summary as JSON

Current behavior:

- Summary is stored in a JSON column.

Benefit:

- Flexible shape.
- Easy to return in API responses.
- Good for early product learning.

Cost:

- Harder to query individual summary fields than normalized columns.

Future direction:

Keep JSON if summaries are mostly displayed, or normalize fields if reporting/querying becomes important.

## 20. Error Handling

Possible OpenAI-related errors:

- Missing API key
- Network failure
- Provider API failure
- Empty response
- Invalid JSON
- Response shape mismatch

Current behavior:

- `OpenAISummaryService` raises `OpenAISummaryServiceError`.
- `RepositoryService` converts that into `RepositoryServiceError`.
- The route converts service errors into HTTP `400 Bad Request`.

This keeps raw provider errors from leaking directly into the API boundary.

## 21. Security and Cost Notes

Important notes:

- Do not commit `OPENAI_API_KEY`.
- Keep prompts compact to control cost.
- Do not send more repository data than needed.
- Store token usage for observability.
- Validate model output before storing it.
- Treat LLM output as generated content, not guaranteed truth.

This phase sends metadata, not full source code. That is a safer first step.

## 22. Interview Notes

## 22.1 Why Use a Separate OpenAI Service?

It isolates provider-specific logic from repository workflow code.

This makes the system easier to test, replace, and maintain.

## 22.2 What Is Prompt Engineering?

Prompt engineering is designing model instructions and context so the model returns useful output in the expected format.

In backend systems, prompt engineering should be treated like API design.

## 22.3 Why Use a System Prompt?

The system prompt defines the model's role and behavior rules.

It keeps the model focused and consistent across requests.

## 22.4 Why Use a User Prompt?

The user prompt carries the specific task data for one request.

In this project, it contains repository metadata.

## 22.5 Why Low Temperature?

Low temperature makes output more predictable, which is better for structured backend responses.

## 22.6 Why Track Token Usage?

Token usage helps estimate cost, debug performance, and optimize prompts.

## 22.7 Why Validate LLM Output?

LLMs can return malformed or unexpected output.

Pydantic validation protects the API contract and database from bad model responses.

## 22.8 Why Store AI Results?

Storing AI results avoids repeated cost and gives users stable reports they can revisit.

## 23. Key Takeaways

- OpenAI is now integrated into the repository analysis workflow.
- The backend generates prompts from deterministic repository metadata.
- The system prompt controls model behavior.
- The user prompt carries request-specific repository facts.
- Temperature is configured for predictable summaries.
- Token usage is captured and stored.
- Summary output is validated with Pydantic.
- Summary and token usage are stored in PostgreSQL.
- The route remains thin.
- The OpenAI provider logic is isolated in its own service.

## 24. Recommended Next Phase

A good next phase would improve reliability and production readiness.

Suggested next steps:

1. Add tests with a fake OpenAI client.
2. Add retry logic for transient OpenAI failures.
3. Add Alembic migrations for the new database columns.
4. Move repository analysis and OpenAI calls into a background worker.
5. Add retrieval endpoints for stored repositories and analysis reports.
6. Add prompt versioning so future summaries can be traced to the prompt that created them.
