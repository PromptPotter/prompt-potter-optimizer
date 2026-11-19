# Review: feed-version (the base + feed-version on 3a1226a51, uncommitted)

**Verdict: LAND AFTER FIXES**

Read for the order (`briefs/feed-version.md`, `common.md`) and the code only. Its probe is `briefs/feed-version-fix.probe.py`.

## Must-fix

### 1. The `machine` version churns forever when a host and its delegate are both online
- **Where:** `promptpotter/application/cycle_reads.py:296-325` (`_COUNTED` keyed by `str(stores.identity.user_id)`; `held["machine"] = served_machine_status(...).model_dump_json()`).
- **Defect:** the count is keyed by user_id, but the stats it counts are the caller's own view of the machine. That view changes with `is_host` (`sees_all`) and with the acting principal (`queue`, `refused`; see `machine_status.py:252-302`). A delegate has the host's user_id and store but a different view, so each observation sees "different" stats and adds 1 to `n`.
- **Scenario:** the team deployment, with the host and a delegate both signed in, and either one holding a queued or refused launch today. Every 2 s feed tick on each tab sends a new `machine` frame while nothing changed.
  - `machineStatusRead` and `backendHealthRead` re-ask every 2 s on every tab. `backendHealthRead` is an external backend probe, so this amounts to polling on a clock.
  - Every command ack names `machine` at a fresh `n`, so every command's `pending` waits on the backend health probe.
  - Base sent a hash per connection, so it did not churn. This is a regression.
- **Probe:** `feed-version-fix.probe.py`. It sets up a host and a delegate on one store, one held slot and one launch ada queued, and writes nothing:
  ```
  same caller twice (no write):        host n=1, host n=1
  host and delegate alternating:       host n=1, delegate n=2, host n=3, delegate n=4, host n=5, delegate n=6
  ```
- **Root fix:** a version belongs to its subject, not to whoever reads it.
  - Count caller-independent stats, keyed by the subject's owner (the store's base dir, or the job registry), so all readers of one subject share one count.
  - For `machine`, the registry's own state is the job files, the landing and the account limit. The caller's served JSON is not.
  - The full root is the writer-stamped version per subject that the ledger already names ("The feed's version spans a whole store"). With it the counter is no longer a module global standing in for a keyed subject.

### 2. The check-in's draft writers still build whole lists from a stale served draft, and the change widens the window
- **Where:**
  - `webapp/components/pipeline/NodeKnobsPanel.tsx:431-434`: `PoolPicker.toggle` builds `active` from the served `pool.active`.
  - `NodeKnobsPanel.tsx:349`: `PoolInPlace` builds `packs` from the served `pool.packs`.
  - `webapp/lib/hooks/useIngestFlow.ts:398-411`: the patch is fixed at click time and queued.
  - `useIngestFlow.ts:405`: the write's answer no longer lands.
- **Defect:** `phase.draft` now moves only when the draft read re-lands after the ack. Until then, a second toggle is computed from the draft as it stood before the first write. The queue then sends it after the first write and silently overwrites it.
- **Scenario:** the operator clicks entry A, then entry B, before the draft read re-lands.
  - W1 = `["*", A]`. W2 = `["*", B]`, because it was built from `["*"]`.
  - W2 is sent once W1's ack settles, and A is lost with no message.
  - Base's window was one POST round trip, since the answer landed. The new window is POST plus the draft GET. The chips stay enabled throughout (`disabled` reads only `!onActive` and the lone chip).
  - The order owns these writers (brief lines 19-20 and "Owns"). The change was not taken to them.
- **Probe:** none. Showing it needs a render harness for `useIngestFlow` plus the panel. The scenario follows line by line from the code cited above.
- **Root fix:** a queued draft write computes its value at its turn in the queue, from the served draft.
  - The new ack resolves only once the draft read stands on the previous write, so the served draft already holds every earlier write.
  - `mutateDraft` takes `(served) => patch` and evaluates it after the previous `writing.run` resolves, reading the draft read's slot.
  - Or: list toggles become add/remove deltas that the server merges under the draft's lock.
  - Locking the chips while saving is the patch, not the root.

### 3. The change that closes the revert is untested, and the new test guards a case that cannot happen
- **Where:**
  - `useIngestFlow.ts:405` drops `commitDraftUpdate` from `writing.run`. This is what actually removes "a stale read reverts a draft edit". No test drives `mutateDraft`/`applyPatch`; only `useQueuedStart.test.ts` mounts `useIngestFlow`.
  - `webapp/lib/read-cache.ts:108-114,239` (`behind`) and `webapp/lib/__tests__/feed-order.test.ts:63-76`.
- **Defect, part 1: `behind` cannot fire with one serving process** (`--workers 1` in `deploy-linux/install-service.sh:112`; Playwright `workers: 1`, one cold server).
  - A slot lands only its current flight. A superseded flight never lands (`mine()`).
  - Each next flight is asked after the previous answer arrived, and its `stood` is counted after that answer was sent. A monotone counter therefore never yields a lower `n` in the same epoch.
  - The test's "older answer" (a server going from at=5 back to at=3 in epoch `e1`) is a server that does not exist.
  - The case its comment names, "a slower worker's", carries another epoch. `ahead` treats another epoch as newer in both directions and `behind` never compares across epochs, so exactly that case lands.
- **Defect, part 2: the 15 s, 33-observation flip in `pools-checkin.spec.ts` is not reproduced or explained by any test here.** The order's flake re-test is unverified by this review.
- **Probe:** reasoned from `read-cache.ts:234-299` (one flight per slot, supersede, `mine()`). On base, the first `feed-order` test would land `{served: 3}`, since base has no `behind`. That was reasoned, not run, because there is no base worktree.
- **Root fix:**
  - Delete `behind` and its test. It is a second ordering mechanism beside supersede.
  - Add a logic test: draft edit acked, with a draft read in flight that was asked before the write. Assert that `phase.draft` never returns to the pre-write draft. This fails on base, which lands the answer and then the stale read.
  - Show `pools-checkin.spec.ts` under load as the order asks (`--repeat-each 10`, the gate's workers), before and after.

## Lesser

- **The ack names every subject, not what its write moved.** `commands.py:70,119` call `feed_versions_said(stores, job_registry)` with `named=""`.
  - Every command walks every store, every inner sandbox and the machine status after its write.
  - `reached` waits on every mounted read that is behind on any subject. Under a live run, where the store moves every tick, every button's pending lasts as long as a full re-read of hierarchy, evidence and cells. Wherever `machine` moved, it also waits on `backendHealthRead`.
  - The brief says "names the version its write produced". The writer-stamped root (ledger row at `code-debt-cleanup.md:200`) gives that directly.
- **The post-write walk can fail a write that was already applied.** If `feed_versions_said` raises after `dispatch`, the client gets a 500 for a filed command, and the next press files it again: the double-filing class this order exists to close.
  - Example: a campaign deleted between `list_campaign_ids` and its cycle-dir walk at `cycle_reads.py:289-292`.
  - Plausible race, not probed.
- **`_COUNTED` grows for the life of the process** (`cycle_reads.py:296`). It holds one entry per (account, subject) ever observed, each with the full stats tuple; `campaigns.version()` is per-campaign file sigs. A deleted campaign's inner sandbox subjects never leave.
- **Counted where it is read.** Stats are taken outside the lock (`cycle_reads.py:312-325`). An observer holding pre-write stats that reaches `_counted` after a post-write observer labels the older stats `n+1`. The feed then emits `n+1` and `n+2` for one write. This only causes spurious re-asks; bodies are read after the count.
- **Two write answers still land.** `useIngestFlow.ts:238-244` (`runCheckin`) and `:326` (`openOrigin`) put a write's answer into `phase.draft`, while `:189` says "The served draft's one landing". The read should be the only one to land a draft.
- **Double-press protection is only the pending window.** It spans only reads that are mounted and behind the produced version. A queued launch moves `machine`, not the cycle's run phase, so a Start button that reads the run phase is pressable again once the ack settles. `feed-order.test.ts:78-111` covers only the press made inside the window.
- **Naming.**
  - `SERVING_EPOCH` and `FeedVersion.epoch` hold a random process name, not an epoch. The word reads as time, which the brief rules out ("never a clock"), and `epoch` already names `useIngestFlow`'s reset counter.
  - The old `_PROCESS` said what it held.
- **LEAN.** The version stamp is spelled twice in `commands.py` (`ingest_upload` and `_Dispatching.__call__`), and `ingest_upload` gains `job_registry` only for it. One stamping site would do.
- **Ledger comment out of step.** The `scripts/complexity_ledger.py:200` comment says "+41", but the baseline moves +19 (112198 → 112217). +41 is the raw net of `promptpotter/` (+61/−20).

## Symptoms seen in passing

- `docs/specs/code-debt-cleanup.md` is unmerged in the worktree index (stages 1/2/3, `UU`), though the working copy is resolved with no markers. The landing must `git add` it.
- `code-debt-cleanup.md:209` cites `reads.py:214-232` for the stood walk. `_endpoint` now spans 214-243 and calls `feed_versions_said`.
- `webapp/lib/api/reads.ts:148`: `draftRead` follows bare `store:`. Any store's move, including an inner sandbox's append, re-asks the draft read, and every draft-edit ack now waits on all store reads.
- `machine_status.py:241-302`: the served machine status is caller-relative, yet it is the feed's version input. This predates the change and becomes load-bearing with it (must-fix 1).

## Checks run

- `feed-version-fix.probe.py`: output above.
- `scripts/gate.py --only ts-types`, `--only specs`, `--only surface-ledger`: green. The generated specs and types match their scripts.
- `npx vitest run --project logic` on `feed-order`, `read-reasks`, `useQueuedStart` and `useCommand`: 21/21 green.
- Not run: the full gate, the Playwright spec, the base comparisons. The base-fail claims are reasoned from code.
