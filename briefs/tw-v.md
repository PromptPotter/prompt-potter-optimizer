# tw-v: the suite runs faster than the rework found it (cloud, Opus)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is `origin/land/tw-xjp` at
`<BASE-V>` = `a1720cae0`, green on the operator's desk (pytest's job peak 386 of 393 MB). That is
`land/test-world` with every test-rework chain integrated, the last two being tw-r and tw-j2. Where
this brief writes `<BASE-V>`, read `a1720cae0`. Replace the ancestor check with
`git merge-base --is-ancestor 9cd30b5a7 <BASE-V>`. Equals-main runs against `<BASE-V>`'s offline home.
Push to `arc/tw-v`, one commit per link, with the full gate green at the tip. `wip/tw-v`, `arc/tw-v` and
`out/tw-v-report` are fresh on origin. If one exists when you push, stop and report: never push to a
name this brief does not give you.

Read `out/tw-j2-report:report.md` (its link 6 gap) and `out/tw-q-report:report.md` (the production
frames a world run pays, each ranked) first.

## Why
The operator asked that the test rework leave the suite faster and smaller than it found it
(`7dce00a96`). The end RSS is met. The wall is not: in tw-j2's session it was 69.4 s at `7dce00a96`,
105.3 s before tw-j2 and 104.1 s after. Per file, from `7dce00a96` to tw-j2's tip: test_archive 4.1 → 27.4 s,
test_security 29.3 → 36.9, test_spend 9.6 → 13.8, test_connectors 1.3 → 3.3. tw-r's two production
re-resolutions were a wash for the suite (97.9 → 97.2 s). test_archive's five shared runs cost 10.7 s
(`line` 3.24 s carrying 24 facts, `displaced` 2.01 s / 8, `family_tree` 2.68 s / 1, `origin_forks`
2.38 s / 2, `gated` 0.36 s / 2). A merge saves at most ~2.7 s. The other ~16.7 s is about 0.5 s per
fact, spent in `World.adopt` (`tests/world.py`: a copy of the run's whole home, a new workspace, the
dataset's definition copied again, the network laid) and in the doors a fact then opens. That holds
even for a fact that only reads. tw-j2 suspected the per-adopt opening and the landing-key build
(tw-q § Symptoms). The world's cold first run costs 3.27 s at tw-j2's base, on `line`'s setup (~2.3 s
warm). tw-j2 stopped its link 6 with the gap reported. You close it.

## Links
1. **The census, before any change.** For each test file: its wall at `7dce00a96`, `<BASE-V>` and the
   tip. For each shared run: its cost, and the facts that read it with the shape each needs (rows,
   rounds, forks, a wound). For each fact: its setup and call seconds, and whether it writes the home
   it adopted (a digest of the tree before and after). This census goes in the report whole, and it
   picks the order of the links below by seconds saved.
2. **A fact's 0.5 s.** Profile one fact's setup and call under cProfile (adopt, workspace, `_lay`,
   the doors it opens), and rank where the time goes. Then two levers, in either order:
   - **A fact that writes nothing pays no copy.** It reads its run's home in place (`Shared.reading`
     exists). No fact may read what another left, and that is enforced, never trusted: a read that
     writes is refused where it happens, or a run's home is checked unchanged where its fixture ends.
     Choose the cheaper of the two and name it in the commit. A fact that does write keeps its copy.
   - **What every adopt repeats is paid once.** Work an adopt redoes per fact, though it is the same
     for every fact on that run, is done once at the run.
   The cold first run is paid once per session, never by whichever fact comes first.
3. **test_archive's runs, the fewest shapes its facts need** (tw-j2's link 6, as written in
   `briefs/tw-j2.md`; worth ~2.7 s at most). Where one run's shape carries another's facts, those
   facts move onto it and the other run is deleted. `tests/CLAUDE.md` § The world's table and each
   fixture's cost line follow.
4. **test_security, test_spend and test_connectors**: the same census and the same two levers, on what
   tw-c and tw-j moved onto the world.
5. **What a world run itself pays.** Profile one shared run under cProfile and rank frames past
   tw-q's. A production frame that re-derives what its caller already holds is fixed where the value
   is owned, as tw-r did (`out/tw-r-report` is absent: read `land/tw-xjp`'s `febc3b6a1` and
   `9d83a8ae7`). `domain/CLAUDE.md` binds. No cache keyed by object identity.

A speed fix decides nothing. Equals-main must read every optimizer and leg UNMOVED, the digests must be
identical, and `promptpotter/assets/behaviour_keys.json` must be unchanged. No fact is cut to close
the gap. No run is shrunk below what a fact it carries reads. Nothing is installed (no pytest-xdist).

**Accepted when** the suite's wall is below `7dce00a96`'s in your session, and its end RSS is not
above `<BASE-V>`'s. If the five links cannot close it, report the gap with the census and what each
link bought, and stop.

## Owns
Every line of `tests/`, plus `scripts/offline_run.py` and `scripts/offline_world.py`. In production,
only a link 5 frame, with each file named in its commit.

## Proof
- Before → after (`7dce00a96`, `<BASE-V>`, tip), per file you touched and for the suite: lines, tests,
  wall, and the pytest process's RSS at start and end; `scripts/offline_run.py`'s wall and child max
  RSS, all in one session.
- Coverage diff (`sys.monitoring` PY_START, `<BASE-V>` vs tip). Every body the base ran and the tip
  does not is deleted code, restored, or named with its reason.
- Equals-main against `<BASE-V>`: every optimizer and leg UNMOVED, digests identical.
- Full gate green at the tip.

**Report:** common.md's FINAL REPORT on `out/tw-v-report`, with link 1's census whole.
