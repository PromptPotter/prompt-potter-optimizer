from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path

from promptpotter.config.paths import PRODUCERS_DIR
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.store.io import rmtree_robust, validate_path_component
from promptpotter.shared.errors import CellInfrastructureError

logger = logging.getLogger(__name__)


_PRODUCER_LABEL = "com.promptpotter.producer"
_COMPOSE_FILES_LABEL = "com.docker.compose.project.config_files"
# Scratch only, in the temp dir for MAX_PATH: a private /tmp is per process, so no liveness lives here.
_SCRATCH_HOME = Path(tempfile.gettempdir()) / "promptpotter-cells"
_SWEPT: set[tuple[Path, Path | None]] = set()
# The producer token this process's containers carry (`producer_lock.py::this_producer`), set by
# its first claim: its lock is how the next run tells what a hard kill left from a live sibling.
_claimed: str | None = None


def machine_producer() -> str:
    """The token a container started here is labelled with; only a claimed machine starts one."""
    if _claimed is None:
        raise RuntimeError("no machine claimed: `claim_machine` runs before any container starts")
    return _claimed


def producer_scratch() -> Path:
    return _SCRATCH_HOME / machine_producer()


PACKAGE_CACHE = "promptpotter-package-cache"
_PACKAGE_CACHE_PORT = 3142
# How a cell reaches this host: every container and image build started here carries it.
HOST_ROUTE = "host.docker.internal:host-gateway"
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
    label = f"{_PRODUCER_LABEL}={machine_producer()}"
    try:
        return await docker(
            "run", "--rm", "--name", name, "--label", label, "--add-host", HOST_ROUTE, *args,
            env=env, timeout=timeout,
        )  # fmt: skip
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


def _dead_producers(home: Path) -> set[str]:
    # Only a token this home minted is judged: another home's lock is not here to read.
    locks = home.glob("*.lock") if home.is_dir() else ()
    # The lock, never an mtime: a cell runs for minutes writing nothing; this process's own reads held.
    return {lock.stem for lock in locks if not producer_lock.held(lock)}


def _forget_producers(home: Path, dead: set[str]) -> None:
    scratch = _SCRATCH_HOME.iterdir() if _SCRATCH_HOME.is_dir() else ()
    for left in [d for d in scratch if d.name in dead]:
        with contextlib.suppress(OSError):
            rmtree_robust(left)
    for token in dead:
        producer_lock.reclaim_producer(home / f"{token}.lock")


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
        label, _, compose_files = rest.partition("|")
        if not cid:
            continue
        if token := label.strip():
            # Any container may carry the label: a token no path component spells is dropped.
            with contextlib.suppress(ValueError):
                by_token.setdefault(validate_path_component(token), []).append(cid)
        # Unlabelled yet naming our overlay is nobody's: nothing this code starts is unlabelled.
        elif ours is not None and ours in compose_files.lower():
            gone.append(cid)
    dead = await asyncio.to_thread(_dead_producers, home)
    gone += [cid for token in dead for cid in by_token.get(token, [])]
    if gone:
        code, out = await docker("rm", "--force", *gone, timeout=120)
        if code != 0:
            logger.debug("could not remove what earlier runs left: %s", out[-300:])
            return
        logger.info("removed %d container(s) left by a run that is gone", len(gone))
    await asyncio.to_thread(_forget_producers, home, dead)


async def claim_machine(jobs_dir: Path, *, compose_overlay: Path | None) -> None:
    """This process's producer token labels its containers; the first claim per home and overlay
    reaps what a dead producer left."""
    global _claimed
    home = jobs_dir / PRODUCERS_DIR
    if (home, compose_overlay) in _SWEPT:
        return
    _claimed = producer_lock.this_producer(jobs_dir)
    producer_scratch().mkdir(parents=True, exist_ok=True)
    _SWEPT.add((home, compose_overlay))
    try:
        await reap_dead_producers(home, compose_overlay=compose_overlay)
    except (OSError, TimeoutError) as exc:
        # Not re-raised into `machine_step`: a failed sweep leaves junk, never a failed cell.
        logger.debug("could not reap what earlier runs left: %s", exc)
