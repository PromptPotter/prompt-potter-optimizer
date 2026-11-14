from __future__ import annotations

import ast
import functools
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

ID_HEX = 16
# 16 hex collides at archive scale (cells = configurations x samples); 64 strains Windows paths.
ADDRESS_HEX = 32


def _import_bindings(stmt: ast.stmt) -> list[tuple[str, str]] | None:
    if isinstance(stmt, ast.If):
        nested = [_import_bindings(s) for s in (*stmt.body, *stmt.orelse)]
        if not nested or any(n is None for n in nested):
            return None
        return [pair for n in nested if n is not None for pair in n]
    if isinstance(stmt, ast.ImportFrom):
        return [(a.asname or a.name, a.name) for a in stmt.names]
    if isinstance(stmt, ast.Import):
        return [
            (a.asname, a.name.rsplit(".", 1)[-1]) if a.asname else (a.name.split(".")[0], a.name)
            for a in stmt.names
        ]
    return None


def _is_prose(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _normalized_source(tree: ast.AST) -> str:
    """Documenting, reformatting or relocating a name is free; rebinding one moves the digest."""
    bindings: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list):
                kept = []
                for s in stmts:
                    if _is_prose(s):
                        continue
                    if (pairs := _import_bindings(s)) is None:
                        kept.append(s)
                    else:
                        bindings.update(pairs)
                stmts[:] = kept
        body = getattr(node, "body", None)
        if isinstance(body, list) and not body:
            body.append(ast.Pass())
    return f"{sorted(bindings)}\n{ast.unparse(tree)}"


def module_source_digest(*modules: ModuleType) -> str:
    return stable_hash(
        [normalized_module(Path(str(m.__file__)).read_text("utf-8")) for m in modules]
    )


@functools.cache
def normalized_module(text: str) -> str:
    """Memoized on the text, never the module: an edit on disk is never answered by an older read."""
    return _normalized_source(ast.parse(text))


def stable_hash(value: Any, *, length: int = ID_HEX) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:length]


CANONICAL_PIN: dict[str, Any] = {"b": 0.1, "a": {"ü": [1.5, None], "A": "Zürich 東京"}}
assert stable_hash(CANONICAL_PIN, length=ADDRESS_HEX) == "bff2239dc003d203bdf1154c1e7222c6", (
    "every stored key is cut from this canonical form: a change here re-keys the archive"
)


def dataset_hash(dataset: Iterable[Any]) -> str:
    """Over each row as its bank file stores it, IN ORDER: one hash is one bank, every slot included."""
    return stable_hash({"rows": [row.banked() for row in dataset]}, length=ADDRESS_HEX)


def content_hash(
    dataset: Iterable[Any],
    pipeline_params: dict[str, Any] | None,
    *,
    candidate_library: Iterable[str],
) -> str:
    """Each node's rendered ``prompt`` rides its config, so *pipeline_params* covers every prompt."""
    blob_dict: dict[str, Any] = {"dataset": dataset_hash(dataset)}
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    if library := sorted(set(candidate_library)):
        blob_dict["candidate_library"] = library
    return stable_hash(blob_dict, length=ADDRESS_HEX)
