"""Manually refresh the cached LiteLLM model price table.

Usage: ``python scripts/refresh_rates.py``

Same code path the CLI runs at every ``optimize`` start — exposed as a
script for pre-warming the cache (e.g. before going offline) or for CI.
Exits 0 on a successful fetch, 1 on network/parse failure.
"""

from __future__ import annotations

import logging
import sys

from promptpotter.shared.spend import CACHE_PATH, UPSTREAM_URL, refresh_rates


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"fetching {UPSTREAM_URL} ...")
    ok = refresh_rates(force=True)
    if not ok:
        print(f"refresh failed; cache at {CACHE_PATH} (if any) is unchanged", file=sys.stderr)
        return 1
    print(f"cache written to {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
