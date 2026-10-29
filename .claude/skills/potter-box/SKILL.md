---
name: potter-box
description: >-
  Operating the self-hosted Linux box that serves the PromptPotter API/webapp and the TermNorm
  backend. Use on the linux / fedora box, the server, "start termnorm", "termnorm isn't reachable",
  restarting the app, the box's .env, what runs in production, or a local change that must reach
  the deployed instance. Reading the box is free; pulling, starting, restarting or editing asks.
compatibility: OpenSSH client with a `potter-box` Host alias in `~/.ssh/config`; tmux on the box; a Wayland desktop session for the GUI-terminal recipe.
---

# potter-box

```bash
ssh potter-box
```

Hostname, user, key and Tailscale address live in `~/.ssh/config` (`Host potter-box`), never in the
repo. Verify with `ssh -o BatchMode=yes potter-box "echo OK; hostname"` — `BatchMode` fails a missing
key at once instead of hanging on a password prompt.

## Reading the box is free; changing it asks

Windows, files, statuses and logs change nothing — do them unasked. Anything that pulls, starts,
restarts or edits asks the operator first, in either autonomy mode (root `CLAUDE.md` § Working
principles).

## `sudo` needs a password

Hand privileged commands (`systemctl restart|stop|start`, service installs, anything under `/etc`) to
the operator rather than retrying. Reading is fine: `systemctl status`, `journalctl` for your own
units, every file the login user owns.

## The app service

`promptpotter.service` runs the FastAPI app and serves the built webapp, environment from
`EnvironmentFile=<install-dir>/.env` (login user, `0600`). **Editing `.env` changes nothing until the
operator restarts the service.** Back it up before appending — it holds every API key, with no other
copy.

**Before calling a deployed behaviour broken, check the code the box runs**: `git log --oneline -1` in
the install dir. Committed locally is not deployed; a config key for an unshipped feature sits inert
and looks like a bug.

⚠️ **The deploy script force-matches tracked files to origin**, silently destroying hand edits on the
box. Run `git status --porcelain` in the install dir before updating — it is routinely non-empty.

## TermNorm runs in a window on the box's own display — open it first, every time

The operator wants to watch it, so **every use of this skill starts here**. `tmux` holds the server so
closing the window cannot kill it; `ptyxis` (the only emulator) draws on the Wayland session.

```bash
ssh potter-box 'bash -s' < .claude/skills/potter-box/open-termnorm.sh
```

`open` mode attaches the window to the running server, reports its commit and changes nothing; where a
reboot took TermNorm down it says so and exits non-zero. The other two modes change the box, so each
follows the operator's yes:

```bash
ssh potter-box 'bash -s -- start' < .claude/skills/potter-box/open-termnorm.sh     # pull, then start
ssh potter-box 'bash -s -- restart' < .claude/skills/potter-box/open-termnorm.sh   # stop, pull, start
```

**A deploy ends with `restart`**: `deploy-linux/update.sh` syncs and restarts the app only
(`BACKEND_DIR` is unset on this box), so TermNorm keeps old code until then.

```bash
ssh -t potter-box 'cd ~/potter/prompt-potter-optimizer/deploy-linux && ./update.sh'           # sudo prompts; a release deploys main here before its tag (/potter-release)
ssh -t potter-box 'cd ~/potter/prompt-potter-optimizer/deploy-linux && ./update.sh v0.8.17'   # back to a named tag
```

- **`termnorm.service` is disabled on purpose.** Don't "fix" it.
- **Stopping: parent first** — the launcher respawns uvicorn:
  `pkill -f start-server-py-LLMs.sh && sleep 1 && pkill -f "uvicorn main:app"`
- **401 is the gate working**, `/health` included. To check, source the backend's `.env` on the box and
  send `Authorization: Bearer $TERMNORM_TOKEN`.

## Reading a service you did not start

`journalctl -u <unit>` on system units is an operator command. Unaided: `systemctl status <unit>`,
`systemctl show <unit> -p EnvironmentFile,ExecStart,ActiveState`, and `ss -tlnp` for what is actually
listening (e.g. confirming an outbound-only component opened no port).
