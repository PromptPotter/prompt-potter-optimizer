"""`api_keys.json` as v0.8.21 wrote it, each secret in clear: sealed under the box key, which the
landing alone may make where none is held (`identity/sealing.py`). The key lives off the data root,
so the plan makes it where it stages the first seal: a plan never applied leaves only a key."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr

from promptpotter.infrastructure.identity import sealing
from promptpotter.infrastructure.store.data_root import Staged, read_as_staged
from promptpotter.infrastructure.store.wallet_store import API_KEYS_FILE, HeldKey, sealed_keys


def staged(projects_root: Path, before: Mapping[Path, bytes]) -> list[Staged]:
    moved: list[Staged] = []
    for path in sorted(projects_root.glob(f"*/{API_KEYS_FILE}")):
        clear = json.loads(read_as_staged(path, before) or b"{}")
        if "tag" in clear:
            continue
        key = sealing.made_key(sealing.key_path(projects_root.parent))
        held = {
            p: HeldKey(secret=SecretStr(k["secret"]), added_at=k["added_at"])
            for p, k in clear.items()
        }
        data = sealed_keys(key, path.parent.name, held).model_dump_json(indent=2).encode()
        moved.append(Staged(path, data, f"{path.parent.name}'s {len(held)} held key(s) sealed"))
    return moved
