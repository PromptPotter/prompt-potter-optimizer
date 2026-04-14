"""Tests for the canonical per-node prompt store and deprecation warnings.

Covers two stable contracts:

1. ``load_node_prompt`` resolves per-node files first, then falls back to
   the dataset-wide ``default.json``, and raises with a migration hint
   when neither exists.
2. ``parse_pipeline_response`` emits deprecation warnings when a pipeline
   still authors prompts the old way (blob in ``config.prompt`` or
   atomic ``"prompt"`` in ``optimizer.param_keys``).
"""

from __future__ import annotations

import json
import logging

import pytest

from promptpotter.application.datasets.prompt_store import load_node_prompt
from promptpotter.application.pipeline_discovery import parse_pipeline_response


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


def test_parse_pipeline_warns_on_blob_prompt(caplog):
    raw = {
        "name": "demo",
        "version": "v0",
        "nodes": {
            "llm_only": {
                "type": "generation",
                "config": {
                    "model": "openai/gpt-oss-120b",
                    "prompt": "You are a legacy blob.",
                },
                "optimizer": {
                    "param_keys": ["model", "temperature"],
                    "langfuse_type": "generation",
                },
            }
        },
        "pipelines": {"default": ["llm_only"]},
    }
    with caplog.at_level(logging.WARNING):
        parse_pipeline_response(raw)
    assert any("monolithic 'prompt' in config" in r.message for r in caplog.records)


def test_parse_pipeline_warns_on_atomic_prompt_param_key(caplog):
    raw = {
        "name": "demo",
        "version": "v0",
        "nodes": {
            "llm_only": {
                "type": "generation",
                "config": {"model": "openai/gpt-oss-120b"},
                "optimizer": {
                    "param_keys": ["model", "prompt"],
                    "langfuse_type": "generation",
                },
            }
        },
        "pipelines": {"default": ["llm_only"]},
    }
    with caplog.at_level(logging.WARNING):
        parse_pipeline_response(raw)
    assert any("lists 'prompt' as an atomic key" in r.message for r in caplog.records)


def test_parse_pipeline_canonical_no_warnings(caplog):
    raw = {
        "name": "demo",
        "version": "v0",
        "nodes": {
            "llm_only": {
                "type": "generation",
                "config": {"model": "openai/gpt-oss-120b", "temperature": 0.0},
                "optimizer": {
                    "param_keys": [
                        "model",
                        "temperature",
                        "persona",
                        "task_intent",
                        "problem_description",
                        "instruction",
                        "thinking_style",
                        "answer_format",
                    ],
                    "langfuse_type": "generation",
                },
            }
        },
        "pipelines": {"default": ["llm_only"]},
    }
    with caplog.at_level(logging.WARNING):
        parse_pipeline_response(raw)
    assert not any(
        "Deprecated prompt authoring" in r.message or "Deprecated param_keys axis" in r.message
        for r in caplog.records
    )
