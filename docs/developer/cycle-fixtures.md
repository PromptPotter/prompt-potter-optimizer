# Local repro harnesses — cycle fixtures + local OIDC

Two answers to one question: *this bug only reproduces on one machine — what do I stand up locally to see it?* **Cycle fixtures** cover bugs in cycle state; the **local OIDC harness** covers bugs behind an authenticated session. Both exist for the same reason, pre-flight gate Q6 (root `CLAUDE.md`) extended: **debug state belongs on disk in human-readable form**, the same way runtime state does. An environment that only exists on the maintainer's box violates Q6 for every collaborator without access to it, so both ship the environment as code.

## Cycle fixtures + Vitest

Frozen `dashboard.json` snapshots at `tests/fixtures/cycles/`, used by
the webapp's Vitest suite to exercise reader-side derivations
(`webapp/lib/derivations/`, plus future `components/**/__tests__/`) on
edge-case cycle states.

### Why fixtures live in `tests/fixtures/cycles/`

The fixture tree is the **canonical debug surface** for cycle state, in
the same way `dashboard.json` on disk is the canonical runtime surface.
A frozen fixture turns "this bug reproduces only on one machine's
`~/.promptpotter/`" into "this bug is a file under
`tests/fixtures/cycles/`." Any collaborator reproduces it cold.

Both Python (`pytest`) and TypeScript (`vitest`) tests reach into this
tree — that's why it lives at the repo's canonical `tests/` location
rather than under `webapp/`.

### Available fixtures

| Name | Loaded by | What it captures |
|---|---|---|
| `l2_terminal/` | vitest | Completed cycle whose round 4 ran `l1_generate` → `l1_critique` → `l2_context` and stopped — no `l1_score` ever fired. Triggers the "fitness bars vanish on completed cycle" loading-skeleton bug. |
| `frozen_campaign/` | pytest | A minted `campaign.json` pinned against the *current* `Campaign` / `CampaignConfig`. Both are `extra="forbid"`, so renaming a field makes every campaign already on disk unloadable — `resume`, `ab`, `verify` and L4's inner cycles die before any scoring. Fed through the real store reader by `tests/test_resume.py`; a freshly-built dict cannot catch this, because by construction it never carries a stale key. |

Note the two fixtures hold **different files**: a cycle's `dashboard.json` and a
campaign's `campaign.json`. The tree is keyed by bug class, not by file kind.

Add more as they're needed for specific bug classes (see § "Freezing a
new fixture" below) — one per bug class that actually lands, never
speculatively.

### How a Vitest test loads a fixture

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

### How a pytest test loads a fixture

There is no Python loader and none is needed — it's two lines against the
`tests/` tree, and the point of a pinned fixture is that nothing regenerates it:

```python
fixture = Path(__file__).parent / "fixtures" / "cycles" / "frozen_campaign" / "campaign.json"
manifest = json.loads(fixture.read_text(encoding="utf-8"))
```

Write it into a `built_stores` tmp workspace and read it back through the real
store method, so the test exercises the loader the engine actually uses.

### Running the tests

`python scripts/gate.py --web` runs the webapp suite along with everything else CI
runs. A fixture is only reached if its test file matches the discovery globs
`lib/**/__tests__/**/*.test.ts` or `components/**/__tests__/**/*.test.ts` — put it
somewhere else and it is silently never run (`webapp/vitest.config.ts`).

### Freezing a new fixture

Two options.

#### Option 1: hand-write the minimal shape

The cleanest option when the bug needs only a handful of fields. Look
up the `DashboardSnapshot` interface (`webapp/lib/poll.tsx::26`) and
write the smallest JSON that triggers the bug. Identifiers can be
deterministic placeholders (`fixture__<name>`, `cycle_<name>01`) — the
test asserts on derived shape, not on identity.

This is how `l2_terminal/` was built: hand-written JSON, fully
human-auditable, no anonymization concerns.

#### Option 2: strip an existing cycle dir

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

## Local OIDC harness

The reference recipe for running PromptPotter with `PROMPTPOTTER_AUTH=on` on a laptop — no Google credentials, no tunnel deploy. The harness lives at [`dev/oidc-local/`](../../dev/oidc-local/) and uses Dex as a local OIDC provider impersonating the Google slot.

```bash
cd dev/oidc-local
docker compose up -d
mkdir -p ../../.promptpotter/identity
cp oidc.json allowlist.json ../../.promptpotter/identity/
cd ../..
PROMPTPOTTER_AUTH=on python -m uvicorn promptpotter.main:app --port 8001
```

Visit `http://localhost:8001/login/`, click Google, log in as `dev@promptpotter.local` / `password`. Full walkthrough + troubleshooting + alternate IdP recipes: [`dev/oidc-local/README.md`](../../dev/oidc-local/README.md).

### When to reach for this

Any bug that only reproduces under an authenticated session:

- A React component that crashes only post-login (the post-login render loop that motivated the harness).
- A renderer that mishandles a missing `email` claim.
- A multi-tenant store path that scopes incorrectly when `IdentityContext.tenant_id` is real.
- A session-cookie edge case (expiry, SameSite, secure-flag mismatch).
- An OIDC claim shape variation (Dex emits a slightly different `email_verified` arm than Google in some configs).

If the bug only shows on `app.promptpotter.com`, mirror it here first — that is where the debugging is fast.

### How the discovery override works

`infrastructure/identity/provider_config.py::OIDCProviderConfig` accepts four optional fields (`issuer` / `authorize_url` / `token_url` / `jwks_url`) on the Google slot. Unset → production Google URLs. Set → any OIDC-conformant IdP (Dex here; Auth0, Keycloak, Okta in production-style adopter deployments).

`infrastructure/identity/google.py::GoogleProviderClient` reads the override-or-default at construction time. The verifier (`identity/verifier.py`) is already discovery-agnostic — it always took `expected_issuer` + `jwks_uri` as parameters, never module constants. The discovery override is OIDC-only; GitHub is OAuth-2.0 and ignores the four fields if set.

## Related docs

- **Testing posture** — owned by [`webapp/CLAUDE.md`](../../webapp/CLAUDE.md) §
  Testing posture; a fixture earns its place only for a reader-side derivation,
  since render components are covered by smoke instead.
- [`docs/specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md) §
  audit guidance — pattern: bug blocked on operator-local context.
- [`dev/oidc-local/README.md`](../../dev/oidc-local/README.md) — harness recipe + IdP swap table.
- [`docs/adr/0002-identity-foundation.md`](../adr/0002-identity-foundation.md) — permanent identity contract; the harness ships against Stage 1.
- [`deploy-linux/README.md`](../../deploy-linux/README.md) — the production Cloudflare Tunnel + systemd deploy that this harness reproduces locally.
