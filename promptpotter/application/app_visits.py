"""Who reached this app from outside without signing in, kept beside the website's visits, and the
day's account of them the operator hears.

A row is a fact (what was asked, what answered); scanner, crawler and visitor are read off the rows
at digest time, never written onto them.
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
_FIELD_CHARS = 300

# What a scanner hunts, by family; a request matching none is no probe.
_HUNTS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        (".env files", r"\.env\b"),
        ("process environment", r"/proc/"),
        ("path traversal", r"\.\./|%2e%2e|\.\.%2f"),
        ("git metadata", r"/\.git\b"),
        ("cloud and SSH keys", r"\.aws|id_rsa|\.ssh/"),
        ("WordPress", r"wp-|wordpress"),
        ("PHP", r"\.php\b|phpmyadmin"),
        ("GraphQL", r"graphql"),
        ("admin consoles", r"actuator|/console|/admin\b|/manager/|cgi-bin"),
    )
)
_AGENT = re.compile(
    r"bot|crawl|spider|slurp|indexer|preview|externalhit|python|curl|wget|go-http|httpx|java/"
    r"|okhttp|scrapy|headless|libwww|axios|node-fetch|zgrab|masscan|nmap|nuclei",
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
        return "crawler" if self.agents else "visitor"


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


def visits_digest(records: Iterable[SiteRecord], *, key: str, hours: int) -> VisitsDigest:
    seen = _addresses(records)
    kinds = {
        kind: [c for c in seen if c.kind == kind] for kind in ("scanner", "crawler", "visitor")
    }
    total = sum(c.requests for c in seen)
    lines = [
        f"🕷 Outside traffic to the app, last {hours} h: {total} requests from "
        f"{len(seen)} addresses without a sign-in."
    ]
    scanners = sorted(kinds["scanner"], key=lambda c: -c.requests)
    answered = [path for c in scanners for path in c.answered]
    if scanners:
        verdict = "⚠ some probes were ANSWERED" if answered else "every probe refused"
        lines.append(f"Scanners {len(scanners)}, {verdict}:")
        lines += [
            f"· {c.ip} ({c.countries.most_common(1)[0][0]}): {c.requests} requests in "
            f"{_span(c.last - c.first)}, hunting {', '.join(label for label, _ in c.hunts.most_common(3))}"
            for c in scanners[:5]
        ]
        if len(scanners) > 5:
            lines.append(f"· and {len(scanners) - 5} more")
        if answered:
            lines.append(f"Answered: {', '.join(answered[:5])}")
    crawlers = kinds["crawler"]
    if crawlers:
        requests: Counter[str] = Counter()
        addresses: Counter[str] = Counter()
        for c in crawlers:
            requests.update(c.agents)
            addresses.update(c.agents.keys())
        named = [
            f"{name} ({_plural(addresses[name], 'address', 'addresses')}, "
            f"{_plural(count, 'request', 'requests')})"
            for name, count in requests.most_common(6)
        ]
        lines.append(f"Crawlers: {', '.join(named)}")
    visitors = kinds["visitor"]
    if visitors:
        countries: Counter[str] = Counter()
        for c in visitors:
            countries[c.countries.most_common(1)[0][0]] += 1
        login = sum(1 for c in visitors if any(p.startswith("/login") for p in c.paths))
        signin = sum(1 for c in visitors if any("/auth/login/" in p for p in c.paths))
        lines.append(
            f"Visitors {len(visitors)} from {_top(countries, 4)}: {login} opened the sign-in "
            f"page, {signin} started a sign-in."
        )
    if not seen:
        lines.append("Nobody.")
    return VisitsDigest(key=key, text="\n".join(lines), ring=bool(answered))


def due_digest() -> VisitsDigest | None:
    """The day's digest once its hour has passed on this box's clock, if it was not told yet."""
    at = time.time()
    local = datetime.fromtimestamp(at).astimezone()
    key = local.date().isoformat()
    store = default_site_record_store()
    if local.hour < _DIGEST_HOUR or any(r.key == key for r in store.kept(APP_VISIT_DIGESTS)):
        return None
    since = at - _WINDOW_S
    window = [r for r in store.kept(APP_VISITS) if (epoch_seconds(r.at) or 0.0) >= since]
    return visits_digest(window, key=key, hours=_WINDOW_S // 3600)


def mark_told(digest: VisitsDigest) -> None:
    """Kept only once heard, so a refused send is retried; the visits past retention go with it."""
    store = default_site_record_store()
    store.keep(
        APP_VISIT_DIGESTS,
        SiteRecord(at=utcnow_iso(), key=digest.key, row={"text": digest.text, "rang": digest.ring}),
    )
    store.drop_before(APP_VISITS, time.time() - _KEEP_DAYS * 24 * 3600)
