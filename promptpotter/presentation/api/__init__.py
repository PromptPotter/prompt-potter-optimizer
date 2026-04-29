from typing import Annotated

from fastapi import Depends

from promptpotter.infrastructure.store import Stores, build_stores

StoreDep = Annotated[Stores, Depends(build_stores)]
