# w11-connectors: area 08 to its Target, core's work out of `harbor.py` (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 08 and the debt doc's `## 08` section, which holds w10-survey's row reading `harbor.py` as six
responsibilities. Target: connectors are the only place backend identity is named; a built-in is one
module binding `CONNECTOR`; core never infers what a connector should declare; clients are `ProviderSpec`
instances; provider selection is explicit.

Links, deletion first:
1. **`harbor.py` (1,545 lines, the package's largest module) keeps only what is Harbor's.** Of its six
   responsibilities, each one core already owns moves onto that core seam, and each one that is
   Harbor's stays. Decision 18: the survey's row decides whether "one module here" still holds; name
   that decision under "decided in-run".
2. **A machine's cell ceiling is counted twice** (decided 2026-11-19, never built): build it, within your
   files.
3. **The sweep unlinks a dead producer's lock before `docker_host.py::reap_dead_producers` reads it**, so
   that producer's containers are never reaped. The live box holds 21 producer locks in `jobs/producers/`.
   Re-test it. Fix it if the root is in your files; else file a row under the owning area.
4. **`connectors/termnorm.py` probes `/version`**: `origin/wip/xconn` (13 days old) has a commit for this.
   Take it if it still applies, else build it. The other TermNorm rows wait on TermNorm's `xconn` branch
   reaching the box: leave them.
5. **Census:** `harbor.py`, `connectors/dspy_module.py`, `infrastructure/llm/openai_compat.py`,
   `infrastructure/llm/base.py`, plus area 08's recovered items. Each gets a verdict.

OWNS: `connectors/**`, `infrastructure/backend.py`, `docker_host.py`, `outbound.py` (less
`notify_operator`, which is w11-access's), and `infrastructure/llm/**` less `spend_book.py` and
`wallet.py`.

DONE WHEN: no machine-held connector's arming reads `_SENDING` · `connectors/termnorm.py` probes
`/version` · the report says, per responsibility, what in `harbor.py` was core's work and where it went ·
keys: name each move by its mechanism. LEAN: `promptpotter/` shrinks.
