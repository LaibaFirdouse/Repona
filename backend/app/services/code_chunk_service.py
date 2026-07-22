from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.code_chunk import CodeChunk
from app.schemas import ChunkEmbedding


class CodeChunkService:
    """
    Handles persistence of repository code chunks.

    Responsibilities:
    - Save chunk embeddings
    - Delete repository chunks
    - Retrieve repository chunks

    Does NOT:
    - Generate chunks
    - Generate embeddings
    - Call LLMs
    """

    def save_chunks(
        self,
        repository_id: str,
        chunk_embeddings: list[ChunkEmbedding],
        db: Session,
    ) -> None:
        """
        Persist all chunk embeddings for a repository.
        """

        code_chunks = [
            CodeChunk(
                repository_id=repository_id,
                file_path=item.chunk.file_path,
                chunk_index=item.chunk.chunk_index,
                start_line=item.chunk.start_line,
                end_line=item.chunk.end_line,
                content=item.chunk.content,
                embedding=item.embedding,
            )
            for item in chunk_embeddings
        ]
        print(f"Prepared {len(code_chunks)} CodeChunk objects")
        db.add_all(code_chunks)
        db.flush()

    def delete_chunks(
        self,
        repository_id: str,
        db: Session,
    ) -> None:
        """
        Delete every chunk belonging to a repository.
        """

        db.execute(
            delete(CodeChunk).where(
                CodeChunk.repository_id == repository_id
            )
        )

        db.commit()

    def get_chunks(
        self,
        repository_id: str,
        db: Session,
    ) -> list[CodeChunk]:
        """
        Fetch every chunk belonging to a repository.
        """

        return (
            db.execute(
                select(CodeChunk)
                .where(
                    CodeChunk.repository_id == repository_id
                )
                .order_by(
                    CodeChunk.file_path,
                    CodeChunk.chunk_index,
                )
            )
            .scalars()
            .all()
        )