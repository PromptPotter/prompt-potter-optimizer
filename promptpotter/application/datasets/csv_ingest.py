"""Tabular blob → ``Table`` → ``list[Sample]`` for the chat-first ingest path.

Two stages, deliberately split (per ``docs/specs/m10-origin-resolution-checkin.md``):

* :func:`read_tabular` decodes + parses an uploaded blob into a header-agnostic
  :class:`Table` (headers + raw rows). It does **not** require any particular
  column names — column identity is the origin-resolution job, gated at mint,
  not at upload. Slice 1 supports CSV; future slices (XLSX first sheet,
  Parquet, JSONL) add a branch on ``fmt`` here.
* :func:`materialize_samples` turns a :class:`Table` into ``Sample`` rows once
  the input/target column mapping is confirmed. Run only at commit time.

The reason this isn't tucked into ``loaders.py``: the HuggingFace loaders own
the install-global benchmark download path; this module owns the
operator-uploaded blob path. Different trust boundary (untrusted file content
vs. signed HF datasets), different failure shape (``IngestError`` returns a
structured reason for the 422 wire response).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from promptpotter.domain.sample import Sample

MAX_SAMPLES = 50_000
"""Per-upload row cap. Prevents a 200 MB CSV from exhausting the draft tempdir.

Sized one order of magnitude above any benchmark we ship; revisit when a
genuine large-dataset onboarding flow surfaces.
"""


@dataclass(frozen=True, slots=True)
class IngestError(Exception):
    """Structured parse / shape failure ready for a 422 wire response.

    ``reason`` is a stable code declared in
    ``docs/specs/m12-api-openapi.yaml::ErrorEnvelope`` (``ingest_failed``
    detail): ``bad_csv`` | ``empty`` | ``too_large`` | ``missing_column``.
    """

    reason: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class Table:
    """A parsed tabular upload, header-agnostic.

    ``rows`` carry every column keyed by its header; no column has been
    interpreted as input or target yet. Whitespace-only cells are kept as-is
    (the column mapping decides which cells matter).
    """

    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def read_tabular(blob: bytes, *, fmt: str = "csv") -> Table:
    """Decode + parse an uploaded blob into a :class:`Table`; raise on shape failure.

    Header-agnostic: any column names are accepted. UTF-8 with BOM tolerated
    (Excel exports often ship one). Mixed line endings handled by
    ``csv.DictReader``. Rows where every cell is blank are dropped (so a
    trailing newline doesn't tank a valid upload). Raises :class:`IngestError`
    on undecodable bytes, unparseable CSV, a missing header row, more than
    :data:`MAX_SAMPLES` rows, or zero data rows.
    """
    if fmt != "csv":
        raise IngestError(reason="bad_csv", message=f"Unsupported upload format: {fmt!r}.")

    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestError(reason="bad_csv", message=f"CSV must be UTF-8 encoded: {exc}") from None

    try:
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = list(reader.fieldnames or [])
    except csv.Error as exc:
        raise IngestError(reason="bad_csv", message=f"CSV parse failed: {exc}") from None

    if not fieldnames:
        raise IngestError(reason="bad_csv", message="CSV has no header row.")

    rows: list[dict[str, str]] = []
    try:
        for row in reader:
            cells = {name: (row.get(name) or "").strip() for name in fieldnames}
            if not any(cells.values()):
                continue  # skip blank trailing rows
            if len(rows) >= MAX_SAMPLES:
                raise IngestError(
                    reason="too_large",
                    message=f"Upload exceeds the per-file cap of {MAX_SAMPLES} rows.",
                )
            rows.append(cells)
    except csv.Error as exc:
        raise IngestError(reason="bad_csv", message=f"CSV parse failed: {exc}") from None

    if not rows:
        raise IngestError(reason="empty", message="CSV has a header row but zero data rows.")

    return Table(headers=tuple(fieldnames), rows=tuple(rows))


def materialize_samples(table: Table, *, query_col: str, ground_truth_col: str) -> list[Sample]:
    """Project a :class:`Table` into ``Sample`` rows using a confirmed column mapping.

    Both ``query_col`` and ``ground_truth_col`` must be members of
    ``table.headers`` (raises ``missing_column`` otherwise — the origin gate
    should have caught this, so this is a belt-and-suspenders guard). Each row
    contributes one ``Sample`` with a positional ``id``; a row missing either
    mapped cell is a ``bad_csv`` failure naming its data-row ordinal.
    """
    for label, col in (("query", query_col), ("ground_truth", ground_truth_col)):
        if col not in table.headers:
            raise IngestError(
                reason="missing_column",
                message=(
                    f"{label} column {col!r} is not one of the uploaded headers "
                    f"{list(table.headers) or '<none>'}."
                ),
            )

    samples: list[Sample] = []
    for ordinal, row in enumerate(table.rows, start=1):
        query = row.get(query_col, "")
        ground_truth = row.get(ground_truth_col, "")
        if not query:
            raise IngestError(reason="bad_csv", message=f"Row {ordinal}: empty {query_col!r} cell.")
        if not ground_truth:
            raise IngestError(
                reason="bad_csv", message=f"Row {ordinal}: empty {ground_truth_col!r} cell."
            )
        samples.append(Sample(id=len(samples), query=query, ground_truth=ground_truth))
    return samples


__all__ = ["MAX_SAMPLES", "IngestError", "Table", "materialize_samples", "read_tabular"]
