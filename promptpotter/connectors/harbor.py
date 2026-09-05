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

import codecs
import json
import locale
import logging
import re
import sys
import tempfile
import time
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.connectors.protocol import BackendUnreachableError, Connector
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.spend import StepTokenUsage

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

# Where the episode's answer text arrives. A verifier grades the cell, but the episode still
# ANSWERED something; `Connector.answer_key` is what makes core read this as `predicted` rather
# than asking a ranking that does not exist.
ANSWER_KEY = "agent_answer"
ANSWER_FILENAME = "answer.txt"

# NO `final_ranking`, and its absence is the declaration: the `agent` node declares no
# `node_role`, so nothing would read one. Do not restore it, and do not reach the same place by
# declaring the agent a RANKER — that switches on `candidate_recall`, which walks a ranking for a
# ground truth this backend does not have and banks the 0.0.

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
        # terminus-2's switch for the whole chat history on `agent_result.metadata`. Tunable
        # rather than pinned because it is a SIZE decision the dataset owns.
        "store_all_messages",
    }
)

# Trial scratch, NOT under the workspace: Harbor nests `<trials_dir>/<trial>/<role>/…` and a
# workspace path is already deep — the MAX_PATH wall that forced L4's `.inner` registry flat.
# Nothing durable lives here; reward, digest and token counts land in the measurement archive.
_TRIALS_ROOT = Path(tempfile.gettempdir()) / "promptpotter-harbor"

# FIXED, never a search axis. The agent sees only name + description eagerly and must open the
# file to read the body, so a candidate free to write its own could win by making itself
# uninviting — the skill goes unread, the arm scores as no-skill, and hiding reads as discovery.
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


# The declared panel. A ContextVar because ``in_process_run`` is a module-level hook called with
# ``(query, payload)`` and no call-site state. NOT a module global: campaigns run as sibling
# `asyncio.create_task`s, each copying the context at spawn, so two concurrent Harbor campaigns
# cannot clobber each other's panel. That both in-process connectors invented this independently is
# the tell that `InProcessRun` lacks an arming argument (`docs/specs/code-debt-cleanup.md`).
_PANEL: ContextVar[dict[str, Any] | None] = ContextVar("harbor_panel", default=None)


# Resolved rosters, keyed by ``(dataset, version)``. Init asks twice — once for the samples, once
# for the fingerprint — so caching is the difference between one network fetch and one per task.
_ROSTER_CACHE: dict[tuple[str, str], list[dict[str, Any]]] = {}


def _registry_tasks(dataset: str, version: str) -> list[dict[str, Any]]:
    """The roster of a PUBLISHED Harbor dataset, resolved from Harbor's own registry.

    The task list is not ours to copy: a dataset here commits the NAME and the VERSION, and a
    second owner of upstream's list would drift the moment it repinned. Safe only because the
    resolved pins fold into the instrument fingerprint (:func:`_identity_config`), so a moved
    commit lands as a new measurement identity. The version is required for the same reason."""
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

    # A dataset naming one task is a different dataset from one naming ten, so the selection
    # belongs in measurement identity — `_identity_config` hashes the pins AFTER this filter.
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
    """Harbor tasks → ``(queries, index_terms)``, and **the one place this backend's answer shape
    is declared** (``connectors/CLAUDE.md`` § The answer shape).

    Normally there is no label — the task's own verifier grades the cell. A task MAY declare an
    ``answer``, making the bank label-carrying, which is what keeps a published auto-rater alive on
    the answer step; without it every ``needs_gold`` judge is skipped.

    Checked as a SET: ``all_verifier_graded`` is whole-bank, so a half-labelled panel has no answer
    shape and raises here. Downstream it would be silent — rank statistics and the recall
    evaluators would report the unlabelled rows as misses.

    Also PUBLISHES the panel, RESOLVED, so an episode reads the pins its samples were built from.
    Done here because init already hands this function the parsed ``harbor_tasks.yaml``. Never
    reset: the binding lives as long as its context (:data:`_PANEL`)."""
    resolved = dict(experiment_data)
    resolved["tasks"] = _panel_tasks(experiment_data)
    _PANEL.set(resolved)
    tasks = [t for t in resolved["tasks"] if t.get("id")]
    labelled = [t for t in tasks if str(t.get("answer") or "").strip()]
    if labelled and len(labelled) != len(tasks):
        unlabelled = [t["id"] for t in tasks if not str(t.get("answer") or "").strip()]
        raise ValueError(
            f"harbor connector: {TASKS_FILE} declares an `answer` for {len(labelled)} of "
            f"{len(tasks)} tasks. A bank's answer shape is whole-bank — declare one for every "
            f"task or for none. Missing: {unlabelled[:5]}"
        )
    out: list[dict[str, Any]] = []
    for t in tasks:
        row: dict[str, Any] = {
            "query": t["id"],
            "ground_truth": str(t["answer"]).strip() if labelled else None,
        }
        # `query` is the TASK ID, so a judge falling back to it would grade against an
        # identifier. A declared `question` rides `Sample.question`, the only channel a judge
        # reads (`domain/sample.py`).
        if question := str(t.get("question") or "").strip():
            row["question"] = question
        out.append(row)
    return out, []


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
            # Harbor names a model the way litellm does — the PREFIX *is* the provider — while
            # this repo splits the two, so they compose here. Sending our spelling raw is silent:
            # Harbor reads `openai/` as the provider and asks a host that does not serve it.
            provider = cfg.get("provider")
            payload["model_name"] = (
                f"{provider}/{model}"
                if provider and not str(model).startswith(f"{provider}/")
                else model
            )
        if kwargs := {k: v for k, v in cfg.items() if k in AGENT_KWARG_KEYS}:
            payload["agent_kwargs"] = kwargs
    return payload


# NO credential bridge, and that is the boundary rather than an omission. The agent spends
# against the provider directly, outside our LLM client and its ledger, so its key belongs in the
# environment Harbor runs in — where litellm already looks — and stays separately revocable.


def _read_tasks(dataset_dir: Path) -> dict[str, Any]:
    from promptpotter.infrastructure.store.io import read_yaml_optional

    return read_yaml_optional(dataset_dir / TASKS_FILE) or {}


def _identity_config(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    """What the cell was measured ON, folded into measurement identity.

    A task is pinned bytes and the agent driving it is the rest of the instrument; repoint either
    and the banked rows describe a benchmark that no longer exists. Narrow on purpose — the pins,
    the agent name and the reward key, so a comment or a retimed timeout voids nothing.

    Hashes the RESOLVED pins, never the declaration, which is what lets a dataset commit only a
    name and a version (:func:`_registry_tasks`)."""
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
            # Question and answer are INSTRUMENT, not pinned bytes: they live in our file, and
            # without them here a corrected gold would replay every verdict taken under the old.
            "question": (t or {}).get("question"),
            "answer": (t or {}).get("answer"),
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
    """Three things must be true before a campaign starts spending: Harbor imports, this
    interpreter decodes UTF-8 by default, and a container runtime answers. All three fail LOUDLY
    here rather than as N identical errored rows — a missing extra, a locale-encoded interpreter
    and a stopped Docker daemon are the ways this backend is 'down', and none is visible from a
    reward of 0."""
    try:
        from harbor.trial.trial import Trial  # noqa: F401
    except ImportError as exc:
        # Name the interpreter, because the likeliest cause is that this is the WRONG one. A bare
        # `python` on Windows resolves to the system install, which imports promptpotter fine and
        # none of its extras -- and "pip install the extra" is then a cure that pollutes that
        # interpreter instead of using the venv that already has it.
        raise BackendUnreachableError(
            "harbor",
            backend_url,
            f"the 'harbor' extra is not importable from {sys.executable}.\n"
            f"  If that is not this repo's .venv, re-run with the venv's interpreter:\n"
            f"    .venv\\Scripts\\python.exe -m promptpotter ...\n"
            f'  If it IS the venv, install the extra: pip install -e ".[harbor]"',
        ) from exc

    # Harbor reads `task.toml`, `instruction.md` and the ATIF trajectory with a bare `read_text()`,
    # so the decode falls to the locale encoding and any task carrying a byte outside it raises
    # inside `Task.__init__`. Upstream's to fix; ours is to refuse rather than discover it per cell.
    if "utf-8" not in codecs.lookup(locale.getpreferredencoding(False)).name:
        raise BackendUnreachableError(
            "harbor",
            backend_url,
            f"this interpreter decodes files as {locale.getpreferredencoding(False)!r}, not UTF-8, "
            # ASCII only in this string, deliberately -- an em dash or an ellipsis included. It is
            # printed to the very console whose encoding it is complaining about, so a non-ASCII
            # character here renders as a replacement char, in the one message that cannot afford
            # to look broken.
            "and Harbor reads its task files without naming an encoding. Every task whose "
            "instruction is not pure Latin-1 would raise before its container is built. Launch "
            "with UTF-8 mode on:\n"
            "  PowerShell:  $env:PYTHONUTF8 = '1'\n"
            "  bash:        export PYTHONUTF8=1\n"
            "  or per-run:  python -X utf8 -m promptpotter ...",
        )

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


# Every escape a terminal recording carries and a prompt must not: SGR colour, cursor and mode
# sequences, charset selectors, bare control bytes. Local rather than `views/display.py::_ANSI_RE`,
# which matches colour alone and sits in a layer this one may not import.
_TERMINAL_ESC = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[]()#][0-9A-Za-z]|\x1b.|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)

# The digest's budget, split by what each part answers, and ORDERED by what must survive: the
# panel trims with a head+tail (``dispatch/bundle.py::TRANSCRIPT_REASONING_CAP``, 2200), so the
# agent's decisions lead and the verifier closes, leaving the terminal — the part a reader can
# most often infer from the other two — as what a long episode loses first. Storing much beyond
# that cap fills archive rows with bytes no prompt will ever show.
_AGENT_DECISION_CAP = 1200
_TERMINAL_TAIL_CAP = 600
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


# Per-turn budgets: a conversation is stored once and read many times. The message keeps its HEAD
# (a turn opens by saying what it will do), the observation its TAIL (output ends with what
# mattered) — the same split `_tail` argues for the pane.
_TURN_MESSAGE_CAP = 1200
_TURN_OBSERVATION_CAP = 800


def _atif_text(value: object) -> str:
    """One ATIF message / observation body as text — a plain string, or the TEXT parts of a
    multimodal ``ContentPart`` array (ATIF-v1.6). An image part contributes a path, not bytes."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for part in value:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            if text := part.get("text"):
                parts.append(str(text))
            elif isinstance(src := part.get("source"), dict) and (path := src.get("path")):
                parts.append(f"[image {path}]")
    return "\n".join(parts)


def _turn(raw: dict[str, Any], index: int, step: str | None) -> dict[str, Any]:
    """One ATIF ``Step`` → one ``domain/scoring.py::TurnRecord``.

    ``index`` is the CELL's running turn ordinal, not the one in the file: on a multi-step trial
    each step writes its own trajectory numbered from 1, and re-using those would give a two-step
    cell two turn 1s. The record is deliberately narrower than the source — no token ids, no
    logprobs, no per-turn metrics — because none of it is read by a prompt, a ruler or a formula.
    """
    out: dict[str, Any] = {"index": index, "source": str(raw.get("source") or "")}
    if step is not None:
        out["step"] = step
    if message := _atif_text(raw.get("message"))[:_TURN_MESSAGE_CAP].strip():
        out["message"] = message
    if reasoning := str(raw.get("reasoning_content") or "")[:_TURN_MESSAGE_CAP].strip():
        out["reasoning"] = reasoning
    if tools := [
        name
        for call in raw.get("tool_calls") or []
        if isinstance(call, dict) and (name := call.get("function_name"))
    ]:
        out["tools"] = [str(t) for t in tools]
    results = (raw.get("observation") or {}).get("results") or []
    observed = "\n".join(
        text for r in results if isinstance(r, dict) and (text := _atif_text(r.get("content")))
    )
    if observed := _tail(observed, _TURN_OBSERVATION_CAP):
        out["observation"] = observed
    return out


def _read_trajectory(path: Path) -> list[dict[str, Any]]:
    """The raw ATIF turns in one trajectory file, or nothing.

    Parsed as plain JSON rather than through Harbor's own ``Trajectory`` model on purpose: this
    reads their PRIVATE trial layout, exactly as ``_digest`` does, so a field they add or move must
    degrade the record rather than raise inside a cell the backend already paid for. ``extra`` is
    forbidden on their model, so validating here would turn an upstream addition into a dead run.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    steps = payload.get("steps") if isinstance(payload, dict) else None
    return [s for s in steps or [] if isinstance(s, dict)]


def _turns(result: TrialResult) -> list[dict[str, Any]]:
    """The cell's conversation, in order, each turn stamped with the STEP it served.

    Two layouts: ``<trial>/agent/trajectory.json`` single-step, ``<trial>/steps/<name>/agent/`` per
    step. Walking ``step_results`` rather than globbing is what makes the STEP NAME available — the
    axis per-step terms pool on, and why a turn ordinal never becomes one
    (``domain/scoring.py::TurnRecord``)."""
    root = _TRIALS_ROOT / str(getattr(result, "trial_name", "") or "")
    steps = getattr(result, "step_results", None) or []
    sources: list[tuple[Path, str | None]] = (
        [
            (root / "steps" / str(sr.step_name) / "agent" / "trajectory.json", str(sr.step_name))
            for sr in steps
        ]
        if steps
        else [(root / "agent" / "trajectory.json", None)]
    )
    turns: list[dict[str, Any]] = []
    for path, step in sources:
        for raw in _read_trajectory(path):
            turns.append(_turn(raw, len(turns) + 1, step))
    if not turns:
        _warn_layout_drift(f"no agent trajectory under {root}")
    return turns


# An answer is read, graded and displayed, never scanned — so the HEAD, and generous enough for a
# long-form answer without letting a task that dumps a log into the file become the `predicted`
# column on every surface.
_ANSWER_CAP = 4000


def _answer(result: TrialResult) -> str:
    """The episode's answer text, from the artifact the task declared, or ``""``.

    Collection MIRRORS the absolute container path under the trial's ``artifacts/``, so the file
    lands at ``artifacts/logs/artifacts/answer.txt``. ``TrialPaths.host_artifact_path`` is asked
    rather than that rule re-derived — the one read in this module going through a public accessor
    instead of Harbor's private trial dir, because the placement is upstream's to change and a
    wrong guess here returns ``""`` for an answer that is on disk.

    Archived per step on a multi-step trial, and the LAST step to write one wins: on a
    ``retrieve → answer`` task both may leave a file, and the answer step's is the answer. Absent
    is ``""``, which core turns into the ``NO_RESULT`` sentinel — the honest reading for a task
    that declared no answer artifact at all."""
    from harbor.models.task.config import MAIN_SERVICE_NAME
    from harbor.models.trial.paths import EnvironmentPaths, TrialPaths

    source = str(EnvironmentPaths.artifacts_dir / ANSWER_FILENAME)
    root = _TRIALS_ROOT / str(getattr(result, "trial_name", "") or "")
    roots = [root] + [
        root / "steps" / str(sr.step_name) for sr in getattr(result, "step_results", None) or []
    ]
    answer = ""
    for base in roots:
        try:
            text = (
                TrialPaths(base)
                .host_artifact_path(MAIN_SERVICE_NAME, source)
                .read_text(encoding="utf-8", errors="replace")
                .strip()
            )
        except OSError:
            continue
        if text:
            answer = text
    return answer[:_ANSWER_CAP]


def _step_rewards(result: TrialResult) -> dict[str, float]:
    """Each step's OWN verifier rewards, keyed ``{step}_{reward}`` — per-step terms a formula reads
    beside the cell's aggregate.

    Beside, never instead of: ``TrialResult.verifier_result`` is already Harbor's fold of these into
    the cell's score. Taking them as independent observations would claim kN readings where there
    are N and let PoBB eliminate on confidence it never earned. A genuine per-step ability
    parameter is a different model, not a different key.

    Only identifier-safe names are emitted, because a formula can name nothing else."""
    out: dict[str, float] = {}
    for sr in getattr(result, "step_results", None) or []:
        name = str(getattr(sr, "step_name", "") or "")
        rewards = getattr(getattr(sr, "verifier_result", None), "rewards", None) or {}
        for key, value in rewards.items():
            if (term := f"{name}_{key}").isidentifier():
                out[term] = float(value)
    return out


def _unscoreable_step(result: TrialResult) -> str | None:
    """Why this trial's reward cannot be believed, or ``None``.

    ``_aggregate_step_rewards`` excludes steps with no verifier result from the denominator, so a
    cell whose first step scored 1.0 and whose second CRASHED reports 1.0 while an honest wrong
    answer reports 0.5 — the crash is rewarded, silently.

    A ``min_reward`` abort is NOT caught here: every step it appended carries a verifier result, so
    the mean is over real readings and the operator declared that gate."""
    for sr in getattr(result, "step_results", None) or []:
        if getattr(sr, "verifier_result", None) is None:
            exc = getattr(sr, "exception_info", None)
            detail = f": {getattr(exc, 'exception_type', '?')}" if exc is not None else ""
            return (
                f"step {getattr(sr, 'step_name', '?')!r} produced no verifier result{detail} — "
                f"Harbor drops it from the reward denominator, so the trial reward would describe "
                f"only the steps that survived"
            )
    return None


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


def _agent_decisions(turns: list[dict[str, Any]]) -> str:
    """What the agent SAID it was doing, turn by turn. ``source == "user"`` is dropped: those turns
    are the task we handed it — on a panel that inlines its evidence they are tens of thousands of
    characters of documents, quoted back at the optimizer as if the agent had produced them."""
    said = [
        f"turn {t.get('index')}: {msg}"
        for t in turns
        if str(t.get("source") or "") != "user"
        and (msg := str(t.get("reasoning") or t.get("message") or "").strip())
    ]
    return "\n".join(said)[:_AGENT_DECISION_CAP]


def _digest(
    result: TrialResult, task_id: str, reward: float | int | None, turns: list[dict[str, Any]]
) -> str:
    """What the optimizer reads about the episode — prose on ``reasoning_trace``, which reaches
    ``pipeline_data`` as an infra key with no mapping and renders through ``sample_transcripts``,
    under the header ``MODEL REASONING``.

    A DIGEST, never the transcript: a 40-turn terminal log is a wall, not a prompt. What earns its
    place is what the agent decided, then the environment's answer to it — the ordering rule and
    why the pane is not the whole record are `CLAUDE.md` § A multi-turn cell."""
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

    # First, and off the turns the caller already read: no second walk of the trial directory for
    # a record `pipeline_data.turns` is about to carry anyway.
    if said := _agent_decisions(turns):
        lines.append(f"\nAGENT DECISIONS:\n{said}")

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


def _step_tokens(result: TrialResult, model_name: str | None) -> dict[str, StepTokenUsage]:
    """The agent's spend on the SAME channel a remote backend's rides. Harbor totals it for us
    (``TrialResult.compute_token_cost_totals``), so this is a projection rather than a count.
    ``n_input_tokens`` is total input INCLUDING cache on their side, which is the convention
    ``step_tokens`` already uses.

    TYPED as :class:`StepTokenUsage` rather than a bare dict, so a count filed under a key nothing
    reads is a type error here rather than a silent drop downstream."""
    n_input, n_cache, n_output, cost = result.compute_token_cost_totals()
    if n_input is None and n_output is None and cost is None:
        return {}
    entry: StepTokenUsage = {
        "input": int(n_input or 0),
        "output": int(n_output or 0),
        "estimated": False,
    }
    if n_cache is not None:
        # `cache_read`, NOT `cached`: everywhere else in this package `cached` is the boolean
        # "replayed from our archive, so it cost nothing", and `record_cost_usd` prices a truthy
        # one at 0.0 — a paid call filed under that name goes missing from the bill.
        entry["cache_read"] = int(n_cache)
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

    # BEFORE the reward is read: Harbor drops a step with no verifier result from its own
    # denominator, so the number below would describe fewer steps than the task declared, and
    # describe it as a success.
    if unscoreable := _unscoreable_step(result):
        from promptpotter.domain.l4.proxies import InnerCycleUnscoreableError

        raise InnerCycleUnscoreableError(f"harbor task {query!r}: {unscoreable}.")

    rewards = result.verifier_result.rewards if result.verifier_result else None
    reward = (rewards or {}).get(reward_key)
    if reward is None:
        # Nothing to grade, and a 0.0 here would be indistinguishable from an episode that ran
        # and failed. The campaign excludes the cell instead.
        # NOTE: this error's name and home are wrong now that it has a non-L4 consumer;
        # generalizing it outside `domain/l4/` is a rename across 23 sites.
        from promptpotter.domain.l4.proxies import InnerCycleUnscoreableError

        raise InnerCycleUnscoreableError(
            f"harbor task {query!r} produced no reward under key {reward_key!r} "
            f"(rewards={rewards}); the episode is unscoreable, not a zero."
        )

    turns = _turns(result)
    data: dict[str, Any] = {
        REWARD_KEY: float(reward),
        "terminal_node": AGENT_NODE,
        "total_time": elapsed,
        "step_timings": {AGENT_NODE: elapsed},
        "step_tokens": _step_tokens(result, model_name),
        "reasoning_trace": _digest(result, query, reward, turns),
        ANSWER_KEY: _answer(result),
    }
    # Absent, never empty: `[]` would claim this episode had no turns and `{}` that its steps
    # scored nothing. A single-step task has neither concept.
    if turns:
        data["turns"] = turns
    data.update(_step_rewards(result))
    return {"data": data}


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
    # so it is a cell.
    measured_unit="cell",
    # Each cell holds a container. Two is the shipped default elsewhere and is the right floor
    # here too: the ceiling is the operator's machine, not the provider.
    max_cells_in_flight=2,
    # The one key always emitted that a formula reads, verified against the dataset's declared
    # mappings at init. Per-step rewards are NOT here — a single-step task emits none, so
    # declaring them would fail init for every task that is not multi-step.
    required_observation_keys=(REWARD_KEY,),
    # An episode answers even though a verifier grades it, and until this existed nothing carried
    # the answer: no ranking means `predicted` was the `NO_RESULT` sentinel on every cell here.
    answer_key=ANSWER_KEY,
    # The "samples" ARE the tasks declared there — read from the dataset config dir at init.
    experiment_file=TASKS_FILE,
    default_pipeline=(AGENT_NODE,),
    # No `node_types`: that roster exists to raise INPUT DEPENDENCIES (a `candidate_source` node
    # wants a candidate library dropped in place). An agent node wants nothing but its task.
)


__all__ = [
    "AGENT_NODE",
    "ANSWER_FILENAME",
    "ANSWER_KEY",
    "CONNECTOR",
    "REWARD_KEY",
    "TASKS_FILE",
    "HarborSession",
    "harbor_wire_adapter",
]
