# Package cache — keeping the network out of a containerized measurement

## The rule

**A containerized cell measures the agent, never the machine's network.** A container that downloads its own tools at run time (terminus-2 installing `tmux`, a verifier installing LibreOffice) makes every cell depend on a mirror, and an outage then yields **a hole scored as the candidate's**, or **a silent wrong number** — `set -e` does not stop a failed `apt-get update && apt-get install`, so the verifier grades without its tools. Three mechanisms close it, none changing measurement identity:

1. **Fetch once per machine** — one cache container serves every opted-in cell.
2. **An infrastructure failure is never a measurement.** A registry/mirror/DNS failure, or a setup or verifier out of clock, is retried with bounded backoff; still failing, it banks as `ErrorCategory.CONNECTION` (`shared/errors.py::CellInfrastructureError`, carrying every attempt's spend) and halts the walk `BACKEND_UNREACHABLE`. A provider out of credit is not retried: `PROVIDER_CREDIT` (`CellSendRefusedError`). Either stays a hole `resume` re-measures. **No fallback to direct downloads** — in an outage it fails the same way, one path later. An episode a provider throttle ended is the backpressure's to resend (`infrastructure/llm/send_pacing.py::Backpressure`); a throttle it outlived is latency, and its grade stands.
3. **No registry call once a task image exists** — a kept `hb__<hash>` tag starts as a prebuilt image.

## The cache

- **One container per machine**, `promptpotter-package-cache` (image and volume alike): `apt-cacher-ng` on port 3142, `--restart unless-stopped`. The cell path starts it before the first opted-in cell (`infrastructure/docker_host.py::ensure_package_cache`), not `Connector.preflight`, so the diagnostics and the embedded launch get it too.
- **Reached at `host.docker.internal:3142`** — native on Docker Desktop; on Linux the compose overlay maps it with `host-gateway`, and firewalld must let bridge traffic reach the published port.
- **Operating it:** `docker logs promptpotter-package-cache` shows hits; `docker rm -f` stops it (the next opted-in cell restarts it); `docker volume rm` drops the downloads.
- **A stale index takes down every cell on the box and reads as the backend being unreachable** — the cache answers `200`, apt refuses the signature, every task burns its infra attempts. Tell it from an outage by fetching without the proxy. The cure is the INDEX, not the volume: delete `InRelease`, `Release` and `Packages` under the cache and restart, keeping the `.deb`s.

## What fits the seam

apt fits: it honours `http_proxy` and its default mirrors are plain HTTP. pip and npm do not, cheaply — their registries are HTTPS-only, so a proxy can tunnel but not cache them; each needs its own mirror service.

## Scope — the identity trade

A dataset opts in with `package_cache: <scope>` in its experiment file. A connector declares its scopes on `Connector.package_cache_scopes`, and run init refuses any other (`wiring.py::_verify_package_cache_scope`) — so a backend declaring none refuses the key rather than ignoring it. Harbor declares `harbor.py::PACKAGE_CACHE_SCOPES`:

- **`verifier`** — the proxy rides `VerifierConfig.env`, the verifier's exec alone. The agent cannot observe it and the packages are the same signature-checked files, so identity does not change (`_identity_config` does not hash the key).
- **`agent` is deliberately not a proxy scope** — a proxy in the container environment is something the agent sees. Instead `harbor.py::_reuse_task_image` builds an agent-ready tag `FROM` the kept task image and runs the agent's install at build time, through the cache as a build argument Docker keeps out of the image. The container is the one setup would have produced, with no proxy anywhere. terminus-2 declares no tool list, so `_AGENT_TOOLS` copies its pair.
