"""`api_keys.json` as v0.8.21 wrote it, each secret in clear: sealed under the box key, which the
first such file makes where the install holds none (`identity/sealing.py`)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from promptpotter.infrastructure.identity import sealing
from promptpotter.infrastructure.identity.grants import identity_paths
from promptpotter.infrastructure.store.data_root import Staged, read_as_staged
from promptpotter.infrastructure.store.wallet_store import API_KEYS_FILE


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    key_path = identity_paths(projects_root).box_key
    key = read_as_staged(key_path, before)
    moved: list[Staged] = []
    for path in sorted(projects_root.glob(f"*/{API_KEYS_FILE}")):
        keys = json.loads(read_as_staged(path, before) or b"{}")
        clear = {p: k for p, k in keys.items() if "secret" in k}
        if not clear:
            continue
        if key is None:
            key = sealing.fresh_box_key()
            moved.append(Staged(key_path, key, "the box key made, to seal the held keys under"))
        sealed = {
            p: {"sealed": sealing.seal(key, k["secret"], bound=p), "added_at": k["added_at"]}
            for p, k in clear.items()
        }
        data = json.dumps({**keys, **sealed}, indent=2, ensure_ascii=False).encode("utf-8")
        moved.append(Staged(path, data, f"{path.parent.name}'s {len(clear)} held key(s) sealed"))
    return moved
