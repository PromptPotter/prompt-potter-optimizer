"""Build the release wheel: stage the two derived asset trees, then ``uv build``.

Most of what the package reads at runtime is committed inside ``promptpotter/`` and
ships because ``pyproject.toml`` names it. Two things cannot work that way, because
they are **build artifacts**, not sources:

* **the dashboard** — ``webapp/out``, ~1.7 MB of Next.js export that exists only
  after ``npm run build`` in ``webapp/``;
* **the benchmark datasets** — ``datasets/``, where each dataset's ``cache.json``
  is gitignored and regenerable (~6.8 MB of HuggingFace rows) while the ~270 KB of
  definitions beside it is exactly what a fresh install needs to have something to
  run.

``pyproject.toml`` declares both under ``package-data``. This script is what makes
those declarations true. Skip it and the globs match nothing **quietly**: the mount
in ``main.py`` guards on ``.exists()``, so the wheel serves an API with no dashboard
and resolves no dataset, and neither failure says anything out loud.

Two rules the staging follows, both to avoid a second copy of a rule that already
exists somewhere else:

* **datasets are selected by ``git ls-files``**, not by a hand-written exclude list.
  The cache/definition split is already encoded in ``.gitignore`` (``datasets/*/
  cache.json``, minus the hand-authored ``email-tagging`` demo rows); re-stating it
  here would give it a second home to drift from.
* **a missing ``webapp/out`` is a hard error.** It is the one input this script
  cannot derive, and shipping without it is precisely the silent degradation above.
  Build the webapp first, or say ``--no-webapp`` and mean it.

Staging targets are gitignored and cleared on every run, so the script is
idempotent and a stale file from a previous release cannot ride along.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_ASSETS = _REPO / "promptpotter" / "assets"
_WEBAPP_SRC = _REPO / "webapp" / "out"
_WEBAPP_DST = _ASSETS / "webapp"
_DATASETS_DST = _ASSETS / "benchmarks"


def _clear(dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)


def stage_webapp() -> int:
    """Copy the exported dashboard into the package. Returns the file count."""
    _clear(_WEBAPP_DST)
    shutil.copytree(_WEBAPP_SRC, _WEBAPP_DST)
    return sum(1 for p in _WEBAPP_DST.rglob("*") if p.is_file())


def stage_datasets() -> int:
    """Copy the *tracked* dataset files into the package. Returns the file count.

    Tracked-only is the whole point: it ships the definitions (``campaign.yaml``,
    ``pipeline.yaml``, ``prompts/``, ``task_description.md``, the sweep payloads)
    and leaves the regenerable HuggingFace caches behind. A benchmark whose cache
    is absent is already a handled case — ``application/datasets/loaders.py::
    resolve_dataset_items`` fetches and re-persists it on first use, the same path
    a fresh clone takes.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z", "datasets/"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    rel_paths = [p for p in listing.stdout.split("\0") if p]
    _clear(_DATASETS_DST)
    for rel in rel_paths:
        src = _REPO / rel
        if not src.is_file():  # tracked but deleted in the working tree
            continue
        dst = _DATASETS_DST / Path(rel).relative_to("datasets")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return sum(1 for p in _DATASETS_DST.rglob("*") if p.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-webapp",
        action="store_true",
        help="ship without the dashboard (headless install); otherwise a missing "
        "webapp/out is an error",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="stage the asset trees but do not invoke `uv build`",
    )
    args = parser.parse_args()

    if args.no_webapp:
        _clear(_WEBAPP_DST)
        print("webapp    : SKIPPED (--no-webapp) — the wheel will serve the API only")
    elif not _WEBAPP_SRC.is_dir():
        print(
            f"error: {_WEBAPP_SRC.relative_to(_REPO)} does not exist.\n"
            "       Build it first:  cd webapp && npm ci && npm run build:deploy\n"
            "       Or pass --no-webapp to ship an API-only wheel deliberately.",
            file=sys.stderr,
        )
        return 1
    else:
        print(f"webapp    : {stage_webapp()} files -> {_WEBAPP_DST.relative_to(_REPO)}")

    print(f"benchmarks: {stage_datasets()} files -> {_DATASETS_DST.relative_to(_REPO)}")

    if args.stage_only:
        return 0
    return subprocess.run(["uv", "build", "--wheel"], cwd=_REPO, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
