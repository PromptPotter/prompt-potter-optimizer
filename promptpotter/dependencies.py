"""FastAPI dependency injection providers."""

from typing import Annotated

from fastapi import Depends

from promptpotter.services.project_store import ProjectStore


def get_store() -> ProjectStore:
    return ProjectStore()


StoreDep = Annotated[ProjectStore, Depends(get_store)]
