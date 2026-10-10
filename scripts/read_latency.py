"""Time the webapp's hot reads against one campaign — the before/after table for read-path work.

    python scripts/read_latency.py --campaign C --cycle Y --dataset D            # a running server
    python scripts/read_latency.py --campaign C --cycle Y --dataset D --in-process  # this checkout, fresh

``app`` is the server's own ``Server-Timing``; wall minus app is queueing and transfer."""

from __future__ import annotations

import argparse
import statistics
import time
import tracemalloc
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Answer:
    status: int
    wall_ms: float
    app_ms: float | None
    size: int
    encoding: str
    validator: dict[str, str]


def _app_ms(header: str | None) -> float | None:
    if not header or "dur=" not in header:
        return None
    return float(header.split("dur=", 1)[1].split(",", 1)[0].split(";", 1)[0])


def _validator(headers: dict[str, str]) -> dict[str, str]:
    if etag := headers.get("etag"):
        return {"If-None-Match": etag}
    if modified := headers.get("last-modified"):
        return {"If-Modified-Since": modified}
    return {}


def _over_http(base: str, path: str, send: dict[str, str]) -> Answer:
    request = urllib.request.Request(base + path, headers={"Accept-Encoding": "gzip", **send})
    began = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
            status, raw = response.status, response.headers
    except urllib.error.HTTPError as err:
        body, status, raw = err.read(), err.code, err.headers
    wall = (time.perf_counter() - began) * 1000
    headers = {k.lower(): v for k, v in raw.items()}
    return Answer(
        status,
        wall,
        _app_ms(headers.get("server-timing")),
        len(body),
        headers.get("content-encoding", "-"),
        _validator(headers),
    )


class _InProcess:
    def __init__(self) -> None:
        from fastapi.testclient import TestClient

        from promptpotter.main import app

        self._client = TestClient(app)
        self._client.__enter__()

    def __call__(self, base: str, path: str, send: dict[str, str]) -> Answer:
        began = time.perf_counter()
        response = self._client.get(base + path, headers={"Accept-Encoding": "gzip", **send})
        wall = (time.perf_counter() - began) * 1000
        headers = {k.lower(): v for k, v in response.headers.items()}
        return Answer(
            response.status_code,
            wall,
            _app_ms(headers.get("server-timing")),
            int(headers.get("content-length", len(response.content))),
            headers.get("content-encoding", "-"),
            _validator(headers),
        )

    def close(self) -> None:
        self._client.__exit__(None, None, None)


def _reads(campaign: str, cycle: str, dataset: str) -> list[tuple[str, str]]:
    course = f"/campaigns/{campaign}/cycles/{cycle}"
    return [
        ("campaigns", "/campaigns"),
        ("cycles", "/cycles"),
        ("sessions/active", "/sessions/active"),
        ("dashboard", f"{course}/dashboard"),
        ("ray", f"{course}/ray?limit=200"),
        ("tree", f"{course}/tree"),
        ("pipeline", f"/campaigns/{campaign}/pipeline"),
        (
            "cells campaign",
            f"/datasets/{dataset}/cells?limit=1000&scope=campaign&campaign_id={campaign}",
        ),
        ("cells dataset", f"/datasets/{dataset}/cells"),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--cycle", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--in-process", action="store_true")
    parser.add_argument("--skip", action="append", default=[], help="a read's name; repeatable")
    args = parser.parse_args()

    in_process = _InProcess() if args.in_process else None
    fetch = in_process or _over_http
    base = "/api/v1" if in_process else f"http://127.0.0.1:{args.port}/api/v1"
    if in_process:
        tracemalloc.start()

    print(
        f"{'read':<17}{'st':>4}{'p50 ms':>9}{'p95 ms':>9}{'app ms':>9}{'bytes':>10}  {'enc':<5}{'304 ms':>8}"
    )
    for name, path in _reads(args.campaign, args.cycle, args.dataset):
        if name in args.skip:
            continue
        answers = [fetch(base, path, {}) for _ in range(args.n)]
        walls = sorted(a.wall_ms for a in answers)
        last = answers[-1]
        apps = [a.app_ms for a in answers if a.app_ms is not None]
        revalidated = fetch(base, path, last.validator) if last.validator else None
        not_modified = (
            f"{revalidated.wall_ms:8.1f}"
            if revalidated and revalidated.status == 304
            else f"{'-':>8}"
        )
        print(
            f"{name:<17}{last.status:>4}{statistics.median(walls):>9.1f}"
            f"{walls[min(len(walls) - 1, round(0.95 * (len(walls) - 1)))]:>9.1f}"
            f"{(statistics.median(apps) if apps else float('nan')):>9.1f}"
            f"{last.size:>10}  {last.encoding:<5}{not_modified}"
        )

    if in_process:
        _, peak = tracemalloc.get_traced_memory()
        print(f"tracemalloc peak: {peak / 1e6:.1f} MB")
        in_process.close()


if __name__ == "__main__":
    main()
