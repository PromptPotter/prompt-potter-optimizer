"""The app a browser world walks, behind the backend its posture names: ``live`` reaches whatever
``TERMNORM_URL`` names, ``offline`` answers it with ``scripts/offline_world.py::FakeBackend`` — so a
check-in reads the same backend schema on every machine, CI's included, whether or not one runs."""

from __future__ import annotations

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from offline_world import FakeBackend

REPO = Path(__file__).resolve().parents[2]


class _Backend(BaseHTTPRequestHandler):
    world: ClassVar[FakeBackend]

    def _answer(self) -> None:
        import httpx

        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        request = httpx.Request(self.command, f"http://{self.headers['Host']}{self.path}")
        answered = self.world.respond(request, body)
        assert answered is None or isinstance(answered, httpx.Response), "no fault is scripted"
        status, content = (
            (404, b'{"error": "offline backend: no route"}')
            if answered is None
            else (answered.status_code, answered.content)
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        self._answer()

    def do_POST(self) -> None:
        self._answer()


def _serve_offline_backend() -> None:
    # Bound before `promptpotter` imports: settings read `TERMNORM_URL` once, at import.
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Backend)
    os.environ["TERMNORM_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
    sys.path.insert(0, str(REPO / "scripts"))
    from offline_world import FakeBackend

    _Backend.world = FakeBackend([])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[e2e] offline backend at {os.environ['TERMNORM_URL']}", flush=True)


def main(argv: list[str]) -> int:
    backend, port = [*argv, "", ""][:2]
    if backend not in ("--backend=offline", "--backend=live") or not port.isdigit():
        print("[e2e] usage: serve_app.py --backend=offline|live <port>", file=sys.stderr)
        return 2
    if backend == "--backend=offline":
        _serve_offline_backend()
    import uvicorn

    uvicorn.run("promptpotter.main:app", host="127.0.0.1", port=int(port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
