"""The Account view's tabs in words, and a refusal as every surface says it: the API's error
handler, the jobs layer and the terminal's refusal path each word a refusal through it."""

from __future__ import annotations

from typing import NamedTuple

from promptpotter.shared.account_tabs import AccountTab
from promptpotter.shared.errors import SendRefusedError
from promptpotter.shared.worded import Worded


class AccountTabInfo(NamedTuple):
    label: str
    group: str
    # Where the tab sits, as a sentence names it: its group, then its label.
    place: str


def _tab(label: str, group: str) -> AccountTabInfo:
    return AccountTabInfo(label, group, f"{group} → {label}")


ACCOUNT_TAB_INFO: Worded[AccountTab, AccountTabInfo] = Worded(
    {
        "profile": _tab("Profile", "Account"),
        "usage": _tab("Usage & limits", "Account"),
        "keys": _tab("API keys", "Account"),
        "activity": _tab("Activity", "Account"),
        "preferences": _tab("Preferences", "Account"),
        "storage": _tab("Storage", "Workspace"),
        "about": _tab("About this unit", "This unit"),
    }
)


def refusal_words(refused: BaseException) -> str:
    """A refusal as every surface says it: its sentence, then the Account tab its remedy lives
    in, where it names one (`SendRefusedError.tab`)."""
    tab = refused.tab if isinstance(refused, SendRefusedError) else None
    return str(refused) if tab is None else f"{refused} ({ACCOUNT_TAB_INFO[tab].place})"
