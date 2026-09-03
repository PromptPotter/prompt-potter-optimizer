"""Harbor-as-connector — one containerized agent EPISODE as one measured cell. A THIN adapter:
it declares ``execution="in_process"`` and delegates to Harbor's own trial runner, because an
episode is not a wire binding.

**What is being optimized here is an Agent Skill**, not a request body. The candidate's rendered
prompt is written as a ``SKILL.md`` and injected through ``AgentConfig.skills``; Harbor uploads it
into the container and the agent discovers it there. That is the same artifact class the
skill-evolution literature evolves, reached through a channel Harbor already ships — so the
comparison is against their object rather than against something merely analogous to it.
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.connectors.protocol import BackendUnreachableError, Connector
from promptpotter.domain.pipeline_overlay import node_config_items

if TYPE_CHECKING:
    from types import ModuleType

    import httpx
    from harbor.models.trial.result import TrialResult

logger = logging.getLogger(__name__)


# The one node a harbor dataset declares. ONE, because an episode is a single call from here —
# the agent's turns are its own loop, not a chain we route a query through node by node.
AGENT_NODE = "agent"

# What the campaign formula scores: the verifier's own number. `VerifierResult.rewards` is a
# NAMED dict, so this is a key lookup rather than a scalar read — a task writing a bare
# `reward.txt` is parsed by Harbor into `{"reward": x}`, which is why that is the default key.
REWARD_KEY = "env_reward"
DEFAULT_TASK_REWARD_KEY = "reward"

# NO `final_ranking` here, and its absence is the declaration. This connector emitted one — a
# one-element list holding `harbor:{task} reward={x}` — purely so it would look ranked-label
# shaped, and its own comment conceded it decided nothing. Nothing read it: the `agent` node
# declares no `node_role`, so `emits_ranking` is False and `terminal_ranking` returns `[]`
# regardless. What it cost was three readers having to un-believe it. Do not restore it, and do
# not reach the same place by declaring the agent a RANKER: that switches on `candidate_recall`,
# which walks a ranking for a ground truth this backend does not have and banks the 0.0.

# Declares the tasks, their pins and the agent. Same role `inner_tasks.yaml` plays for L4: the
# dataset's "samples" ARE the tasks named here, so there is no CSV table.
TASKS_FILE = "harbor_tasks.yaml"

# Reserved per-node config key carrying the instrument fingerprint (see `_identity_config`).
# Part of measurement identity, NEVER a wire tunable — the adapter strips it.
INSTRUMENT_KEY = "harbor_instrument"

# Harbor's kwargs that a `Node.tune` entry may move on the agent. Anything else in the node
# config is ours (or the fingerprint) and does not reach Harbor.
AGENT_KWARG_KEYS = frozenset(
    {
        "max_turns",
        "temperature",
        "reasoning_effort",
        "parser_name",
        "enable_summarize",
        "interleaved_thinking",
        "max_thinking_tokens",
    }
)

# Where trial scratch goes. NOT under the workspace, and that is deliberate: Harbor nests
# `<trials_dir>/<trial_name>/<role>/…` and a workspace path is already deep, which is the same
# 260-char MAX_PATH wall that forced L4's `.inner` registry flat. Nothing durable lives here —
# the reward, the digest and the token counts are projected into the measurement archive, which
# is where a fact is supposed to land.
_TRIALS_ROOT = Path(tempfile.gettempdir()) / "promptpotter-harbor"

# The skill's frontmatter `description` — FIXED, never a search axis, and that is an instrument
# decision rather than a shortcut. The agent sees only name + description eagerly and must open
# the file to read the body, so a candidate free to write its own description could win by
# making itself uninviting: the agent never reads the skill, the arm scores as no-skill, and a
# degenerate hiding strategy reads as a discovery. Holding the hook constant is what makes the
# body the thing that varied.
_SKILL_NAME = "task-approach"
_SKILL_DESCRIPTION = (
    "Read this before acting. Required approach, conventions and completion criteria for "
    "this task. Always consult it first."
)


class HarborSession:
    """In-process noop session — no remote service, so no handshake to make or recover."""

    __slots__ = ()

    async def set_terms(
        self, http: httpx.AsyncClient, base_url: str, terms: list[str]
    ) -> dict[str, Any]:
        return {"status": "noop", "terms_count": len(terms)}

    async def recover(self, http: httpx.AsyncClient, base_url: str) -> bool:
        return True


# The declared panel. A ContextVar for the same reason the dspy connector holds its student in
# one: ``in_process_run`` is a module-level hook the loop calls with ``(query, payload)`` and no
# call-site state, so a task's pins have to be reachable without an argument to carry them.
#
# **Read it as a ContextVar, not as a module global.** Campaigns run as `asyncio.create_task`
# siblings (`jobs/launcher/mint_and_start.py`), so each COPIES the context at spawn and two
# concurrent Harbor campaigns cannot see each other's panel. Simplify this to a plain global on
# the belief that it is one, and they clobber each other silently.
#
# That both in-process connectors needing per-run state invented this independently is the tell
# that `InProcessRun` is missing an arming argument — filed in
# `docs/specs/code-debt-cleanup.md`; the fix DELETES both ContextVars.
_PANEL: ContextVar[dict[str, Any] | None] = ContextVar("harbor_panel", default=None)


# Rosters already resolved, keyed by ``(dataset, version)``. Harbor's registry is one JSON file
# fetched over the network, and init asks for the roster twice — once for the samples, once for
# the instrument fingerprint — so resolving per process is the difference between one fetch and a
# fetch per question.
_ROSTER_CACHE: dict[tuple[str, str], list[dict[str, Any]]] = {}


def _registry_tasks(dataset: str, version: str) -> list[dict[str, Any]]:
    """The roster of a PUBLISHED Harbor dataset, resolved from Harbor's own registry.

    **The task list is not ours to copy.** Harbor publishes it, pins every task to a commit, and
    versions the set. Committing those rows into this repo would make us a second owner of a list
    that already has one, and the copy would drift the moment upstream repinned — silently, since
    nothing compares them. What a dataset here commits is the NAME and the VERSION.

    That is safe only because the resolved pins are folded into the instrument fingerprint
    (:func:`_identity_config`): if upstream moves a commit under a version, measurement identity
    changes and the banked rows are not reused, instead of being quietly re-served as if they
    described the new bytes. The version is REQUIRED for the same reason — resolving "latest"
    would put a moving target behind a fixed dataset name.
    """
    if (cached := _ROSTER_CACHE.get((dataset, version))) is not None:
        return cached

    from harbor.registry.client.json import JsonRegistryClient

    specs = JsonRegistryClient().dataset_specs.get(dataset)
    if not specs:
        raise ValueError(
            f"harbor connector: no dataset {dataset!r} in Harbor's registry. "
            f"`harbor dataset list` names what is published."
        )
    spec = specs.get(version)
    if spec is None:
        raise ValueError(
            f"harbor connector: dataset {dataset!r} has no version {version!r} "
            f"(published: {sorted(specs)})."
        )
    tasks = [
        {
            "id": t.name,
            "git_url": t.git_url,
            "git_commit_id": t.git_commit_id,
            "path": str(t.path),
        }
        for t in spec.tasks
    ]
    _ROSTER_CACHE[(dataset, version)] = tasks
    return tasks


def _panel_tasks(panel: dict[str, Any]) -> list[dict[str, Any]]:
    """The episodes this dataset measures, from whichever of Harbor's two task sources it names.

    Mirrors ``TaskConfig``'s own split rather than inventing one: a published dataset resolved by
    ``harbor_dataset`` + ``harbor_dataset_version``, or tasks declared inline for a locally
    authored panel. A committed dataset uses the first — see :func:`_registry_tasks`.
    """
    if inline := panel.get("tasks"):
        return list(inline)
    dataset = panel.get("harbor_dataset")
    if not dataset:
        raise ValueError(
            f"harbor connector: {TASKS_FILE} names neither `harbor_dataset` (a published "
            f"dataset to resolve) nor an inline `tasks` list."
        )
    version = panel.get("harbor_dataset_version")
    if not version:
        raise ValueError(
            f"harbor connector: {TASKS_FILE} declares `harbor_dataset: {dataset}` with no "
            f"`harbor_dataset_version`. Resolving 'latest' would put a moving roster behind a "
            f"fixed dataset name, so the version is required."
        )
    tasks = _registry_tasks(str(dataset), str(version))

    # `tasks_include` selects a SLICE of the published roster by task id. A dataset that names one
    # is a different dataset from one that names ten — `sample_id` is scoped by dataset name and
    # the row text is not in the key — so the selection belongs in measurement identity, which is
    # why `_identity_config` hashes the resolved pins AFTER this filter rather than before.
    include = panel.get("tasks_include")
    if not include:
        return tasks
    wanted = list(dict.fromkeys(str(i) for i in include))
    by_id = {t["id"]: t for t in tasks}
    if missing := [i for i in wanted if i not in by_id]:
        raise ValueError(
            f"harbor connector: {TASKS_FILE} includes {missing}, which {dataset}@{version} "
            f"does not publish (it has {sorted(by_id)})."
        )
    return [by_id[i] for i in wanted]


def _extract_experiment(
    experiment_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Harbor tasks → ``(queries, index_terms)``. **There is no label to match** — the cell is
    graded by the task's own verifier, so ``ground_truth`` is ``None`` and says so.

    Also PUBLISHES the panel — RESOLVED, so an episode reads the same pins the samples were built
    from rather than resolving again and possibly differently. Done here rather than in a seam of
    its own because init hands this function the parsed ``harbor_tasks.yaml``, so a second channel
    carrying the same file would be a redundant path. Never reset: the binding lives as long as
    the context it was set in, and arming a second dataset re-binds it there — see :data:`_PANEL`
    for why that is per-task and not per-process.
    """
    resolved = dict(experiment_data)
    resolved["tasks"] = _panel_tasks(experiment_data)
    _PANEL.set(resolved)
    return [{"query": t["id"], "ground_truth": None} for t in resolved["tasks"] if t.get("id")], []


def _current_task(query: str) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """The declared task for one query, plus the reward key and agent block it is graded under."""
    panel = _PANEL.get()
    if panel is None:
        raise RuntimeError(
            "harbor connector: no panel published — init reads the dataset's "
            f"{TASKS_FILE} through `extract_experiment` before anything is scored, so this "
            "ran outside an armed session."
        )
    for task in panel.get("tasks") or []:
        if (task or {}).get("id") == query:
            return (
                task,
                panel.get("reward_key") or DEFAULT_TASK_REWARD_KEY,
                panel.get("agent") or {},
            )
    raise RuntimeError(
        f"harbor connector: no task declared for {query!r} in {TASKS_FILE}. The panel that "
        "keyed this campaign is not the one being scored — reuse the name only with the same tasks."
    )


def harbor_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Outbound payload for one episode: the task id, the candidate's skill text, and whichever
    Harbor agent kwargs the node declared as tunable."""
    payload: dict[str, Any] = {"query": query}
    for node, cfg in node_config_items(pipeline_params):
        if node != AGENT_NODE:
            continue
        if prompt := cfg.get("prompt"):
            payload["prompt"] = prompt
        if model := cfg.get("model"):
            # Harbor names a model the way litellm does — the PREFIX *is* the provider
            # (`resolve_model_connection` splits on the first `/`). This repo splits the two, so
            # `model: openai/gpt-oss-120b` + `provider: openrouter` must be composed here into
            # `openrouter/openai/gpt-oss-120b`. Sending our spelling raw is not an error anyone
            # sees: Harbor reads `openai/` as the provider, asks `api.openai.com` for a model it
            # does not serve, and the cell fails for a reason that looks like the agent's.
            provider = cfg.get("provider")
            payload["model_name"] = (
                f"{provider}/{model}"
                if provider and not str(model).startswith(f"{provider}/")
                else model
            )
        if kwargs := {k: v for k, v in cfg.items() if k in AGENT_KWARG_KEYS}:
            payload["agent_kwargs"] = kwargs
    return payload


# NO credential bridge here, and that is the boundary, not an omission. TermNorm reaches its
# provider with a key configured in TermNorm's own environment; `settings.TERMNORM_TOKEN` is the
# bearer token for OUR wire to that service, never the provider key behind it. Harbor is the same
# shape: the agent spends against the provider directly, outside our LLM client and its ledger,
# so its key belongs in the environment Harbor runs in — where litellm already looks — and a
# separate one there is what makes that spend readable and revocable on its own. Forwarding
# `settings.OPENROUTER_API_KEY` into the container would quietly merge the two.


def _read_tasks(dataset_dir: Path) -> dict[str, Any]:
    from promptpotter.infrastructure.store.io import read_yaml_optional

    return read_yaml_optional(dataset_dir / TASKS_FILE) or {}


def _identity_config(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    """What the cell was measured ON, folded into measurement identity.

    A Harbor task is pinned bytes — a git commit plus a path — and the agent that drives it is
    the rest of the instrument. Repoint a task at a newer commit, or swap the agent, and the
    banked rows describe a benchmark that no longer exists; without this they would be silently
    replayed under the new declaration. Narrow on purpose: the task PINS, the agent name and the
    reward key, not the whole file, so a comment or a retimed timeout voids nothing.

    Hashes the RESOLVED pins, never the declaration. That is what lets the dataset commit only a
    name and a version (:func:`_registry_tasks`): upstream moving a commit under that version
    moves this fingerprint, so the change lands as a new measurement identity rather than as
    stale rows served against bytes nobody read.
    """
    from promptpotter.domain.pipeline_schema import stable_hash

    tasks = _read_tasks(dataset_dir)
    pins = [
        {
            "id": (t or {}).get("id"),
            "git_url": (t or {}).get("git_url"),
            "git_commit_id": (t or {}).get("git_commit_id"),
            "path": (t or {}).get("path"),
            "name": (t or {}).get("name"),
            "ref": (t or {}).get("ref"),
        }
        for t in _panel_tasks(tasks)
    ]
    fingerprint = stable_hash(
        [
            sorted(pins, key=lambda p: str(p["id"])),
            tasks.get("agent") or {},
            tasks.get("reward_key") or DEFAULT_TASK_REWARD_KEY,
        ]
    )[:12]
    return {AGENT_NODE: {INSTRUMENT_KEY: fingerprint}}


# The Harbor MINOR series `_digest` was written against. A minor, not a full version: patch
# releases do not move a package's directory layout, and pinning one would warn on every run for
# an upgrade that changed nothing we read.
EXPECTED_HARBOR_SERIES = "0.22"


async def _version_check(_http: httpx.AsyncClient, _base_url: str) -> str | None:
    """The INSTALLED Harbor's minor series. Both arguments are ignored — the hook's signature is
    written for a remote backend and this one is in-process, so the "revision" is what
    ``import harbor`` resolves to rather than what a service reports.

    Advisory by design (``_verify_connector_revision`` only warns): the pin in ``pyproject.toml``
    is what actually stops a drifting layout arriving, and this is what names it if someone
    installs past the pin anyway."""
    try:
        import harbor
    except ImportError:
        return None
    version = str(getattr(harbor, "__version__", "") or "")
    return ".".join(version.split(".")[:2]) if version else None


async def _preflight(backend_url: str) -> None:
    """Two things must be true before a campaign starts spending: Harbor imports, and a
    container runtime answers. Both fail LOUDLY here rather than as N identical errored rows —
    a missing extra and a stopped Docker daemon are the two ways this backend is 'down', and
    neither is visible from a reward of 0."""
    try:
        from harbor.trial.trial import Trial  # noqa: F401
    except ImportError as exc:
        raise BackendUnreachableError(
            "harbor",
            backend_url,
            "the 'harbor' extra is not installed — `pip install -e \".[harbor]\"`",
        ) from exc

    import asyncio

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "version",
            "--format",
            "{{.Server.Version}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (OSError, TimeoutError) as exc:
        raise BackendUnreachableError("harbor", backend_url, f"docker not callable: {exc}") from exc
    if proc.returncode != 0:
        detail = (err or b"").decode(errors="replace").strip()[:200]
        raise BackendUnreachableError(
            "harbor", backend_url, f"docker daemon not responding: {detail}"
        )
    logger.debug("harbor preflight: docker server %s", (out or b"").decode().strip())


def _write_skill(root: Path, prompt: str) -> Path:
    """The candidate's prompt as an Agent Skill. The layout is not ours to choose: Harbor uploads
    ``<skills_dir>/<name>/SKILL.md`` and the agent finds it with a depth-2 ``find``, so the extra
    directory level is load-bearing. The frontmatter must parse as YAML and carry both ``name``
    and ``description`` — an agent that fails to parse it SKIPS the skill in silence, which would
    make every candidate score as no-skill and read as 'the prompt does not matter'."""
    skill_dir = root / _SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    body = prompt.strip()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {_SKILL_NAME}\ndescription: {_SKILL_DESCRIPTION}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return root


# Every escape a terminal recording carries and a prompt must not: SGR colour, the cursor and
# mode sequences (`\x1b[?2004h`), the charset selectors, and the bare control bytes — backspace
# among them, which is what a `printf` of a `\b` literal leaves behind. Local rather than
# `presentation/views/display.py::_ANSI_RE`, which matches colour ALONE and sits in a layer this
# one may not import; a pane run through that filter is still two-thirds punctuation.
_TERMINAL_ESC = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[]()#][0-9A-Za-z]|\x1b.|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)

# The digest's budget, split by what each part answers. Sized against
# ``dispatch/bundle.py::TRANSCRIPT_REASONING_CAP`` (2200), which is where the panel trims it
# again: storing much beyond that fills archive rows with bytes no prompt will ever show.
_TERMINAL_TAIL_CAP = 1200
_VERIFIER_TAIL_CAP = 600


def _tail(text: str, cap: int) -> str:
    """The END of a captured stream, on a line boundary. The tail, never the head: a build log
    opens with package installs identical across every candidate and closes with the one thing
    that differed."""
    clean = _TERMINAL_ESC.sub("", text)
    lines = [ln.rstrip() for ln in clean.splitlines()]
    out: list[str] = []
    size = 0
    for line in reversed(lines):
        if not line:
            continue
        if size + len(line) > cap:
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(reversed(out))


def _read_tail(path: Path, cap: int) -> str:
    try:
        return _tail(path.read_text(encoding="utf-8", errors="replace"), cap)
    except OSError:
        return ""


# Names already warned about, so a ten-cell round says it once rather than ten times. Process-
# scoped on purpose: what it reports is a fact about the installed Harbor, not about a cell.
_LAYOUT_WARNED: set[str] = set()


def _warn_layout_drift(what: str) -> None:
    """The digest's artifacts are read out of Harbor's PRIVATE layout, which has no public
    accessor — so an upstream rename returns empty rather than raising, and the optimizer quietly
    goes back to grading two scalars with nothing on screen to say so. Absence is expected only
    for a trial that never started, so it is worth one line either way."""
    if what in _LAYOUT_WARNED:
        return
    _LAYOUT_WARNED.add(what)
    logger.warning(
        "harbor connector: %s — the digest loses its TERMINAL/VERIFIER tail and the optimizer "
        "sees only scalars. Expected if the trial never started; otherwise Harbor's trial layout "
        "moved and `_digest` needs updating (the extra is pinned <0.23 for this reason).",
        what,
    )


def _digest(result: TrialResult, task_id: str, reward: float | int | None) -> str:
    """What the optimizer reads about the episode — prose on ``reasoning_trace``, which reaches
    ``pipeline_data`` as an infra key with no mapping and renders through ``sample_transcripts``.

    A DIGEST, never the transcript: a 40-turn terminal log is a wall, not a prompt, and
    `<dispatch-first>` puts the shaping here rather than downstream of it. **What earns its place
    is what the next candidate could act on** — the COMMANDS the agent ran and the verifier's own
    last words. A reward and a turn count name the outcome and nothing about how it was reached,
    which is a critique node handed two scalars and asked to find a root cause; the first real
    episode failed because ``printf`` ate the ``\\b`` escapes out of a regex, and every number we
    were emitting was blind to it.
    """
    lines = [f"task={task_id} reward={reward}"]
    exc = getattr(result, "exception_info", None)
    if exc is not None:
        lines.append(
            f"failed: {getattr(exc, 'exception_type', '?')}: {getattr(exc, 'exception_message', '')}"[
                :300
            ]
        )
    timing = getattr(result, "agent_execution", None)
    if timing is not None:
        started, finished = (
            getattr(timing, "started_at", None),
            getattr(timing, "finished_at", None),
        )
        if started and finished:
            lines.append(f"agent ran {(finished - started).total_seconds():.0f}s")
    ctx = getattr(result, "agent_result", None)
    meta = getattr(ctx, "metadata", None) if ctx is not None else None
    if isinstance(meta, dict):
        # `n_episodes` is terminus-2's own spelling of the turn count — read it, rather than the
        # three plausible names it does not use, or a capped-out episode reports no cap.
        for key in ("n_episodes", "finish_reason", "termination_reason", "summarization_count"):
            if (val := meta.get(key)) is not None:
                lines.append(f"{key}={val}")

    # The artifacts Harbor wrote for this trial. `trials_dir` is ours and it lays them out as
    # `<trial_name>/<role>/…`, so the directory is addressable without parsing `trial_uri`.
    root = _TRIALS_ROOT / str(getattr(result, "trial_name", "") or "")
    panes = sorted(root.glob("agent/*.pane")) if root.is_dir() else []
    if panes and (pane := _read_tail(panes[0], _TERMINAL_TAIL_CAP)):
        lines.append(f"\nTERMINAL (tail):\n{pane}")
    else:
        _warn_layout_drift(f"no agent pane under {root}")
    if verdict := _read_tail(root / "verifier" / "test-stdout.txt", _VERIFIER_TAIL_CAP):
        lines.append(f"\nVERIFIER (tail):\n{verdict}")
    else:
        _warn_layout_drift(f"no verifier stdout under {root}")
    return "\n".join(lines)


def _step_tokens(result: TrialResult, model_name: str | None) -> dict[str, dict[str, Any]]:
    """The agent's spend on the SAME channel a remote backend's rides. Harbor totals it for us
    (``TrialResult.compute_token_cost_totals``), so this is a projection rather than a count.
    ``n_input_tokens`` is total input INCLUDING cache on their side, which is the convention
    ``step_tokens`` already uses."""
    n_input, n_cache, n_output, cost = result.compute_token_cost_totals()
    if n_input is None and n_output is None and cost is None:
        return {}
    entry: dict[str, Any] = {
        "input": int(n_input or 0),
        "output": int(n_output or 0),
        "estimated": False,
    }
    if n_cache is not None:
        entry["cached"] = int(n_cache)
    if cost is not None:
        # Harbor prices the call through litellm. Ours is the authority for models it knows, but
        # an agent CLI's spend never passes through our client at all, so without this the
        # campaign ceiling would bound the optimizer half of a run and nothing else.
        entry["cost_usd"] = float(cost)
    if model_name:
        entry["model"] = model_name
    return {AGENT_NODE: entry}


def _task_config(task: dict[str, Any], harbor_config: ModuleType) -> Any:
    """One declared task → Harbor's ``TaskConfig``. Both of its source shapes are accepted: a
    registry package (``name``/``ref``) and a pinned git checkout (``git_url``/``path``/
    ``git_commit_id``). The pins are what make a re-measure the same measurement."""
    if name := task.get("name"):
        return harbor_config.TaskConfig(name=name, ref=task.get("ref"))
    path = task.get("path")
    if not path:
        raise ValueError(
            f"harbor task {task.get('id')!r} declares neither 'name' (a registry package) "
            f"nor 'path' (a task directory) in {TASKS_FILE}."
        )
    return harbor_config.TaskConfig(
        path=Path(path),
        git_url=task.get("git_url"),
        git_commit_id=task.get("git_commit_id"),
    )


async def _in_process_run(query: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run one episode and project its verdict onto the ``{"data": {…}}`` shape ``measure_sample``
    parses from an HTTP body — so the scorer reads a Harbor result identically to a remote one."""
    from harbor.models.trial import config as harbor_config
    from harbor.trial.trial import Trial

    task, reward_key, agent_cfg = _current_task(query)

    agent_kwargs = dict(agent_cfg.get("kwargs") or {})
    agent_kwargs.update(payload.get("agent_kwargs") or {})
    # The MODEL comes from the node config, never from the agent block — `datasets/CLAUDE.md`
    # makes `nodes.{node}.config.model` the dataset's one statement of what it measures on, and a
    # second spelling in `harbor_tasks.yaml` would be a second owner of the same fact.
    model_name = payload.get("model_name")

    _TRIALS_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pp-skill-") as skill_root:
        skills: list[str] = []
        if prompt := payload.get("prompt"):
            skills.append(str(_write_skill(Path(skill_root), prompt)))

        trial = await Trial.create(
            harbor_config.TrialConfig(
                task=_task_config(task, harbor_config),
                trials_dir=_TRIALS_ROOT,
                agent=harbor_config.AgentConfig(
                    name=agent_cfg.get("name") or "terminus-2",
                    model_name=model_name,
                    skills=skills,
                    kwargs=agent_kwargs,
                ),
                environment=harbor_config.EnvironmentConfig(
                    type=agent_cfg.get("environment") or "docker"
                ),
            )
        )
        start = time.monotonic()
        result = await trial.run()
        elapsed = time.monotonic() - start

    rewards = result.verifier_result.rewards if result.verifier_result else None
    reward = (rewards or {}).get(reward_key)
    if reward is None:
        # No number came back, so there is nothing to grade — and a 0.0 here would be a LIE
        # about the candidate, indistinguishable from an episode that ran and failed. The
        # campaign excludes the cell instead.
        # NOTE: this error's name and home are now wrong — it has a second, non-L4 consumer.
        # Generalizing it to `CellUnscoreableError` outside `domain/l4/` is a mechanical rename
        # across 23 sites and is deliberately NOT bundled into this connector's first commit.
        from promptpotter.domain.l4.proxies import InnerCycleUnscoreableError

        raise InnerCycleUnscoreableError(
            f"harbor task {query!r} produced no reward under key {reward_key!r} "
            f"(rewards={rewards}); the episode is unscoreable, not a zero."
        )

    return {
        "data": {
            REWARD_KEY: float(reward),
            "terminal_node": AGENT_NODE,
            "total_time": elapsed,
            "step_timings": {AGENT_NODE: elapsed},
            "step_tokens": _step_tokens(result, model_name),
            "reasoning_trace": _digest(result, query, reward),
        }
    }


CONNECTOR = Connector(
    name="harbor",
    execution="in_process",
    wire_adapter=harbor_wire_adapter,
    session_factory=HarborSession,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    preflight=_preflight,
    # The connector most exposed to upstream drift, because `_digest` reads Harbor's private trial
    # layout. Opt-in everywhere else; declared here for that reason.
    expected_revision=EXPECTED_HARBOR_SERIES,
    version_check=_version_check,
    identity_config=_identity_config,
    # An episode is a whole agent run — minutes, with its own container build and its own spend —
    # so it is a cell, and a round of them is long enough that the group is the only unit that
    # can bound a press.
    measured_unit="cell",
    concurrency_arming="batch",
    # Each cell holds a container. Two is the shipped default elsewhere and is the right floor
    # here too: the ceiling is the operator's machine, not the provider.
    max_cells_in_flight=2,
    # The one key `_in_process_run` always emits that the campaign formula reads. Verified against
    # the dataset's declared observation_mappings at init.
    required_observation_keys=(REWARD_KEY,),
    # The "samples" ARE the tasks declared there — read from the dataset config dir at init.
    experiment_file=TASKS_FILE,
    default_pipeline=(AGENT_NODE,),
    # No `node_types`: that roster exists to raise INPUT DEPENDENCIES (a `candidate_source` node
    # wants a candidate library dropped in place). An agent node wants nothing but its task.
)


__all__ = [
    "AGENT_NODE",
    "CONNECTOR",
    "REWARD_KEY",
    "TASKS_FILE",
    "HarborSession",
    "harbor_wire_adapter",
]
