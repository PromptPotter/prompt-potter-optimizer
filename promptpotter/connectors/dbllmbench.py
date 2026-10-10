from __future__ import annotations

import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import yaml

from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import Connector, InProcessWorkload
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import LLMSpendBound
from promptpotter.domain.spend import StepUsage
from promptpotter.infrastructure.docker_host import (
    PRODUCER_SCRATCH,
    docker,
    docker_daemon_fault,
    machine_step,
    run_cell_container,
)
from promptpotter.infrastructure.llm.openai_compat import cell_gateway_body, sent_effort
from promptpotter.infrastructure.llm.registry import openai_compat_spec
from promptpotter.infrastructure.store.io import rmtree_robust
from promptpotter.shared.errors import (
    CellInfrastructureError,
    CellThrottledError,
    CellUnscoreableError,
    ErrorCategory,
    cell_failure,
    is_provider_credit_refusal,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import TurnRecord

logger = logging.getLogger(__name__)


WRITER_NODE = "query_writer"

QUESTIONS_FILE = "questions.json"

RETRY_LEVELS = (0, 2, 4)


def retry_levels(cfg: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(sorted({0, *(int(level) for level in cfg.get("retry_levels") or RETRY_LEVELS)}))


# Upstream's hardcoded count (`src/runner/src/lib.rs::REPETITIONS`).
REPETITIONS = 3


def repetitions(cfg: Mapping[str, Any]) -> int:
    # Fewer needs an image built with `resources/dbllmbench-repetitions.patch`: upstream runs three.
    return int(cfg.get("repetitions") or REPETITIONS)


ANSWER_KEY = "generated_query"
RETRIES_KEY = "retries_used"
VISIBLE_ERROR_KEY = "visible_error_r0"
SILENT_WRONG_KEY = "silent_wrong_r0"


def accuracy_key(level: int) -> str:
    return f"accuracy_r{level}"


_DATA_ROOT = "/opt/db-llm-bench/data"
_WORK = "/work"
_KEY_ENV = "DBLLMBENCH_API_KEY"
_ANTHROPIC = "anthropic"
_OPENAI_BASE_URL = "https://api.openai.com/v1"

# Upstream's own compose stack, reached from inside the runner's container.
_DB_DEFAULT_URLS = {
    "typedb": "http://host.docker.internal:1729",
    "neo4j": "bolt://host.docker.internal:7687",
}
_DB_DEFAULT_USERS = {"typedb": "admin", "neo4j": "neo4j"}

_CELL_WAIT_S = 3600.0
_NOTE_CAP = 300
_TURN_CAP = 1200

# A tag is only a name: a rebuild under an old one would replay every cell banked on the old build.
_COMMIT_LABEL = "org.promptpotter.db-llm-bench.commit"
_IMAGES_CHECKED: set[str] = set()


def _extract_experiment(experiment_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "query": q["question"],
            "ground_truth": None,
            # Pinned: a corrected expected value must not replay old verdicts.
            "source_pin": {
                "expected": q.get("expected"),
                "expected_by_db": q.get("expected_by_db"),
                "ordered": bool(q.get("ordered")),
                "unanswerable": bool(q.get("unanswerable")),
            },
        }
        for q in experiment_data["questions"]
    ]


def _upstream_question(workload: InProcessWorkload, sample: Sample) -> dict[str, Any]:
    questions = (workload.experiment or {}).get("questions") or []
    if sample.id >= len(questions) or questions[sample.id].get("question") != sample.query:
        raise CellUnscoreableError(
            f"dbllmbench question {sample.query[:80]!r} is not row {sample.id} of the "
            f"{QUESTIONS_FILE} this run opened with.",
            spent={},
        )
    return dict(questions[sample.id])


def dbllmbench_wire_adapter(query: str, pipeline_params: dict[str, Any] | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query}
    for node, cfg in node_config_items(pipeline_params):
        if node != WRITER_NODE:
            continue
        if prompt := cfg.get("prompt"):
            payload["prompt"] = prompt
        payload["config"] = {k: v for k, v in cfg.items() if k != "prompt"}
    return payload


def _sent_spend_bound(node: str, cfg: Mapping[str, Any]) -> LLMSpendBound | None:
    if node != WRITER_NODE:
        return None
    context, reply = cfg.get("max_input_tokens"), cfg.get("max_tokens")
    if context is None or reply is None:
        return None
    route = cfg.get("route_order")
    return LLMSpendBound(
        kind="llm",
        attempts=repetitions(cfg) * (max(retry_levels(cfg)) + 1),
        input_tokens=int(context),
        max_tokens=int(reply),
        hosts=tuple(route) if route else None,
    )


def _model_entry(cfg: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    # The key rides the container's environment, never the config file: that is scratch on disk.
    model, provider = cfg.get("model"), cfg.get("provider")
    if not isinstance(model, str) or not isinstance(provider, str):
        raise RuntimeError(
            f"dbllmbench connector: nodes.{WRITER_NODE}.config must name a `model` and a "
            "`provider`."
        )
    reply = int(cfg.get("max_tokens") or 8192)
    effort = sent_effort(cfg.get("reasoning_effort"))
    spec = openai_compat_spec(provider)
    body = cell_gateway_body(
        spec, provider, route_order=cfg.get("route_order"), reasoning_effort=effort
    )
    if provider == _ANTHROPIC:
        claude: dict[str, Any] = {"model": model, "max_tokens": reply}
        if effort is not None:
            claude["effort"] = effort
        return {"claude": claude}, {"ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY}
    if spec is None:
        raise RuntimeError(
            f"dbllmbench connector: provider {provider!r} is neither {_ANTHROPIC!r} nor an "
            "OpenAI-compatible gateway this install knows."
        )
    extra: dict[str, Any] = {}
    if (temperature := cfg.get("temperature")) is not None:
        extra["temperature"] = temperature
    if body is not None:
        extra |= body
    elif effort is not None:
        extra["reasoning_effort"] = effort
    entry: dict[str, Any] = {
        "model": model,
        "base_url": spec.base_url or _OPENAI_BASE_URL,
        "api_key_env": _KEY_ENV,
        "max_tokens": reply,
    }
    if extra:
        entry["extra_body"] = extra
    return {"openai-compatible": entry}, {_KEY_ENV: getattr(settings, spec.api_key_attr)}


def _asset(cfg: Mapping[str, Any], key: str) -> str:
    path = cfg.get(key)
    if not isinstance(path, str) or not path:
        raise RuntimeError(f"dbllmbench connector: nodes.{WRITER_NODE}.config names no `{key}`.")
    return f"{_DATA_ROOT}/{path}"


def harness_config(cfg: Mapping[str, Any], prompt: str) -> tuple[dict[str, Any], dict[str, str]]:
    db = str(cfg.get("db") or "typedb")
    entry: dict[str, Any] = {
        "prompts": f"{_WORK}/template.txt",
        "url": settings.DBLLMBENCH_DB_URL or _DB_DEFAULT_URLS.get(db) or "",
        "schema": _asset(cfg, "schema"),
    }
    if database := cfg.get("database"):
        entry["database"] = database
    if db in _DB_DEFAULT_USERS:
        auth: dict[str, Any] = {
            "username": settings.DBLLMBENCH_DB_USERNAME or _DB_DEFAULT_USERS[db],
            "password": settings.DBLLMBENCH_DB_PASSWORD or "password",
        }
        if db == "typedb":
            auth["tls"] = settings.DBLLMBENCH_DB_TLS
        entry["auth"] = auth
    n_examples = int(cfg.get("examples") or 0)
    if n_examples:
        entry["examples"] = _asset(cfg, "examples_dir")
    if prompt:
        entry["skills"] = f"{_WORK}/skills"
    model, env = _model_entry(cfg)
    return {
        "dbs": [{db: entry}],
        "models": [model],
        "questionsPath": f"{_WORK}/questions.json",
        "exampleCounts": [n_examples],
        "maxRetryCounts": list(retry_levels(cfg)),
        # Absent at the protocol's count, so a reported cell's config is upstream's own keys.
        **({"repetitions": runs} if (runs := repetitions(cfg)) != REPETITIONS else {}),
        # The skill folder holds the CANDIDATE: a skill-off run is another arm, at twice the bill.
        "skillsBaseline": False,
    }, env


def _spent(records: list[dict[str, Any]], cfg: Mapping[str, Any]) -> dict[str, dict[str, object]]:
    if not records:
        return {}
    tokens = [r.get("tokens") or {} for r in records]
    model = cfg.get("model")
    entry = StepUsage(
        input=sum(int(t.get("input") or 0) for t in tokens),
        output=sum(int(t.get("output") or 0) for t in tokens),
        # Only the search image reports `cached`; upstream's build counts input and output alone.
        cache_read=(
            sum(int(t["cached"] or 0) for t in tokens)
            if all("cached" in t for t in tokens)
            else None
        ),
        model=model if isinstance(model, str) else None,
    )
    return {WRITER_NODE: entry.wire()}


def _stalled(record: Mapping[str, Any]) -> str | None:
    for attempt in record.get("attempts") or []:
        tokens = attempt.get("tokens") or {}
        if (
            attempt.get("error")
            and attempt.get("query") is None
            and attempt.get("response") is None
            and not tokens.get("input")
            and not tokens.get("output")
        ):
            return str(attempt["error"])
    return None


def _first_error(record: Mapping[str, Any]) -> str | None:
    attempts = record.get("attempts") or [{}]
    error = attempts[0].get("error")
    return None if error is None else str(error)


def project_records(
    records: list[dict[str, Any]], levels: tuple[int, ...], runs: int
) -> dict[str, float]:
    by_level: dict[int, list[dict[str, Any]]] = {level: [] for level in levels}
    for record in records:
        if (level := record.get("maxRetries")) in by_level:
            by_level[level].append(record)
    short = {level: len(rows) for level, rows in by_level.items() if len(rows) != runs}
    if short:
        raise ValueError(
            f"{runs} repetitions expected at each of retry levels {levels}; found {short}"
        )
    out: dict[str, float] = {}
    for level, rows in by_level.items():
        if any(not isinstance(r.get("accurate"), bool) for r in rows):
            raise ValueError(f"a record at retry level {level} carries no verdict")
        out[accuracy_key(level)] = sum(1 for r in rows if r["accurate"]) / runs
    first, top = by_level[levels[0]], by_level[levels[-1]]
    missed = [r for r in first if not r["accurate"]]
    visible = sum(1 for r in missed if _first_error(r) is not None)
    out[VISIBLE_ERROR_KEY] = visible / runs
    out[SILENT_WRONG_KEY] = (len(missed) - visible) / runs
    out[RETRIES_KEY] = sum(int(r.get("retriesUsed") or 0) for r in top) / runs
    return out


def _shown(attempt: Mapping[str, Any]) -> str:
    return str(attempt.get("query") or attempt.get("response") or "")[:_TURN_CAP]


def _turns(query: str, record: Mapping[str, Any]) -> list[TurnRecord]:
    turns: list[TurnRecord] = [{"index": 0, "source": "user", "message": query}]
    for n, attempt in enumerate(record.get("attempts") or [], start=1):
        turn: TurnRecord = {"index": n, "source": "agent", "message": _shown(attempt)}
        if error := attempt.get("error"):
            turn["observation"] = str(error)[:_TURN_CAP]
        turns.append(turn)
    return turns


def _digest(top: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for record in top:
        verdict = "accurate" if record.get("accurate") else "NOT accurate"
        lines.append(f"run {record.get('repetition')}: {verdict}")
        for attempt in record.get("attempts") or []:
            lines.append(_shown(attempt))
            if error := attempt.get("error"):
                lines.append(f"-> {str(error)[:_NOTE_CAP]}")
    return "\n".join(lines)


def _outcome_note(first: list[dict[str, Any]]) -> str | None:
    for record in first:
        if record.get("accurate"):
            continue
        if (error := _first_error(record)) is not None:
            return error.strip()[:_NOTE_CAP]
        return "the query ran and returned a result that is not the expected one"
    return None


async def _preflight(_backend_url: str) -> str | None:
    return await docker_daemon_fault()


async def _check_image(image: str) -> None:
    # At the first cell, not in `Connector.preflight`: that is handed no node config.
    if image in _IMAGES_CHECKED:
        return
    code, out = await docker(
        "image", "inspect", "--format", f'{{{{index .Config.Labels "{_COMMIT_LABEL}"}}}}', image
    )
    built = (
        "Build it from promptpotter/connectors/resources/dbllmbench.Dockerfile, whose header has "
        "the command."
    )
    if code != 0:
        raise CellInfrastructureError(
            f"dbllmbench: the runner image {image!r} is not on this Docker host. {built}",
            spent={},
        )
    named = image.rpartition(":")[2].partition("-")[0]
    if not named or not out.startswith(named):
        raise CellInfrastructureError(
            f"dbllmbench: the runner image {image!r} was built from upstream commit "
            f"{out or 'unknown'!r}, not the one its tag names. {built}",
            spent={},
        )
    _IMAGES_CHECKED.add(image)


def _failure(query: str, log: str, spent: dict[str, dict[str, object]]) -> CellUnscoreableError:
    tail = log[-600:]
    message = f"dbllmbench question {query[:80]!r} ended without a verdict: {tail}"
    if "transient provider error" in log or "provider timeout" in log:
        return cell_failure(message, ErrorCategory.PROVIDER_THROTTLED, spent=spent)
    if "fatal provider error" in log and is_provider_credit_refusal(log):
        return cell_failure(message, ErrorCategory.PROVIDER_CREDIT, spent=spent)
    # No record of what it sent: the cell's whole bound is held.
    return cell_failure(message, ErrorCategory.CONNECTION, spent=spent or None)


async def _in_process_run(
    workload: InProcessWorkload, sample: Sample, payload: dict[str, Any]
) -> dict[str, Any]:
    query = sample.query
    question = _upstream_question(workload, sample)
    cfg: dict[str, Any] = payload.get("config") or {}
    prompt = str(payload.get("prompt") or "")
    image = cfg.get("runner_image")
    if not image:
        raise RuntimeError(
            f"dbllmbench connector: nodes.{WRITER_NODE}.config names no `runner_image`."
        )
    config, env = harness_config(cfg, prompt)
    db = next(iter(config["dbs"][0]))
    template = _asset(cfg, "template")
    with machine_step():
        await _check_image(str(image))

    name = f"pp-dbllmbench-{uuid4().hex[:12]}"
    work = PRODUCER_SCRATCH / name
    work.mkdir()
    try:
        (work / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        (work / "questions.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        if prompt:
            (work / "skills").mkdir()
            (work / "skills" / "candidate.md").write_text(prompt, encoding="utf-8", newline="\n")
        # Upstream's template from its `{{skills}}` slot down: the candidate replaces the rest.
        cut = (
            f"sed -n '/{{{{skills}}}}/,$p' {template} > /tmp/template.txt "
            f"&& cp /tmp/template.txt {_WORK}/template.txt "
            f"&& exec db-llm-bench {_WORK}/config.yml {_WORK}/out.json"
        )
        args = ["--pull", "never", "--add-host", "host.docker.internal:host-gateway"]
        for key in env:
            args += ["-e", key]
        args += ["-v", f"{work}:{_WORK}", str(image), "sh", "-c", cut]
        with machine_step():
            code, log = await run_cell_container(name, *args, env=env, timeout=_CELL_WAIT_S)
        try:
            written = json.loads((work / "out.json").read_text(encoding="utf-8"))
            records = list(written["questions"][0]["dbs"][db]["results"])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            records = []
    finally:
        with contextlib.suppress(OSError):
            rmtree_robust(work)

    levels = retry_levels(cfg)
    top = [r for r in records if r.get("maxRetries") == levels[-1]]
    spent = _spent(top, cfg)
    if code != 0:
        raise _failure(query, log, spent)
    if stall := next((s for r in top if (s := _stalled(r)) is not None), None):
        raise CellThrottledError(f"dbllmbench question {query[:80]!r}: {stall}", spent=spent)
    try:
        observed = project_records(records, levels, repetitions(cfg))
    except ValueError as exc:
        raise CellUnscoreableError(
            f"dbllmbench question {query[:80]!r}: {exc}.", spent=spent
        ) from exc

    first = [r for r in records if r.get("maxRetries") == levels[0]]
    missed = next((n for n, r in enumerate(first) if not r.get("accurate")), 0)
    shown = top[missed]
    data: dict[str, Any] = {
        **observed,
        ANSWER_KEY: str(shown.get("generated") or ""),
        "terminal_node": WRITER_NODE,
        "step_tokens": spent,
        "reasoning_trace": _digest(top),
        "turns": _turns(query, shown),
    }
    if note := _outcome_note(first):
        data["outcome_note"] = note
    return {"data": data}


CONNECTOR = Connector(
    name="dbllmbench",
    execution="in_process",
    wire_adapter=dbllmbench_wire_adapter,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    preflight=_preflight,
    sent_spend_bound=_sent_spend_bound,
    # Every cell queries the same database, so the ceiling is this machine's.
    max_cells_in_flight=2,
    cells_hold_the_machine=True,
    cell_wait_s=_CELL_WAIT_S,
    required_observation_keys=(accuracy_key(0),),
    answer_key=ANSWER_KEY,
    experiment_file=QUESTIONS_FILE,
    default_pipeline=(WRITER_NODE,),
)


__all__ = [
    "ANSWER_KEY",
    "CONNECTOR",
    "QUESTIONS_FILE",
    "RETRY_LEVELS",
    "WRITER_NODE",
    "accuracy_key",
    "dbllmbench_wire_adapter",
    "harness_config",
    "project_records",
]
