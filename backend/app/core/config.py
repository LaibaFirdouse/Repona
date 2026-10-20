import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _get_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _get_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return float(raw_value)


def _get_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return int(raw_value)


def normalize_database_url(url: str) -> str:
    """Map Neon-style postgres URLs to SQLAlchemy's psycopg driver."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def _database_url_from_env() -> str:
    default = "postgresql+psycopg://postgres:postgres@127.0.0.1:5433/repona"
    return normalize_database_url(os.getenv("DATABASE_URL", default))


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Repository Intelligence Agent API")
    app_env: str = os.getenv("APP_ENV", "local")
    app_version: str = os.getenv("APP_VERSION", "0.1.0")
    api_v1_prefix: str = os.getenv("API_V1_PREFIX", "/api/v1")
    database_url: str = _database_url_from_env()
    llm_provider: str = os.getenv("LLM_PROVIDER", "cursor").strip().lower()
    ollama_base_url: str = os.getenv(
        "OLLAMA_BASE_URL",
        "http://localhost:11434",
    )
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
    ollama_timeout: int = _get_int_env("OLLAMA_TIMEOUT", 180)
    # Runtime window sent as num_ctx. This is separate from the evidence byte caps and the output cap.
    ollama_num_ctx: int = _get_int_env("OLLAMA_NUM_CTX", 4096)
    context_margin_tokens: int = _get_int_env("CONTEXT_MARGIN_TOKENS", 256)
    cursor_model: str = os.getenv("CURSOR_MODEL", "composer-2.5-fast")
    cursor_timeout: int = _get_int_env("CURSOR_TIMEOUT", 180)
    cursor_api_key: str = os.getenv("CURSOR_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "sonnet")
    claude_timeout: int = _get_int_env("CLAUDE_TIMEOUT", 180)
    codex_model: str = os.getenv("CODEX_MODEL", "gpt-5.6-luna")
    codex_timeout: int = _get_int_env("CODEX_TIMEOUT", 180)
    opencode_model: str = os.getenv("OPENCODE_MODEL", "opencode/big-pickle")
    opencode_timeout: int = _get_int_env("OPENCODE_TIMEOUT", 180)
    zai_base_url: str = os.getenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
    zai_model: str = os.getenv("ZAI_MODEL", "glm-5.3")
    zai_timeout: int = _get_int_env("ZAI_TIMEOUT", 60)
    copilot_model: str = os.getenv("COPILOT_MODEL", "auto")
    copilot_timeout: int = _get_int_env("COPILOT_TIMEOUT", 180)
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_temperature: float = _get_float_env("OPENAI_TEMPERATURE", 0.2)
    openai_max_output_tokens: int = _get_int_env("OPENAI_MAX_OUTPUT_TOKENS", 800)
    openai_timeout: float = _get_float_env("OPENAI_TIMEOUT", 60.0)
    llm_context_tokens: int = _get_int_env("LLM_CONTEXT_TOKENS", 128000)
    llm_max_claims: int = _get_int_env("LLM_MAX_CLAIMS", 16)
    llm_max_response_bytes: int = _get_int_env("LLM_MAX_RESPONSE_BYTES", 65536)
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str | None = os.getenv("NEO4J_PASSWORD")
    qdrant_url: str = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "")
    credential_key: str = os.getenv("CREDENTIAL_KEY", "")
    retrieval_lane_limit: int = _get_int_env("RETRIEVAL_LANE_LIMIT", 8)
    retrieval_vector_raw_limit: int = _get_int_env("RETRIEVAL_VECTOR_RAW_LIMIT", 64)
    retrieval_vector_max_file_filter: int = _get_int_env("RETRIEVAL_VECTOR_MAX_FILE_FILTER", 256)
    retrieval_light_snapshot_validate: bool = _get_bool_env("RETRIEVAL_LIGHT_SNAPSHOT_VALIDATE", True)
    embed_warmup_on_startup: bool = _get_bool_env("EMBED_WARMUP_ON_STARTUP", True)
    progressive_indexing: bool = _get_bool_env("PROGRESSIVE_INDEXING", True)
    progressive_complete_deferred_in_ingest: bool = _get_bool_env("PROGRESSIVE_COMPLETE_DEFERRED_IN_INGEST", True)
    embed_catalogue_before_activate: bool = _get_bool_env("EMBED_CATALOGUE_BEFORE_ACTIVATE", True)
    lazy_embed_on_query: bool = _get_bool_env("LAZY_EMBED_ON_QUERY", True)
    lazy_embed_max_chunks: int = _get_int_env("LAZY_EMBED_MAX_CHUNKS", 32)
    query_planner_enabled: bool = _get_bool_env("QUERY_PLANNER_ENABLED", True)
    lexical_min_hits: int = _get_int_env("LEXICAL_MIN_HITS", 2)
    retrieval_span_limit: int = _get_int_env("RETRIEVAL_SPAN_LIMIT", 12)
    retrieval_edge_limit: int = _get_int_env("RETRIEVAL_EDGE_LIMIT", 24)
    answer_overview_span_bonus: int = _get_int_env("ANSWER_OVERVIEW_SPAN_BONUS", 10)
    answer_evidence_rounds: int = _get_int_env("ANSWER_EVIDENCE_ROUNDS", 1)
    answer_more_evidence_max_queries: int = _get_int_env("ANSWER_MORE_EVIDENCE_MAX_QUERIES", 4)
    answer_more_evidence_max_paths: int = _get_int_env("ANSWER_MORE_EVIDENCE_MAX_PATHS", 6)
    debug: bool = _get_bool_env("DEBUG", True)
    rate_limit_enabled: bool = _get_bool_env("RATE_LIMIT_ENABLED", True)
    rate_limit_trust_proxy: bool = _get_bool_env("RATE_LIMIT_TRUST_PROXY", False)
    rate_limit_ask_per_minute: int = _get_int_env("RATE_LIMIT_ASK_PER_MINUTE", 12)
    rate_limit_index_per_hour: int = _get_int_env("RATE_LIMIT_INDEX_PER_HOUR", 360)
    client_session_ttl_hours: int = _get_int_env("CLIENT_SESSION_TTL_HOURS", 48)
    client_cookie_secure: bool = _get_bool_env("CLIENT_COOKIE_SECURE", False)
    server_logs_enabled: bool = _get_bool_env("SERVER_LOGS_ENABLED", True)
    server_log_level: str = os.getenv("SERVER_LOG_LEVEL", "INFO").strip().upper()
    server_log_buffer_max: int = _get_int_env("SERVER_LOG_BUFFER_MAX", 250)
    server_logs_token: str = os.getenv("SERVER_LOGS_TOKEN", "")


settings = Settings()
