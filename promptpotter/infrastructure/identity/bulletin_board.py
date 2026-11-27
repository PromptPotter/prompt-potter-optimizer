"""The host's bulletins and each account's dismissals (`IdentityPaths.bulletins`), a survivor:
every write reads and rewrites the file under its write lock."""

from __future__ import annotations

import secrets
from pathlib import Path

from promptpotter.domain.bulletin import Bulletin, BulletinBoard
from promptpotter.infrastructure.store.io import write_json, write_lock
from promptpotter.infrastructure.store.read_model import read_file
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import FileKind


def read_board(path: Path) -> BulletinBoard:
    """Absent is an empty board; a file that does not validate raises."""
    held = read_file(path, BulletinBoard.model_validate_json, FileKind.STORED_CONFIG)
    return BulletinBoard(bulletins=(), dismissed={}) if held is None else held


def _write(path: Path, board: BulletinBoard) -> None:
    write_json(path, board.model_dump(mode="json"))


def post(path: Path, *, text: str, pops_up: bool) -> Bulletin:
    bulletin = Bulletin(id=secrets.token_hex(6), posted_at=utcnow_iso(), text=text, pops_up=pops_up)
    with write_lock(path):
        board = read_board(path)
        _write(
            path, BulletinBoard(bulletins=(*board.bulletins, bulletin), dismissed=board.dismissed)
        )
    return bulletin


def withdraw(path: Path, *, bulletin_id: str) -> bool:
    """Whether *bulletin_id* named a bulletin. Its dismissals go with it."""
    with write_lock(path):
        board = read_board(path)
        kept = tuple(b for b in board.bulletins if b.id != bulletin_id)
        if len(kept) == len(board.bulletins):
            return False
        dismissed = {
            account: rest
            for account, held in board.dismissed.items()
            if (rest := tuple(i for i in held if i != bulletin_id))
        }
        _write(path, BulletinBoard(bulletins=kept, dismissed=dismissed))
    return True


def rekey(path: Path, *, old_id: str, new_id: str) -> None:
    """An account's dismissals follow its id (`accounts.py::claim_default`)."""
    with write_lock(path):
        board = read_board(path)
        if old_id in board.dismissed:
            dismissed = dict(board.dismissed)
            dismissed[new_id] = dismissed.pop(old_id)
            _write(path, BulletinBoard(bulletins=board.bulletins, dismissed=dismissed))


def dismiss(path: Path, *, account_id: str, bulletin_id: str) -> bool:
    """Whether *bulletin_id* names a bulletin; dismissing one twice writes once."""
    with write_lock(path):
        board = read_board(path)
        if all(b.id != bulletin_id for b in board.bulletins):
            return False
        held = board.dismissed.get(account_id, ())
        if bulletin_id not in held:
            dismissed = {**board.dismissed, account_id: (*held, bulletin_id)}
            _write(path, BulletinBoard(bulletins=board.bulletins, dismissed=dismissed))
    return True
