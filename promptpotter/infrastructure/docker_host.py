"""The Docker host a containerized connector runs its cells on — the `docker` CLI, the daemon
probe, a cell's labelled container, the machine's package cache, and the sweep of what a killed
run left behind."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from filelock import FileLock, Timeout

from promptpotter.infrastructure.store.io import rmtree_robust
from promptpotter.shared.errors import CellInfrastructureError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

logger = logging.getLogger(__name__)


# What one process leaves on this machine under one token: its scratch directory, and the label
# every container it starts carries. The token's lock is how the next run tells what a hard kill
# left from a live sibling, the kernel dropping it with its holder exactly as machine slots rely
# on (`backend.py::MachineSlots`).
CONTAINER_PRODUCER = f"{os.getpid():x}{uuid4().hex[:4]}"
_PRODUCER_LABEL = "com.promptpotter.producer"
# Compose's own record of the files a container was built from, which names a connector's overlay
# — the one marker on an unlabelled container.
_COMPOSE_FILES_LABEL = "com.docker.compose.project.config_files"
_PRODUCER_LOCK = ".producer.lock"
# ONE home for every connector's cells: under a second, the sweep reads a live sibling's token as
# a producer with no scratch, and reaps it. In the system temp dir: a workspace path nears MAX_PATH.
_PRODUCERS_HOME = Path(tempfile.gettempdir()) / "promptpotter-cells"
PRODUCER_SCRATCH = _PRODUCERS_HOME / CONTAINER_PRODUCER
_PRODUCER_HELD = FileLock(str(PRODUCER_SCRATCH / _PRODUCER_LOCK), timeout=0)
# The overlays swept for, ``None`` standing for the sweep that names none.
_SWEPT: set[Path | None] = set()

# The machine's package-download cache — one container, image and volume of this name
# (`docs/operations/package-cache.md`). `apt-cacher-ng` from Debian's own archive, not a
# third-party image.
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
# Process-scoped: the container is a fact about the machine, not a run.
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
    """Why the daemon cannot run a cell, for a ``Connector.preflight`` — a container runtime must
    answer before a campaign starts spending — or ``None`` where it answers."""
    try:
        code, out = await docker("version", "--format", "{{.Server.Version}}")
    except (OSError, TimeoutError) as exc:
        return f"docker not callable: {exc}"
    return None if code == 0 else f"docker daemon not responding: {out[:200]}"


@contextlib.contextmanager
def machine_step() -> Iterator[None]:
    """A docker CLI unreachable or silent at cell time is the machine's fault: the cell measured
    nothing and is never banked as the candidate's."""
    try:
        yield
    except (OSError, TimeoutError) as exc:
        raise CellInfrastructureError(str(exc), spent={}) from exc


async def run_cell_container(
    name: str, *args: str, env: Mapping[str, str], timeout: float
) -> tuple[int, str]:
    """One cell's ``docker run``, labelled this producer's so the sweep finds what a hard kill left.
    Killed where its caller stops waiting: left alone, the container goes on calling the provider."""
    label = f"{_PRODUCER_LABEL}={CONTAINER_PRODUCER}"
    try:
        return await docker(
            "run", "--rm", "--name", name, "--label", label, *args, env=env, timeout=timeout
        )
    except (asyncio.CancelledError, TimeoutError):
        with contextlib.suppress(OSError, TimeoutError):
            await asyncio.shield(docker("kill", name))
        raise


async def ensure_package_cache() -> None:
    """The machine's package cache, running. Idempotent, and safe against a sibling cell racing it:
    a second ``run`` loses on the name and the re-inspect finds the winner's container."""
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
    """Whether the process behind *token* is gone. Asked of the lock it holds for its own life,
    never of an mtime: a cell runs for minutes writing nothing, and a sibling's live containers are
    not this run's to remove."""
    scratch = home / token
    if not scratch.is_dir():
        return True
    lock = FileLock(str(scratch / _PRODUCER_LOCK), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        return False
    lock.release()
    return True


async def reap_dead_producers(home: Path, *, compose_overlay: Path | None) -> None:
    """Remove the containers and scratch of every dead producer under *home*. An unlabelled
    container naming *compose_overlay* is nobody's: nothing this code starts is unlabelled."""
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
    """Hold this process's scratch and its lock, then sweep dead producers, once per
    *compose_overlay*. Runs at ``backend.py::MachineSlots.hold``, never in ``Connector.preflight``."""
    if compose_overlay in _SWEPT:
        return
    PRODUCER_SCRATCH.mkdir(parents=True, exist_ok=True)
    _PRODUCER_HELD.acquire()
    _SWEPT.add(compose_overlay)
    try:
        await reap_dead_producers(_PRODUCERS_HOME, compose_overlay=compose_overlay)
    except (OSError, TimeoutError) as exc:
        # Not re-raised into `machine_step`: a failed sweep leaves junk, never a failed cell.
        logger.debug("could not reap what earlier runs left: %s", exc)
