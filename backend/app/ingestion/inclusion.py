from pathlib import PurePosixPath

INCLUDED_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".sql",
    ".sh",
}
INCLUDED_FILENAMES = {"Dockerfile", "requirements.txt"}
EXCLUDED_COMPONENTS = {".git", "node_modules", "build", "dist"}
LOCKFILE_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
}
REGULAR_FILE_MODES = {"100644", "100755"}


def is_included(relative_path: str, mode: str) -> bool:
    if mode not in REGULAR_FILE_MODES:
        return False
    parts = [part for part in relative_path.split("/") if part]
    if not parts:
        return False
    if any(part in EXCLUDED_COMPONENTS for part in parts):
        return False
    name = parts[-1]
    if name in LOCKFILE_NAMES or name.endswith(".lock"):
        return False
    if name in INCLUDED_FILENAMES:
        return True
    return PurePosixPath(name).suffix.lower() in INCLUDED_EXTENSIONS
