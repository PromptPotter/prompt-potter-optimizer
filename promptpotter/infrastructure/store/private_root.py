"""The data root reaches its owner alone, whichever process makes it and under whatever umask."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from promptpotter.shared.errors import PotterError

_PRIVATE = 0o700


class DataRootExposedError(PotterError):
    code = "data_root_exposed"

    def __init__(self, root: Path, mode: int) -> None:
        super().__init__(
            f"The data root {root} is mode {mode:o}, readable by other users, and this process "
            f"cannot tighten it: as its owner, run `chmod 700 {root}`.",
            details={"path": str(root)},
        )


def hold_private(root: Path) -> None:
    """Made 0700, or tightened to it; Windows' per-user profile already scopes the root."""
    if sys.platform != "win32":
        root.mkdir(mode=_PRIVATE, parents=True, exist_ok=True)
        mode = stat.S_IMODE(root.stat().st_mode)
        if mode != _PRIVATE:
            try:
                os.chmod(root, _PRIVATE)
            except PermissionError:
                raise DataRootExposedError(root, mode) from None
