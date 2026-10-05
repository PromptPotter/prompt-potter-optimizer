"""db-llm-bench-as-connector — one benchmark QUESTION, run by TypeDB's own harness, as one
measured cell. A THIN adapter: it writes the harness a config, runs its binary in a container and
reads the results file back, because the retry loop and the scorer are theirs
(``github.com/typedb/db-llm-bench``) and a port of either would be a second benchmark.

**What is being optimized here is the head of their prompt**: the candidate's rendered prompt is
written as the one skill file their runner loads, into a template cut at its ``{{skills}}`` slot —
so everything below the slot (schema, examples, the fenced-block footer, the question and its
return-shape line) stays theirs, and the pair of files a cell ran on drops into their runner
unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import yaml

from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import Connector, InProcessWorkload, NoopSession
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import LLMSpendBound
from promptpotter.infrastructure.llm.registry import openai_compat_spec
from promptpotter.shared.errors import (
    CellInfrastructureError,
    CellSendRefusedError,
    CellThrottledError,
    CellUnscoreableError,
    ErrorCategory,
    is_provider_credit_refusal,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.domain.scoring import TurnRecord
    from promptpotter.domain.spend import StepTokenUsage

logger = logging.getLogger(__name__)


# The one node a dbllmbench dataset declares. ONE, and an `llm` rather than an `agent`: no tool is
# called, and the retries are the harness feeding an error back, not the model choosing a step.
WRITER_NODE = "query_writer"

# The panel: upstream's own questions file, vendored verbatim.
QUESTIONS_FILE = "questions.json"

# The retry budgets a cell is read at, which are the published protocol's. The harness runs once
# at the highest and DERIVES the lower ones by cutting the attempt trace, so the three accuracies
# are views of one execution — a token count summed across them counts each attempt three times.
RETRY_LEVELS = (0, 2, 4)


def retry_levels(cfg: Mapping[str, Any]) -> tuple[int, ...]:
    """The retry budgets this cell is read at: the protocol's unless the node narrows them, and
    always zero among them — the first-attempt reading every other is banked beside."""
    return tuple(sorted({0, *(int(level) for level in cfg.get("retry_levels") or RETRY_LEVELS)}))


# Runs per question in the published protocol, and upstream's hardcoded count
# (`src/runner/src/lib.rs::REPETITIONS`). A cell's accuracy is a mean over its runs, and fewer
# records than it asked for is a cell that did not finish rather than a smaller sample.
REPETITIONS = 3


def repetitions(cfg: Mapping[str, Any]) -> int:
    """Runs per question for this cell: the protocol's unless the node asks for fewer, which only
    an image built with ``resources/dbllmbench-repetitions.patch`` can run — upstream's own build
    ignores the key, runs three, and the cell fails its record count."""
    return int(cfg.get("repetitions") or REPETITIONS)


ANSWER_KEY = "generated_query"
RETRIES_KEY = "retries_used"
# How a first attempt MISSED, which is the benchmark's own finding: an error the harness could
# feed back, against a query that ran and answered wrongly, which nothing can retry. With
# `accuracy_r0` the three partition the cell's runs.
VISIBLE_ERROR_KEY = "visible_error_r0"
SILENT_WRONG_KEY = "silent_wrong_r0"


def accuracy_key(level: int) -> str:
    return f"accuracy_r{level}"


_DATA_ROOT = "/opt/db-llm-bench/data"
_WORK = "/work"
_KEY_ENV = "DBLLMBENCH_API_KEY"
_ANTHROPIC = "anthropic"
_OPENAI_BASE_URL = "https://api.openai.com/v1"

# Where the database is, which is the deployment's and never the dataset's: the same panel runs
# against a local container or a cloud cluster. `settings.DBLLMBENCH_DB_*` name it, from `.env`
# beside the API keys; empty, the defaults are upstream's own compose stack, reached from inside
# the runner's container.
_DB_DEFAULT_URLS = {
    "typedb": "http://host.docker.internal:1729",
    "neo4j": "bolt://host.docker.internal:7687",
}
_DB_DEFAULT_USERS = {"typedb": "admin", "neo4j": "neo4j"}

# The harness bounds every await it makes — a provider call, a query, its own backoffs — so this
# only ends a container that stopped answering for them.
_CELL_TIMEOUT_S = 3600.0
_NOTE_CAP = 300
_TURN_CAP = 1200

# The label `resources/dbllmbench.Dockerfile` stamps with the upstream commit it built. A tag is
# only a name, and a rebuild under an old one would replay every cell banked on the old build.
_COMMIT_LABEL = "org.promptpotter.db-llm-bench.commit"
# Process-scoped: an image is a fact about the machine, not a run.
_IMAGES_CHECKED: set[str] = set()


def _extract_experiment(
    experiment_data: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Upstream's questions → ``(queries, index_terms)``, and **the one place this backend's answer
    shape is declared** (``connectors/CLAUDE.md`` § The answer shape): no label, because the
    harness executes the query and compares the RESULT, and the expected value is not text a
    generated query could be matched against."""
    return [
        {
            "query": q["question"],
            "ground_truth": None,
            # What the cell is graded against. Without it a corrected expected value would replay
            # every verdict taken under the old one.
            "source_pin": {
                "expected": q.get("expected"),
                "expected_by_db": q.get("expected_by_db"),
                "ordered": bool(q.get("ordered")),
                "unanswerable": bool(q.get("unanswerable")),
            },
        }
        for q in experiment_data["questions"]
    ], []


def _current_question(panel: Mapping[str, Any] | None, query: str) -> dict[str, Any]:
    if panel is None:
        raise RuntimeError(
            f"dbllmbench connector: this run's workload carries no {QUESTIONS_FILE}, so no "
            "question can be resolved for it."
        )
    for question in panel["questions"]:
        if question.get("question") == query:
            return dict(question)
    raise RuntimeError(
        f"dbllmbench connector: no question {query[:80]!r} in {QUESTIONS_FILE}. The samples being "
        "scored did not come from this workload's panel."
    )


def dbllmbench_wire_adapter(query: str, pipeline_params: dict[str, Any] | None) -> dict[str, Any]:
    """Outbound payload for one question: the candidate's rendered prompt, and the node config the
    harness's own config file is written from."""
    payload: dict[str, Any] = {"query": query}
    for node, cfg in node_config_items(pipeline_params):
        if node != WRITER_NODE:
            continue
        if prompt := cfg.get("prompt"):
            payload["prompt"] = prompt
        payload["config"] = {k: v for k, v in cfg.items() if k != "prompt"}
    return payload


def _sent_spend_bound(node: str, cfg: Mapping[str, Any]) -> LLMSpendBound | None:
    """What one cell can bill: every repetition taking every retry, each reading the declared
    context and replying the reply cap. The harness sends from its container, past this process,
    so the cell is held whole at this and billed off the token counts its results file reports."""
    if node != WRITER_NODE:
        return None
    context, reply = cfg.get("max_input_tokens"), cfg.get("max_tokens")
    if context is None or reply is None:
        return None
    route = cfg.get("route_order")
    return LLMSpendBound(
        kind="llm",
        attempts=repetitions(cfg) * (max(retry_levels(cfg)) + 1),
        # Priced as a token count (`cell_bound`); the context we declare IS that count.
        input_bytes=int(context),
        max_tokens=int(reply),
        hosts=tuple(route) if route else None,
    )


def _model_entry(cfg: Mapping[str, Any], prompt: str) -> tuple[dict[str, Any], dict[str, str]]:
    """The harness's ``models`` entry for this node's model, and the environment its key rides.
    The key is named to the container and never written into the config file, which is scratch on
    disk for the length of the cell."""
    model, provider = cfg.get("model"), cfg.get("provider")
    if not isinstance(model, str) or not isinstance(provider, str):
        raise RuntimeError(
            f"dbllmbench connector: nodes.{WRITER_NODE}.config must name a `model` and a "
            "`provider`."
        )
    reply = int(cfg.get("max_tokens") or 8192)
    effort = cfg.get("reasoning_effort")
    if provider == _ANTHROPIC:
        claude: dict[str, Any] = {"model": model, "max_tokens": reply}
        if effort is not None:
            claude["effort"] = effort
        return {"claude": claude}, {"ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY}
    spec = openai_compat_spec(provider)
    if spec is None:
        raise RuntimeError(
            f"dbllmbench connector: provider {provider!r} is neither {_ANTHROPIC!r} nor an "
            "OpenAI-compatible gateway this install knows."
        )
    extra: dict[str, Any] = {}
    if (temperature := cfg.get("temperature")) is not None:
        extra["temperature"] = temperature
    if route := cfg.get("route_order"):
        extra["provider"] = {"order": list(route), "allow_fallbacks": False}
    if spec.gateway:
        # Every run, retry and question of one candidate re-sends the same head — the candidate,
        # the schema, the examples — and a host's prompt cache is its own. Naming the candidate as
        # the session keeps the gateway on the host that already holds that head.
        extra["session_id"] = hashlib.sha256(prompt.encode()).hexdigest()[:32]
        # Asked for, as our own client asks: the cache breakdown `_spent` bills the cell off.
        extra["usage"] = {"include": True}
    if effort is not None:
        # The gateway's body extension where it is one, the OpenAI spelling everywhere else.
        if spec.gateway:
            extra["reasoning"] = {"effort": effort}
        else:
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
    """A path inside the image's copy of upstream ``data/``, which the node config must name."""
    path = cfg.get(key)
    if not isinstance(path, str) or not path:
        raise RuntimeError(f"dbllmbench connector: nodes.{WRITER_NODE}.config names no `{key}`.")
    return f"{_DATA_ROOT}/{path}"


def harness_config(cfg: Mapping[str, Any], prompt: str) -> tuple[dict[str, Any], dict[str, str]]:
    """The harness's config document for one cell, and the environment its container needs. Paths
    are the container's: the dataset's assets are the image's own copy of upstream ``data/``, the
    question, template and skill are the cell's scratch."""
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
    model, env = _model_entry(cfg, prompt)
    return {
        "dbs": [{db: entry}],
        "models": [model],
        "questionsPath": f"{_WORK}/questions.json",
        "exampleCounts": [n_examples],
        "maxRetryCounts": list(retry_levels(cfg)),
        # Absent at the protocol's count, so a reported cell's config is upstream's own keys.
        **({"repetitions": runs} if (runs := repetitions(cfg)) != REPETITIONS else {}),
        # The skill folder holds the CANDIDATE, so a skill-off run is a different arm, not a
        # control for this one — and it would double what the cell bills.
        "skillsBaseline": False,
    }, env


def _spent(records: list[dict[str, Any]], cfg: Mapping[str, Any]) -> dict[str, StepTokenUsage]:
    """What the cell's runs billed, off the records at the HIGHEST retry level alone — the lower
    ones are cuts of the same attempts.

    ``cache_read`` only where the harness reports one (the search image's ``cached``): upstream's
    own build counts input and output alone, so its cells are priced with every token at the list
    rate, which a cached prefix bills well under."""
    if not records:
        return {}
    tokens = [r.get("tokens") or {} for r in records]
    entry: StepTokenUsage = {
        "input": sum(int(t.get("input") or 0) for t in tokens),
        "output": sum(int(t.get("output") or 0) for t in tokens),
        "estimated": False,
    }
    if all("cached" in t for t in tokens):
        entry["cache_read"] = sum(int(t["cached"] or 0) for t in tokens)
    if isinstance(model := cfg.get("model"), str):
        entry["model"] = model
    return {WRITER_NODE: entry}


def _stalled(record: Mapping[str, Any]) -> str | None:
    """The provider's stall, where the harness recorded one as an attempt: nothing was received,
    so the run says how the provider was doing and nothing about the prompt."""
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
    records: list[dict[str, Any]],
    levels: tuple[int, ...] = RETRY_LEVELS,
    runs: int = REPETITIONS,
) -> dict[str, float]:
    """One question's result records → the observations a formula reads. ``records`` is each of
    the *runs* repetitions at every retry level in *levels*, as the harness wrote them."""
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
    """What one attempt SAID: its query, or the reply the harness found no query in."""
    return str(attempt.get("query") or attempt.get("response") or "")[:_TURN_CAP]


def _turns(query: str, record: Mapping[str, Any]) -> list[TurnRecord]:
    """One repetition as a conversation: the question, then each attempt with what the database
    answered it. The error the harness fed back rides the attempt's ``observation``."""
    turns: list[TurnRecord] = [{"index": 0, "source": "user", "message": query}]
    for n, attempt in enumerate(record.get("attempts") or [], start=1):
        turn: TurnRecord = {"index": n, "source": "agent", "message": _shown(attempt)}
        if error := attempt.get("error"):
            turn["observation"] = str(error)[:_TURN_CAP]
        turns.append(turn)
    return turns


def _digest(top: list[dict[str, Any]]) -> str:
    """What the model wrote across the cell's runs, for the optimizer's reading of a miss."""
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
    """Why a first attempt missed, in the database's own words; ``None`` where none did."""
    for record in first:
        if record.get("accurate"):
            continue
        if (error := _first_error(record)) is not None:
            return error.strip()[:_NOTE_CAP]
        return "the query ran and returned a result that is not the expected one"
    return None


async def _docker(
    *args: str, env: Mapping[str, str] | None = None, timeout: float
) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, **env} if env else None,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"`docker {' '.join(args[:2])}` did not answer in {timeout:.0f}s"
        ) from None
    return proc.returncode or 0, (out or b"").decode(errors="replace").strip()


async def _preflight(backend_url: str) -> None:
    """A container runtime must answer before a campaign starts spending. The image and the
    database are checked by the first cell — the harness validates both before it calls a model."""
    try:
        code, out = await _docker("version", "--format", "{{.Server.Version}}", timeout=30)
    except (OSError, TimeoutError) as exc:
        raise BackendUnreachableError(
            "dbllmbench", backend_url, f"docker not callable: {exc}"
        ) from exc
    if code != 0:
        raise BackendUnreachableError(
            "dbllmbench", backend_url, f"docker daemon not responding: {out[:200]}"
        )


async def _check_image(image: str) -> None:
    """The runner image is on this Docker host and was built from the commit its tag names. At
    the FIRST CELL rather than in ``Connector.preflight``, which is handed no node config — and
    the diagnostics and the embedded launch measure cells without ever running it."""
    if image in _IMAGES_CHECKED:
        return
    try:
        code, out = await _docker(
            "image", "inspect", "--format", f'{{{{index .Config.Labels "{_COMMIT_LABEL}"}}}}',
            image, timeout=30,
        )  # fmt: skip
    except (OSError, TimeoutError) as exc:
        raise CellInfrastructureError(f"dbllmbench: {exc}", spent={}, step_timings={}) from exc
    built = (
        "Build it from promptpotter/connectors/resources/dbllmbench.Dockerfile, whose header has "
        "the command."
    )
    if code != 0:
        raise CellInfrastructureError(
            f"dbllmbench: the runner image {image!r} is not on this Docker host. {built}",
            spent={},
            step_timings={},
        )
    named = image.rpartition(":")[2].partition("-")[0]
    if not named or not out.startswith(named):
        raise CellInfrastructureError(
            f"dbllmbench: the runner image {image!r} was built from upstream commit "
            f"{out or 'unknown'!r}, not the one its tag names. {built}",
            spent={},
            step_timings={},
        )
    _IMAGES_CHECKED.add(image)


def _failure(
    query: str, log: str, spent: dict[str, StepTokenUsage], elapsed: float
) -> CellUnscoreableError:
    """A harness that ended without a full set of verdicts measured its provider, its database or
    this machine — never the prompt. Which one decides what the run does next."""
    tail = log[-600:]
    timings = {WRITER_NODE: elapsed}
    message = f"dbllmbench question {query[:80]!r} ended without a verdict: {tail}"
    if "transient provider error" in log or "provider timeout" in log:
        # The provider's load, outlasting the harness's own backoff.
        return CellThrottledError(message, spent=spent, step_timings=timings)
    if "fatal provider error" in log and is_provider_credit_refusal(log):
        return CellSendRefusedError(
            message, category=ErrorCategory.PROVIDER_CREDIT, spent=spent, step_timings=timings
        )
    return CellInfrastructureError(message, spent=spent, step_timings=timings)


async def _in_process_run(
    workload: InProcessWorkload, query: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Run one question through the harness and project its records onto the ``{"data": {…}}``
    shape ``measure_sample`` parses from an HTTP body."""
    question = _current_question(workload.experiment, query)
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
    await _check_image(str(image))

    # Scratch in the system temp dir, never the workspace: nothing durable lives here.
    work = Path(tempfile.mkdtemp(prefix="pp-dbllmbench-"))
    name = f"pp-dbllmbench-{uuid4().hex[:12]}"
    start = time.monotonic()
    try:
        (work / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        (work / "questions.json").write_text(
            json.dumps({"questions": [question]}, ensure_ascii=False), encoding="utf-8"
        )
        if prompt:
            (work / "skills").mkdir()
            (work / "skills" / "candidate.md").write_text(prompt, encoding="utf-8", newline="\n")
        # Upstream's own template from its `{{skills}}` slot down: the lines above the slot are
        # the half of their prompt the candidate replaces.
        cut = (
            f"sed -n '/{{{{skills}}}}/,$p' {template} > /tmp/template.txt "
            f"&& cp /tmp/template.txt {_WORK}/template.txt "
            f"&& exec db-llm-bench {_WORK}/config.yml {_WORK}/out.json"
        )
        args = ["run", "--rm", "--pull", "never", "--name", name]
        args += ["--add-host", "host.docker.internal:host-gateway"]
        for key in env:
            args += ["-e", key]
        args += ["-v", f"{work}:{_WORK}", str(image), "sh", "-c", cut]
        try:
            code, log = await _docker(*args, env=env, timeout=_CELL_TIMEOUT_S)
        except asyncio.CancelledError:
            # The docker CLI was only attached: left alone, the container goes on calling the
            # provider for a cell nobody will read.
            await asyncio.shield(_docker("kill", name, timeout=30))
            raise
        except TimeoutError as exc:
            await _docker("kill", name, timeout=30)
            raise CellInfrastructureError(
                f"dbllmbench question {query[:80]!r}: {exc}",
                spent={},
                step_timings={WRITER_NODE: time.monotonic() - start},
            ) from exc
        elapsed = time.monotonic() - start
        try:
            written = json.loads((work / "out.json").read_text(encoding="utf-8"))
            records = list(written["questions"][0]["dbs"][db]["results"])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            records = []
    finally:
        shutil.rmtree(work, ignore_errors=True)

    levels = retry_levels(cfg)
    top = [r for r in records if r.get("maxRetries") == levels[-1]]
    spent = _spent(top, cfg)
    timings = {WRITER_NODE: elapsed}
    if code != 0:
        raise _failure(query, log, spent, elapsed)
    if stall := next((s for r in top if (s := _stalled(r)) is not None), None):
        raise CellThrottledError(
            f"dbllmbench question {query[:80]!r}: {stall}", spent=spent, step_timings=timings
        )
    try:
        observed = project_records(records, levels, repetitions(cfg))
    except ValueError as exc:
        # Nothing to grade, and a 0.0 here would read as three runs that all missed.
        raise CellUnscoreableError(
            f"dbllmbench question {query[:80]!r}: {exc}.", spent=spent, step_timings=timings
        ) from exc

    first = [r for r in records if r.get("maxRetries") == levels[0]]
    # The run a reader most needs: the first that missed on its first attempt, else the first.
    missed = next((n for n, r in enumerate(first) if not r.get("accurate")), 0)
    shown = top[missed]
    data: dict[str, Any] = {
        **observed,
        ANSWER_KEY: str(shown.get("generated") or ""),
        "terminal_node": WRITER_NODE,
        "total_time": elapsed,
        "step_timings": timings,
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
    session_factory=NoopSession,
    extract_experiment=_extract_experiment,
    in_process_run=_in_process_run,
    preflight=_preflight,
    # The harness sends from its container, so no send of the cell's passes through this process:
    # the cell is held whole at its declared worst case and billed off what its results report.
    sent_spend_bound=_sent_spend_bound,
    # Each cell holds a container, and every one of them queries the same database — so the
    # ceiling is this machine's, and every run on it draws from one pool.
    max_cells_in_flight=2,
    cells_hold_the_machine=True,
    required_observation_keys=(accuracy_key(0),),
    answer_key=ANSWER_KEY,
    # The "samples" ARE the questions declared there — read from the dataset config dir at init.
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
