"""The roots the package runs against — install content, user data, benchmarks.

**Three roots, not one.** Until now a single constant stood for all three:
``REPO_ROOT``, a four-level parent walk out of ``infrastructure/store/layout.py``
whose own comment called itself "the ONE anchor". That is correct in a source
checkout and wrong everywhere else — installed, ``parents[3]`` is
``site-packages/``, so the package looked for its own optimizer prompts under
``site-packages/datasets/`` and wrote every campaign into a directory ``pip``
owns and deletes on upgrade.

The walk had also already been copied four more times (``wiring.py``,
``settings.py``, ``notebook_run.py``, and ``session.py``'s CWD-relative
``Path("datasets")``), each recomputing the same wrong thing at its own depth.
One anchor that is wrong in three different ways is not an anchor, so it is
replaced by three roots that each say what they are:

- :data:`PACKAGE_ROOT` / :func:`optimizer_assets_root` — **install content.**
  Read-only, ships inside the wheel, ours not the operator's.
- :func:`user_data_root` — **user data.** Read-write, per machine, holds
  measurements we do not own and must never lose to a reinstall.
- :func:`benchmark_datasets_root` — **sample content.** The repo's benchmark
  dataset *definitions*, read-only like the install content above. They ship
  (~270 KB, staged by ``scripts/build_release.py``); the per-dataset HuggingFace
  row caches do not, since they are 6.8 MB, regenerable on first use, and the
  operator's rather than ours — so they are written to the user-data root.

``site-packages/datasets`` deserves its own warning: that is where the
HuggingFace ``datasets`` library installs, and it is a declared extra of this
project. So the old constant did not merely point at a missing directory — with
``[benchmarks]`` installed it pointed at a real one full of the wrong files, and
enumerating it yielded ``arrow_dataset.py`` as a candidate dataset. A wrong path
that exists fails silently; that is the failure mode this module removes.
"""

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
_PROJECT_NAME = "promptpotter-optimizer"

_ENV_HOME = "PROMPTPOTTER_HOME"


@lru_cache(maxsize=1)
def source_checkout_root() -> Path | None:
    """The repo root when running from a source tree, else ``None``.

    True for a plain checkout and for an editable install (both keep a
    ``pyproject.toml`` beside the package); false for a wheel, which ships none.

    The marker is verified by NAME, not by existence. ``site-packages`` is a
    directory anyone may drop a ``pyproject.toml`` into, and mistaking one for
    this repo would put user data back inside the install — the exact failure
    this module exists to prevent.
    """
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
    """Where campaigns, sessions and measurements are written (the ``.promptpotter`` tree).

    Resolution order, and the middle branch is the one that matters for anyone
    already using this repo:

    1. ``$PROMPTPOTTER_HOME`` — explicit, wins always.
    2. **The checkout's own ``.promptpotter/``, whenever we are running from a
       source tree.** Development and ``deploy-linux/`` both run from a checkout,
       so both keep the exact tree they have today. Nothing moves.
    3. The OS application-data dir — reached only when there is no checkout to
       sit beside, which is precisely the case that would otherwise resolve into
       ``site-packages/``.
    """
    override = os.environ.get(_ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()
    checkout = source_checkout_root()
    if checkout is not None:
        return checkout / ".promptpotter"
    return _os_app_data_dir()


def optimizer_assets_root() -> Path:
    """Install content: the optimizer's own pipeline + optimizer prompt sets.

    These are install-global by contract (``docs/developer/stable-api.md`` §3 —
    "one file configures the optimizer for every campaign"), so they belong to the
    package, not to the operator's dataset tier, and they ship in the wheel.
    """
    return PACKAGE_ROOT / "assets" / "optimizer"


def optimizer_pipeline_path() -> Path:
    """The optimizer's node manifest — the operator's copy if there is one, else ours.

    The ONE file under ``assets/optimizer/`` an operator legitimately owns: provider,
    model and temperature, per optimizer node. Its two neighbours are not theirs to
    edit — ``resolved_schemas.json`` is generated from the Pydantic models and
    ``sets/*.yaml`` are the L4 instrument — so this shadows a single FILE and never
    the directory. A directory-level override would silently invite a hand-written
    schema registry.

    Tenant-first, install-fallback: the same rule
    ``store/dataset_access.py::readable_dataset_dir`` already applies to datasets.
    In a checkout the operator path is ``.promptpotter/optimizer/pipeline.yaml``,
    which does not exist, so the packaged manifest wins and nothing moves. Under a
    wheel this is what makes the optimizer configurable at all: the shipped manifest
    sits in ``site-packages``, where an edit lasts until the next ``pip install -U``.
    """
    override = user_data_root() / "optimizer" / "pipeline.yaml"
    return override if override.is_file() else optimizer_assets_root() / "pipeline.yaml"


def env_file_path() -> Path:
    """The ONE ``.env`` this install reads — and the one the first-run prompt writes.

    A checkout keeps ``<repo>/.env``: that is where every existing key already sits, and
    relocating it would silently un-configure a working box. Everything else lands beside
    the user data, so the file is a property of the INSTALL rather than of wherever the
    operator happened to be standing.

    That last part is the fix. Two call sites resolved ``.env`` off ``Path.cwd()``
    independently — the settings model and the first-run key prompt — which is invisible
    in development (CWD is always the repo root) and scatters one ``.env`` per working
    directory under a wheel. The key pasted last week simply is not found from anywhere
    else, and nothing says so: the run reaches the provider and fails with the provider's
    own auth error.
    """
    checkout = source_checkout_root()
    return (checkout if checkout is not None else user_data_root()) / ".env"


def benchmark_datasets_root() -> Path:
    """The benchmark DEFINITIONS: the checkout's ``datasets/``, else package assets.

    **Read-only, on every install shape** — under a wheel it resolves inside
    ``site-packages``, which pip deletes on upgrade and a system install refuses
    to write. Nothing in the package may write here, and nothing needs to: a
    benchmark's materialized rows belong to the operator, not to us, so they land
    in the user tree instead (``store/dataset_access.py::readable_dataset_rows``).
    A benchmark arriving without its rows is an already-handled case —
    ``application/datasets/loaders.py::resolve_dataset_items`` fetches and persists
    them on first use, exactly as a fresh clone does.

    What this must never be is ``site-packages/datasets`` — the HuggingFace
    library's own directory, and a declared extra of this project.
    """
    checkout = source_checkout_root()
    return checkout / "datasets" if checkout is not None else PACKAGE_ROOT / "assets" / "benchmarks"


def webapp_static_root() -> Path:
    """The exported Next.js dashboard mounted at ``/``.

    Staged into the package by ``scripts/build_release.py``, which errors rather than
    omit it. It may still be absent — that is what ``--no-webapp`` deliberately buys —
    and the mount guards on existence.
    """
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
