#!/usr/bin/env python
"""Materialize a `sealqa-longseal-*` dataset's Harbor task directories and its panel file.

A build step rather than a loader: a harbor dataset's samples ARE its `harbor_tasks.yaml` tasks,
and a loader registered under the same name would win over the connector's panel.

Both outputs are the drawn cut and stay out of git — `.gitignore` makes the same split for
`datasets/*/cache.json`. SealQA ships a per-row `canary` GUID and this repo is public, so the
questions and golds must not be committed; this file plus its pinned revision regenerates them.

Usage (from the repo root):

    python scripts/build_sealqa_tasks.py                     # every sealqa-longseal-* dir
    python scripts/build_sealqa_tasks.py sealqa-longseal-12  # just this one
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = REPO_ROOT / "datasets"

# Pinned, not a branch: `sample_id` is scoped by dataset name and the row text is outside the key,
# so a moving roster would re-serve old measurements for new rows.
HF_DATASET = "vtllms/sealqa"
HF_CONFIG = "longseal"
HF_SPLIT = "test"
HF_REVISION = "b2ecadf036972d8471a5c4cdf92aa3b3c6ba96e7"

# The size is the experimental variable, so it lives in the dataset NAME: `sealqa-longseal-20` is a
# different instrument, not this one reconfigured.
_SIZES = (12, 20, 30)
_NAME_RE = re.compile(r"^sealqa-longseal-(\d+)(-2step)?$")

# Harbor's convention artifacts dir, which `connectors/harbor.py::_answer` reads back as
# `predicted`. One spelling, shared by the instruction and the verifier.
ANSWER_PATH = "/logs/artifacts/answer.txt"
RETRIEVED_PATH = "/logs/artifacts/retrieved.txt"


def _sizes_from_name(name: str) -> tuple[int, bool]:
    """`(haystack size, is two-step)` for a dataset directory name."""
    m = _NAME_RE.match(name)
    if m is None:
        raise SystemExit(f"{name!r} is not a sealqa-longseal-* dataset name.")
    size = int(m.group(1))
    if size not in _SIZES:
        raise SystemExit(f"{name!r}: LongSeal ships {_SIZES}, not {size}.")
    return size, bool(m.group(2))


def _render_docs(docs: list[dict[str, Any]]) -> str:
    """The haystack as prompt text, NUMBERED so a model can cite one and a grounding judge can read
    the citation back.

    Title and text only: a URL is a retrieval artifact rather than evidence and `date` is usually
    null. The one place that choice is made, for both layouts."""
    out: list[str] = []
    for i, d in enumerate(docs, start=1):
        title = str(d.get("title") or "").strip()
        text = str(d.get("text") or "").strip()
        head = f"[{i}] {title}" if title else f"[{i}]"
        out.append(f"{head}\n{text}" if text else head)
    return "\n\n".join(out)


# One image for the whole panel, so the layer cache builds it once. `tmux`/`asciinema` are Harbor's
# agent tooling, baked in so no cell pays an apt-get and the runtime needs no egress. Nothing that
# FETCHES is added: on a host that cannot enforce a network policy, that is the only thing between
# the agent and the live web.
_DOCKERFILE = """FROM ubuntu:24.04

# Harbor's agent tooling only. A fetch tool here would hand the agent a way around the haystack.
RUN apt-get update \\
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tmux asciinema \\
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
"""

# Inert stub. Harbor's OracleAgent would run this; the gold stays out of the task directory the
# agent's image is built from.
_SOLVE_SH = (
    "#!/bin/bash\n"
    "# No oracle. The gold answer is held outside the container (`harbor_tasks.yaml`) and\n"
    "# graded there, so that the agent's environment never contains what it is asked for.\n"
)

# No `network_mode`: Harbor enforces every non-public policy through an egress sidecar needing
# `CONFIG_NFT_FIB_INET` in the daemon's kernel, which Docker Desktop lacks, so declaring one ERRs
# every cell instead of sealing it. The toolless image is what holds here — see `_DOCKERFILE`.
#
# On a Linux host whose kernel has it, add `network_mode = "no-network"` under `[agent]` and
# `[verifier]`. Phase-scoped, so `[environment]` stays public and the image still builds; the agent
# itself is a host process, so sealing the container costs it no model access.
_TASK_TOML = """schema_version = "1.4"

[metadata]

[verifier]
timeout_sec = 300.0

[agent]
timeout_sec = 900.0

[environment]
build_timeout_sec = 600.0
"""

# `env_reward` is instrument health — "did this episode answer at all" — never the score. A
# container verifier may decide nothing more: `trial/multi_step.py` empties `/tests` at the NEXT
# step's verification rather than before its agent, so a gold in `tests/` is readable by a later
# step. Correctness is graded outside, by `campaign.yaml`.
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

# The offline line is INSTRUMENT, identical across arms — a property of the machine, like the
# answer path. Without it an agent spends turns discovering the shape of its own sandbox.
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

_TASK_TOML_2STEP = """schema_version = "1.4"

[metadata]

[verifier]
timeout_sec = 300.0

[agent]
timeout_sec = 900.0

[environment]
build_timeout_sec = 600.0

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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


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
    # `steps/<name>/workdir/` is Harbor's upload convention: one upload into the agent's cwd,
    # surviving into the answer step, rather than an image layer per task.
    for i, d in enumerate(docs, start=1):
        title = str(d.get("title") or "").strip()
        text = str(d.get("text") or "").strip()
        head = f"[{i}] {title}" if title else f"[{i}]"
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


def _panel_yaml(tasks: list[dict[str, str]]) -> str:
    """`harbor_tasks.yaml` — the panel, inline, one absolute path per task.

    Absolute because Harbor resolves `TaskConfig.path` against the process CWD and this file is
    generated per machine. `question` and `answer` ride each task: `query` is the task ID, so a
    judge falling back to it would grade against an identifier, and the declared answer is what
    makes this a labelled bank."""
    # json.dumps every scalar — a question or gold may hold a colon, quote or newline, and a
    # hand-rolled quoting rule would be a second YAML writer with one user.
    lines = [
        "# GENERATED by scripts/build_sealqa_tasks.py — do not hand-edit, and do not commit.",
        f"# {HF_DATASET} config={HF_CONFIG} split={HF_SPLIT} revision={HF_REVISION}",
        "",
        "reward_key: reward",
        "",
        "agent:",
        "  name: terminus-2",
        "  environment: docker",
        # NO `kwargs:`, and the absence is the declaration. `harbor.py` resolves the two sources as
        # `dict(agent_cfg["kwargs"]).update(payload["agent_kwargs"])`, so the overlay in
        # `pipeline.yaml::nodes.agent.config` ALWAYS wins and anything written here is dead the
        # moment it is read — while reading as authoritative, and regenerating itself under every
        # dataset, this file being gitignored. Name and environment are its to declare; tunables
        # are not.
        "",
        "tasks:",
    ]
    for t in tasks:
        lines.append(f"  - id: {json.dumps(t['id'])}")
        lines.append(f"    path: {json.dumps(t['path'])}")
        lines.append(f"    question: {json.dumps(t['question'])}")
        lines.append(f"    answer: {json.dumps(t['answer'])}")
    return "\n".join(lines) + "\n"


def build(name: str) -> None:
    from datasets import load_dataset

    size, two_step = _sizes_from_name(name)
    dataset_dir = DATASETS_DIR / name
    if not dataset_dir.is_dir():
        raise SystemExit(f"{dataset_dir} does not exist — the dataset's config dir is committed.")

    ds = load_dataset(HF_DATASET, name=HF_CONFIG, split=HF_SPLIT, revision=HF_REVISION)
    column = f"{size}_docs"

    tasks_root = dataset_dir / "tasks"
    # Rebuilt whole rather than merged into: a stale directory from an earlier revision would be
    # a task Harbor still runs and nothing compares against the pin.
    if tasks_root.exists():
        shutil.rmtree(tasks_root)

    tasks: list[dict[str, str]] = []
    for i, row in enumerate(ds):
        docs = list(row.get(column) or [])
        if not docs:
            continue
        task_id = f"longseal-{i:03d}"
        task_dir = tasks_root / task_id
        question = str(row["question"]).strip()
        if two_step:
            _write_two_step(task_dir, question, docs)
        else:
            _write_single_step(task_dir, question, docs)
        tasks.append(
            {
                "id": task_id,
                "path": str(task_dir.resolve()),
                "question": question,
                "answer": str(row["answer"]).strip(),
            }
        )

    _write(dataset_dir / "harbor_tasks.yaml", _panel_yaml(tasks))
    print(
        f"{name}: {len(tasks)} tasks ({'two-step' if two_step else 'single-step'}, "
        f"{size}-doc haystack) -> {tasks_root}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "names",
        nargs="*",
        help="dataset directory names; default is every sealqa-longseal-* dir that exists",
    )
    args = parser.parse_args()
    names = args.names or sorted(
        p.name for p in DATASETS_DIR.iterdir() if _NAME_RE.match(p.name) and p.is_dir()
    )
    if not names:
        raise SystemExit("no sealqa-longseal-* dataset directory found under datasets/.")
    for name in names:
        build(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
