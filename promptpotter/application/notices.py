"""What the box tells its operator and the n8n receivers. Every send goes through
``infrastructure/outbound.py``; the admin bot and the health verb call these, so neither spells a duty."""

from __future__ import annotations

import asyncio
import time

from promptpotter.application import health
from promptpotter.application.account import trails_since
from promptpotter.application.app_visits import due_digest, mark_told
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.power import PowerLevel, power_change
from promptpotter.application.views.trail import trail_fields
from promptpotter.config.paths import default_projects_root
from promptpotter.config.settings import settings
from promptpotter.infrastructure.outbound import notify_operator, post_json
from promptpotter.infrastructure.producer_lock import minted_producer
from promptpotter.shared.clock import utcnow_iso

_TRAIL_WINDOW_H = 24


def _tell(one: health.Checked) -> None:
    if one.change is not None and (said := health.notice(one)) is not None:
        notify_operator(said, ring=one.change.state == "broken")


def check_and_tell() -> list[health.Checked]:
    """Every entry point's health check, each change of state already told. Blocking."""
    checked = asyncio.run(health.check(default_projects_root()))
    for one in checked:
        _tell(one)
    return checked


def tell_landed(tally: str) -> None:
    """What a landing wrote and dropped, counted (`maintenance/landing.py::Landing.tally`): one
    notice whatever its size, the stamp keeping each line."""
    if tally:
        notify_operator(
            f"landed the data root: {tally}. `land-data --dry-run` lists each line.", ring=False
        )


def tell_shutdown(registry: JobRegistry) -> None:
    """The server's runs in flight as it stops, told. A clean exit 0 leaves ``Restart=on-failure``
    quiet; a SIGKILL or OOM never reaches this."""
    own = minted_producer(registry.jobs_dir)
    running = [] if own is None else [j for j in registry.list_running() if j.producer_id == own]
    name = settings.BRAND_SERVICE_NAME
    if not running:
        notify_operator(f"{name} stopped (nothing of its own running).", ring=False)
        return
    notify_operator(
        f"⚠ {name} stopped with {len(running)} run(s) in flight: "
        f"{', '.join(job.what for job in running)}\nResume from the campaign once it is back up.",
        ring=True,
    )


def tell_poll(token: str, refused: str | None) -> None:
    """What the admin bot's poll was answered, told where it changes its channel's standing."""
    _tell(health.poll_answered(default_projects_root(), token, refused))


def tell_power(was: PowerLevel) -> PowerLevel:
    """The level now; an unheard notice keeps the old one, so it is told again next time."""
    now, notice = power_change(was)
    if notice is not None and notify_operator(notice.text, ring=notice.ring) != "sent":
        return was
    return now


def tell_visits_digest() -> None:
    digest = due_digest()
    if digest is not None and notify_operator(digest.text, ring=digest.ring) == "sent":
        mark_told(digest)


def forward_trail_to_briefing() -> bool:
    """What every account did in the last day, and the box's health, sent OUT to n8n.

    Sent whole each time: the receiver keeps the newest, so a missed send costs freshness only,
    and a send that stops arriving is itself the box going quiet.
    """
    url = settings.N8N_TRAIL_WEBHOOK_URL.get_secret_value().strip()
    if not url:
        return False
    trails, unreadable = trails_since(default_projects_root(), time.time() - _TRAIL_WINDOW_H * 3600)
    return post_json(
        url,
        {
            "as_of": utcnow_iso(),
            "window_hours": _TRAIL_WINDOW_H,
            "accounts": [
                {"who": account.who, "lines": trail_fields(account)} for account in trails
            ],
            "unreadable": unreadable,
            "health": health.briefing(default_projects_root()),
        },
        what="Trail forward",
        headers={"X-Trail-Secret": settings.N8N_TRAIL_WEBHOOK_SECRET.get_secret_value().strip()},
    )
