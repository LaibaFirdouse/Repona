from dataclasses import dataclass


@dataclass(slots=True)
class Chunk:
    """
    Represents a chunk of code extracted from a repository.

    This object is exchanged between services before being
    persisted to the database.
    """

    file_path: str
    chunk_index: int
    start_line: int
    end_line: int
    content: str