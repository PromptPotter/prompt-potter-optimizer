# cite-fold: a citation's panel by word, served; a panel member folds by the layer rule (cloud)

`briefs/common.md` binds you. Base: `origin/main` (3784ed33b or later). The rows are
`docs/specs/code-debt-cleanup.md` § wave-5 · l4, the first three: delete each as you close it.

## Links
1. **A citation's panel is matched by substring, not by word**:
   `potter/validators.py::l1_evidence_grounding` tests `all(w in _words(rendered[n]) for w in parts)`,
   where each part is a joined string, so `in` is a substring test against the panel text: "a"
   matches inside any word, and a short quote is held by whichever panel sorts first in `citable`.
   Action: a part holds where its word sequence is a contiguous run of the panel's word sequence
   (whole tokens, same order). Grep the other `_words` (`dispatch/injections/panels.py:592`) first:
   one word splitter for both, if they mean the same. **Re-test:** a one-word citation that only one
   panel holds as a word names that panel; a part that is a word's fragment cites nothing.
2. **The derived citation panel is not served**: `webapp/components/node-surface/L1Variants.tsx`
   reads `evidence_grounding.citation` only; the panel the validator derived
   (`EvidenceGrounding.field`) never reaches the browser. Action: the variant row serves `field`
   on the channel it already serves `citation` on, and the card names the panel beside the quote.
   Regenerate the served types/specs the gate checks. **Re-test:** a variant's card shows the panel
   its citation was held by.
3. **A panel member restates the family's whole `inner_campaign`**:
   `infrastructure/store/dataset_access.py` (~:233) merges each layer's `panel:` section with `|=`,
   one level deep, so a member (`e2e/dataset.yaml`) repeats every `inner_campaign` key to change
   one. Action: the panel section folds by the rule every layered document uses
   (`domain/layer_fold.py::fold_layers`), never a second merge. Then trim each member's `panel:` to
   the keys it changes (`git grep -l "^panel:" -- promptpotter/assets research`). **Re-test:**
   `e2e/dataset.yaml` names only the `inner_campaign` keys it changes, and every member's resolved
   panel is unchanged (print each one's resolved `panel` before and after; byte-equal JSON).

## Owns
`application/optimizers/potter/validators.py`, `potter/dispatch/injections/panels.py` (the word
splitter only), the served variant row's model and its projection, `L1Variants.tsx` and its CSS,
`infrastructure/store/dataset_access.py`, the dataset YAMLs' `panel:` sections, their tests,
regenerated specs/types. NOT `application/runner/**` (incl. `runner/output.py`),
`presentation/cli/**`, `application/bench/**`, `domain/l4/**`, `application/jobs/**` (other chains
run there). Link 1 may move potter's treatment digest: name old→new in the commit body.

## Target
Net ≤ 0 `promptpotter/` lines on links 1 and 3; link 2 grows by the served field and its render.

## Report
Per `common.md`, plus each member's resolved-panel equality check (link 3).
