"""python -m promptpotter — unified CLI entry point."""

import sys


def main() -> None:
    # "export" is the only namespaced subcommand; everything else is campaign_runner
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        from promptpotter.cli.export_results import main as export_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]  # strip "export"
        export_main()
    else:
        from promptpotter.cli.campaign_runner import main as campaign_main

        campaign_main()


main()
