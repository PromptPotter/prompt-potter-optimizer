ARC w2v: the data root is private no matter who launches the process. Branch `arc/w2v` off `origin/batch/5`. Not a census row: the operator asked for it after a box read (2026-11-07).

What happened: the box's data root `/var/lib/promptpotter` was 0755, and 12 files in it were 0644 (`identity/oidc.json`, `identity/default_claimed.json`, 8 `projects/*/user.json`, `site_records/app_visit_digests.jsonl`). The service unit runs with `UMask=0077` (`deploy-linux/hardening.sh::unit_hardening`), but:
- `deploy-linux/update.sh::as_unit` runs `land-data`, `close-admission` and `health` through `systemd-run` WITHOUT the unit's hardening block, so those verbs write under umask 0022. The 8 `user.json` share one second (2026-10-31 12:10:11), a `land-data` run. `as_unit`'s own comment says a verb runs "under the unit's environment": it is a second spelling of that environment, drifted weaker.
- A verb the operator runs by hand from a shell, or any non-deployed install, has the shell's umask too. The data root itself is created with whatever mode its creator had (`install-service.sh:40`'s documented `sudo mkdir -p`).

The root, two edits:
1. The code holds the data root at 0700 on POSIX: at the one place every process (CLI, server, admin bot, `embedded_run`) passes before its first write under the root, create it 0700 and tighten an existing wider root in place. If tightening fails (not the owner), refuse with a typed error that names the path and the `chmod 700` to run. Windows is a no-op (the per-user profile already scopes `LOCALAPPDATA`). With the root at 0700, the mode of anything beneath it, including third-party writes (the Hugging Face cache) and copied survivors, reaches no other user. Grep where the root is first created (`config/paths.py::user_data_root` resolves it without side effects; keep it that way, and put the guard where the root is made, or at the shared startup).
2. `as_unit` passes the unit's hardening block (`unit_hardening`'s non-comment lines as `systemd-run -p` properties), so a verb runs under exactly the unit's rules. One source; no `UMask` line spelled a second time.

A test: under a forced 022 umask, a fresh root is created 0700 and an existing 0755 root is tightened; the refusal path is typed. The test fails if the guard is removed. Skip on Windows.

Docs: the deploy README's security list (`deploy-linux/README.md:351` area) gains one line: the app holds the data root 0700; `as_unit` verbs run under the unit's hardening.

Owns: the module holding the guard, its test, `deploy-linux/update.sh` (`as_unit` only), `deploy-linux/README.md` (one line), and `install-service.sh`'s creation comment if it names a mode. If the guard's module is in digest reach (`source_scan.py::optimizer_prompt_shapers`), put the guard in a module outside it.

Named served deltas: none.

Checks: the full gate; `--digests` byte-equal to main; `--against /tmp/base`: decisions and requests unmoved for every optimizer (all five; name any that did not run). `bash -n deploy-linux/update.sh`; print the `systemd-run` argv `as_unit` now builds (a dry echo; no systemd here).

Commit: `fix(store): the data root is 0700 whoever launches; verbs run hardened`, or close to it.
