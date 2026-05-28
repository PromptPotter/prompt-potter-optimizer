"""CSV → ``list[Sample]`` for the M13 chat-first ingest path.

Slice 1 supports CSV only: two required columns ``query`` + ``ground_truth``.
Future slices (XLSX first sheet, Parquet, JSONL) declare their parsers in
this module before the wire endpoint advertises them.

The reason this isn't tucked into ``loaders.py``: the HuggingFace loaders
own the install-global benchmark download path; this module owns the
operator-uploaded blob path. Different trust boundary (untrusted file
content vs. signed HF datasets), different failure shape (``IngestError``
returns a structured reason for the 422 wire response).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from promptpotter.domain.sample import Sample

REQUIRED_COLUMNS: tuple[str, ...] = ("query", "ground_truth")
"""Slice-1 contract: each row carries exactly these two non-empty fields.

Extra columns are ignored by ``Sample`` (``model_config = {"extra": "ignore"}``).
"""

MAX_SAMPLES = 50_000
"""Per-upload row cap. Prevents a 200 MB CSV from exhausting the draft tempdir.

Sized one order of magnitude above any benchmark we ship; revisit when a
genuine large-dataset onboarding flow surfaces.
"""


@dataclass(frozen=True, slots=True)
class IngestError(Exception):
    """Structured parse / shape failure ready for a 422 wire response.

    ``reason`` is a stable code declared in
    ``docs/specs/m12-api-openapi.yaml::POST /datasets/ingest 422``.
    """

    reason: str
    message: str

    def __str__(self) -> str:
        return self.message


def parse_csv_to_samples(blob: bytes, *, source_file: str = "") -> list[Sample]:
    """Decode + parse a CSV blob into Samples; raise :class:`IngestError` on shape failure.

    UTF-8 with BOM tolerated (Excel exports often ship one). Mixed line
    endings handled by ``csv.DictReader``. Whitespace-only fields count as
    missing; empty rows (all fields blank) are skipped silently so a
    trailing newline in the CSV doesn't tank a valid upload.
    """
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestError(
            reason="bad_csv",
            message=f"CSV must be UTF-8 encoded: {exc}",
        ) from None

    try:
        reader = csv.DictReader(io.StringIO(text))
    except csv.Error as exc:
        raise IngestError(reason="bad_csv", message=f"CSV parse failed: {exc}") from None

    fieldnames = list(reader.fieldnames or [])
    missing = [col for col in REQUIRED_COLUMNS if col not in fieldnames]
    if missing:
        raise IngestError(
            reason="missing_column",
            message=(
                f"Required column(s) missing: {', '.join(missing)}. "
                f"Got columns: {fieldnames or '<none>'}."
            ),
        )

    samples: list[Sample] = []
    for row_idx, row in enumerate(reader):
        query = (row.get("query") or "").strip()
        ground_truth = (row.get("ground_truth") or "").strip()
        if not query and not ground_truth:
            continue  # skip blank trailing rows
        if not query:
            raise IngestError(
                reason="bad_csv",
                message=f"Row {row_idx + 2}: empty `query` cell.",
            )
        if not ground_truth:
            raise IngestError(
                reason="bad_csv",
                message=f"Row {row_idx + 2}: empty `ground_truth` cell.",
            )
        if len(samples) >= MAX_SAMPLES:
            raise IngestError(
                reason="too_large",
                message=f"Upload exceeds the per-file cap of {MAX_SAMPLES} rows.",
            )
        samples.append(Sample(id=len(samples), query=query, ground_truth=ground_truth))

    if not samples:
        raise IngestError(
            reason="empty",
            message="CSV has the required columns but zero data rows.",
        )
    _ = source_file  # accepted for caller bookkeeping; not embedded in Sample
    return samples


__all__ = ["MAX_SAMPLES", "REQUIRED_COLUMNS", "IngestError", "parse_csv_to_samples"]
