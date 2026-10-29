"""Provider-aware prompt token budgeting.

Provider selection (cursor, openai, ollama, …) is independent of tokenizer
selection. A matching tokenizer is used only when the configured model and
prompt format are verified; otherwise a documented conservative estimate is
applied with a safety margin.

Margins follow the counting strategy that actually runs: approximate UTF-8
estimates always reserve CONTEXT_MARGIN_TOKENS. Tiktoken is used for OpenAI
only when an encoding loads successfully, and counts Chat Completions message
framing for system+user (not every API option such as response_format).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Literal

from app.core.config import settings
from app.retrieval.packet import (
    conservative_byte_tokens,
    fits_model_context,
    qwen_prompt_tokens,
)

logger = logging.getLogger(__name__)

StrategyName = Literal["tiktoken", "qwen_chat_template", "conservative_bytes"]

# Ollama tags verified against the Qwen2.5 Instruct chat template and the
# Hugging Face tokenizer Qwen/Qwen2.5-1.5B-Instruct (see packet.qwen_prompt_tokens).
VERIFIED_QWEN_OLLAMA_MODELS = frozenset(
    {
        "qwen2.5:1.5b",
        "qwen2.5:1.5b-instruct",
    }
)

QWEN_HF_TOKENIZER_ID = "Qwen/Qwen2.5-1.5B-Instruct"

# OpenAI chat.completions message framing (system + user). Matches the common
# tiktoken cookbook constants for gpt-3.5/gpt-4/gpt-4o-family chat models.
# This does not model response_format, tools, or name fields — those are why
# tiktoken budgets set exact=False even when encoding succeeds.
OPENAI_TOKENS_PER_MESSAGE = 3
OPENAI_REPLY_PRIMING_TOKENS = 3


def normalize_ollama_model(model: str | None) -> str:
    return (model or settings.ollama_model or "").strip().lower()


def ollama_tokenizer_mode() -> str:
    """auto | qwen2.5 | conservative — from OLLAMA_TOKENIZER."""
    raw = (getattr(settings, "ollama_tokenizer", None) or "auto").strip().lower()
    if raw in {"auto", "qwen2.5", "qwen", "conservative"}:
        if raw == "qwen":
            return "qwen2.5"
        return raw
    return "auto"


def ollama_model_uses_qwen_tokenizer(model: str | None = None) -> bool:
    """True only when the Ollama model is verified to match the Qwen chat tokenizer."""
    mode = ollama_tokenizer_mode()
    if mode == "conservative":
        return False
    if mode == "qwen2.5":
        return True
    return normalize_ollama_model(model) in VERIFIED_QWEN_OLLAMA_MODELS


def conservative_margin_tokens() -> int:
    return settings.context_margin_tokens


@dataclass(frozen=True)
class PromptBudget:
    """Token accounting policy for one provider+model pair.

    `margin_tokens` matches `strategy` at resolve time. If a count call has to
    fall back to approximate bytes, `measure_*` returns the conservative margin
    so packing and repair stay aligned with the strategy that actually ran.
    """

    provider: str
    model: str
    strategy: StrategyName
    context_tokens: int
    margin_tokens: int
    exact: bool

    @property
    def uses_chat_roles(self) -> bool:
        return self.strategy in {"tiktoken", "qwen_chat_template"}

    def measure_text(self, text: str) -> tuple[int, int]:
        """Return (prompt_tokens, margin_tokens) for plain text."""
        if self.strategy == "tiktoken":
            counted = _tiktoken_text_tokens(text, self.model)
            if counted is not None:
                return counted, self.margin_tokens
            logger.warning(
                "tiktoken_unavailable falling back to conservative_bytes model=%s",
                self.model,
            )
            return conservative_byte_tokens(text), conservative_margin_tokens()
        return conservative_byte_tokens(text), self.margin_tokens

    def measure_chat(self, system: str, user: str) -> tuple[int, int]:
        """Return (prompt_tokens, margin_tokens) for a system+user chat turn."""
        if self.strategy == "qwen_chat_template":
            try:
                return qwen_prompt_tokens(system, user), self.margin_tokens
            except Exception as error:
                logger.warning(
                    "qwen_tokenizer_unavailable falling back to conservative_bytes error=%s",
                    type(error).__name__,
                )
                return (
                    conservative_byte_tokens(system + "\n" + user),
                    conservative_margin_tokens(),
                )
        if self.strategy == "tiktoken":
            counted = _tiktoken_chat_tokens(system, user, self.model)
            if counted is not None:
                return counted, self.margin_tokens
            logger.warning(
                "tiktoken_unavailable falling back to conservative_bytes model=%s",
                self.model,
            )
            return (
                conservative_byte_tokens(system + "\n" + user),
                conservative_margin_tokens(),
            )
        return conservative_byte_tokens(system + "\n" + user), self.margin_tokens

    def count_text(self, text: str) -> int:
        tokens, _margin = self.measure_text(text)
        return tokens

    def count_chat(self, system: str, user: str) -> int:
        tokens, _margin = self.measure_chat(system, user)
        return tokens

    def pack_counter(self, *, system_prefix: str | None = None) -> Callable[[str], int]:
        """Counter for pack_evidence / final packet checks.

        When `system_prefix` is set (all live answer paths), count the same
        system+user pair that packing, repair, and the provider call use.
        Chat strategies apply model framing inside measure_chat; conservative
        strategies join with a newline. When a count falls back to approximate
        bytes, the returned total is inflated by any missing safety margin so
        pack_evidence (which receives a fixed margin_tokens) stays conservative.
        """
        resolved_margin = self.margin_tokens
        if system_prefix is not None:
            system = system_prefix

            def count_chat_body(body: str) -> int:
                tokens, used_margin = self.measure_chat(system, body)
                return tokens + max(0, used_margin - resolved_margin)

            return count_chat_body

        def count_text_body(body: str) -> int:
            tokens, used_margin = self.measure_text(body)
            return tokens + max(0, used_margin - resolved_margin)

        return count_text_body

    def fits(self, prompt_tokens: int, output_tokens: int, margin_tokens: int | None = None) -> bool:
        margin = self.margin_tokens if margin_tokens is None else margin_tokens
        return fits_model_context(
            prompt_tokens,
            output_tokens,
            margin,
            self.context_tokens,
        )

    def repair_exceeds(self, system_message: str, repair_user: str, max_output_tokens: int) -> bool:
        prompt_tokens, margin = self.measure_chat(system_message, repair_user)
        return not self.fits(prompt_tokens, max_output_tokens, margin)


def resolve_prompt_budget(
    provider: str | None = None,
    model: str | None = None,
) -> PromptBudget:
    """Build the budgeting policy for a provider and optional model override."""
    name = (provider or settings.llm_provider or "cursor").strip().lower()
    if name == "claude-code":
        name = "claude"

    if name == "ollama":
        ollama_model = (model or settings.ollama_model or "").strip()
        if ollama_model_uses_qwen_tokenizer(ollama_model):
            return PromptBudget(
                provider="ollama",
                model=ollama_model or settings.ollama_model,
                strategy="qwen_chat_template",
                context_tokens=settings.ollama_num_ctx,
                # Exact Qwen counts still reserve CONTEXT_MARGIN_TOKENS for runtime drift.
                margin_tokens=conservative_margin_tokens(),
                exact=True,
            )
        return PromptBudget(
            provider="ollama",
            model=ollama_model or settings.ollama_model,
            strategy="conservative_bytes",
            context_tokens=settings.ollama_num_ctx,
            margin_tokens=conservative_margin_tokens(),
            exact=False,
        )

    if name == "openai":
        openai_model = (model or settings.openai_model or "").strip() or settings.openai_model
        if _tiktoken_encoding(openai_model) is not None:
            # Chat framing is included in counts. exact=False because API options
            # such as response_format are not tokenized here; margin stays 0 only
            # while tiktoken remains the active strategy (see measure_* fallback).
            return PromptBudget(
                provider="openai",
                model=openai_model,
                strategy="tiktoken",
                context_tokens=settings.llm_context_tokens,
                margin_tokens=0,
                exact=False,
            )
        return PromptBudget(
            provider="openai",
            model=openai_model,
            strategy="conservative_bytes",
            context_tokens=settings.llm_context_tokens,
            margin_tokens=conservative_margin_tokens(),
            exact=False,
        )

    # Cursor and other CLI providers: no local tokenizer for the routed model.
    configured = model or _default_model_for_provider(name)
    return PromptBudget(
        provider=name,
        model=configured,
        strategy="conservative_bytes",
        context_tokens=settings.llm_context_tokens,
        margin_tokens=conservative_margin_tokens(),
        exact=False,
    )


def _default_model_for_provider(provider: str) -> str:
    if provider == "cursor":
        return settings.cursor_model
    if provider == "claude":
        return settings.claude_model
    if provider == "codex":
        return settings.codex_model
    if provider == "opencode":
        return settings.opencode_model
    if provider == "zai":
        return settings.zai_model
    if provider == "copilot":
        return settings.copilot_model
    if provider == "openai":
        return settings.openai_model
    if provider == "ollama":
        return settings.ollama_model
    return settings.cursor_model


def _tiktoken_encoding(model: str):
    try:
        import tiktoken

        return tiktoken.encoding_for_model(model or settings.openai_model)
    except Exception:
        return None


def _tiktoken_text_tokens(text: str, model: str) -> int | None:
    if not text:
        return 0
    encoding = _tiktoken_encoding(model)
    if encoding is None:
        return None
    try:
        return len(encoding.encode(text))
    except Exception:
        return None


def _tiktoken_chat_tokens(system: str, user: str, model: str) -> int | None:
    """Count system+user Chat Completions framing. Not a full request tokenizer."""
    encoding = _tiktoken_encoding(model)
    if encoding is None:
        return None
    try:
        tokens = OPENAI_REPLY_PRIMING_TOKENS
        for content in (system, user):
            tokens += OPENAI_TOKENS_PER_MESSAGE
            tokens += len(encoding.encode(content or ""))
        return tokens
    except Exception:
        return None
