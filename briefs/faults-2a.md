# faults-2a: every refusal's act is typed, served and spelled at every door (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `faults-2a`, arc `arc/faults-2a`.

- **Base:** `origin/main` at `30af50704` (mkey landed: API tokens, `account.self`, a cancel's rung,
  the rung checked before the target in `dispatcher.py::CommandDispatcher.open`). Your `<base sha>` in
  common.md is this commit.
- **The code shrinks**, judged as common.md says: prose refusal fields, a second stop→refusal rule,
  loose reason strings and a second parse door go. Run `git diff --shortstat <base> HEAD -- promptpotter`
  after each link.
- **Spec:** `### batch/28 · faults`, these rows only (re-run each Re-test first; ren renamed names
  across the tree, so re-find each site):
  - "A run phase spells two refusal words";
  - "Three refusals sit outside the act rule" (with `jobs/quota.py::_contended`);
  - "An act's value is a string beside a typed field";
  - "The command log has no file kind";
  - "A stop becomes a refusal by two rules";
  - "Two parsers bound depth by hand";
  - "Validation reasons as loose strings";
  - "A positional argument has no declared spelling";
  - "Three served acts reach no door" (with `cli/commands/launch.py`).

  **Not yours:** Q3 and the other fault-channel rows (faults-2b, after you).

## Decisions (orchestrator; build them, do not reopen them)

- **The act stays in `shared/`** (`shared/errors.py::NextAct`): every `PotterError` carries it and
  `shared/` sits below `domain/`. Its `value` is the JSON scalar its field takes (`str | int | float |
  bool`), so `mode.from_round: 2` is an int a strict `WireInt` admits; `payload` sends it as typed.
  `command_kinds.py::takes_act` validates an act's payload against its kind's payload model (a stop's at
  import, a refusal's at the gate), and the gate fails when `NextAct.kind`'s Literal and the
  `CommandKind` rows that take acts differ: one roster, checked, never two that drift.
- **A run phase's refusal is a `Refusal` with its acts**: `RunPhaseInfo.no_action` / `parked` go.
- **The refusals outside the scan are `Refusal`s with acts**: the campaign store's `mint_cycle` /
  `cycle_minted_otherwise` 409, `LANDING_REMEDY`, harbor's pin-roster `ValueError`, and `_contended`
  (`RateLimitedError` carries acts as every `PotterError` does). A CLI-only verb is the no-kind act
  form faults-1 uses. `refusal-words` scans each.
- **One stop→refusal mapping in `domain/`**, read by `scoring_scope.py::_refused` and
  `passes.py::_grade`.
- **Every CLI spelling of a payload field is declared beside the payload**, positional or flag
  (`CommandPayload.cli_flags` grows to that, or is replaced by one spelling map); the CLI builds its
  arguments from it and `cli/main.py::_unsaid` matches whole spellings only.
- **An act a refusal or a hold names rides the row it is served on**, and every door reads it:
  `DatasetIndexEntry.refusal` (webapp and CLI), `useQueuedStart.ts`'s `said` with its acts,
  `ArmsGate.next_acts` a served field (not a `ClassVar`), `launch.py` printing `acts_line`.
- **The command log has its own `FileKind`**, whose remedy names the command log; `dispatcher.py`
  and `read_model.py`'s `_refusal` use it.
- **Validation reasons are `ValidationReason` rows**: `INVARIANT_REASONS` and
  `PARSE_FAILURE_ALL_NO_OP` go.
- **One bounded parse door** in `store/io.py` for YAML and `literal_eval` alike, answering a refusal at
  the bound (`RecursionError` included); `capo/members.py::_instructions` reads through it;
  `parse_yaml` parses each text once.

## Links (one commit each, in order)

1. The typed act and its one roster; the run phase's, the store's, the landing's, harbor's and
   `_contended`'s refusals; the one stop→refusal mapping.
2. The CLI spellings beside the payload; every door reads served acts.
3. The command log's file kind; the reason enum; the bounded parse door.

## Beside you

- Name every shared file in its link's commit body. **rk-rest** runs beside you (`checkin.py`,
  `mint.py`, `scoring/formula/`, `datasets/draft_patch.py`, `connectors/`, `infrastructure/backend.py`,
  `llm/openai_compat.py`, `runner/inner/`, `optimizers/__init__.py` and `nodes.py`). The two of you are
  disjoint unless your harbor pin-roster refusal meets its `connectors/harbor.py` hunk: touch only
  your own hunk. Both of you cut rows in `code-debt-cleanup.md`: delete only your own rows.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg; behaviour keys unchanged.
- Each row's re-test, before and after. Pressing a `mode.from_round` act through the API is admitted
  (a test); a 5000-deep list as a capo init reply is one refused child; a torn command log's refusal
  names the command log.
- `python scripts/gate.py --only refusal-words` green with the new scans.
