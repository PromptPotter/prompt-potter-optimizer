"""Render review.md for any cycle dir, on demand.

Standalone wrapper around `promptpotter.application.review.render_review_md`
so the meta-campaign skill (and the operator) can backfill review.md when
the in-loop renderer didn't fire — e.g. on forks that inherit a parent's
final block, or when emitter was None during finalize.

Usage:
    python scripts/render_review.py <cycle_dir> \
        [--audit-dir <other_cycle_dir>/.runtime/cache/rounds]

If --audit-dir is omitted, audits are read from <cycle_dir>/.runtime/cache/rounds.
Forks that share their parent's audits can point --audit-dir at the parent.

Writes review.md alongside index.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from promptpotter.application.review import render_review_md


def _load_rounds(cycle_dir: Path) -> list[dict]:
    rounds_dir = cycle_dir / "rounds"
    if not rounds_dir.exists():
        return []
    files = sorted(rounds_dir.glob("round_*.json"))
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


def _load_audits(audit_dir: Path, n_rounds: int) -> list[dict | None]:
    out: list[dict | None] = []
    for i in range(1, n_rounds + 1):
        f = audit_dir / f"round_{i:04d}.json"
        if f.exists():
            out.append(json.loads(f.read_text(encoding="utf-8")))
        else:
            out.append(None)
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cycle_dir", type=Path)
    parser.add_argument("--audit-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    cycle_dir: Path = args.cycle_dir.resolve()
    if not (cycle_dir / "index.json").exists():
        print(f"no index.json at {cycle_dir}", file=sys.stderr)
        return 2

    index = json.loads((cycle_dir / "index.json").read_text(encoding="utf-8"))
    rounds = _load_rounds(cycle_dir)
    audit_dir = (args.audit_dir or (cycle_dir / ".runtime" / "cache" / "rounds")).resolve()
    audits = _load_audits(audit_dir, len(rounds)) if audit_dir.exists() else [None] * len(rounds)

    ctx: list[str] = []
    # Best-effort task_context — fork inherits parent's so we don't always have it.
    # Pull from last round's opt_search_point.task_context when present.
    if rounds:
        tc = (rounds[-1].get("opt_search_point") or {}).get("task_context") or {}
        if isinstance(tc, dict):
            for key in ("pipeline_purpose", "optimization_goals", "key_challenges"):
                val = tc.get(key)
                if isinstance(val, str) and val.strip():
                    ctx.append(val)
                elif isinstance(val, list):
                    ctx.extend(str(v) for v in val if v)

    content = render_review_md(
        index,
        rounds,
        round_audits=audits,
        context_object=ctx,
    )
    out_path = cycle_dir / "review.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"wrote {out_path} ({len(rounds)} rounds, {sum(1 for a in audits if a)} audits)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
