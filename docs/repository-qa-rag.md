# Repository QA and RAG Learning Guide

## 1. Phase Goal

This phase adds Repository QA: the ability to ask a question about an already analyzed repository.

New endpoint:

```text
POST /api/v1/ask
```

Input:

```json
{
  "repository_id": "repository-id-from-analysis",
  "question": "Which services are important in this repository?"
}
```

High-level workflow:

```text
Question
  -> Retrieve repository context
  -> Query Neo4j if needed
  -> Build prompt
  -> LLM
  -> Answer
```

This is the first Retrieval Augmented Generation workflow in the project.

## 2. What Was Implemented

The backend now supports asking questions about stored repository analysis.

It retrieves context from:

- PostgreSQL analysis reports
- Neo4j graph data when the question appears relationship-oriented

Then it sends the retrieved context to OpenAI and returns a structured answer.

The answer response includes:

```text
repository_id
question
answer
token_usage
```

The answer object includes:

```text
answer
confidence
sources
graph_context_used
```

## 3. Files Created or Updated

```text
backend/app/api/v1/router.py                    updated
backend/app/api/v1/routes/ask.py                created
backend/app/crud/repository_crud.py             updated
backend/app/schemas/qa.py                       created
backend/app/services/neo4j_graph_service.py     updated
backend/app/services/repository_qa_service.py   created

docs/repository-qa-rag.md                       created
```

## 4. Architecture

The Repository QA flow uses the existing layered backend architecture:

```text
FastAPI Route
  -> Pydantic Request Schema
  -> RepositoryQAService
  -> RepositoryCRUD
  -> PostgreSQL
  -> Neo4jGraphService when needed
  -> Prompt Builder
  -> OpenAI
  -> Pydantic Response Schema
```

Each layer has a focused job:

- Route handles HTTP.
- Pydantic validates input and output shapes.
- CRUD retrieves stored repository context.
- Neo4j service retrieves graph relationships.
- QA service orchestrates the workflow.
- OpenAI generates the final answer from retrieved context.

## 5. Request Flow

When a client calls `POST /api/v1/ask`, the flow is:

```text
1. Client sends repository_id and question.
2. FastAPI validates the request with RepositoryQuestionRequest.
3. The route receives a database session through Depends(get_db).
4. The route calls RepositoryQAService.answer_question().
5. QA service loads the Repository from PostgreSQL.
6. QA service loads the latest AnalysisReport from PostgreSQL.
7. QA service decides whether graph context is useful.
8. If needed, QA service asks Neo4jGraphService for graph context.
9. QA service builds a system prompt.
10. QA service builds a user prompt containing retrieved context.
11. QA service calls OpenAI.
12. OpenAI returns JSON.
13. QA service validates the JSON with Pydantic.
14. FastAPI returns the structured answer.
```

## 6. Why This Is Retrieval Augmented Generation

Retrieval Augmented Generation is usually shortened to RAG.

RAG means:

```text
Retrieve relevant context first.
Then generate an answer using that context.
```

This endpoint is RAG because it does not ask the LLM to answer from memory alone.

Instead, the backend first retrieves repository-specific context:

- Repository URL
- Stored analysis report
- File count
- Directory count
- Detected technologies
- Stored summary
- Directory structure sample
- Neo4j graph relationships when useful

Then the backend puts that context into the prompt.

The LLM generates an answer grounded in the retrieved repository data.

This is different from a plain LLM call.

Plain LLM call:

```text
Question -> LLM -> Answer
```

RAG call:

```text
Question -> Retrieve context -> LLM with context -> Answer
```

The retrieval step makes answers more relevant to the specific repository.

## 7. Endpoint Design

File:

```text
backend/app/api/v1/routes/ask.py
```

Endpoint:

```text
POST /ask
```

Because the app mounts v1 routes under `/api/v1`, the full path is:

```text
POST /api/v1/ask
```

Route responsibilities:

- Accept the validated request.
- Receive a database session.
- Call the QA service.
- Convert known service errors into HTTP errors.

The route does not:

- Query PostgreSQL directly.
- Query Neo4j directly.
- Build prompts.
- Call OpenAI directly.

This keeps business logic out of the route.

## 8. Pydantic QA Schemas

File:

```text
backend/app/schemas/qa.py
```

## 8.1 RepositoryQuestionRequest

Purpose:

- Validates incoming QA requests.

Fields:

```text
repository_id
question
```

Validation rules:

- `repository_id` must not be empty.
- `question` must be at least 3 characters.
- `question` can be at most 2000 characters.

Why this matters:

The backend should reject obviously invalid input before doing database, graph, or LLM work.

## 8.2 RepositoryQuestionAnswer

Purpose:

- Defines the structured answer returned by the LLM.

Fields:

```text
answer
confidence
sources
graph_context_used
```

Why this matters:

Instead of returning one loose paragraph, the backend returns a predictable answer object.

## 8.3 RepositoryQuestionResponse

Purpose:

- Defines the full API response.

Fields:

```text
repository_id
question
answer
token_usage
```

Token usage is reused from the existing repository schema because OpenAI reports token counts for both summary generation and QA.

## 9. CRUD Updates

File:

```text
backend/app/crud/repository_crud.py
```

New functions:

```text
get_repository_by_id()
get_latest_analysis_report()
```

## 9.1 get_repository_by_id()

Purpose:

- Loads the repository row for the requested `repository_id`.

Why it exists:

The QA service needs to confirm that the repository exists before answering questions about it.

## 9.2 get_latest_analysis_report()

Purpose:

- Loads the most recent analysis report for a repository.

Why it exists:

The QA endpoint answers questions using stored analysis results. It should use the newest available report.

Ordering rule:

```text
ORDER BY created_at DESC
```

This gives the latest report first.

## 10. Neo4j Retrieval Updates

File:

```text
backend/app/services/neo4j_graph_service.py
```

New functions:

```text
query_repository_context()
read_repository_context()
```

## 10.1 query_repository_context()

Purpose:

- Opens a Neo4j session.
- Runs graph read queries.
- Returns graph context for the QA prompt.

It requires:

```text
NEO4J_PASSWORD
```

If Neo4j is not configured or cannot be queried, the graph service raises a controlled error.

## 10.2 read_repository_context()

Purpose:

- Runs Cypher queries inside a Neo4j read transaction.

It retrieves:

- File import relationships
- Module usage relationships
- Service files
- Highly imported files

This graph context helps answer questions about architecture, dependencies, modules, and services.

## 10.3 Example Retrieved Graph Context

The graph context may look like:

```json
{
  "imported_files": [
    {
      "source": "backend/app/main.py",
      "target": "backend/app/api/v1/router.py"
    }
  ],
  "module_uses": [
    {
      "source": "api",
      "target": "services"
    }
  ],
  "services": [
    {
      "service": "backend/app/services/repository_service.py",
      "module": "backend"
    }
  ],
  "central_files": [
    {
      "file": "backend/app/schemas/repository.py",
      "import_count": 4
    }
  ]
}
```

## 11. RepositoryQAService Explained

File:

```text
backend/app/services/repository_qa_service.py
```

This is the main service for Repository QA.

It coordinates:

- Context retrieval
- Optional graph retrieval
- Prompt building
- OpenAI call
- Response validation

## 11.1 RepositoryQAServiceError

Purpose:

- Represents controlled QA workflow failures.

Examples:

- Repository not found
- No analysis report exists
- Neo4j retrieval fails
- OpenAI key is missing
- OpenAI returns invalid JSON

## 11.2 graph_keywords

Purpose:

- Defines words that suggest graph context may help.

Examples:

```text
imports
modules
services
dependencies
architecture
central
flow
```

If the question contains these words, the QA service queries Neo4j.

This is a simple first version of query routing.

## 11.3 answer_question()

Purpose:

- Main QA workflow method.

What it does:

```text
1. Load repository from PostgreSQL.
2. Load latest analysis report from PostgreSQL.
3. Decide whether to query Neo4j.
4. Retrieve graph context if needed.
5. Build system prompt.
6. Build user prompt.
7. Call OpenAI.
8. Return structured response.
```

## 11.4 should_query_graph()

Purpose:

- Decides whether graph context should be retrieved.

Current strategy:

- Lowercase the question.
- Check whether it contains graph-related keywords.

Trade-off:

This is simple and transparent, but not perfect. Later, this could be replaced with intent classification.

## 11.5 build_system_prompt()

Purpose:

- Defines model behavior for repository QA.

Current behavior contract:

```text
You are a repository intelligence assistant.
Answer using only retrieved repository context.
If context is not enough, say what is missing.
Return only valid JSON.
Do not include markdown.
```

Why this matters:

The system prompt tells the model not to invent answers from outside knowledge.

## 11.6 build_user_prompt()

Purpose:

- Builds the request-specific prompt.

It includes:

- User question
- Repository ID and URL
- Analysis report data
- Detected technologies
- Stored summary
- Directory structure sample
- Optional graph context
- Required JSON response shape

This is the prompt that performs RAG: it combines the question with retrieved context.

## 11.7 call_llm()

Purpose:

- Calls OpenAI and requests JSON output.

It uses:

```text
model
temperature
max_tokens
response_format={"type": "json_object"}
```

The same OpenAI configuration from the summary phase is reused.

## 11.8 parse_answer()

Purpose:

- Parses the LLM response as JSON.
- Validates it against `RepositoryQuestionAnswer`.

Why it matters:

LLM output should be validated before returning it to clients.

## 11.9 extract_token_usage()

Purpose:

- Converts OpenAI usage metadata into the existing `TokenUsage` schema.

Token tracking helps with cost and observability.

## 12. Prompt Design

The QA prompt has two parts:

```text
System prompt
User prompt
```

## 12.1 System Prompt

The system prompt defines rules.

It tells the model:

- What role it has
- What context it may use
- What to do if context is insufficient
- What output format to return

## 12.2 User Prompt

The user prompt contains the question and retrieved data.

The retrieved context is intentionally structured as JSON so the model can see clear sections:

```text
question
repository
retrieved_context
required_json_shape
```

## 13. Why Not Send the Whole Repository?

The QA endpoint does not send full source code to the LLM.

Reasons:

- Full repositories can exceed token limits.
- Sending too much context increases cost.
- More context can make answers worse if it is noisy.
- Sensitive source code should be handled carefully.
- The current project has stored metadata and graph relationships, which are smaller and more targeted.

This phase uses retrieved metadata and graph context as a safer first RAG strategy.

## 14. How Neo4j Helps QA

Neo4j is useful when questions are relationship-heavy.

Examples:

```text
Which modules depend on each other?
Which files are most central?
Where are services implemented?
What imports what?
```

These questions are hard to answer from a flat summary alone.

Graph context gives the LLM relationship facts that it can cite in its answer.

## 15. Trade-Offs

## 15.1 Keyword-Based Graph Routing

Current behavior:

- Query Neo4j only when the question contains certain keywords.

Benefit:

- Simple and beginner-friendly.
- Avoids unnecessary Neo4j calls for broad summary questions.

Cost:

- May miss questions where graph context would help.
- May query Neo4j for some questions where graph context is not necessary.

Future direction:

Use an intent classifier or always retrieve a small graph summary.

## 15.2 Latest Report Only

Current behavior:

- QA uses the latest analysis report.

Benefit:

- Simple default behavior.
- Usually what users expect.

Cost:

- User cannot ask about older reports yet.

Future direction:

Allow `analysis_report_id` in the request.

## 15.3 No QA Persistence Yet

Current behavior:

- Answers are returned but not stored.

Benefit:

- Keeps the phase focused on RAG.

Cost:

- No conversation history.
- No audit trail of questions and answers.

Future direction:

Add `Question` and `Answer` tables.

## 15.4 Metadata and Graph Context Only

Current behavior:

- The LLM sees stored report metadata and graph relationships.

Benefit:

- Lower token usage.
- Easier to reason about.
- Avoids sending entire source code.

Cost:

- Some detailed code questions cannot be answered yet.

Future direction:

Add code chunk retrieval for file-level or function-level questions.

## 16. Error Handling

Possible errors:

- Repository does not exist.
- Repository has no analysis report.
- Neo4j is needed but not configured.
- Neo4j query fails.
- OpenAI API key is missing.
- OpenAI request fails.
- OpenAI returns invalid JSON.

The route converts `RepositoryQAServiceError` into an HTTP `400 Bad Request` response.

This keeps raw database, graph, and provider errors away from the API boundary.

## 17. Security and Safety Notes

The QA endpoint should answer from retrieved repository context only.

Important safety rules:

- Do not ask the model to invent missing repository facts.
- Do not send more data than needed.
- Validate model output.
- Track token usage.
- Avoid returning raw provider errors.
- Add authentication before supporting private repositories in a real product.

## 18. Interview Notes

## 18.1 What Is RAG?

RAG means Retrieval Augmented Generation.

It retrieves relevant data first, then uses an LLM to generate an answer from that data.

## 18.2 Why Use RAG for Repository QA?

The LLM does not know the user's specific repository by default.

RAG gives the model repository-specific context before it answers.

## 18.3 What Is Retrieval?

Retrieval is the step where the backend gathers relevant context.

In this project, retrieval comes from PostgreSQL and Neo4j.

## 18.4 Why Query Neo4j Only If Needed?

Graph queries are most useful for relationship-heavy questions.

For a broad question like `What does this repository do?`, the stored summary may be enough.

For `Which services depend on each other?`, Neo4j is much more useful.

## 18.5 Why Validate the LLM Answer?

LLMs can return malformed or unexpected output.

Pydantic validation protects the API response contract.

## 18.6 Why Return Sources?

Sources help the user understand what context influenced the answer.

They also make the answer easier to debug.

## 19. Key Takeaways

- Added `POST /api/v1/ask`.
- The endpoint accepts `repository_id` and `question`.
- PostgreSQL retrieves stored repository context.
- Neo4j retrieves graph context for relationship-heavy questions.
- The QA service builds a prompt from retrieved context.
- OpenAI generates a structured answer.
- Pydantic validates the answer shape.
- This is Retrieval Augmented Generation because generation is grounded in retrieved context.
- The route remains thin and delegates workflow to the service layer.

## 20. Recommended Next Phase

Suggested next steps:

1. Add tests with fake PostgreSQL/Neo4j/OpenAI dependencies.
2. Store questions and answers in PostgreSQL.
3. Add `analysis_report_id` to support asking about specific reports.
4. Add code chunk retrieval for detailed code questions.
5. Add graph retrieval endpoints for frontend visualization.
6. Add authentication before private repository QA.
