"""Three roots, never one: :data:`PACKAGE_ROOT` ships in the wheel, :func:`user_data_root`
survives a reinstall, and :func:`benchmark_datasets_root` holds read-only definitions."""

from __future__ import annotations

import os
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

# The installed package itself (``.../promptpotter``). Depth-correct by
# construction rather than by counting: this module is ``promptpotter/config/paths.py``,
# so the package is always exactly two parents up, in a checkout and in a wheel alike.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# Read by :func:`source_checkout_root` to prove a neighbouring ``pyproject.toml``
# is OURS. Must match ``[project].name``.
_PROJECT_NAME = "promptpotter"

_ENV_HOME = "PROMPTPOTTER_HOME"


@lru_cache(maxsize=1)
def source_checkout_root() -> Path | None:
    """The repo root when running from a source tree (checkout or editable install), else ``None``.
    The marker is verified by NAME: anyone may drop a ``pyproject.toml`` into ``site-packages``."""
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
    """Per-user application-data dir for this platform. No I/O; the caller creates it."""
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
    """Where campaigns, sessions and measurements are written. ``$PROMPTPOTTER_HOME`` wins, else
    the checkout's own ``.promptpotter/``, else the OS app-data dir — never ``site-packages``."""
    override = os.environ.get(_ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()
    checkout = source_checkout_root()
    if checkout is not None:
        return checkout / ".promptpotter"
    return _os_app_data_dir()


def optimizer_assets_root() -> Path:
    """Install content: the optimizer's own pipeline + optimizer prompt sets. Install-global by
    contract (``stable-api.md`` §3), so they ship in the wheel and are not the operator's tier."""
    return PACKAGE_ROOT / "assets" / "optimizer"


def optimizer_pipeline_path() -> Path:
    """The optimizer's node manifest, tenant-first then install — the ONE file under
    ``assets/optimizer/`` an operator owns, so this shadows a FILE and never the directory."""
    override = user_data_root() / "optimizer" / "pipeline.yaml"
    return override if override.is_file() else optimizer_assets_root() / "pipeline.yaml"


def env_file_path() -> Path:
    """The ONE ``.env`` this install reads and the first-run prompt writes — a property of the
    INSTALL, never of the CWD, which under a wheel scatters one file per working directory."""
    checkout = source_checkout_root()
    return (checkout if checkout is not None else user_data_root()) / ".env"


def benchmark_datasets_root() -> Path:
    """The benchmark DEFINITIONS, read-only on every install shape — materialized rows are the
    operator's and land in the user tree. Never ``site-packages/datasets`` — HuggingFace's own."""
    checkout = source_checkout_root()
    return checkout / "datasets" if checkout is not None else PACKAGE_ROOT / "assets" / "benchmarks"


def webapp_static_root() -> Path:
    """The exported Next.js dashboard mounted at ``/``. It may be absent — that is what
    ``--no-webapp`` buys — so the mount guards on existence."""
    checkout = source_checkout_root()
    return (
        checkout / "webapp" / "out" if checkout is not None else PACKAGE_ROOT / "assets" / "webapp"
    )


# Bound once at import, like ``settings.APP_VERSION``. ``$PROMPTPOTTER_HOME`` is
# read here, so it is an environment decision made before the process starts.
DEFAULT_PROJECTS_ROOT = user_data_root() / "projects"


__all__ = [
    "DEFAULT_PROJECTS_ROOT",
    "PACKAGE_ROOT",
    "benchmark_datasets_root",
    "env_file_path",
    "optimizer_assets_root",
    "optimizer_pipeline_path",
    "source_checkout_root",
    "user_data_root",
    "webapp_static_root",
]
