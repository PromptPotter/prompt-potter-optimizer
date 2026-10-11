from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.store.io import rmtree_robust
from promptpotter.shared.errors import CellInfrastructureError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

logger = logging.getLogger(__name__)


# The token's lock is how the next run tells what a hard kill left from a live sibling.
CONTAINER_PRODUCER = f"{os.getpid():x}{uuid4().hex[:4]}"
_PRODUCER_LABEL = "com.promptpotter.producer"
_COMPOSE_FILES_LABEL = "com.docker.compose.project.config_files"
_PRODUCER_LOCK = ".producer.lock"
# ONE home: under a second, the sweep reaps a live sibling as scratch-less. Temp dir: MAX_PATH.
_PRODUCERS_HOME = Path(tempfile.gettempdir()) / "promptpotter-cells"
PRODUCER_SCRATCH = _PRODUCERS_HOME / CONTAINER_PRODUCER
_SWEPT: set[Path | None] = set()

PACKAGE_CACHE = "promptpotter-package-cache"
_PACKAGE_CACHE_PORT = 3142
PACKAGE_CACHE_PROXY = f"http://host.docker.internal:{_PACKAGE_CACHE_PORT}"
_PACKAGE_CACHE_DOCKERFILE = (
    "FROM debian:bookworm-slim\n"
    "RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y "
    "--no-install-recommends apt-cacher-ng && rm -rf /var/lib/apt/lists/*\n"
    f"EXPOSE {_PACKAGE_CACHE_PORT}\n"
    'CMD ["/usr/sbin/apt-cacher-ng", "-c", "/etc/apt-cacher-ng", "ForeGround=1"]\n'
)
_package_cache_running = False


async def docker(
    *args: str,
    stdin: bytes | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, **env} if env else None,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"`docker {' '.join(args[:2])}` did not answer in {timeout:.0f}s"
        ) from None
    return proc.returncode or 0, (out or b"").decode(errors="replace").strip()


async def docker_daemon_fault() -> str | None:
    try:
        code, out = await docker("version", "--format", "{{.Server.Version}}")
    except (OSError, TimeoutError) as exc:
        return f"docker not callable: {exc}"
    return None if code == 0 else f"docker daemon not responding: {out[:200]}"


@contextlib.contextmanager
def machine_step() -> Iterator[None]:
    try:
        yield
    except (OSError, TimeoutError) as exc:
        raise CellInfrastructureError(str(exc), spent={}) from exc


async def run_cell_container(
    name: str, *args: str, env: Mapping[str, str], timeout: float
) -> tuple[int, str]:
    label = f"{_PRODUCER_LABEL}={CONTAINER_PRODUCER}"
    try:
        return await docker(
            "run", "--rm", "--name", name, "--label", label, *args, env=env, timeout=timeout
        )
    except (asyncio.CancelledError, TimeoutError):
        # Left alone, the container goes on calling the provider.
        with contextlib.suppress(OSError, TimeoutError):
            await asyncio.shield(docker("kill", name))
        raise


async def ensure_package_cache() -> None:
    global _package_cache_running
    if _package_cache_running:
        return
    running = ("container", "inspect", "--format", "{{.State.Running}}", PACKAGE_CACHE)
    code, state = await docker(*running)
    if code != 0:
        if (await docker("image", "inspect", PACKAGE_CACHE))[0] != 0:
            code, out = await docker(
                "build", "-t", PACKAGE_CACHE, "-",
                stdin=_PACKAGE_CACHE_DOCKERFILE.encode(),
                timeout=600,
            )  # fmt: skip
            if code != 0:
                raise CellInfrastructureError(
                    f"building {PACKAGE_CACHE} failed: {out[-300:]}", spent={}
                )
        # Unchecked: a sibling racing this wins the name, and the re-inspect finds its container.
        await docker(
            "run", "--detach", "--name", PACKAGE_CACHE, "--restart", "unless-stopped",
            "--publish", f"{_PACKAGE_CACHE_PORT}:{_PACKAGE_CACHE_PORT}",
            "--volume", f"{PACKAGE_CACHE}:/var/cache/apt-cacher-ng",
            PACKAGE_CACHE,
        )  # fmt: skip
    elif state != "true":
        await docker("start", PACKAGE_CACHE)
    code, state = await docker(*running)
    if state != "true":
        raise CellInfrastructureError(f"{PACKAGE_CACHE} is not running: {state[-300:]}", spent={})
    _package_cache_running = True


def forget_package_cache() -> None:
    global _package_cache_running
    _package_cache_running = False


def _producer_is_dead(home: Path, token: str) -> bool:
    # The lock, never an mtime: a cell runs for minutes writing nothing.
    return not producer_lock.held(home / token / _PRODUCER_LOCK)


async def reap_dead_producers(home: Path, *, compose_overlay: Path | None) -> None:
    code, out = await docker(
        "ps", "-a", "--format",
        f'{{{{.ID}}}}|{{{{.Label "{_PRODUCER_LABEL}"}}}}|{{{{.Label "{_COMPOSE_FILES_LABEL}"}}}}',
    )  # fmt: skip
    ours = None if compose_overlay is None else str(compose_overlay).lower()
    by_token: dict[str, list[str]] = {}
    gone: list[str] = []
    for line in out.splitlines() if code == 0 else []:
        cid, _, rest = line.partition("|")
        token, _, compose_files = rest.partition("|")
        if not cid:
            continue
        if token.strip():
            by_token.setdefault(token.strip(), []).append(cid)
        # Unlabelled yet naming our overlay is nobody's: nothing this code starts is unlabelled.
        elif ours is not None and ours in compose_files.lower():
            gone.append(cid)
    for token in by_token.keys() | {d.name for d in home.iterdir() if d.is_dir()}:
        if token == CONTAINER_PRODUCER or not await asyncio.to_thread(
            _producer_is_dead, home, token
        ):
            continue
        gone += by_token.get(token, [])
        with contextlib.suppress(OSError):
            await asyncio.to_thread(rmtree_robust, home / token)
    if gone:
        await docker("rm", "--force", *gone, timeout=120)
        logger.info("removed %d container(s) left by a run that is gone", len(gone))


async def claim_machine(*, compose_overlay: Path | None) -> None:
    if compose_overlay in _SWEPT:
        return
    producer_lock.take(PRODUCER_SCRATCH / _PRODUCER_LOCK)
    _SWEPT.add(compose_overlay)
    try:
        await reap_dead_producers(_PRODUCERS_HOME, compose_overlay=compose_overlay)
    except (OSError, TimeoutError) as exc:
        # Not re-raised into `machine_step`: a failed sweep leaves junk, never a failed cell.
        logger.debug("could not reap what earlier runs left: %s", exc)
