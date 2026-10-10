"""Runs under a bare venv holding only the wheel, CWD outside any checkout: no test framework."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _get(url: str) -> tuple[int, bytes, str]:
    """An ``HTTPError`` is an ANSWER here: urllib raises on the 404 and the 500 this asks about."""
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return resp.status, resp.read(), resp.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("content-type", "")


def main() -> int:
    home = os.environ.get("PROMPTPOTTER_HOME")
    assert home, "set PROMPTPOTTER_HOME to a scratch dir before running this"
    home_path = Path(home).expanduser().resolve()

    from promptpotter.config import paths

    assert paths.source_checkout_root() is None, (
        f"running from a checkout at {paths.source_checkout_root()} — "
        "install the wheel into a clean venv and run from outside the repo"
    )

    # `pyproject.toml::package-data` decides this, and a typo there drops a whole tree silently.
    bench = paths.benchmark_datasets_root()
    assert bench.is_dir(), f"benchmark definitions absent from the wheel: {bench}"
    assert (bench / "promptpotter-self" / "campaign.yaml").is_file(), (
        f"{bench} exists but carries no dataset definitions"
    )
    assert paths.PACKAGE_ROOT in bench.parents or bench.is_relative_to(paths.PACKAGE_ROOT), (
        f"benchmarks resolved outside the installed package: {bench}"
    )

    manifest_path = paths.optimizer_manifest_path("potter", paths.optimizers_root() / "potter")
    assert manifest_path.is_file(), f"optimizer manifest absent from the wheel: {manifest_path}"
    assert paths.checkin_manifest_path().is_file(), "check-in manifest absent from the wheel"

    for name, value in (
        ("DEFAULT_PROJECTS_ROOT", paths.DEFAULT_PROJECTS_ROOT),
        ("env_file_path()", paths.env_file_path()),
    ):
        assert value.is_relative_to(home_path), f"{name} = {value}, expected under {home_path}"

    from promptpotter.infrastructure.llm.pricing import CACHE_PATH

    assert CACHE_PATH.is_relative_to(home_path), (
        f"rates cache = {CACHE_PATH}, expected under {home_path}"
    )

    from promptpotter.application.optimizer_manifest import checkin_manifest, resolve_optimizer

    potter = resolve_optimizer("potter", {})
    assert potter.llm_nodes, "the potter manifest declares no llm node"
    assert potter.resolved_schemas, "the potter schema registry is empty"
    assert checkin_manifest().schema.get_node("checkin"), "the check-in manifest has no checkin"

    from promptpotter.infrastructure.store.stores import build_stores
    from promptpotter.shared.identity import default_identity

    store = build_stores(default_identity(), projects_root=paths.DEFAULT_PROJECTS_ROOT)
    assert store.base_dir.is_relative_to(home_path), f"store rooted at {store.base_dir}"
    assert store.benchmarks_root == bench

    from promptpotter.main import app

    # `openapi()`, not `app.routes`: FastAPI versions disagree on whether an included router flattens.
    served = app.openapi().get("paths") or {}
    assert "/api/v1/cycles" in served, f"API did not mount its routes: {sorted(served)[:8]}"

    # `main.py` mounts behind a bare `.exists()`: a wheel serving a naked API reads as `--no-webapp`.
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

    # The uvicorn a deploy runs, never an in-process ASGI client: the lifespan runs too.
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "promptpotter.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
    )
    try:
        # Poll the exit code beside the socket: a crash during startup is a process that is gone.
        status, body = 0, b""
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            assert server.poll() is None, f"server exited during startup, rc={server.returncode}"
            try:
                status, body, _ = _get(f"{base}/api/v1/health")
                break
            except OSError:
                time.sleep(0.25)
        else:
            raise AssertionError(f"nothing answered at {base}/api/v1/health within 90s")
        assert status == 200, f"health answered {status}: {body[:300]!r}"
        assert json.loads(body).get("status") == "healthy", f"health body: {body[:300]!r}"

        status, body, _ = _get(f"{base}/api/v1/campaigns")
        assert status == 200, f"GET /api/v1/campaigns answered {status}: {body[:300]!r}"
        listing = json.loads(body)
        assert "campaigns" in listing, f"campaign listing off-contract: {body[:300]!r}"

        if os.environ.get("PROMPTPOTTER_SMOKE_EXPECT_WEBAPP") == "1":
            status, body, content_type = _get(f"{base}/")
            assert status == 200, f"dashboard root answered {status}: {body[:300]!r}"
            assert "text/html" in content_type, f"dashboard root served {content_type!r}"
            assert b"<html" in body.lower(), f"dashboard root body: {body[:300]!r}"
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)

    print(f"wheel smoke OK — package {paths.PACKAGE_ROOT}, user data {home_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
