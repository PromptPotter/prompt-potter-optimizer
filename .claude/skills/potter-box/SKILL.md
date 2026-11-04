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

`ssh potter-box` — hostname, user, key and Tailscale address live in `~/.ssh/config`, never in the repo.
Verify with `ssh -o BatchMode=yes potter-box "echo OK; hostname"`.

**Reading the box is free; changing it asks** — anything that pulls, starts, restarts or edits, in either
autonomy mode. `sudo` needs a password: hand privileged commands (`systemctl restart|stop|start`, anything
under `/etc`) to the operator. Unaided reads: `systemctl status|show <unit>`, `journalctl` for your own
units, `ss -tlnp`.

## The app service

`promptpotter.service` runs the FastAPI app and the built webapp. **Its environment is
`/etc/promptpotter.env`** (`ENV_FILE` in `deploy-linux/deploy.config`; root `0600`, so an edit is the operator's
`sudo` — back it up first). The admin bot loads it, then `/etc/promptpotter-admin-bot.env` over it. The
checkout's `<install-dir>/.env` is a divergent copy only the login user's CLI reads: an edit there reaches no
service. Read what a running service holds from `/proc/$(systemctl show <unit> -p MainPID --value)/environ` —
names or a value's hash, never a value. An edit lands only when the operator restarts the service.

**Before calling a deployed behaviour broken, check the code the box runs** (`git log --oneline -1` in the
install dir). **`update.sh` force-matches tracked files to origin**, destroying hand edits — run
`git status --porcelain` there first.

```bash
ssh -t potter-box 'cd ~/potter/prompt-potter-optimizer/deploy-linux && ./update.sh'           # main; a release deploys here before its tag
ssh -t potter-box 'cd ~/potter/prompt-potter-optimizer/deploy-linux && ./update.sh v0.8.17'   # back to a named tag
```

## TermNorm runs in a window on the box's own display — open it first, every time

```bash
ssh potter-box 'bash -s' < .claude/skills/potter-box/open-termnorm.sh                # open: attach, report commit, change nothing
ssh potter-box 'bash -s -- start' < .claude/skills/potter-box/open-termnorm.sh       # pull, then start — after the operator's yes
ssh potter-box 'bash -s -- restart' < .claude/skills/potter-box/open-termnorm.sh     # stop, pull, start — after the operator's yes
```

- **A deploy ends with `restart`**: `update.sh` restarts the app only (`BACKEND_DIR` is unset on this box).
- **`termnorm.service` is disabled on purpose.**
- **Stopping: parent first** — the launcher respawns uvicorn:
  `pkill -f start-server-py-LLMs.sh && sleep 1 && pkill -f "uvicorn main:app"`
- **401 is the gate working**, `/health` included: send `Authorization: Bearer $TERMNORM_TOKEN` from the backend's `.env`.
