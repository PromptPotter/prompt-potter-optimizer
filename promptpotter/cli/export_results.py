"""CLI entrypoint for exporting campaign results and supplemental materials.

Usage::

    # Export supplemental materials markdown
    python -m promptpotter export supplemental \\
        --backend-id local --output supplemental.md

    # Export all campaign data as JSON
    python -m promptpotter export json \\
        --backend-id local --output paper_results.json

    # Export specific campaigns
    python -m promptpotter export supplemental \\
        --backend-id local --campaigns campaign_abc,campaign_def \\
        --output supplemental.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from promptpotter.infrastructure.store.project_store import ProjectStore


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m promptpotter export",
        description="Export campaign results for paper supplemental materials.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Shared arguments
    for name, help_text in [
        ("supplemental", "Generate supplemental materials markdown"),
        ("json", "Export all campaign data as JSON"),
    ]:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--backend-id", required=True, help="Backend identifier")
        sp.add_argument("--output", "-o", required=True, help="Output file path")
        sp.add_argument(
            "--campaigns",
            default=None,
            help="Comma-separated campaign IDs (default: all)",
        )
        sp.add_argument(
            "--store-dir",
            default=None,
            help="Project store directory (default: .promptpotter/projects)",
        )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    store = ProjectStore(args.store_dir)
    campaign_ids = args.campaigns.split(",") if args.campaigns else None
    output = Path(args.output)

    # Lazy import to avoid loading scipy at CLI parse time
    from promptpotter.services.campaign.reporting import generate_export_json, generate_supplemental

    if args.command == "supplemental":
        content = generate_supplemental(
            store,
            args.backend_id,
            campaign_ids=campaign_ids,
        )
        output.write_text(content, encoding="utf-8")
        print(f"Supplemental materials written to {output}")

    elif args.command == "json":
        data = generate_export_json(
            store,
            args.backend_id,
            campaign_ids=campaign_ids,
        )
        output.write_text(
            json.dumps(data, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Export JSON written to {output}")


if __name__ == "__main__":
    main()
