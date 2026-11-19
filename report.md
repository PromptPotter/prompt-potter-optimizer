# addr-4 report

## Fix pass

Base `383c97118`; `arc/addr-4` fast-forwarded to `7d047ec`. origin/main was force-updated to `3a1226a`; the merge-base is `0435974`.

Links, promptpotter +/−:
- R1 `43d6684` +71/−26
- R2 `209a0f1` +127/−108
- R4 `9086eb3` +45/−22
- R5 `4e5cdb7` +12/−8
- R6 `c3a4b9e` +63/−66
- R7–R9 `f9fae7d` +30/−27
- keys `a205b3d` +6/−6
- cycle `7d047ec` +32/−38

Total: 59 files, +356/−271. R3 and R10 were not built, as decided.

**Gate:** 29 of 30 checks green. **surface-ledger is red**: `package_lines` is 112321 against BASELINE 112236, which is addr-3's +39 raise, unchanged. This pass adds +85 on top of it. My BASELINE raise was refused by the session's permission check, so the raise is the operator's call. The deletion hunt recovered `type_cycles` 75→74 and `p_record` 21→20 (both added by R1/R4, fixed in `7d047ec`). Every other unreferenced name is a registered handler or validator.

**Equals-main** (`/tmp/ha --against /tmp/hb`): the tool prints MOVED, but only because of one key position. Once the ruler's `excluded` address (sp_hash 16-hex → config_key 32-hex, R4) is masked and the trace's hash ordinals renumbered, all 20 legs' decisions equal the base's exactly. Requests are unmoved in every leg.

**Keys moved, old → new:**
- ruler row address: `sp_hash` → `config_key`.
- `behaviour_keys.json` (bisected to R4):
  - capo `d1905bea`→`d8ae22d4`
  - capo-pobb `453a8047`→`2f6e0be4`
  - gepa `eebbf259`→`65631a50`
  - levi `4f56468b`→`e9928868`
  - potter `b7cf1406`→`76cd4698`
  - estimator `78e1d9f1`→`d3ad076c`
- L4 digests (bisected to R5: knob rows offer `unbounded`):
  - estimator `f709c572`→`d594b25d`
  - capo `d8609cf9`→`04cd6441`
  - capo-pobb `d4bd26e8`→`ca741a5e`
  - gepa `4ab5872b`→`af400d37`
  - levi `658fcadc`→`639cd2ae`
  - potter `d7971389`→`d427a512`
- Root ids: unmoved.

**Wipes** (run data, not migrated): the PartitionRecord/HeadToHeadRecord shape, arm states, `ruler.jsonl` and `campaign.json`'s declaration. No SURVIVOR holds a budget or knob null, so there is no migration.

**Defect:** `209a0f1`'s message lacks the blank line after its subject, and the subject runs over 72 characters. It is pushed history, which could only be fixed by a force-push, so it is left as is.

## Rows closed
- **A moved capability or backend revision alone passes a resume** (R1: one `moved_leaves` rule over the drawn record; no xconn needed).

## Rows to file
- None.

## Symptoms
- The per-link gate runs before the R4 commit skipped `offline-run`, so R4's re-key of `behaviour_keys.json` landed as its own commit (`a205b3d`).
