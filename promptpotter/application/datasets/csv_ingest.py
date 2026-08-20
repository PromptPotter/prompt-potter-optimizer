"""Tabular blob → ``Table`` → ``list[Sample]`` for the operator-uploaded ingest path. The
HuggingFace loaders own the signed-download path; this one owns untrusted file content."""

from __future__ import annotations

import csv
import io
import json
import random
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.domain.pipeline_schema import CANDIDATE_LIBRARY_FILE
from promptpotter.domain.sample import Sample
from promptpotter.shared.errors import PayloadInvalidError

MAX_SAMPLES = 50_000
"""Per-upload row cap. Prevents a 200 MB CSV from exhausting the draft tempdir.

Sized one order of magnitude above any benchmark we ship; revisit when a
genuine large-dataset onboarding flow surfaces.
"""

# Top-level JSON object keys that may wrap a record list ({"data": [...]}).
_JSON_RECORD_KEYS = ("data", "rows", "items", "records", "examples")


class IngestError(PayloadInvalidError):
    """``reason`` is a stable code declared in ``m12-api-openapi.yaml::ErrorEnvelope``. A
    :class:`PayloadInvalidError`, so the central ``PotterError`` handler maps it with no arm."""

    code = "ingest_failed"

    def __init__(self, *, reason: str, message: str) -> None:
        super().__init__(message, details={"reason": reason})
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Table:
    """Header-agnostic: no column has been read as input or target yet, and whitespace-only
    cells are kept as-is."""

    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


# Operator-facing list of what ingest reads — kept in one place so the picker
# hint, the unsupported-format error, and the docs stay in sync.
SUPPORTED_FORMATS_HINT = "Drop a CSV, TSV, JSON, JSONL, or Excel (.xlsx) file."


def format_from_filename(filename: str) -> str:
    """An unrecognised extension maps to ``"unknown"`` so :func:`read_tabular` refuses it with
    a file-type message instead of a UTF-8 decode error."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("csv", "txt", ""):
        return "csv"
    if ext in ("tsv", "tab"):
        return "tsv"
    if ext == "json":
        return "json"
    if ext in ("jsonl", "ndjson"):
        return "jsonl"
    if ext in ("xlsx", "xlsm"):
        return "xlsx"
    return "unknown"


def read_tabular(blob: bytes, *, fmt: str = "csv") -> Table:
    """Header-agnostic — column identity is the origin check-in's job, gated at mint, not here.
    UTF-8 with BOM tolerated (Excel CSV exports ship one)."""
    if fmt == "xlsx":
        return _read_xlsx(blob)
    if fmt in ("csv", "tsv"):
        return _read_delimited(_decode(blob), "\t" if fmt == "tsv" else ",")
    if fmt == "json":
        return _read_json_records(_decode(blob))
    if fmt == "jsonl":
        return _read_jsonl(_decode(blob))
    raise IngestError(
        reason="unsupported_format",
        message=f"That file type isn't supported. {SUPPORTED_FORMATS_HINT}",
    )


def _decode(blob: bytes) -> str:
    try:
        return blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise IngestError(
            reason="unsupported_format",
            message=f"That file isn't readable as text (expected UTF-8). {SUPPORTED_FORMATS_HINT}",
        ) from None


def _read_delimited(text: str, delimiter: str) -> Table:
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        fieldnames = list(reader.fieldnames or [])
    except csv.Error as exc:
        raise IngestError(reason="bad_csv", message=f"Parse failed: {exc}") from None

    if not fieldnames:
        raise IngestError(reason="bad_csv", message="File has no header row.")

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
        raise IngestError(reason="bad_csv", message=f"Parse failed: {exc}") from None

    if not rows:
        raise IngestError(reason="empty", message="File has a header row but zero data rows.")

    return Table(headers=tuple(fieldnames), rows=tuple(rows))


def _read_json_records(text: str) -> Table:
    try:
        doc: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestError(reason="bad_json", message=f"JSON parse failed: {exc}") from None

    records = _records_from_json(doc)
    return _records_to_table(records)


def _records_from_json(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if isinstance(doc, dict):
        for key in _JSON_RECORD_KEYS:
            val = doc.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
        # Object-of-columns: every value a list, all the same length → transpose.
        cols = {k: v for k, v in doc.items() if isinstance(v, list)}
        if cols and len(cols) == len(doc):
            lengths = {len(v) for v in cols.values()}
            if len(lengths) == 1:
                n = lengths.pop()
                return [{k: cols[k][i] for k in cols} for i in range(n)]
    raise IngestError(
        reason="bad_json",
        message="JSON must be a list of objects, an object wrapping one, or an object of equal-length columns.",
    )


def _read_jsonl(text: str) -> Table:
    records: list[dict[str, Any]] = []
    for ordinal, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise IngestError(
                reason="bad_json", message=f"Line {ordinal}: JSON parse failed: {exc}"
            ) from None
        if isinstance(obj, dict):
            records.append(obj)
    return _records_to_table(records)


def _records_to_table(records: list[dict[str, Any]]) -> Table:
    headers: list[str] = []
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for rec in records:
        for key in rec:
            if key not in seen:
                seen.add(key)
                headers.append(key)
        cells = {key: _stringify_cell(rec.get(key)) for key in rec}
        if not any(cells.values()):
            continue
        if len(rows) >= MAX_SAMPLES:
            raise IngestError(
                reason="too_large",
                message=f"Upload exceeds the per-file cap of {MAX_SAMPLES} rows.",
            )
        rows.append(cells)

    if not headers:
        raise IngestError(reason="bad_json", message="No object records found in the upload.")
    if not rows:
        raise IngestError(reason="empty", message="Upload parsed but has zero data rows.")

    # Backfill every row to the full header set so the Table is rectangular.
    full = [{h: row.get(h, "") for h in headers} for row in rows]
    return Table(headers=tuple(headers), rows=tuple(full))


def _stringify_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _read_xlsx(blob: bytes) -> Table:
    """Gated by ``settings.HARDENED_MODE`` — Excel is a macro / zip-bomb / XXE vector, so a
    hardened deployment refuses rather than parses it."""
    from promptpotter.config.settings import settings

    if settings.HARDENED_MODE:
        raise IngestError(
            reason="hardened_blocked",
            message="Excel uploads are disabled in hardened mode — export your sheet to CSV and drop it again.",
        )

    try:
        import openpyxl  # lazy: an extra since 0.8.11, and off the hot CSV/JSON path
    except ModuleNotFoundError:
        raise IngestError(
            reason="unsupported_format",
            message="This install cannot read .xlsx — `pip install promptpotter[excel]`, "
            "or export the sheet to CSV and drop it again.",
        ) from None

    try:
        wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a grab-bag of errors on bad blobs
        raise IngestError(reason="bad_csv", message=f"Excel parse failed: {exc}") from None
    try:
        ws = wb.active
        if ws is None:
            raise IngestError(reason="bad_csv", message="Excel workbook has no active sheet.")
        rows_iter = ws.iter_rows(values_only=True)
        header_row = next(rows_iter, None)
        if not header_row:
            raise IngestError(reason="bad_csv", message="Excel sheet has no header row.")
        headers = [str(c).strip() if c is not None else "" for c in header_row]
        records: list[dict[str, Any]] = []
        for cells in rows_iter:
            records.append({headers[i]: cells[i] for i in range(min(len(headers), len(cells)))})
        return _records_to_table(records)
    finally:
        wb.close()


def materialize_samples(
    table: Table, *, query_col: str, ground_truth_col: str, order_seed: str | None
) -> list[Sample]:
    """Belt-and-suspenders: the origin gate should already have rejected a column that is not a
    member of ``table.headers``.

    ``order_seed`` decorrelates the minted ``Sample.id`` sequence from the upload's row order;
    ``None`` keeps the file as delivered. An uploaded bank is routinely GROUPED BY LABEL, and the
    round-subset ranker ties across never-measured samples and breaks that tie on ascending id — so
    a label-ordered id sequence hands each round a disjoint single-label panel and cross-round
    accuracy stops being a series. Seeded rather than random: ids are minted ONCE here and read back
    from the committed ``cache.json`` forever after, and ``sample_id`` is part of the measurement
    cache key, so the permutation must be reproducible from what is on disk. Re-seeding an EXISTING
    dataset is therefore a re-cut, not an edit — it repoints every cached row at a different
    question, so it belongs on a new dataset rather than in place.
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

    # Validated in FILE order so a rejection quotes the row number the operator sees in their own
    # file; the permutation below only decides which id each surviving row is minted under.
    pairs: list[tuple[str, str]] = []
    for ordinal, row in enumerate(table.rows, start=1):
        query = row.get(query_col, "")
        ground_truth = row.get(ground_truth_col, "")
        if not query:
            raise IngestError(reason="bad_csv", message=f"Row {ordinal}: empty {query_col!r} cell.")
        if not ground_truth:
            raise IngestError(
                reason="bad_csv", message=f"Row {ordinal}: empty {ground_truth_col!r} cell."
            )
        pairs.append((query, ground_truth))
    if order_seed is not None:
        random.Random(order_seed).shuffle(pairs)
    return [
        Sample(id=i, query=query, ground_truth=ground_truth)
        for i, (query, ground_truth) in enumerate(pairs)
    ]


def _dedup_terms(values: Iterable[str]) -> tuple[str, ...]:
    """The shared shape of a candidate library however it is sourced — a dropped file or a
    dataset column — so the two never diverge."""
    seen: dict[str, None] = {}
    for value in values:
        term = value.strip()
        if term and term != "--":
            seen.setdefault(term, None)
    return tuple(seen)


def parse_candidate_library(blob: bytes, filename: str) -> tuple[str, ...]:
    """An UNQUOTED-comma ``.csv`` splits mid-entry, which is why the operator-facing hint steers
    comma-laden lists to per-line ``.txt`` or Excel rather than raw CSV."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("txt", ""):
        return _dedup_terms(_decode(blob).splitlines())
    table = read_tabular(blob, fmt=format_from_filename(filename))
    return _dedup_terms(row.get(table.headers[0], "") for row in table.rows)


def read_candidate_library_file(dataset_config_dir: Path) -> tuple[str, ...]:
    """The single file-read seam, so the runtime term-index union and the reopened draft parse
    identically. Absent file → ``()``: the pool is the answers alone, degenerate but runnable."""
    path = dataset_config_dir / CANDIDATE_LIBRARY_FILE
    if not path.is_file():
        return ()
    return _dedup_terms(path.read_text(encoding="utf-8").splitlines())


def candidate_library_from_rows(rows: Iterable[dict[str, Any]], column: str) -> tuple[str, ...]:
    """The alternative to a file drop when the targets already live in the data. Same dedup
    shape as :func:`parse_candidate_library`."""
    return _dedup_terms(str(row.get(column, "")) for row in rows)


MAX_ENUMERATED_LABELS = 40
"""Upper bound on a closed label set. Above this the target column reads as
open-ended (free text / high-cardinality id), not a fixed taxonomy worth
enumerating verbatim into the origin prompt."""


def closed_label_set(
    values: Iterable[str], *, n_rows: int, max_enum: int = MAX_ENUMERATED_LABELS
) -> tuple[str, ...] | None:
    """``None`` for an open-ended column — there is no small fixed answer space to enumerate.
    The single cardinality gate is the closed-vs-open detector; no scorer special-casing."""
    distinct = sorted({v.strip() for v in values if v and v.strip()})
    if not 2 <= len(distinct) <= max_enum:
        return None
    if len(distinct) > n_rows // 2:
        return None
    return tuple(distinct)


__all__ = [
    "MAX_SAMPLES",
    "IngestError",
    "Table",
    "candidate_library_from_rows",
    "closed_label_set",
    "format_from_filename",
    "materialize_samples",
    "parse_candidate_library",
    "read_candidate_library_file",
    "read_tabular",
]
