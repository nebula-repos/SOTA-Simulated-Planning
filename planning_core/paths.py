import os
from pathlib import Path


def _iter_repo_root_candidates() -> list[Path]:
    candidates: list[Path] = []

    env_repo_root = os.getenv("SOTA_REPO_ROOT")
    if env_repo_root:
        candidates.append(Path(env_repo_root).expanduser().resolve())

    module_root = Path(__file__).resolve().parent.parent
    candidates.append(module_root)

    cwd = Path.cwd().resolve()
    candidates.extend([cwd, *cwd.parents])
    return candidates


def _looks_like_repo_root(path: Path) -> bool:
    return (
        (path / "pyproject.toml").exists()
        and (path / "planning_core").exists()
        and (path / "apps").exists()
    )


def _resolve_repo_root() -> Path:
    seen: set[Path] = set()
    for candidate in _iter_repo_root_candidates():
        if candidate in seen:
            continue
        seen.add(candidate)
        if _looks_like_repo_root(candidate):
            return candidate

    return Path(__file__).resolve().parent.parent


def _resolve_output_dir(repo_root: Path) -> Path:
    env_output_dir = os.getenv("SOTA_OUTPUT_DIR")
    if env_output_dir:
        return Path(env_output_dir).expanduser().resolve()
    return repo_root / "output"


REPO_ROOT = _resolve_repo_root()
OUTPUT_DIR = _resolve_output_dir(REPO_ROOT)
