"""A LongSeal cut's Harbor task directories and its panel, built from the hub rows into the tenant
tier — SealQA ships a per-row `canary` GUID that no definition in this package may carry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.dataset_access import MATERIALIZED_PANEL_FILE
from promptpotter.infrastructure.store.io import rmtree_robust, write_bytes

# Harbor's artifacts dir, which `connectors/harbor.py::_answer` reads back as `predicted`.
ANSWER_PATH = "/logs/artifacts/answer.txt"
RETRIEVED_PATH = "/logs/artifacts/retrieved.txt"


def _doc(i: int, d: dict[str, Any]) -> tuple[str, str]:
    title = str(d.get("title") or "").strip()
    return (f"[{i}] {title}" if title else f"[{i}]"), str(d.get("text") or "").strip()


def _render_docs(docs: list[dict[str, Any]]) -> str:
    parts = (_doc(i, d) for i, d in enumerate(docs, start=1))
    return "\n\n".join(f"{head}\n{text}" if text else head for head, text in parts)


# Nothing that FETCHES: where no network policy holds, the toolless image is the only seal.
_DOCKERFILE = """FROM ubuntu:24.04

# Harbor's agent tooling only. A fetch tool here would hand the agent a way around the haystack.
RUN apt-get update \\
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tmux asciinema \\
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
"""

_SOLVE_SH = (
    "#!/bin/bash\n"
    "# No oracle. The gold answer is held outside the container (`panel.yaml`) and\n"
    "# graded there, so that the agent's environment never contains what it is asked for.\n"
)

# No `network_mode`: its egress sidecar needs `CONFIG_NFT_FIB_INET`, which Docker Desktop lacks.
_TASK_TOML = """schema_version = "1.4"

[metadata]

[verifier]
timeout_sec = 300.0

[agent]
timeout_sec = 900.0

[environment]
build_timeout_sec = 600.0
"""

# `env_reward` is instrument health, never the score: a gold in `tests/` is readable by a step.
_TEST_SH = f"""#!/bin/bash
# Non-empty answer artifact => 1; correctness is the campaign's call, not this container's.
# Echoes because `_digest` tails this into the optimizer's view and warns when the tail is empty —
# a silent script fires that warning on every healthy cell and masks real layout drift.
if [ -s "{ANSWER_PATH}" ]; then
  echo "answer: $(head -c 200 "{ANSWER_PATH}")"
  echo 1 > /logs/verifier/reward.txt
else
  echo "answer: MISSING - the episode ended without writing {ANSWER_PATH}"
  echo 0 > /logs/verifier/reward.txt
fi
"""

_INSTRUCTION_SINGLE = """{question}

Documents:

{documents}

---

This machine is offline and has no search tool. The documents above are the only evidence there
is; they were retrieved automatically and were not curated.

When you have decided, write your final answer to `{answer_path}` and stop.
"""

_INSTRUCTION_RETRIEVE = """The documents that were retrieved for a question are in the working \
directory, one file per document, numbered.

Question: {question}

This machine is offline and has no search tool. Those documents are the only evidence there is.

Work out which of these documents settle the question. Write the document numbers you are relying \
on to `{retrieved_path}`, one per line, followed by a line for each saying what it establishes.
"""

_INSTRUCTION_ANSWER = """Answer the question from the documents you selected.

Question: {question}

Your selection is at `{retrieved_path}` and the documents are still in the working directory. \
Write your final answer to `{answer_path}` and stop.
"""

_TASK_TOML_2STEP = (
    _TASK_TOML
    + """
# Named rather than inherited: this fold is what makes two steps ONE cell score. Per-step rewards
# ride the row as terms beside it — promoting either to an item would claim 2N observations.
multi_step_reward_strategy = "mean"

[[steps]]
name = "retrieve"
[steps.agent]
timeout_sec = 900.0
[steps.verifier]
timeout_sec = 300.0
# SEPARATE: a shared verifier's `tests/` lands in the agent's container and is only emptied at the
# NEXT step's verification, so gold document numbers would be readable throughout the answer step.
environment_mode = "separate"

[[steps]]
name = "answer"
[steps.agent]
timeout_sec = 900.0
[steps.verifier]
timeout_sec = 300.0
"""
)

_TEST_SH_RETRIEVE = f"""#!/bin/bash
# Non-empty selection artifact => 1. Which documents were the right ones is graded OUTSIDE the
# container: the gold document set would otherwise sit in `/tests` for the answer step to read.
# Echoes for the same reason as the answer step's verifier — see `_TEST_SH`.
if [ -s "{RETRIEVED_PATH}" ]; then
  echo "selection: $(head -c 200 "{RETRIEVED_PATH}")"
  echo 1 > /logs/verifier/reward.txt
else
  echo "selection: MISSING - the step ended without writing {RETRIEVED_PATH}"
  echo 0 > /logs/verifier/reward.txt
fi
"""


def _write(path: Path, text: str) -> None:
    # Bytes, so a task's files are LF on every machine: they are what the task image hashes.
    write_bytes(path, text.encode("utf-8"))


def _write_single_step(task_dir: Path, question: str, docs: list[dict[str, Any]]) -> None:
    _write(task_dir / "task.toml", _TASK_TOML)
    _write(
        task_dir / "instruction.md",
        _INSTRUCTION_SINGLE.format(
            question=question, documents=_render_docs(docs), answer_path=ANSWER_PATH
        ),
    )
    _write(task_dir / "environment" / "Dockerfile", _DOCKERFILE)
    _write(task_dir / "solution" / "solve.sh", _SOLVE_SH)
    _write(task_dir / "tests" / "test.sh", _TEST_SH)


def _write_two_step(task_dir: Path, question: str, docs: list[dict[str, Any]]) -> None:
    _write(task_dir / "task.toml", _TASK_TOML_2STEP)
    _write(task_dir / "environment" / "Dockerfile", _DOCKERFILE)
    _write(task_dir / "solution" / "solve.sh", _SOLVE_SH)
    # `steps/<name>/workdir/` is Harbor's upload convention: it lands in the agent's cwd.
    for i, d in enumerate(docs, start=1):
        head, text = _doc(i, d)
        _write(
            task_dir / "steps" / "retrieve" / "workdir" / f"doc-{i:02d}.md",
            f"{head}\n\n{text}\n" if text else f"{head}\n",
        )
    _write(
        task_dir / "steps" / "retrieve" / "instruction.md",
        _INSTRUCTION_RETRIEVE.format(question=question, retrieved_path=RETRIEVED_PATH),
    )
    _write(task_dir / "steps" / "retrieve" / "tests" / "test.sh", _TEST_SH_RETRIEVE)
    _write(
        task_dir / "steps" / "answer" / "instruction.md",
        _INSTRUCTION_ANSWER.format(
            question=question, retrieved_path=RETRIEVED_PATH, answer_path=ANSWER_PATH
        ),
    )
    _write(task_dir / "steps" / "answer" / "tests" / "test.sh", _TEST_SH)


def _panel_yaml(tasks: list[dict[str, str]], provenance: str) -> str:
    """Paths are relative to this file, since a task's pin enters its sample key; `harbor.py`
    resolves them against the panel's directory."""
    # json.dumps every scalar: a question or gold may hold a colon, quote or newline.
    lines = [
        "# BUILT from the definition's `source:` — rebuilt whole, never hand-edited.",
        f"# {provenance}",
        "",
        "reward_key: reward",
        "",
        "agent:",
        "  name: terminus-2",
        "  environment: docker",
        # NO `kwargs:` — `harbor.py` lays `pipeline.nodes.agent.config` over it, always.
        "",
        "tasks:",
    ]
    for t in tasks:
        lines.append(f"  - id: {json.dumps(t['id'])}")
        lines.append(f"    path: {json.dumps(t['path'])}")
        lines.append(f"    question: {json.dumps(t['question'])}")
        lines.append(f"    answer: {json.dumps(t['answer'])}")
    return "\n".join(lines) + "\n"


def write_longseal_panel(
    rows: list[dict[str, Any]], out_dir: Path, *, docs: int, two_step: bool, provenance: str
) -> int:
    column = f"{docs}_docs"
    tasks_root = out_dir / "tasks"
    # Rebuilt whole rather than merged into: a stale directory is a task Harbor still runs.
    if tasks_root.exists():
        rmtree_robust(tasks_root)
    tasks: list[dict[str, str]] = []
    for i, row in enumerate(rows):
        haystack = list(row.get(column) or [])
        if not haystack:
            continue
        task_id = f"longseal-{i:03d}"
        task_dir = tasks_root / task_id
        question = str(row["question"]).strip()
        if two_step:
            _write_two_step(task_dir, question, haystack)
        else:
            _write_single_step(task_dir, question, haystack)
        tasks.append(
            {
                "id": task_id,
                "path": task_dir.relative_to(out_dir).as_posix(),
                "question": question,
                "answer": str(row["answer"]).strip(),
            }
        )
    _write(out_dir / MATERIALIZED_PANEL_FILE, _panel_yaml(tasks, provenance))
    return len(tasks)


__all__ = ["write_longseal_panel"]
