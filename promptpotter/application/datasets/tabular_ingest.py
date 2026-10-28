"""Any tabular file a person can export becomes one ``Table``.

The format is read off the BYTES, never trusted from the extension: a first-time user's file is an
Excel CSV in their locale's code page and separator, a pre-2007 ``.xls``, a JSONL saved as ``.json``.
The extension only breaks a tie the content cannot (``.tsv`` with one column).
"""

from __future__ import annotations

import codecs
import csv
import io
import json
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import repeat
from pathlib import Path
from typing import IO, Any, Literal
from xml.etree import ElementTree

from promptpotter.config.settings import settings
from promptpotter.domain.pipeline_schema import CANDIDATE_LIBRARY_FILE
from promptpotter.domain.sample import Sample
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.extras import Extra, require_extra

# Per-upload row cap, an order of magnitude above any shipped bank: bounds the draft's footprint.
MAX_SAMPLES = 50_000
MAX_COLUMNS = 1_024
_MAX_SCANNED_ROWS = 2 * MAX_SAMPLES

# A spreadsheet is a zip of XML: what it INFLATES to is the bound, not what was uploaded.
_MAX_INFLATED_BYTES = 256 * 1024 * 1024

# One cell may hold a whole document; the upload cap already bounds it.
csv.field_size_limit(32 * 1024 * 1024)

_DELIMITERS = ",;\t|"
_SNIFF_CHARS = 64 * 1024

TabularFormat = Literal["text", "xlsx", "ods", "xls"]

SUPPORTED_FORMATS_HINT = (
    "Drop a table: CSV or TSV (any separator or encoding), JSON, JSONL, Excel (.xlsx, .xls) "
    "or OpenDocument (.ods)."
)


IngestReason = Literal[
    "unreadable",
    "bad_json",
    "empty",
    "too_large",
    "bad_slug",
    "unsupported_format",
    "hardened_blocked",
    "missing_column",
    "missing_value",
]


class IngestError(PayloadInvalidError):
    """``reason`` rides the error envelope's ``details`` (``main.py::_error_response``)."""

    code = "ingest_failed"

    def __init__(self, *, reason: IngestReason, message: str) -> None:
        super().__init__(message, details={"reason": reason})
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Table:
    headers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def file_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def sniff_format(blob: bytes) -> TabularFormat:
    if blob.startswith(b"PK\x03\x04"):
        return _zip_format(blob)
    if blob.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "xls"
    return "text"


def _zip_format(blob: bytes) -> TabularFormat:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            if sum(info.file_size for info in zf.infolist()) > _MAX_INFLATED_BYTES:
                raise IngestError(
                    reason="too_large", message="The spreadsheet inflates past the upload cap."
                )
            names = set(zf.namelist())
            if "xl/workbook.xml" in names:
                return "xlsx"
            if "mimetype" in names and zf.read("mimetype").startswith(
                b"application/vnd.oasis.opendocument.spreadsheet"
            ):
                return "ods"
    except zipfile.BadZipFile:
        raise IngestError(
            reason="unreadable", message="The file is a damaged ZIP archive."
        ) from None
    raise IngestError(
        reason="unsupported_format",
        message=f"That ZIP archive is not a spreadsheet (a Word file, perhaps?). {SUPPORTED_FORMATS_HINT}",
    )


def read_tabular(blob: bytes, filename: str) -> Table:
    fmt = sniff_format(blob)
    if fmt != "text" and settings.HARDENED_MODE:
        # A spreadsheet is a macro / zip-bomb / XXE vector.
        raise IngestError(
            reason="hardened_blocked",
            message="Spreadsheet uploads are disabled in hardened mode — export your sheet to CSV and drop it again.",
        )
    match fmt:
        case "xlsx":
            return _records_to_table(_xlsx_records(blob))
        case "ods":
            return _records_to_table(_ods_records(blob))
        case "xls":
            return _records_to_table(_xls_records(blob))
        case "text":
            return _read_text(decode_text(blob), file_extension(filename))


def decode_text(blob: bytes) -> str:
    """A BOM names its encoding; without one, UTF-8 if it decodes, else Windows-1252 — what Excel writes."""
    for bom, encoding in (
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if blob.startswith(bom):
            return blob.decode(encoding, errors="replace")
    head = blob[:_SNIFF_CHARS]
    # UTF-16 with no BOM: in Latin text one byte of every pair is NUL, the other never.
    half = len(head) // 2
    for nuls, clean, encoding in (
        (head[1::2].count(0), head[::2].count(0), "utf-16-le"),
        (head[::2].count(0), head[1::2].count(0), "utf-16-be"),
    ):
        if half and nuls > 0.9 * half and clean < 0.01 * half:
            return blob.decode(encoding, errors="replace")
    if b"\x00" in head:
        raise IngestError(
            reason="unsupported_format",
            message=f"That looks like a binary file (a PDF or an image?), not a table. {SUPPORTED_FORMATS_HINT}",
        )
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return blob.decode("cp1252", errors="replace")


def _read_text(text: str, extension: str) -> Table:
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        first_line = stripped.split("\n", 1)[0].strip()
        if stripped.startswith("{") and "\n" in stripped.strip() and _is_json_object(first_line):
            return _records_to_table(_jsonl_records(stripped))
        return _records_to_table(_json_records(stripped))
    return _read_delimited(text, _delimiter(text, extension))


def _is_json_object(line: str) -> bool:
    try:
        return isinstance(json.loads(line), dict)
    except json.JSONDecodeError:
        return False


def _delimiter(text: str, extension: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:_SNIFF_CHARS], delimiters=_DELIMITERS).delimiter
    except csv.Error:
        # One column: no separator to find.
        return "\t" if extension in ("tsv", "tab") else ","


def _read_delimited(text: str, delimiter: str) -> Table:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader, None)
        if not header or not any(cell.strip() for cell in header):
            raise IngestError(reason="unreadable", message="File has no header row.")
        headers = _unique_headers(header)
        return _records_to_table(dict(zip(headers, cells, strict=False)) for cells in reader)
    except csv.Error as exc:
        raise IngestError(reason="unreadable", message=f"Parse failed: {exc}") from None


def _json_records(text: str) -> list[dict[str, Any]]:
    try:
        doc: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestError(reason="bad_json", message=f"JSON parse failed: {exc}") from None
    if isinstance(doc, list):
        return _list_records(doc)
    if isinstance(doc, dict):
        # Every top-level list of rows, whatever it is called: `train` + `test` are one bank.
        nested = [
            record
            for value in doc.values()
            if isinstance(value, list) and value and all(isinstance(r, dict) for r in value)
            for record in value
        ]
        if nested:
            return nested
        cols = {k: v for k, v in doc.items() if isinstance(v, list)}
        if cols and len(cols) == len(doc) and len({len(v) for v in cols.values()}) == 1:
            n = len(next(iter(cols.values())))
            return [{k: cols[k][i] for k in cols} for i in range(n)]
    raise IngestError(
        reason="bad_json",
        message="JSON must hold rows: a list of objects, a list of lists under a header row, "
        "an object wrapping such a list, or an object of equal-length columns.",
    )


def _list_records(doc: list[Any]) -> list[dict[str, Any]]:
    if all(isinstance(r, dict) for r in doc):
        return doc
    if doc and all(isinstance(r, list) for r in doc):
        header, *body = doc
        return [dict(zip((str(h) for h in header), row, strict=False)) for row in body]
    if all(not isinstance(r, (dict, list)) for r in doc):
        return [{"value": r} for r in doc]
    raise IngestError(reason="bad_json", message="A JSON list must hold rows of one kind.")


def _jsonl_records(text: str) -> list[dict[str, Any]]:
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
        if not isinstance(obj, dict):
            raise IngestError(
                reason="bad_json", message=f"Line {ordinal}: a JSONL row is an object."
            )
        records.append(obj)
    return records


def _unique_headers(raw: Iterable[Any]) -> list[str]:
    """A blank header is named by position, a repeated one numbered: a column is never dropped."""
    headers: list[str] = []
    taken: set[str] = set()
    for i, cell in enumerate(raw, start=1):
        name = _stringify_cell(cell) or f"column_{i}"
        base, n = name, 2
        while name in taken:
            name, n = f"{base}_{n}", n + 1
        taken.add(name)
        headers.append(name)
    return headers


def _records_to_table(records: Iterable[dict[Any, Any]]) -> Table:
    keys: list[Any] = []
    seen: set[Any] = set()
    raw_rows: list[dict[Any, str]] = []
    for rec in records:
        for key in rec:
            if key not in seen:
                seen.add(key)
                keys.append(key)
        if len(keys) > MAX_COLUMNS:
            raise IngestError(
                reason="too_large",
                message=f"Upload exceeds the per-file cap of {MAX_COLUMNS} columns.",
            )
        cells = {key: _stringify_cell(value) for key, value in rec.items()}
        if not any(cells.values()):
            continue
        if len(raw_rows) >= MAX_SAMPLES:
            raise IngestError(
                reason="too_large",
                message=f"Upload exceeds the per-file cap of {MAX_SAMPLES} rows.",
            )
        raw_rows.append(cells)

    if not keys:
        raise IngestError(reason="empty", message="No rows found in the upload.")
    if not raw_rows:
        raise IngestError(reason="empty", message="Upload parsed but has zero data rows.")

    headers = _unique_headers(keys)
    rows = tuple(
        {h: row.get(k, "") for h, k in zip(headers, keys, strict=True)} for row in raw_rows
    )
    return Table(headers=tuple(headers), rows=rows)


def _stringify_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _sheet_records(rows: Iterable[Sequence[Any]]) -> Iterator[dict[str, Any]]:
    scanned = _scan_bounded(rows)
    header = list(next((r for r in scanned if any(map(_stringify_cell, r))), ()))
    while header and not _stringify_cell(header[-1]):
        header.pop()
    if not header:
        raise IngestError(reason="unreadable", message="The sheet has no header row.")
    headers = _unique_headers(header)
    for cells in scanned:
        yield {h: _sheet_cell(c) for h, c in zip(headers, cells, strict=False)}


# A blank row is skipped, never free: a run of them is the file's claim, so it counts toward a ceiling.
def _scan_bounded(rows: Iterable[Sequence[Any]]) -> Iterator[Sequence[Any]]:
    for scanned, cells in enumerate(rows, start=1):
        if scanned > _MAX_SCANNED_ROWS:
            raise IngestError(
                reason="too_large",
                message=f"Upload exceeds the per-file cap of {_MAX_SCANNED_ROWS} rows, blank ones included.",
            )
        yield cells


def _sheet_cell(value: Any) -> Any:
    # A spreadsheet stores every number as a float; `3.0` is the label `3`.
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _xlsx_records(blob: bytes) -> Iterator[dict[str, Any]]:
    require_extra(Extra.EXCEL)
    import openpyxl  # lazy: an extra, and off the hot text path

    try:
        wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a grab-bag of errors on bad blobs
        raise IngestError(reason="unreadable", message=f"Excel parse failed: {exc}") from exc
    try:
        ws = wb.active
        if ws is None:
            raise IngestError(reason="unreadable", message="Excel workbook has no active sheet.")
        # The declared <dimension> and row `r=` gaps are the file's claim: the caps bound the walk.
        ws.reset_dimensions()
        yield from _sheet_records(
            ws.iter_rows(max_row=_MAX_SCANNED_ROWS + 1, max_col=MAX_COLUMNS + 1, values_only=True)
        )
    finally:
        wb.close()


def _xls_records(blob: bytes) -> Iterator[dict[str, Any]]:
    require_extra(Extra.EXCEL)
    import xlrd  # lazy: an extra, and off the hot text path

    try:
        # Ragged: a row holds its own cells, never padded to the widest one.
        book = xlrd.open_workbook(file_contents=blob, on_demand=True, ragged_rows=True)
        sheet = book.sheet_by_index(0)
    except Exception as exc:  # xlrd raises its own grab-bag on bad blobs
        raise IngestError(reason="unreadable", message=f"Excel parse failed: {exc}") from exc
    return _sheet_records(sheet.row_values(i, end_colx=MAX_COLUMNS + 1) for i in range(sheet.nrows))


_TABLE_NS = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_ODS_TABLE = f"{_TABLE_NS}table"
_ODS_ROW = f"{_TABLE_NS}table-row"
_ODS_TEXT_P = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p"


def _ods_records(blob: bytes) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        try:
            source = zf.open("content.xml")  # inflates no further than its declared size
        except KeyError as exc:
            raise IngestError(
                reason="unreadable", message=f"OpenDocument parse failed: {exc}"
            ) from None
        with source:
            try:
                yield from _sheet_records(_ods_rows(source))
            except ElementTree.ParseError as exc:
                raise IngestError(
                    reason="unreadable", message=f"OpenDocument parse failed: {exc}"
                ) from None


# Streamed: every element is dropped once read, so memory holds one open path and one cell.
# A repeat count is the file's claim, and blank tails repeat to the sheet's edge: blanks expand
# only once a value follows, a valued row streams to the row cap, a row stops one past the
# column cap, and a blank row yields once whatever it declares.
def _ods_rows(source: IO[bytes]) -> Iterator[list[str]]:
    rows_repeated = f"{_TABLE_NS}number-rows-repeated"
    columns_repeated = f"{_TABLE_NS}number-columns-repeated"
    path: list[ElementTree.Element] = []
    table: ElementTree.Element | None = None
    row: ElementTree.Element | None = None
    cell: ElementTree.Element | None = None
    cells: list[str] = []
    blanks = 0
    for event, elem in ElementTree.iterparse(source, events=("start", "end")):
        if event == "start":
            if table is None and elem.tag == _ODS_TABLE:
                table = elem
            elif row is not None and cell is None and path[-1] is row:
                cell = elem
            elif table is not None and row is None and elem.tag == _ODS_ROW:
                row, cells, blanks = elem, [], 0
            path.append(elem)
            continue
        path.pop()
        if elem is cell:
            cell = None
            value = "\n".join("".join(p.itertext()) for p in elem.findall(_ODS_TEXT_P))
            count = int(elem.get(columns_repeated, "1"))
            if not value:
                blanks += count
            elif len(cells) <= MAX_COLUMNS:
                cells.extend([""] * min(blanks, MAX_COLUMNS + 1 - len(cells)))
                cells.extend([value] * min(count, MAX_COLUMNS + 1 - len(cells)))
                blanks = 0
        elif elem is row:
            row = None
            yield from repeat(cells, int(elem.get(rows_repeated, "1"))) if cells else ([],)
        elif elem is table:
            return
        if cell is None and path:
            path[-1].remove(elem)
    raise IngestError(reason="unreadable", message="The OpenDocument file has no sheet.")


def materialize_samples(table: Table, *, query_col: str, ground_truth_col: str) -> list[Sample]:
    """In FILE order: a run's partition seed ranks rows by content, so no position is tied to a label."""
    for label, col in (("query", query_col), ("ground_truth", ground_truth_col)):
        if col not in table.headers:
            raise IngestError(
                reason="missing_column",
                message=(
                    f"{label} column {col!r} is not one of the uploaded headers "
                    f"{list(table.headers) or '<none>'}."
                ),
            )

    # Validated in FILE order so a rejection quotes the operator's own row number.
    pairs: list[tuple[str, str]] = []
    for ordinal, row in enumerate(table.rows, start=1):
        query = row.get(query_col, "")
        ground_truth = row.get(ground_truth_col, "")
        if not query:
            raise IngestError(
                reason="missing_value", message=f"Row {ordinal}: empty {query_col!r} cell."
            )
        if not ground_truth:
            raise IngestError(
                reason="missing_value", message=f"Row {ordinal}: empty {ground_truth_col!r} cell."
            )
        pairs.append((query, ground_truth))
    return [
        Sample(id=i, query=query, ground_truth=ground_truth)
        for i, (query, ground_truth) in enumerate(pairs)
    ]


def _dedup_terms(values: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        term = value.strip()
        if term and term != "--":
            seen.setdefault(term, None)
    return tuple(seen)


def parse_candidate_library(blob: bytes, filename: str) -> tuple[str, ...]:
    if sniff_format(blob) == "text" and file_extension(filename) in ("txt", ""):
        terms = _dedup_terms(decode_text(blob).splitlines())
    else:
        table = read_tabular(blob, filename)
        terms = _dedup_terms(row.get(table.headers[0], "") for row in table.rows)
    if not terms:
        raise IngestError(
            reason="empty",
            message="The candidate library has no usable entries (every line was blank).",
        )
    return terms


def read_candidate_library_file(dataset_dir: Path) -> tuple[str, ...]:
    path = dataset_dir / CANDIDATE_LIBRARY_FILE
    if not path.is_file():
        return ()
    return _dedup_terms(path.read_text(encoding="utf-8").splitlines())


def candidate_library_from_rows(rows: Iterable[dict[str, Any]], column: str) -> tuple[str, ...]:
    return _dedup_terms(str(row.get(column, "")) for row in rows)


MAX_ENUMERATED_LABELS = 40
"""Upper bound on a closed label set. Above this the target column reads as
open-ended (free text / high-cardinality id), not a fixed taxonomy worth
enumerating verbatim into the origin prompt."""


def closed_label_set(
    values: Iterable[str], *, n_rows: int, max_enum: int = MAX_ENUMERATED_LABELS
) -> tuple[str, ...] | None:
    distinct = sorted({v.strip() for v in values if v and v.strip()})
    if not 2 <= len(distinct) <= max_enum:
        return None
    if len(distinct) > n_rows // 2:
        return None
    return tuple(distinct)
