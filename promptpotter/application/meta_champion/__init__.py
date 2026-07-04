"""Meta-champion — reduce the on-disk pp-self corpus to one ranked champion table.

The developer-facing L4 "which meta-prompt state is overall best?" reducer. Pure disk read
(zero LLM); the coronation match + champion seeding land in the L4 verdict/champion slices.
"""

from promptpotter.application.meta_champion.champion import (
    ChampionPointer,
    CoronationOutcome,
    champion_pointer_path,
    coronate,
    promote_champion,
    read_champion_pointer,
)
from promptpotter.application.meta_champion.reducer import (
    CandidateRow,
    CellEffect,
    ChampionRegistry,
    Provenance,
    read_registry,
    reduce_corpus,
    registry_path,
    write_registry,
)

__all__ = [
    "CandidateRow",
    "CellEffect",
    "ChampionPointer",
    "ChampionRegistry",
    "CoronationOutcome",
    "Provenance",
    "champion_pointer_path",
    "coronate",
    "promote_champion",
    "read_champion_pointer",
    "read_registry",
    "reduce_corpus",
    "registry_path",
    "write_registry",
]
