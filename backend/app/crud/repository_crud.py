from sqlalchemy.orm import Session

from app.models.analysis_report import AnalysisReport
from app.models.repository import Repository


class RepositoryCRUD:
    def get_repository_by_id(
        self, db: Session, repository_id: str
    ) -> Repository | None:
        return db.query(Repository).filter(Repository.id == repository_id).first()

    def get_repository_by_url(self, db: Session, repo_url: str) -> Repository | None:
        return db.query(Repository).filter(Repository.repo_url == repo_url).first()

    def get_latest_analysis_report(
        self,
        db: Session,
        repository_id: str,
    ) -> AnalysisReport | None:
        return (
            db.query(AnalysisReport)
            .filter(AnalysisReport.repository_id == repository_id)
            .order_by(AnalysisReport.created_at.desc())
            .first()
        )

    def get_or_create_repository(self, db: Session, repo_url: str) -> Repository:
        repository = self.get_repository_by_url(db, repo_url)
        if repository is not None:
            return repository

        repository = Repository(repo_url=repo_url)
        db.add(repository)
        db.flush()
        return repository

    def create_analysis_report(
        self,
        db: Session,
        repository: Repository,
        status: str,
        file_count: int,
        directory_count: int,
        ignored_directories: list[str],
        technologies: list[dict[str, str]],
        directory_structure: list[dict],
        summary: dict,
        token_usage: dict[str, int],
    ) -> AnalysisReport:
        analysis_report = AnalysisReport(
            repository_id=repository.id,
            status=status,
            file_count=file_count,
            directory_count=directory_count,
            ignored_directories=ignored_directories,
            technologies=technologies,
            directory_structure=directory_structure,
            summary=summary,
            token_usage=token_usage,
        )
        db.add(analysis_report)
        db.flush()
        return analysis_report

    def save_repository_analysis(
        self,
        db: Session,
        repo_url: str,
        status: str,
        file_count: int,
        directory_count: int,
        ignored_directories: list[str],
        technologies: list[dict[str, str]],
        directory_structure: list[dict],
        summary: dict,
        token_usage: dict[str, int],
    ) -> tuple[Repository, AnalysisReport]:
        repository = self.get_or_create_repository(db, repo_url)
        analysis_report = self.create_analysis_report(
            db=db,
            repository=repository,
            status=status,
            file_count=file_count,
            directory_count=directory_count,
            ignored_directories=ignored_directories,
            technologies=technologies,
            directory_structure=directory_structure,
            summary=summary,
            token_usage=token_usage,
        )
        db.commit()
        db.refresh(repository)
        db.refresh(analysis_report)
        return repository, analysis_report
