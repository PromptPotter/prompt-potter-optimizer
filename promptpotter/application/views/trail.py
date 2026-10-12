from __future__ import annotations

from promptpotter.application.account import Trail


def trail_rows(trail: Trail) -> list[str]:
    """One row per line; the actor is named only where it is somebody else."""
    rows = []
    for line in trail.lines:
        stamp = line.at[:19].replace("T", " ")
        actor = "" if line.actor in trail.own else f"by {line.actor}"
        rows.append(" ".join(part for part in (stamp, line.what, line.subject, actor) if part))
    return rows


def trail_notes(trail: Trail) -> list[str]:
    return [f"Could not read: {', '.join(trail.unreadable)}"] if trail.unreadable else []


__all__ = ["trail_notes", "trail_rows"]
