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
from functools import cached_property
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

from promptpotter import connectors
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.datasets.authored import ConfigLayers
from promptpotter.application.embedded_run import run_campaign, run_head_to_head
from promptpotter.application.initialization.scoring_scope import arm_diagnostic_scoring
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.launch import Inline, Launched
from promptpotter.application.jobs.mint import mint_framed_cycle
from promptpotter.application.maintenance.landing import complete_registries
from promptpotter.application.optimizer_manifest import (
    manifest_directories,
    running_prompt,
    select_optimizer,
)
from promptpotter.application.pipeline_resolve import bind_resolution, laid_session, run_resolution
from promptpotter.config.paths import install_datasets_root, optimizer_manifest_path, user_data_root
from promptpotter.config.settings import settings
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.campaign_config import CampaignConfig, CycleConfig
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
from promptpotter.infrastructure.store.dataset_access import FAMILY_FILE, ORIGIN_FILE
from promptpotter.infrastructure.store.io import read_yaml, write_yaml
from promptpotter.shared.plugin_registry import Registry

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.cycle_result import CycleResult
    from promptpotter.domain.records import CycleRecord
    from promptpotter.domain.sample import Sample
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
    """What the network answers a campaign with: its config's templates, its rows, their hardness;
    each derived once a script, however many facts adopt the run it laid."""

    config: CampaignConfig
    rows: int
    hardness: int

    @cached_property
    def samples(self) -> list[Sample]:
        return synthetic_rows(self.rows)

    @cached_property
    def templates(self) -> dict[str, str]:
        return text_templates(self.config)


@dataclass(frozen=True)
class Shared:
    """A run several facts read: the home it left, what its act returned, and the script it ran on,
    none where its act opened its round through `factories.py::opened_round` on answers it declared."""

    home: Path
    made: Any
    script: Script | None

    @contextmanager
    def reading(self) -> Iterator[Stores]:
        """Its home as the data root, for a fact or fixture reading what it left in place, never a
        copy: a sandbox is a workspace of the data root its outer cycle ran under. A read that
        wrote fails at its exit, since every later reader shares the home."""
        before = _tree_digest(self.home)
        with pytest.MonkeyPatch.context() as home:
            home.setenv("PROMPTPOTTER_HOME", str(self.home))
            yield workspace()
        after = _tree_digest(self.home)
        moved = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
        assert not moved, f"a read in place wrote its shared home: {sorted(moved)[:5]}"


def _tree_digest(root: Path) -> dict[str, tuple[int, int]]:
    """Each file under *root*, to its size and write time: a write moves one, a rewrite of the same
    bytes included. Never its bytes, since a lock this process holds (its producer token,
    `producer_lock.py::this_producer`) refuses a read on Windows."""
    return {
        p.relative_to(root).as_posix(): ((st := p.stat()).st_size, st.st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


@dataclass
class World:
    stores: Stores
    net: Router
    scratch: Path
    ran: list[Ran] = field(default_factory=list)
    script: Script | None = None

    def __post_init__(self) -> None:
        # The dataset's definition at the install tier the stores read, as a wheel ships it: once
        # a fact, which every world it builds on its data root reads.
        family = DATASET.split("/")[0]
        if not (installed := self.stores.install_datasets_root / family).is_dir():
            shutil.copytree(install_datasets_root() / family, installed)

    @contextmanager
    def family(self) -> Iterator[dict[str, Any]]:
        """The world's dataset family as its install tier holds it, edited as an operator edits a
        definition: written back as the block exits, for every later opening to read."""
        assert self.stores.install_datasets_root != install_datasets_root(), "the wheel's tier"
        path = self.stores.install_datasets_root / DATASET.split("/")[0] / FAMILY_FILE
        held = read_yaml(path)
        yield held
        write_yaml(path, held)

    def _answer(self, script: Script) -> None:
        """The network answers *script*: every later run of this world, resumes and forks included."""
        capture = self.scratch / f"requests-{len(self.ran)}"
        capture.mkdir(parents=True, exist_ok=True)
        self.net.llm.says = ByCallIdentity(
            capture, script.templates, cites=cited_panel(script.config)
        )
        self.net.backend = FakeBackend(script.samples, hardness=script.hardness)
        self.script = script

    def _lay(self, script: Script) -> None:
        """*script* answered, its rows the cut of the definition's source."""
        self._answer(script)
        stand_in_fetch(self.stores, script.samples)

    def adopt(self, shared: Shared) -> World:
        """This fact's own copy of *shared*'s home, read and run on as if this world had run it: the
        copy holds the run's cut, so only the network is laid, by its script or by the fact."""
        shutil.copytree(shared.home, user_data_root(), dirs_exist_ok=True)
        adopted = World(workspace(), self.net, self.scratch)
        if shared.script is not None:
            adopted._answer(shared.script)
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

    async def minted(self, rows: int = 40) -> CycleHop:
        """A fresh potter campaign minted as `run_campaign` mints it (`jobs/mint.py::mint_framed_cycle`),
        no round run and no job admitted: its root cycle, for a fact reading what a mint writes."""
        config = campaign_config("potter", 0, bench_trigger="at_end")
        self._lay(Script(config, rows, 0))
        async with init_services(
            DATASET, identity=self.stores.identity, stores=self.stores
        ) as session:
            assert session.dataset is not None, "a dataset session resolved its dataset"
            laid, config = laid_session(session, ConfigLayers.of(session.dataset, stated(config)))
            running = await mint_framed_cycle(laid, config, laid.bank.samples, task_text=None)
        return running.hop

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
    async def opened(
        self, rows: int = 40, *, connector: Connector | None = None
    ) -> AsyncIterator[Session]:
        """A session on the world's dataset as every door opens one, scoring as a pass outside a
        run does (`scoring_scope.py::open_diagnostic`); its client closes as the block exits.
        *connector* serves the dataset as a plugin's does: registered (`serving`), named by its
        family, which then lays no node and ships no origin, the graph being the connector's."""
        config = campaign_config("potter", 1, bench_trigger="at_end")
        script = Script(config, rows, 0)
        self._answer(script)
        with pytest.MonkeyPatch.context() as mp:
            if connector is not None:
                serving(mp, connector)
                graph = (connector.pipeline_declaration or self.net.backend.declared)(
                    self.stores, None
                )["nodes"]
                with self.family() as held:
                    declared = held["pipeline"]["nodes"]
                    if graph.keys() != declared.keys():
                        # A node the family never declared runs on the family's task model.
                        owned = {"config": {"model": declared["llm_only"]["config"]["model"]}}
                        held["pipeline"]["nodes"] = {n: declared.get(n, owned) for n in graph}
                        origin = self.stores.install_datasets_root / DATASET / ORIGIN_FILE
                        origin.unlink()
                    held["pipeline"]["connector"] = connector.name
            stand_in_fetch(self.stores, script.samples)
            async with init_services(
                DATASET, identity=self.stores.identity, stores=self.stores
            ) as session:
                laid, config = laid_session(session, ConfigLayers.stating(stated(config)))
                bound = bind_resolution(laid, run_resolution(laid, CycleConfig.laid(config, ())))
                with arm_diagnostic_scoring(bound, config):
                    yield bound


def serving(mp: pytest.MonkeyPatch, *added: Connector) -> None:
    """*added* in the connector table beside the built-ins, as a plugin registers one, for *mp*'s
    span: the table completes on them, so a connector its checks refuse raises here."""
    members = [*connectors._builtins()]
    built_in = {c.name for _, c in members if isinstance(c, Connector)}
    members += [(c.name, c) for c in added if c.name not in built_in]
    table = Registry(connectors.CONNECTORS.group, members.copy, connectors._validate)
    mp.setattr(connectors, "CONNECTORS", table)
    complete_registries()


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
            with offline_network(mp) as net, scoped(account=workspace()):
                ran = World(workspace(), net, home.parent / "world")
                if on is not None and on.script is not None:
                    ran._answer(on.script)
                made = asyncio.run(act(ran))
    finally:
        unrefuse()
    assert not refused, f"a shared run sent what the world does not answer: {refused}"
    return Shared(home, made, ran.script)


@contextmanager
def offline_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[Router]:
    """Its provider caps no rate; the account it sends on is the one its caller bound."""
    for key in PROVIDER_KEYS:
        monkeypatch.setattr(settings, key, SecretStr(NOT_A_KEY))
    monkeypatch.setattr(settings, "RATE_LIMITS", {})
    world = Router(FakeLLM(lambda _body: "ok"), FakeBackend([]))
    restore = install_network(world)
    send_pacing.cache_clear()
    try:
        yield world
    finally:
        restore()
        send_pacing.cache_clear()
    # A refused request reads as a provider error, which a journey can assert past unseen.
    assert not world.unrouted, f"a journey sent what the world does not answer: {world.unrouted}"
