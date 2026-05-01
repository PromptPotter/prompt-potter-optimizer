"""Cross-cycle review CLI — read-only pivot over a tenant's campaigns.

Renders the same body as ``library/runs.md`` to stdout: every cycle
grouped by ``l1_generate_hash``, with a ``## Sweep view`` section when
sweep-mode cycles exist. ``proxy_lift_corr`` (Spearman) reported in the
footer when ≥4 paired ``l1_generate_hash``es exist across both modes —
the validity check on M10's "round 1 predicts full cycle" hypothesis.

Stdout only. Not a write verb.

Examples::

    python scripts/ppot_review.py
    python scripts/ppot_review.py --tenant default --projects-root ./projects
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from promptpotter.application.leaderboard import (  # noqa: E402
    build_leaderboard_rows,
    compute_proxy_lift_corr,
    format_runs_md,
)
from promptpotter.infrastructure.store import build_stores  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--projects-root",
        default="projects",
        help="Tenant root containing sessions/ + campaigns/ (default: projects)",
    )
    parser.add_argument(
        "--datasets-root",
        default="datasets",
        help="Datasets root for store init (default: datasets)",
    )
    parser.add_argument("--tenant", default="default", help="Tenant id (default: default)")
    args = parser.parse_args()

    projects_root = Path(args.projects_root).resolve()
    datasets_root = Path(args.datasets_root).resolve()
    if not projects_root.exists():
        print(f"projects root not found: {projects_root}", file=sys.stderr)
        return 2

    stores = build_stores(projects_root, datasets_root=datasets_root, tenant_id=args.tenant)
    rows = build_leaderboard_rows(stores)
    if not rows:
        print("(no cycles found)")
        return 0

    print(format_runs_md(rows))

    corr, n_pairs = compute_proxy_lift_corr(rows)
    if corr is not None:
        verdict = "trust" if corr >= 0.6 else ("pair" if corr >= 0.4 else "suspend")
        print(f"proxy_lift_corr: {corr:+.3f}  (n_pairs={n_pairs}, verdict={verdict})")
    else:
        print(f"proxy_lift_corr: insufficient data (n_pairs={n_pairs}, need ≥ 4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
