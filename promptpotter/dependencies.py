"""FastAPI dependency injection providers."""

from typing import Annotated

from fastapi import Depends

from promptpotter.infrastructure.store import Stores, build_stores


def get_store() -> Stores:
    return build_stores()


StoreDep = Annotated[Stores, Depends(get_store)]
