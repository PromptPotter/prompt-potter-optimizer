"""Verdict sidecars for ``verify`` runs. The per-sample measurements land in the cross-cycle ``measurements/``
through the ordinary scoring gateway; this store owns only the workspace-scope record."""

from __future__ import annotations

from pathlib import Path

from promptpotter.domain.results import DiagnosticRunRecord
from promptpotter.infrastructure.store.io import read_json_optional, write_json


def _ts_filename_part(ts: str) -> str:
    return ts.replace(":", "-")


class DiagnosticRunStore:
    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _root(self) -> Path:
        return self._base_dir / "diagnostics"

    def _dir(self) -> Path:
        return self._root() / "runs"

    def sidecar_path(self, filename: str) -> Path:
        """A diagnostic that is not a :class:`DiagnosticRunRecord` still belongs in the same
        directory, so this store owns ``diagnostics/`` rather than only its own subtree."""
        return self._root() / filename

    def _path(self, ts: str, config_hash: str) -> Path:
        slug = _ts_filename_part(ts)
        return self._dir() / f"{slug}_{config_hash[:12]}.json"

    def save(self, record: DiagnosticRunRecord) -> Path:
        path = self._path(record.ts, record.config_hash)
        write_json(path, record.model_dump())
        return path

    def list(self, dataset: str | None = None) -> list[DiagnosticRunRecord]:
        """All records, newest first; optionally filtered by ``dataset``."""
        out: list[DiagnosticRunRecord] = []
        dir_ = self._dir()
        if not dir_.exists():
            return out
        for p in sorted(dir_.glob("*.json"), reverse=True):
            raw = read_json_optional(p)
            if not isinstance(raw, dict):
                continue
            if dataset is not None and raw.get("dataset") != dataset:
                continue
            out.append(DiagnosticRunRecord.model_validate(raw))
        return out


__all__ = ["DiagnosticRunStore"]
