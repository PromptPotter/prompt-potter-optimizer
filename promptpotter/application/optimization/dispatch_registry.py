"""Layer registry — assembled at import time, no lazy globals.

Imports the surface modules' section-renderer dicts and binds them to
``Layer`` enum keys. Consumers (``compile_prompt_vars``, l1.py, etc.)
import ``LAYER_CONFIGS`` directly. Surface modules do NOT import from
this module — the cycle is broken structurally by routing all type
primitives through ``dispatch_types``.
"""

from __future__ import annotations

from promptpotter.application.optimization.dispatch_types import Layer, LayerConfig
from promptpotter.application.optimization.l1_surface import L1_SECTION_RENDERERS
from promptpotter.application.optimization.l2_surface import L2_SECTION_RENDERERS

__all__ = ["LAYER_CONFIGS"]


LAYER_CONFIGS: dict[Layer, LayerConfig] = {
    Layer.L1_GENERATE: LayerConfig(
        sections=L1_SECTION_RENDERERS,
        read_overrides=lambda osp: (
            osp.l1_section_overrides,
            osp.l1_section_overrides_text,
        ),
    ),
    Layer.L2: LayerConfig(sections=L2_SECTION_RENDERERS),
    Layer.L3: LayerConfig(sections={}),  # L3 vars ride as extras.
    Layer.L1_CRITIQUE: LayerConfig(sections={}),  # single dispatch_msg via extras.
}
