# Whitelabel — running this unit under another name

> ## ⚠ DRAFT — parked until someone asks for it
>
> Written 2026-08-05 against the live single-tenant install. **No second unit has
> ever been built from this page**, so read it as the intended shape, not as a
> walked path.
>
> **Verified:** the fan-out functions in `brand-env.sh` do what they claim, and
> `BRAND_SHORT_NAME` / `BRAND_SERVICE_NAME` repaint the CLI description, the
> FastAPI title and `/health`'s `service`.
>
> **Not verified — and this is the work when it resumes:** no deploy has run
> through `brand_export_webapp`, so the webapp half is wired but unproven against
> a real override set; tier 0's mirror recipe is written from GitHub's documented
> behavior, not from doing it; tier 3 has never been attempted at all. Expect the
> first real adopter to find the gap, and expand from what they hit.

A distributor sells this to its own customers under its own name. That is four
separate renames, not one, and they get progressively more expensive: what the
screen says, what the box is called, what the code is called, and what your copy
of the source is called. **Most forks only ever do the first two.**

One rule sets the shape of everything below: **the brand is data, the identity is
code.** Anything a customer reads — names, URLs, legal links — comes from
declarations you set outside the source tree, and upstream never has to know. The
package name, the CLI verb and the on-disk state tree are identity: renaming them
is a code change that costs you every merge from upstream afterward, and the state
rename orphans your campaigns on top of that.

## The steps

| | Tier | You change | Costs you |
|---|---|---|---|
| 0 | **Your copy** | the repo name, and fork-vs-mirror | nothing, if you decide it first |
| 1 | **Brand surface** | `deploy.config`'s brand block → rebuild | nothing — it is data |
| 2 | **Deployment identity** | `deploy.config`'s hostname/unit block | one re-run of the install scripts |
| 3 | **Code identity** | the package, the verb, the state tree | merge conflicts forever, and orphaned campaigns |

Do them in order, and verify sign-in end to end between 2 and 3 — tier 2 is what
breaks the OIDC round trip, and tier 3 makes that breakage much harder to
attribute.

## Tier 0 — your copy of the source

**Name the repository first.** It is the cheapest moment: before any deploy
config exists, before a systemd unit is named after it, before a checkout path
appears in a runbook. A GitHub fork's name is independent of upstream's, so
renaming costs nothing at this point and gets awkward later.

**Fork or mirror — pick on visibility, not on convenience.** Both keep you able
to pull upstream; they differ in what GitHub lets you do afterward.

- **Fork** when your copy is public and you intend to send changes back. You get
  the upstream link, PRs upstream, and `gh repo sync`. What you do *not* get: a
  fork of a public repo cannot later be made private, and it stays visible in
  upstream's fork network — which is usually wrong for a commercial whitelabel.
- **Mirror** when your copy is private or the branding is the product. Duplicate
  rather than fork, then wire upstream back by hand as a second remote:

  ```bash
  git clone --bare https://github.com/PromptPotter/prompt-potter-optimizer.git
  cd prompt-potter-optimizer.git
  git push --mirror https://github.com/acme/acme-prompt-lab.git
  cd .. && rm -rf prompt-potter-optimizer.git

  git clone https://github.com/acme/acme-prompt-lab.git && cd acme-prompt-lab
  git remote add upstream https://github.com/PromptPotter/prompt-potter-optimizer.git
  git fetch upstream && git merge upstream/main     # every update, from here on
  ```

  You lose the fork-network link and the one-click sync; you keep every update,
  because `upstream` is an ordinary remote.

Either way, **do not rename the Python package here** — that is tier 3, and doing
it in the same breath as the repo rename is what makes every later merge painful.

## Tier 1 — the brand surface

`deploy-linux/deploy.config`'s `--- brand ---` block is the ONE declaration.
`brand-env.sh` fans it out both ways: the engine's three keys into `.env`, and
the `NEXT_PUBLIC_*` twins exported for the webapp build. `bootstrap.sh` and
`update.sh` both call it, so editing the block and re-deploying repaints the
install — and an update never silently repaints it back upstream.

An unset value is never written, so a half-filled block leaves upstream defaults
standing rather than blanking the surfaces it didn't name.

Three rules the fields encode, which is why they are not one flat string:

- **`PUBLISHER_*` is yours, the provider is not.** The publisher is whoever
  distributes the unit — repaint it. The provider names who *powers* it: a
  provenance fact, the one field with no override (`webapp/lib/brand.ts`).
- **`MARKETING_URL=""` drops the login showcase whole**
  (`components/login/BrandShowcase.tsx`), so a reseller never funnels its paying
  users upstream.
- **`TERMS_URL` / `PRIVACY_URL` / `IMPRINT_URL` are separate overrides**, not
  derived from the marketing URL. A distributor answers for its own terms, and
  clearing the marketing URL must not take the consent links down with it.

### The mark is a file swap, not a config key

The mark has no env override, because a glyph is not a string. A distributor
replaces the artwork instead — three files, and nothing else refers to them:

| File | Where it shows | Constraint on a replacement |
|---|---|---|
| `webapp/public/brand/mark-pot.png` | login eyebrow, running-jobs button, About-this-unit | the **alpha channel is the mark** — it is used as a CSS mask painted with `currentColor`, so colour in the file is ignored. Ship a transparent cut, not a coloured one. |
| `webapp/public/brand/tab-icon-pot-32.png` + `-dark.png` | browser tab | two cuts, ink and light, chosen by `media=` on the `<link>`. Raster cannot hold a `prefers-color-scheme` rule internally, so both are required. |
| `webapp/public/brand/app-icon-pot-512.png` | Web App Manifest / installed icon | must be **opaque** — a launcher composites it onto a surface we do not control |

`webapp/components/brand/PotterMark.tsx` is the only renderer; it masks the PNG
rather than embedding an `<img>`, which is what keeps the glyph tinting with
theme and hover instead of hardcoding an ink colour.

Do **not** wrap a replacement mark in a circle to make it survive both themes —
that is what the tab pair already solves, and only for the tab.
→ [`BRAND.md`](../../BRAND.md)

The engine's copies live on `Settings` (`config/settings.py`:
`BRAND_SHORT_NAME`, `BRAND_SERVICE_NAME`, `BRAND_DOCS_URL`); the webapp's are
enumerated by `webapp/lib/brand.ts`. Next inlines its half at build time, so
**the rebuild IS the rename** — there is no runtime brand config to drift.

## Tier 2 — deployment identity

The rest of `deploy.config`: the systemd unit, the cloudflared tunnel, the
install dir, the public hostname. The four `deploy-linux/*.sh` scripts read it
and nothing else.

**Two files the scripts do not write, and sign-in stays broken until both move:**
`.env` `ALLOWED_ORIGINS`, and `.promptpotter/identity/oidc.json` `redirect_uri` —
plus the matching redirect URI in the OAuth provider's console. The failure is
silent from the app's side: the provider rejects the callback, so nothing on the
box logs a cause.

## Tier 3 — code identity

The `promptpotter` package, the `python -m promptpotter` verb, the
`$PROMPTPOTTER_*` variables, and the on-disk state tree. The tree is named in one
place — `config/paths.py`, where `_PROJECT_NAME`, `_ENV_HOME`, `user_data_root`
and `_os_app_data_dir` sit together — so this tier is cheap to *write* and
expensive to *live with*: it conflicts with every upstream merge, and renaming
the state dir orphans every campaign under the old name. **Move the tree; never
teach the resolver to read both.** Count what would move before you start:
`ls .promptpotter/projects/*/campaigns`.

## Never rename

- **The provider.** Above — it is provenance, not a label.
- **`prompt_variants.json`'s `"source"`.** Where a prompt block came from. A
  citation, not a badge.
- **Dataset names and `campaign_id`.** `sample_id` is the measurement cache key
  (`(dataset_name, node_configs, sample_id)`), so renaming voids the archive
  without saying so.
- **`name:` in `assets/optimizer/pipeline.yaml`.** It identifies the optimizer
  pipeline, not the seller. The optimizer prompt sets beside it name the product
  too, and that is equally deliberate: they describe the system being optimized,
  not the party selling it.
