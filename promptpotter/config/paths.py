from __future__ import annotations

import os
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# Must match `pyproject.toml::[project].name`.
_PROJECT_NAME = "promptpotter"

_ENV_HOME = "PROMPTPOTTER_HOME"


@lru_cache(maxsize=1)
def source_checkout_root() -> Path | None:
    """The marker is verified by NAME: anyone may drop a `pyproject.toml` into `site-packages`."""
    candidate = PACKAGE_ROOT.parent
    pyproject = candidate / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        with pyproject.open("rb") as fh:
            name = tomllib.load(fh).get("project", {}).get("name")
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return candidate if name == _PROJECT_NAME else None


def _os_app_data_dir() -> Path:
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "PromptPotter"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PromptPotter"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "promptpotter"


def user_data_root() -> Path:
    override = os.environ.get(_ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()
    checkout = source_checkout_root()
    if checkout is not None:
        return checkout / ".promptpotter"
    return _os_app_data_dir()


def default_jobs_dir() -> Path:
    return user_data_root() / "jobs"


def optimizers_root() -> Path:
    return PACKAGE_ROOT / "assets" / "optimizers"


def checkin_assets_root() -> Path:
    return PACKAGE_ROOT / "assets" / "checkin"


def _shadowed(relative: Path, shipped: Path) -> Path:
    # A FILE, never its directory: the generated schema registry beside the manifest is ours.
    override = user_data_root() / relative
    return override if override.is_file() else shipped


def optimizer_manifest_path(name: str, shipped: Path) -> Path:
    return _shadowed(Path("optimizers") / name / "pipeline.yaml", shipped / "pipeline.yaml")


def checkin_manifest_path() -> Path:
    return _shadowed(Path("checkin") / "pipeline.yaml", checkin_assets_root() / "pipeline.yaml")


def env_file_path() -> Path:
    """The install's, never the CWD's: under a wheel that scatters one `.env` per working directory."""
    checkout = source_checkout_root()
    return (checkout if checkout is not None else user_data_root()) / ".env"


def benchmark_datasets_root() -> Path:
    """The checkout's `datasets/`, else the packaged ones — never `site-packages/datasets`, HuggingFace's own."""
    checkout = source_checkout_root()
    return checkout / "datasets" if checkout is not None else PACKAGE_ROOT / "assets" / "benchmarks"


def webapp_static_root() -> Path:
    """May not exist (`--no-webapp`): the mount guards on existence."""
    checkout = source_checkout_root()
    return (
        checkout / "webapp" / "out" if checkout is not None else PACKAGE_ROOT / "assets" / "webapp"
    )


# Bound at import: `$PROMPTPOTTER_HOME` is an environment decision, never a runtime one.
DEFAULT_PROJECTS_ROOT = user_data_root() / "projects"


__all__ = [
    "DEFAULT_PROJECTS_ROOT",
    "PACKAGE_ROOT",
    "benchmark_datasets_root",
    "checkin_assets_root",
    "checkin_manifest_path",
    "default_jobs_dir",
    "env_file_path",
    "optimizer_manifest_path",
    "optimizers_root",
    "source_checkout_root",
    "user_data_root",
    "webapp_static_root",
]
