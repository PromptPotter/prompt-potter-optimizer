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

Hostname, user, key and the Tailscale address live in `~/.ssh/config` (`Host potter-box`, plus a
Tailscale block if off-LAN), never in the repo. Verify with
`ssh -o BatchMode=yes potter-box "echo OK; hostname"` — `BatchMode` fails a missing key at once
instead of hanging on a password prompt an agent cannot answer.

## `sudo` needs a password

Hand privileged commands to the operator rather than retrying them — an agent has no way to
supply it, and a retry just burns a round trip. That covers `systemctl restart|stop|start`,
service installs, and anything under `/etc`. Reading is fine: `systemctl status`, `journalctl` for
your own units, and every file the login user owns.

## The app service

`promptpotter.service` runs the FastAPI app and serves the built webapp. Its environment comes
from `EnvironmentFile=<install-dir>/.env`, which the login user owns at `0600`.

So **editing `.env` changes nothing until the service restarts** — and the restart is the
operator's, per above. Back the file up before appending; it holds every API key on the box and
there is no other copy.

**Before concluding a deployed behaviour is broken, check the box is running the code you think
it is.** `git log --oneline -1` in the install dir is one command and answers it. Local work that
is merely committed is not deployed, and work that is only in your working tree is not even
that — a config key you add for a feature that has not shipped will sit inert and look like a bug
in the feature.

⚠️ **The deploy script force-matches tracked files to origin.** Anything hand-edited on the box is
destroyed by the next update, silently. `git status --porcelain` in the install dir before
updating tells you what you are about to lose — and it is routinely non-empty, so treat a clean
tree as the surprise rather than the default.

## Reading the box is free; changing it asks

Opening a window, reading a file, a status or a log changes nothing, so do it without asking.
Anything that pulls, starts, restarts or edits changes what the box serves: ask the operator
first, in either autonomy mode (root `CLAUDE.md` § Working principles).

## TermNorm runs in a window on the box's own display — open it first, every time

Not a background process — the operator wants to watch it, so **every use of this skill starts
here**, whatever the ask. `tmux` holds the server so closing the window cannot kill it; `ptyxis` is
the only emulator installed, and it draws on the Wayland session.

```bash
ssh potter-box 'bash -s' < .claude/skills/potter-box/open-termnorm.sh
```

That is the script's `open` mode: it attaches the window to the running server, reports its commit
and changes nothing. Where a reboot took TermNorm down it says so and exits non-zero. The other
two modes change the box, so each follows the operator's yes:

```bash
ssh potter-box 'bash -s -- start' < .claude/skills/potter-box/open-termnorm.sh     # pull, then start
ssh potter-box 'bash -s -- restart' < .claude/skills/potter-box/open-termnorm.sh   # stop, pull, start
```

**A deploy ends with `restart`.** `deploy-linux/update.sh` syncs and restarts the app only;
`BACKEND_DIR` is unset on this box, so TermNorm keeps its old code until this runs.

```bash
ssh -t potter-box 'cd ~/potter/prompt-potter-optimizer/deploy-linux && ./update.sh'           # sudo prompts; a release deploys main here before its tag (/potter-release)
ssh -t potter-box 'cd ~/potter/prompt-potter-optimizer/deploy-linux && ./update.sh v0.8.17'   # back to a named tag
```

Three things that cost time to learn:

- **`termnorm.service` is disabled on purpose.** Don't "fix" it.
- **Stopping: parent first** — the launcher respawns uvicorn.
  `pkill -f start-server-py-LLMs.sh && sleep 1 && pkill -f "uvicorn main:app"`
- **401 is the gate working, not a fault** — `/health` included. To really check, source the
  backend's `.env` on the box and send `Authorization: Bearer $TERMNORM_TOKEN`.

## Reading a service you did not start

`journalctl -u <unit>` needs privileges for system units, so it is an operator command. What you
can do unaided: `systemctl status <unit>` for the run state, `systemctl show <unit> -p
EnvironmentFile,ExecStart,ActiveState` to see how it is wired, and `ss -tlnp` to check what is
actually listening — useful for confirming an outbound-only component really opened no port.
