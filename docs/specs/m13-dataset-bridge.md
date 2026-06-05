# M13 — Dataset Bridge: identity, name collisions, and the version-and-repoint contract

> **Status:** Phases 1–3 SHIPPED. Phase 1 (safe collision choices), Phase 2 (version-and-repoint Replace, crash-safe + resumable), Phase 3 (framing refactor — `derived_origin*` → dataset-framed names, freeze-at-commit documented).
> **Companion:** [`m13-chat-first-user-web.md`](m13-chat-first-user-web.md) (§ Ingest, § Commit path) — this spec deepens its *dataset-identity* model. Read that first.
> **Audience:** the operator is a data scientist. Dataset identity must behave like a versioned data artifact, not a filename.

---

## 0. Why this exists

Dropping a file into the chat ingests it as a **dataset** under `projects/{tenant}/datasets/{slug}/`. The `slug` is derived from the filename (`email-tagging-eval.csv` → `email-tagging-eval`). Drop a file whose slug already exists and today you hit a dead-end 409:

> `Slug 'email-tagging-eval' already exists in your collection. Suggested slug: email-tagging-eval-2.`

Two problems, one shallow and one structural:

1. **Shallow (UX):** the chat surfaces the raw 409 as an error and stops. The operator has no in-flow way to say what they meant.
2. **Structural (data integrity):** a slug collision is a *name* collision, but the slug is also the dataset's *identity* and the *pointer every campaign resolves through*. A campaign reads its data **live** from `datasets/{slug}/cache.json` at every run/resume (`resolve_dataset_config_dir` → `load_dataset(dataset_name)`), and measurements are stamped by `dataset_name`. So silently overwriting — or renaming — a slug would **falsify every result already computed against it**. Two genuinely different files can share a filename-derived slug; the system must never let one quietly take the other's place.

The operator's framing (verbatim): *"replace must mutate the previous dataset alias … such that the newly dropped can take the place, BUT it also must mutate the pointer."* That is exactly the right model, and §2 makes it precise.

---

## 1. The conceptual model (data-scientist-facing)

Three roles are today **fused into one string** (`slug`). Separate them in your head — the whole design follows from it:

| Role | What it is | Mutability | Analogy |
|---|---|---|---|
| **Name** | the human-friendly handle (`email-tagging-eval`) | **mutable** alias | a Git branch / a W&B alias (`latest`) |
| **Version** | one immutable snapshot of the example bank + origin config | **immutable** once written | a Git commit / a HF dataset revision |
| **Pin** | the specific version a campaign was run against | **fixed** at mint, per campaign | a commit SHA a tag once pointed at |

**The contract for a data scientist:**

- A **Dataset** is an immutable bank of labeled examples (`{query → ground_truth}`) plus its origin config (starting prompt, pipeline overlay, scorer). *Data, once a campaign has touched it, is never mutated in place.*
- A **Name** points at a version. Re-dropping new data under an existing name **adds a version and moves the name** — it never edits the old version.
- A **Campaign** is pinned to the version it ran on. Its measurements stay truthful forever, because the data it references can't change under it.

This is the W&B-artifact / HuggingFace-revision / Git-branch convention: **immutable data, movable name.** It is the norm precisely because data with downstream results must be reproducible.

### 1.1 Current reality vs. the model (the gap)

Today there is **no version layer**. The slug is simultaneously Name, Version, and Pin:

- `datasets/{slug}/` is the only home for the data (no `{slug}@v1`).
- `campaign.json::dataset_name = "{slug}"` — the pin is *the name*, resolved live.
- Measurement archive entries are stamped `dataset_name = "{slug}"`.

So "replace the data under this name" is impossible without **either**:

- **(A) Version-and-repoint** — move the old version to a versioned name, repoint its dependent campaigns + measurements to follow it, then write the new data under the freed name. *Near-term; this spec, Phase 2.*
- **(B) Snapshot-at-mint** — copy the example bank into the campaign at mint so campaigns are self-contained, making the name freely movable. *End-state; deeper change, noted in §4.3.*

Phase 2 implements **(A)** because it requires no change to how campaigns load data. **(B)** is the eventual simplification and is recorded so we don't paint over it.

---

## 2. Name-collision resolution (the bridge)

When a dropped file's slug matches an existing dataset, the chat must recognize it and offer the **three intents** a collision can carry. No content hashing (operator decision 2026-06-05 — name-only); the operator asserts intent via the labels.

| Intent | Choice | Effect | Safety |
|---|---|---|---|
| "Same data — run again" | **Use existing** | Start a new campaign on the dataset already on disk (no re-ingest). Routes through the derived-from-dataset draft path (`postDraftFromDataset` → wizard, pre-filled). | Zero risk — reuses, never writes. |
| "Different data — keep both" | **Save as new** | Re-ingest the dropped file under the suggested free name `{slug}-2`. | Zero risk — new name, old data untouched. **Default.** |
| "Updated data — supersede" | **Replace** | Version-and-repoint (§2.1). | Guarded; never overwrites. |

**Safe default = Save as new.** Replace is never the default and never silently destroys.

### 2.1 Replace = version-and-repoint (Phase 2, data-critical)

Replacing `email-tagging-eval` (which has prior campaigns/measurements) executes as an **atomic, crash-safe migration** — old data and its results are *preserved*, just renamed out of the way:

1. **Version the old data.** Rename `datasets/email-tagging-eval/` → `datasets/email-tagging-eval-v1/` (smallest free `-vN`). The bank, config, and origin hash are byte-identical — only the directory name changes.
2. **Move the pin with the data.** For every campaign whose `campaign.json::dataset_name == "email-tagging-eval"`, rewrite it to `email-tagging-eval-v1` — **and** every cycle `index.json::header.dataset_name` under it (the cycle listing surfaces that header and only backfills from the manifest when it's *empty*, so a stale stamp would otherwise outlive the move). Re-stamp that campaign's measurement-archive entries (index summaries + detail files; `dataset_name: email-tagging-eval → email-tagging-eval-v1`). After this step, every old campaign resolves to *the same bytes it always ran on* — now living under `-v1`. Any lifecycle (active / archived / deleted) is repointed; an archived campaign's results must stay truthful too.
3. **Land the new data.** Commit the freshly-dropped draft under the freed canonical name `email-tagging-eval`.

Net: the friendly name now resolves to the newest data; every prior campaign is intact, truthful, and pinned to `-v1`. Nothing was overwritten.

**Guards & crash-safety:**

- **Order matters.** Version (step 1) and repoint (step 2) commit *before* the new data lands (step 3). If the process dies mid-migration, the worst state is "old data under `-v1`, name free, campaigns repointed" — no campaign ever resolves to *wrong* data. A resumable marker (`datasets/.migrations/{id}.json`) records the in-flight rename so a crash between 1 and 2 is recoverable (campaigns still pointing at the old name are detected and repointed on next access).
- **`campaign_id` is opaque.** It embeds the dataset name as a cosmetic prefix (`email-tagging-eval__ab12cd`) but resolution uses the `dataset_name` *field*, which step 2 rewrites. The id is not re-minted (ids are immutable); the prefix becomes historical, which is fine and documented.
- **Measurement archive** is dataset-scoped (v2 schema stamps + filters by `dataset_name`). Step 2's re-stamp is a bounded rewrite over the entries owned by the repointed campaigns. A campaign that has *no* measurements yet (minted, never run) repoints trivially.
- **No dependents → fast path.** If the old dataset has zero campaigns, Replace degrades to "archive old → write new" with nothing to repoint (still non-destructive: old version retained under `-v1`).

### 2.2 Phase 1 (this commit) — safe choices only

Phase 1 ships **Use existing** + **Save as new** + Cancel. Replace is **scoped here but deferred to Phase 2** because it mutates campaign records and the measurement archive — it earns its own tested pass with the migration marker above. Phase 1 is pure win: it turns the dead-end 409 into the two zero-risk actions that cover the common cases (re-test the same data; keep a differently-named copy).

**Wire:** the 409 ingest response gains the colliding name alongside the suggestion, so the chat can offer "Use existing `{slug}`" without string-surgery on the message:

```
409  { error: "slug_collision",
       message: "A dataset named 'email-tagging-eval' already exists.",
       details: { slug: "email-tagging-eval", suggested_slug: "email-tagging-eval-2" } }
```

**Chat flow:** on a 409 with `details.slug`, the chat renders a collision card (not an error bubble) with two buttons:
- **Use existing 'email-tagging-eval'** → `postDraftFromDataset(slug)` → `onOpenDraft(draft)` (pre-filled wizard).
- **Save as new (email-tagging-eval-2)** → re-run ingest with `slug = suggested_slug` → normal check-in flow.

---

## 3. Framing drift — the rename/reposition plan

The collision work surfaced real vocabulary drift. The operator's instinct ("there must be drift of framing") is correct. The single worst offender:

### 3.1 "Origin" is a homonym — split it

`origin` currently means **four** different things:

1. the committed **dataset artifact** (the four files) — *the wrong use*;
2. the optimizer's **starting `OptSearchPoint`** (`resolve_origin_opt_search_point`) — *correct, keep*;
3. the **dataset itself** as a pickable thing in the UI ("Add an Origin", "start your first Origin") — *the wrong use*;
4. the **pre-mutation moment** ("origin accuracy", round 0) — *correct, keep*.

**Resolution:** reserve **"origin"** for the optimizer's starting point (meanings 2 & 4 — that *is* correct domain language; renaming it would be the real error). Call the data artifact a **"Dataset"** everywhere (meanings 1 & 3). A campaign is minted **from a dataset**; its **origin** is the starting OSP derived from that dataset. Two words, two concepts, no overlap.

**Concrete renames (Phase 3):**

| Surface | From | To | Notes |
|---|---|---|---|
| UI copy | "Origin" picker label; "Add an Origin"; "start your first Origin" | "Dataset"; "Add a dataset"; "start your first dataset" | `ListAndMintFlow`, `ChatIngestFlow`, `IngestPane` |
| Wire/field | `derived_origin` | `derived_from_dataset` | uses "origin" to mean "dataset" — the homonym in a field name |
| Helper | `derived_origin_slug()` | `dataset_source_of(source_file)` (returns the source dataset slug or `None`) | also splits parse-vs-check |
| Backend comments/docstrings | "derived-from-existing origin", "canonical origin" | "derived-from-existing dataset", "canonical dataset" | `launcher.py`, `ingest.py` |
| Spec | `m13-chat-first` "The committed artifact is an Origin" | "…is a **Dataset**" | keep one capital-D noun |

### 3.2 `slug` — make the name/identity transition explicit

`slug` is a mutable human name *before* commit and an immutable filesystem identity *after*. That transition is invisible. Options (Phase 3, pick one in review):

- **Minimal:** keep `slug`, but rename the user-facing concept to **"dataset name"** and document the freeze-at-commit in one place; introduce the version suffix (`-vN`) as the *only* sanctioned post-commit identity change (via Replace).
- **Structural:** introduce a `DatasetName` newtype (peer of `TenantId`, `CycleDir`) owning `validate_dataset_name` + `default_slug_from_filename` + the collision check, so "operating on the name" is type-distinct from "operating on the data".

The version-and-repoint contract (§2.1) is what *gives the name permission to move* — so §3.2 and Phase 2 land together.

### 3.3 `DraftCampaign` — name the fusion honestly

`DraftCampaign` fuses **dataset-origin prep** (`task_description`, `pipeline_overlay`, `starting_prompt`, column mapping → the four files) with **campaign config** (`connector`, `scoring_composite`, `max_rounds`, `optimizer_*` → `campaign.json`). The fusion is *correct for the one-form ingest UX*, but the name hides it. Phase 3 (low-priority): either rename to `DraftDataset` (the campaign config rides along as a sub-shape) or document the split-at-commit in the class docstring. No behavior change — naming only.

### 3.4 Glossary gaps

`docs/glossary.md` omits **`slug` / dataset-name**, **`origin`** (the homonym it most needs to disambiguate), and conflates **`Dataset`** ("the optimization target plus its config") without naming the four files. Phase 1 adds the three entries (cheap, immediate) so the vocabulary has one home.

---

## 4. Phasing & status

- [x] **Phase 1 — safe choices + glossary + copy** *(shipped)*
  - 409 carries `details.slug`; `slug_collision` error code.
  - Chat collision card: **Use existing** + **Save as new** + Cancel.
  - Glossary: add `slug`/dataset-name, `origin` (disambiguated), tighten `Dataset`.
  - UI copy: "Origin" → "Dataset" across the ingest surface.
- [x] **Phase 2 — Replace = version-and-repoint** *(shipped, data-critical)*
  - `datasets/{slug}/` → `{slug}-vN` (`TenantDatasetStore.version_dataset`); repoint dependent `campaign.json::dataset_name` **+ cycle `index.json` headers** (`CampaignStore.repoint_dataset`) + re-stamp measurements (`MeasurementArchive.restamp_dataset`); the freed name is re-ingested through the normal draft/check-in flow.
  - Resumable migration marker (`datasets/.migrations/{id}.json`); crash-safe ordering; idempotent `recover_pending_replacements` invoked at each replace **and** by the launcher before any run resolves a pin; zero-dependents degrades to a pure archive-old (no repoint). Orchestration: `application/datasets/dataset_replace.py`. Endpoint: typed sync `POST /commands/replace-dataset` (`replaceDataset`).
  - Chat: **Replace** button on the collision card (cautionary outlined style; data-safe — old data archived as a version).
  - Tests: migration round-trip; old-campaign-still-resolves; measurement re-stamp; crash-between-steps recovery.
- [x] **Phase 3 — framing refactor** *(shipped)*
  - Renamed `derived_origin` → `derived_from_dataset` (wire field, openapi, webapp) and `derived_origin_slug()` → `dataset_source_of()`; backend "origin"→"dataset" comment sweep on the dataset-meaning uses (the optimizer-OSP `origin` is untouched). `DatasetName` newtype: **declined** — the minimal path (keep `slug`, document the freeze-at-commit + `-vN` as the only sanctioned post-commit identity change) is sufficient; the freeze now lives in the `DraftCampaign` docstring. `DraftCampaign` not renamed (the fusion is documented in its docstring instead — naming-only, low value).
  - **Doc homonym sweep** (§3.1 row 5) — `m13-chat-first-user-web.md` was the canonical statement of the wrong usage ("Origin is the existing PromptPotter domain word" for the artifact, "Origin files / components / `DraftOrigin`", `tier:"yours"` "Origins", ~14 sites). Reframed in place to **Dataset** for the artifact, with one forward-pointer to this §3.1 (origin reserved for the OSP). Same sweep applied to `m12-api-openapi.yaml` ("the four Origin files" → "Dataset files") and `cli-reference.md` ("builds that origin" → "dataset"). This closes the contradiction with `glossary.md`, which already certified the rename.

### 4.1 Non-goals
- Content-addressed dedup / "this is the same file" detection (operator chose name-only).
- A full revision graph (`@v1/@v2/@latest` UI). Phase 2's `-vN` is a flat, sufficient versioning; a richer model is a later milestone if demand appears.

### 4.2 Invariants (every phase upholds)
- **No in-place mutation of data a campaign has touched.** Ever.
- **Every campaign resolves to the exact bytes it ran on**, before and after any Replace.
- **Slug collision is recoverable in-flow**, never a dead-end error.

### 4.3 End-state note (snapshot-at-mint)
The cleanest long-term fix to the whole class of "name move breaks campaigns" problems is **(B)**: snapshot the example bank into the campaign at mint so campaigns are self-contained and the name is trivially movable (no repoint step). Phase 2's version-and-repoint is correct and ships sooner; (B) is the simplification to revisit once the dataset-identity vocabulary (§3) has settled.
