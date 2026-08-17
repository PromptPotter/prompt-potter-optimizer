"""Exercise an INSTALLED wheel from outside any checkout.

The wheel is a shape the repo ships and never runs. In a source tree every path
function takes its checkout branch — ``benchmark_datasets_root()`` is ``<repo>/datasets``,
``webapp_static_root()`` is ``<repo>/webapp/out`` — so the two trees the wheel actually
serves (``assets/benchmarks/``, ``assets/webapp/``) are gitignored, staged by
``build_release.py``, and read by no dev run and no test. ``test_integrity.py``
monkeypatches ``PACKAGE_ROOT`` to cover ``paths.py``'s own functions and asserts ``Path``
VALUES; no consumer is ever constructed under the wheel shape — not ``Stores``, not the
FastAPI mount, not the optimizer manifest.

So this runs as the installed package, with the CWD somewhere else entirely, and asks the
questions a dev run structurally cannot. Failures are ``AssertionError`` with the measured
value in them; there is no framework here on purpose — it must run under a bare venv that
has the wheel and nothing else.

Requires ``$PROMPTPOTTER_HOME`` to be set to a scratch dir: half of what is checked is
that user data lands there rather than inside the install. ``$PROMPTPOTTER_SMOKE_EXPECT_WEBAPP=1``
additionally demands the dashboard — set it for a release wheel, never for a ``--no-webapp`` one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    home = os.environ.get("PROMPTPOTTER_HOME")
    assert home, "set PROMPTPOTTER_HOME to a scratch dir before running this"
    home_path = Path(home).expanduser().resolve()

    from promptpotter.config import paths

    # 1. We are genuinely under a wheel. Every assertion below is meaningless otherwise,
    #    and "ran it from the repo by mistake" is the way this check quietly stops working.
    assert paths.source_checkout_root() is None, (
        f"running from a checkout at {paths.source_checkout_root()} — "
        "install the wheel into a clean venv and run from outside the repo"
    )

    # 2. Install content ships and is reachable. `pyproject.toml::package-data` decides
    #    this, not the staging, and a typo there drops a whole tree silently.
    bench = paths.benchmark_datasets_root()
    assert bench.is_dir(), f"benchmark definitions absent from the wheel: {bench}"
    assert (bench / "promptpotter-self" / "campaign.yaml").is_file(), (
        f"{bench} exists but carries no dataset definitions"
    )
    assert paths.PACKAGE_ROOT in bench.parents or bench.is_relative_to(paths.PACKAGE_ROOT), (
        f"benchmarks resolved outside the installed package: {bench}"
    )

    manifest_path = paths.optimizer_pipeline_path()
    assert manifest_path.is_file(), f"optimizer manifest absent from the wheel: {manifest_path}"

    # 3. User data lands in $PROMPTPOTTER_HOME — never in site-packages (pip deletes that
    #    on upgrade) and never in the CWD (which scatters it per directory).
    for name, value in (
        ("DEFAULT_PROJECTS_ROOT", paths.DEFAULT_PROJECTS_ROOT),
        ("env_file_path()", paths.env_file_path()),
    ):
        assert value.is_relative_to(home_path), f"{name} = {value}, expected under {home_path}"

    from promptpotter.shared.pricing import CACHE_PATH

    assert CACHE_PATH.is_relative_to(home_path), (
        f"rates cache = {CACHE_PATH}, expected under {home_path}"
    )

    # 4. The consumers, not just the paths. Each of these is a real read of a shipped
    #    asset, and each was previously exercised only in the checkout shape.
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        optimizer_manifest,
        optimizer_resolved_schemas,
    )

    nodes = optimizer_manifest().get("nodes") or {}
    assert "l1_generate" in nodes, f"optimizer manifest has no l1_generate: {sorted(nodes)}"
    assert optimizer_resolved_schemas(), "generated schema registry is empty"

    from promptpotter.infrastructure.store.stores import build_stores
    from promptpotter.shared.identity import default_identity

    store = build_stores(default_identity(), projects_root=paths.DEFAULT_PROJECTS_ROOT)
    assert store.base_dir.is_relative_to(home_path), f"store rooted at {store.base_dir}"
    assert store.benchmarks_root == bench

    from promptpotter.main import app

    # Through `openapi()`, not `app.routes`: the wheel installs unpinned deps, so the
    # FastAPI here is whatever a `pip install` resolves rather than what `uv.lock` pins,
    # and the two disagree about whether an included router flattens into `app.routes`.
    # The generated document is the version-stable question — and generating it is itself
    # the check, since that is what `scripts/build_openapi.py` gates in CI.
    served = app.openapi().get("paths") or {}
    assert "/api/v1/cycles" in served, f"API did not mount its routes: {sorted(served)[:8]}"

    # 5. The dashboard, when the wheel was built to carry one. `build_release.py` checks the
    #    zip holds `assets/webapp/index.html`; nothing checks that the CONSUMER finds it, and
    #    `main.py` mounts behind a bare `.exists()` — so a wheel serving a naked API looks
    #    identical to `--no-webapp`, which is the shape CI builds. Opt-in by env because this
    #    one script covers both shapes.
    if os.environ.get("PROMPTPOTTER_SMOKE_EXPECT_WEBAPP") == "1":
        from promptpotter.main import WEBAPP_DIR

        assert WEBAPP_DIR.is_relative_to(paths.PACKAGE_ROOT), (
            f"dashboard resolved outside the installed package: {WEBAPP_DIR}"
        )
        assert (WEBAPP_DIR / "index.html").is_file(), (
            f"no index.html under {WEBAPP_DIR} — the API mounts, the dashboard 404s"
        )
        assert any(getattr(r, "name", None) == "webapp" for r in app.routes), (
            "index.html is present but nothing mounted it at /"
        )

    print(f"wheel smoke OK — package {paths.PACKAGE_ROOT}, user data {home_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
