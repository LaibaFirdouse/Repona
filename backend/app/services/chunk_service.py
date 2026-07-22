import os
from pathlib import Path

from app.schemas import Chunk


class ChunkService:
    """
    Reads a repository from disk and splits supported files
    into overlapping chunks.

    Responsibilities:
    - Walk repository
    - Ignore unwanted files/directories
    - Read source files
    - Split into overlapping chunks

    Does NOT:
    - Generate embeddings
    - Save to database
    - Call an LLM
    """

    IGNORE_DIRS = {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".cache",
        ".idea",
        ".vscode",
        "coverage",
    }

    IGNORE_FILES = {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "poetry.lock",
        "Pipfile.lock",
    }

    SUPPORTED_EXTENSIONS = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".json",
        ".md",
        ".yml",
        ".yaml",
        ".toml",
        ".sql",
        ".sh",
    }

    SUPPORTED_FILENAMES = {
        "Dockerfile",
    }

    def __init__(
        self,
        chunk_size: int = 100,
        overlap: int = 20,
    ):
        if overlap >= chunk_size:
            raise ValueError(
                "Overlap must be smaller than chunk size."
            )

        self.chunk_size = chunk_size
        self.overlap = overlap

    def create_chunks(
        self,
        repository_path: Path,
    ) -> list[Chunk]:

        if not repository_path.exists():
            raise FileNotFoundError(
                f"Repository path does not exist: {repository_path}"
            )

        if not repository_path.is_dir():
            raise ValueError(
                f"Repository path is not a directory: {repository_path}"
            )

        chunks: list[Chunk] = []

        for root, dirs, files in os.walk(repository_path):

            # Prevent os.walk from entering ignored directories.
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in self.IGNORE_DIRS
            ]

            root_path = Path(root)

            for filename in files:

                file_path = root_path / filename

                if file_path.name in self.IGNORE_FILES:
                    continue

                if (
                    file_path.suffix.lower()
                    not in self.SUPPORTED_EXTENSIONS
                    and file_path.name
                    not in self.SUPPORTED_FILENAMES
                ):
                    continue

                try:
                    content = file_path.read_text(
                        encoding="utf-8",
                        errors="ignore",
                    )
                except (PermissionError, OSError):
                    continue

                lines = content.splitlines()

                if not lines:
                    continue

                relative_path = str(
                    file_path.relative_to(repository_path)
                )

                start = 0
                chunk_index = 0

                while start < len(lines):

                    end = min(
                        start + self.chunk_size,
                        len(lines),
                    )

                    chunks.append(
                        Chunk(
                            file_path=relative_path,
                            chunk_index=chunk_index,
                            start_line=start + 1,
                            end_line=end,
                            content="\n".join(
                                lines[start:end]
                            ),
                        )
                    )

                    chunk_index += 1
                    start += (
                        self.chunk_size
                        - self.overlap
                    )

        return chunks