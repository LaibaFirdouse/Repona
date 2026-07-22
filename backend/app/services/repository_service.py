from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

from app.crud.repository_crud import RepositoryCRUD
from app.schemas.repository import (
    DirectoryEntry,
    RepositoryCreateRequest,
    RepositoryCreateResponse,
    RepositoryMetadata,
    TechnologyDetection,
)
from app.services.neo4j_graph_service import Neo4jGraphService, Neo4jGraphServiceError
from app.services.openai_summary_service import (
    OpenAISummaryService,
    OpenAISummaryServiceError,
)
from app.services.openai_summary_service import OpenAISummaryService

from app.schemas.repository import (
    RepositorySummary,
    RepositorySummaryResult,
    TokenUsage,
)
from app.services.chunk_service import ChunkService
from app.services.embedding_service import EmbeddingService
from app.services.code_chunk_service import CodeChunkService


class RepositoryServiceError(Exception):
    pass


class RepositoryService:
    ignored_directories = {
        ".git",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }

    def __init__(
        self,
        repository_crud: RepositoryCRUD | None = None,
        summary_service: OpenAISummaryService | None = None,
        graph_service: Neo4jGraphService | None = None,
        chunk_service: ChunkService | None = None,
        embedding_service: EmbeddingService | None = None,
        code_chunk_service: CodeChunkService | None = None,
    ) -> None:
        self.repository_crud = repository_crud or RepositoryCRUD()
        self.summary_service = summary_service or OpenAISummaryService()
        self.graph_service = graph_service or Neo4jGraphService()

        self.chunk_service = chunk_service or ChunkService()
        self.embedding_service = embedding_service or EmbeddingService()
        self.code_chunk_service = code_chunk_service or CodeChunkService()
        
    def create_repository(
        self,
        request: RepositoryCreateRequest,
        db: Session,
    ) -> RepositoryCreateResponse:
        repo_url = str(request.repo_url)

        print("\n========== STEP 1 ==========")
        print("Starting repository analysis")

        with tempfile.TemporaryDirectory(
            prefix="repo-intelligence-"
        ) as temporary_directory:
            repository_path = Path(temporary_directory) / "repository"

            self.clone_repository(repo_url, repository_path)

            print("\n========== STEP 2 ==========")
            print("Repository cloned")

            metadata = self.read_repository_metadata(repository_path)

            print("\n========== STEP 3 ==========")
            print("Metadata extracted")

            graph = self.graph_service.build_graph(
                repository_path=repository_path,
                ignored_directories=self.ignored_directories,
            )
            print("\n========== STEP 4 ==========")
            print("Chunking repository...")

            chunks = self.chunk_service.create_chunks(repository_path)

            print(f"Generated {len(chunks)} chunks.")

            print("\n========== STEP 5 ==========")
            print("Generating embeddings...")

            chunk_embeddings = self.embedding_service.generate_embeddings(chunks)

            print(f"Generated {len(chunk_embeddings)} embeddings.")

        print("\n========== STEP 4 ==========")
        print("Graph built")
        print(graph.stats())

        print("\n========== STEP 5 ==========")
        print("Generating repository summary...")

        summary = RepositorySummary(
            executive_summary=(
                f"This repository contains {metadata.file_count} files across "
                f"{metadata.directory_count} directories."
            ),
            main_technologies=[
                technology.name for technology in metadata.technologies
            ],
            architecture_observations=[
                "Repository successfully cloned.",
                "Metadata extracted from repository structure.",
                "Technology stack detected automatically.",
            ],
            notable_directories=[
                entry.path
                for entry in metadata.directory_structure[:5]
            ],
            next_steps=[
                "Build vector embeddings.",
                "Enable repository question answering.",
                "Perform semantic code search.",
            ],
        )

        summary_result = RepositorySummaryResult(
            summary=summary,
            token_usage=TokenUsage(),
        )

        print("\n========== STEP 6 ==========")
        print("Repository summary generated.")

        try:
            repository, analysis_report = self.repository_crud.save_repository_analysis(
                db=db,
                repo_url=repo_url,
                status="analyzed",
                file_count=metadata.file_count,
                directory_count=metadata.directory_count,
                ignored_directories=metadata.ignored_directories,
                technologies=self.serialize_model_list(metadata.technologies),
                directory_structure=self.serialize_model_list(
                    metadata.directory_structure
                ),
                summary=self.serialize_model(summary_result.summary),
                token_usage=self.serialize_model(summary_result.token_usage),
            )

            print("\n========== STEP 7 ==========")
            print("Saved to PostgreSQL")
            print("\n========== STEP 8 ==========")
            print("Saving code chunks...")

            self.code_chunk_service.save_chunks(
                repository_id=repository.id,
                chunk_embeddings=chunk_embeddings,
                db=db,
            )
            db.commit()
            print("Committed code chunks")

            print("Code chunks saved successfully.")

        except SQLAlchemyError as error:
            db.rollback()
            raise RepositoryServiceError(
                "Unable to store repository analysis."
            ) from error

        # try:
        #     graph_stats = self.graph_service.store_graph(
        #         repository_id=repository.id,
        #         repo_url=repo_url,
        #         graph=graph,
        #     )
        try:
           graph_stats = self.graph_service.store_graph(
            repository_id=repository.id,
            repo_url=repo_url,
            graph=graph,
        )
           print("Neo4j write successful")

        except Exception:
             import traceback
             traceback.print_exc()
             raise

        # print("\n========== STEP 8 ==========")
        # print("Saved graph to Neo4j")

        except Neo4jGraphServiceError as error:
            raise RepositoryServiceError(str(error)) from error

        print("\n========== STEP 9 ==========")
        print("Returning response")

        return RepositoryCreateResponse(
            repository_id=repository.id,
            analysis_report_id=analysis_report.id,
            repo_url=request.repo_url,
            status="analyzed",
            message="Repository metadata extracted successfully.",
            metadata=metadata,
            summary=summary_result.summary,
            token_usage=summary_result.token_usage,
            graph=graph_stats,
        )
    def serialize_model(self, model) -> dict:
        if hasattr(model, "model_dump"):
            return model.model_dump()
        return model.dict()

    def serialize_model_list(self, models: list) -> list[dict]:
        serialized_models = []

        for model in models:
            serialized_models.append(self.serialize_model(model))

        return serialized_models

    def clone_repository(self, repo_url: str, destination: Path) -> None:
        command = [
            "git",
            "clone",
            "--depth",
            "1",
            repo_url,
            str(destination),
        ]

        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError as error:
            raise RepositoryServiceError(
                "Git is not installed or is not available on PATH."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise RepositoryServiceError("Repository clone timed out.") from error
        except subprocess.CalledProcessError as error:
            error_message = error.stderr.strip() or "Unable to clone repository."
            raise RepositoryServiceError(error_message) from error

    def read_repository_metadata(self, repository_path: Path) -> RepositoryMetadata:
        directory_structure = self.build_directory_structure(
            repository_path, repository_path
        )
        file_count = self.count_files(repository_path)
        directory_count = self.count_directories(repository_path)
        technologies = self.detect_technologies(repository_path)

        return RepositoryMetadata(
            file_count=file_count,
            directory_count=directory_count,
            ignored_directories=sorted(self.ignored_directories),
            technologies=technologies,
            directory_structure=directory_structure,
        )

    def build_directory_structure(
        self, directory: Path, repository_path: Path
    ) -> list[DirectoryEntry]:
        entries = []

        for path in self.iter_visible_children(directory):
            relative_path = path.relative_to(repository_path).as_posix()
            if path.is_dir():
                entries.append(
                    DirectoryEntry(
                        name=path.name,
                        path=relative_path,
                        kind="directory",
                        children=self.build_directory_structure(path, repository_path),
                    )
                )
            else:
                entries.append(
                    DirectoryEntry(
                        name=path.name,
                        path=relative_path,
                        kind="file",
                    )
                )

        return entries

    def iter_visible_children(self, directory: Path) -> list[Path]:
        return sorted(
            (child for child in directory.iterdir() if not self.should_ignore(child)),
            key=lambda child: (child.is_file(), child.name.lower()),
        )

    def should_ignore(self, path: Path) -> bool:
        return path.is_dir() and path.name in self.ignored_directories

    def count_files(self, repository_path: Path) -> int:
        file_count = 0

        for _, directory_names, file_names in os.walk(repository_path):
            directory_names[:] = [
                directory_name
                for directory_name in directory_names
                if directory_name not in self.ignored_directories
            ]
            file_count += len(file_names)

        return file_count

    def count_directories(self, repository_path: Path) -> int:
        directory_count = 0

        for _, directory_names, _ in os.walk(repository_path):
            visible_directory_names = [
                directory_name
                for directory_name in directory_names
                if directory_name not in self.ignored_directories
            ]
            directory_count += len(visible_directory_names)
            directory_names[:] = visible_directory_names

        return directory_count

    def detect_technologies(self, repository_path: Path) -> list[TechnologyDetection]:
        detections = []

        package_json_path = repository_path / "package.json"
        if package_json_path.exists():
            detections.extend(self.detect_from_package_json(package_json_path))

        requirements_path = repository_path / "requirements.txt"
        if requirements_path.exists():
            detections.extend(self.detect_from_requirements(requirements_path))

        pyproject_path = repository_path / "pyproject.toml"
        if pyproject_path.exists():
            detections.extend(self.detect_from_pyproject(pyproject_path))

        return self.deduplicate_technologies(detections)

    def detect_from_package_json(
        self, package_json_path: Path
    ) -> list[TechnologyDetection]:
        try:
            package_data = json.loads(package_json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return []

        dependencies = {
            **package_data.get("dependencies", {}),
            **package_data.get("devDependencies", {}),
        }
        detections = [
            TechnologyDetection(
                name="Node.js", category="runtime", source="package.json"
            )
        ]

        package_technology_map = {
            "@nestjs/core": ("NestJS", "backend framework"),
            "@vitejs/plugin-react": ("Vite", "frontend tooling"),
            "angular": ("Angular", "frontend framework"),
            "express": ("Express", "backend framework"),
            "fastify": ("Fastify", "backend framework"),
            "next": ("Next.js", "frontend framework"),
            "react": ("React", "frontend library"),
            "typescript": ("TypeScript", "language"),
            "vite": ("Vite", "frontend tooling"),
            "vue": ("Vue", "frontend framework"),
        }

        for package_name, (technology_name, category) in package_technology_map.items():
            if package_name in dependencies:
                detections.append(
                    TechnologyDetection(
                        name=technology_name,
                        category=category,
                        source="package.json",
                    )
                )

        return detections

    def detect_from_requirements(
        self, requirements_path: Path
    ) -> list[TechnologyDetection]:
        try:
            requirements = requirements_path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError):
            return []

        detections = [
            TechnologyDetection(
                name="Python", category="language", source="requirements.txt"
            )
        ]
        requirement_technology_map = {
            "django": ("Django", "backend framework"),
            "fastapi": ("FastAPI", "backend framework"),
            "flask": ("Flask", "backend framework"),
            "pandas": ("Pandas", "data library"),
            "pytest": ("Pytest", "testing"),
            "sqlalchemy": ("SQLAlchemy", "database toolkit"),
        }

        for package_name, (
            technology_name,
            category,
        ) in requirement_technology_map.items():
            if package_name in requirements:
                detections.append(
                    TechnologyDetection(
                        name=technology_name,
                        category=category,
                        source="requirements.txt",
                    )
                )

        return detections

    def detect_from_pyproject(self, pyproject_path: Path) -> list[TechnologyDetection]:
        detections = [
            TechnologyDetection(
                name="Python", category="language", source="pyproject.toml"
            )
        ]

        if tomllib is None:
            return detections

        try:
            pyproject_data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return detections

        project_dependencies = pyproject_data.get("project", {}).get("dependencies", [])
        dependency_text = "\n".join(project_dependencies).lower()
        build_backend = pyproject_data.get("build-system", {}).get("build-backend", "")

        if "poetry" in build_backend:
            detections.append(
                TechnologyDetection(
                    name="Poetry", category="packaging", source="pyproject.toml"
                )
            )
        if "setuptools" in build_backend:
            detections.append(
                TechnologyDetection(
                    name="Setuptools", category="packaging", source="pyproject.toml"
                )
            )
        if "fastapi" in dependency_text:
            detections.append(
                TechnologyDetection(
                    name="FastAPI",
                    category="backend framework",
                    source="pyproject.toml",
                )
            )
        if "django" in dependency_text:
            detections.append(
                TechnologyDetection(
                    name="Django", category="backend framework", source="pyproject.toml"
                )
            )
        if "flask" in dependency_text:
            detections.append(
                TechnologyDetection(
                    name="Flask", category="backend framework", source="pyproject.toml"
                )
            )

        return detections

    def deduplicate_technologies(
        self,
        detections: list[TechnologyDetection],
    ) -> list[TechnologyDetection]:
        unique_detections = {}

        for detection in detections:
            key = (detection.name, detection.category, detection.source)
            unique_detections[key] = detection

        return sorted(
            unique_detections.values(),
            key=lambda detection: (
                detection.category,
                detection.name,
                detection.source,
            ),
        )

    def build_repository_id(self, repo_url: str) -> str:
        normalized_url = repo_url.rstrip("/")
        repository_name = normalized_url.split("/")[-1].replace(".git", "")
        return f"repo-{repository_name.lower()}"
