"""What this box answers for, each checked one way: a probe reads held, broken or unverified, and
only a CHANGE of state lands on the box's own workspace ledger, which is also what gets told."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import ClassVar, NamedTuple

import httpx

from promptpotter import connectors
from promptpotter.application.account import registered_or_default_identity
from promptpotter.application.jobs.launcher.admission import probe_backend
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.run_records import HealthRecord, HealthState
from promptpotter.infrastructure.identity.bundle import build_identity_bundle
from promptpotter.infrastructure.identity.paths import default_identity_paths
from promptpotter.infrastructure.identity.provider_config import CodeExchangeError
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.openai_compat import ProviderSpec
from promptpotter.infrastructure.llm.registry import keyed_specs
from promptpotter.infrastructure.store.read_model import LedgerIndex, RecordClasses
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.tls import tls_context


class Reading(NamedTuple):
    state: HealthState
    said: str


class Probe(NamedTuple):
    name: str
    check: Callable[[], Awaitable[Reading]]
    fix: str


def _backend(name: str) -> Probe:
    async def check() -> Reading:
        refused = await probe_backend(name)
        if isinstance(refused, BackendUnreachableError):
            return Reading("broken", str(refused))
        return Reading("held", "reachable")

    return Probe(
        f"backend:{name}", check, f"bring {name} up at {connectors.CONNECTORS.get(name).base_url()}"
    )


def signin_probe(provider: str) -> str:
    return f"signin:{provider}"


# A token endpoint asked to redeem a code nobody issued (RFC 6749 § 5.2, GitHub's own spelling):
# blaming the code means it accepted this server's client, blaming the client means it did not.
_NOBODYS_CODE = "promptpotter-health-probe"
_CLIENT_ACCEPTED = frozenset({"invalid_grant", "bad_verification_code"})
_CLIENT_REFUSED = frozenset(
    {
        "invalid_client",
        "unauthorized_client",
        "incorrect_client_credentials",
        "redirect_uri_mismatch",
    }
)


def _signin(provider: str, redeem: Callable[[], Awaitable[object]], oidc: Path) -> Probe:
    async def check() -> Reading:
        try:
            await redeem()
        except CodeExchangeError as answer:
            if answer.error in _CLIENT_REFUSED:
                return Reading(
                    "broken", f"{provider} refused this server's client ({answer.error})"
                )
            return Reading(
                "held" if answer.error in _CLIENT_ACCEPTED else "unverified", answer.error
            )
        except httpx.HTTPError as exc:
            return Reading("unverified", f"unreachable: {type(exc).__name__}")
        return Reading("unverified", "a code nobody issued was redeemed")

    fix = f"set {provider}'s client_id / client_secret in {oidc}, then restart the app"
    return Probe(signin_probe(provider), check, fix)


def _llm_key(provider: str, spec: ProviderSpec, key: str) -> Probe:
    async def check() -> Reading:
        try:
            async with httpx.AsyncClient(timeout=10, verify=tls_context()) as client:
                resp = await client.get(
                    spec.base_url + spec.key_check, headers={"Authorization": f"Bearer {key}"}
                )
        except httpx.HTTPError as exc:
            return Reading("unverified", f"unreachable: {type(exc).__name__}")
        if resp.status_code in (401, 403):
            return Reading(
                "broken", f"{spec.display_name} refused this server's key (HTTP {resp.status_code})"
            )
        return Reading("held" if resp.is_success else "unverified", f"HTTP {resp.status_code}")

    fix = f"set {spec.api_key_attr} in the app's environment file, then restart the app"
    return Probe(f"llm:{provider}", check, fix)


def probes() -> list[Probe]:
    """The roster: every remote backend service, every configured sign-in provider, every LLM key."""
    paths = default_identity_paths()
    bundle = build_identity_bundle(paths)
    redeem: dict[str, Callable[[], Awaitable[object]]] = {}
    if bundle.google is not None:
        redeem["google"] = partial(
            bundle.google.exchange_code, code=_NOBODYS_CODE, expected_nonce=_NOBODYS_CODE
        )
    if bundle.github is not None:
        redeem["github"] = partial(bundle.github.exchange_code, code=_NOBODYS_CODE)
    return [
        *(
            _backend(name)
            for name, connector in connectors.CONNECTORS.table.items()
            if connector.execution == "remote_http"
        ),
        *(_signin(provider, call, paths.provider_config) for provider, call in redeem.items()),
        *(_llm_key(provider, spec, key) for provider, (spec, key) in keyed_specs().items()),
    ]


class _Standing:
    records: ClassVar[RecordClasses] = (HealthRecord,)

    def __init__(self) -> None:
        self._last: dict[str, HealthRecord] = {}

    def feed(self, record: HealthRecord) -> None:
        self._last[record.probe] = record

    def value(self) -> dict[str, HealthRecord]:
        return dict(self._last)


def _box(projects_root: Path) -> WorkspaceDir:
    """The box's own tenant: what fails for every account is kept where its host reads."""
    return build_stores(registered_or_default_identity(), projects_root=projects_root).base_dir


def standing(projects_root: Path) -> dict[str, HealthRecord]:
    """Each probe's last change; no record reads as held."""
    ledger = CycleEventLog.workspace_path(_box(projects_root))
    return LedgerIndex.of(ledger, (_Standing,)).view(_Standing)


def is_broken(projects_root: Path, name: str) -> bool:
    last = standing(projects_root).get(name)
    return last is not None and last.state == "broken"


class Checked(NamedTuple):
    probe: Probe
    reading: Reading
    # The change this check recorded, or None where the probe stood where it was.
    change: HealthRecord | None


async def check(projects_root: Path) -> list[Checked]:
    roster = probes()
    readings = await asyncio.gather(*(probe.check() for probe in roster))
    last = standing(projects_root)
    checked = []
    for probe, reading in zip(roster, readings, strict=True):
        was = last[probe.name].state if probe.name in last else "held"
        change: HealthRecord | None = None
        match reading.state:
            case "held" | "broken" as state if state != was:
                change = HealthRecord(probe=probe.name, state=state, said=reading.said)
                CycleEventLog.open_workspace(_box(projects_root)).append(change)
        checked.append(Checked(probe, reading, change))
    return checked


def notice(checked: Checked) -> str | None:
    """What the operator hears: a change, and nothing else."""
    if checked.change is None:
        return None
    if checked.change.state == "broken":
        return f"⚠ {checked.probe.name} is BROKEN: {checked.reading.said}\nFix: {checked.probe.fix}"
    return f"✓ {checked.probe.name} works again."


def row(checked: Checked) -> str:
    said = f"{checked.probe.name}: {checked.reading.state} ({checked.reading.said})"
    return f"{said}; fix: {checked.probe.fix}" if checked.reading.state == "broken" else said


def briefing(projects_root: Path) -> dict[str, dict[str, str | None]]:
    """Every probe's standing state, since when, and its fix."""
    last = standing(projects_root)
    return {
        probe.name: {
            "state": last[probe.name].state if probe.name in last else "held",
            "since": last[probe.name].timestamp if probe.name in last else None,
            "fix": probe.fix,
        }
        for probe in probes()
    }
