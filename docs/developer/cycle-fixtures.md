# Local repro harnesses — cycle fixtures + local OIDC

Two answers to one question: *this bug only reproduces on one machine — what do I stand up locally to
see it?* **Cycle fixtures** cover bugs in cycle state; the **local OIDC harness** covers bugs behind an
authenticated session. Both exist for the same reason — pre-flight gate Q6 (root `CLAUDE.md`) extended:
**debug state belongs on disk in human-readable form**, the same way runtime state does. An environment
that exists only on the maintainer's box violates Q6 for every collaborator without access to it, so
both ship the environment as code.

## Cycle fixtures

Frozen snapshots at `tests/fixtures/cycles/`, turning "this reproduces only on one machine's
`.promptpotter/`" into "this is a file in the repo". Both `pytest` and `vitest` reach into the tree,
which is why it sits at the repo-canonical `tests/` rather than under `webapp/`.

| Name | Loaded by | What it captures |
|---|---|---|
| `l2_terminal/` | vitest | Completed cycle whose round 4 ran `l1_generate` → `l1_critique` → `l2_context` and stopped — no `l1_score` ever fired. Triggers the "fitness bars vanish on completed cycle" loading-skeleton bug. |
| `frozen_campaign/` | pytest | A minted `campaign.json` pinned against the *current* `Campaign` / `CampaignConfig`. Both are `extra="forbid"`, so renaming a field makes every campaign already on disk unloadable — `resume`, `ab`, `verify` and L4's inner cycles die before any scoring. Fed through the real store reader by `tests/test_resume.py`; **a freshly-built dict cannot catch this**, because by construction it never carries a stale key. |

The two hold **different files** — a cycle's `dashboard.json` and a campaign's `campaign.json`. The
tree is keyed by bug class, not by file kind, and gains one entry per bug class that actually lands,
never speculatively.

**Loading.** Vitest: `loadCycleFixture(name)` (`webapp/lib/test-utils/fixtures.ts`) hides the
relative-path arithmetic and returns `DashboardSnapshot`; it hardcodes `dashboard.json`, so a fixture
holding another file is loaded by its own test. Pytest: there is no loader and none is needed — read
the path directly, then write it into a `built_stores` tmp workspace and read it back **through the
real store method**, so the test exercises the loader the engine actually uses.

**A fixture is only reached if its test matches the discovery globs** `lib/**/__tests__/**/*.test.ts`
or `components/**/__tests__/**/*.test.ts` — put it anywhere else and it is silently never run
(`webapp/vitest.config.ts`). `python scripts/gate.py --web` runs the suite.

**Freezing a new one.** Prefer hand-writing the minimal shape against the `DashboardSnapshot`
interface: deterministic placeholder ids (`fixture__<name>`, `cycle_<name>01`), since the test asserts
on derived shape rather than identity. That is how `l2_terminal/` was built — fully auditable, no
anonymization concerns. Only when the bug needs a wider slice, strip a real cycle dir: replace the
three ids, drop what the repro doesn't need (LLM call traces, per-sample scoring blocks, large
`spend.history`), and **anonymize every field carrying real user data** — `current_query_payload`,
operator-private sample ids, per-candidate `changes_description` strings. Leave a one-paragraph
`README.md` beside it naming the bug class. Hand-strip; a scripted helper earns its place only once
several fixtures need the same treatment.

## Local OIDC harness

The reference recipe for running with `PROMPTPOTTER_AUTH=on` on a laptop — no Google credentials, no
tunnel deploy. [`dev/oidc-local/`](../../dev/oidc-local/) runs Dex as a local OIDC provider
impersonating the Google slot.

```bash
cd dev/oidc-local
docker compose up -d
mkdir -p ../../.promptpotter/identity
cp oidc.json allowlist.json ../../.promptpotter/identity/
cd ../..
PROMPTPOTTER_AUTH=on python -m uvicorn promptpotter.main:app --port 8001
```

Visit `http://localhost:8001/login/`, click Google, log in as `dev@promptpotter.local` / `password`.
Walkthrough, troubleshooting and alternate-IdP recipes:
[`dev/oidc-local/README.md`](../../dev/oidc-local/README.md).

Reach for it for any bug that only reproduces under an authenticated session — a component that
crashes post-login, a missing `email` claim, a tenant-scoped store path with a real
`IdentityContext.tenant_id`, a session-cookie edge case, or an OIDC claim-shape variation (Dex emits a
different `email_verified` arm than Google in some configs). **If the bug only shows on
`app.promptpotter.com`, mirror it here first** — that is where the debugging is fast.

The swap works because `OIDCProviderConfig` takes four optional discovery fields on the Google slot
(unset → production Google; set → any OIDC-conformant IdP) and the verifier was always
discovery-agnostic, taking `expected_issuer` + `jwks_uri` as parameters. The override is OIDC-only:
GitHub is OAuth 2.0 and ignores the four fields.

## Related

- **Testing posture** — owned by [`webapp/CLAUDE.md`](../../webapp/CLAUDE.md) § Testing posture; a
  fixture earns its place only for a reader-side derivation, since render components get smoke instead.
- [`../adr/0002-identity-foundation.md`](../adr/0002-identity-foundation.md) — the permanent identity
  contract this harness ships against.
- [`../../deploy-linux/README.md`](../../deploy-linux/README.md) — the production deploy it reproduces.
