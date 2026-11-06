"""One containerized Harbor trial per cell, graded by the task's verifier.

The candidate prompt ships as ONE Agent Skill per episode with a FIXED frontmatter ``description``,
its body within the spec's limits (``docs/research/external-constraints.md`` § SKILL);
``nodes.agent.config.skill_delivery: system_prompt`` is the second channel, fixed per campaign and
never in ``param_keys``. The Skill reaches the model only if opened, so arrival is MEASURED
(``_skill_opened`` / ``_skill_in_first_request`` fill ``SKILL_KEY``); no core ``turn_scalars``
member carries it, since it would be constant ``1.0`` elsewhere.

The panel is the pinned roster file ``pin-roster`` writes, which refuses to overwrite a moved pin;
no read fetches the registry. A local task's ``path`` is relative to its panel's directory, and the
panel's ``agent.kwargs`` may set no key ``nodes.agent.config`` owns.

Trial scratch goes to the system temp dir, anything durable to the measurement archive. Never sweep
the task images, the package cache, or a container not naming our compose overlay: the Docker host
has other tenants, and ``docker_host.py::reap_dead_producers`` reaps our leftovers. A trial that
measured the machine is never a cell: ``_infrastructure_failure`` retries, then raises.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, cast, get_args

import httpx

from promptpotter.config.settings import non_utf8_encoding
from promptpotter.connectors.protocol import (
    PACKAGE_CACHE_KEY,
    Connector,
    InProcessWorkload,
    cell_envelope_of,
)
from promptpotter.domain.pipeline_params import NodeParams
from promptpotter.domain.pipeline_schema import LLMSpendBound
from promptpotter.domain.spend import StepUsage, TokenAccount
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
from promptpotter.infrastructure.llm.registry import (
    get_llm_client,
    host_key,
    host_key_attr,
    openai_compat_spec,
    providers,
)
from promptpotter.infrastructure.llm.request import ChatMessage, ChatRequest
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.send_pacing import SEND_ATTEMPTS, SendBudget, drawn_budget
from promptpotter.infrastructure.llm.spend_book import (
    Billed,
    CallLabel,
    admitted,
    answered,
    reservation_left,
)
from promptpotter.infrastructure.llm.telemetry import emit_backend_warning, rate_priced_usd
from promptpotter.shared.errors import (
    CellInfrastructureError,
    CellUnscoreableError,
    ContextOverflowError,
    ErrorCategory,
    NoApiKeyError,
    SendRefusedError,
    cell_failure,
    is_provider_credit_refusal,
)
from promptpotter.shared.extras import Extra
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.tls import tls_context

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import ModuleType

    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.trial.paths import TrialPaths
    from harbor.models.trial.result import TrialResult
    from harbor.trial.trial import Trial

    from promptpotter.domain.pipeline_params import PipelineParams
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.value_tree import Delivery
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

# Harbor's agents load litellm, which otherwise fetches its price map over the network at import.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


AGENT_NODE = "agent"
_AGENT_SEND = CallLabel(AGENT_NODE, "backend")

REWARD_KEY = "env_reward"
# Harbor parses a task's bare `reward.txt` into `{"reward": x}`.
DEFAULT_TASK_REWARD_KEY = "reward"

ANSWER_KEY = "agent_answer"
ANSWER_FILENAME = "answer.txt"

SKILL_KEY = "skill_opened"

INSTRUMENT_KEY = "harbor_instrument"

# What the agent's model is SENT, by our client (`_AgentModel`): never terminus-2's own sender.
_SEND_KEYS = ("temperature", "top_p", "reasoning_effort", "max_tokens", "max_input_tokens")
# What terminus-2 itself reads.
_TERMINUS_KEYS = ("max_turns", "parser_name", "enable_summarize", "store_all_messages")
# Owned by `nodes.agent.config`, so a panel's `agent.kwargs` may set none of them.
AGENT_KWARG_KEYS = frozenset((*_SEND_KEYS, *_TERMINUS_KEYS))
# What terminus-2 hands its own sender, which `_AgentModel` replaces: set, each would reach no wire.
_DISCARDED_SENDER_KEYS = frozenset(
    {
        "api_base",
        "model_info",
        "max_thinking_tokens",
        "use_responses_api",
        "llm_backend",
        "llm_kwargs",
        "llm_call_kwargs",
        "collect_rollout_details",
        "interleaved_thinking",
    }
)

_TRIALS_ROOT = PRODUCER_SCRATCH
# Read by the compose overlay, which interpolates it into the producer label.
_PRODUCER_ENV = "PROMPTPOTTER_HARBOR_PRODUCER"

_DOCKER_OVERLAY = Path(__file__).parent / "resources" / "harbor-docker-compose.yaml"

# Verifier only: a proxy in the agent's environment is observable by the agent.
PACKAGE_CACHE_SCOPES = frozenset({"verifier"})

# The ATIF file an agent writes into its logs dir, which Harbor names but never reads back.
_TRAJECTORY = "trajectory.json"


class _AgentDeclares(NamedTuple):
    tools: tuple[tuple[str, str], ...]
    # Written into its logs dir by every step it runs: `_layout_fault` refuses a clean step without one.
    logs: tuple[str, ...]
    # Its every send goes through `agent._llm`, which `_AgentModel` replaces; `False`: it sends none.
    model_seam: bool


# An in-process agent is declared or refused (`_attempt`): one sending past `_llm` would
# reach its provider unadmitted. An installed agent sends from its container, its ATIF best-effort.
# terminus-2's tools mirror `TmuxSession._install_recording_tools`; `run` dumps its trajectory in `finally`.
_AGENTS: dict[str, _AgentDeclares] = {
    "terminus-2": _AgentDeclares(
        tools=(("tmux", "tmux -V"), ("asciinema", "asciinema --version")),
        logs=(_TRAJECTORY,),
        model_seam=True,
    ),
    "oracle": _AgentDeclares(tools=(), logs=(), model_seam=False),
    "nop": _AgentDeclares(tools=(), logs=(), model_seam=False),
}
_UNDECLARED = _AgentDeclares(tools=(), logs=(), model_seam=False)


def _agent_name(panel: Mapping[str, Any] | None) -> str:
    return ((panel or {}).get("agent") or {}).get("name") or "terminus-2"


# `TmuxSession._detect_system_info`'s probe order, which decides the install command it runs.
_PACKAGE_MANAGERS = ("apt-get", "dnf", "yum", "apk", "pacman", "brew", "pkg", "zypper")

_CELL_ATTEMPTS = 3
assert _CELL_ATTEMPTS < SEND_ATTEMPTS, "a trial holds a container: it reruns less than a send"
_INFRA_BACKOFF_S = 45.0
# A whole agent episode, its infrastructure retries and their backoff included.
_CELL_ENVELOPE_S = 3600.0

# A terminus-2 summarize pass is three calls (`_summarize`).
_TERMINUS_SUMMARY_CALLS = 3
_REGISTRY_TIMEOUT_S = 30.0
# Flat beside the overlay: `pyproject.toml` package-data ships `connectors/resources/*.json`.
_ROSTER_DIR = Path(__file__).parent / "resources"
_REGISTRY_NAME = re.compile(r"[\w.-]+")

# The harness's own words (Docker, a registry pull): our sends fail typed (`_AgentModel.failure`).
_HARNESS_NETWORK_FAILURE = re.compile(
    r"no such host|Temporary failure in name resolution|failed to fetch oauth token|"
    r"TLS handshake timeout|i/o timeout|dial tcp|Network is unreachable"
)
_FETCH_FAILURE = re.compile(
    r"^Err:\d+ https?://|Failed to fetch https?://|Temporary failure resolving|"
    r"Could not fetch URL https?://|Failed to establish a new connection|"
    r"npm (?:ERR!|error) code (?:EAI_AGAIN|ENOTFOUND|ETIMEDOUT|ECONNRESET)",
    re.MULTILINE,
)
_HARNESS_TIMEOUTS = frozenset(
    {"EnvironmentStartTimeoutError", "AgentSetupTimeoutError", "VerifierTimeoutError"}
)
# What a send ends a cell on, never a score: the agent never saw the provider answer.
_NOT_THE_PROMPT = frozenset(
    {
        ErrorCategory.PROVIDER_THROTTLED,
        ErrorCategory.PROVIDER_CREDIT,
        ErrorCategory.CONNECTION,
        ErrorCategory.SERVER,
    }
)

# FIXED, never a search axis: a candidate writing its own description could win by being uninviting.
_SKILL_NAME = "task-approach"
SKILL_FILENAME = "SKILL.md"
_SKILL_DESCRIPTION = (
    "Read this before acting. Required approach, conventions and completion criteria for "
    "this task. Always consult it first."
)

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


def _prompt_delivery(pipeline_params: PipelineParams) -> Delivery:
    cfg = pipeline_params.node(AGENT_NODE).params
    return "request" if _skill_delivery(cfg) == "system_prompt" else "artifact_body"


def _system_skill_template(template: str, prompt: str) -> str:
    block = f'<skill name="{_SKILL_NAME}">\n{prompt.strip()}\n</skill>\n\n'
    # terminus-2 fills the template with `str.format`, so the skill's own braces are doubled.
    return block.replace("{", "{{").replace("}", "}}") + template


def _roster_path(dataset: str, version: str) -> Path:
    for part in (dataset, version):
        if not _REGISTRY_NAME.fullmatch(part):
            raise ValueError(f"harbor connector: {part!r} is not a registry name.")
    return _ROSTER_DIR / f"harbor-roster.{dataset}@{version}.json"


@functools.cache
def _pinned_roster(dataset: str, version: str) -> list[dict[str, Any]]:
    # A read never dials out: the roster is what `pin_roster` wrote, or nothing.
    path = _roster_path(dataset, version)
    try:
        pin = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(
            f"harbor connector: {dataset}@{version} has no pinned roster ({path.name}). Pin it "
            f"once with `python -m promptpotter pin-roster {dataset} {version}`."
        ) from None
    return list(pin["tasks"])


def _registry_roster(dataset: str, version: str) -> list[dict[str, Any]]:
    from harbor.constants import DEFAULT_REGISTRY_URL
    from harbor.models.registry import DatasetSpec

    # Not `JsonRegistryClient`: its `requests.get` takes no timeout.
    response = httpx.get(
        DEFAULT_REGISTRY_URL,
        timeout=_REGISTRY_TIMEOUT_S,
        follow_redirects=True,
        verify=tls_context(),
    )
    response.raise_for_status()
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


def pin_roster(dataset: str, version: str) -> Path:
    """The ONE fetch of Harbor's registry, an operator step: its file serves every later read."""
    path = _roster_path(dataset, version)
    tasks = _registry_roster(dataset, version)
    # The registry is a file on upstream's main branch: a version is no promise its roster holds.
    if path.is_file() and json.loads(path.read_text(encoding="utf-8"))["tasks"] != tasks:
        raise ValueError(
            f"harbor connector: the registry now publishes a different {dataset}@{version} "
            f"roster than {path.name} pins. The pin stands — a panel is never re-cut under the "
            f"name it had (`datasets/CLAUDE.md`); delete the file only to re-cut on purpose."
        )
    # One task per line: a re-pin's diff reads task by task.
    lines = ",\n".join(f"  {json.dumps(t)}" for t in tasks)
    path.write_text(
        f'{{"harbor_dataset": {json.dumps(dataset)}, '
        f'"harbor_dataset_version": {json.dumps(version)}, "tasks": [\n{lines}\n]}}\n',
        encoding="utf-8",
        newline="\n",
    )
    return path


def _resolve_experiment(_stores: Stores, panel: Mapping[str, Any]) -> dict[str, Any]:
    agent = panel.get("agent") or {}
    kwargs = set(agent.get("kwargs") or {})
    if owned := sorted(kwargs & AGENT_KWARG_KEYS):
        raise ValueError(
            f"harbor connector: the panel sets {owned} under `agent.kwargs`, which "
            f"`pipeline.nodes.{AGENT_NODE}.config` owns and lays over it. Delete them here."
        )
    seamed = _AGENTS.get(_agent_name(panel), _UNDECLARED).model_seam
    if seamed and (discarded := sorted(kwargs & _DISCARDED_SENDER_KEYS)):
        raise ValueError(
            f"harbor connector: the panel sets {discarded} under `agent.kwargs`, which configure "
            "the agent's own sender; `_AgentModel` sends in its place, so they would reach no "
            "wire. Delete them here."
        )
    resolved = _resolve_tasks(panel)
    for task in resolved["tasks"]:
        _task_pin(task)
    return resolved


def _resolve_tasks(panel: Mapping[str, Any]) -> dict[str, Any]:
    if inline := panel.get("tasks"):
        return {**panel, "tasks": list(inline)}
    dataset = panel.get("harbor_dataset")
    if not dataset:
        raise ValueError(
            "harbor connector: the panel names neither `harbor_dataset` (a published "
            "dataset to resolve) nor an inline `tasks` list."
        )
    version = panel.get("harbor_dataset_version")
    if not version:
        raise ValueError(
            f"harbor connector: the panel declares `harbor_dataset: {dataset}` with no "
            f"`harbor_dataset_version`. Resolving 'latest' would put a moving roster behind a "
            f"fixed dataset name, so the version is required."
        )
    tasks = _pinned_roster(str(dataset), str(version))

    include = panel.get("tasks_include")
    if not include:
        return {**panel, "tasks": tasks}
    wanted = list(dict.fromkeys(str(i) for i in include))
    by_id = {t["id"]: t for t in tasks}
    if missing := [i for i in wanted if i not in by_id]:
        raise ValueError(
            f"harbor connector: the panel includes {missing}, which {dataset}@{version} "
            f"does not publish (it has {sorted(by_id)})."
        )
    return {**panel, "tasks": [by_id[i] for i in wanted]}


def _extract_experiment(experiment_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    tasks = [t for t in experiment_data["tasks"] if t.get("id")]
    labelled = [t for t in tasks if str(t.get("answer") or "").strip()]
    if labelled and len(labelled) != len(tasks):
        unlabelled = [t["id"] for t in tasks if not str(t.get("answer") or "").strip()]
        raise ValueError(
            f"harbor connector: the panel declares an `answer` for {len(labelled)} of "
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
        if question := str(t.get("question") or "").strip():
            row["question"] = question
        out.append(row)
    return out


# Hashed into every cell's identity (`_identity_config`): respelling it re-keys every banked cell.
_AGENT_SENDER = "registry-client.ChatRequest"


def _agent_fields(cfg: NodeParams) -> dict[str, Any]:
    if discarded := sorted(set(cfg.keys()) & _DISCARDED_SENDER_KEYS):
        raise ValueError(
            f"harbor connector: `nodes.{AGENT_NODE}.config` sets {discarded}, which configure "
            "terminus-2's own sender; `_AgentModel` sends in its place, so they would reach no "
            "wire. Delete them."
        )
    fields: dict[str, Any] = {SKILL_DELIVERY_KEY: _skill_delivery(cfg.params)}
    if cfg.prompt:
        fields["prompt"] = cfg.prompt
    if cfg.model:
        fields["model"] = cfg.model
    if cfg.provider:
        fields["provider"] = cfg.provider
    if send := {k: v for k in _SEND_KEYS if (v := cfg.value(k)) is not None}:
        fields["send"] = send
    if cfg.route_order:
        fields.setdefault("send", {})["route_order"] = list(cfg.route_order)
    if kwargs := {k: cfg.value(k) for k in _TERMINUS_KEYS if k in cfg}:
        fields["agent_kwargs"] = kwargs
    return fields


def harbor_wire_adapter(
    sample: Sample,
    pipeline_params: PipelineParams,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": sample.query}
    if (cfg := pipeline_params.nodes.get(AGENT_NODE)) is not None:
        payload.update(_agent_fields(cfg))
    return payload


def _refuse_send_faults(
    agent_name: str, model: str | None, provider: str | None, send: Mapping[str, Any]
) -> None:
    """What `_AgentModel` would otherwise meet mid-episode, refused before a cell is bought."""
    where = f"harbor connector: `nodes.{AGENT_NODE}.config`"
    if model is None and provider is None:
        if _AGENTS.get(agent_name, _UNDECLARED).model_seam:
            raise ValueError(
                f"{where} names no model and no provider, so its {agent_name} agent has "
                "nothing to send on."
            )
        return
    if not model or not provider:
        raise ValueError(
            f"{where} names a {'provider' if provider else 'model'} and no "
            f"{'model' if provider else 'provider'}, so its agent has nothing to send on."
        )
    if provider not in providers():
        raise ValueError(f"{where}: no LLM provider {provider!r}; have {sorted(providers())}.")
    if send.get("max_input_tokens") is None:
        raise ValueError(
            f"{where} declares no `max_input_tokens`: terminus-2 summarizes against it, and "
            "nothing else tells it the model's window."
        )
    spec = openai_compat_spec(provider)
    if send.get("route_order") and (spec is None or not spec.gateway):
        raise ValueError(
            f"{where} sets `route_order` on {provider!r}, which is no gateway: it has no host "
            "behind it to route to."
        )


def _sent_spend_bound(
    node: str, cfg: Mapping[str, Any], experiment: Mapping[str, Any] | None
) -> LLMSpendBound | None:
    if node != AGENT_NODE:
        return None
    # What the adapter SENDS, retries excluded: each of those is admitted at its own bound.
    fields = _agent_fields(NodeParams.parse(cfg))
    send, agent = fields.get("send") or {}, fields.get("agent_kwargs") or {}
    _refuse_send_faults(_agent_name(experiment), fields.get("model"), fields.get("provider"), send)
    turns, reply, context = (
        agent.get("max_turns"),
        send.get("max_tokens"),
        send.get("max_input_tokens"),
    )
    if turns is None or reply is None or context is None:
        return None
    summarized = _TERMINUS_SUMMARY_CALLS if agent.get("enable_summarize", True) else 0
    # Our client holds each send at the dearest host: its gateway route falls back, capped.
    return LLMSpendBound(
        kind="llm",
        attempts=int(turns) * (1 + summarized),
        input_tokens=int(context),
        max_tokens=int(reply),
        hosts=None,
    )


def _task_pin(task: Mapping[str, Any]) -> dict[str, Any]:
    """A local `path` enters `Sample.key` as the panel spells it, so it is relative to the panel's
    directory (`InProcessWorkload.experiment_dir`): an absolute one keys a cell to one machine."""
    path = str(task.get("path") or "")
    if (
        path
        and not task.get("git_url")
        and (PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute())
    ):
        raise ValueError(
            f"harbor task {task.get('id')!r}: local path {path!r} is absolute; pin it relative "
            "to the panel that names it."
        )
    return {
        "id": task.get("id"),
        "git_url": task.get("git_url"),
        "git_commit_id": task.get("git_commit_id"),
        "path": task.get("path"),
        "name": task.get("name"),
        "ref": task.get("ref"),
        # Pinned though they live in our file: a corrected gold must not replay old verdicts.
        "question": task.get("question"),
        "answer": task.get("answer"),
    }


def _identity_config(
    _stores: Stores, experiment: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    if experiment is None:
        raise ValueError("harbor connector: no panel on this machine to fingerprint.")
    # Narrow on purpose: a task rides its own sample (`_task_pin`), so a wider panel re-keys none.
    fingerprint = stable_hash(
        [
            experiment.get("agent") or {},
            experiment.get("reward_key") or DEFAULT_TASK_REWARD_KEY,
            _AGENT_SENDER,
        ]
    )
    return {AGENT_NODE: {INSTRUMENT_KEY: fingerprint}}


# The minor series the layout reads were written against: a recorded fact beside what the install
# reports (`RunWiringRecord.backend_revision`), never a gate — `_layout_fault` refuses a moved layout.
EXPECTED_HARBOR_SERIES = "0.22"


async def _version_check(_http: httpx.AsyncClient, _base_url: str) -> str | None:
    try:
        import harbor
    except ImportError:
        return None
    version = str(getattr(harbor, "__version__", "") or "")
    return ".".join(version.split(".")[:2]) if version else None


async def _preflight(_backend_url: str) -> str | None:
    if (encoding := non_utf8_encoding()) is not None:
        return (
            f"this interpreter decodes files as {encoding!r}, not UTF-8, "
            # ASCII only: this is printed to the very console whose encoding it complains about.
            "and Harbor reads its task files without naming an encoding. Every task whose "
            "instruction is not pure Latin-1 would raise before its container is built. Launch "
            "with UTF-8 mode on:\n"
            "  PowerShell:  $env:PYTHONUTF8 = '1'\n"
            "  bash:        export PYTHONUTF8=1\n"
            "  or per-run:  python -X utf8 -m promptpotter ..."
        )

    return await docker_daemon_fault()


def _agent_install_script(tools: tuple[tuple[str, str], ...]) -> str:
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
    # Private API: `DockerEnvironment` builds on every start, resolving `FROM` over the network.
    if environment.task_env_config.docker_image is not None:
        return
    image = environment._env_vars.main_image_name
    if (await docker("image", "inspect", "--format", "{{.Id}}", image))[0] != 0:
        return
    if tools:
        script = _agent_install_script(tools)
        ready = f"{image}:agent-{stable_hash(script)}"
        if (await docker("image", "inspect", "--format", "{{.Id}}", ready))[0] != 0:
            user = (await docker("image", "inspect", "--format", "{{.Config.User}}", image))[1]
            dockerfile = (
                f"FROM {image}\nUSER root\nRUN {json.dumps(['/bin/sh', '-c', script])}\n"
                + (f"USER {user}\n" if user else "")
            )
            # A build argument never reaches the image's environment, where the agent would see it.
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
    # The agent finds `<skills_dir>/<name>/SKILL.md` with a depth-2 `find`: keep the directory.
    skill_dir = root / _SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    body = prompt.strip()
    # terminus-2 SILENTLY skips a skill whose frontmatter lacks `name`/`description` or is not LF.
    (skill_dir / SKILL_FILENAME).write_text(
        f"---\nname: {_SKILL_NAME}\ndescription: {_SKILL_DESCRIPTION}\n---\n\n{body}\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


# Not `views/render/primitives.py::_ANSI_RE`: colour only, and in a layer this one may not import.
_TERMINAL_ESC = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[]()#][0-9A-Za-z]|\x1b.|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)

_AGENT_DECISION_CAP = 1200
_TERMINAL_TAIL_CAP = 600
_VERIFIER_TAIL_CAP = 600
_VERIFIER_FAILURE = re.compile(r"^.*\bFAIL.*$", re.MULTILINE)
_OUTCOME_NOTE_CAP = 300


def _tail(text: str, cap: int) -> str:
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


_TURN_MESSAGE_CAP = 1200
_TURN_OBSERVATION_CAP = 800


def _atif_text(value: object) -> str:
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
    # Never Harbor's `Trajectory` model: it forbids `extra`, so an upstream field would raise.
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    steps = payload.get("steps") if isinstance(payload, dict) else None
    return [s for s in steps or [] if isinstance(s, dict)]


def _trial_root(result: TrialResult) -> Path:
    return _TRIALS_ROOT / str(result.trial_name)


class _StepLogs(NamedTuple):
    """One step's logs where Harbor's own `TrialPaths` puts them; a one-step trial is its own step."""

    step: str | None
    paths: TrialPaths
    # The step raised, so a log missing from it says nothing of the layout.
    failed: bool


def _step_logs(result: TrialResult) -> list[_StepLogs]:
    from harbor.models.trial.paths import TrialPaths

    trial = TrialPaths(_trial_root(result))
    steps = getattr(result, "step_results", None) or []
    if steps:
        return [
            _StepLogs(
                str(sr.step_name),
                TrialPaths(trial.step_dir(str(sr.step_name))),
                sr.exception_info is not None,
            )
            for sr in steps
        ]
    return [_StepLogs(None, trial, result.exception_info is not None)]


def _layout_fault(result: TrialResult, agent_logs: tuple[str, ...]) -> str | None:
    """A SCORED trial lacks Harbor's verifier stdout, or a log its agent declares, only where Harbor's layout moved."""
    for logs in _step_logs(result):
        if not logs.paths.test_stdout_path.is_file():
            return f"no verifier stdout at {logs.paths.test_stdout_path}"
        if logs.failed:
            continue
        for name in agent_logs:
            if not (path := logs.paths.agent_dir / name).is_file():
                return f"no {name} at {path}, which its agent writes every step"
    return None


def _trajectory_sources(result: TrialResult) -> list[tuple[Path, str | None]]:
    return [(logs.paths.agent_dir / _TRAJECTORY, logs.step) for logs in _step_logs(result)]


def _turns(result: TrialResult) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for path, step in _trajectory_sources(result):
        for raw in _read_trajectory(path):
            turns.append(_turn(raw, len(turns) + 1, step))
    return turns


def _skill_opened(result: TrialResult) -> float | None:
    saw_trajectory = False
    for path, _step in _trajectory_sources(result):
        for raw in _read_trajectory(path):
            saw_trajectory = True
            for call in raw.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                # Never a message: `<available_skills>` puts the path in every instruction.
                args = call.get("arguments")
                if SKILL_FILENAME in (json.dumps(args) if args else ""):
                    return 1.0
    return 0.0 if saw_trajectory else None


def _skill_in_first_request(result: TrialResult, prompt: str) -> float | None:
    firsts = [
        steps[0] for path, _step in _trajectory_sources(result) if (steps := _read_trajectory(path))
    ]
    if not firsts:
        return None
    body = prompt.strip()
    return 1.0 if all(body in _atif_text(first.get("message")) for first in firsts) else 0.0


_ANSWER_CAP = 4000


def _answer(result: TrialResult) -> str:
    from harbor.models.task.config import MAIN_SERVICE_NAME
    from harbor.models.trial.paths import EnvironmentPaths, TrialPaths

    source = str(EnvironmentPaths.artifacts_dir / ANSWER_FILENAME)
    answer = ""
    # The trial's own first: a multi-step trial leaves its last answer in its last step.
    for paths in (TrialPaths(_trial_root(result)), *(logs.paths for logs in _step_logs(result))):
        try:
            text = (
                paths.host_artifact_path(MAIN_SERVICE_NAME, source)
                .read_text(encoding="utf-8", errors="replace")
                .strip()
            )
        except OSError:
            continue
        if text:
            answer = text
    return answer[:_ANSWER_CAP]


def _step_rewards(result: TrialResult) -> dict[str, float]:
    out: dict[str, float] = {}
    for sr in getattr(result, "step_results", None) or []:
        name = str(getattr(sr, "step_name", "") or "")
        rewards = getattr(getattr(sr, "verifier_result", None), "rewards", None) or {}
        for key, value in rewards.items():
            # A formula can name only an identifier.
            if (term := f"{name}_{key}").isidentifier():
                out[term] = float(value)
    return out


_PHASES: tuple[str, ...] = ("environment_setup", "agent_setup", "agent_execution", "verifier")


def _phase_timings(result: TrialResult, attempt_s: float) -> dict[str, float]:
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
    return [
        (exc.exception_type, f"{exc.exception_type}: {exc.exception_message}")
        for exc in (result.exception_info, *(sr.exception_info for sr in result.step_results or []))
        if exc is not None
    ]


def _infrastructure_failure(result: TrialResult) -> tuple[str, ErrorCategory] | None:
    for kind, detail in _trial_failures(result):
        # An installed agent's provider speaks only through the harness's words.
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


def _agent_decisions(turns: list[dict[str, Any]]) -> str:
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
        # `n_episodes` is terminus-2's spelling of the turn count.
        for key in ("n_episodes", "finish_reason", "termination_reason", "summarization_count"):
            if (val := meta.get(key)) is not None:
                lines.append(f"{key}={val}")

    # Decisions lead, the verifier closes: `dispatch/bundle.py` trims this to a head and a tail.
    if said := _agent_decisions(turns):
        lines.append(f"\nAGENT DECISIONS:\n{said}")

    # The last step's: the state the verifier closed on. A pane is an agent's own (terminus-2's).
    last = _step_logs(result)[-1].paths
    panes = sorted(last.agent_dir.glob("*.pane"))
    if panes and (pane := _read_tail(panes[0], _TERMINAL_TAIL_CAP)):
        lines.append(f"\nTERMINAL (tail):\n{pane}")
    if verdict := _read_tail(last.test_stdout_path, _VERIFIER_TAIL_CAP):
        lines.append(f"\nVERIFIER (tail):\n{verdict}")
    return "\n".join(lines)


def _outcome_note(result: TrialResult) -> str | None:
    log = _step_logs(result)[-1].paths.test_stdout_path
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    failures = _VERIFIER_FAILURE.findall(_TERMINAL_ESC.sub("", text))
    # The LAST: pytest closes on the line naming why.
    return failures[-1].strip()[:_OUTCOME_NOTE_CAP] if failures else None


def _trial_tokens(result: TrialResult) -> TokenAccount | None:
    # The fourth total is a cost of unstated provenance (provider, CLI or price map): no bill.
    n_input, n_cache, n_output, _ = result.compute_token_cost_totals()
    if n_input is None and n_output is None:
        return None
    return TokenAccount(
        input=int(n_input or 0),
        output=int(n_output or 0),
        cache_read=None if n_cache is None else int(n_cache),
    )


def _step_tokens(
    usage: TokenAccount | None, cost_usd: float | None, episode: _Episode
) -> dict[str, StepUsage]:
    if usage is None and cost_usd is None:
        return {}
    usage = usage or TokenAccount()
    return {
        AGENT_NODE: StepUsage(
            input=usage.input,
            output=usage.output,
            cache_read=usage.cache_read,
            cost_usd=cost_usd,
            rate_priced_usd=rate_priced_usd(
                usage, model=episode.model, provider=episode.provider, cost_usd=cost_usd
            ),
            model=episode.model,
            provider=episode.provider,
        )
    }


def _sum_spend(parts: list[dict[str, StepUsage]]) -> dict[str, dict[str, object]]:
    # One unpriced attempt makes the sum unpriced, never a smaller price.
    entries = [p[AGENT_NODE] for p in parts if AGENT_NODE in p]
    if not entries:
        return {}
    reads = [e.cache_read for e in entries if e.cache_read is not None]
    reported = [e.cost_usd for e in entries if e.cost_usd is not None]
    priced = [e.rate_priced_usd for e in entries if e.rate_priced_usd is not None]
    all_reported = len(reported) == len(entries)
    total = StepUsage(
        input=sum(e.input for e in entries),
        output=sum(e.output for e in entries),
        cache_read=sum(reads) if reads else None,
        cost_usd=sum(reported) if all_reported else None,
        rate_priced_usd=(
            sum(reported) + sum(priced)
            if not all_reported and len(reported) + len(priced) == len(entries)
            else None
        ),
        model=entries[-1].model,
        provider=entries[-1].provider,
    )
    return {AGENT_NODE: total.wire()}


def _task_config(task: dict[str, Any], task_root: Path, harbor_config: ModuleType) -> Any:
    if name := task.get("name"):
        return harbor_config.TaskConfig(name=name, ref=task.get("ref"))
    path = task.get("path")
    if not path:
        raise ValueError(
            f"harbor task {task.get('id')!r} declares neither 'name' (a registry package) "
            "nor 'path' (a task directory) in the panel."
        )
    return harbor_config.TaskConfig(
        # A git task's path is inside its repository; a local one, inside the panel's directory.
        path=Path(path) if task.get("git_url") else task_root / path,
        git_url=task.get("git_url"),
        git_commit_id=task.get("git_commit_id"),
    )


@dataclass(frozen=True)
class _Episode:
    query: str
    task: dict[str, Any]
    task_root: Path
    reward_key: str
    agent_name: str
    agent_kwargs: dict[str, Any]
    model: str | None
    provider: str | None
    # What `_AgentModel` puts on each send (`_SEND_KEYS`, `route_order`).
    send: dict[str, Any]
    declared: _AgentDeclares | None
    environment: str
    cached: bool
    prompt: str | None
    in_system_prompt: bool
    tools: tuple[tuple[str, str], ...]
    agent_logs: tuple[str, ...]

    @property
    def harbor_model_name(self) -> str | None:
        # Harbor reads the model's PREFIX as the provider: ours sent raw asks the wrong host.
        if self.model and self.provider and not self.model.startswith(f"{self.provider}/"):
            return f"{self.provider}/{self.model}"
        return self.model


def _panel_dir(workload: InProcessWorkload) -> Path:
    if workload.experiment_dir is None:
        raise CellUnscoreableError("a harbor cell ran with no panel directory to resolve", spent={})
    return workload.experiment_dir


def _episode(workload: InProcessWorkload, sample: Sample, payload: dict[str, Any]) -> _Episode:
    task = sample.source_pin
    if task is None:
        raise CellUnscoreableError(
            f"harbor task {sample.query!r} carries no pin, so it is no row of a panel.",
            spent={},
        )
    panel = workload.experiment or {}
    agent_cfg = panel.get("agent") or {}
    agent_kwargs = dict(agent_cfg.get("kwargs") or {})
    agent_kwargs.update(payload.get("agent_kwargs") or {})
    agent_name = _agent_name(panel)
    declared = _AGENTS.get(agent_name)
    tools, logs = (declared or _UNDECLARED).tools, (declared or _UNDECLARED).logs
    return _Episode(
        query=sample.query,
        task=task,
        task_root=_panel_dir(workload),
        reward_key=panel.get("reward_key") or DEFAULT_TASK_REWARD_KEY,
        agent_name=agent_name,
        agent_kwargs=agent_kwargs,
        # From the node config, never the panel's agent block (`datasets/CLAUDE.md`).
        model=payload.get("model"),
        provider=payload.get("provider"),
        send=dict(payload.get("send") or {}),
        declared=declared,
        environment=agent_cfg.get("environment") or "docker",
        cached=panel.get(PACKAGE_CACHE_KEY) in PACKAGE_CACHE_SCOPES,
        prompt=payload.get("prompt"),
        in_system_prompt=payload[SKILL_DELIVERY_KEY] == "system_prompt",
        # terminus-2 installs asciinema only while it records; baking it otherwise adds a tool.
        tools=tuple(
            (tool, probe)
            for tool, probe in tools
            if tool != "asciinema" or agent_kwargs.get("record_terminal_session") is not False
        ),
        agent_logs=logs,
    )


def _trial_config(episode: _Episode, skills: list[str]) -> Any:
    from harbor.models.trial import config as harbor_config

    os.environ[_PRODUCER_ENV] = CONTAINER_PRODUCER
    return harbor_config.TrialConfig(
        task=_task_config(episode.task, episode.task_root, harbor_config),
        trials_dir=_TRIALS_ROOT,
        agent=harbor_config.AgentConfig(
            name=episode.agent_name,
            model_name=episode.harbor_model_name,
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
    # Private API: the template is read at construction, so it is replaced on the built agent.
    template = getattr(trial.agent, "_prompt_template", None)
    if not isinstance(template, str):
        raise RuntimeError(
            f"harbor connector: agent {agent_name!r} has no prompt template, so "
            f"`{SKILL_DELIVERY_KEY}: system_prompt` cannot reach its model."
        )
    trial.agent._prompt_template = _system_skill_template(template, prompt)


def _install_model(trial: Trial, agent_name: str, model: _AgentModel) -> None:
    # Private API, as `_install_system_skill`: `run` builds its `Chat` over `_llm`, after this.
    if not hasattr(trial.agent, "_llm"):
        raise RuntimeError(
            f"harbor connector: agent {agent_name!r} holds no `_llm`, so its sends cannot be ours."
        )
    trial.agent._llm = model


def _chat_message(turn: Mapping[str, Any]) -> ChatMessage:
    role = turn.get("role")
    if role not in ("system", "user", "assistant"):
        raise ValueError(f"harbor connector: the agent's history holds a {role!r} turn.")
    return {"role": role, "content": str(turn.get("content") or "")}


class _AgentModel:
    """terminus-2's model (``agent._llm``): each of its sends goes out on the registry's client,
    admitted, paced and billed there, on the box's key (``CONNECTOR.cell_payer``)."""

    def __init__(self, model: str | None, provider: str | None, send: Mapping[str, Any]) -> None:
        assert model and provider, "refused at resolve (`_refuse_send_faults`)"
        payer = CONNECTOR.cell_payer
        if payer != "host":
            raise RuntimeError(
                f"harbor connector: its cells draw the {payer!r} wallet, and only the box's "
                "keys are in a cell's reach."
            )
        key = host_key(provider)
        if key is None:
            raise NoApiKeyError(provider=provider, key=host_key_attr(provider))
        self._client = get_llm_client(provider, key)
        self._model, self._provider, self._send = model, provider, dict(send)
        self.usage = TokenAccount()
        self._answered = 0
        self._cost: float | None = 0.0
        # A refusal is raised again on every later call: terminus-2 retries a failed query, and
        # would answer around a send we refused.
        self._ended: SendRefusedError | None = None
        # The last send's fault no answer has followed: terminus-2 retries it, and a trial
        # ending on it never measured the prompt.
        self._fault: Exception | None = None

    async def call(
        self,
        prompt: str,
        message_history: Sequence[Mapping[str, Any]] = (),
        # `Chat`'s log path and Responses-API chain id: this wire reads neither, and a panel's
        # `llm_call_kwargs` are refused before a trial (`_DISCARDED_SENDER_KEYS`).
        **_chat: object,
    ) -> Any:
        from harbor.llms.base import (
            ContextLengthExceededError,
            LLMResponse,
            OutputLengthExceededError,
        )
        from harbor.models.metric.usage_info import UsageInfo

        if self._ended is not None:
            raise self._ended
        send = self._send
        request = ChatRequest(
            [*map(_chat_message, message_history), {"role": "user", "content": prompt}],
            self._model,
            temperature=send.get("temperature"),
            max_tokens=send.get("max_tokens"),
            reasoning_effort=send.get("reasoning_effort"),
            top_p=send.get("top_p"),
            route_order=send.get("route_order"),
        )
        try:
            reply = await self._client.chat(request, label=_AGENT_SEND)
        except ContextOverflowError as exc:
            # terminus-2 summarizes its history on this, and resends.
            raise ContextLengthExceededError(str(exc)) from exc
        except Exception as exc:
            if failed_send(exc).may_have_billed:
                self._cost = None
            if isinstance(exc, SendRefusedError):
                self._ended = exc
            else:
                self._fault = exc
            raise
        self._fault = None
        self._answered += 1
        self.usage += reply.usage
        if self._cost is not None:
            self._cost = None if reply.cost_usd is None else self._cost + reply.cost_usd
        if reply.finish_reason == "length":
            raise OutputLengthExceededError(
                f"{self._model} stopped at its max_tokens", truncated_response=reply.content
            )
        return LLMResponse(
            content=reply.content,
            reasoning_content=reply.reasoning or None,
            model_name=reply.model,
            # Harbor's own tally, which no bill reads: an unreported cost is its zero.
            usage=UsageInfo(
                prompt_tokens=reply.usage.input,
                completion_tokens=reply.usage.output,
                cache_tokens=reply.usage.cache_read or 0,
                cost_usd=reply.cost_usd or 0.0,
            ),
        )

    def get_model_context_limit(self) -> int:
        return int(self._send["max_input_tokens"])

    def get_model_output_limit(self) -> int | None:
        reply = self._send.get("max_tokens")
        return None if reply is None else int(reply)

    @property
    def spent(self) -> TokenAccount | None:
        return self.usage if self._answered else None

    @property
    def cost_usd(self) -> float | None:
        """``None`` once any send that may have billed reported no cost: never a smaller price."""
        return self._cost if self._answered else None

    def failure(self, result: TrialResult) -> tuple[str, ErrorCategory] | None:
        """What ended the episode that was never the prompt's doing; our own refusal raises."""
        if self._ended is not None:
            outcome = failed_send(self._ended)
            if outcome.failure not in _NOT_THE_PROMPT:
                raise self._ended
            return outcome.detail[:300], outcome.failure
        if self._fault is not None and (
            (outcome := failed_send(self._fault)).failure in _NOT_THE_PROMPT
        ):
            return outcome.detail[:300], outcome.failure
        if self._client.pushback(self._model) is not None and any(
            kind == "AgentTimeoutError" for kind, _ in _trial_failures(result)
        ):
            return (
                f"the agent ran out of time while {self._provider} held its sends",
                ErrorCategory.PROVIDER_THROTTLED,
            )
        return None


async def _installed_run(trial: Trial, episode: _Episode) -> TrialResult:
    """An installed agent sends from its container: one send here, settled on Harbor's token read."""
    model, provider = episode.model, episode.provider
    with admitted(
        _AGENT_SEND,
        reservation_left(),
        model=model,
        provider=provider,
        paid_by=CONNECTOR.cell_payer,
    ) as admission:
        try:
            result = await trial.run()
        except Exception as exc:
            admission.close(failed_send(exc))
            raise
        usage = _trial_tokens(result)
        admission.close(
            answered(None if usage is None else Billed(usage, None, model=model, provider=provider))
        )
    return result


class _Ran(NamedTuple):
    result: TrialResult
    elapsed_s: float
    usage: TokenAccount | None
    cost_usd: float | None
    failure: tuple[str, ErrorCategory] | None


async def _attempt(episode: _Episode) -> _Ran:
    from harbor.agents.installed.base import BaseInstalledAgent
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.trial.trial import Trial

    prompt, in_system_prompt = episode.prompt, episode.in_system_prompt
    seamed = episode.declared is not None and episode.declared.model_seam
    model = _AgentModel(episode.model, episode.provider, episode.send) if seamed else None
    if episode.cached:
        with machine_step():
            await ensure_package_cache()
    with tempfile.TemporaryDirectory(prefix="pp-skill-") as skill_root:
        skills = (
            [str(_write_skill(Path(skill_root), prompt))] if prompt and not in_system_prompt else []
        )
        # Before `Trial.create`: it builds or pulls the image, which is part of the cell's cost.
        start = time.monotonic()
        trial = await Trial.create(_trial_config(episode, skills))
        installed = isinstance(trial.agent, BaseInstalledAgent)
        if model is not None:
            _install_model(trial, episode.agent_name, model)
        elif episode.declared is None and not installed:
            raise ValueError(
                f"harbor connector: agent {episode.agent_name!r} runs in this process and is "
                "undeclared (`_AGENTS`), so its sends would reach its provider unadmitted."
            )
        if prompt and in_system_prompt:
            _install_system_skill(trial, episode.agent_name, prompt)
        if isinstance(trial.agent_environment, DockerEnvironment):
            with machine_step():
                await _reuse_task_image(
                    trial.agent_environment, episode.tools, proxied=episode.cached
                )
        if installed:
            result = await _installed_run(trial, episode)
            return _Ran(result, time.monotonic() - start, _trial_tokens(result), None, None)
        result = await trial.run()
        elapsed = time.monotonic() - start
        if model is None:
            return _Ran(result, elapsed, None, None, None)
        return _Ran(result, elapsed, model.spent, model.cost_usd, model.failure(result))


async def _back_off(
    query: str, budget: SendBudget, wait_s: float, cause: str, category: ErrorCategory
) -> None:
    logger.warning(
        "harbor task %r attempt %d/%d measured the infrastructure (%s); retrying in %.0fs",
        query,
        budget.resent,
        budget.attempts,
        cause,
        wait_s,
    )
    # On the ledger too: a run the API server hosts has no console.
    emit_backend_warning(
        kind="infrastructure",
        attempt=budget.resent,
        max_attempts=budget.attempts,
        wait_s=wait_s,
        query=query,
        detail=cause,
        error_class=category.value,
    )
    await asyncio.sleep(wait_s)


async def _run_episode(
    episode: _Episode,
) -> tuple[TrialResult, float, dict[str, dict[str, object]]]:
    query = episode.query
    # Discarded attempts included: a retried episode still ran the agent.
    attempts: list[dict[str, StepUsage]] = []
    budget = drawn_budget()
    while True:
        try:
            ran = await _attempt(episode)
        except CellInfrastructureError as exc:
            cause, category = str(exc), exc.category
        else:
            result, elapsed = ran.result, ran.elapsed_s
            attempts.append(_step_tokens(ran.usage, ran.cost_usd, episode))
            if (failure := ran.failure or _infrastructure_failure(result)) is None:
                break
            cause, category = failure
        forget_package_cache()
        wait = (
            budget.resend_wait(base_s=_INFRA_BACKOFF_S)
            if category is ErrorCategory.CONNECTION
            else None
        )
        if wait is None:
            raise cell_failure(
                f"harbor task {query!r} measured the infrastructure, not the agent, "
                f"on attempt {budget.attempt}/{budget.attempts}: {cause}",
                category,
                spent=_sum_spend(attempts),
            )
        await _back_off(query, budget, wait, cause, category)
    return result, elapsed, _sum_spend(attempts)


def _reward(
    result: TrialResult, episode: _Episode, bill: dict[str, dict[str, object]]
) -> float | int:
    query = episode.query
    if unscoreable := _unscoreable_step(result):
        raise CellUnscoreableError(f"harbor task {query!r}: {unscoreable}.", spent=bill)

    rewards = result.verifier_result.rewards if result.verifier_result else None
    reward = (rewards or {}).get(episode.reward_key)
    if reward is None:
        raise CellUnscoreableError(
            f"harbor task {query!r} produced no reward under key {episode.reward_key!r} "
            f"(rewards={rewards}); the episode is unscoreable, not a zero.",
            spent=bill,
        )
    return cast("float | int", reward)


def _skill_arrival(result: TrialResult, episode: _Episode) -> float | None:
    prompt, in_system_prompt = episode.prompt, episode.in_system_prompt
    # No prompt, no artifact: `0.0` would report declining a file that was never written.
    opened = (
        None
        if not prompt
        else _skill_in_first_request(result, prompt)
        if in_system_prompt
        else _skill_opened(result)
    )
    if opened is not None and not opened:
        # A warning, not a discount: the term is constant when healthy, uniform across arms if not.
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
    bill: dict[str, dict[str, object]],
) -> dict[str, Any]:
    turns = _turns(result)
    data: dict[str, Any] = {
        REWARD_KEY: float(reward),
        "terminal_node": AGENT_NODE,
        "step_tokens": bill,
        "reasoning_trace": _digest(result, episode.query, reward, turns),
        ANSWER_KEY: _answer(result),
    }
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
    episode = _episode(workload, sample, payload)
    result, attempt_s, bill = await _run_episode(episode)
    reward = _reward(result, episode, bill)
    if (fault := _layout_fault(result, episode.agent_logs)) is not None:
        raise cell_failure(
            f"harbor task {episode.query!r} scored with {fault}: Harbor's trial layout moved "
            f"under a connector written against series {EXPECTED_HARBOR_SERIES}, and the digest "
            "and the skill arrival read it.",
            ErrorCategory.PIPELINE,
            spent=bill,
        )
    return {"data": _episode_data(result, episode, reward, attempt_s, bill)}


CONNECTOR = Connector(
    name="harbor",
    execution="in_process",
    wire_adapter=harbor_wire_adapter,
    cell_envelope_s=cell_envelope_of(_CELL_ENVELOPE_S),
    sent_spend_bound=_sent_spend_bound,
    holds_own_sends=True,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    extra=Extra.HARBOR,
    preflight=_preflight,
    expected_revision=EXPECTED_HARBOR_SERIES,
    version_check=_version_check,
    identity_config=_identity_config,
    resolve_experiment=_resolve_experiment,
    measured_unit="cell",
    prompt_delivery=_prompt_delivery,
    # Each cell holds a container: the ceiling is the MACHINE's, shared by every run on it.
    max_cells_in_flight=5,
    cell_attempts=_CELL_ATTEMPTS,
    cells_hold_the_machine=True,
    compose_overlay=_DOCKER_OVERLAY,
    package_cache_scopes=PACKAGE_CACHE_SCOPES,
    # Per-step rewards are not required: a single-step task emits none.
    required_observation_keys=(REWARD_KEY, SKILL_KEY),
    answer_key=ANSWER_KEY,
)
