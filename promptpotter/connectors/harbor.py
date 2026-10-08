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

import asyncio
import functools
import json
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast, get_args

import httpx

from promptpotter.config.settings import non_utf8_encoding
from promptpotter.connectors.protocol import Connector, InProcessWorkload, NoopSession
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import LLMSpendBound
from promptpotter.domain.spend import StepTokenUsage, TokenAccount
from promptpotter.infrastructure.docker_host import (
    CONTAINER_PRODUCER,
    PACKAGE_CACHE_PROXY,
    PRODUCER_SCRATCH,
    docker,
    docker_daemon_fault,
    ensure_package_cache,
    forget_package_cache,
    machine_step,
)
from promptpotter.infrastructure.llm.litellm_sends import litellm_route, litellm_sends_billed_as
from promptpotter.infrastructure.llm.openai_compat import cell_gateway_body, sent_effort
from promptpotter.infrastructure.llm.registry import openai_compat_spec
from promptpotter.infrastructure.llm.spend_book import (
    Billed,
    CallLabel,
    admitted,
    reservation_left,
)
from promptpotter.infrastructure.llm.telemetry import emit_backend_warning
from promptpotter.shared.errors import (
    CellInfrastructureError,
    CellThrottledError,
    CellUnscoreableError,
    ErrorCategory,
    cell_failure,
    is_provider_credit_refusal,
)
from promptpotter.shared.hashing import stable_hash

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.trial.result import TrialResult
    from harbor.trial.trial import Trial

    from promptpotter.domain.sample import Sample
    from promptpotter.domain.value_tree import Delivery
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


# The one node a harbor dataset declares. ONE, because an episode is a single call from here —
# the agent's turns are its own loop, not a chain we route a query through node by node.
AGENT_NODE = "agent"
# Whose bill every model send of an episode is, however many turns it takes.
_AGENT_SEND = CallLabel(AGENT_NODE, "backend")

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

# Whether the episode opened the artifact the candidate prompt WAS (see `_skill_opened`). A
# measured observation like the reward beside it, not a diagnostic: on this backend the prompt
# reaches the model only if the model opens the file, so this is the term that separates "the skill
# was wrong" from "the skill was never read" — and an arm scoring as no-skill is a round whose arms
# were all the same episode.
SKILL_KEY = "skill_opened"

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
        "top_p",
        "parser_name",
        "enable_summarize",
        "interleaved_thinking",
        "max_thinking_tokens",
        # terminus-2's switch for the whole chat history on `agent_result.metadata`. Tunable
        # rather than pinned because it is a SIZE decision the dataset owns.
        "store_all_messages",
    }
)

# Trial scratch is this process's on the Docker host, NOT under the workspace (`connectors/CLAUDE.md`
# § Execution mode): only a HARD KILL leaves one behind, for `docker_host.reap_dead_producers`.
_TRIALS_ROOT = PRODUCER_SCRATCH
# Read by the compose overlay, which interpolates it into the producer label.
_PRODUCER_ENV = "PROMPTPOTTER_HARBOR_PRODUCER"

# Keeps a task's image across cells and stops the container without a grace period. It reads
# Harbor's compose infra variables, which task-authored compose files reference too.
_DOCKER_OVERLAY = Path(__file__).parent / "resources" / "harbor-docker-compose.yaml"

# Where a dataset may route downloads through the machine's package cache. Only the verifier: a
# proxy in the agent's environment is something the agent can observe, which changes the cell.
PACKAGE_CACHE_SCOPES = frozenset({"verifier"})

# What an agent's setup installs into a container that lacks it, as `(tool, probe)`. Harbor's
# installed agents declare `SYSTEM_PACKAGES`; terminus-2 declares nothing, so its pair is copied
# from `TmuxSession._install_recording_tools`, which probes and installs exactly these.
_AGENT_TOOLS: dict[str, tuple[tuple[str, str], ...]] = {
    "terminus-2": (("tmux", "tmux -V"), ("asciinema", "asciinema --version")),
}
# `TmuxSession._detect_system_info`'s probe order, which decides the install command it runs.
_PACKAGE_MANAGERS = ("apt-get", "dnf", "yum", "apk", "pacman", "brew", "pkg", "zypper")

# Attempts at a cell whose trial measured the infrastructure, and the wait before the next one.
_INFRA_ATTEMPTS = 3
_INFRA_BACKOFF_S = 45.0

# The calls one terminus-2 turn bills when nothing goes wrong: the turn itself, plus a summarize
# pass of three calls (`_summarize`) unless `enable_summarize` is off. Its RETRIES are deliberately
# not counted here — see `_sent_spend_bound`.
_TERMINUS_SUMMARY_CALLS = 3
# The same for Harbor's registry fetch, which blocks whichever caller resolves a panel first.
_REGISTRY_ATTEMPTS = 3
_REGISTRY_BACKOFF_S = 2.0
_REGISTRY_TIMEOUT_S = 30.0

# A failed download as the tools a container fetches with print it: Docker's registry client and
# litellm (in a Harbor exception), apt, pip and npm (in a verifier's output).
_HARNESS_NETWORK_FAILURE = re.compile(
    r"no such host|Temporary failure in name resolution|failed to fetch oauth token|"
    r"TLS handshake timeout|i/o timeout|dial tcp|Network is unreachable|APIConnectionError"
)
_FETCH_FAILURE = re.compile(
    r"^Err:\d+ https?://|Failed to fetch https?://|Temporary failure resolving|"
    r"Could not fetch URL https?://|Failed to establish a new connection|"
    r"npm (?:ERR!|error) code (?:EAI_AGAIN|ENOTFOUND|ETIMEDOUT|ECONNRESET)",
    re.MULTILINE,
)
# Harbor's names for a harness phase that ran out of clock. Environment start, agent setup and a
# verifier that installs its tools are all download-bound, so none of them is the agent's score.
_HARNESS_TIMEOUTS = frozenset(
    {"EnvironmentStartTimeoutError", "AgentSetupTimeoutError", "VerifierTimeoutError"}
)
# A model provider's throttle as litellm names it. terminus-2 retries it inside the turn, so it
# spends the agent's clock (logged to `trial.log`) and ends the episode once the retries run out —
# a cell measuring the provider, which `CellThrottledError` hands to the run's backpressure.
_PROVIDER_THROTTLE = re.compile(r"\bRateLimitError\b")

# FIXED, never a search axis. The agent sees only name + description eagerly and must open the
# file to read the body, so a candidate free to write its own could win by making itself
# uninviting — the skill goes unread, the arm scores as no-skill, and hiding reads as discovery.
_SKILL_NAME = "task-approach"
# The Agent Skills spec's filename, and the needle `_skill_opened` looks for. One owner: it is both
# what we WRITE and what we detect a read of, and two spellings could disagree silently.
SKILL_FILENAME = "SKILL.md"
_SKILL_DESCRIPTION = (
    "Read this before acting. Required approach, conventions and completion criteria for "
    "this task. Always consult it first."
)

# Which channel carries the candidate prompt: an instrument the campaign fixes in
# `nodes.agent.config`, never a search axis. Absent is `agent_skill`; spelled out, it re-keys.
SKILL_DELIVERY_KEY = "skill_delivery"
SkillDelivery = Literal["agent_skill", "system_prompt"]


def _skill_delivery(cfg: Mapping[str, Any]) -> SkillDelivery:
    value = cfg.get(SKILL_DELIVERY_KEY, "agent_skill")
    if value not in get_args(SkillDelivery):
        raise ValueError(
            f"harbor connector: `{SKILL_DELIVERY_KEY}: {value}` names no channel; the channels "
            f"are {list(get_args(SkillDelivery))}."
        )
    return cast(SkillDelivery, value)


def _prompt_delivery(pipeline_params: dict[str, Any] | None) -> Delivery:
    cfg = dict(node_config_items(pipeline_params)).get(AGENT_NODE, {})
    return "request" if _skill_delivery(cfg) == "system_prompt" else "artifact_body"


def _system_skill_template(template: str, prompt: str) -> str:
    """The skill at the head of terminus-2's prompt template — the text it sends as the first
    message and keeps through summarization, since it sends no system message of its own."""
    block = f'<skill name="{_SKILL_NAME}">\n{prompt.strip()}\n</skill>\n\n'
    # terminus-2 fills the template with `str.format`, so the skill's own braces are doubled.
    return block.replace("{", "{{").replace("}", "}}") + template


@functools.cache
def _registry_tasks(dataset: str, version: str) -> list[dict[str, Any]]:
    """The roster of a PUBLISHED Harbor dataset, resolved from Harbor's own registry.

    The task list is not ours to copy: a dataset here commits the NAME and the VERSION, and a
    second owner of upstream's list would drift the moment it repinned. Safe only because each
    resolved pin folds into its own sample's content address (:func:`_task_pin`), so a moved commit
    lands as a new measurement identity. The version is required for the same reason.

    Never served from a copy when the fetch fails: a cached roster would be that second owner."""
    from harbor.constants import DEFAULT_REGISTRY_URL
    from harbor.models.registry import DatasetSpec

    # Fetched here rather than through `JsonRegistryClient`, whose `requests.get` takes no timeout.
    for n in range(1, _REGISTRY_ATTEMPTS + 1):
        try:
            response = httpx.get(
                DEFAULT_REGISTRY_URL, timeout=_REGISTRY_TIMEOUT_S, follow_redirects=True
            )
            response.raise_for_status()
            break
        except httpx.HTTPError as exc:
            if n == _REGISTRY_ATTEMPTS:
                raise BackendUnreachableError(
                    "harbor", DEFAULT_REGISTRY_URL, f"registry fetch failed {n} times: {exc}"
                ) from exc
            logger.warning("harbor registry fetch %d/%d failed: %s", n, _REGISTRY_ATTEMPTS, exc)
            time.sleep(_REGISTRY_BACKOFF_S * n)

    # Memoized: a run carries its own resolved document, and each sample's key hashes the pins it used.
    specs = {
        spec.version: spec
        for spec in map(DatasetSpec.model_validate, response.json())
        if spec.name == dataset
    }
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
    return [
        {
            "id": t.name,
            "git_url": t.git_url,
            "git_commit_id": t.git_commit_id,
            "path": str(t.path),
        }
        for t in spec.tasks
    ]


def _resolve_experiment(panel: Mapping[str, Any]) -> dict[str, Any]:
    """The panel with its ``tasks`` pinned, from whichever of Harbor's two task sources it names.

    Mirrors ``TaskConfig``'s own split rather than inventing one: a published dataset resolved by
    ``harbor_dataset`` + ``harbor_dataset_version``, or tasks declared inline for a locally
    authored panel. A committed dataset uses the first — see :func:`_registry_tasks`.
    """
    if (scope := panel.get("package_cache")) is not None and scope not in PACKAGE_CACHE_SCOPES:
        raise ValueError(
            f"harbor connector: {TASKS_FILE} declares `package_cache: {scope}`; the scopes are "
            f"{sorted(PACKAGE_CACHE_SCOPES)}."
        )
    if inline := panel.get("tasks"):
        return {**panel, "tasks": list(inline)}
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

    include = panel.get("tasks_include")
    if not include:
        return {**panel, "tasks": tasks}
    wanted = list(dict.fromkeys(str(i) for i in include))
    by_id = {t["id"]: t for t in tasks}
    if missing := [i for i in wanted if i not in by_id]:
        raise ValueError(
            f"harbor connector: {TASKS_FILE} includes {missing}, which {dataset}@{version} "
            f"does not publish (it has {sorted(by_id)})."
        )
    return {**panel, "tasks": [by_id[i] for i in wanted]}


def _extract_experiment(experiment_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Harbor tasks → rows, and **the one place this backend's answer shape is declared**
    (``connectors/CLAUDE.md`` § The answer shape).

    Normally there is no label — the task's own verifier grades the cell. A task MAY declare an
    ``answer``, making the bank label-carrying, which is what keeps a published auto-rater alive on
    the answer step; without it every ``needs_gold`` judge is skipped.

    Checked as a SET: ``all_verifier_graded`` is whole-bank, so a half-labelled panel has no answer
    shape and raises here. Downstream it would be silent — rank statistics and the recall
    evaluators would report the unlabelled rows as misses."""
    tasks = [t for t in experiment_data["tasks"] if t.get("id")]
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
            "source_pin": _task_pin(t),
        }
        # `query` is the TASK ID, so a judge falling back to it would grade against an
        # identifier. A declared `question` rides `Sample.question`, the only channel a judge
        # reads (`domain/sample.py`).
        if question := str(t.get("question") or "").strip():
            row["question"] = question
        out.append(row)
    return out


# Hashed into every cell's identity (`_identity_config`), so its bytes are a KEY and not a
# description of one gateway: respelling it re-keys every banked harbor cell.
_REASONING_CHANNEL = "openrouter:extra_body.reasoning"


def harbor_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Outbound payload for one episode: the task id, the candidate's skill text and its channel,
    and whichever Harbor agent kwargs the node declared as tunable."""
    payload: dict[str, Any] = {"query": query}
    for node, cfg in node_config_items(pipeline_params):
        if node != AGENT_NODE:
            continue
        payload[SKILL_DELIVERY_KEY] = _skill_delivery(cfg)
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
        kwargs = {k: v for k, v in cfg.items() if k in AGENT_KWARG_KEYS}
        effort = sent_effort(kwargs.pop("reasoning_effort", None))
        # Harbor's model name leads with the provider, as litellm's does.
        via = str(payload.get("model_name") or "").partition("/")[0]
        body = cell_gateway_body(
            openai_compat_spec(via),
            via,
            route_order=cfg.get("route_order"),
            reasoning_effort=effort,
        )
        call_kwargs: dict[str, Any] = {}
        if body is not None:
            # terminus-2 forwards `llm_call_kwargs` into every litellm completion, whose
            # `drop_params` drops `reasoning_effort` on a model it does not list; the body rides.
            call_kwargs["extra_body"] = body
        elif effort is not None:
            kwargs["reasoning_effort"] = effort
        if (reply := cfg.get("max_tokens")) is not None:
            call_kwargs["max_tokens"] = int(reply)
        if call_kwargs:
            kwargs["llm_call_kwargs"] = call_kwargs
        if (context := cfg.get("max_input_tokens")) is not None:
            # Unsent, terminus-2 assumes the model's whole window — 1M tokens for one litellm does
            # not list. Merged into litellm's entry for the model (`register_model`), so its prices
            # stand.
            kwargs["model_info"] = {"max_input_tokens": int(context)}
        if kwargs:
            payload["agent_kwargs"] = kwargs
    return payload


def _sent_spend_bound(node: str, cfg: Mapping[str, Any]) -> LLMSpendBound | None:
    """What one episode bills when it RUNS AS DECLARED, read off the payload the wire adapter
    SENDS, so the hold cannot count on a limit the agent was never given: every turn it may take,
    each reading the context cap and replying the reply cap. A limit left unsent bounds nothing,
    so that cell cannot run under a spend ceiling.

    **Its retries are not in here, and that is the whole point.** They are contingent and
    sequential — terminus retries a turn up to 3 times, litellm retries each of those up to 3, and
    `_in_process_run` re-runs the whole trial up to `_INFRA_ATTEMPTS` — so multiplying them into
    ONE simultaneous worst case priced a cell at 540 full-context calls, $4.24, against a campaign
    whose cells measured $0.00217 apiece. The ceiling then afforded exactly one cell at a time and
    silently ran every agent panel serially. A retry is still admitted: every send this agent makes
    goes through litellm in this process at its own real bound (`llm/litellm_sends.py`), and what
    the reservation cannot cover is held against the ceiling itself, which refuses it only when the
    campaign genuinely cannot afford the next send."""
    if node != AGENT_NODE:
        return None
    sent = harbor_wire_adapter("", {node: dict(cfg)}).get("agent_kwargs") or {}
    call = sent.get("llm_call_kwargs") or {}
    turns = sent.get("max_turns")
    reply = call.get("max_tokens")
    context = (sent.get("model_info") or {}).get("max_input_tokens")
    if turns is None or reply is None or context is None:
        return None
    summarized = _TERMINUS_SUMMARY_CALLS if sent.get("enable_summarize", True) else 0
    route = (call.get("extra_body") or {}).get("provider") or {}
    return LLMSpendBound(
        kind="llm",
        attempts=int(turns) * (1 + summarized),
        # Priced as a token count (`cell_bound`); the context we send IS that count.
        input_bytes=context,
        max_tokens=reply,
        hosts=None if route.get("allow_fallbacks", True) else tuple(route["order"]),
    )


# NO credential bridge, and that is the boundary rather than an omission. The agent spends
# against the provider directly, outside our LLM client — billed on our ledger all the same, send
# by send (`litellm_sends.py`) — so its key belongs in the environment Harbor runs in, where
# litellm already looks, and stays separately revocable.


def _task_pin(task: Mapping[str, Any]) -> dict[str, Any]:
    """What ONE cell was measured on, as its sample's ``source_pin``: the RESOLVED pins, never the
    declaration, which is what lets a dataset commit only a name and a version
    (:func:`_registry_tasks`). Repoint one and its banked rows describe a task that no longer
    exists, so that cell alone stops replaying."""
    return {
        "id": task.get("id"),
        "git_url": task.get("git_url"),
        "git_commit_id": task.get("git_commit_id"),
        "path": task.get("path"),
        "name": task.get("name"),
        "ref": task.get("ref"),
        # Question and answer are pinned too, though they live in our file: without them a
        # corrected gold would replay every verdict taken under the old.
        "question": task.get("question"),
        "answer": task.get("answer"),
    }


def _identity_config(
    _stores: Stores, _dataset_dir: Path, experiment: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """What every cell of the panel is measured WITH, folded into measurement identity: the agent
    driving the task and the reward it is graded on. Narrow on purpose, so a comment or a retimed
    timeout voids nothing. The tasks themselves are not here — each rides its own sample
    (:func:`_task_pin`), so widening a panel re-keys none of the cells it already had."""
    if experiment is None:
        raise ValueError(f"harbor connector: no {TASKS_FILE} on this machine to fingerprint.")
    fingerprint = stable_hash(
        [
            experiment.get("agent") or {},
            experiment.get("reward_key") or DEFAULT_TASK_REWARD_KEY,
            # Where a gateway agent's `reasoning_effort` travels (`cell_gateway_body`): a cell banked
            # while litellm dropped it ran at the model's default effort, whatever its config says.
            _REASONING_CHANNEL,
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


async def _preflight(_backend_url: str) -> str | None:
    """Three things must be true before a campaign starts spending: Harbor imports, this
    interpreter decodes UTF-8 by default, and a container runtime answers. All three fail LOUDLY
    here rather than as N identical errored rows — a missing extra, a locale-encoded interpreter
    and a stopped Docker daemon are the ways this backend is 'down', and none is visible from a
    reward of 0."""
    try:
        from harbor.trial.trial import Trial  # noqa: F401
    except ImportError:
        # Name the interpreter, because the likeliest cause is that this is the WRONG one. A bare
        # `python` on Windows resolves to the system install, which imports promptpotter fine and
        # none of its extras -- and "pip install the extra" is then a cure that pollutes that
        # interpreter instead of using the venv that already has it.
        return (
            f"the 'harbor' extra is not importable from {sys.executable}.\n"
            f"  If that is not this repo's .venv, re-run with the venv's interpreter:\n"
            f"    .venv\\Scripts\\python.exe -m promptpotter ...\n"
            f'  If it IS the venv, install the extra: pip install -e ".[harbor]"'
        )

    # Harbor reads `task.toml`, `instruction.md` and the ATIF trajectory with a bare `read_text()`,
    # so the decode falls to the locale encoding and any task carrying a byte outside it raises
    # inside `Task.__init__`. Upstream's to fix; ours is to refuse rather than discover it per cell.
    if (encoding := non_utf8_encoding()) is not None:
        return (
            f"this interpreter decodes files as {encoding!r}, not UTF-8, "
            # ASCII only in this string, deliberately -- an em dash or an ellipsis included. It is
            # printed to the very console whose encoding it is complaining about, so a non-ASCII
            # character here renders as a replacement char, in the one message that cannot afford
            # to look broken.
            "and Harbor reads its task files without naming an encoding. Every task whose "
            "instruction is not pure Latin-1 would raise before its container is built. Launch "
            "with UTF-8 mode on:\n"
            "  PowerShell:  $env:PYTHONUTF8 = '1'\n"
            "  bash:        export PYTHONUTF8=1\n"
            "  or per-run:  python -X utf8 -m promptpotter ..."
        )

    return await docker_daemon_fault()


def _agent_install_script(tools: tuple[tuple[str, str], ...]) -> str:
    """The shell an agent's setup runs to install what its container lacks, with the install
    command taken from Harbor itself (private: ``TmuxSession._get_combined_install_command``)."""
    from harbor.agents.terminus_2.tmux_session import TmuxSession

    probes = "".join(
        f'{probe} >/dev/null 2>&1 || missing="$missing {tool}"\n' for tool, probe in tools
    )
    branches = "".join(
        f"{'if' if i == 0 else 'elif'} which {pm} >/dev/null 2>&1; then "
        f"{TmuxSession._get_combined_install_command(None, {'package_manager': pm}, ['$missing'])}\n"
        for i, pm in enumerate(_PACKAGE_MANAGERS)
    )
    return f'set -e\nmissing=""\n{probes}[ -z "$missing" ] && exit 0\n{branches}fi\n'


async def _reuse_task_image(
    environment: DockerEnvironment, tools: tuple[tuple[str, str], ...], *, proxied: bool
) -> None:
    """Start a task whose image is already on this machine as a prebuilt one — with the agent's
    setup tools baked in, when the agent declares any.

    Private API: ``DockerEnvironment`` builds on every start, and a build resolves the task's
    ``FROM`` against its registry, so every cell needed the network before anything ran. The image
    is the content-addressed tag the compose overlay names — built from these same task files.

    The agent-ready tag runs the agent's own install at BUILD time, so its setup finds the tools
    present and installs nothing. The container the agent then works in carries the same tools;
    the proxy is a build argument, which never reaches an image's environment."""
    if environment.task_env_config.docker_image is not None:
        return
    image = environment._env_vars.main_image_name
    if (await docker("image", "inspect", "--format", "{{.Id}}", image))[0] != 0:
        return
    if tools:
        script = _agent_install_script(tools)
        ready = f"{image}:agent-{stable_hash(script)[:12]}"
        if (await docker("image", "inspect", "--format", "{{.Id}}", ready))[0] != 0:
            user = (await docker("image", "inspect", "--format", "{{.Config.User}}", image))[1]
            dockerfile = (
                f"FROM {image}\nUSER root\nRUN {json.dumps(['/bin/sh', '-c', script])}\n"
                + (f"USER {user}\n" if user else "")
            )
            proxy = ("--build-arg", f"http_proxy={PACKAGE_CACHE_PROXY}") if proxied else ()
            code, out = await docker(
                "build", "--tag", ready, "--add-host", "host.docker.internal:host-gateway",
                *proxy, "-",
                stdin=dockerfile.encode(),
                timeout=600,
            )  # fmt: skip
            if code != 0:
                raise CellInfrastructureError(f"building {ready} failed: {out[-300:]}", spent={})
        image = ready
    environment.task_env_config.docker_image = image
    environment._env_vars.prebuilt_image_name = image


def _write_skill(root: Path, prompt: str) -> Path:
    """The candidate's prompt as an Agent Skill. The layout is not ours to choose: Harbor uploads
    ``<skills_dir>/<name>/SKILL.md`` and the agent finds it with a depth-2 ``find``, so the extra
    directory level is load-bearing.

    Two things the agent SILENTLY skips the skill for, which scores every candidate as no-skill:
    frontmatter that is not YAML carrying both ``name`` and ``description``, and a line ending that
    is not LF — ``terminus_2.py::_parse_skill_frontmatter`` matches ``r"^---\\n(.*?)\\n---"``, so
    ``newline`` may never fall back to the platform default."""
    skill_dir = root / _SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    body = prompt.strip()
    (skill_dir / SKILL_FILENAME).write_text(
        f"---\nname: {_SKILL_NAME}\ndescription: {_SKILL_DESCRIPTION}\n---\n\n{body}\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


# Every escape a terminal recording carries and a prompt must not: SGR colour, cursor and mode
# sequences, charset selectors, bare control bytes. Local rather than `views/render/primitives.py::_ANSI_RE`,
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
# A failed check as a verifier prints one: SpreadsheetBench's `Test case 1: FAIL … Value diff at
# U5`, pytest's `FAILED path::test - …`. The LAST, because pytest closes on the line naming why.
_VERIFIER_FAILURE = re.compile(r"^.*\bFAIL.*$", re.MULTILINE)
_OUTCOME_NOTE_CAP = 300


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


def _trial_root(result: TrialResult) -> Path:
    # `trials_dir` is ours (`_trial_config`) and Harbor lays a trial out as `<trial_name>/<role>/…`,
    # so the directory is addressable without parsing `trial_uri`.
    return _TRIALS_ROOT / str(result.trial_name)


def _trajectory_sources(result: TrialResult) -> list[tuple[Path, str | None]]:
    """Every ATIF trajectory this trial wrote, with the STEP each one served.

    Two layouts: ``<trial>/agent/trajectory.json`` single-step, ``<trial>/steps/<name>/agent/`` per
    step. Walking ``step_results`` rather than globbing is what makes the STEP NAME available — the
    axis per-step terms pool on, and why a turn ordinal never becomes one
    (``domain/scoring.py::TurnRecord``)."""
    root = _trial_root(result)
    steps = getattr(result, "step_results", None) or []
    if steps:
        return [
            (root / "steps" / str(sr.step_name) / "agent" / "trajectory.json", str(sr.step_name))
            for sr in steps
        ]
    return [(root / "agent" / "trajectory.json", None)]


def _turns(result: TrialResult) -> list[dict[str, Any]]:
    """The cell's conversation, in order, each turn stamped with the STEP it served."""
    turns: list[dict[str, Any]] = []
    for path, step in _trajectory_sources(result):
        for raw in _read_trajectory(path):
            turns.append(_turn(raw, len(turns) + 1, step))
    if not turns:
        _warn_layout_drift(f"no agent trajectory under {_trial_root(result)}")
    return turns


def _skill_opened(result: TrialResult) -> float | None:
    """Whether the episode OPENED the skill: ``1.0``, ``0.0``, or ``None`` for no evidence.

    The candidate's prompt is the skill's BODY, and the agent is shown only the frontmatter, so an
    unopened skill is an episode that ran with no candidate prompt in it — every arm of such a
    round is the same no-skill episode and the δ ruler is flat by construction.

    Read off ``tool_calls[].arguments``, never a turn's message: the ``<available_skills>`` block
    carries the skill's own path and is appended to the INSTRUCTION, so a text scan there matches
    every episode whether or not it acted.

    ``None`` rather than ``0.0`` where no trajectory exists — an episode that produced no record has
    not declined to open the skill. Same rule as ``_phase_timings``."""
    saw_trajectory = False
    for path, _step in _trajectory_sources(result):
        for raw in _read_trajectory(path):
            saw_trajectory = True
            for call in raw.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                args = call.get("arguments")
                if SKILL_FILENAME in (json.dumps(args) if args else ""):
                    return 1.0
    return 0.0 if saw_trajectory else None


def _skill_in_first_request(result: TrialResult, prompt: str) -> float | None:
    """``SKILL_KEY`` under ``skill_delivery: system_prompt``: whether every trajectory's first turn
    — the request the template became — carried the skill body. ``None`` without a trajectory."""
    firsts = [
        steps[0] for path, _step in _trajectory_sources(result) if (steps := _read_trajectory(path))
    ]
    if not firsts:
        return None
    body = prompt.strip()
    return 1.0 if all(body in _atif_text(first.get("message")) for first in firsts) else 0.0


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
    root = _trial_root(result)
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


_PHASES: tuple[str, ...] = ("environment_setup", "agent_setup", "agent_execution", "verifier")


def _phase_timings(result: TrialResult, attempt_s: float) -> dict[str, float]:
    """Where the GRADED attempt's wall clock went, in seconds — Harbor's own four phases plus what
    it did not attribute.

    Read from ``TrialResult``'s ``TimingInfo`` pairs rather than stopwatched here: Harbor already
    brackets each phase, and a second set of brackets around the same work would drift from it and
    give two answers to one question. The remainder — the attempt minus the phases Harbor reported
    — lands under ``overhead`` so the map SUMS to that attempt's wall clock; without it a reader
    would silently take the parts for the whole, which is the failure this key exists to prevent.
    The cell's own clock (``step_timings``) also counts every attempt this one replaced.

    An absent phase is omitted rather than zeroed. ``0.0`` says the phase ran instantly, absence
    says Harbor did not report it, and on a task with no verifier only one of those is true."""
    out: dict[str, float] = {}
    for phase in _PHASES:
        info = getattr(result, phase, None)
        started, finished = getattr(info, "started_at", None), getattr(info, "finished_at", None)
        if started is None or finished is None:
            continue
        seconds = (finished - started).total_seconds()
        if seconds >= 0.0:
            out[phase] = seconds
    if out:
        out["overhead"] = max(0.0, attempt_s - sum(out.values()))
    return out


def _trial_failures(result: TrialResult) -> list[tuple[str, str]]:
    """Every exception the trial recorded, as ``(type, "type: message")``."""
    return [
        (exc.exception_type, f"{exc.exception_type}: {exc.exception_message}")
        for exc in (result.exception_info, *(sr.exception_info for sr in result.step_results or []))
        if exc is not None
    ]


def _provider_throttle(result: TrialResult) -> str | None:
    """The throttle that ended this episode, in the provider's words, or ``None``. terminus-2
    retries one inside the turn and gives up with it; one that only preceded the agent running out
    of clock ended it too. A throttle the episode outlived is latency, and its grade stands."""
    failures = _trial_failures(result)
    for _, detail in failures:
        if _PROVIDER_THROTTLE.search(detail):
            return detail
    if any(kind == "AgentTimeoutError" for kind, _ in failures):
        return _first_line(_trial_root(result) / "trial.log", _PROVIDER_THROTTLE)
    return None


def _infrastructure_failure(result: TrialResult) -> tuple[str, ErrorCategory] | None:
    """Why this trial measured the machine rather than the agent, and the category that cell is
    banked as, or ``None``. Only a ``CONNECTION`` one is worth a retry.

    Asked whether or not a reward exists: a verifier whose tool download failed still grades — a
    ``set -e`` script does not stop on a failed ``apt-get update && …`` — without the tools it was
    fetching. Only package-manager and registry messages count, so an agent's own failed request
    (a server it should have started) stays its score."""
    for kind, detail in _trial_failures(result):
        # A litellm `APIError` carrying OpenRouter's body.
        if is_provider_credit_refusal(detail):
            return (
                f"the provider account is out of credit: {detail[:300]}",
                ErrorCategory.PROVIDER_CREDIT,
            )
        if kind in _HARNESS_TIMEOUTS or _HARNESS_NETWORK_FAILURE.search(detail):
            return detail[:300], ErrorCategory.CONNECTION
    for log in _trial_root(result).rglob("test-stdout.txt"):
        if log.parent.name == "verifier" and (line := _first_line(log, _FETCH_FAILURE)):
            return (
                f"the verifier could not download what it installs: {line}",
                ErrorCategory.CONNECTION,
            )
    return None


def _first_line(path: Path, pattern: re.Pattern[str]) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = pattern.search(text)
    return None if match is None else text[match.start() :].partition("\n")[0][:300]


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

    root = _trial_root(result)
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


def _outcome_note(result: TrialResult) -> str | None:
    """``pipeline_data.outcome_note``, which ``failing_samples`` renders where a labelled cell
    shows what was said against the truth. ``None`` where the verifier printed no failed check."""
    log = _trial_root(result) / "verifier" / "test-stdout.txt"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    failures = _VERIFIER_FAILURE.findall(_TERMINAL_ESC.sub("", text))
    return failures[-1].strip()[:_OUTCOME_NOTE_CAP] if failures else None


def _step_tokens(result: TrialResult, model_name: str | None) -> dict[str, StepTokenUsage]:
    """The episode's spend on the SAME channel a remote backend's rides — the ROW's account of what
    the cell cost, never its bill: an agent in this process was billed send by send as it ran
    (``litellm_sends.py``), and one in a container by :func:`_container_bill`. Harbor totals it for
    us (``TrialResult.compute_token_cost_totals``), so this is a projection rather than a count.
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
    # Truthy, not `is not None`: Harbor reports a call litellm cannot price (any `:nitro` name) as
    # 0.0, and a banked 0.0 is a free cell where the honest answer is an unpriced one.
    if cost:
        entry["cost_usd"] = float(cost)
    if model_name:
        entry["model"] = model_name
    return {AGENT_NODE: entry}


def _container_bill(result: TrialResult, model_name: str | None) -> Billed | None:
    """The bill of a trial whose agent ran in its container, where none of its sends passes through
    this process: what Harbor read off the agent's own logs, the only account there is. ``None``
    where it read nothing — an unknown, never a zero."""
    entry = _step_tokens(result, model_name).get(AGENT_NODE)
    if entry is None:
        return None
    model, provider = litellm_route(model_name) if model_name else (None, None)
    usage = TokenAccount(
        input=entry["input"], output=entry["output"], cache_read=entry.get("cache_read")
    )
    return Billed(usage, entry.get("cost_usd"), model=model, provider=provider)


def _sum_spend(parts: list[dict[str, StepTokenUsage]]) -> dict[str, StepTokenUsage]:
    """Several attempts' ``step_tokens`` as one bill. A total cost only where every attempt
    priced itself: one unpriced attempt makes the sum unpriced, never a smaller price."""
    entries = [p[AGENT_NODE] for p in parts if AGENT_NODE in p]
    if not entries:
        return {}
    total: StepTokenUsage = {
        "input": sum(e["input"] for e in entries),
        "output": sum(e["output"] for e in entries),
        "estimated": False,
    }
    if any("cache_read" in e for e in entries):
        total["cache_read"] = sum(e.get("cache_read", 0) for e in entries)
    if all("cost_usd" in e for e in entries):
        total["cost_usd"] = sum(e["cost_usd"] for e in entries)
    if model := entries[-1].get("model"):
        total["model"] = model
    return {AGENT_NODE: total}


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


@dataclass(frozen=True)
class _Episode:
    query: str
    task: dict[str, Any]
    reward_key: str
    agent_name: str
    agent_kwargs: dict[str, Any]
    model_name: str | None
    environment: str
    cached: bool
    prompt: str | None
    in_system_prompt: bool
    tools: tuple[tuple[str, str], ...]


def _episode(workload: InProcessWorkload, sample: Sample, payload: dict[str, Any]) -> _Episode:
    # The task IS the sample's pin (`_task_pin`): the pins `_task_config` builds a trial from.
    task = sample.source_pin
    if task is None:
        raise CellUnscoreableError(
            f"harbor task {sample.query!r} carries no pin, so it is no row of a {TASKS_FILE}.",
            spent={},
        )
    panel = workload.experiment or {}
    agent_cfg = panel.get("agent") or {}
    agent_kwargs = dict(agent_cfg.get("kwargs") or {})
    agent_kwargs.update(payload.get("agent_kwargs") or {})
    agent_name = agent_cfg.get("name") or "terminus-2"
    return _Episode(
        query=sample.query,
        task=task,
        reward_key=panel.get("reward_key") or DEFAULT_TASK_REWARD_KEY,
        agent_name=agent_name,
        agent_kwargs=agent_kwargs,
        # The MODEL comes from the node config, never from the agent block — `datasets/CLAUDE.md`
        # makes `nodes.{node}.config.model` the dataset's one statement of what it measures on, and
        # a second spelling in `harbor_tasks.yaml` would be a second owner of the same fact.
        model_name=payload.get("model_name"),
        environment=agent_cfg.get("environment") or "docker",
        cached=panel.get("package_cache") in PACKAGE_CACHE_SCOPES,
        prompt=payload.get("prompt"),
        in_system_prompt=payload[SKILL_DELIVERY_KEY] == "system_prompt",
        # terminus-2 installs asciinema only while it records; baking it otherwise adds a tool.
        tools=tuple(
            (tool, probe)
            for tool, probe in _AGENT_TOOLS.get(agent_name, ())
            if tool != "asciinema" or agent_kwargs.get("record_terminal_session") is not False
        ),
    )


def _trial_config(episode: _Episode, skills: list[str]) -> Any:
    from harbor.models.trial import config as harbor_config

    os.environ[_PRODUCER_ENV] = CONTAINER_PRODUCER
    return harbor_config.TrialConfig(
        task=_task_config(episode.task, harbor_config),
        trials_dir=_TRIALS_ROOT,
        agent=harbor_config.AgentConfig(
            name=episode.agent_name,
            model_name=episode.model_name,
            skills=skills,
            kwargs=episode.agent_kwargs,
        ),
        environment=harbor_config.EnvironmentConfig(
            type=episode.environment,
            extra_docker_compose=[_DOCKER_OVERLAY] if episode.environment == "docker" else [],
        ),
        verifier=harbor_config.VerifierConfig(
            env={"http_proxy": PACKAGE_CACHE_PROXY} if episode.cached else {}
        ),
    )


def _install_system_skill(trial: Trial, agent_name: str, prompt: str) -> None:
    # Private API: the template is read at construction, so it is replaced on the
    # built agent. An agent without one cannot carry the mode, and says so here.
    template = getattr(trial.agent, "_prompt_template", None)
    if not isinstance(template, str):
        raise RuntimeError(
            f"harbor connector: agent {agent_name!r} has no prompt template, so "
            f"`{SKILL_DELIVERY_KEY}: system_prompt` cannot reach its model."
        )
    trial.agent._prompt_template = _system_skill_template(template, prompt)


async def _billed_run(trial: Trial, model_name: str | None) -> TrialResult:
    from harbor.agents.installed.base import BaseInstalledAgent

    if not isinstance(trial.agent, BaseInstalledAgent):
        # Every send it makes goes through litellm in this process, billed as it is made.
        with litellm_sends_billed_as(_AGENT_SEND):
            result = await trial.run()
        return result
    # One in its container sends from there, past this process: the trial is one send from
    # here, at whatever the cell has left, billed off Harbor's reading of its logs.
    with admitted(_AGENT_SEND, reservation_left(), model=model_name, provider=None) as admission:
        result = await trial.run()
        if (billed := _container_bill(result, model_name)) is not None:
            admission.settle(billed)
    return result


async def _attempt(episode: _Episode) -> tuple[TrialResult, float]:
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.trial.trial import Trial

    prompt, in_system_prompt = episode.prompt, episode.in_system_prompt
    if episode.cached:
        with machine_step():
            await ensure_package_cache()
    with tempfile.TemporaryDirectory(prefix="pp-skill-") as skill_root:
        skills = (
            [str(_write_skill(Path(skill_root), prompt))] if prompt and not in_system_prompt else []
        )
        # BEFORE `Trial.create`, not after it. Creation builds or pulls the environment image
        # and is a real part of what a cell costs; timing only `run()` reported an agent
        # episode as cheaper than it was, by exactly the amount the harness spent getting ready.
        start = time.monotonic()
        trial = await Trial.create(_trial_config(episode, skills))
        if prompt and in_system_prompt:
            _install_system_skill(trial, episode.agent_name, prompt)
        if isinstance(trial.agent_environment, DockerEnvironment):
            with machine_step():
                await _reuse_task_image(
                    trial.agent_environment, episode.tools, proxied=episode.cached
                )
        result = await _billed_run(trial, episode.model_name)
        return result, time.monotonic() - start


async def _back_off(query: str, n: int, cause: str, category: ErrorCategory) -> None:
    logger.warning(
        "harbor task %r attempt %d/%d measured the infrastructure (%s); retrying in %.0fs",
        query,
        n,
        _INFRA_ATTEMPTS,
        cause,
        _INFRA_BACKOFF_S * n,
    )
    # And on the LEDGER, where every surface can read it. A run hosted by the API server writes
    # no terminal mirror, so a warning that only reaches `logging` exists in one console — and
    # this one carries the whole diagnosis, while the stop it ends in says only that a backend
    # was unreachable.
    emit_backend_warning(
        kind="infrastructure",
        attempt=n,
        max_attempts=_INFRA_ATTEMPTS,
        wait_s=_INFRA_BACKOFF_S * n,
        query=query,
        detail=cause,
        error_class=category.value,
    )
    await asyncio.sleep(_INFRA_BACKOFF_S * n)


async def _run_episode(episode: _Episode) -> tuple[TrialResult, float, dict[str, StepTokenUsage]]:
    """The graded attempt, how long THAT attempt took, and every attempt's spend."""
    query = episode.query
    # Every attempt's spend, discarded ones included: a retried episode still ran the agent.
    attempts: list[dict[str, StepTokenUsage]] = []
    for n in range(1, _INFRA_ATTEMPTS + 1):
        try:
            result, elapsed = await _attempt(episode)
        except CellInfrastructureError as exc:
            cause, category = str(exc), exc.category
        else:
            attempts.append(_step_tokens(result, episode.model_name))
            if (throttle := _provider_throttle(result)) is not None:
                # The provider's load, which the run answers for every cell at once.
                raise CellThrottledError(
                    f"harbor task {query!r} was throttled by its model provider: {throttle}",
                    spent=_sum_spend(attempts),
                )
            if (failure := _infrastructure_failure(result)) is None:
                break
            cause, category = failure
        forget_package_cache()
        if n == _INFRA_ATTEMPTS or category is not ErrorCategory.CONNECTION:
            raise cell_failure(
                f"harbor task {query!r} measured the infrastructure, not the agent, "
                f"on attempt {n}/{_INFRA_ATTEMPTS}: {cause}",
                category,
                spent=_sum_spend(attempts),
            )
        await _back_off(query, n, cause, category)
    return result, elapsed, _sum_spend(attempts)


def _reward(result: TrialResult, episode: _Episode, bill: dict[str, StepTokenUsage]) -> float | int:
    query = episode.query
    # BEFORE the reward is read: Harbor drops a step with no verifier result from its own
    # denominator, so the number below would describe fewer steps than the task declared, and
    # describe it as a success.
    if unscoreable := _unscoreable_step(result):
        raise CellUnscoreableError(f"harbor task {query!r}: {unscoreable}.", spent=bill)

    rewards = result.verifier_result.rewards if result.verifier_result else None
    reward = (rewards or {}).get(episode.reward_key)
    if reward is None:
        # Nothing to grade, and a 0.0 here would be indistinguishable from an episode that ran
        # and failed. The campaign excludes the cell instead.
        raise CellUnscoreableError(
            f"harbor task {query!r} produced no reward under key {episode.reward_key!r} "
            f"(rewards={rewards}); the episode is unscoreable, not a zero.",
            spent=bill,
        )
    return cast("float | int", reward)


def _skill_arrival(result: TrialResult, episode: _Episode) -> float | None:
    prompt, in_system_prompt = episode.prompt, episode.in_system_prompt
    # Only where a skill was actually injected. With no prompt there is no artifact to open, so
    # `0.0` would report the arm declining to read a file that was never written.
    opened = (
        None
        if not prompt
        else _skill_in_first_request(result, prompt)
        if in_system_prompt
        else _skill_opened(result)
    )
    if opened is not None and not opened:
        # A line per cell rather than a scoring discount: the term is constant on a healthy
        # channel, so charging it moves nothing when things work and discounts every arm
        # UNIFORMLY when they break — invisible arithmetically, and identical to a finding.
        logger.warning(
            "harbor connector: %r measured a NO-SKILL episode — the candidate's prompt "
            "reached the model not at all, so a round of these cannot separate arms on it. %s",
            episode.query,
            "The first request did not carry it: `_system_skill_template` no longer reaches "
            "terminus-2's template."
            if in_system_prompt
            else f"The agent never opened it: check the frontmatter parses (`_write_skill`) "
            f"and that {SKILL_FILENAME} is present in the container.",
        )
    return opened


def _episode_data(
    result: TrialResult,
    episode: _Episode,
    reward: float | int,
    attempt_s: float,
    bill: dict[str, StepTokenUsage],
) -> dict[str, Any]:
    turns = _turns(result)
    data: dict[str, Any] = {
        REWARD_KEY: float(reward),
        "terminal_node": AGENT_NODE,
        "step_tokens": bill,
        "reasoning_trace": _digest(result, episode.query, reward, turns),
        ANSWER_KEY: _answer(result),
    }
    # Absent, never empty: `[]` would claim this episode had no turns and `{}` that its steps
    # scored nothing. A single-step task has neither concept.
    if turns:
        data["turns"] = turns
    if note := _outcome_note(result):
        data["outcome_note"] = note
    if phases := _phase_timings(result, attempt_s):
        data["step_phases"] = phases
    if (opened := _skill_arrival(result, episode)) is not None:
        data[SKILL_KEY] = opened
    data.update(_step_rewards(result))
    return data


async def _in_process_run(
    workload: InProcessWorkload, sample: Sample, payload: dict[str, Any]
) -> dict[str, Any]:
    """Run one episode and project its verdict onto the ``{"data": {…}}`` shape ``measure_sample``
    parses from an HTTP body — so the scorer reads a Harbor result identically to a remote one."""
    episode = _episode(workload, sample, payload)
    result, attempt_s, bill = await _run_episode(episode)
    reward = _reward(result, episode, bill)
    return {"data": _episode_data(result, episode, reward, attempt_s, bill)}


CONNECTOR = Connector(
    name="harbor",
    execution="in_process",
    wire_adapter=harbor_wire_adapter,
    # Harbor sets no limit of its own that we do not send it, so the bound is ours.
    sent_spend_bound=_sent_spend_bound,
    # Every send an episode makes is billed where it is made (`_in_process_run`), so a cell is no
    # send of its own: it RESERVES that bound, and its sends draw on the reservation.
    holds_own_sends=True,
    session_factory=NoopSession,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    preflight=_preflight,
    # The connector most exposed to upstream drift, because `_digest` reads Harbor's private trial
    # layout. Opt-in everywhere else; declared here for that reason.
    expected_revision=EXPECTED_HARBOR_SERIES,
    version_check=_version_check,
    identity_config=_identity_config,
    resolve_experiment=_resolve_experiment,
    # An episode is a whole agent run — minutes, with its own container build and its own spend —
    # so it is a cell.
    measured_unit="cell",
    # By default the prompt is the SKILL's body, not a message. `terminus-2` shows the model only
    # the frontmatter, so it arrives only if the model opens the file — which is why `SKILL_KEY` is
    # a required observation beside it, and why this is the one connector where a value can be
    # optimized every round and reach nothing. `skill_delivery: system_prompt` sends it instead.
    prompt_delivery=_prompt_delivery,
    # Each cell holds a container, so the ceiling is the operator's MACHINE rather than the
    # provider, and it binds every run on that machine together — two campaigns at this depth hold
    # twice this many containers, and a verifier that cannot finish inside its timeout measures the
    # box. The depth actually reached is the lower of this and what the spend ceiling admits: every
    # cell in flight reserves its whole bound (`_sent_spend_bound`).
    max_cells_in_flight=5,
    cells_hold_the_machine=True,
    compose_overlay=_DOCKER_OVERLAY,
    # The keys always emitted that a formula reads, verified against the dataset's declared
    # mappings at init. Per-step rewards are NOT here — a single-step task emits none, so
    # declaring them would fail init for every task that is not multi-step.
    #
    # `SKILL_KEY` earns its place where those cannot: EVERY harbor cell handed a prompt can answer
    # it, single-step or not. Declared here so a harbor dataset that forgets the mapping raises at
    # init rather than dropping the observation in silence — which on this key would mean a whole
    # campaign of arms scored as no-skill with nothing saying so.
    required_observation_keys=(REWARD_KEY, SKILL_KEY),
    # An episode answers even though a verifier grades it, and until this existed nothing carried
    # the answer: no ranking means `predicted` was the `NO_RESULT` sentinel on every cell here.
    answer_key=ANSWER_KEY,
    # The "samples" ARE the tasks declared there — read from the dataset config dir at init.
    experiment_file=TASKS_FILE,
    default_pipeline=(AGENT_NODE,),
    # No `default_node_config`, and that is a REFUSAL rather than an omission. The harbor datasets
    # repeat their `prompt_info` + `optimizer` blocks verbatim, and folding them here does not
    # deduplicate anything: this field is an INGEST SEED written into a fresh dataset's own
    # `pipeline.yaml` (`protocol.py`), never something a run inherits — for an `in_process`
    # connector `_resolve_pipeline_schema` parses the dataset file ALONE. A harbor dataset cannot
    # be ingested either (`datasets/ingest.py` refuses a connector declaring an `experiment_file`
    # and carrying no labels), so a seed here reaches nothing. What a measurement declares must not
    # ship, and change, independently of the measurement.
    # No `node_roles`: that roster exists to raise INPUT DEPENDENCIES (a `candidate_source` node
    # wants a candidate library dropped in place). An agent node wants nothing but its task.
)


__all__ = [
    "AGENT_NODE",
    "ANSWER_FILENAME",
    "ANSWER_KEY",
    "CONNECTOR",
    "REWARD_KEY",
    "TASKS_FILE",
    "harbor_wire_adapter",
]
