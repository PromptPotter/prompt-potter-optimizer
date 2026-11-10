"""Stands the `walk` world up in a throwaway home: a copy of a run's workspace, its account agreed to the
live terms, as the owner of any campaign it holds has. The source is never served, so nothing the
walk server writes reaches it. Runs under ``PROMPTPOTTER_HOME=<home>``, which binds at import."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from promptpotter.application.account import accept_terms, local_stores
from promptpotter.config.paths import PROJECTS_DIR
from promptpotter.config.settings import TERMS_VERSION
from promptpotter.infrastructure.store.io import rmtree_robust


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(
            "[e2e] usage: PROMPTPOTTER_HOME=<home> walk_world.py <source workspace>",
            file=sys.stderr,
        )
        return 2
    source = Path(argv[0]).resolve() / PROJECTS_DIR
    home = Path(os.environ["PROMPTPOTTER_HOME"]).resolve()
    # The guard is the PATH: this home is deleted whole.
    tmp = Path(tempfile.gettempdir()).resolve()
    if home == tmp or not home.is_relative_to(tmp):
        print(
            f"[e2e] REFUSING to replace {home} — the walk world must live under {tmp}.",
            file=sys.stderr,
        )
        return 2
    # A world is read only here, and a gate's `walk-world` build prunes every other world under the
    # clone's lock (`scripts/gate.py::_walk_world`): the copy holds that lock too, or inherits its
    # gate's hold.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from processes import queue_behind_siblings

    queue_behind_siblings("a walk world's copy")
    if not source.is_dir():
        print(
            f"[e2e] no workspace at {source} — run `python scripts/gate.py --only walk-world`",
            file=sys.stderr,
        )
        return 2
    if home.exists():
        rmtree_robust(home)
    shutil.copytree(source, home / PROJECTS_DIR)
    accept_terms(local_stores(tenant=None), TERMS_VERSION)
    print(f"[e2e] walk world {home} from {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
