# LLM provider abstraction

## Why an abstraction layer is used

The application previously called the OpenAI SDK directly from the repository summarization and QA services. That design worked, but it made the rest of the system tightly coupled to one vendor-specific implementation.

A provider abstraction solves that problem by introducing a single interface that each LLM implementation must satisfy. The application can now:

- switch between local and cloud models without changing business logic
- run locally with Ollama while keeping the same API endpoints
- add Anthropic, Gemini, or other providers later with minimal disruption

## Provider pattern

The core idea is simple: each LLM backend implements the same contract.

The shared interface lives in:

- [backend/app/services/llm/base.py](../backend/app/services/llm/base.py)

It defines a single method:

```python
generate(prompt: str) -> str
```

Concrete providers are implemented in:

- [backend/app/services/llm/ollama_provider.py](../backend/app/services/llm/ollama_provider.py)
- [backend/app/services/llm/openai_provider.py](../backend/app/services/llm/openai_provider.py)

Both providers return a plain string response, which keeps the upper layers simple and provider-agnostic.

## Factory pattern

The factory is responsible for selecting the correct provider based on configuration.

It lives in:

- [backend/app/services/llm/llm_factory.py](../backend/app/services/llm/llm_factory.py)

This keeps the provider selection logic in one place instead of scattering `if/else` branches through the application.

## How to switch between Ollama and OpenAI

Set the provider in your environment:

```env
LLM_PROVIDER=ollama
```

or:

```env
LLM_PROVIDER=openai
```

Additional provider-specific configuration is available in:

- [backend/app/core/config.py](../backend/app/core/config.py)

### Ollama

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3:4b
```

### OpenAI

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=gpt-4o-mini
```

## How FastAPI communicates with the LLM

FastAPI routes do not speak to OpenAI or Ollama directly.

Instead:

1. The repository summary service uses the provider abstraction to generate a repository summary.
2. The repository QA service uses the same abstraction to generate answers from repository context.
3. Both services only know how to build a prompt and parse the response.

The flow is:

- [backend/app/api/v1/routes/repository.py](../backend/app/api/v1/routes/repository.py)
- [backend/app/services/repository_service.py](../backend/app/services/repository_service.py)
- [backend/app/services/openai_summary_service.py](../backend/app/services/openai_summary_service.py)
- [backend/app/api/v1/routes/ask.py](../backend/app/api/v1/routes/ask.py)
- [backend/app/services/repository_qa_service.py](../backend/app/services/repository_qa_service.py)

This keeps the API layer and the business logic independent from specific LLM vendors.

## How this scales to Anthropic, Gemini, or other providers

This architecture is intentionally extensible. To add a new provider later:

1. Create a new provider class that implements the shared interface.
2. Add a new provider-specific implementation for request formatting and response parsing.
3. Register it in the factory.

Because the rest of the application depends on the abstraction, no major refactoring should be needed when adding a new provider.

## Files created or updated

### New files

- [backend/app/services/llm/base.py](../backend/app/services/llm/base.py) - shared provider interface and error type
- [backend/app/services/llm/ollama_provider.py](../backend/app/services/llm/ollama_provider.py) - Ollama implementation using the local generation endpoint
- [backend/app/services/llm/openai_provider.py](../backend/app/services/llm/openai_provider.py) - existing OpenAI behavior wrapped behind the same interface
- [backend/app/services/llm/llm_factory.py](../backend/app/services/llm/llm_factory.py) - provider selection based on environment variables
- [backend/tests/test_llm_factory.py](../backend/tests/test_llm_factory.py) - regression tests for provider selection
- [docs/llm-provider-abstraction.md](llm-provider-abstraction.md) - this architecture guide

### Updated files

- [backend/app/core/config.py](../backend/app/core/config.py) - added provider and Ollama environment settings
- [backend/app/services/openai_summary_service.py](../backend/app/services/openai_summary_service.py) - now uses the provider abstraction
- [backend/app/services/repository_qa_service.py](../backend/app/services/repository_qa_service.py) - now uses the provider abstraction
- [docker-compose.yml](../docker-compose.yml) - passes provider settings into the API container
- [.env.example](../.env.example) - documents the new environment variables
