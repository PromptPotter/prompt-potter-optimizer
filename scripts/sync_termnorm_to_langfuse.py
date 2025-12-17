#!/usr/bin/env python3
"""
Sync TermNorm dataset to Langfuse cloud.

Fetches the full dataset from TermNorm API and uploads it to Langfuse
as a dataset with items for prompt optimization workflows.

Usage:
    python scripts/sync_termnorm_to_langfuse.py

Environment variables required:
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
"""

import os
import sys
import httpx
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def get_langfuse_client():
    """Initialize Langfuse client from environment."""
    from langfuse import Langfuse

    return Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    )


def fetch_termnorm_dataset(
    base_url: str = "http://127.0.0.1:8000",
    dataset_name: str = "termnorm_ground_truth",
) -> dict:
    """Fetch dataset from TermNorm API."""
    url = f"{base_url}/experiments/datasets/{dataset_name}"
    print(f"Fetching dataset from {url}...")

    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()
    data = response.json()

    # TermNorm API returns {"dataset_name": str, "items": list}
    return {
        "dataset_name": data.get("dataset_name", dataset_name),
        "items": data.get("items", []),
        "total_count": len(data.get("items", [])),
    }


def sync_to_langfuse(
    termnorm_data: dict,
    langfuse_dataset_name: str = "termnorm_ground_truth",
):
    """Upload TermNorm dataset to Langfuse cloud."""
    langfuse = get_langfuse_client()

    items = termnorm_data.get("items", [])
    total_count = termnorm_data.get("total_count", len(items))

    print(f"Found {len(items)} items (total: {total_count})")

    # Create or get dataset in Langfuse
    print(f"Creating/updating dataset '{langfuse_dataset_name}' in Langfuse...")
    dataset = langfuse.create_dataset(
        name=langfuse_dataset_name,
        description=f"TermNorm ground truth dataset - synced {datetime.now().isoformat()}",
        metadata={
            "source": "termnorm_api",
            "synced_at": datetime.now().isoformat(),
            "total_items": total_count,
        },
    )

    print(f"Dataset created/updated: {dataset.name}")

    # Upload each item
    success_count = 0
    error_count = 0

    for i, item in enumerate(items):
        try:
            # Extract fields from TermNorm item
            item_id = item.get("id", f"item_{i}")
            input_data = item.get("input", {})
            expected_output = item.get("expected_output")
            metadata = item.get("metadata", {})

            # Create dataset item in Langfuse
            langfuse.create_dataset_item(
                dataset_name=langfuse_dataset_name,
                input=input_data,
                expected_output=expected_output,
                metadata={
                    **metadata,
                    "termnorm_id": item_id,
                    "source_dataset": "termnorm_ground_truth",
                },
            )
            success_count += 1

            if (i + 1) % 10 == 0:
                print(f"  Uploaded {i + 1}/{len(items)} items...")

        except Exception as e:
            error_count += 1
            print(f"  Error uploading item {item_id}: {e}")

    # Flush to ensure all items are sent
    langfuse.flush()

    print(f"\nSync complete!")
    print(f"  Successfully uploaded: {success_count}")
    print(f"  Errors: {error_count}")
    print(f"\nView dataset at: https://cloud.langfuse.com")

    return {"success": success_count, "errors": error_count}


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Sync TermNorm dataset to Langfuse")
    parser.add_argument(
        "--termnorm-url",
        default="http://127.0.0.1:8000",
        help="TermNorm API base URL",
    )
    parser.add_argument(
        "--dataset-name",
        default="termnorm_ground_truth",
        help="Dataset name in both TermNorm and Langfuse",
    )

    args = parser.parse_args()

    # Verify Langfuse credentials
    if not os.getenv("LANGFUSE_SECRET_KEY"):
        print("Error: LANGFUSE_SECRET_KEY not set in environment")
        sys.exit(1)

    # Fetch from TermNorm
    try:
        data = fetch_termnorm_dataset(
            base_url=args.termnorm_url,
            dataset_name=args.dataset_name,
        )
    except httpx.HTTPError as e:
        print(f"Error fetching from TermNorm: {e}")
        sys.exit(1)

    # Sync to Langfuse
    result = sync_to_langfuse(data, langfuse_dataset_name=args.dataset_name)

    if result["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
