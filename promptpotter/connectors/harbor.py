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
from promptpotter.connectors.protocol import (
    PACKAGE_CACHE_KEY,
    Connector,
    InProcessWorkload,
)
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.pipeline_overlay import node_config_items
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
from promptpotter.infrastructure.llm.litellm_sends import litellm_sends_billed_as
from promptpotter.infrastructure.llm.openai_compat import cell_gateway_body, sent_effort
from promptpotter.infrastructure.llm.registry import openai_compat_spec
from promptpotter.infrastructure.llm.send_failure import failed_send
from promptpotter.infrastructure.llm.send_pacing import SendBudget, drawn_budget
from promptpotter.infrastructure.llm.spend_book import (
    Billed,
    CallLabel,
    admitted,
    answered,
    reservation_left,
)
from promptpotter.infrastructure.llm.telemetry import emit_backend_warning, rate_priced_usd
from promptpotter.infrastructure.tls import tls_context
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

# Harbor's agents import litellm, which otherwise fetches its price map over the network at import.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


AGENT_NODE = "agent"
_AGENT_SEND = CallLabel(AGENT_NODE, "backend")

REWARD_KEY = "env_reward"
# Harbor parses a task's bare `reward.txt` into `{"reward": x}`.
DEFAULT_TASK_REWARD_KEY = "reward"

ANSWER_KEY = "agent_answer"
ANSWER_FILENAME = "answer.txt"

SKILL_KEY = "skill_opened"

TASKS_FILE = "harbor_tasks.yaml"

INSTRUMENT_KEY = "harbor_instrument"

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
        "store_all_messages",
    }
)

_TRIALS_ROOT = PRODUCER_SCRATCH
# Read by the compose overlay, which interpolates it into the producer label.
_PRODUCER_ENV = "PROMPTPOTTER_HARBOR_PRODUCER"

_DOCKER_OVERLAY = Path(__file__).parent / "resources" / "harbor-docker-compose.yaml"

# Verifier only: a proxy in the agent's environment is observable by the agent.
PACKAGE_CACHE_SCOPES = frozenset({"verifier"})

# terminus-2 declares no `SYSTEM_PACKAGES`; this mirrors `TmuxSession._install_recording_tools`.
_AGENT_TOOLS: dict[str, tuple[tuple[str, str], ...]] = {
    "terminus-2": (("tmux", "tmux -V"), ("asciinema", "asciinema --version")),
}
# `TmuxSession._detect_system_info`'s probe order, which decides the install command it runs.
_PACKAGE_MANAGERS = ("apt-get", "dnf", "yum", "apk", "pacman", "brew", "pkg", "zypper")

_CELL_ATTEMPTS = 3
_INFRA_BACKOFF_S = 45.0

# A terminus-2 summarize pass is three calls (`_summarize`).
_TERMINUS_SUMMARY_CALLS = 3
_REGISTRY_ATTEMPTS = 3
_REGISTRY_BACKOFF_S = 2.0
_REGISTRY_TIMEOUT_S = 30.0

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
_HARNESS_TIMEOUTS = frozenset(
    {"EnvironmentStartTimeoutError", "AgentSetupTimeoutError", "VerifierTimeoutError"}
)
_PROVIDER_THROTTLE = re.compile(r"\bRateLimitError\b")

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


def _prompt_delivery(pipeline_params: dict[str, Any] | None) -> Delivery:
    cfg = dict(node_config_items(pipeline_params)).get(AGENT_NODE, {})
    return "request" if _skill_delivery(cfg) == "system_prompt" else "artifact_body"


def _system_skill_template(template: str, prompt: str) -> str:
    block = f'<skill name="{_SKILL_NAME}">\n{prompt.strip()}\n</skill>\n\n'
    # terminus-2 fills the template with `str.format`, so the skill's own braces are doubled.
    return block.replace("{", "{{").replace("}", "}}") + template


@functools.cache
def _registry_tasks(dataset: str, version: str) -> list[dict[str, Any]]:
    from harbor.constants import DEFAULT_REGISTRY_URL
    from harbor.models.registry import DatasetSpec

    # Not `JsonRegistryClient`: its `requests.get` takes no timeout.
    budget = SendBudget(None, attempts=_REGISTRY_ATTEMPTS)
    while True:
        try:
            response = httpx.get(
                DEFAULT_REGISTRY_URL,
                timeout=_REGISTRY_TIMEOUT_S,
                follow_redirects=True,
                verify=tls_context(),
            )
            response.raise_for_status()
            break
        except httpx.HTTPError as exc:
            wait = budget.resend_wait(base_s=_REGISTRY_BACKOFF_S)
            if wait is None:
                raise BackendUnreachableError(
                    "harbor",
                    DEFAULT_REGISTRY_URL,
                    f"registry fetch failed {budget.attempts} times: {exc}",
                ) from exc
            logger.warning(
                "harbor registry fetch %d/%d failed: %s", budget.resent, budget.attempts, exc
            )
            time.sleep(wait)

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
        if question := str(t.get("question") or "").strip():
            row["question"] = question
        out.append(row)
    return out


# Hashed into every cell's identity (`_identity_config`): respelling it re-keys every banked cell.
_REASONING_CHANNEL = "openrouter:extra_body.reasoning"


def harbor_wire_adapter(
    query: str,
    pipeline_params: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query}
    for node, cfg in node_config_items(pipeline_params):
        if node != AGENT_NODE:
            continue
        payload[SKILL_DELIVERY_KEY] = _skill_delivery(cfg)
        if prompt := cfg.get("prompt"):
            payload["prompt"] = prompt
        if model := cfg.get("model"):
            payload["model"] = model
        via = str(cfg.get("provider") or "")
        if via:
            payload["provider"] = via
        kwargs = {k: v for k, v in cfg.items() if k in AGENT_KWARG_KEYS}
        effort = sent_effort(kwargs.pop("reasoning_effort", None))
        body = cell_gateway_body(
            openai_compat_spec(via),
            via,
            route_order=cfg.get("route_order"),
            reasoning_effort=effort,
        )
        call_kwargs: dict[str, Any] = {}
        if body is not None:
            # litellm's `drop_params` drops `reasoning_effort` on a model it does not list.
            call_kwargs["extra_body"] = body
        elif effort is not None:
            kwargs["reasoning_effort"] = effort
        if (reply := cfg.get("max_tokens")) is not None:
            call_kwargs["max_tokens"] = int(reply)
        if call_kwargs:
            kwargs["llm_call_kwargs"] = call_kwargs
        if (context := cfg.get("max_input_tokens")) is not None:
            # Unsent, terminus-2 assumes the whole window: 1M tokens for a model litellm lacks.
            kwargs["model_info"] = {"max_input_tokens": int(context)}
        if kwargs:
            payload["agent_kwargs"] = kwargs
    return payload


def _sent_spend_bound(node: str, cfg: Mapping[str, Any]) -> LLMSpendBound | None:
    if node != AGENT_NODE:
        return None
    # What the adapter SENDS, retries excluded: each of those is admitted at its own bound.
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
        input_tokens=context,
        max_tokens=reply,
        hosts=None if route.get("allow_fallbacks", True) else tuple(route["order"]),
    )


def _task_pin(task: Mapping[str, Any]) -> dict[str, Any]:
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
    _stores: Stores, _dataset_dir: Path, experiment: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    if experiment is None:
        raise ValueError(f"harbor connector: no {TASKS_FILE} on this machine to fingerprint.")
    # Narrow on purpose: a task rides its own sample (`_task_pin`), so a wider panel re-keys none.
    fingerprint = stable_hash(
        [
            experiment.get("agent") or {},
            experiment.get("reward_key") or DEFAULT_TASK_REWARD_KEY,
            _REASONING_CHANNEL,
        ]
    )
    return {AGENT_NODE: {INSTRUMENT_KEY: fingerprint}}


# A minor series, not a version: a patch release moves no layout `_digest` reads.
EXPECTED_HARBOR_SERIES = "0.22"


async def _version_check(_http: httpx.AsyncClient, _base_url: str) -> str | None:
    try:
        import harbor
    except ImportError:
        return None
    version = str(getattr(harbor, "__version__", "") or "")
    return ".".join(version.split(".")[:2]) if version else None


async def _preflight(_backend_url: str) -> str | None:
    try:
        from harbor.trial.trial import Trial  # noqa: F401
    except ImportError:
        return (
            f"the 'harbor' extra is not importable from {sys.executable}.\n"
            f"  If that is not this repo's .venv, re-run with the venv's interpreter:\n"
            f"    .venv\\Scripts\\python.exe -m promptpotter ...\n"
            f'  If it IS the venv, install the extra: pip install -e ".[harbor]"'
        )

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


def _trajectory_sources(result: TrialResult) -> list[tuple[Path, str | None]]:
    root = _trial_root(result)
    steps = getattr(result, "step_results", None) or []
    if steps:
        return [
            (root / "steps" / str(sr.step_name) / "agent" / "trajectory.json", str(sr.step_name))
            for sr in steps
        ]
    return [(root / "agent" / "trajectory.json", None)]


def _turns(result: TrialResult) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for path, step in _trajectory_sources(result):
        for raw in _read_trajectory(path):
            turns.append(_turn(raw, len(turns) + 1, step))
    if not turns:
        _warn_layout_drift(f"no agent trajectory under {_trial_root(result)}")
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


def _provider_throttle(result: TrialResult) -> str | None:
    failures = _trial_failures(result)
    for _, detail in failures:
        if _PROVIDER_THROTTLE.search(detail):
            return detail
    if any(kind == "AgentTimeoutError" for kind, _ in failures):
        return _first_line(_trial_root(result) / "trial.log", _PROVIDER_THROTTLE)
    return None


def _infrastructure_failure(result: TrialResult) -> tuple[str, ErrorCategory] | None:
    for kind, detail in _trial_failures(result):
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


_LAYOUT_WARNED: set[str] = set()


def _warn_layout_drift(what: str) -> None:
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
    log = _trial_root(result) / "verifier" / "test-stdout.txt"
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
    result: TrialResult, episode: _Episode, cost_usd: float | None
) -> dict[str, StepUsage]:
    usage = _trial_tokens(result)
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


def _task_config(task: dict[str, Any], harbor_config: ModuleType) -> Any:
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
    model: str | None
    provider: str | None
    environment: str
    cached: bool
    prompt: str | None
    in_system_prompt: bool
    tools: tuple[tuple[str, str], ...]

    @property
    def harbor_model_name(self) -> str | None:
        # Harbor reads the model's PREFIX as the provider: ours sent raw asks the wrong host.
        if self.model and self.provider and not self.model.startswith(f"{self.provider}/"):
            return f"{self.provider}/{self.model}"
        return self.model


def _episode(workload: InProcessWorkload, sample: Sample, payload: dict[str, Any]) -> _Episode:
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
        # From the node config, never the panel's agent block (`datasets/CLAUDE.md`).
        model=payload.get("model"),
        provider=payload.get("provider"),
        environment=agent_cfg.get("environment") or "docker",
        cached=panel.get(PACKAGE_CACHE_KEY) in PACKAGE_CACHE_SCOPES,
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


async def _billed_run(trial: Trial, episode: _Episode) -> tuple[TrialResult, float | None]:
    from harbor.agents.installed.base import BaseInstalledAgent

    model, provider = episode.model, episode.provider
    if not isinstance(trial.agent, BaseInstalledAgent):
        # Its sends go through litellm in this process, each billed as it is made.
        with litellm_sends_billed_as(_AGENT_SEND, model=model, provider=provider) as reported:
            result = await trial.run()
        return result, reported.usd
    # An installed agent sends from its container: one send here, settled on Harbor's token read.
    with admitted(_AGENT_SEND, reservation_left(), model=model, provider=provider) as admission:
        try:
            result = await trial.run()
        except Exception as exc:
            admission.close(failed_send(exc))
            raise
        usage = _trial_tokens(result)
        admission.close(
            answered(None if usage is None else Billed(usage, None, model=model, provider=provider))
        )
    return result, None


async def _attempt(episode: _Episode) -> tuple[TrialResult, float, float | None]:
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
        # Before `Trial.create`: it builds or pulls the image, which is part of the cell's cost.
        start = time.monotonic()
        trial = await Trial.create(_trial_config(episode, skills))
        if prompt and in_system_prompt:
            _install_system_skill(trial, episode.agent_name, prompt)
        if isinstance(trial.agent_environment, DockerEnvironment):
            with machine_step():
                await _reuse_task_image(
                    trial.agent_environment, episode.tools, proxied=episode.cached
                )
        result, cost_usd = await _billed_run(trial, episode)
        return result, time.monotonic() - start, cost_usd


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
            result, elapsed, cost_usd = await _attempt(episode)
        except CellInfrastructureError as exc:
            cause, category = str(exc), exc.category
        else:
            attempts.append(_step_tokens(result, episode, cost_usd))
            if (throttle := _provider_throttle(result)) is not None:
                raise CellThrottledError(
                    f"harbor task {query!r} was throttled by its model provider: {throttle}",
                    spent=_sum_spend(attempts),
                )
            if (failure := _infrastructure_failure(result)) is None:
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
    return {"data": _episode_data(result, episode, reward, attempt_s, bill)}


CONNECTOR = Connector(
    name="harbor",
    execution="in_process",
    wire_adapter=harbor_wire_adapter,
    sent_spend_bound=_sent_spend_bound,
    holds_own_sends=True,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
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
    experiment_file=TASKS_FILE,
    default_pipeline=(AGENT_NODE,),
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
