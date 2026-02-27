#!/usr/bin/env python3
"""
One-shot cleanup script for Langfuse cloud.

Deletes junk traces and old dataset items (null expectedOutput) via the
Langfuse REST API. Safe by default — prints what it would delete without
actually deleting. Pass --execute to perform deletions.

Usage:
    python scripts/langfuse_cleanup.py              # dry-run
    python scripts/langfuse_cleanup.py --execute    # actually delete

Environment variables required:
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
    LANGFUSE_HOST (default: https://cloud.langfuse.com)
"""

import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")

DATASET_NAME = "termnorm_ground_truth"

# Trace names to DELETE (exact match or prefix match for round_*)
JUNK_TRACE_NAMES = {"a", "b", "evaluate", "grow", "candidate_ranker",
                    "web_search", "score_fix_verification", "diagnostic_test",
                    "llm_call"}

# Trace name prefixes to DELETE
JUNK_TRACE_PREFIXES = ("round_",)

# Trace names to KEEP
KEEP_TRACE_NAMES = {"feedback_cycle"}

# Also keep any trace whose name starts with "eval_" (from backfill)
KEEP_TRACE_PREFIXES = ("eval_", "feedback_cycle")


def auth() -> tuple[str, str]:
    """Basic auth tuple for Langfuse REST API."""
    return (PUBLIC_KEY, SECRET_KEY)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def fetch_all_traces() -> list[dict]:
    """Fetch all traces from Langfuse (paginated)."""
    traces: list[dict] = []
    page = 1
    limit = 100

    while True:
        resp = requests.get(
            f"{LANGFUSE_HOST}/api/public/traces",
            params={"page": page, "limit": limit},
            auth=auth(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("data", [])
        traces.extend(batch)

        meta = data.get("meta", {})
        total_pages = meta.get("totalPages", 1)
        print(f"  Fetched traces page {page}/{total_pages} ({len(batch)} items)")

        if page >= total_pages:
            break
        page += 1

    return traces


def fetch_all_dataset_items() -> list[dict]:
    """Fetch all dataset items from a dataset (paginated)."""
    items: list[dict] = []
    page = 1
    limit = 100

    while True:
        resp = requests.get(
            f"{LANGFUSE_HOST}/api/public/dataset-items",
            params={"datasetName": DATASET_NAME, "page": page, "limit": limit},
            auth=auth(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("data", [])
        items.extend(batch)

        meta = data.get("meta", {})
        total_pages = meta.get("totalPages", 1)
        print(f"  Fetched dataset items page {page}/{total_pages} ({len(batch)} items)")

        if page >= total_pages:
            break
        page += 1

    return items


def _delete_with_retry(url: str, max_retries: int = 5) -> bool:
    """DELETE with exponential backoff on 429 rate limits."""
    for attempt in range(max_retries):
        try:
            resp = requests.delete(url, auth=auth(), timeout=30)
            if resp.status_code in (200, 204):
                return True
            if resp.status_code == 429:
                wait = 2 ** attempt  # 1, 2, 4, 8, 16 seconds
                time.sleep(wait)
                continue
            # Other error — log and give up
            print(f"  HTTP {resp.status_code}: {resp.text[:100]}")
            return False
        except requests.exceptions.ReadTimeout:
            wait = 2 ** attempt
            time.sleep(wait)
            continue
        except requests.exceptions.ConnectionError:
            time.sleep(2 ** attempt)
            continue
    return False


def delete_trace(trace_id: str) -> bool:
    """Delete a single trace. Returns True on success."""
    return _delete_with_retry(f"{LANGFUSE_HOST}/api/public/traces/{trace_id}")


def delete_dataset_item(item_id: str) -> bool:
    """Delete a single dataset item. Returns True on success."""
    return _delete_with_retry(f"{LANGFUSE_HOST}/api/public/dataset-items/{item_id}")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def is_junk_trace(name: str | None) -> bool:
    """Return True if the trace should be deleted."""
    if name is None or name.strip() == "":
        return True  # empty-name traces are junk
    if name in JUNK_TRACE_NAMES:
        return True
    for prefix in JUNK_TRACE_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def should_keep_trace(name: str | None) -> bool:
    """Return True if the trace should be explicitly kept."""
    if not name:
        return False
    if name in KEEP_TRACE_NAMES:
        return True
    for prefix in KEEP_TRACE_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# Main cleanup
# ---------------------------------------------------------------------------

def cleanup_traces(*, execute: bool) -> dict[str, int]:
    """Identify and optionally delete junk traces."""
    print("\n=== TRACES ===")
    print("Fetching all traces...")
    traces = fetch_all_traces()
    print(f"Total traces: {len(traces)}")

    to_delete: list[dict] = []
    to_keep: list[dict] = []
    unknown: list[dict] = []

    # Count by name for summary
    delete_by_name: dict[str, int] = {}
    keep_by_name: dict[str, int] = {}

    for t in traces:
        name = t.get("name") or ""
        tid = t.get("id", "")

        if should_keep_trace(name):
            to_keep.append(t)
            keep_by_name[name] = keep_by_name.get(name, 0) + 1
        elif is_junk_trace(name):
            to_delete.append(t)
            display_name = name if name else "(empty)"
            delete_by_name[display_name] = delete_by_name.get(display_name, 0) + 1
        else:
            unknown.append(t)

    # Print summary
    print(f"\nTraces to DELETE: {len(to_delete)}")
    for name, count in sorted(delete_by_name.items(), key=lambda x: -x[1]):
        print(f"  {name:30s} {count:>4}")

    print(f"\nTraces to KEEP: {len(to_keep)}")
    for name, count in sorted(keep_by_name.items(), key=lambda x: -x[1]):
        print(f"  {name:30s} {count:>4}")

    if unknown:
        print(f"\nUNCLASSIFIED (will keep): {len(unknown)}")
        for t in unknown[:10]:
            print(f"  {t.get('name', '???'):30s} id={t.get('id', '')[:12]}")
        if len(unknown) > 10:
            print(f"  ... and {len(unknown) - 10} more")

    # Execute deletions
    deleted = 0
    failed = 0
    if execute and to_delete:
        print(f"\nDeleting {len(to_delete)} traces (with rate-limit backoff)...")
        for i, t in enumerate(to_delete):
            tid = t.get("id", "")
            if delete_trace(tid):
                deleted += 1
            else:
                failed += 1
                print(f"  FAILED to delete trace {tid}")

            if (i + 1) % 25 == 0:
                print(f"  [{i + 1}/{len(to_delete)}] deleted={deleted} failed={failed}")

            # Pace requests: ~2/sec to stay under rate limit
            time.sleep(0.5)

        print(f"\nDeleted: {deleted}, Failed: {failed}")
    elif not execute and to_delete:
        print("\n(dry-run — pass --execute to actually delete)")

    return {"total": len(traces), "to_delete": len(to_delete),
            "kept": len(to_keep), "deleted": deleted, "failed": failed}


def cleanup_dataset_items(*, execute: bool) -> dict[str, int]:
    """Identify and optionally delete dataset items with null expectedOutput."""
    print(f"\n=== DATASET ITEMS ({DATASET_NAME}) ===")
    print("Fetching all dataset items...")
    items = fetch_all_dataset_items()
    print(f"Total dataset items: {len(items)}")

    to_delete: list[dict] = []
    to_keep: list[dict] = []

    for item in items:
        expected = item.get("expectedOutput")
        if expected is None:
            to_delete.append(item)
        else:
            to_keep.append(item)

    print(f"\nItems to DELETE (null expectedOutput): {len(to_delete)}")
    if to_delete:
        # Show a few examples
        for item in to_delete[:5]:
            input_data = item.get("input", {})
            query = input_data.get("query", str(input_data)[:50]) if isinstance(input_data, dict) else str(input_data)[:50]
            print(f"  query: {query}")
        if len(to_delete) > 5:
            print(f"  ... and {len(to_delete) - 5} more")

    print(f"Items to KEEP (has expectedOutput): {len(to_keep)}")

    # Execute deletions
    deleted = 0
    failed = 0
    if execute and to_delete:
        print(f"\nDeleting {len(to_delete)} dataset items (with rate-limit backoff)...")
        for i, item in enumerate(to_delete):
            iid = item.get("id", "")
            if delete_dataset_item(iid):
                deleted += 1
            else:
                failed += 1
                print(f"  FAILED to delete item {iid}")

            if (i + 1) % 20 == 0:
                print(f"  [{i + 1}/{len(to_delete)}] deleted={deleted} failed={failed}")

            time.sleep(0.5)

        print(f"\nDeleted: {deleted}, Failed: {failed}")
    elif not execute and to_delete:
        print("\n(dry-run — pass --execute to actually delete)")

    return {"total": len(items), "to_delete": len(to_delete),
            "kept": len(to_keep), "deleted": deleted, "failed": failed}


def main():
    parser = argparse.ArgumentParser(
        description="Clean up junk traces and dataset items from Langfuse cloud",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform deletions (default is dry-run)",
    )
    parser.add_argument(
        "--traces-only",
        action="store_true",
        help="Only clean up traces, skip dataset items",
    )
    parser.add_argument(
        "--items-only",
        action="store_true",
        help="Only clean up dataset items, skip traces",
    )
    args = parser.parse_args()

    # Validate credentials
    if not PUBLIC_KEY or not SECRET_KEY:
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set")
        print("Set them in .env or as environment variables")
        sys.exit(1)

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"Langfuse Cleanup [{mode}]")
    print(f"Host: {LANGFUSE_HOST}")
    print(f"Dataset: {DATASET_NAME}")

    trace_stats = {}
    item_stats = {}

    if not args.items_only:
        trace_stats = cleanup_traces(execute=args.execute)

    if not args.traces_only:
        item_stats = cleanup_dataset_items(execute=args.execute)

    # Final summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    if trace_stats:
        print(f"Traces:  {trace_stats.get('total', 0)} total, "
              f"{trace_stats.get('to_delete', 0)} junk, "
              f"{trace_stats.get('kept', 0)} kept, "
              f"{trace_stats.get('deleted', 0)} deleted")
    if item_stats:
        print(f"Items:   {item_stats.get('total', 0)} total, "
              f"{item_stats.get('to_delete', 0)} null expectedOutput, "
              f"{item_stats.get('kept', 0)} kept, "
              f"{item_stats.get('deleted', 0)} deleted")

    if not args.execute and (trace_stats.get("to_delete", 0) or item_stats.get("to_delete", 0)):
        print("\nRe-run with --execute to perform deletions.")


if __name__ == "__main__":
    main()
