"""A panel of Harbor task directories built from hub rows into the tenant tier, each directory laid
from the asset template layers its definition names (``assets/panels/``) — a hub row may carry
what no definition in this package may (SealQA's per-row `canary` GUID)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promptpotter.config.paths import PACKAGE_ROOT
from promptpotter.infrastructure.store.dataset_access import MATERIALIZED_PANEL_FILE
from promptpotter.infrastructure.store.io import rmtree_robust, write_bytes

_TEMPLATES = PACKAGE_ROOT / "assets" / "panels"
# A template file named with this field is laid once per document.
_PER_DOCUMENT = "{n}"


def _doc(i: int, d: dict[str, Any]) -> tuple[str, str]:
    title = str(d.get("title") or "").strip()
    return (f"[{i}] {title}" if title else f"[{i}]"), str(d.get("text") or "").strip()


def _template(layers: list[str]) -> dict[str, str]:
    """Each layer's files by their path in a task directory; a later layer's file replaces one."""
    files: dict[str, str] = {}
    for layer in layers:
        root = _TEMPLATES / layer
        if not root.is_dir():
            raise ValueError(f"no panel template layer {layer!r} under {_TEMPLATES}")
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            files[path.relative_to(root).as_posix()] = path.read_bytes().decode("utf-8")
    return files


def _write(path: Path, text: str) -> None:
    # Bytes, so a task's files are LF on every machine: they are what the task image hashes.
    write_bytes(path, text.encode("utf-8"))


def _write_task(
    task_dir: Path, template: dict[str, str], question: str, docs: list[dict[str, Any]]
) -> None:
    pages = [_doc(i, d) for i, d in enumerate(docs, start=1)]
    fields = {
        "question": question,
        "documents": "\n\n".join(f"{head}\n{text}" if text else head for head, text in pages),
    }
    for rel, text in template.items():
        if _PER_DOCUMENT not in rel:
            _write(task_dir / rel, text.format_map(fields))
            continue
        for i, (head, body) in enumerate(pages, start=1):
            page = {**fields, "n": f"{i:02d}", "document": f"{head}\n\n{body}" if body else head}
            _write(task_dir / rel.format_map(page), text.format_map(page))


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


def write_task_panel(
    rows: list[dict[str, Any]],
    out_dir: Path,
    *,
    layers: list[str],
    columns: dict[str, str],
    task_prefix: str,
    provenance: str,
) -> int:
    """One task per row holding documents: `columns` names the row's `question`, `answer` and
    `documents` (a list of ``{title, text}``) columns."""
    template = _template(layers)
    tasks_root = out_dir / "tasks"
    # Rebuilt whole rather than merged into: a stale directory is a task Harbor still runs.
    rmtree_robust(tasks_root)
    tasks: list[dict[str, str]] = []
    for i, row in enumerate(rows):
        haystack = list(row.get(columns["documents"]) or [])
        if not haystack:
            continue
        task_id = f"{task_prefix}-{i:03d}"
        task_dir = tasks_root / task_id
        question = str(row[columns["question"]]).strip()
        _write_task(task_dir, template, question, haystack)
        tasks.append(
            {
                "id": task_id,
                "path": task_dir.relative_to(out_dir).as_posix(),
                "question": question,
                "answer": str(row[columns["answer"]]).strip(),
            }
        )
    _write(out_dir / MATERIALIZED_PANEL_FILE, _panel_yaml(tasks, provenance))
    return len(tasks)
