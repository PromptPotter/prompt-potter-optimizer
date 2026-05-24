"""Manually refresh the cached LiteLLM model price table. Exits 0 on success, 1 on failure."""

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
