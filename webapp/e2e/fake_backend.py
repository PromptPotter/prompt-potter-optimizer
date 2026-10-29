"""TermNorm's wire on a local port, answered by the offline world's `FakeBackend`
(`scripts/offline_world.py`): the throwaway world reads a backend schema with no TermNorm up. It
measures nothing — the cold tier mints no campaign, and an armed spend tier walks the real one."""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from offline_world import FakeBackend  # noqa: E402

BACKEND = FakeBackend([])


class _Wire(BaseHTTPRequestHandler):
    def _answer(self) -> None:
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        request = httpx.Request(self.command, f"http://{FakeBackend.AT}{self.path}")
        answer = BACKEND.respond(request, body)
        if not isinstance(answer, httpx.Response):
            self.send_error(404)
            return
        self.send_response(answer.status_code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(answer.content)))
        self.end_headers()
        self.wfile.write(answer.content)

    do_GET = do_POST = _answer


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("[e2e] usage: fake_backend.py <port>", file=sys.stderr)
        return 2
    ThreadingHTTPServer(("127.0.0.1", int(argv[0])), _Wire).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
