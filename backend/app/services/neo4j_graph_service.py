from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError
from pathlib import Path

from app.core.config import settings
from app.schemas.repository import RepositoryGraphStats


@dataclass(frozen=True)
class FileNode:
    path: str
    module: str
    is_service: bool


@dataclass
class RepositoryGraph:
    files: list[FileNode] = field(default_factory=list)
    file_imports: set[tuple[str, str]] = field(default_factory=set)
    module_uses: set[tuple[str, str]] = field(default_factory=set)
    services: set[str] = field(default_factory=set)

    def stats(self) -> RepositoryGraphStats:
        modules = {file_node.module for file_node in self.files}
        return RepositoryGraphStats(
            file_nodes=len(self.files),
            module_nodes=len(modules),
            service_nodes=len(self.services),
            file_import_relationships=len(self.file_imports),
            module_use_relationships=len(self.module_uses),
        )


class Neo4jGraphServiceError(Exception):
    pass


class Neo4jGraphService:
    source_file_extensions = {".py", ".js", ".jsx", ".ts", ".tsx"}
    javascript_import_pattern = re.compile(
        r"(?:import\s+.*?\s+from\s+|export\s+.*?\s+from\s+|require\()"
        r"[\"']([^\"']+)[\"']"
    )

    def __init__(self, driver=None) -> None:
        self.driver = driver

    def build_graph(
        self, repository_path: Path, ignored_directories: set[str]
    ) -> RepositoryGraph:
        graph = RepositoryGraph()
        source_files = self.collect_source_files(repository_path, ignored_directories)
        path_lookup = {
            path.relative_to(repository_path).as_posix(): path for path in source_files
        }

        for file_path in source_files:
            relative_path = file_path.relative_to(repository_path).as_posix()
            module_name = self.detect_module_name(relative_path)
            is_service = self.is_service_file(file_path, relative_path)
            graph.files.append(
                FileNode(path=relative_path, module=module_name, is_service=is_service)
            )
            if is_service:
                graph.services.add(relative_path)

        module_by_file = {file_node.path: file_node.module for file_node in graph.files}

        for file_path in source_files:
            source_path = file_path.relative_to(repository_path).as_posix()
            imports = self.extract_imports(file_path)
            for import_target in imports:
                target_path = self.resolve_import(
                    import_target=import_target,
                    current_file=file_path,
                    repository_path=repository_path,
                    path_lookup=path_lookup,
                )
                if target_path is None or target_path == source_path:
                    continue

                graph.file_imports.add((source_path, target_path))
                source_module = module_by_file.get(source_path)
                target_module = module_by_file.get(target_path)
                if source_module and target_module and source_module != target_module:
                    graph.module_uses.add((source_module, target_module))

        return graph

    def store_graph(
        self,
        repository_id: str,
        repo_url: str,
        graph: RepositoryGraph,
    ) -> RepositoryGraphStats:
        if not settings.neo4j_password:
            raise Neo4jGraphServiceError("NEO4J_PASSWORD is not configured.")

        driver = self.driver or GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

        try:
            with driver.session() as session:
                session.execute_write(
                    self.write_graph,
                    repository_id,
                    repo_url,
                    graph,
                )
        except Neo4jError as error:
            raise Neo4jGraphServiceError(
                "Unable to store repository graph in Neo4j."
            ) from error
        finally:
            if self.driver is None:
                driver.close()

        return graph.stats()

    def query_repository_context(self, repository_id: str) -> dict:
        if not settings.neo4j_password:
            raise Neo4jGraphServiceError("NEO4J_PASSWORD is not configured.")

        driver = self.driver or GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

        try:
            with driver.session() as session:
                return session.execute_read(self.read_repository_context, repository_id)
        except Neo4jError as error:
            raise Neo4jGraphServiceError(
                "Unable to retrieve repository graph context."
            ) from error
        finally:
            if self.driver is None:
                driver.close()

    def collect_source_files(
        self,
        repository_path: Path,
        ignored_directories: set[str],
    ) -> list[Path]:
        source_files = []

        for directory, directory_names, file_names in os.walk(repository_path):
            directory_names[:] = [
                directory_name
                for directory_name in directory_names
                if directory_name not in ignored_directories
            ]
            current_directory = Path(directory)
            for file_name in file_names:
                file_path = current_directory / file_name
                if file_path.suffix in self.source_file_extensions:
                    source_files.append(file_path)

        return sorted(source_files, key=lambda path: path.as_posix())

    def detect_module_name(self, relative_path: str) -> str:
        path_parts = Path(relative_path).parts
        if len(path_parts) == 1:
            return Path(relative_path).stem
        return path_parts[0]

    def is_service_file(self, file_path: Path, relative_path: str) -> bool:
        normalized_path = relative_path.lower()
        return (
            "service" in file_path.stem.lower() or "/services/" in f"/{normalized_path}"
        )

    def extract_imports(self, file_path: Path) -> list[str]:
        if file_path.suffix == ".py":
            return self.extract_python_imports(file_path)
        if file_path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
            return self.extract_javascript_imports(file_path)
        return []

    def extract_python_imports(self, file_path: Path) -> list[str]:
        try:
            parsed_file = ast.parse(file_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            return []

        imports = []
        for node in ast.walk(parsed_file):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module_name = "." * node.level + (node.module or "")
                imports.append(module_name)

        return imports

    def extract_javascript_imports(self, file_path: Path) -> list[str]:
        try:
            file_content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        return self.javascript_import_pattern.findall(file_content)

    def resolve_import(
        self,
        import_target: str,
        current_file: Path,
        repository_path: Path,
        path_lookup: dict[str, Path],
    ) -> str | None:
        if current_file.suffix == ".py":
            return self.resolve_python_import(
                import_target, current_file, repository_path, path_lookup
            )
        return self.resolve_javascript_import(
            import_target, current_file, repository_path, path_lookup
        )

    def resolve_python_import(
        self,
        import_target: str,
        current_file: Path,
        repository_path: Path,
        path_lookup: dict[str, Path],
    ) -> str | None:
        if import_target.startswith("."):
            level = len(import_target) - len(import_target.lstrip("."))
            module_name = import_target.lstrip(".")
            base_directory = current_file.parent
            for _ in range(max(level - 1, 0)):
                base_directory = base_directory.parent
            candidate_base = base_directory / Path(module_name.replace(".", "/"))
        else:
            candidate_base = repository_path / Path(import_target.replace(".", "/"))

        return self.match_candidate_path(
            candidate_base, repository_path, path_lookup, [".py"]
        )

    def resolve_javascript_import(
        self,
        import_target: str,
        current_file: Path,
        repository_path: Path,
        path_lookup: dict[str, Path],
    ) -> str | None:
        if not import_target.startswith("."):
            return None

        candidate_base = (current_file.parent / import_target).resolve()
        if not candidate_base.is_relative_to(repository_path.resolve()):
            return None

        return self.match_candidate_path(
            candidate_base,
            repository_path,
            path_lookup,
            [".ts", ".tsx", ".js", ".jsx"],
        )

    def match_candidate_path(
        self,
        candidate_base: Path,
        repository_path: Path,
        path_lookup: dict[str, Path],
        extensions: list[str],
    ) -> str | None:
        candidates = []
        if candidate_base.suffix:
            candidates.append(candidate_base)
        else:
            candidates.extend(
                candidate_base.with_suffix(extension) for extension in extensions
            )
            candidates.extend(
                candidate_base / f"index{extension}" for extension in extensions
            )
            candidates.append(candidate_base / "__init__.py")

        for candidate in candidates:
            try:
                relative_candidate = candidate.relative_to(repository_path).as_posix()
            except ValueError:
                continue
            if relative_candidate in path_lookup:
                return relative_candidate

        return None

    @staticmethod
    def write_graph(
        transaction, repository_id: str, repo_url: str, graph: RepositoryGraph
    ) -> None:
        # files = [file_node.__dict__ for file_node in graph.files]
        files = [
         {
                **file_node.__dict__,
                "name": Path(file_node.path).name,
         }
        for file_node in graph.files
       ] 
        modules = sorted({file_node.module for file_node in graph.files})
        services = sorted(graph.services)
        file_imports = [
            {"source": source, "target": target}
            for source, target in sorted(graph.file_imports)
        ]
        module_uses = [
            {"source": source, "target": target}
            for source, target in sorted(graph.module_uses)
        ]

        transaction.run(
            """
            MERGE (repository:Repository {id: $repository_id})
            SET repository.url = $repo_url
            """,
            repository_id=repository_id,
            repo_url=repo_url,
        )
        transaction.run(
            """
            UNWIND $files AS file
            MATCH (repository:Repository {id: $repository_id})
            MERGE (node:File {repository_id: $repository_id, path: file.path})
            SET node.name = file.name,
                node.module = file.module,
                node.is_service = file.is_service
            MERGE (repository)-[:CONTAINS]->(node)
            """,
            repository_id=repository_id,
            files=files,
        )
        transaction.run(
            """
            UNWIND $modules AS module_name
            MATCH (repository:Repository {id: $repository_id})
            MERGE (module:Module {repository_id: $repository_id, name: module_name})
            MERGE (repository)-[:CONTAINS]->(module)
            """,
            repository_id=repository_id,
            modules=modules,
        )
        transaction.run(
            """
            UNWIND $files AS file
            MATCH (file_node:File {repository_id: $repository_id, path: file.path})
            MATCH (module:Module {repository_id: $repository_id, name: file.module})
            MERGE (module)-[:CONTAINS]->(file_node)
            """,
            repository_id=repository_id,
            files=files,
        )
        transaction.run(
            """
            UNWIND $file_imports AS item
            MATCH (source:File {repository_id: $repository_id, path: item.source})
            MATCH (target:File {repository_id: $repository_id, path: item.target})
            MERGE (source)-[:IMPORTS]->(target)
            """,
            repository_id=repository_id,
            file_imports=file_imports,
        )
        transaction.run(
            """
            UNWIND $module_uses AS item
            MATCH (source:Module {repository_id: $repository_id, name: item.source})
            MATCH (target:Module {repository_id: $repository_id, name: item.target})
            MERGE (source)-[:USES]->(target)
            """,
            repository_id=repository_id,
            module_uses=module_uses,
        )
        transaction.run(
            """
            UNWIND $services AS service_path
            MATCH (file_node:File {repository_id: $repository_id, path: service_path})
            MERGE (service:Service {repository_id: $repository_id, path: service_path})
            SET service.name = file_node.path
            MERGE (service)-[:IMPLEMENTED_IN]->(file_node)
            """,
            repository_id=repository_id,
            services=services,
        )

    @staticmethod
    def read_repository_context(transaction, repository_id: str) -> dict:
        imported_files = transaction.run(
            """
            MATCH (source:File {repository_id: $repository_id})-[:IMPORTS]->(target:File)
            RETURN source.path AS source, target.path AS target
            ORDER BY source.path, target.path
            LIMIT 50
            """,
            repository_id=repository_id,
        ).data()
        module_uses = transaction.run(
            """
            MATCH (source:Module {repository_id: $repository_id})-[:USES]->(target:Module)
            RETURN source.name AS source, target.name AS target
            ORDER BY source.name, target.name
            LIMIT 50
            """,
            repository_id=repository_id,
        ).data()
        services = transaction.run(
            """
            MATCH (service:Service {repository_id: $repository_id})-[:IMPLEMENTED_IN]->(file:File)
            RETURN service.path AS service, file.module AS module
            ORDER BY service.path
            LIMIT 50
            """,
            repository_id=repository_id,
        ).data()
        central_files = transaction.run(
            """
            MATCH (source:File {repository_id: $repository_id})-[:IMPORTS]->(target:File {repository_id: $repository_id})
            RETURN target.path AS file, count(source) AS import_count
            ORDER BY import_count DESC, file
            LIMIT 20
            """,
            repository_id=repository_id,
        ).data()
    @staticmethod
    def read_central_files(
        transaction,
        repository_id: str,
        limit: int,
    ):
        return transaction.run(
            """
            MATCH (source:File {repository_id: $repository_id})
                -[:IMPORTS]->
                (target:File {repository_id: $repository_id})
            RETURN
                target.path AS file,
                count(source) AS imports
            ORDER BY imports DESC, file
            LIMIT $limit
            """,
            repository_id=repository_id,
            limit=limit,
        ).data()
    def get_file_count(self, repository_id: str) -> int:
        driver = self.driver or GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

        try:
          with driver.session() as session:
            result = session.run(
                """
                MATCH (f:File {repository_id: $repository_id})
                RETURN count(f) AS count
                """,
                repository_id=repository_id,
            ).single()

            return result["count"] if result else 0
        finally:
          if self.driver is None:
            driver.close()
    def get_module_count(self, repository_id: str) -> int:
       driver = self.driver or GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
       )

       try:
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (m:Module {repository_id: $repository_id})
                    RETURN count(m) AS count
                    """,
                    repository_id=repository_id,
                ).single()

                return result["count"] if result else 0
       finally:
            if self.driver is None:
                driver.close()
    def get_modules(self, repository_id: str) -> list[str]:
       driver = self.driver or GraphDatabase.driver(
         settings.neo4j_uri,
         auth=(settings.neo4j_user, settings.neo4j_password),
       )

       try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (m:Module {repository_id: $repository_id})
                RETURN m.name AS name
                ORDER BY name
                """,
                repository_id=repository_id,
            )

            return [row["name"] for row in result]
       finally:
        if self.driver is None:
            driver.close()
    def get_files_in_module(
        self,
        repository_id: str,
        module: str,
    ) -> list[str]:
        driver = self.driver or GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

        try:
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (m:Module {repository_id:$repository_id, name:$module})
                        -[:CONTAINS]->
                        (f:File)
                    RETURN f.name AS name
                    ORDER BY name
                    """,
                    repository_id=repository_id,
                    module=module,
                )

                return [row["name"] for row in result]

        finally:
            if self.driver is None:
                driver.close()
    def get_module_dependencies(
        self,
        repository_id: str,
        module: str,
    ) -> list[str]:
        driver = self.driver or GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

        try:
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (m:Module {repository_id:$repository_id, name:$module})
                        -[:USES]->
                        (other:Module)
                    RETURN other.name AS name
                    ORDER BY name
                    """,
                    repository_id=repository_id,
                    module=module,
                )

                return [row["name"] for row in result]

        finally:
            if self.driver is None:
                driver.close()
    def get_central_files(
        self,
        repository_id: str,
        limit: int = 10,
    ) -> list[dict]:

        driver = self.driver or GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

        try:
            with driver.session() as session:
                return session.execute_read(
                    self.read_central_files,
                    repository_id,
                    limit,
                )
        finally:
            if self.driver is None:
                driver.close()
        return {
            "imported_files": imported_files,
            "module_uses": module_uses,
            "services": services,
            "central_files": central_files,
        }
