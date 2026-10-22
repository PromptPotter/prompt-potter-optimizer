from __future__ import annotations

from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import stable_hash


class Treatment(FrozenModel):
    """Two runs are one treatment only where ``digest`` agrees; no measurement key reads it."""

    optimizer: str
    version: str
    # Per llm node; a resume diverges on these.
    prompt_hashes: dict[str, str]
    # The manifest with its overlay laid on and its members' knobs resolved, code defaults in: a
    # knob, a walk, a roster edit or a moved member default is a new treatment.
    manifest: str
    source: str

    @property
    def digest(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


__all__ = ["Treatment"]
