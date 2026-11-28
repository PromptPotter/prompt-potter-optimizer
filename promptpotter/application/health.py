"""What this box answers for, each checked one way: a probe reads held, broken or unverified, and
only a CHANGE of state lands on the box's own workspace ledger, which is also what gets told."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import ClassVar, NamedTuple

import httpx

from promptpotter import connectors
from promptpotter.application.account import (
    SignInRefusal,
    record_refusal,
    registered_or_default_identity,
)
from promptpotter.application.datasets.startability import offer_connectors
from promptpotter.application.jobs.launcher.admission import probe_backend
from promptpotter.config.paths import (
    default_projects_root,
    source_checkout_root,
    webapp_static_root,
)
from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.connector import BACKEND_REACHABILITY_INFO
from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.domain.records.workspace import HealthReader, HealthRecord, HealthState
from promptpotter.domain.said import Said
from promptpotter.infrastructure.identity.accounts import build_identity_bundle
from promptpotter.infrastructure.identity.grants import default_identity_paths
from promptpotter.infrastructure.identity.oidc import CodeExchangeError
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.openai_compat import ProviderSpec
from promptpotter.infrastructure.llm.registry import keyed_specs
from promptpotter.infrastructure.store.read_model import LedgerIndex, RecordClasses
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.tls import tls_context


class Reading(NamedTuple):
    state: HealthState
    said: str
    by: HealthReader = "probe"


class Probe(NamedTuple):
    name: str
    check: Callable[[], Awaitable[Reading]]
    fix: str


def _backend(connector: Connector, endpoint: str | None) -> Probe:
    async def check() -> Reading:
        # No address is no answer: said on every check, never recorded or told as broken.
        if (unset := connector.unconfigured(endpoint)) is not None:
            return Reading("unverified", unset)
        # Any refusal a launch would get is broken: a missing extra refuses it as surely.
        refused = await probe_backend(connector, endpoint)
        if refused is None:
            return Reading("held", BACKEND_REACHABILITY_INFO["live"].label)
        return Reading("broken", str(refused))

    return Probe(
        f"backend:{connector.name}" + (f":{endpoint}" if endpoint else ""),
        check,
        connector.unconfigured(endpoint)
        or f"bring {connector.name} up at {connector.base_url(endpoint)}",
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


def telegram_refusal(status: int) -> str | None:
    """What a Telegram answer says that no retry changes; None where one may."""
    if status in (401, 403, 404):
        return f"Telegram refused the admin bot's token (HTTP {status})"
    # Telegram holds a bot's messages for ONE reader: a webhook or a second poller takes them.
    return "another poller or a webhook holds the bot" if status == 409 else None


_CHANNEL = "channel:telegram"


def _operator_channel(token: str) -> Probe:
    """The channel every other notice rides: dead, it says nothing, so its health is checked here."""

    async def check() -> Reading:
        try:
            async with httpx.AsyncClient(
                base_url=f"https://api.telegram.org/bot{token}", timeout=10, verify=tls_context()
            ) as client:
                me = await client.get("/getMe")
                if (refused := telegram_refusal(me.status_code)) is not None:
                    return Reading("broken", refused)
                hook = await client.get("/getWebhookInfo")
        except httpx.HTTPError as exc:
            return Reading("unverified", f"unreachable: {type(exc).__name__}")
        if not (me.is_success and hook.is_success):
            return Reading("unverified", f"HTTP {me.status_code} / {hook.status_code}")
        if hook.json().get("result", {}).get("url"):
            return Reading("broken", "a webhook holds the bot, so commands never arrive")
        return Reading("held", f"@{me.json().get('result', {}).get('username', '?')}")

    fix = (
        "give the admin bot a bot of its own, and set its current token (@BotFather, /mybots, "
        "API Token) as ADMIN_BOT_TELEGRAM_TOKEN in the app's .env; restart the app and the admin bot"
    )
    return Probe(_CHANNEL, check, fix)


# What `npm run build` stamps beside the bundle (`webapp/vite.config.ts::BUILD_SOURCE`).
BUILD_SOURCE = "build-source.json"
WEBAPP_BUNDLE = "webapp:bundle"
REBUILD = "run `npm run build` in webapp/, then reload the page"


def webapp_bundle() -> Reading | None:
    """A checkout serves `webapp/out/` as last built, however old: a bundle built from other
    `webapp/` bytes than the checkout's is broken, refusing nothing. None where nothing is built."""
    checkout, served = source_checkout_root(), webapp_static_root()
    if checkout is None or not served.is_dir():
        return None
    # The worktree's bytes as `webapp/vite.config.ts::webappTree` stamps them, in an index of its own
    # copied from the checkout's, so only a file whose stat moved is hashed.
    with tempfile.TemporaryDirectory() as scratch:
        index, at = Path(scratch) / "index", ("--path-format=absolute", "--git-path", "index")
        git = partial(subprocess.run, cwd=checkout, capture_output=True, text=True, timeout=10)
        try:
            shutil.copyfile(git(["git", "rev-parse", *at], check=True).stdout.strip(), index)
            git = partial(git, env={**os.environ, "GIT_INDEX_FILE": str(index)})
            git(["git", "add", "-A", "--", "webapp"], check=True)
            tree = git(["git", "write-tree", "--prefix=webapp/"], check=True).stdout.strip()
        except subprocess.CalledProcessError as exc:
            return Reading("unverified", f"no webapp/ tree to compare: {exc.stderr.strip()}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Reading("unverified", f"no webapp/ tree to compare: {exc}")
    try:
        built = json.loads((served / BUILD_SOURCE).read_text(encoding="utf-8")).get("webapp_tree")
    except (OSError, ValueError, AttributeError):
        built = None
    if built == tree:
        return Reading("held", "built from this checkout's webapp/")
    if built is None:
        return Reading("broken", "the page carries no build stamp")
    return Reading("broken", "the page was built from another webapp/ than this checkout's")


def stale_page() -> Said | None:
    """The page's own build, said where the checkout moved past it: `/health` serves it."""
    bundle = webapp_bundle()
    if bundle is None or bundle.state != "broken":
        return None
    label = "This page is out of date"
    return Said(key=WEBAPP_BUNDLE, tone="warn", label=label, sub=f"{bundle.said}: {REBUILD}.")


def _webapp_probe() -> Probe:
    async def check() -> Reading:
        reading = await asyncio.to_thread(webapp_bundle)
        return reading if reading is not None else Reading("unverified", "no webapp/out/ built")

    return Probe(WEBAPP_BUNDLE, check, REBUILD)


def probes() -> list[Probe]:
    """The roster: every remote backend endpoint the offer reaches, an unaddressed backend
    included, every configured sign-in provider, every LLM key, the operator's channel where one is configured, and a
    checkout's built page."""
    paths = default_identity_paths()
    bundle = build_identity_bundle(paths)
    redeem = {
        provider: partial(client.exchange_code, code=_NOBODYS_CODE, nonce=_NOBODYS_CODE)
        for provider, client in bundle.clients.items()
    }
    reached = offer_connectors()
    return [
        *(
            _backend(connector, endpoint)
            for name, connector in connectors.CONNECTORS.table.items()
            if connector.execution == "remote_http" and (reached is None or name in reached)
            for endpoint in connector.probed_endpoints()
        ),
        *(_signin(provider, call, paths.provider_config) for provider, call in redeem.items()),
        *(_llm_key(provider, spec, key) for provider, (spec, key) in keyed_specs().items()),
        *(
            [_operator_channel(token)]
            if (token := settings.ADMIN_BOT_TELEGRAM_TOKEN.get_secret_value().strip())
            else []
        ),
        *([_webapp_probe()] if source_checkout_root() is not None else []),
    ]


class _LastProbes:
    records: ClassVar[RecordClasses] = (HealthRecord,)

    def __init__(self) -> None:
        self._last: dict[str, HealthRecord] = {}

    def feed(self, record: HealthRecord) -> None:
        self._last[record.probe] = record

    def value(self) -> dict[str, HealthRecord]:
        return dict(self._last)


# Joined to the roster where the registries complete (`maintenance/landing.py::complete_registries`).
LEDGER_FOLDS = (_LastProbes,)


def _box(projects_root: Path) -> WorkspaceDir:
    """The box's own tenant: what fails for every account is kept where its host reads."""
    return build_stores(registered_or_default_identity(), projects_root=projects_root).base_dir


def standing(projects_root: Path) -> dict[str, HealthRecord]:
    """Each probe's last change; no record reads as held."""
    ledger = CycleEventLog.workspace_path(_box(projects_root))
    return LedgerIndex.of(ledger).view(_LastProbes)


def is_broken(projects_root: Path, name: str) -> bool:
    last = standing(projects_root).get(name)
    return last is not None and last.state == "broken"


def sign_in_refused(provider: str, why: str, refusal: SignInRefusal) -> SignInRefusal:
    """A sign-in the provider or its exchange turned down, filed once on the box's trail (nobody
    signed in yet, so it is the box's own to keep). Here, beside the probe it reads: no retry of
    the person's mends a provider the health check found refusing this server."""
    box = build_stores(registered_or_default_identity(), projects_root=default_projects_root())
    record_refusal(box, subject=f"sign-in through {provider}: {why}")
    return "signin_unavailable" if is_broken(box.projects_root, signin_probe(provider)) else refusal


# RFC 6749 § 4.1.2.1's closed set: a refusal is filed in these words, never the query's text.
_PROVIDER_ERRORS = frozenset(
    {
        "invalid_request",
        "unauthorized_client",
        "access_denied",
        "unsupported_response_type",
        "invalid_scope",
        "server_error",
        "temporarily_unavailable",
    }
)


def provider_refused(provider: str, error: str) -> SignInRefusal:
    """The provider's `?error=` on a flow that checked out, in the RFC's words or one fixed word."""
    word = error if error in _PROVIDER_ERRORS else "unrecognized_error"
    return sign_in_refused(provider, f"the provider returned {word}", "provider_returned_error")


class Checked(NamedTuple):
    probe: Probe
    reading: Reading
    # The change this check recorded, or None where the probe stood where it was.
    change: HealthRecord | None


def _recorded(
    projects_root: Path, probe: Probe, reading: Reading, last: HealthRecord | None
) -> Checked:
    """*reading* lands on the ledger where it changes *last*, its probe's standing; `unverified`
    never does."""
    was = last.state if last is not None else "held"
    change: HealthRecord | None = None
    match reading.state:
        case "held" | "broken" as state if state != was:
            change = HealthRecord(probe=probe.name, state=state, said=reading.said, by=reading.by)
            CycleEventLog.open_workspace(_box(projects_root)).append(change)
    return Checked(probe, reading, change)


async def _read(probe: Probe, last: HealthRecord | None) -> Reading:
    """What the admin bot's poll was refused stands on its channel until a poll answers again: no
    probe of Telegram sees a second poller, so while one stands no probe second-guesses it."""
    if last is not None and last.state == "broken" and last.by == "poll":
        return Reading("broken", last.said, by="poll")
    return await probe.check()


async def check(projects_root: Path) -> list[Checked]:
    roster = probes()
    last = standing(projects_root)
    readings = await asyncio.gather(*(_read(probe, last.get(probe.name)) for probe in roster))
    return [
        _recorded(projects_root, probe, reading, last.get(probe.name))
        for probe, reading in zip(roster, readings, strict=True)
    ]


def poll_answered(projects_root: Path, token: str, refused: str | None) -> Checked:
    """The admin bot's poll is its channel's one reader, so what it was answered is the channel's
    standing: a refusal stands until a poll is answered, which holds the channel again."""
    reading = Reading(
        "held" if refused is None else "broken",
        "polled" if refused is None else f"refused: {refused}",
        by="poll",
    )
    last = standing(projects_root).get(_CHANNEL)
    return _recorded(projects_root, _operator_channel(token), reading, last)


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
