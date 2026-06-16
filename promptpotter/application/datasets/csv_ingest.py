"""Tabular blob → ``Table`` → ``list[Sample]`` for the chat-first ingest path.

Two stages, deliberately split (per ``docs/specs/roadmap.md``):

* :func:`read_tabular` decodes + parses an uploaded blob into a header-agnostic
  :class:`Table` (headers + raw rows). It does **not** require any particular
  column names — column identity is the origin-resolution job, gated at mint,
  not at upload. Dispatches on ``fmt`` (derived from the filename by
  :func:`format_from_filename`): CSV, TSV, JSON, JSONL, and XLSX (first sheet).
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
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.domain.pipeline_schema import CANDIDATE_LIBRARY_FILE
from promptpotter.domain.sample import Sample

MAX_SAMPLES = 50_000
"""Per-upload row cap. Prevents a 200 MB CSV from exhausting the draft tempdir.

Sized one order of magnitude above any benchmark we ship; revisit when a
genuine large-dataset onboarding flow surfaces.
"""

# Top-level JSON object keys that may wrap a record list ({"data": [...]}).
_JSON_RECORD_KEYS = ("data", "rows", "items", "records", "examples")


@dataclass(frozen=True, slots=True)
class IngestError(Exception):
    """Structured parse / shape failure ready for a 422 wire response.

    ``reason`` is a stable code declared in
    ``docs/specs/m12-api-openapi.yaml::ErrorEnvelope`` (``ingest_failed``
    detail): ``bad_csv`` | ``bad_json`` | ``empty`` | ``too_large`` |
    ``missing_column`` | ``unsupported_format`` | ``hardened_blocked``.
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


# Operator-facing list of what ingest reads — kept in one place so the picker
# hint, the unsupported-format error, and the docs stay in sync.
SUPPORTED_FORMATS_HINT = "Drop a CSV, TSV, JSON, JSONL, or Excel (.xlsx) file."


def format_from_filename(filename: str) -> str:
    """Map an upload filename to a parse format. A recognised extension →
    its format; ``.txt`` / blank / no extension → ``"csv"`` (best-effort text
    parse so an extensionless CSV still reads); any other extension (``.png``,
    ``.pdf``, …) → ``"unknown"`` so :func:`read_tabular` rejects it with a
    friendly "unsupported file type" message rather than a UTF-8 decode error.
    Recognised: ``csv`` | ``tsv`` | ``json`` | ``jsonl`` | ``xlsx``."""
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
    """Decode + parse an uploaded blob into a :class:`Table`; raise on shape failure.

    Header-agnostic: any column names are accepted; the origin check-in reads the
    columns + sample rows and proposes the input/target mapping. Dispatches on
    ``fmt`` (see :func:`format_from_filename`). UTF-8 with BOM tolerated (Excel
    CSV exports often ship one). Rows where every cell is blank are dropped.
    Raises :class:`IngestError` on an unsupported type, undecodable bytes,
    unparseable content, a missing header row, >:data:`MAX_SAMPLES` rows, or
    zero data rows.
    """
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
    """UTF-8 (BOM-tolerant) decode for text formats; structured error otherwise.

    A decode failure means the bytes aren't UTF-8 text — almost always a binary
    file given a text extension — so the message points back at supported types
    rather than leaking the raw codec error."""
    try:
        return blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise IngestError(
            reason="unsupported_format",
            message=f"That file isn't readable as text (expected UTF-8). {SUPPORTED_FORMATS_HINT}",
        ) from None


def _read_delimited(text: str, delimiter: str) -> Table:
    """Parse CSV/TSV text into a :class:`Table` via ``csv.DictReader``."""
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
    """Parse a JSON document into a :class:`Table`, permissively. Accepts a
    top-level list of objects, an object wrapping a list under a known key
    (``data``/``rows``/``items``/``records``/``examples``), or an
    object-of-columns (every value an equal-length list → transposed)."""
    try:
        doc: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestError(reason="bad_json", message=f"JSON parse failed: {exc}") from None

    records = _records_from_json(doc)
    return _records_to_table(records)


def _records_from_json(doc: Any) -> list[dict[str, Any]]:
    """Normalise a parsed JSON document to a list of record dicts."""
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
    """Parse JSONL/NDJSON (one JSON object per non-blank line) into a :class:`Table`."""
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
    """Flatten record dicts into a header-agnostic :class:`Table`. Headers are the
    first-seen-ordered union of keys; cells are stringified (``json.dumps`` for
    nested dict/list, ``""`` for ``None``). Blank rows dropped, cap enforced."""
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
    """Coerce one JSON cell value to a string: nested dict/list → compact JSON,
    ``None`` → ``""``, scalars → ``str``."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _read_xlsx(blob: bytes) -> Table:
    """Parse the first sheet of an ``.xlsx`` workbook into a :class:`Table`.

    Gated by ``settings.HARDENED_MODE`` — Excel is a macro / zip-bomb / XXE
    vector, so a hardened deployment rejects it rather than parses. ``openpyxl``
    runs ``read_only`` (streams large sheets) + ``data_only`` (cached values,
    not formulas) and never executes VBA."""
    from promptpotter.config.settings import settings

    if settings.HARDENED_MODE:
        raise IngestError(
            reason="hardened_blocked",
            message="Excel uploads are disabled in hardened mode — export your sheet to CSV and drop it again.",
        )

    import openpyxl  # lazy: keep the import cost off the hot CSV/JSON path

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


def _dedup_terms(values: Iterable[str]) -> tuple[str, ...]:
    """Strip, drop blanks + the TermNorm ``--`` placeholder, dedup to first
    occurrence (order-preserving). The shared shape of a candidate library however
    it's sourced — a dropped file or a dataset column — so the two never diverge."""
    seen: dict[str, None] = {}
    for value in values:
        term = value.strip()
        if term and term != "--":
            seen.setdefault(term, None)
    return tuple(seen)


def parse_candidate_library(blob: bytes, filename: str) -> tuple[str, ...]:
    """Parse a dropped candidate-library file into the flat target list.

    Two shapes, split on extension (matching the dependency hint "one entry per
    line, or a single-column CSV/Excel"): a ``.txt`` / extensionless file is read
    line-by-line (no header assumption — every non-blank line is a target); any
    tabular format reuses :func:`read_tabular` and takes the **first column**,
    dropping its header. Blanks and the TermNorm ``--`` placeholder are skipped;
    order is preserved and duplicates collapse to first occurrence (so the index
    stays stable). Raises :class:`IngestError` on an unparseable tabular blob.

    Entries that themselves contain commas (e.g. ``"Steel, low-alloyed, ... U"``)
    are safe via ``.txt`` (one per line) or ``.xlsx`` (cells read verbatim), and via
    CSV only when the source quotes them (valid CSV). An UNQUOTED-comma ``.csv``
    splits mid-entry — so the operator-facing hint steers comma-laden lists to
    per-line ``.txt`` / Excel, not raw CSV."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("txt", ""):
        return _dedup_terms(_decode(blob).splitlines())
    table = read_tabular(blob, fmt=format_from_filename(filename))
    return _dedup_terms(row.get(table.headers[0], "") for row in table.rows)


def read_candidate_library_file(dataset_config_dir: Path) -> tuple[str, ...]:
    """Read the candidate library committed alongside an origin (``candidate_library.txt``).

    The single file-read seam for the per-pipeline origin's target list — the
    runtime term-index union (``bootstrap/wiring.py``) and the reopen draft
    (``ingest.draft_from_dataset`` via :class:`AuthoredDataset`) both read it
    through here, so the parse (one entry per line) and normalization
    (:func:`_dedup_terms`: strip, drop blanks + the ``--`` placeholder, dedup)
    match however the library was sourced. Absent file → ``()`` (no dependency
    was dropped; the pool is the answers alone — degenerate but runnable)."""
    path = dataset_config_dir / CANDIDATE_LIBRARY_FILE
    if not path.is_file():
        return ()
    return _dedup_terms(path.read_text(encoding="utf-8").splitlines())


def candidate_library_from_rows(rows: Iterable[dict[str, Any]], column: str) -> tuple[str, ...]:
    """Build a candidate library from the distinct values of ``column`` across a
    dataset's own rows — the "build from dataset" source.

    The unified alternative to a file drop: when the targets already live in the
    data (the ground-truth/target column, the union of the dataset's category
    sheets), the library is derived rather than re-uploaded. Same dedup shape as
    :func:`parse_candidate_library`."""
    return _dedup_terms(str(row.get(column, "")) for row in rows)


MAX_ENUMERATED_LABELS = 40
"""Upper bound on a closed label set. Above this the target column reads as
open-ended (free text / high-cardinality id), not a fixed taxonomy worth
enumerating verbatim into the origin prompt."""


def closed_label_set(
    values: Iterable[str], *, n_rows: int, max_enum: int = MAX_ENUMERATED_LABELS
) -> tuple[str, ...] | None:
    """Distinct target-column values when the column reads as a CLOSED label set.

    Returns the sorted distinct non-empty values when the column looks like a
    fixed classification taxonomy (``2 <= distinct <= max_enum`` *and*
    ``distinct <= n_rows // 2`` — so a free-text or numeric column, where
    ``distinct ≈ n_rows``, reads as open-ended). Returns ``None`` for an
    open-ended column: there's no small fixed answer space to enumerate.

    The single cardinality gate is the closed-vs-open detector — a classifier's
    gold column (e.g. ``{financial, actionable, informational, other}`` over 32
    rows) returns its four labels; GSM8K's numeric answers (``distinct ≈ n``)
    return ``None``. No scorer special-casing needed.
    """
    distinct = sorted({v.strip() for v in values if v and v.strip()})
    if not 2 <= len(distinct) <= max_enum:
        return None
    if len(distinct) > n_rows // 2:
        return None
    return tuple(distinct)


__all__ = [
    "MAX_ENUMERATED_LABELS",
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
