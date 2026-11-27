"""The app a browser world walks, behind the world its posture names: ``live`` reaches whatever
``TERMNORM_ENDPOINTS`` and every other host name, ``offline`` answers every send of this process with
``scripts/offline_world.py::Router`` (its ``FakeBackend``, an empty model catalogue, the bundled price
floor) and resolves no name — so a check-in reads the same world on every machine, CI's included,
and no request leaves for a proxy to refuse."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _install_offline_world() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    from offline_world import Router, install_network

    install_network(Router.idle())
    print("[e2e] offline world: every send answered in-process", flush=True)


def main(argv: list[str]) -> int:
    backend, port = [*argv, "", ""][:2]
    if backend not in ("--backend=offline", "--backend=live") or not port.isdigit():
        print("[e2e] usage: serve_app.py --backend=offline|live <port>", file=sys.stderr)
        return 2
    if backend == "--backend=offline":
        _install_offline_world()
    from promptpotter.application import health

    # The page under test is this checkout's build, by the check the app's own `/health` says.
    bundle = health.webapp_bundle()
    if bundle is None or bundle.state != "held":
        said = "no webapp/out/ built" if bundle is None else bundle.said
        print(f"[e2e] REFUSING to serve webapp/out/: {said}; {health.REBUILD}", file=sys.stderr)
        return 2
    import uvicorn

    uvicorn.run("promptpotter.main:app", host="127.0.0.1", port=int(port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
