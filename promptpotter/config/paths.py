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
_ENV_FILE = "PROMPTPOTTER_ENV_FILE"


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
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PromptPotter"
    else:
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


INSTALL_CACHE_DIR = ".cache"


def install_cache_root() -> Path:
    """What derives from the installed code alone, so every home on one install shares it."""
    checkout = source_checkout_root()
    return (checkout if checkout is not None else _os_app_data_dir()) / INSTALL_CACHE_DIR


PROJECTS_DIR = "projects"


def default_projects_root() -> Path:
    return user_data_root() / PROJECTS_DIR


def jobs_dir(projects_root: Path) -> Path:
    return projects_root.parent / "jobs"


def optimizers_root() -> Path:
    return PACKAGE_ROOT / "assets" / "optimizers"


def checkin_assets_root() -> Path:
    return PACKAGE_ROOT / "assets" / "checkin"


def free_tier_path() -> Path:
    """The install's own copy, where its operator edits the free tier, before the shipped one."""
    return _shadowed(Path("free_tier.yaml"), PACKAGE_ROOT / "assets" / "free_tier.yaml")


def _shadowed(relative: Path, shipped: Path) -> Path:
    # A FILE, never its directory: the generated schema registry beside the manifest is ours.
    override = user_data_root() / relative
    return override if override.is_file() else shipped


def optimizer_manifest_path(shipped: Path) -> Path:
    """Keyed by the registered manifest's own directory, so no requested name ever reaches a path."""
    return _shadowed(Path("optimizers") / shipped.name / "pipeline.yaml", shipped / "pipeline.yaml")


BLOCK_PACKS_DIR = "block_packs"


def block_packs_root() -> Path:
    """The install's packs, read-only to every account: a directory placed or linked here, since none ships."""
    return user_data_root() / BLOCK_PACKS_DIR


def checkin_manifest_path() -> Path:
    return _shadowed(Path("checkin") / "pipeline.yaml", checkin_assets_root() / "pipeline.yaml")


def env_file_path() -> Path:
    """The install's, never the CWD's: under a wheel that scatters one `.env` per working directory."""
    override = os.environ.get(_ENV_FILE)
    if override:
        return Path(override).expanduser().resolve()
    checkout = source_checkout_root()
    return (checkout if checkout is not None else user_data_root()) / ".env"


def install_datasets_root() -> Path:
    return PACKAGE_ROOT / "assets" / "datasets"


def webapp_static_root() -> Path:
    """May not exist (`--no-webapp`): the mount guards on existence."""
    checkout = source_checkout_root()
    return (
        checkout / "webapp" / "out" if checkout is not None else PACKAGE_ROOT / "assets" / "webapp"
    )


MEASUREMENTS_DIR = "measurements"
OPTIMIZER_REUSE_DIR = "optimizer_reuse"
JUDGE_REUSE_DIR = "judge_reuse"

SHARED_CACHE_DIRS: tuple[str, ...] = (MEASUREMENTS_DIR, OPTIMIZER_REUSE_DIR, JUDGE_REUSE_DIR)
