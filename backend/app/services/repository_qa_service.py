from __future__ import annotations

import json
import re
from json import JSONDecoder

from sqlalchemy.orm import Session

from app.crud.repository_crud import RepositoryCRUD
from app.schemas.qa import (
    RepositoryQuestionAnswer,
    RepositoryQuestionRequest,
    RepositoryQuestionResponse,
)
from app.schemas.repository import TokenUsage
from app.services.llm.base import BaseLLMProvider, LLMProviderError
from app.services.llm.llm_factory import LLMFactory
from app.services.neo4j_graph_service import Neo4jGraphService, Neo4jGraphServiceError
from app.services.retrieval_service import RetrievalService


class RepositoryQAServiceError(Exception):
    pass


class RepositoryQAService:
    graph_keywords = {
        "architecture",
        "call",
        "calls",
        "central",
        "depend",
        "dependency",
        "dependencies",
        "file",
        "files",
        "flow",
        "graph",
        "import",
        "imports",
        "module",
        "modules",
        "service",
        "services",
        "use",
        "uses",
    }

    def __init__(
        self,
        repository_crud: RepositoryCRUD | None = None,
        graph_service: Neo4jGraphService | None = None,
        llm_provider: BaseLLMProvider | None = None,
        retrieval_service: RetrievalService | None = None,
    ) -> None:
        self.repository_crud = repository_crud or RepositoryCRUD()
        self.graph_service = graph_service or Neo4jGraphService()
        self.llm_provider = llm_provider or LLMFactory.create_provider()
        self.retrieval_service = (
            retrieval_service or RetrievalService()
        )

    def answer_question(
        self,
        request: RepositoryQuestionRequest,
        db: Session,
    ) -> RepositoryQuestionResponse:
        repository = self.repository_crud.get_repository_by_id(
            db, request.repository_id
        )
        if repository is None:
            raise RepositoryQAServiceError("Repository was not found.")

        analysis_report = self.repository_crud.get_latest_analysis_report(
            db,
            request.repository_id,
        )
        if analysis_report is None:
            raise RepositoryQAServiceError("Repository has no analysis report yet.")
        retrieved_chunks = self.retrieval_service.retrieve_chunks(
            repository_id=request.repository_id,
            query=request.question,
            db=db,
        )
        retrieved_context = "\n\n".join(
            f"File: {chunk.file_path}\n{chunk.content}"
            for chunk in retrieved_chunks
        )
        # Cap the context sent to the LLM so prompt prefill stays fast on
        # modest hardware; oversized contexts just make generation slower
        # without improving the answer.
        max_context_chars = 12000
        if len(retrieved_context) > max_context_chars:
            print(
                f"Truncating retrieved context: "
                f"{len(retrieved_context)} -> {max_context_chars} chars"
            )
            retrieved_context = (
                retrieved_context[:max_context_chars]
                + "\n[...retrieved context truncated...]"
            )
        print(f"Retrieved chunks: {len(retrieved_chunks)}")
        print(f"Retrieved context chars: {len(retrieved_context)}")

        graph_context_used = self.should_query_graph(request.question)
        graph_context = {}
        if graph_context_used:
            try:
                graph_context = self.graph_service.query_repository_context(
                    request.repository_id
                )
            except Neo4jGraphServiceError as error:
                raise RepositoryQAServiceError(str(error)) from error
        graph_answer = self.try_graph_answer(
            request.repository_id,
            request.question,
        )

        if graph_answer is not None:
            return RepositoryQuestionResponse(
                repository_id=request.repository_id,
                question=request.question,
                answer=graph_answer,
                token_usage=TokenUsage(),
            )

        system_prompt = self.build_system_prompt()
        user_prompt = self.build_user_prompt(
            question=request.question,
            repo_url=repository.repo_url,
            analysis_report=analysis_report,
            graph_context=graph_context,
            graph_context_used=graph_context_used,
            retrieved_context=retrieved_context,
        )
        print(f"User prompt chars: {len(user_prompt)}")
        print("\n========== RETRIEVED CODE ==========\n")
        print(retrieved_context[:2000])  # print first 2000 characters
        print("\n===================================\n")
        answer, token_usage = self.call_llm(system_prompt, user_prompt)

        return RepositoryQuestionResponse(
            repository_id=request.repository_id,
            question=request.question,
            answer=answer,
            token_usage=token_usage,
        )

    def should_query_graph(self, question: str) -> bool:
        normalized_question = question.lower()
        return any(keyword in normalized_question for keyword in self.graph_keywords)
    def try_graph_answer(
        self,
        repository_id: str,
        question: str,
    ) -> RepositoryQuestionAnswer | None:
        question = question.lower()

        if "how many files" in question:
            count = self.graph_service.get_file_count(repository_id)
            return RepositoryQuestionAnswer(
                answer=f"The repository contains {count} files.",
                confidence="high",
                sources=["Neo4j File nodes"],
                graph_context_used=True,
            )

        if "how many modules" in question:
            count = self.graph_service.get_module_count(repository_id)
            return RepositoryQuestionAnswer(
                answer=f"The repository contains {count} modules.",
                confidence="high",
                sources=["Neo4j Module nodes"],
                graph_context_used=True,
            )

        if "list modules" in question or "what modules" in question:
            modules = self.graph_service.get_modules(repository_id)
            return RepositoryQuestionAnswer(
                answer=", ".join(modules),
                confidence="high",
                sources=["Neo4j Module nodes"],
                graph_context_used=True,
            )
        if "files in module" in question or "list files in" in question:
            words = question.split()

            module = words[-1]

            files = self.graph_service.get_files_in_module(
                repository_id,
                module,
            )

            return RepositoryQuestionAnswer(
                answer=(
                       f"Module '{module}' contains {len(files)} files:\n\n"
                       + "\n".join(f"- {file}" for file in files)
                ),
                confidence="high",
                sources=["Neo4j Module -> File"],
                graph_context_used=True,
            )
        if "module" in question and (
             "use" in question
             or "depend" in question
        ):
            # words = question.split()

            # module = words[-1]

            modules = self.graph_service.get_modules(repository_id)

            module = None

            for candidate in modules:
                if re.search(rf"\b{re.escape(candidate)}\b", question, re.IGNORECASE):
                    module = candidate
                    break

            if module is None:
                return None

            modules = self.graph_service.get_module_dependencies(
                repository_id,
                module,
            )

            if not modules:
                answer = f"Module '{module}' does not use any other modules."
            else:
                answer = (
                    f"Module '{module}' uses {len(modules)} module(s):\n\n"
                    + "\n".join(f"- {m}" for m in modules)
                )

            return RepositoryQuestionAnswer(
                answer=answer,
                confidence="high",
                sources=["Neo4j Module -> USES -> Module"],
                graph_context_used=True,
            )
        if "central" in question or "imported the most" in question:
            files = self.graph_service.get_central_files(repository_id)

            if not files:
                return None

            lines = [
                f"{item['file']} ({item['imports']} imports)"
                for item in files
            ]

            return RepositoryQuestionAnswer(
                answer="\n".join(lines),
                confidence="high",
                sources=["Neo4j IMPORTS graph"],
                graph_context_used=True,
            )

        return None

    def build_system_prompt(self) -> str:
        return (
            "You are a repository question answering assistant.\n"
            "Answer the user's question using the retrieved repository code.\n"
            "Return EXACTLY one valid JSON object with EXACTLY these keys:\n"
            "- \"answer\": a string, the direct answer to the question\n"
            "- \"confidence\": one of \"high\", \"medium\", or \"low\"\n"
            "- \"sources\": a list of strings naming the files or context used\n"
            "- \"graph_context_used\": a boolean, true or false\n"
            "Do NOT include any other keys.\n"
            "Do NOT wrap it in markdown.\n"
            "Do NOT explain your answer.\n"
            "Do NOT add any text before or after the JSON.\n"
            "The first character of your response must be '{'.\n"
            "The last character of your response must be '}'."
        )

    def build_user_prompt(
        self,
        question: str,
        repo_url: str,
        analysis_report,
        graph_context: dict,
        graph_context_used: bool,
        retrieved_context: str,
    ) -> str:
        prompt_payload = {
            "question": question,
            "retrieved_code": retrieved_context,
            "required_json_shape": {
                "answer": "string: the direct answer to the question",
                "confidence": "string: one of 'high', 'medium', or 'low'",
                "sources": "list of strings: files or context used",
                "graph_context_used": "boolean: true or false",
            },
        }
        return json.dumps(prompt_payload, indent=2)

    def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[RepositoryQuestionAnswer, TokenUsage]:
        combined_prompt = self.build_provider_prompt(system_prompt, user_prompt)

        print(f"Combined prompt chars: {len(combined_prompt)}")

        try:
            response_content = self.llm_provider.generate(combined_prompt)
            print("\n========== RAW LLM RESPONSE ==========\n")
            print(response_content)
            print("\n=====================================\n")
        except LLMProviderError as error:
            raise RepositoryQAServiceError(str(error)) from error

        answer = self.parse_answer(response_content)
        token_usage = TokenUsage()
        return answer, token_usage

    def build_provider_prompt(self, system_prompt: str, user_prompt: str) -> str:
        return f"SYSTEM PROMPT:\n{system_prompt}\n\nUSER PROMPT:\n{user_prompt}"

    def parse_answer(self, response_content: str) -> RepositoryQuestionAnswer:
        try:
            response_content = response_content.strip()

            if response_content.startswith("```json"):
                response_content = response_content.removeprefix("```json").strip()

            if response_content.startswith("```"):
                response_content = response_content.removeprefix("```").strip()

            if response_content.endswith("```"):
                response_content = response_content.removesuffix("```").strip()
            decoder = JSONDecoder()

            data, _ = decoder.raw_decode(response_content)
        except json.JSONDecodeError as error:
            raise RepositoryQAServiceError("LLM returned invalid JSON.") from error

        try:
            return RepositoryQuestionAnswer(**data)
        except ValueError as error:
            raise RepositoryQAServiceError(
                "OpenAI answer did not match the expected shape."
            ) from error
