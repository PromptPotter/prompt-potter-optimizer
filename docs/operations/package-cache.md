# Package cache — keeping the network out of a containerized measurement

## The rule

**A containerized cell measures the agent, never the machine's network.** A benchmark whose
container downloads its own tools at run time — terminus-2 installing `tmux`, a SpreadsheetBench
verifier installing LibreOffice (167 packages, 134 MB, per cell) — makes every cell depend on a
package mirror. That costs wall clock on every cell, and during an outage it produces one of two
things, both wrong:

- **a hole scored as the candidate's** — the build or setup fails and the cell reads as unscoreable;
- **a silent wrong number** — `set -e` does not stop on a failed `apt-get update && apt-get install`,
  so the verifier grades without the tools it went to fetch, and the reward is written.

Three mechanisms close this, and none of them changes measurement identity:

1. **Fetch once per machine.** One cache container serves every opted-in cell's downloads.
2. **An infrastructure failure is never a measurement.** A cell whose trial shows a
   registry/mirror/DNS failure, or whose setup or verifier ran out of clock, is retried by the
   connector with bounded backoff. The same applies to an episode that a model provider's throttle
   ended, or that ran out of clock after one. A throttle the episode outlived is only latency, and
   its grade stands. If the retries still fail, the cell is banked as `ErrorCategory.CONNECTION`
   (`shared/errors.py::CellInfrastructureError`), carrying what every attempt spent so the one catch
   in `measure_sample` bills it, and the walk halts with `StopReason.BACKEND_UNREACHABLE`. A
   provider account out of credit is not retried: it is banked as `ErrorCategory.PROVIDER_CREDIT`
   (`CellCreditExhaustedError`) on the first attempt and halts with `StopReason.PROVIDER_CREDIT`.
   Either cell stays a hole that `resume` re-measures. There is no fallback to direct downloads: in
   an outage it fails the same way, one path later.
3. **No registry call once a task image exists.** A kept `hb__<hash>` tag starts as a prebuilt
   image, so a cell no longer resolves the task's `FROM` against its registry.

## The cache

- **One container per machine:** `promptpotter-package-cache` (same name for its image and
  volume), running `apt-cacher-ng` from Debian's own archive, published on port 3142 with
  `--restart unless-stopped`. The cell path starts it, once per process, before the first cell of an
  opted-in dataset. Doing it there rather than in `Connector.preflight` means every entry point
  that measures a cell gets it, including the diagnostics and the embedded launch, which never run
  preflight.
- **Reached at `host.docker.internal:3142`.** Docker Desktop resolves that name natively; on Linux
  (the Fedora deploy box) the compose overlay maps it with `host-gateway`. Untested on Fedora:
  firewalld must let bridge traffic reach a published port, which Docker's own NAT rules normally
  do.
- **Operating it:** `docker logs promptpotter-package-cache` shows cache hits. `docker rm -f
  promptpotter-package-cache` stops it, and the next opted-in cell starts it again. `docker volume rm
  promptpotter-package-cache` drops the downloads.

## What fits the seam, and what does not

| Tool | Fits | Why |
|---|---|---|
| apt (Debian/Ubuntu images) | yes | `http_proxy` is honoured and the default mirrors are plain HTTP, so apt-cacher-ng caches them. |
| pip | no, cheaply | PyPI is HTTPS-only, so an HTTP proxy can only tunnel it, not cache it. It needs a PyPI mirror (`PIP_INDEX_URL`), which is a second service. |
| npm | no, cheaply | Same reason; it needs a registry mirror (`npm_config_registry`). |

## Scope — the identity trade

A dataset opts in with `package_cache: <scope>` in its experiment file. Only harbor honours one, in
`harbor_tasks.yaml`, and `harbor.py::PACKAGE_CACHE_SCOPES` is the set it accepts.

- **`verifier`** — the proxy rides `VerifierConfig.env`, which Harbor passes to the verifier's
  exec alone. The agent cannot observe it and the packages are the same signature-checked files, so
  **measurement identity does not change** (`_identity_config` does not hash the key).
- **`agent`** — deliberately not offered as a proxy scope: a proxy in the container environment is
  something the agent sees and inherits. The agent's own setup downloads are kept out a different
  way. `harbor.py::_reuse_task_image` builds an agent-ready tag `FROM` the kept task image and
  runs the agent's own install there, at build time. That install goes through the cache as a
  build argument, which Docker keeps out of the image environment and its history. The agent's
  setup then finds its tools present and installs nothing. The container is the one setup would
  have produced — same tool versions, same apt lists, no proxy anywhere — so identity is unchanged.
  terminus-2 declares no tool list, so `_AGENT_TOOLS` copies its pair.
