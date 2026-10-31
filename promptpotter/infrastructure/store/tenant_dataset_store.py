from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from promptpotter.domain.dataset_name import validate_dataset_name
from promptpotter.domain.pipeline_schema import CANDIDATE_LIBRARY_FILE
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_FILE,
    FRAMING_FILE,
    MATERIALIZED_PANEL_FILE,
    ORIGIN_FILE,
    render_framing_file,
)
from promptpotter.infrastructure.store.io import (
    read_bytes_optional,
    read_yaml_optional,
    validate_path_component,
    write_bytes,
    write_json,
    write_text,
    write_yaml,
)
from promptpotter.infrastructure.store.layout import dataset_id_prefix
from promptpotter.infrastructure.store.read_model import append_row, derived, file_sig, fold_jsonl
from promptpotter.shared.clock import utcnow_iso

# Panels a definition's `source:` built for this tenant, and the task directories they name.
MATERIALIZED_DIR = "materialized"
# Every bank, ONCE, under its `dataset_hash`; `labels.jsonl` beside them maps each name onto one.
BANKS_DIR = "banks"
# Every file uploaded, under its own content: an upload definition's `source:` reads it from here.
UPLOADS_DIR = "uploads"
LABELS_FILE = "labels.jsonl"
# Which bank each fetched `source:` recipe cut: a content fact, so an inner sandbox shares it.
CUTS_FILE = "cuts.jsonl"


class TenantDatasetStore:
    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def committed_datasets_root(self) -> Path:
        return self._base_dir / "datasets"

    def dataset_dir(self, slug: str) -> Path:
        validate_dataset_name(slug)
        return self.committed_datasets_root() / slug

    def list_slugs(self) -> list[str]:
        root = self.committed_datasets_root()
        if not root.is_dir():
            return []
        out: list[str] = []
        for path in sorted(root.rglob(DEFINITION_FILE)):
            name = path.parent.relative_to(root).as_posix()
            try:
                validate_dataset_name(name)
            except ValueError:
                continue
            out.append(name)
        return out

    def slug_exists(self, slug: str) -> bool:
        try:
            return self.dataset_dir(slug).is_dir()
        except ValueError:
            return False

    def suggest_free_slug(self, slug: str) -> str:
        n = 2
        while True:
            candidate = f"{slug}-{n}"
            if not self.slug_exists(candidate):
                return candidate
            n += 1

    def _labels_path(self) -> Path:
        return self._base_dir / BANKS_DIR / LABELS_FILE

    def _bank_path(self, digest: str) -> Path:
        return self._base_dir / BANKS_DIR / f"{validate_path_component(digest)}.json"

    def put_bank(self, bank: Bank) -> None:
        rows = bank.banked()
        held = self._banked_rows(bank.dataset_hash)
        if held is None:
            write_json(self._bank_path(bank.dataset_hash), {"items": rows})
        elif held != rows:
            raise RuntimeError(f"bank {bank.dataset_hash} on disk holds other rows than its hash")

    def relabel(self, label: str, digest: str) -> None:
        """A label moves; no bank and no measurement filed under either hash is rewritten."""
        validate_dataset_name(label)
        if self.hash_of(label) != digest:
            append_row(
                self._labels_path(),
                {"label": label, "dataset_hash": digest, "labelled_at": utcnow_iso()},
            )

    def labels(self) -> dict[str, str]:
        return {
            label: str(row["dataset_hash"])
            for label, row in fold_jsonl(self._labels_path(), "label").items()
        }

    def hash_of(self, label: str) -> str | None:
        return self.labels().get(label)

    def _banked_rows(self, digest: str) -> list[dict[str, Any]] | None:
        raw = read_bytes_optional(self._bank_path(digest))
        return None if raw is None else _bank_items(raw)

    def bank(self, digest: str) -> Bank | None:
        rows = self._banked_rows(digest)
        return None if rows is None else Bank.read(rows)

    def rows(self, digest: str, slots: Iterable[int]) -> dict[int, Sample] | None:
        """Only *slots* validated: a page of a big bank builds the rows it shows and no others."""
        rows = self._banked_rows(digest)
        return None if rows is None else {slot: Sample(id=slot, **rows[slot]) for slot in slots}

    def held_version(self, name: str) -> tuple[object, ...]:
        """Stats only: what moves the bank *name* holds, a bank's own rows being its hash."""
        banks = self._base_dir / BANKS_DIR
        return (
            str(self._base_dir),
            file_sig(banks),
            file_sig(self._labels_path()),
            file_sig(banks / CUTS_FILE),
            file_sig(self.materialized_panel_dir(name) / MATERIALIZED_PANEL_FILE),
        )

    def labelled(self, label: str) -> Bank | None:
        digest = self.hash_of(label)
        return None if digest is None else self.bank(digest)

    def record_cut(self, source: str, bank: Bank) -> None:
        self.put_bank(bank)
        append_row(
            self._base_dir / BANKS_DIR / CUTS_FILE,
            {"source": source, "dataset_hash": bank.dataset_hash},
        )

    def cut(self, source: str) -> Bank | None:
        row = fold_jsonl(self._base_dir / BANKS_DIR / CUTS_FILE, "source").get(source)
        return None if row is None else self.bank(str(row["dataset_hash"]))

    def banks_sharing(self, keys: frozenset[str]) -> frozenset[str]:
        """Every held bank measuring any of *keys*: a re-cut shares its rows' history by content."""
        root = self._base_dir / BANKS_DIR
        found: set[str] = set()
        for path in sorted(root.glob("*.json")) if root.is_dir() else ():
            held = derived(("bank_keys", path), sig=file_sig(path), compute=partial(_keys, path))
            if held is not None and not held.isdisjoint(keys):
                found.add(path.stem)
        return frozenset(found)

    def materialized_panel_dir(self, name: str) -> Path:
        """Where a panel recipe materializes a connector panel and the task directories it names."""
        return self._base_dir / MATERIALIZED_DIR / dataset_id_prefix(name)

    def keep_upload(self, blob: bytes, extension: str) -> Path:
        """Every upload, kept BEFORE it is parsed, so a file the parser refused or crashed on can be re-read."""
        suffix = f".{extension}" if extension.isalnum() and len(extension) <= 8 else ""
        path = self.upload_path(f"{hashlib.sha256(blob).hexdigest()}{suffix}")
        write_bytes(path, blob)
        return path

    def upload_path(self, name: str) -> Path:
        return self._base_dir / UPLOADS_DIR / validate_path_component(name)

    def task_context_path(self, name: str) -> Path:
        return self._base_dir / "task-context" / f"{dataset_id_prefix(name)}.yaml"

    def load_task_context(self, name: str) -> TaskDecomposition:
        return TaskDecomposition.from_dict(read_yaml_optional(self.task_context_path(name)))

    def save_task_context(self, name: str, framing: TaskDecomposition) -> Path | None:
        """`None` on an empty record: this tier shadows the definition's committed framing, and an empty file buries it."""
        if not framing:
            return None
        path = self.task_context_path(name)
        write_yaml(path, framing.to_dict())
        return path

    def write_definition(
        self,
        slug: str,
        *,
        source: dict[str, Any],
        pipeline: dict[str, Any],
        campaign: dict[str, Any],
        description: str,
        framing: TaskDecomposition,
        origin: dict[str, dict[str, Any]],
    ) -> Path:
        """WHOLE files: the tenant tier lays no defaults or family under a definition."""
        dst = self.dataset_dir(slug)
        write_text(dst / FRAMING_FILE, render_framing_file(description, framing))
        if origin:
            write_yaml(dst / ORIGIN_FILE, origin)
        # Last: the definition file is what makes the directory a dataset.
        write_yaml(
            dst / DEFINITION_FILE, {"source": source, "pipeline": pipeline, "campaign": campaign}
        )
        return dst

    def write_candidate_library(self, slug: str, library: Sequence[str]) -> Path:
        path = self.dataset_dir(slug) / CANDIDATE_LIBRARY_FILE
        write_text(path, "\n".join(library))
        return path


def _bank_items(raw: bytes) -> list[dict[str, Any]]:
    return list(json.loads(raw)["items"])


def read_bank(raw: bytes) -> Bank:
    return Bank.read(_bank_items(raw))


def _keys(path: Path) -> frozenset[str]:
    raw = read_bytes_optional(path)
    return frozenset() if raw is None else frozenset(s.key for s in read_bank(raw).samples)
