import re

# Every escape sequence and control byte a terminal writes, tab and newlines kept.
_TERMINAL_ESCAPE = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[]()#][0-9A-Za-z]|\x1b.|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)


def strip_terminal(text: str) -> str:
    """*text* as read, its colours, cursor moves and control bytes gone."""
    return _TERMINAL_ESCAPE.sub("", text)
