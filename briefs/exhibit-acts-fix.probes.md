# exhibit-acts review probes (adapt their paths; run from a checkout with PYTHONPATH pinned to it)

## exa_probe.py
```python
"""Pytest plugin: logs what the pre-diff rule (every kind filable) would have offered each reader."""
import promptpotter.application.hierarchy as hier
import promptpotter.application.jobs.launcher.admission as adm
import promptpotter.application.served_dashboard as sd
from promptpotter.domain.command_kinds import CommandKind
from promptpotter.domain.hierarchy import RunAdmission
from promptpotter.domain.refusal import Refusal

_orig = adm.admitted


def _spy(stores, campaign, run):
    got = _orig(stores, campaign, run)
    refused = adm.campaign_refusal(stores, campaign)
    old = RunAdmission.of(
        run,
        campaign_refusal=None if refused is None else Refusal.of(refused),
        filable=tuple(CommandKind),
    )
    print(
        f"\nPROBE reader={stores.identity.tenant_id} caps={len(stores.identity.capabilities)} "
        f"phase={run.run_phase} new.offers={got.offers} new.refusal={got.refusal and got.refusal.code} "
        f"old.offers={old.offers} old.refusal={old.refusal and old.refusal.code} "
        f"filable_n={len(got.filable)} json_bytes={len(got.model_dump_json())}"
    )
    return got


adm.admitted = _spy
hier.admitted = _spy
sd.admitted = _spy
```

## exa_probe2.py
```python
"""Pytest plugin: prints the ETag version each admission's reader is answered under, beside its filable."""
import hashlib

import promptpotter.application.hierarchy as hier
import promptpotter.application.jobs.launcher.admission as adm
import promptpotter.application.served_dashboard as sd

_orig = adm.admitted


def _spy(stores, campaign, run):
    got = _orig(stores, campaign, run)
    version = hier.entries_version([stores])
    digest = hashlib.sha256(repr(version).encode()).hexdigest()[:12]
    print(f"\nPROBE2 reader_caps={len(stores.identity.capabilities)} filable={len(got.filable)} version={digest}")
    return got


adm.admitted = _spy
hier.admitted = _spy
sd.admitted = _spy
```

## partial_caps.py
```python
"""RunAdmission.of across readers: all, none, step-only, run-only, run+lookahead (no create)."""
from promptpotter.domain.command_kinds import CommandKind, filable
from promptpotter.domain.hierarchy import RunAdmission
from promptpotter.domain.phases import ProducerReading, ProducerState, RunPhase, RunState, StopReading, StopReason
from promptpotter.domain.refusal import Refusal
from promptpotter.shared.errors import MINT_FRESH
from promptpotter.shared.identity import (
    CAMPAIGN_RUN_CAP, CAMPAIGN_STEP_CAP, CAMPAIGN_BUDGET_CAP, OWNER_COMMAND_CAPABILITIES, default_identity,
)
from dataclasses import replace

def reader(caps):
    return filable(replace(default_identity("t"), capabilities=frozenset(caps)))

READERS = {
    "owner": reader(OWNER_COMMAND_CAPABILITIES),
    "none": reader(()),
    "step-only": reader({CAMPAIGN_STEP_CAP}),
    "run-only": reader({CAMPAIGN_RUN_CAP}),
    "run+budget(no create)": reader({CAMPAIGN_RUN_CAP, CAMPAIGN_BUDGET_CAP, CAMPAIGN_STEP_CAP}),
}
live = ProducerReading.of(ProducerState.LIVE)
absent = ProducerReading.of(ProducerState.ABSENT)
held = ProducerReading.of(ProducerState.HELD)
moved = Refusal(code="definition_moved", message="Its definition moved: start a fresh campaign.", details={}, next_acts=(MINT_FRESH,))
CASES = {
    "running": (RunState(RunPhase.RUNNING, live), None),
    "gate": (RunState(RunPhase.GATE, held), None),
    "budget-stopped": (RunState(RunPhase.PAUSED, absent, stop=StopReading(stop_reason=StopReason.BUDGET_EXHAUSTED, cause=None, detail="", at="t")), None) if hasattr(StopReason, "BUDGET_EXHAUSTED") else None,
    "detached+campaign-moved": (RunState(RunPhase.DETACHED, absent), moved),
}
for name, case in CASES.items():
    if case is None:
        continue
    run, refused = case
    for who, kinds in READERS.items():
        try:
            a = RunAdmission.of(run, campaign_refusal=refused, filable=kinds)
        except Exception as exc:  # noqa: BLE001
            print(f"{name:26} {who:22} ERROR {exc!r}")
            continue
        r = a.refusal
        print(f"{name:26} {who:22} offers={a.offers!s:6} refusal={(r.code if r else None)!s:18} acts={[x.kind for x in (r.next_acts if r else ())]} msg={(r.message if r else '')[:60]!r}")
```

## proposed_rule.py
```python
"""The review's proposed #5 rule beside the landing's, over readers x run states.

Proposed: `read_only` only where the run asks something (a verb or a named act) and the reader
files none of it; otherwise the run's own refusal stands, its acts passed through `offered`."""
from dataclasses import replace

from promptpotter.domain.command_kinds import RUN_VERB_KIND, CommandKind, filable, offered
from promptpotter.domain.hierarchy import _READ_ONLY, RunAdmission
from promptpotter.domain.phases import RUN_PHASE_INFO, ProducerReading, ProducerState, RunPhase, RunState
from promptpotter.domain.refusal import Refusal
from promptpotter.shared.errors import MINT_FRESH
from promptpotter.shared.identity import (
    CAMPAIGN_BUDGET_CAP, CAMPAIGN_CREATE_CAP, CAMPAIGN_RUN_CAP, CAMPAIGN_STEP_CAP,
    OWNER_COMMAND_CAPABILITIES, default_identity,
)


def reader(caps):
    return filable(replace(default_identity("t"), capabilities=frozenset(caps)))


def proposed(run, campaign_refusal, kinds):
    own = RunAdmission.of(run, campaign_refusal=campaign_refusal, filable=tuple(CommandKind))
    action = RUN_PHASE_INFO[run.run_phase].action
    verb = RUN_VERB_KIND[action] if action else None
    named = () if own.refusal is None else own.refusal.next_acts
    kept = offered(named, kinds)
    if (verb or named) and (verb is None or verb not in kinds) and not kept:
        return None, _READ_ONLY
    if own.refusal is None:
        return own.offers, None
    r = own.refusal
    return None, Refusal(code=r.code, message=r.message, details=r.details, next_acts=kept)


READERS = {
    "owner": reader(OWNER_COMMAND_CAPABILITIES),
    "none (exhibit)": reader(()),
    "step-only": reader({CAMPAIGN_STEP_CAP}),
    "run-only": reader({CAMPAIGN_RUN_CAP}),
    "run+step+budget": reader({CAMPAIGN_RUN_CAP, CAMPAIGN_BUDGET_CAP, CAMPAIGN_STEP_CAP}),
    "create-only": reader({CAMPAIGN_CREATE_CAP}),
}
live, absent, held = (ProducerReading.of(s) for s in (ProducerState.LIVE, ProducerState.ABSENT, ProducerState.HELD))
# Stand-in: a campaign refusal naming a fresh mint (its wording is this probe's, not the product's).
stand_in = Refusal(code="campaign_refused_standin", message="<campaign refusal>", details={}, next_acts=(MINT_FRESH,))
CASES = {
    "running": (RunState(RunPhase.RUNNING, live), None),
    "running, not attached": (RunState(RunPhase.RUNNING, absent), None),
    "queued": (RunState(RunPhase.QUEUED, absent), None),
    "gate": (RunState(RunPhase.GATE, held), None),
    "detached": (RunState(RunPhase.DETACHED, absent), None),
    "detached+campaign refusal": (RunState(RunPhase.DETACHED, absent), stand_in),
}
for name, (run, refused) in CASES.items():
    for who, kinds in READERS.items():
        landed = RunAdmission.of(run, campaign_refusal=refused, filable=kinds)
        offers, r = proposed(run, refused, kinds)
        lr = landed.refusal
        print(
            f"{name:26} {who:16} landing: {landed.offers!s:5} {(lr.code if lr else '-'):26}"
            f" | proposed: {offers!s:5} {(r.code if r else '-'):26} acts={[a.kind for a in (r.next_acts if r else ())]}"
        )
```
