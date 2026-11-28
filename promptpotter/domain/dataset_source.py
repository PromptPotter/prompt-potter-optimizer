from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.extras import Extra


class SourceKind(Enum):
    HUB = "hub"
    GITHUB = "github"
    # A file this tenant uploaded, named by its own content: pinned without a `rev`.
    UPLOAD = "upload"
    # The rows' inputs are the `source:` section itself.
    INLINE = "inline"

    @property
    def extra(self) -> Extra | None:
        return Extra.BENCHMARKS if self is SourceKind.HUB else None

    @property
    def local(self) -> bool:
        return self in (SourceKind.UPLOAD, SourceKind.INLINE)


def _source_lacks(field: str, said: str) -> PayloadInvalidError:
    return PayloadInvalidError(
        f"the definition's `source:` names no `{field}`: {said}.",
        code="source_incomplete",
        details={"lacks": field},
    )


@dataclass(frozen=True, slots=True)
class Source:
    """A definition's ``source:`` section: where the rows come from, at which commit, cut how; the
    recipe it names is core's (`loaders.py::BANK_RECIPES`) or its connector's (`Connector.panel_recipes`)."""

    recipe: str
    kind: SourceKind
    repo: str
    rev: str
    params: Mapping[str, object]

    @classmethod
    def of(cls, section: Mapping[str, object]) -> Source | None:
        if not section:
            return None
        params = dict(section)
        recipe = str(params.pop("recipe", ""))
        if not recipe:
            raise _source_lacks("recipe", f"it names {sorted(section)}")
        kind = next(
            (
                k
                for k in (SourceKind.HUB, SourceKind.GITHUB, SourceKind.UPLOAD)
                if k.value in params
            ),
            None,
        )
        if kind is None:
            return cls(recipe, SourceKind.INLINE, "", "", params)
        repo = str(params.pop(kind.value))
        if kind is SourceKind.UPLOAD:
            return cls(recipe, kind, repo, "", params)
        # A moving ref re-cuts the bank on the next machine that fetches it, under the name it has.
        rev = str(params.pop("rev", ""))
        if not rev:
            raise _source_lacks("rev", f"it fetches {repo} at no pinned commit")
        return cls(recipe, kind, repo, rev, params)

    @property
    def configs(self) -> tuple[str, ...]:
        return (str(self["config"]),) if "config" in self.params else ()

    def __getitem__(self, key: str) -> Any:
        try:
            return self.params[key]
        except KeyError:
            raise ValueError(f"recipe {self.recipe!r} needs `source.{key}`") from None

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)
