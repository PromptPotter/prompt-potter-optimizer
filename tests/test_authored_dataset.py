"""Canonical authored-dataset reader — the single shared parse behind the CLI
``new`` config-read, the launcher web-launch, and ``draft_from_dataset``.

One contract worth guarding (tests/CLAUDE.md cat. 3 — wire/schema): the reader
returns the WHOLE ``nodes.{name}`` sub-blocks, not just ``nodes.*.config``.
Collapsing to ``.config`` (what ``load_dataset_node_overlay`` does) would
silently drop ``optimizer.param_allowed_values`` locks from a committed draft —
the regression this asserts against. Validation + backend-type lowercasing ride
the same canonical case.
"""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.application.datasets import read_authored_dataset


def test_read_authored_dataset_preserves_full_node_blocks_and_validates(tmp_path: Path) -> None:
    """A node's ``optimizer`` sub-block survives the read; config validates; backend lowercased."""
    (tmp_path / "campaign.json").write_text(
        json.dumps(
            {
                "campaign_config": {
                    "scoring": "exact_match(predicted, ground_truth)",
                    "optimization": {
                        "max_rounds": 5,
                        "improvement_threshold": 0.01,
                        "degradation_threshold": 0.05,
                    },
                    "optimizer_llm": {"provider": "openrouter", "model": "openai/gpt-oss-120b"},
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "pipeline.json").write_text(
        json.dumps(
            {
                "backend_type": "TermNorm",
                "nodes": {
                    "llm_only": {
                        "config": {"model": "openai/gpt-oss-20b"},
                        "optimizer": {"param_allowed_values": {"temperature": [0.0, 0.5]}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "task_description.md").write_text("  Route the ticket.  ", encoding="utf-8")

    authored = read_authored_dataset(tmp_path)

    # The optimizer lock must survive — not dropped to nodes.*.config.
    assert authored.pipeline_nodes["llm_only"]["optimizer"] == {
        "param_allowed_values": {"temperature": [0.0, 0.5]}
    }
    assert authored.backend_type == "termnorm"  # lowercased
    assert authored.task_description == "Route the ticket."  # stripped
    assert (
        authored.campaign_config.optimization.max_rounds == 5
    )  # campaign_config unwrapped + validated
