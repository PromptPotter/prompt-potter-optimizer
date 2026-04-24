"""Structural invariants for the inbox registry.

The registry is the single source of truth for optimizer-prompt wiring
(see ``docs/developer/information-flow.md``). These checks guard:

1. Every name listed in ``LAYER_ORDER`` has a matching ``InboxField`` in
   ``INBOX``.
2. Every field declared as consumed by a layer is actually listed in
   that layer's ``LAYER_ORDER`` (otherwise the field is dead code).
3. Mutex declarations point at fields that exist in the same layer.

These are structural invariants — not display/formatting assertions —
and are cheap to maintain. They catch silent drift when fields are
added or removed.
"""

from __future__ import annotations

from promptpotter.application.optimization.nodes.inbox_registry import (
    INBOX,
    LAYER_ORDER,
    Layer,
)


def test_layer_order_names_exist_in_registry() -> None:
    registry_names = {f.name for f in INBOX}
    for layer, names in LAYER_ORDER.items():
        for n in names:
            assert n in registry_names, f"{layer}: order references unknown field {n!r}"


def test_registry_layers_match_layer_order() -> None:
    """Field.layers must equal the set of layers whose LAYER_ORDER lists the field."""
    for f in INBOX:
        declared_by_order = {layer for layer, names in LAYER_ORDER.items() if f.name in names}
        assert f.layers == frozenset(declared_by_order), (
            f"Field {f.name!r}: declared layers={f.layers} but LAYER_ORDER places it in "
            f"{declared_by_order}"
        )


def test_mutex_partners_exist_in_same_layer() -> None:
    by_name = {f.name: f for f in INBOX}
    groups: dict[tuple[Layer, str], list[str]] = {}
    for f in INBOX:
        for layer, (group, _pri) in f.mutex.items():
            groups.setdefault((layer, group), []).append(f.name)

    for (layer, group), members in groups.items():
        assert len(members) >= 2, (
            f"Mutex group {(layer, group)} has a single member {members} — "
            f"mutex needs at least two partners"
        )
        layer_order = LAYER_ORDER[layer]
        for m in members:
            assert m in layer_order, (
                f"Mutex partner {m!r} is declared on {layer} but missing from LAYER_ORDER"
            )
            assert layer in by_name[m].layers, (
                f"Mutex partner {m!r} has mutex on {layer} but layers={by_name[m].layers}"
            )


def test_mutex_priorities_are_unique_within_group() -> None:
    """Two fields in the same (layer, group) must have distinct priorities."""
    groups: dict[tuple[Layer, str], list[tuple[int, str]]] = {}
    for f in INBOX:
        for layer, (group, pri) in f.mutex.items():
            groups.setdefault((layer, group), []).append((pri, f.name))

    for key, members in groups.items():
        priorities = [pri for pri, _ in members]
        assert len(set(priorities)) == len(priorities), (
            f"Mutex group {key} has duplicate priorities: {members}"
        )
