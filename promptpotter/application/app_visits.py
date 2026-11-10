"""Who reached this app from outside without signing in, kept beside the website's visits, and the
day's account of them the operator hears.

A row is a fact (what was asked, what answered); scanner, bot, person and fetcher are read off the
rows at digest time, never written onto them. The digest is written for an operator who is no web
person: people first, a week's trend beside them, everything else named for what it is.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NamedTuple

from promptpotter.infrastructure.store.site_records import (
    SiteRecord,
    default_site_record_store,
)
from promptpotter.shared.clock import epoch_seconds, utcnow_iso

APP_VISITS = "app_visits"
# One row per day told, keyed by its local date: what was said, and that it was.
APP_VISIT_DIGESTS = "app_visit_digests"
_KEEP_DAYS = 90
_DIGEST_HOUR = 7
_WINDOW_S = 24 * 3600
_DAYS_SHOWN = 7
_FIELD_CHARS = 300
# The app's page calls its API as it opens; an address that never did fetched a page nobody opened.
_PAGE_CALL = "/api/"

# What a scanner hunts, by family, worded for someone who never ran a server; a request matching
# none is no probe.
_HUNTS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("secret config files (.env)", r"\.env\b"),
        ("the server's settings (/proc)", r"/proc/"),
        ("files outside the app (../)", r"\.\./|%2e%2e|\.\.%2f"),
        ("source code history (.git)", r"/\.git\b"),
        ("cloud and SSH keys", r"\.aws|id_rsa|\.ssh/"),
        ("WordPress pages", r"wp-|wordpress"),
        ("PHP pages", r"\.php\b|phpmyadmin"),
        ("a GraphQL endpoint", r"graphql"),
        ("admin panels", r"actuator|/console|/admin\b|/manager/|cgi-bin"),
    )
)
_AGENT = re.compile(
    r"bot|crawl|spider|slurp|indexer|preview|externalhit|python|curl|wget|go-http|httpx|java/"
    r"|okhttp|scrapy|headless|libwww|axios|node-fetch|zgrab|masscan|nmap|nuclei|n8n",
    re.IGNORECASE,
)
_AGENT_NAME = re.compile(r"[A-Za-z][\w.-]*(?:bot|crawler|spider|indexer|Preview)", re.IGNORECASE)


def keep_visit(
    *,
    ip: str,
    country: str,
    method: str,
    path: str,
    query: str,
    status: int,
    ua: str,
    referrer: str,
) -> None:
    """A query is kept only where nothing answered: a served one may carry a sign-in code."""
    row = {
        "ip": ip,
        "country": country,
        "method": method,
        "path": path[:_FIELD_CHARS],
        "query": query[:_FIELD_CHARS] if status >= 400 else "",
        "status": status,
        "ua": ua[:_FIELD_CHARS],
        "referrer": referrer[:_FIELD_CHARS],
    }
    default_site_record_store().keep(APP_VISITS, SiteRecord(at=utcnow_iso(), key="", row=row))


def _hunted(row: dict[str, Any]) -> list[str]:
    asked = f"{row.get('path', '')}?{row.get('query', '')}"
    return [label for label, pattern in _HUNTS if pattern.search(asked)]


def _agent_name(ua: str) -> str:
    named = _AGENT_NAME.search(ua)
    return named.group(0) if named else (ua.split("/", 1)[0].strip() or "no user agent")


@dataclass
class _Address:
    ip: str
    first: float
    last: float
    requests: int = 0
    countries: Counter[str] = field(default_factory=Counter)
    hunts: Counter[str] = field(default_factory=Counter)
    agents: Counter[str] = field(default_factory=Counter)
    paths: set[str] = field(default_factory=set)
    # A probe something answered: the one finding that is not routine.
    answered: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        if self.hunts:
            return "scanner"
        if self.agents:
            return "bot"
        return "person" if any(p.startswith(_PAGE_CALL) for p in self.paths) else "fetcher"

    @property
    def country(self) -> str:
        return self.countries.most_common(1)[0][0]


def _addresses(records: Iterable[SiteRecord]) -> list[_Address]:
    by_ip: dict[str, _Address] = {}
    for record in records:
        at, row = epoch_seconds(record.at) or 0.0, record.row
        ip = str(row.get("ip", ""))
        address = by_ip.setdefault(ip, _Address(ip=ip, first=at, last=at))
        address.first, address.last = min(address.first, at), max(address.last, at)
        address.requests += 1
        address.countries[str(row.get("country") or "?")] += 1
        address.paths.add(str(row.get("path", "")))
        ua = str(row.get("ua", ""))
        if not ua or _AGENT.search(ua):
            address.agents[_agent_name(ua)] += 1
        hunted = _hunted(row)
        address.hunts.update(hunted)
        status = int(row.get("status", 0))
        if hunted and 200 <= status < 300:
            address.answered.append(f"{row.get('path')} ({status})")
    return list(by_ip.values())


def _span(seconds: float) -> str:
    if seconds < 120:
        return f"{seconds:.0f} s"
    return f"{seconds / 60:.0f} min" if seconds < 7200 else f"{seconds / 3600:.0f} h"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _top(counts: Counter[str], n: int = 3) -> str:
    return ", ".join(f"{name} {count}" for name, count in counts.most_common(n))


class VisitsDigest(NamedTuple):
    key: str
    text: str
    # Rings only where a probe was answered; a refused crawl is routine.
    ring: bool


def _countries(addresses: Iterable[_Address]) -> Counter[str]:
    return Counter(a.country for a in addresses)


def _local(at: float) -> str:
    local = datetime.fromtimestamp(at).astimezone()
    return f"{local:%a} {local.day} {local:%b %H:%M}"


def _trend(days: list[list[SiteRecord]], *, now: float, began: float) -> str:
    """People per day, oldest first; a day that ended before counting began has no number."""
    counts = [
        "—"
        if now - back * _WINDOW_S <= began
        else str(sum(1 for a in _addresses(days[back]) if a.kind == "person"))
        for back in reversed(range(len(days)))
    ]
    said = f"People per day, oldest to today: {' · '.join(counts)}"
    return (
        f"{said} (counting began {_local(began)})" if began > now - len(days) * _WINDOW_S else said
    )


def _scans(scanners: list[_Address], answered: list[str]) -> list[str]:
    verdict = "SOME GOT AN ANSWER (see top)" if answered else "all blocked"
    lines = [f"· Attack scans, robots trying known weak spots: {len(scanners)}, {verdict}"]
    lines += [
        f"   ↳ {c.ip} ({c.country}): {_plural(c.requests, 'try', 'tries')} in "
        f"{_span(c.last - c.first)}, after {', '.join(label for label, _ in c.hunts.most_common(3))}"
        for c in scanners[:5]
    ]
    if len(scanners) > 5:
        lines.append(f"   ↳ and {len(scanners) - 5} more")
    return lines


def visits_digest(records: Iterable[SiteRecord], *, key: str, now: float) -> VisitsDigest:
    """The last day read for its people first, beside a week of them; *records* may reach further back."""
    days: list[list[SiteRecord]] = [[] for _ in range(_DAYS_SHOWN)]
    began = now
    for record in records:
        at = epoch_seconds(record.at) or 0.0
        began = min(began, at)
        back = int((now - at) // _WINDOW_S)
        if 0 <= back < _DAYS_SHOWN:
            days[back].append(record)
    seen = _addresses(days[0])
    kinds = {kind: [a for a in seen if a.kind == kind] for kind in ("person", "bot", "fetcher")}
    scanners = sorted((a for a in seen if a.kind == "scanner"), key=lambda a: -a.requests)
    answered = [path for a in scanners for path in a.answered]

    lines = ["👥 Visitors to the app, last 24 h"]
    if answered:
        lines.append(f"⚠ An attack scan got an answer. Check these now: {', '.join(answered[:5])}")
    people = kinds["person"]
    if people:
        reached = sum(1 for a in people if any(p.startswith("/login") for p in a.paths))
        started = sum(1 for a in people if any("/auth/login/" in p for p in a.paths))
        lines.append(
            f"People who opened the app: {len(people)}, from {_top(_countries(people), 4)}. "
            f"{reached} reached the sign-in page, {started} started signing in."
        )
    else:
        lines.append("People who opened the app: none.")
    lines.append(_trend(days, now=now, began=began))

    others = [a for a in seen if a.kind != "person"]
    if others:
        lines.append(
            f"Not people: {_plural(len(others), 'address', 'addresses')}, "
            f"{_plural(sum(a.requests for a in others), 'request', 'requests')}"
        )
    if bots := kinds["bot"]:
        names: Counter[str] = Counter()
        for a in bots:
            names.update(a.agents.keys())
        lines.append(
            f"· Bots that say so (search engines, AI, tools): {len(bots)} — {_top(names, 6)}"
        )
    if fetchers := kinds["fetcher"]:
        lines.append(
            f"· Fetched a page but never opened it (link previews, checkers, unnamed bots): "
            f"{len(fetchers)} — from {_top(_countries(fetchers), 4)}"
        )
    if scanners:
        lines += _scans(scanners, answered)
    lines.append("Signed-in users and your own browser are never counted here.")
    return VisitsDigest(key=key, text="\n".join(lines), ring=bool(answered))


def due_digest() -> VisitsDigest | None:
    """The day's digest once its hour has passed on this box's clock, if it was not told yet."""
    at = time.time()
    local = datetime.fromtimestamp(at).astimezone()
    key = local.date().isoformat()
    store = default_site_record_store()
    if local.hour < _DIGEST_HOUR or any(r.key == key for r in store.kept(APP_VISIT_DIGESTS)):
        return None
    return visits_digest(store.kept(APP_VISITS), key=key, now=at)


def mark_told(digest: VisitsDigest) -> None:
    """Kept only once heard, so a refused send is retried; the visits past retention go with it."""
    store = default_site_record_store()
    store.keep(
        APP_VISIT_DIGESTS,
        SiteRecord(at=utcnow_iso(), key=digest.key, row={"text": digest.text, "rang": digest.ring}),
    )
    store.drop_before(APP_VISITS, time.time() - _KEEP_DAYS * 24 * 3600)
