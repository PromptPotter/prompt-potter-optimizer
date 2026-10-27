from __future__ import annotations

from promptpotter.application.account import AccountTrail, Trail


def trail_rows(trail: Trail) -> list[str]:
    """One row per line; the actor is named only where it is somebody else."""
    rows = []
    for line in trail.lines:
        stamp = line.at[:19].replace("T", " ")
        actor = "" if line.actor in trail.own else f"by {line.actor}"
        rows.append(" ".join(part for part in (stamp, line.what, line.subject, actor) if part))
    return rows


def trail_fields(account: AccountTrail) -> list[dict[str, str]]:
    """The rows as fields, for a receiver that words them itself."""
    own = account.trail.own
    return [
        {
            "at": line.at,
            "what": line.what,
            "subject": line.subject,
            "campaign": line.campaign_id,
            "dataset": account.datasets.get(line.campaign_id, ""),
            "by": "" if line.actor in own else line.actor,
        }
        for line in account.trail.lines
    ]


def trail_notes(trail: Trail) -> list[str]:
    return [f"Could not read: {', '.join(trail.unreadable)}"] if trail.unreadable else []
