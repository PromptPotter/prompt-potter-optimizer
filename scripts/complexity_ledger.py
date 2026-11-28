"""What the next change costs, counted — and the ceiling that holds each row where it stands.

    python scripts/complexity_ledger.py            # print the ledger
    python scripts/complexity_ledger.py --check    # the gate's `surface-ledger` check

A row fails only where it rose above its count at the merge-base with the line the branch was cut
from (``processes.py::branch_base``, as ``gate.py --changed`` reads it), counted by
that commit's own script over its own tree, so a fall edits nothing and slack a cut left can't be
refilled. A raise edits its ``BASELINE`` row, the ceiling it may reach, with a written reason in the
COMMIT BODY (root ``CLAUDE.md`` § Working).

A script and not a module of the package: it counts every layer, the suite and the generated
contract beside them, none of which an installed wheel carries.
"""

from __future__ import annotations

import ast
import collections
import importlib
import inspect
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import typing
from collections.abc import Iterator
from pathlib import Path

from processes import REPO, branch_base, clone_cache, git, replace_changed, run

from promptpotter.shared.import_walk import import_statements

# The ceiling each row's last raise declared; a row that never rose past it in this branch ignores it.
BASELINE = {
    # Lines of the package's own Python: a change removes more than it adds (operator, 2026-11-13),
    # judged, not counted (2026-11-14): a raise names the substance its lines carry.
    # webb-5b (+138): the output contract's flattening, moved from the browser (-275 lines there).
    # faults-1 (+12): a stop's own words served and narrated at every door, a refused send's among
    # them; a payload field's terminal flag declared once beside the payload; refusal acts checked.
    # coupon (+80): the host-admin `set_coupon` verb and its bot door, the coupon's window off its
    # grant; the daily quota counting campaigns minted, and the coupon's room net of a campaign's history.
    # narr-3 (+17): the readout builds the diff table and the parent's preview off the round's
    # records, where the writers pre-built them into the acts; (+15) a cycle's one append door, which
    # a muted scope mutes and a scope bound on that cycle lends its handle, and a verb refusing
    # another's scope; (+105, net of the dashboard's dead meter branches) a step's facts typed by kind
    # (a count, a fitness in its scorer's unit, a text, settings) and worded by the renderer alone,
    # with a fold's fitness built once; the round's opening as such facts, a critique a round lacks
    # said, the parent's fitness banked on L2's box, each candidate's
    # rendered prompt in the population table, a readout naming the InitClosed it missed, and the
    # browser's skip refusal served off the run, apart from the launch's admission.
    # ren (+29): `Panel` named `RoundSamples`, so twelve proposers' `propose` signatures wrap, each
    # beside a samples parameter only gepa reads (filed); net of two dead definitions deleted.
    # model-lines (+22): the bench's one optimizer call target, typed (`CallTarget`), laid under
    # every llm node by the one node fold (`PipelineManifest.calling`) and read by a bare selection
    # (`bench_choice`); net of the manifest validator selection already ran, and 22 yaml lines gone.
    # mkey (+516): a door, an account's API tokens: the token store and its SURVIVOR row, three
    # command kinds, the bearer's resolution through the one identity builder, the stream's per-tick
    # re-check; its fix pass the token cap, `account.self` and a cancel's rung.
    # rk-rest (+57): each connector's typed experiment model (decision 6) and a call's booked cost.
    # faults-2a (+143): a run phase's hold and the store's, landing's, harbor's and quota's refusals
    # as typed `Refusal` rows with acts, checked against their payload; a field's positional or flag
    # declared beside the payload; served acts read at every door; the command log's own file kind;
    # YAML composed once, bounded in Python, through the one parse door capo's reply reads too.
    # mcp (+272): a held task's form elicitation and its once-filed answer; the host registry over
    # Streamable HTTP, one session per caller, a mount a declared row.
    # passb-2 1 (+55): a verify's served admission (`PassAdmission`, its ladder on the shared
    # `PassRefusal`, the cycle's `verify` fact, the command refused by it); a slot's served stage.
    # passb-2 2 (-8): the held-out verb has one name; the export has one writer.
    # faults-2b (-451): a fault is a value; `logging` leaves application/ and domain/.
    # tv-2 (+100): the host's exhibit, the one cross-tenant read, resolved once (`exhibit.py`),
    # and the follow pick's fall-through to it, else a served `Said`. tv-2 (+74): a held task's
    # input on a 2026-07-28 wire (`tasks/get`'s input requests, `tasks/update`), and the HTTP
    # door's managers swept once their sessions end. tv-2 (+1): the exhibit's pipeline read.
    # tv-2's access fix (+9): a sandbox opens only off its store's own cycle, a moment stays on
    # its address's tree, and a deleted exhibit is followed by no one.
    # passb-2 typed log (+36): a command log row typed by its kind's recorded shape on load.
    # lite-2 + la-2 (+29): OpenRouter's endpoints payload as one model, validated at the fetch
    # (+34); the hold fold beside its one reader, a sample fold's dead flag, a `--json` launch
    # asked nothing (-5). addr (-9).
    # money-2 (+18): the standing budget's named-only patch and its arrival-order fold, and the
    # browser sign-in moved above admission so the host's claim holds the destroyers' gate; the
    # `owned_campaign` alias gone.
    # faults-3 1 (+8): each line fold names its file's kind, the ruler its own code;
    # `LineIndex.of` takes none. faults-3 2 (+35): a periodic step's last pass on disk, read by every
    # door's machine-status (the in-process global reached no CLI), and each cycle the reaper
    # cannot read named in it. faults-3 3 (+1): the CLI words `unloadable`.
    # small-4 (+100): the archive's three new facts, each its substance: an answer's filing run and
    # an L4 cell's inner campaign, the lookup `inventory` names per entry, and the `reclaim` mode
    # with its plan (and the browser's dataset scope it needs), net of one door for every rewriting
    # pass and one mode table; one RoundContext per round (0). addr-2 (-29): a family root and a
    # retired arm read from the index, lineage keyed by address, a served short label. io (+14):
    # one read open retried past a replace's swap, for every reader (only one text read retried).
    # valid 4 (-7): a fresh mint lays the one span it writes, so `HeldRun.of` reads nothing again.
    # tests-w (+20): the launch's scope and its line's open, named so the harness opens as
    # `run_optimization` does (its hand copy, -30, is in tests/). tests-w 4 (+1): a home that
    # fetched no rates reads the floor's one table, never a copy keyed by its absent file.
    # l1-edits (+35): L1's edit list applied in one place, its missed anchor an arm's own reason,
    # and every optimizer call's record on failure too, its error on the node's audit block; the
    # all-no-op round and the pool twin gone. l4-runs (+39): a bench member whose point moved banks
    # its pass refused and the fold reads it so, and a nested run is filed as its spawner's kind;
    # (+28) an entry finding a landing in flight says so and waits, off the CLI's loop.
    # limits-ux (+268): one served reading per cap (`ArmedCaps`), its clamp on the ceiling record,
    # its wording table, and `set-limits` answering it at every door. draw-strata (+8): a round's
    # order interleaves the benchmarks its rows measure, so a cut arm has read every one.
    # flight-ux (+140): the sample look-ahead at every door: a launch's depth filed as its press,
    # the CLI verb, and the served reason a deeper press is not admitted, with its readout.
    # strata-flight (-39): limits-ux's look-ahead refusal is flight-ux's `shallow`, and the Max
    # press rides the one payload by name (`LookaheadCells`). flight-axes (+159): samples per
    # candidate and candidates at once on the one press, an inner run following Max speed, words.
    # l4-outer link 4 + its fold (+138): the critique's own-row panel, the length budget, and the
    # webapp:bundle probe that says a page older than its checkout.
    # flight-share A (+55): a backend's ceiling is one count of every phase sending on its machine,
    # a run holding cells leaves one to a run holding none, and a wait polls another run's landing.
    # flight-share B (+76): the spend book's count by part (`SpendCount`), read by its stop, its
    # room and its refusal, served as every money cap's `used` and `cap_lines`, and a walk's halt.
    # l4-outer-6 (+4): `PanelShareKnobs`, each layout node's discretionary share as a knob.
    # land-order 1 (+22): the landing stamp's package version, and older code's refusal to land.
    # land-order 2 (+10): `landing_key` hashes each reader's and each migration's body.
    # land-order fix (+10): older code reads the landed version alone (`_LandedVersion`, lax),
    # never the strict stamp a newer release may have reshaped.
    # l4-valid (+19 net, -14 of it `CellOf` and the per-round pin): `BACKEND_HOLE`, the stop a
    # backend's unfilled cell ends on in its words, and the outer's one re-ask of such a cell.
    # gate-hold (+4): only a launch's ask refuses a busy admission gate, and the sweep asks each
    # orphan alone under it, again, after a scan off it.
    # small-5 (+13): `RepairAttempt`, each re-asked attempt's finish, token split and length.
    # entry-land (+8): `Verb.lands_at_entry`, so a deploy's dry run and its pre-landing verbs write
    # nothing the old code still serves or a roll-back would run on.
    # small-7 (+3): a line-file rewrite left empty deletes the file, so a reclaim leaves no 0-byte cells.
    # small-7 (+27): `archive-datasets`, the served scopes the archive control picks from.
    # l4-words-fix (+25, all the migration): job files v0.8.20 ended on `panel_cut` land on
    # `panel_hole` (`migrations/job_endings.py`); the chain is net 0 beside it.
    # spend-served (+19): the bench's set-aside is a hold on the payer its cells draw, and the
    # stop record and the dashboard word the book's count by part.
    # panel-rule-2 (+24): `Connector.declared_panel_model` and `layer_fold.py::LaidAs`, so a
    # panel folds family→member by its connector's rule and `inner_campaign` by `CAMPAIGN_RULE`.
    # small-9 (+18): `embedded_run.py::HostDefinition`, a host's definition written past the run's
    # one `enter`, so a compile lands and sweeps once.
    # anchor-words (+1): `l1.py`'s `import re`, so an edit's anchor reads by its words and an
    # outer L1 quoting potter's hard-wrapped prompts lands its edit.
    # refuse-1 link 1 (+49): `errors.py::_field` / `_form`, a union field's refusal in one entry
    # naming its form off the model's schema, and REST's 422 handing `refusing` its route's model.
    # refuse-1 link 2 (+2): `spend_book.py::_said`, the count's words beside the calls out, which a
    # stop now names as a send's refusal does, since both read the one remainder.
    # small-11 link 2 (+28): `mcp/server.py::_Manager.sessions`, the HTTP door's own session table
    # (opened on a reply naming it, ended by DELETE / 404 / the SDK's idle timeout), since mcp 2.3
    # exposes no session-end hook and the sweep may no longer read its private `_server_instances`.
    # live-3 (+40 with its fix pass): the round read serves the round no file holds yet off the
    # dashboard's fold (`RoundView.unfiled`, decided by `LiveDashboardState.unfiled`) and frames a
    # 304 on the leaf store, so no client stitches the two; `MeasurementRole.is_proposal` is the
    # one rule of a round's own arms.
    # store-2f/2g (+52): a write never re-makes the campaign or cycle a delete removed (`io.py::_created`
    # walks down from its anchor, known by its place under a workspace), and `CampaignCycle` carries a
    # launch's pair. A read's validator is `file_sig`, never an mtime alone (−16).
    # webd-2 link 1 (+30): `cycle_reads.py::stamped_frames` and `FrameStamp`, the served contract on
    # every stream frame (an `EventSource` reads no header), and the two stamped frame models served.
    # rates-1 (−6): a launch awaits `pricing.py::refresh_rates`, no refresh thread per mint.
    # webd-3 link 1 (+8): `hierarchy.py::DeleteAct`, the listing serving what Delete files in its
    # payload's own words, so the dialog decides nothing; `campaign_lifecycle` folded in (−9).
    # rates-2 (−5): the refresh keys on the home's rates file, which a failed fetch stamps too.
    # readings-3 (+21): `Matcher.two_valued` on each matcher and `Scorer.two_valued`, the grades a
    # scorer declares 0/1, read by `grade_measurands`, and `read_arms`' replicate means read graded.
    # readings-4 (+14): `CellSheet.two_valued`, the declaration θ and `metric_measurand` read past the
    # scorer (`Scorer.graded` builds it, `responses_binary` reads it), in place of φ off the values;
    # +3: a verify reads the recorded level's band, which a level under two cells lacks.
    # readings-5 link 1 (+28): `matchers.py::_emits_two_valued`, a formula's two-valuedness derived
    # over what emits (a matcher, a judge's score set: `registry.py::two_valued_terms`).
    # link 2 (+4): `RowGrade`, the grades a scorer declares, `hit` among them (`GradeColumn.binary` went).
    # link 3 (+37): `statistics.py::difference_interval` (Newcombe's hybrid score, Welch's), the
    # verify's band of fresh − recorded, and `declared_two_valued`, the one check of a declaration.
    # link 4 (+4): `metric_measurand` maps the core `fitness` channel to its grade, nothing else.
    # store-3 (+14): a draft is a third deleted unit (`io.py::_ANCHORS`), and an anchor is matched
    # whole from the data root (`io.py::_place`), so a nested root and a slug `projects/a/campaigns/b` write.
    # store-3b (+5): `io.py::claim_dir`, a campaign's exclusive create through io; `_created` answers
    # "gone" only where the unit is absent. `cycle_reads.py::write_export` (+1): an export follows the umask.
    # inner-1 (+7): `InstrumentMode.enclosed_as`, the spawner's kind off the inner's `filed_as`; the
    # type moved to `run_scope.py` (`shared/` names no domain kind); +5 is one import's wrap.
    # readings-6 (+2): `VerifyReading.held`'s description names the graded rule (Welch where both
    # sides spread, else Newcombe's construction over each side's band, a constant side's its support's).
    # rates-3 (+9): `RatesFile` holds its last try, last fetch and standing fault, and
    # `RatesFile.unfetched` says them to the sweep's rates step; one cache reader (`_cached`) (−8).
    # desk (+3): `io.py::_place` says why it resolves (8.3 names, links) and `_created` why it makes
    # the dirs on the path as given; `permitted_narrowing` takes `at: Campaign | CampaignCycle`.
    # rates-4 (+14): `refresh_route_answers` returns each row it left with the answer it keeps and
    # its age; `reaper.py::_recorded` is a step's one recorder, the reclaim's own step `orphans`.
    # sidebar-closed (+12): `config_map.py::KNOB_SOURCE_SET_BY` words each knob source and says
    # which no layer set, so the webapp's `UNSET_SOURCES` and `LAYER_WORDS` go.
    # tests-x (+2): `build_stores` refuses a projects root off the data root io places a write from.
    # small-13 (−8): a merged sheet is standing by construction; the fit is keyed by `ArmKey`.
    # usage-2 (−2): `StepUsage` takes its parts from `TokenAccount`'s fields, never a hand list.
    # rk-dec-3 link 1 (+21): `phase.py::render_round_block`, the round readout's μ0, τ and hold off
    # the round `served_rounds` serves the dashboard at that close. link 2 (+16): the cycle read
    # names its headline's column (`Election.ended_on`), the one an unfiltered spread reads.
    # store-4 (+7): `io.py::refuse_off_workspace` matches a store's workspace to `WORKSPACE_SHAPES`
    # through the anchor's own `_place` (one compare, `_plain` folded in).
    # store-5a (+5): a population's signature carries a scoped task's own walk counts, so a round
    # close reloads only the populations it moved; `LastOf`, a declared fold, where `last_fold`
    # made record classes in module globals.
    # feed-version (+61 over 112198): a subject's version only moves forward (`cycle_reads.py::FeedVersion`,
    # one count per owner from stats such as `machine_status.py::machine_version`'s, never a reader's
    # view, walked and counted under one lock, a vanished store gone and a subject no walk finds
    # dropped) and a command's ack names the versions its write produced.
    # exhibit-acts (+65 over 112259): `command_kinds.py::filable` / `offered`, the dispatcher's
    # capability map read by every offer, and `RunAdmission`, moved beside `HierarchyState`.
    # exhibit-acts-fix 1 (+77): every served offer built for its reader where it is built (skip,
    # verify, held-out, stubs, a hold's decision), the kinds served once per tree and on `/auth/me`.
    # 2 (+11): a read's version covers its reader's kinds, the feed counted per reader.
    # 5 (+2): a run's own refusal stands, its acts narrowed to its reader's; `read_only` only where
    # its reader files none of what the run asks.
    # 6 (+3): `RunAdmission.stop_acts`, the stop's acts its reader files, where the client filtered.
    # 8 (+11): `host_surface.py::ListTools`, the MCP listing as its session's identity files it.
    # 9 (+13): `filable`'s docstring names what stands outside the map; `holding` -> `worded_refusal`
    # wraps `RUN_PHASE_INFO`'s detached row (+6); the launch test reads `RUN_VERB_KIND`.
    # vote (+98): `paired_election.vote`, the bar's paired cells chosen once (`election.py::seated`)
    # and banked on `RoundWinner`; `origin_panel_cells`, the panel's own size, refused past the origin.
    # addr-3 (+59) less addr-4 (−20) = +39: a run declaration is a fold patch over `defaults.yaml`
    # that refuses a list it cannot lay (`layer_fold.py::patch_between`), and an arm is one
    # `RunSpec` planned on its seat and numbered once at the mint (`ArmSeat`). bkeys-3 (+21): the
    # landing key hashes the code a load runs (`build_ts_types.py::loading_modules`'s schema walk).
    # addr-4's fix pass (+85): a cycle files the cell identity it drew under and a moved one
    # refuses (`moved_leaves` over the drawn record, +45); a null clears on every layer and
    # `unbounded` is the one no-bound word on each budget axis (+19); the archive and the ruler
    # are addressed by `config_key` (+23); the rest nets −2.
    # bkeys-4 (+90): the landing key's code is the import closure of what a load calls, one walk
    # the gate's scripts share (`shared/import_walk.py`, moved out of `scripts/last_green.py`);
    # +10, a plugin manifest keys what it can run; −16, a round file stores facts (`RoundResult`
    # dumps no computed field). bkeys-4 fix (+31): a file is walked from its own tree
    # (`import_walk.py::_tree`) and hashed off the walk's one parse; the walk reads every
    # `KindTable` (`kinds.py::KindTable.every`). W1's plugin key (±0): one rule, its own files and
    # the import closure its members, its files and the run reach (`optimizer_manifest.py::behaviour_key`).
    # w1-rebase (±0): W1 (+260 over 112176) replayed on main's 112539, the resolution adding none.
    # l4-key-b (+83): the archive row's one strict reader and a landing that drops a line cache's
    # rows by why (link 9, +105), net of links 1-8; the hunt found no dead code in the arc's files.
    # spend-tomb (+24): the campaign cap count folds every cycle's book (`served_dashboard.py::
    # spend_counts`), a sandbox banks each inner campaign at its address, a verify's difference band.
    # l4-key-c (−4) and the web chain (±0) land beside them.
    # rows-hook (+62): `BlockRace.choose_rows`, the order a block race takes each walk's untaken
    # pool in at each block's open; `Walk.budget`, how many of its pool a walk takes, so its replay
    # state covers every row it may take; `Walk.reorder`, which re-seats each cell out or back by
    # sample id (the in-round measuring loop's seam; ml-3, −1, rides it past a close on the press).
    # ml-2 (+163): `pobb.row_choice` / `refit`, the race choosing each block's rows by expected
    # information (`rasch.py::most_informative`, `theta_on_ruler`) and the sampler's pool it reads
    # (`RoundSamples.pool`); a `refused` coupling severity, so informative off `overlap` stops the mint.
    # ml-4 (+187): `race.py::_hand_out`, per_round's allocation of a block's calls by what each
    # walk's next call tells, and `QueryLoopState`'s per-row reads, built when a row is first taken.
    # ml-5 (+257): `rolling` dispatch, a decision on every returned row (`_ScoringPhase.roll`,
    # `arrive`), the calls it warrants (`race.py::_settles`), and the read order a resume replays.
    # ml-6 (its commit's count) and ml-fork (+30: `ForkDecline`, `ThetaCaveat.withholds`).
    # ml-7 (+89 over 113402): the race's one call (`race_protocol.py::Decider.decide`) and the row
    # policy any eliminator hands its view to (`row_policy.py::send_rows`), modules of their own;
    # the turn wheel, `Untaken`, `Stretch` and `choose_rows` they replace net −; the walk keyed by arm.
    # The loop's line merged with the chains above, the resolution adding none.
    # tw-0…tw-4 (+1 past it, +40 of their own): a structural invariant leaves the suite for the
    # load it guards (`select_optimizer`'s reply order), `fill` and `flight` read one
    # `Walk.sendable`, and the rules the suite reached through a seated race stand on their own
    # (`EliminationCut.of`, `RowKnobs.rows_ran`); a home listing no rates shares the floor's table.
    # tw-r2 (+84): what a walk bought and none took is named (`records/untaken.py`, its fold and
    # `Walk.untaken_bought`), so the running campaign leaves out its own press, nothing else.
    # tw-r3 (+3): `_ArchiveRecorder._file` files only a cell no address names (one filing rule).
    # tw-x (+22): the level series folds off the ruler, its scope and reach
    # (`difficulty.py::level_series_of`), so its rules are a table, not a seated session.
    # tw-j (+11): `node_context.py::closed_round` takes the three fields it reads, not the `Cycle`,
    # and `own_rows` is the parent's fold as a pure function, so its facts are a table.
    # job-endings (+4): the migration also lands v0.8.20's `converged` on `optimizer_exhausted`.
    # ui-roots (+39): a round a rebase re-elected names its parent's candidates (`LineOwners`), and
    # a cycle a supersede retired refuses a start, naming its fork (`RunState.runs_on`).
    # eps-tie (+17): the coupling naming an ε a tied opening field reaches (`couplings.py::POBB`),
    # and the ε knob saying a cut also ends an arm's election under `vote: overlap`.
    # round (+162): each selector owns its round: the crown, how the round ended (`RoundEnding`,
    # its replay) and the cells its coverage floor counts (`Selector.covered`); potter crowns on the
    # round's pairs (`paired.py::read_pair`), and the `next_parent` and `not_separated_stall` switches
    # the paper ablates join; net of the bench's crowning bar, `BarRead`, `cells_read`, `theta_point`
    # and the high-water L2/L3 read beside the stall stream (`HighWater`).
    # rebase-line (+13): a fork's line served whole: the candidates its bars lead with below its
    # cut (`SubtreeCycleEntryFacts.inherited`), and its Spend figure billing its chain.
    # v0.8.21 (−134): the three migrations v0.8.20 landed on the box go, with the retired claim row.
    # w7-small (+12): health probes only the offer's connectors (`offer_connectors`), and a sandbox
    # owner names its own hop (`SandboxOwner.hop`).
    # w7-text (−807): `log.md` and `review.md` excised; one round block off `ServedRound`.
    # w7-host (+239): the host's bulletins, a survivor beside the accounts: its board, its post and
    # dismiss commands, the account's read at the API and the terminal, and the admin bot's `/bulletin`.
    # w7-line (+19): the dashboard serves `active`, `successor` and `line`, and each line arm its
    # open cells; `measuring_arm`, the race fold and the subtree's `inherited` go.
    # w7-open (+59): one `opening.py::Opening` per run, the `LaunchRequest` shape its five kinds
    # share (the embedded and inner doors among them), and `jobs/shares.py` beside quota.
    # w7-gate (+6): `config/` gets its layering row, so first-run's `.env` write moves under
    # `application/` (`first_run.py::keep_api_key`), its prompt to the CLI that asks it.
    # w7-gate (+2): `health.py::webapp_bundle` hashes the worktree's `webapp/` in an index of its own.
    # w7-web (+78): the words the browser composed are served (a lookahead's segments, a subject's
    # cell means, the lifetime bill and its caveats, a sample movement's tone); 12 unread fields go.
    # w7-ver2 (+85): a writer stamps its subject's version (`io.py::stamp`, `Stores.stamps`), each
    # read compares stamps, and a held closed series is not re-sent; `remove_bare_dir` prunes a stamp.
    # w7-rows (+160): five host-admin bot verbs become command kinds through the dispatcher
    # (payloads, `operator_acts.py`, the served contract), and `claim_default` re-keys dismissals.
    # w7-open2 (+93): a fork is a `ForkLaunch` request kind whose `open` cuts it, admitted before its
    # cut on its campaign (`domain/job.py::launch_campaign`), which quota and shares read.
    # w7-rows2 (+3): `SetCouponPayload.budget` is typed (unbounded included); `RunState.successor` is one Said.
    # w7-web2 (−9): six unread served fields and two browser derivations go.
    # w7-rows4 (+4): `CommandResult` in `shared/` (the MCP host door imports no `cli`); `JobRegistry` refuses a dir
    # not named `jobs`, which, as a stamp subject read off the path, would stamp nothing; `_SUBJECTS` goes.
    # w7-open3 (+3): one admission gate holds a fork's share and its cut (`LaunchRequest.stand`); the
    # opening's exit is the cut's one withdraw (`_unfork` goes).
    # w7-rows5 (+2): a fork's failed withdraw names the stub it kept, as a note on the refusal it rides.
    # w7-rows4 link 3 backed out (+5): the stamp subject read off the path by name is unsound (a dataset
    # named `jobs` takes its workspace's stamps), so `stamped_subject` declares each root again.
    # w7-res (+43): one `pipeline_resolve.py::Resolution` compiles the scorer off the bank's recorded
    # `PartitionRecord.verifier_graded`, and the `--set` grammar moves to `application/path_patch.py`.
    # w7-res2 (+17): one `RunResolution` is the fold the read and the run call; it replaces
    # `configure_and_apply_pipeline`, `ParamLayers.of_cycle`, `recorded_schema` and `_CampaignMerge`.
    # w7-spend (+20): `MeteredSpend.of` serves the spend words the browser composed (bill, rate-priced,
    # replay and buckets lines); the stop record banks `SpendRollup`, and four unread fields go.
    # w7-res3 (-7): a draft's merge is a `RunResolution`, and `Session.pipeline_schema` goes.
    # w7-rows6 (-31): a Refusal carries its notes; every read of a cycle's draw is `drawn_partition`.
    # withdraw-bulletin (+69): the host takes a bulletin back at its terminal and its bot,
    # its dismissals with it; without it, a posted word stood forever.
    # w8-spine (−44): the repair's readers go with their producer; `Pass.recorded` goes.
    # w8-gate (+5): `io.py::_durable` reads `Kind.durable` (and `complete_durable`); seven callers' flags go.
    # w8-resolve (+4): `SchemaResolution` is the base a dataset read and a run share; `ReadoutProjection` takes its inputs.
    # w8-launch (+61): the embedded launch and head-to-head file their commands (`Hosted`), a mint
    # takes `step_rounds`, `new --draft` starts a kept check-in; `EmbeddedMint`/`EmbeddedResume` go.
    # w8-resolve2 (+24): an answer files its `Grading` (scoring + judges) and `open_cell` compiles off
    # it through the one `graded_scorer`, so the cell read resolves no dataset.
    # w8-web (+60, webapp −167): the browser's spend per round, dock order, measured sets and first
    # order are served facts now (`SpendByRound`, `docked`, `measured`, `first_measured`, `line_count`).
    # w8-store (+23): a subject's stamp compacts past 2048 bytes under its epoch's seqlock
    # (`io.py::stamp_of`), so a stamp no longer grows a byte per write; `_SUBJECTS` goes. (+1): five
    # bare stats and the archive's listing read through `io.py` (an import each).
    # w8-spine2 (+10): `CellSheet.filled_from`, the walk fill named once where three sites folded
    # by hand; a prior's catch-up reads its rows under the scorer it files under.
    # w8-l4 (−2): a seat's optimizer owns its nodes (`panel.inner_nodes.<optimizer>`); a content
    # filter's row is graded and cut by the rate, not fatal on one sighting.
    # w8-resolve3 (+38): `Resolution.proven_scorer` refuses a criterion's unowned term before an
    # answer is filed (`campaign_owners`, `refuse_unowned_terms`); a fork's stamp reads its hop's
    # narrowing beside the root's; `current_config` is a `NodeCall`, read by field or `laid_keys()`.
    # w8-launch3 (−6): the sweep alone reclaims; `PathComponent` is the domain's.
    # w8-box2 (+8): a DSPy LM and an installed agent are handed `wallet.py::sending_key`'s key,
    # never the environment's. (+6 at its landing): the OpenBLAS thread cap stays the one act at
    # import, since numpy can load before an entry point's start (16 cores: ~640 MB per process).
    # w8-job (−36): a job holds no ending; how it ended is read off its cycle's stop.
    # Windows stamp fix (+1): a compaction taker seeks to byte 0 before it locks.
    # w8-web2 (+32, webapp −78): a library snippet serves its `lineage_source`, walked once; the
    # dashboard serves `pause_waits_on`, the pause's wording; a lift side's verdict joins its tone in
    # `LIFT_SIDE_INFO`, so the browser words no side.
    # w8-spine3 (−5): an invalid arm has no reason and the envelope no report; a decision is one
    # `DecisionRecord`; a race's priors are sheets of the session scorer.
    # w8-fork (+16): an in-run cut lands its job's share under the gate it cuts under
    # (`fork_siblings.py::cut_run_fork`), whose signature forwards `cut_fork`'s.
    # w8-wallet (+12): a send past a reserve its ceiling sized halts as that ceiling, never as the
    # allowance (`SpendBook.reserve_is_own`, `RESERVE_PER_CEILING`); `slot_rate` folds two helpers.
    # w8-store2 (+24): the data root is spelled once, resolved and long (`paths.py::long_form`), so
    # a job file's `workspace` (a SURVIVOR) is re-spelled by a one-shot migration (`job_workspaces`).
    # w8-launch4 (+3 net of its links): a detached resume's hold carries no session
    # (`run_job.py::HeldLaunch`), so the run's own process opens its cycle once. (+61): a mint's
    # job names its cycle before the mint writes it (`opening.py::Opening.name`), every kind's open
    # taking its `Opening`, so the landing keeps the torn mint its job names, not its workspace.
    # Windows long-root fix (+9): `paths.py::short_form` relates two paths in one spelling and
    # `long_form` normalizes through it; `cell_signatures` reads `file_sig` per file.
    # w8-round (+10): the line's post-election buys are their own row set (`line_results`), so the
    # race a closed round names is the one its election read.
    # w8-spine4 (+93): a walk files its `FoldBasisRecord` and every reader folds its recorded rows
    # under it (`Narration`, the live board's pass), so no row stamps a running fold. (+39): a
    # `PairedReading` is a `ReadPair` or an `UnreadPair`, each arm's fields declared on it. (−8):
    # three fields nobody read go. (−3): round's and spine4's overlap, merged.
    # w9-payer (+36): a cell's payer is resolved per cell (`wallet.py::cells_paid_by`, read through
    # `BackendClient.cells_paid_by` by every in-run planner), where `Connector.cell_payer` declared it.
    # w9-pass (+3): a raced round announces its parent's arm before the walks, so its catch-up is
    # that arm's pass. Set to the measured count at the w9 landing.
    # w10-identity (+159): an account's held keys rest sealed under the box key
    # (`identity/sealing.py`, stdlib, as core reads them), with the survivor's one-shot migration.
    # (+98): the account deleter (`account.py::delete_account`), the host's `delete-account`.
    # (−5): `IdentityContext.issuer`, which nothing read, and the host predicate's second spelling.
    # w10-jobs (+162): a queued launch waits on disk and the freed slot's holder runs it in its own
    # process (`queued_launch.py`, `spawning.py` out of `run_job.py`); the live-jobs migration goes.
    # w10-records (−625): a race's catch-ups are walks of the one loop; θ leaves the round's statement.
    # w10-seal (+97): the box key lives off the data root (`sealing.py::key_path`), each seal names
    # its format and key and binds its account, slot and date, a tag binds the whole file, and a
    # lost key refuses with its remedy (`FileKind.SEALED_KEYS`) instead of being made anew.
    # Set to the measured count at the w10 landing.
    # w11-store (+157) set 114204; plugin −31, scoring −186, config +61, access +50, connectors −40
    # (each its report's count): set to the measured count once they landed (−140).
    # w11-web (+17): the subtree read serves `SubtreeCampaignFacts.candidates`, the count the
    # lineage tree draws, in place of the browser's count of forked cycles.
    # w11-spend (+31): the sub-cent rule and its assert, the money fold's own module header and
    # imports, and `SpendMeter`'s constructor.
    # w11-runner (+90): the cancel shield around a rebase cut, the held run's signatures moved up
    # from `jobs/`, and the imports `round.py`'s split into `measure.py` repeats.
    # w11-scoring2 (−282): the stale-data protocol and the hand fitness means go; set to the measured
    # count.
    # w11-jobs2 (+38): a cycle's and a draft's read serve the refusal their launch meets (+29); a
    # queued Start's job holds its draft against a second command (+9).
    # w11-connectors2 (+9): Harbor's harness fault typed at its door, and the connector's roster-pin
    # declaration.
    # w11-tests3 (+2): the bundle check's scratch index seeds from the checkout's own.
    # w11-web2 (+22): a command route refuses its body by the model it declares
    # (`commands.py::_CommandRoute`), where the app's handler dug it out of `request.scope`.
    # w11-records2 (+113): `run_records.py` splits into ten record families and four non-record
    # modules, each with its own header and imports (+139); the spend rollups' module (+23).
    # w11-reads (+56): a line's cells keyed once per individual (`LineCells`), one pair read per
    # head-to-head request, a restatement's narration, and a follower's tail ending at its switch.
    # w11-tests4 (+7): `spawning.py::command`, the one door the offline world swaps so a launch's
    # child runs in it (+5); `hierarchy.py::store_version` skips a launch not yet landed (+2).
    # w11-store2 (+5): a `Line` names the ledgers that answer for it (`LineScope`, +50), against the
    # landing key's import walk and the hub's definition writer going (−45).
    # w11-config2 (+80): an unreachable source refuses typed, a refused finding is served with its
    # reason, a second pick reopens its check-in, and a chain's modes fold to one name.
    # w11-optim2 (+41): the backbone crowns and races (`optimizer_contract.py::RankingSelector`,
    # `race_protocol.py::BlockRace`, +120) where each peer spelled its own (capo, gepa, levi -56).
    # w11-jobs3 (+9): a job file (a SURVIVOR) re-held as one `hop` by its one-shot migration
    # (`migrations/job_hops.py`, +24), against `UNRESOLVED_HOP` and the mint's split land going.
    # w11-jobs3 (+1): `launch_preflight` resolves its config once and hands it to `sends_on`.
    # w11-web3 (+15): a key's removal answers the slot it emptied (`account.py::RemovedKey`) and
    # replays it, and refuses a key not held, where it answered a bare 202 either way.
    # w11-connectors3 (−7): each connector's tunables are its own `EnvBlock`, and harbor's panel
    # recipe folds into its connector, against `settings.py`'s fields and `harbor_panel.py` going.
    "package_lines": 114311,
    # `model_copy(update=…)` calls: each a copy `StrictModel.model_copy` revalidates, keys no rename sees.
    # small-4 (+1): `spawn.py` stamps the inner campaign on the cell's facts; `l4/proxies.py`, which
    # builds them, is hashed into the L4 estimator, so stamping there re-keys every banked L4 cell.
    "model_copy_updates": 156,
    # The `__init__.py` files that re-export names instead of staying empty.
    "reexport_shims": 0,
    # Campaign-config knobs, plus the node knobs each optimizer member declares.
    # model-lines (+1): `optimization.call_target`, which replaced eleven node `model` lines.
    # l4-outer-4 (+4): `TranscriptKnobs` on `l1_generate` and `l1_critique`, the transcript caps
    # a family declares (the L4 critique reads every seat's whole inner run); +1 `length_budget`,
    # the one bound on a proposal's length, replacing a 3,200-char cap and a 35% rewrite floor.
    # l4-outer-6 (+4): `discretionary_chars` on the four layout nodes, the panel share a family
    # sizes, replacing a `NODE_LAYOUTS` constant per node.
    # vote (+2): `paired_election.vote` and `origin_panel_cells`, the overlap vote and its panel's size.
    # ml-2 (+2): `pobb.row_choice` and `pobb.refit`, the measuring loop's row-choice switches.
    # ml-4 (+1): `pobb.shape`, whether a block's rows are shared or each walk's own.
    # ml-5 (+2): `pobb.dispatch` and `pobb.read_order`, when the race decides and in what order.
    # crown (+2): `paired_election.crown` and `next_parent` and `not_separated_stall` join, the
    # paper's ablations of what a round crowns, ends on and counts; the bench's `crowning_bar` goes.
    # w11-optim2 (106, under the 107 a cut left): `levi_refine.failure_below` and
    # `map_elites.lloyd_iterations`, the reference's constants made node config.
    "config_leaf_fields": 106,
    # `Settings` fields — what the environment can set.
    # tv-2 (+1): `EXHIBIT_CAMPAIGN`, the setting that names the exhibit.
    # w10-seal (+1): `BOX_KEY_FILE`, the box key's path off the data root (the unit's credential).
    "settings_env": 40,
    # Parameters annotated `Any`.
    "any_params": 34,
    # `dict[str, Any]` maps declared in `domain/`.
    "domain_any_maps": 48,
    # Models that opt out of `StrictModel`'s `extra="forbid"`.
    # rk-rest (+2): `HarborPanel` and `DbllmbenchPanel`, a backend's panel keys the bench never reads.
    # land-order (+1): `landing.py::_LandedVersion`, a newer release's stamp read for its version.
    "models_lax": 12,
    # Injections the optimizers' members price — each a block an optimizer prompt can carry.
    "injections": 32,
    # Escalation rules, priced the same way: the round, climb and heal tables together, since a
    # transition written as a branch beside the tables was policy the count could not see.
    "escalation_rules": 11,
    # Function-local imports of the package's own modules. The bare invocation's first-run check
    # is an application read, and `--help` loads no use case, so it is named as a string in `main()`.
    # l4-key-b (+1): `embedded_run.py::enter` imports the landing, as `run_job.py`'s entry does,
    # since only an entry lands.
    "deferred_imports": 7,
    # `TYPE_CHECKING` imports on a file-level import cycle: a knot hidden, not cut (`_type_cycles`).
    "type_cycles": 51,
    # Files naming three or more `CycleRecord` arms, each a site the next arm must reach.
    "p_record": 21,
    # The arms each file naming most of them leaves out: a mirror rotting raises it.
    # hubs-2 (-36): its 36 were `ledger_scan.py`'s folds and `live_dashboard.py`'s `_handle_*`
    # table, each reading its own subset (`_DISPATCHES`); no file mirrors the roster.
    "p_record_drift": 0,
    # Served properties no surface reads by name; a payload field only the terminal sends is one.
    # webb-5b (+1): `NodeOutputSchema.json_schema`, the backend's own document, served beside the
    # `contract` rows the browser now draws.
    # model-lines (+2): `call_target` on a served campaign config, which no surface edits yet.
    # l4-measure (+3): `CycleEntryFacts.curve`, the held-out curve beside `BenchScore`, unrendered.
    # limits-ux (+1): `ArmedCaps.said`, the terminal's rendering of the rows the browser draws.
    # flight-ux (+1): a launch payload's `lookahead`, which the terminal sends and no surface yet.
    # strata-flight (-1): the count once limits-ux and flight-ux stand in one tree.
    # flight-share B (+3): `CapReading.count`'s parts, which the browser reads in `cap_lines`' words.
    # small-5 (+3): `RepairAttempt`'s fields on a round's audit block, which no page draws yet.
    # cfg-3 (+2): a campaign layer's nested partials (split, lives, judge, call target), served
    # where a door states a layer; three of their keys no surface reads by name yet.
    # vote (+3): `origin_panel_cells` on the three served campaign configs, which no surface edits yet.
    # w7-host (-1): the webapp's bulletins read every field they serve, and one more the account already did.
    # w7-line, w7-web (−2): an unread served field each deleted.
    # w7-rows2 (−1): `WarmingDashboard.warming_up`, a discriminator no door read.
    # w7-web2 (−6): `ServedBackpressure`'s epochs and sender, `Evidence.generated_at`,
    # `HeadToHead.verdict_absent`, `MeResponse.issuer`.
    # w7-spend (−3): `MeteredSpend`'s rate and floor flags no reader read.
    # withdraw-bulletin (+1): `WithdrawBulletinPayload.bulletin_id`, a host-only kind no browser sends.
    # w8-launch (−1): measured at its landing; one served property gained a reader.
    # w8-web (+1): `RunStanding.candidates_tested`, the lift denominator `_climb` reads server-side;
    # its browser figure moved to `ServedDashboard.line_count`.
    # w8-resolve3 (+1): the fork preview is asked of a cycle (`_CycleAddressed`), whose `descend`
    # no browser preview sends.
    # w8-web2 (+1): `Variation.donated`, read server-side now (`hierarchy_subtree.py` serves the
    # loci a donor gave as `donated_loci`).
    # w10-records (+1): θ left the browser with the round's statement; `AbilityReading` stays
    # served to the CLI and MCP readers (evidence, export).
    # w11-config (+2): a check-in's mode pick sends `mode` and the server maps it to the chain, so
    # the browser no longer reads the `exclude_nodes` the config models serve.
    "p_served": 203,
    # Fields of a served or recorded model written and never read (`_unread_fields`); it only falls, to 0.
    # hubs-2 (+1): `CycleResult.result_prompt_fields`, read off the banked `index.json::final` by
    # key (`offline_run.py`), no longer by the copy into the deleted `CycleFinal`.
    # w8-spine4 (−14): `RoundAudit.interrupted`, `SampleFoldRow.sp`, `ArchiveReport.conflicts` go;
    # seven fields read by a door no name match sees are marked with it (`_READ_UNSEEN`).
    # w9-fields (−13): the pack's citation, `NodeInput.variables` and `PromptExport.tool` go; the PROV
    # export's three fields name their readers (`_READ_UNSEEN`).
    "p_unread_fields": 0,
    # Files naming three or more `CommandKind` rows, each a site the next row must reach.
    # w7-rows (+1): `commands/operator_acts.py`, the one handler module of the five host-admin kinds.
    # withdraw-bulletin (+1): `commands/bulletin_acts.py`, the one handler module of the three bulletin
    # kinds (post, withdraw, dismiss).
    # w8-launch (+2): `embedded_run.py` files the three launch kinds it holds; `cli/main.py` spells
    # `start-checkin`'s draft as `new --draft`.
    "p_command": 14,
    # The rows each file naming most of them leaves out.
    "p_command_drift": 0,
    # What every optimizer member is handed: `NodeContext`'s public members, inherited ones included,
    # and the public members of each object one of its properties exposes.
    "p_member": 12,
    # What a member may declare beyond it: each `CycleRead` kind counted as `p_member` is.
    # narr-3 (+3): `Parent.fitness`, the parent's fitness as the `FitnessFact` L2's box banks.
    # small-3 (+2): `RoundPanel`, the round's draw as a declared read, for `propose`'s parameter.
    # l4-outer (+1): `Target.transcript`, a row's one transcript source, its inner run on L4.
    # round (+1): `LevelSeries.hold`, the lift a round that moved the line held on every row it
    # met, one read for the readout and the dashboard.
    # w8-spine2 (+1): `CellSheet.filled_from`, the walk fill, beside `merged`, the standing fold.
    # w8-round (+1): `RoundResult.closed_rows`, the race plus the line's post-election buys, which
    # the δ fold and the parent's own rows read where `raced` is the election's alone.
    "p_reads": 295,
    # `tests/test_*.py` — `tests/CLAUDE.md` names what each one owns.
    # webd-2 (+1): `test_stream_stamp.py`, its brief's own file, since store-2f lands in the others.
    # rates-1 (+1): `test_world_edges.py`, what a run meets at its edges: the rates it prices by
    # and a fault in the offline world's own policy.
    "test_files": 9,
    # Test functions over 150 lines across them: each holds one fact (`tests/CLAUDE.md`).
    "test_functions_over_150": 0,
    # Top-level class names the package defines twice, private ones included: one name, two things.
    "class_names_twice": 0,
}

_PACKAGE_ROOT = REPO / "promptpotter"

# ``assets/`` is install DATA the package reads, never code it imports — the optimizer
# manifest, the exported dashboard, the benchmark dataset definitions.
_ASSETS_ROOT = _PACKAGE_ROOT / "assets"

# The suite's shape (`tests/CLAUDE.md`): prose alone let four files land in one arc against it.
_TESTS_ROOT = REPO / "tests"


def _count_tests() -> tuple[int, int]:
    files = sorted(_TESTS_ROOT.glob("test_*.py"))
    over_150 = sum(
        node.end_lineno is not None and node.end_lineno - node.lineno >= 150
        for f in files
        for node in ast.parse(f.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    )
    return len(files), over_150


# What the browser is OFFERED — every property of every schema in the generated contract, so the
# roster is the app's own answer and not a hand-kept list.
_CONTRACT = REPO / "docs" / "specs" / "openapi.generated.json"


def _served_properties() -> list[str]:
    schemas = json.loads(_CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    return [prop for s in schemas.values() for prop in s.get("properties", {})]


# Where a change lands by hand: the package, the scripts and the webapp's production source. A test, a prose doc and a generated file are no such site.
_WEBAPP_SOURCE = ("app", "components", "lib")
# The terminal's readers: the CLI, and the one view builder that renders only its lines.
_CLI_ROOT = _PACKAGE_ROOT / "presentation" / "cli"
_TERMINAL_VIEW = _PACKAGE_ROOT / "application" / "views" / "evidence.py"


def _sites() -> dict[Path, str]:
    webapp = REPO / "webapp"
    browser = [
        p
        for root in _WEBAPP_SOURCE
        for p in (webapp / root).rglob("*")
        if p.suffix in (".ts", ".tsx")
        and ".generated." not in p.name
        and not any("test" in part for part in p.relative_to(webapp).parts)
    ]
    files = [
        *_package_files("*.py"),
        *(REPO / "scripts").glob("*.py"),
        *browser,
    ]
    return {p: p.read_text(encoding="utf-8") for p in files}


_Spelling = tuple[re.Pattern[str], re.Pattern[str]]


def _spelled(literal: str, *identifiers: str) -> _Spelling:
    """A roster entry by NAME, then as a quoted string."""
    named = re.compile("|".join(rf"\b{re.escape(name)}\b" for name in identifiers))
    return named, re.compile(rf"[\"']{re.escape(literal)}[\"']")


# Three entries named make a file a site the roster's next entry must reach.
_ENUMERATES = 3


# A projection's `_handle_*` table and a fold's declared kinds (`records: ClassVar[RecordClasses]`)
# each read their own subset, which `read_model.py::LedgerIndex.check_every_arm_read` holds whole:
# a file dispatching by kind enumerates the roster, never mirrors it.
_DISPATCHES = re.compile(r"def _handle_\w+\(|^\s+records: ClassVar\[RecordClasses\]", re.MULTILINE)


def _enumerations(sites: dict[Path, str], roster: list[_Spelling]) -> tuple[int, int]:
    """The files enumerating *roster*, and the entries each mirror leaves out. A file naming more
    than half the roster mirrors it; one naming fewer, or dispatching by kind (`_DISPATCHES`), reads
    its subset, so leaves out nothing. A quoted value is often a common word, so it counts only
    inside a file that already enumerates by name."""
    enumerating = left_out = 0
    for text in sites.values():
        named = [bool(name.search(text)) for name, _ in roster]
        if sum(named) < _ENUMERATES:
            continue
        enumerating += 1
        if 2 * sum(named) <= len(roster) or _DISPATCHES.search(text):
            continue
        left_out += sum(
            not (hit or quoted.search(text)) for hit, (_, quoted) in zip(named, roster, strict=True)
        )
    return enumerating, left_out


def _members_bound(cls: ast.ClassDef) -> set[str]:
    members: set[str] = set()
    for stmt in cls.body:
        if isinstance(stmt, _Function | ast.ClassDef):
            members.add(stmt.name)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            members.add(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            members.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
    members.update(
        node.attr
        for node in ast.walk(cls)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )
    return {member for member in members if not member.startswith("_")}


def _class_node(cls: type) -> ast.ClassDef:
    body = ast.parse(Path(inspect.getfile(cls)).read_text(encoding="utf-8")).body
    for name in cls.__qualname__.split("."):
        node = next(n for n in body if isinstance(n, ast.ClassDef) and n.name == name)
        body = node.body
    return node


def _public_surface(cls: type) -> set[str]:
    """The public names *cls* answers to: its own and every package class's it inherits, less
    pydantic's own API, which a package override (`StrictModel.model_copy`) only re-types."""
    from pydantic import BaseModel

    return {
        member
        for klass in cls.__mro__
        if klass.__module__.startswith("promptpotter")
        for member in _members_bound(_class_node(klass))
        if member not in vars(BaseModel)
    }


def _imported_names(module: str) -> dict[str, object]:
    """What a module's names resolve to, the ones it imports for typing alone included."""
    tree = ast.parse(Path(inspect.getfile(importlib.import_module(module))).read_text("utf-8"))
    return {
        alias.asname or alias.name: getattr(importlib.import_module(node.module), alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and not node.level
        for alias in node.names
        if node.module.startswith("promptpotter")
    }


def _count_handed(cls: type) -> int:
    """*cls*'s public surface, and the public surface of each package class a public property or
    annotated attribute exposes — a member reaches through one as it reaches the context itself."""
    node = _class_node(cls)
    names = _imported_names(cls.__module__)
    exposed: list[tuple[str, ast.expr | None]] = []
    for stmt in node.body:
        if isinstance(stmt, _Function) and any(
            isinstance(d, ast.Name) and d.id == "property" for d in stmt.decorator_list
        ):
            exposed.append((stmt.name, stmt.returns))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            exposed.append((stmt.target.id, stmt.annotation))
    handed = set(_public_surface(cls))
    for attr, annotation in exposed:
        if attr.startswith("_") or annotation is None:
            continue
        for ref in ast.walk(annotation):
            sub = names.get(ref.id) if isinstance(ref, ast.Name) else None
            if isinstance(sub, type) and sub.__module__.startswith("promptpotter"):
                handed |= {f"{attr}.{member}" for member in _public_surface(sub)}
    return len(handed)


# A `type` / `interface` declaration's keys declare a shape and read nothing. Its body opens on
# `=`, `<`, `{` or `extends`: an import's `type X,` specifier is no declaration, and read as one
# it swallows the code after it up to the next `;` at depth zero.
_TYPE_DECLARATION = re.compile(
    r"^[ \t]*(?:export\s+)?(?:declare\s+)?(interface|type)\s+\w+(?=\s*(?:[=<{]|extends\b))",
    re.MULTILINE,
)
# A property read, never one assigned; then destructured: a binding's pattern, or a parameter's.
_READ = re.compile(r"\.(\w+)\b(?!\s*=(?![=>]))|\[[\"'](\w+)[\"']\](?!\s*=(?![=>]))")
_DESTRUCTURED = re.compile(
    r"\b(?:const|let|var)\s*\{([^{}]*)\}\s*(?::[^=;]*)?=(?![=>])"
    r"|\(\s*\{([^{}]*)\}\s*(?::[^(){};]*)?\)\s*(?:=>|\{|:)"
)


def _without_types(text: str) -> str:
    kept: list[str] = []
    end = 0
    for match in _TYPE_DECLARATION.finditer(text):
        if match.start() < end:
            continue
        kept.append(text[end : match.start()])
        depth, end = 0, match.end()
        while end < len(text):
            char = text[end]
            end += 1
            if char in "{([":
                depth += 1
            elif char in "})]":
                depth -= 1
                if not depth and match[1] == "interface":
                    break
            elif char == ";" and not depth:
                break
    kept.append(text[end:])
    return "".join(kept)


def _read_names(sites: dict[Path, str]) -> set[str]:
    """By NAME, not by schema: a name read on any type counts as read on every schema declaring it,
    and a whole-object dump (`JSON.stringify`, a copy button) reads nothing here."""
    read: set[str] = set()
    for path, text in sites.items():
        if path.suffix in (".ts", ".tsx"):
            code = _without_types(text)
            for groups in _DESTRUCTURED.findall(code):
                for entry in "".join(groups).split(","):
                    if key := re.match(r"\s*(\w+)", entry):
                        read.add(key[1])
        elif _CLI_ROOT in path.parents or path == _TERMINAL_VIEW:
            code = text
        else:
            continue
        read.update(name for match in _READ.findall(code) for name in match if name)
    return read


# A stamp says when a line or a file landed: read by a person off the file, never by code.
_STAMPS = frozenset({"timestamp", "at", "created_at", "updated_at"})
# A class pattern's keyword is a read: `case X(field=…)`.
_PATTERN_KEYWORD = re.compile(r"^\s*case\s.*", re.MULTILINE)


def _declared_fields(recorded: list[object]) -> set[tuple[str, str]]:
    """Every field of a served model (a contract schema) or a recorded one (a ledger arm and
    every model nested in it), as (model, field)."""
    import pydantic

    declared: set[tuple[str, str]] = set()
    schemas = json.loads(_CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    declared.update((name, prop) for name, s in schemas.items() for prop in s.get("properties", {}))
    seen: set[type] = set()
    stack: list[object] = list(recorded)
    while stack:
        held = stack.pop()
        if isinstance(held, type) and issubclass(held, pydantic.BaseModel):
            if held in seen:
                continue
            seen.add(held)
            for name, info in held.model_fields.items():
                declared.add((held.__name__, name))
                stack.append(info.annotation)
        else:
            stack.extend(typing.get_args(getattr(held, "__value__", held)))
    return declared


def _recorded_models() -> list[object]:
    """What lands on disk: every data-root row's model, a ledger's arms among them."""
    from pydantic import TypeAdapter

    from promptpotter.domain.kinds import KindTable
    from promptpotter.infrastructure.store.data_root import KINDS, FoldedLines, Parsed

    models: list[object] = []
    for kind in KINDS:
        match kind.shape:
            case TypeAdapter():
                models.append(kind.shape._type)
            case Parsed(model=KindTable() as table) | FoldedLines(model=KindTable() as table):
                models.extend(table.registered().values())
            case Parsed(model=model) | FoldedLines(model=model):
                models.append(model)
    return models


# Read by a door no name match sees, each named: a mark is a reader, never an exemption.
_READ_UNSEEN: dict[tuple[str, str], str] = {
    ("VerifySaturated", "already_measured"): "its sentence, `command_effects.py::VERIFY_WORDS`",
    ("ErrorRecord", "traceback"): "the operator, reading the ledger line",
    ("MemberAddress", "pass_role"): "`scoring/paired.py::read_pair`'s address equality",
    ("PromptExport", "artifact_version"): "`export.py::parse_prompt_export`, by `raw.get`",
    (
        "LineageEntity",
        "was_derived_from",
    ): "a reader of `export.json`, as PROV-O's `wasDerivedFrom`",
    (
        "LineageEntity",
        "was_generated_by",
    ): "a reader of `export.json`, as PROV-O's `wasGeneratedBy`",
    ("PipelineViewEdge", "from_"): "the browser, as its alias `from` (`PipelineFlow.tsx`)",
    **{
        ("OptimizerPromptTemplate", f): "`optimizer_template.py::prompt_fields`, by its roster"
        for f in ("persona", "task_intent", "thinking_style")
    },
}


def _unread_fields(sites: dict[Path, str], recorded: list[object]) -> list[tuple[str, str]]:
    """A declared field no production code reads by name (`.field`, `["field"]`, a destructured
    binding, a class pattern's keyword), no stamp and no `_READ_UNSEEN` door: written and never
    read."""
    read: set[str] = set()
    for path, text in sites.items():
        code = _without_types(text) if path.suffix in (".ts", ".tsx") else text
        read.update(name for match in _READ.findall(code) for name in match if name)
        for groups in _DESTRUCTURED.findall(code):
            read.update(m[1] for e in "".join(groups).split(",") if (m := re.match(r"\s*(\w+)", e)))
        for line in _PATTERN_KEYWORD.findall(text):
            read.update(re.findall(r"(\w+)=", line))
    return sorted(
        (model, name)
        for model, name in _declared_fields(recorded)
        if name not in read and name not in _STAMPS and (model, name) not in _READ_UNSEEN
    )


def _package_files(pattern: str) -> list[Path]:
    stowaways = sorted(_ASSETS_ROOT.rglob("*.py")) if _ASSETS_ROOT.is_dir() else []
    assert not stowaways, (
        f"Python files under {_ASSETS_ROOT.name}/ are excluded from every ledger count, so "
        f"these are uncounted surface: {[str(p.relative_to(_PACKAGE_ROOT)) for p in stowaways]}. "
        "assets/ is install DATA — move code into the package proper, or out of the dataset "
        "that staged it."
    )
    return [p for p in _PACKAGE_ROOT.rglob(pattern) if _ASSETS_ROOT not in p.parents]


_Function = ast.FunctionDef | ast.AsyncFunctionDef


def _nodes(tree: ast.AST) -> Iterator[tuple[ast.AST, int]]:
    """Every node of *tree* with the number of functions enclosing it, in ``ast.walk``'s order."""
    todo: collections.deque[tuple[ast.AST, int]] = collections.deque([(tree, 0)])
    while todo:
        node, depth = todo.popleft()
        yield node, depth
        inside = depth + isinstance(node, _Function)
        todo.extend((child, inside) for child in ast.iter_child_nodes(node))


def _is_bare_any(annotation: ast.expr) -> bool:
    """``Any`` or ``Any | None``. Not a mypy flag: ``disallow_any_explicit`` rejects an honest
    ``dict[str, Any]`` just as hard, and ``warn_return_any`` cannot see a param at all — an
    ``Any`` IS a complete annotation."""
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        right = annotation.right
        if not (isinstance(right, ast.Constant) and right.value is None):
            return False
        annotation = annotation.left
    return isinstance(annotation, ast.Name) and annotation.id == "Any"


_ANY_MAPS = ("dict[str, Any]", "Mapping[str, Any]")


def _declared_extra(model: ast.ClassDef) -> str | None:
    extra: str | None = None
    for stmt in model.body:
        if not isinstance(stmt, ast.Assign) or not any(
            isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
        ):
            continue
        value = stmt.value
        keys: dict[object, ast.expr] = {}
        if isinstance(value, ast.Call):
            keys = {kw.arg: kw.value for kw in value.keywords if kw.arg}
        elif isinstance(value, ast.Dict):
            keys = {
                k.value: v
                for k, v in zip(value.keys, value.values, strict=True)
                if isinstance(k, ast.Constant)
            }
        node_extra = keys.get("extra")
        if isinstance(node_extra, ast.Constant):
            extra = str(node_extra.value)
    return extra


def _count_lax_models(bases: dict[str, list[str]], declared: dict[str, str | None]) -> int:
    """Models that do NOT end up ``extra="forbid"`` — Pydantic's default returns a valid-looking
    instance from a misspelled keyword. AST, not import: eager package init makes import order a
    hazard."""

    def is_model(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name == "BaseModel":
            return True
        if name in seen or name not in bases:
            return False
        return any(is_model(b, seen | {name}) for b in bases[name])

    def effective_extra(name: str, seen: frozenset[str] = frozenset()) -> str | None:
        if name in seen or name not in bases:
            return None
        if declared.get(name):
            return declared[name]
        for base in bases[name]:
            found = effective_extra(base, seen | {name})
            if found:
                return found
        return None

    return sum(
        1
        for name in bases
        if name != "BaseModel" and is_model(name) and effective_extra(name) != "forbid"
    )


# A function-local import of our OWN package, `importlib.import_module` included unless its
# argument is a literal naming another package. Sanctioned for one reason, which the import must
# declare: it gates an optional extra, so hoisting it would make the core un-importable without
# that extra (docs/decisions.md § 0006). Mark those `# extras: <name>` on the import line or the one above; the
# rest are debt and count here. Why startup and cycles do not excuse one:
# `docs/developer/conventions.md` § Code shape.
_EXTRAS_MARKER = "# extras:"


def _imports_own_package(node: ast.AST) -> typing.TypeGuard[ast.stmt | ast.expr]:
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").startswith("promptpotter")
    if isinstance(node, ast.Import):
        return any(a.name.startswith("promptpotter") for a in node.names)
    if not (isinstance(node, ast.Call) and node.args):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name != "import_module":
        return False
    target = node.args[0]
    return not (isinstance(target, ast.Constant) and isinstance(target.value, str)) or (
        target.value.startswith("promptpotter")
    )


def _is_copy_update(call: ast.Call) -> bool:
    func = call.func
    named = isinstance(func, ast.Attribute) and func.attr == "model_copy"
    return named and any(kw.arg == "update" for kw in call.keywords)


def _source_counts(py_files: list[Path]) -> dict[str, int]:
    """The seven counts read off source, in ONE parse and one walk per module.

    A deferred import counts once per function enclosing it, so a closure's import is charged to
    the closure and to the function that holds it. ``domain_any_maps`` is scoped to ``domain/``
    because package-wide the shape is mostly the backend's own runtime-keyed overlay, and a count
    that flags what nobody may fix gets muted."""
    domain_root = _PACKAGE_ROOT / "domain"
    package_lines = any_params = any_maps = deferred = copies = 0
    bases: dict[str, list[str]] = {}
    declared: dict[str, str | None] = {}
    classes: collections.Counter[str] = collections.Counter()
    for path in py_files:
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        package_lines += len(lines)
        in_domain = domain_root in path.parents
        tree = ast.parse(source)
        classes.update(s.name for s in tree.body if isinstance(s, ast.ClassDef))
        for node, depth in _nodes(tree):
            if isinstance(node, _Function):
                args = node.args
                annotations = [a.annotation for a in args.posonlyargs + args.args + args.kwonlyargs]
                any_params += sum(ann is not None and _is_bare_any(ann) for ann in annotations)
                if in_domain:
                    any_maps += sum(
                        ann is not None and any(s in ast.unparse(ann) for s in _ANY_MAPS)
                        for ann in (*annotations, node.returns)
                    )
            elif isinstance(node, ast.Call):
                copies += _is_copy_update(node)
            elif isinstance(node, ast.ClassDef):
                if names := [b.id for b in node.bases if isinstance(b, ast.Name)]:
                    bases[node.name] = names
                    declared[node.name] = _declared_extra(node)
            if depth and _imports_own_package(node):
                context = lines[max(0, node.lineno - 2) : node.end_lineno or node.lineno]
                deferred += depth * (not any(_EXTRAS_MARKER in line for line in context))
    return {
        "package_lines": package_lines,
        "model_copy_updates": copies,
        "any_params": any_params,
        "domain_any_maps": any_maps,
        "models_lax": _count_lax_models(bases, declared),
        "deferred_imports": deferred,
        "class_names_twice": sum(n > 1 for n in classes.values()),
    }


def _type_cycles(py_files: list[Path]) -> int:
    """``TYPE_CHECKING`` imports lying on a file-level import cycle: each a knot the type-only
    block hides rather than cuts, so the layering it crosses is fictional at runtime."""
    statements = {p.relative_to(REPO).as_posix(): import_statements(p) for p in py_files}
    edges = {rel: {t for files, _ in found for t in files} for rel, found in statements.items()}

    def reaches(start: str, goal: str) -> bool:
        seen, todo = {start}, [start]
        while todo:
            for nxt in edges.get(todo.pop(), ()):
                if nxt == goal:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return False

    return sum(
        any(reaches(t, rel) for t in files if t != rel)
        for rel, found in statements.items()
        for files, typed in found
        if typed
    )


def _is_reexport_shim(init_file: Path) -> bool:
    """A module-level ``from … import`` binding a name that is no module and that the body never
    reads is there to be imported from HERE. A submodule imported for its registration is not."""
    tree = ast.parse(init_file.read_text(encoding="utf-8"))
    read = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for stmt in tree.body:
        if not isinstance(stmt, ast.ImportFrom) or stmt.module == "__future__":
            continue
        # `from . import x` is relative to the package this `__init__.py` is.
        base = init_file.parents[stmt.level - 1] if stmt.level else REPO
        home = base.joinpath(*(stmt.module or "").split("."))
        for alias in stmt.names:
            is_module = (home / f"{alias.name}.py").is_file() or (home / alias.name).is_dir()
            if not is_module and (alias.asname or alias.name) not in read:
                return True
    return False


def compute_ledger() -> dict[str, int]:
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.config_map import KNOBS, member_knob_count
    from promptpotter.application.maintenance.landing import complete_registries
    from promptpotter.application.optimizer_contract import NODE_MEMBERS, CycleRead
    from promptpotter.config.settings import Settings
    from promptpotter.domain.command_kinds import PAYLOAD_MODEL_FOR_KIND, CommandKind
    from promptpotter.domain.record_kinds import RECORDS

    complete_registries()
    py_files = _package_files("*.py")
    source = _source_counts(py_files)
    sites = _sites()
    arms = RECORDS.registered()
    # A module declaring arms is the roster itself, never a site the next arm must reach.
    declaring = {Path(sys.modules[arm.__module__].__file__ or "") for arm in arms.values()}
    records, records_left_out = _enumerations(
        {path: text for path, text in sites.items() if path not in declaring},
        [_spelled(kind, arm.__name__) for kind, arm in arms.items()],
    )
    commands, commands_left_out = _enumerations(
        sites,
        [
            _spelled(k.value, f"CommandKind.{k.name}", PAYLOAD_MODEL_FOR_KIND[k].__name__)
            for k in CommandKind
        ],
    )
    priced: collections.Counter[str] = collections.Counter()
    for member in NODE_MEMBERS.table.values():
        priced.update(member.priced_surface)
    read = _read_names(sites)
    test_files, test_functions_over_150 = _count_tests()

    return {
        "package_lines": source["package_lines"],
        "model_copy_updates": source["model_copy_updates"],
        "reexport_shims": sum(_is_reexport_shim(p) for p in py_files if p.name == "__init__.py"),
        # The overlay leaf stands for the node knobs each member declares, counted instead.
        "config_leaf_fields": len(KNOBS) - 1 + member_knob_count(),
        "settings_env": len(Settings.model_fields),
        "any_params": source["any_params"],
        "domain_any_maps": source["domain_any_maps"],
        "models_lax": source["models_lax"],
        **priced,
        "deferred_imports": source["deferred_imports"],
        "type_cycles": _type_cycles(py_files),
        "p_record": records,
        "p_record_drift": records_left_out,
        "p_served": sum(prop not in read for prop in _served_properties()),
        "p_unread_fields": len(_unread_fields(sites, _recorded_models())),
        "p_command": commands,
        "p_command_drift": commands_left_out,
        "p_member": _count_handed(NodeContext),
        "p_reads": sum(map(_count_handed, CycleRead.__subclasses__())),
        "test_files": test_files,
        "test_functions_over_150": test_functions_over_150,
        "class_names_twice": source["class_names_twice"],
    }


def _format(ledger: dict[str, int]) -> str:
    width = max(len(k) for k in ledger)
    rows = [f"  {k.ljust(width)}  {v:>6}  / {BASELINE.get(k, '-')}" for k, v in ledger.items()]
    return "complexity ledger (count / ceiling)\n" + "\n".join(rows)


# What the ledger reads, so a commit's own script counts that commit's tree.
_COUNTED = (
    "promptpotter",
    "scripts",
    "tests",
    "docs/specs",
    *(f"webapp/{root}" for root in _WEBAPP_SOURCE),
    "pyproject.toml",
)
# A `_format` row, which every commit's script prints.
_ROW = re.compile(r"^  (\w+)\s+(\d+)\s+/", re.MULTILINE)


def _git(*args: str) -> str:
    code, out = git(*args)
    if code:
        raise SystemExit(f"surface-ledger: git {' '.join(args)}: {out}")
    return out


def _counted_at(sha: str) -> dict[str, int]:
    """The ledger *sha*'s own script prints over *sha*'s tree, kept per sha in the clone's cache."""
    kept = clone_cache() / "surface-ledger" / f"{sha}.json"
    if kept.is_file():
        counts: dict[str, int] = json.loads(kept.read_text(encoding="utf-8"))
        return counts
    archive = subprocess.run(
        ["git", "archive", sha, "--", *_COUNTED], cwd=REPO, capture_output=True, check=True
    )
    with tempfile.TemporaryDirectory(prefix="pp-ledger-", ignore_cleanup_errors=True) as tree:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(tree, filter="data")
        code, out = run(
            [sys.executable, "scripts/complexity_ledger.py"], Path(tree), PYTHONPATH=tree
        )
    if code:
        raise SystemExit(f"surface-ledger: the ledger at {sha[:12]} did not count:\n{out}")
    counts = {name: int(n) for name, n in _ROW.findall(out)}
    kept.parent.mkdir(exist_ok=True)
    replace_changed(kept, json.dumps(counts))
    return counts


def _baseline_at(sha: str) -> dict[str, int]:
    tree = ast.parse(_git("show", f"{sha}:scripts/complexity_ledger.py"))
    (value,) = (
        stmt.value
        for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "BASELINE" for t in stmt.targets)
    )
    ceilings: dict[str, int] = ast.literal_eval(value)
    return ceilings


def check(ledger: dict[str, int], base: dict[str, int], ceilings: dict[str, int]) -> list[str]:
    """Each row that rose above *base*, the merge-base's counts, past a ceiling this branch did not
    raise from the merge-base's *ceilings* to hold it; a row new here rose from nothing."""
    if set(ledger) != set(BASELINE):
        moved = sorted(set(ledger) ^ set(BASELINE))
        return [f"complexity-ledger dimensions changed ({moved}); update BASELINE in this commit"]
    risen = {
        k: (v, base.get(k))
        for k, v in ledger.items()
        if (k not in base or v > base[k]) and (BASELINE[k] == ceilings.get(k) or v > BASELINE[k])
    }
    if not risen:
        return []
    return [
        "the cost of the next change grew (dimension: here vs the merge-base with the branch's line): "
        f"{risen}. Subtract instead, or raise its ceiling with a reason in the commit body."
    ]


if __name__ == "__main__":
    counted = compute_ledger()
    if "--check" not in sys.argv[1:]:
        print(_format(counted))
        raise SystemExit(0)
    line, merge_base = branch_base()
    risen = check(counted, _counted_at(merge_base), _baseline_at(merge_base))
    if risen:
        print(
            "\n".join(risen)
            + f"\nMeasured against {line}, merge-base {merge_base[:12]}; BASELINE lives in scripts/complexity_ledger.py."
        )
        # A stale ref reads red only, for growth its line itself made: so a red, never a green, asks origin.
        tip, held = (
            _git("rev-parse", line),
            _git("ls-remote", "origin", line.removeprefix("origin/")),
        )
        if held and held.split()[0] != tip:
            raise SystemExit(f"surface-ledger: {line} is stale, origin holds {held[:12]}: fetch.")
    raise SystemExit(bool(risen))
