"""Tests for the canonical per-node prompt store.

``load_node_prompt`` resolves per-node files first, then falls back to
the dataset-wide ``default.json``, and raises with a migration hint
when neither exists.
"""

from __future__ import annotations

import json

import pytest

from promptpotter.application.datasets.prompt_store import load_node_prompt


def _write_template(path, **fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields), encoding="utf-8")


def test_load_node_prompt_prefers_per_node_file(tmp_path, monkeypatch):
    from promptpotter.application.datasets import prompt_store

    monkeypatch.setattr(prompt_store, "_REPO_ROOT", tmp_path)
    load_node_prompt.cache_clear()

    d = tmp_path / "datasets" / "mydataset" / "prompts"
    _write_template(d / "default.json", persona="default-persona")
    _write_template(d / "my_node.json", persona="node-specific")

    tmpl = load_node_prompt("mydataset", "my_node")
    assert tmpl.persona == "node-specific"


def test_load_node_prompt_falls_back_to_default(tmp_path, monkeypatch):
    from promptpotter.application.datasets import prompt_store

    monkeypatch.setattr(prompt_store, "_REPO_ROOT", tmp_path)
    load_node_prompt.cache_clear()

    d = tmp_path / "datasets" / "mydataset" / "prompts"
    _write_template(d / "default.json", persona="default-persona")

    tmpl = load_node_prompt("mydataset", "some_node_without_own_file")
    assert tmpl.persona == "default-persona"


def test_load_node_prompt_raises_with_migration_hint(tmp_path, monkeypatch):
    from promptpotter.application.datasets import prompt_store

    monkeypatch.setattr(prompt_store, "_REPO_ROOT", tmp_path)
    load_node_prompt.cache_clear()

    with pytest.raises(FileNotFoundError, match="Canonical prompt template not found"):
        load_node_prompt("nonexistent_dataset", "some_node")
