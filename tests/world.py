"""The edge tier's one world: a fact's stores at its own data root, every network edge answered
by `scripts/offline_world.py`, and campaigns minted and run through the real entry
(`embedded_run.py::run_campaign`) on the offline run's script (`scripts/offline_run.py`). A fact
reads what a run left through the doors that serve it; it never seats a cycle by hand.

The `world` fixture (`conftest.py`) builds one per fact and releases it at teardown; a run several
facts read is run once (`shared_run`) and each fact reads its own copy (`World.adopt`)."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from factories import ledger_records, workspace
from offline_run import (
    DATASET,
    ByCallIdentity,
    campaign_config,
    cited_panel,
    stand_in_fetch,
    stated,
    synthetic_rows,
    text_templates,
)
from offline_world import (
    NOT_A_KEY,
    PROVIDER_KEYS,
    FakeBackend,
    FakeLLM,
    Router,
    install_network,
    refuse_network,
)
from pydantic import SecretStr

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.datasets.authored import ConfigLayers
from promptpotter.application.embedded_run import run_campaign, run_head_to_head
from promptpotter.application.initialization.scoring_scope import arm_diagnostic_scoring
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.launch import Inline, Launched
from promptpotter.application.optimizer_manifest import (
    manifest_directories,
    running_prompt,
    select_optimizer,
)
from promptpotter.application.pipeline_resolve import laid_session
from promptpotter.config.paths import install_datasets_root, optimizer_manifest_path, user_data_root
from promptpotter.config.settings import settings
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.command_kinds import (
    ArmRequest,
    Door,
    ForkCyclePayload,
    HeadToHeadRequest,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.draft_edit import CampaignLayer, OptimizationOverrides
from promptpotter.domain.pipeline_manifest import prompt_key
from promptpotter.domain.run_mode import OnDivergence, RunMode
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.registry import send_pacing
from promptpotter.infrastructure.run_scope import scoped

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.cycle_result import CycleResult
    from promptpotter.domain.records import CycleRecord
    from promptpotter.infrastructure.store.stores import Stores


@dataclass(frozen=True)
class Ran:
    """One campaign the world ran: the session its entry opened and what the cycle returned."""

    session: Session
    result: CycleResult

    @property
    def hop(self) -> CycleHop:
        return self.session.hop


@dataclass(frozen=True)
class Script:
    """What the network answers a campaign with: its config's templates, its rows, their hardness."""

    config: CampaignConfig
    rows: int
    hardness: int


@dataclass(frozen=True)
class Shared:
    """A run several facts read: the home it left, what its act returned, and the script it ran on."""

    home: Path
    made: Any
    script: Script

    @contextmanager
    def reading(self) -> Iterator[None]:
        """Its home as the data root, for a fixture reading what it left in place, never a copy:
        a sandbox is a workspace of the data root its outer cycle ran under."""
        with pytest.MonkeyPatch.context() as home:
            home.setenv("PROMPTPOTTER_HOME", str(self.home))
            yield


@dataclass
class World:
    stores: Stores
    net: Router
    scratch: Path
    ran: list[Ran] = field(default_factory=list)
    script: Script | None = None

    def __post_init__(self) -> None:
        # The dataset's definition at the install tier the stores read, as a wheel ships it.
        family = DATASET.split("/")[0]
        shutil.copytree(
            install_datasets_root() / family,
            self.stores.install_datasets_root / family,
            dirs_exist_ok=True,
        )

    def _lay(self, script: Script) -> None:
        """The network answers *script*: every later run of this world, resumes and forks included."""
        samples = synthetic_rows(script.rows)
        capture = self.scratch / f"requests-{len(self.ran)}"
        capture.mkdir(parents=True, exist_ok=True)
        self.net.llm.says = ByCallIdentity(
            capture, text_templates(script.config), cites=cited_panel(script.config)
        )
        self.net.backend = FakeBackend(samples, hardness=script.hardness)
        stand_in_fetch(self.stores, samples)
        self.script = script

    def adopt(self, shared: Shared) -> World:
        """This fact's own copy of *shared*'s home, read and run on as if this world had run it."""
        shutil.copytree(shared.home, user_data_root(), dirs_exist_ok=True)
        adopted = World(workspace(), self.net, self.scratch)
        adopted._lay(shared.script)
        return adopted

    async def campaign(
        self,
        optimizer: str = "potter",
        *,
        rounds: int = 1,
        rows: int = 40,
        step_rounds: int | None = None,
        knobs: dict[str, dict[str, Any]] | None = None,
        hardness: int = 0,
        origin_gate: str | None = None,
        down: bool = False,
    ) -> Ran:
        """A fresh campaign of *optimizer* over *rows* synthetic rows, run through the real entry;
        *step_rounds* pauses it at that round's boundary, as `--step` does; *origin_gate* gates
        its origin, and *down* has the backend refuse every cell, as the `--gated` leg launches."""
        config = campaign_config(optimizer, rounds, bench_trigger="at_end", knobs=knobs)
        if origin_gate is not None:
            gated = config.optimization.model_copy(update={"origin_gate": origin_gate})
            config = config.model_copy(update={"optimization": gated})
        self._lay(Script(config, rows, hardness))
        self.net.backend.down = down
        session, result = await run_campaign(
            DATASET,
            stated(config),
            stores=self.stores,
            readout_sink=lambda _line: None,
            budget=Budget(),
            step_rounds=step_rounds,
            mode=RunMode(),
        )
        return self._ran(session, result)

    async def resume(
        self,
        hop: CycleHop,
        *,
        from_round: int | None = None,
        on_divergence: OnDivergence = OnDivergence.HALT,
        step_rounds: int | None = None,
    ) -> Ran:
        """The cycle at *hop*, resumed through the same entry: rewound to *from_round* when named,
        paused at *step_rounds*' boundary as `resume --step` does."""
        session, result = await run_campaign(
            hop,
            stores=self.stores,
            readout_sink=lambda _line: None,
            budget=Budget(),
            step_rounds=step_rounds,
            mode=RunMode(from_round=from_round, on_divergence=on_divergence),
        )
        return self._ran(session, result)

    async def fork(self, hop: CycleHop, round_num: int, **asked: Any) -> Ran:
        """A fork of *hop* at *round_num* through the `fork_cycle` command door, run inline."""
        payload = ForkCyclePayload(
            campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, round=round_num, **asked
        )
        dispatched = await CommandDispatcher(self.stores, inline=Inline()).dispatch(
            CommandCall(payload, door=Door.TERMINAL)
        )
        launched = dispatched.result
        assert isinstance(launched, Launched) and launched.held is not None, launched
        session, result = await (await launched.held).run_inline(readout_sink=None)
        return self._ran(session, result)

    def edit_proposer(self) -> None:
        """The proposer's template edited in this home's shadow of its manifest, as an operator's
        edit lands (`config/paths.py::optimizer_manifest_path`): a resume after it diverges."""
        assert self.script is not None, "a proposer is edited under a campaign's script"
        selected = select_optimizer(self.script.config.optimization)
        node_config = selected.node_config(selected.proposer)
        key = prompt_key(node_config)
        held = running_prompt(selected.proposer, node_config, selected.manifest)
        for directory in manifest_directories(selected.name):
            authored = yaml.safe_load(optimizer_manifest_path(directory).read_text("utf-8"))
            if key in (authored.get("resolved_prompts") or {}):
                break
        else:
            raise AssertionError(f"no manifest of {selected.name!r} holds {key!r}")
        # Appended, so the network still knows the node by its template's opening.
        authored["resolved_prompts"][key]["problem_description"] = (
            f"{held.problem_description}\n\nEdited on resume."
        )
        shadow = user_data_root() / "optimizers" / directory.name / "pipeline.yaml"
        shadow.parent.mkdir(parents=True, exist_ok=True)
        shadow.write_text(yaml.safe_dump(authored, sort_keys=False, allow_unicode=True), "utf-8")
        assert optimizer_manifest_path(directory) == shadow, "the manifest shadow is not read"

    def _ran(self, session: Session, result: CycleResult) -> Ran:
        ran = Ran(session, result)
        self.ran.append(ran)
        return ran

    def ledger(self, hop: CycleHop) -> CycleEventLog:
        return CycleEventLog.open(CycleDir(self.stores.campaigns.cycle_dir(hop)))

    def records(self, hop: CycleHop) -> list[CycleRecord]:
        return ledger_records(self.ledger(hop))

    async def head_to_head(
        self, arms: dict[str, str], h2h_id: str, *, rounds: int = 1, rows: int = 40
    ) -> list[Ran]:
        """Arms of one head-to-head, minted in one act through the real entry
        (`embedded_run.py::run_head_to_head`): *arms* maps each arm's key to its optimizer, each
        laying its optimizer and node knobs over the first's declaration, as `head-to-head` does."""
        configs = {
            key: campaign_config(name, rounds, bench_trigger="at_end") for key, name in arms.items()
        }
        first = next(iter(configs.values()))
        self._lay(Script(first, rows, 0))
        templates = {n: t for c in configs.values() for n, t in text_templates(c).items()}
        self.net.llm.says = ByCallIdentity(
            self.scratch / f"requests-{len(self.ran)}", templates, cites=cited_panel(first)
        )
        sent = {key: c.model_dump(mode="json", exclude_unset=True) for key, c in configs.items()}
        request = HeadToHeadRequest(
            head_to_head_id=h2h_id,
            arms=[
                ArmRequest(
                    arm_key=key,
                    optimization=OptimizationOverrides.model_validate(
                        {
                            "optimizer": arms[key],
                            "nodes": sent[key]["optimization"].get("nodes", {}),
                        }
                    ),
                )
                for key in arms
            ],
        )
        # What every arm shares: the first's declaration less what each arm lays over it.
        shared = sent[next(iter(arms))]
        for knob in ("optimizer", "nodes"):
            shared["optimization"].pop(knob, None)
        ran = await run_head_to_head(
            DATASET,
            CampaignLayer.model_validate(shared),
            request,
            stores=self.stores,
            mode=RunMode(),
            readout_sink=lambda _line: None,
        )
        return [self._ran(session, result) for _, session, result in ran]

    @asynccontextmanager
    async def opened(self, rows: int = 40) -> AsyncIterator[Session]:
        """A session on the world's dataset as every door opens one, scoring as a pass outside a
        run does (`scoring_scope.py::arm_diagnostic_scoring`); its client closes as the block exits."""
        config = campaign_config("potter", 1, bench_trigger="at_end")
        self._lay(Script(config, rows, 0))
        async with init_services(
            DATASET, identity=self.stores.identity, stores=self.stores
        ) as session:
            laid, config = laid_session(session, ConfigLayers.stating(stated(config)))
            with arm_diagnostic_scoring(laid, config):
                yield laid


def shared_run[T](
    factory: pytest.TempPathFactory,
    act: Callable[[World], Coroutine[Any, Any, T]],
    *,
    on: Shared | None = None,
) -> Shared:
    """*act* run once on a world at its own home, for a module- or session-scoped fixture: each fact
    reading it takes its own copy (`World.adopt`), so no fact reads what another left. *on* starts
    it from a copy of that shared run, so a run several fixtures build on is run once."""
    home = factory.mktemp("shared") / "home"
    if on is not None:
        shutil.copytree(on.home, home)
    refused: set[str] = set()
    unrefuse = refuse_network(refused)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("PROMPTPOTTER_HOME", str(home))
            with offline_network(mp) as net:
                ran = World(workspace(), net, home.parent / "world")
                if on is not None:
                    ran._lay(on.script)
                made = asyncio.run(act(ran))
    finally:
        unrefuse()
    assert not refused and ran.script is not None, f"a shared run sent {refused} or ran nothing"
    return Shared(home, made, ran.script)


@contextmanager
def offline_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[Router]:
    """Its provider caps no rate and binds one account, the terminal's, as a sending entry does."""
    for key in PROVIDER_KEYS:
        monkeypatch.setattr(settings, key, SecretStr(NOT_A_KEY))
    monkeypatch.setattr(settings, "RATE_LIMITS", {})
    world = Router(FakeLLM(lambda _body: "ok"), FakeBackend([]))
    # Upstream lists one row of the bundled floor as it stands, so a home a fact opens holds that
    # row (`pricing.py::load_rates`), never its own copy of every row the floor already prices.
    world.rates = dict([next(iter(world.rates.items()))])
    restore = install_network(world)
    send_pacing.cache_clear()
    try:
        with scoped(account=workspace()):
            yield world
    finally:
        restore()
        send_pacing.cache_clear()
    # A refused request reads as a provider error, which a journey can assert past unseen.
    assert not world.unrouted, f"a journey sent what the world does not answer: {world.unrouted}"
