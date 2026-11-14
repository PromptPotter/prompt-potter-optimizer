# probe-bkeys: push one Linux offline run's traces (Sonnet-sized, no code change)

This is a measurement, not a chain. Change no tracked file. Make no paid LLM call. Run no gate.

1. `git fetch origin arc/bkeys && git checkout --detach FETCH_HEAD` (it must be `74ccb72ac`).
2. Build the env exactly as `briefs/common.md` on `origin/brief/wave-5` says. Read only its env section.
3. Run `PROMPTPOTTER_HOME=/tmp/p .venv/bin/python scripts/offline_run.py > /tmp/p.log 2>&1` once, and record its exit code. A non-zero exit is expected: it prints `wrote` or `unchanged` for `promptpotter/assets/behaviour_keys.json`.
4. Make a fresh branch from an empty tree: `git checkout --orphan out/bkeys-probe && git rm -rfq .`. Into it, copy:
   - `/tmp/p/{capo,capo-pobb,gepa,levi,potter}/trace.json`, as `<name>/trace.json`;
   - `/tmp/p/gepa/requests/`, as `gepa/requests/`;
   - `/tmp/p.log`;
   - the regenerated `promptpotter/assets/behaviour_keys.json`, as `behaviour_keys.json` (take it from the detached checkout's working tree before you switch branches: copy it to /tmp first);
   - `.venv/bin/python -m pip freeze` (or `uv pip freeze`) output, as `freeze.txt`;
   - `python -V`, `uname -a` and the exit code, as `env.txt`.
5. Commit with the message `probe: bkeys Linux traces` and push: `git push origin out/bkeys-probe`. Then stop.
