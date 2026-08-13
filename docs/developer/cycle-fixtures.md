# Cycle fixtures + Vitest

Frozen `dashboard.json` snapshots at `tests/fixtures/cycles/`, used by
the webapp's Vitest suite to exercise reader-side derivations
(`webapp/lib/derivations/`, plus future `components/**/__tests__/`) on
edge-case cycle states.

## Why fixtures live in `tests/fixtures/cycles/`

The fixture tree is the **canonical debug surface** for cycle state, in
the same way `dashboard.json` on disk is the canonical runtime surface.
A frozen fixture turns "this bug reproduces only on one machine's
`~/.promptpotter/`" into "this bug is a file under
`tests/fixtures/cycles/`." Any collaborator reproduces it cold.

Both Python (`pytest`) and TypeScript (`vitest`) tests reach into this
tree — that's why it lives at the repo's canonical `tests/` location
rather than under `webapp/`.

## Available fixtures

| Name | Loaded by | What it captures |
|---|---|---|
| `l2_terminal/` | vitest | Completed cycle whose round 4 ran `l1_generate` → `l1_critique` → `l2_context` and stopped — no `l1_score` ever fired. Triggers the "fitness bars vanish on completed cycle" loading-skeleton bug. |
| `frozen_campaign/` | pytest | A minted `campaign.json` pinned against the *current* `Campaign` / `CampaignConfig`. Both are `extra="forbid"`, so renaming a field makes every campaign already on disk unloadable — `resume`, `ab`, `verify` and L4's inner cycles die before any scoring. Fed through the real store reader by `tests/test_resume.py`; a freshly-built dict cannot catch this, because by construction it never carries a stale key. |

Note the two fixtures hold **different files**: a cycle's `dashboard.json` and a
campaign's `campaign.json`. The tree is keyed by bug class, not by file kind.

Add more as they're needed for specific bug classes (see § "Freezing a
new fixture" below) — one per bug class that actually lands, never
speculatively.

## How a Vitest test loads a fixture

```ts
import { describe, expect, it } from "vitest";
import { loadCycleFixture } from "@/lib/test-utils/fixtures";
import { roundCandidates } from "@/lib/derivations/round-candidates";

const dash = loadCycleFixture("l2_terminal");
const rows = roundCandidates(dash);

it("emits every historical round's candidates", () => {
  expect(rows.filter((r) => !r.is_origin)).toHaveLength(6);
});
```

`loadCycleFixture(name)` (`webapp/lib/test-utils/fixtures.ts`) hides
the relative-path arithmetic to `tests/fixtures/cycles/<name>/dashboard.json`
and returns it typed as `DashboardSnapshot`. It hardcodes that filename, so a
fixture holding some other file is loaded by its own test.

## How a pytest test loads a fixture

There is no Python loader and none is needed — it's two lines against the
`tests/` tree, and the point of a pinned fixture is that nothing regenerates it:

```python
fixture = Path(__file__).parent / "fixtures" / "cycles" / "frozen_campaign" / "campaign.json"
manifest = json.loads(fixture.read_text(encoding="utf-8"))
```

Write it into a `built_stores` tmp workspace and read it back through the real
store method, so the test exercises the loader the engine actually uses.

## Running the tests

`python scripts/gate.py --web` runs the webapp suite along with everything else CI
runs. A fixture is only reached if its test file matches the discovery globs
`lib/**/__tests__/**/*.test.ts` or `components/**/__tests__/**/*.test.ts` — put it
somewhere else and it is silently never run (`webapp/vitest.config.ts`).

## Freezing a new fixture

Two options.

### Option 1: hand-write the minimal shape

The cleanest option when the bug needs only a handful of fields. Look
up the `DashboardSnapshot` interface (`webapp/lib/poll.tsx::26`) and
write the smallest JSON that triggers the bug. Identifiers can be
deterministic placeholders (`fixture__<name>`, `cycle_<name>01`) — the
test asserts on derived shape, not on identity.

This is how `l2_terminal/` was built: hand-written JSON, fully
human-auditable, no anonymization concerns.

### Option 2: strip an existing cycle dir

When the bug needs a wider slice (e.g. realistic round histories with
many evaluators), copy from an operator's real cycle dir under
`.promptpotter/projects/<tenant>/campaigns/<campaign>/cycles/<cycle>/`.
Then:

1. Replace `campaign_id`, `cycle_id`, `session_id` with deterministic
   `fixture__<name>` / `cycle_<name>01` / `fixture-session-<name>`.
2. Drop fields the bug doesn't need (LLM call traces, per-sample
   scoring blocks, large `spend.history` arrays — anything that bloats
   the file without contributing to the repro).
3. Anonymize any field that contains real user data: `current_query_payload`,
   `current_sample_id` if mapped to operator-private samples,
   per-candidate `changes_description` strings (LLM output from a real
   campaign).
4. Drop in a one-paragraph `README.md` next to it explaining what state
   the fixture captures and which bug class it exercises (see existing
   fixtures for the shape).

Hand-strip; a scripted helper only earns its place once several fixtures
need the same treatment.

## Related docs

- **Testing posture** — owned by [`webapp/CLAUDE.md`](../../webapp/CLAUDE.md) §
  Testing posture; a fixture earns its place only for a reader-side derivation,
  since render components are covered by smoke instead.
- [`docs/specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md) §
  audit guidance — pattern: bug blocked on operator-local context.
