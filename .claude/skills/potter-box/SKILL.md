---
name: potter-box
description: Operating the self-hosted Linux box that serves the PromptPotter API/webapp and the TermNorm backend. Use this whenever the user mentions the linux box, the fedora box, the server, "start termnorm", "termnorm isn't reachable", restarting the app, editing the box's .env, or checking what is actually running in production — and also when a change you just made locally needs to reach the deployed instance, even if the user does not name the server. Holds only what cannot be answered in one command: preferences, and lessons that cost real time. Everything else, go read the box.
compatibility: OpenSSH client with a `potter-box` Host alias configured (see Setup); tmux on the box; a Wayland desktop session for the GUI-terminal recipe.
---

# potter-box

```bash
ssh potter-box
```

**The alias is the point.** Hostname, user, key path and the Tailscale address live in
`~/.ssh/config` — never in this file, never in the repo. That keeps this skill shareable: a reader
gets the operating knowledge without inheriting your attack surface, and points the same alias at
their own machine.

## Setup (once per workstation)

Add to `~/.ssh/config`, filling in your own values. Nothing else in this skill needs editing.

```
Host potter-box
    HostName <lan-ip-or-dns>
    User <you>
    IdentityFile ~/.ssh/<your-key>
    IdentitiesOnly yes
```

Add a second block (`potter-box-ts` or similar) for the Tailscale address if the box is reachable
off-LAN. Verify with `ssh -o BatchMode=yes potter-box "echo OK; hostname"` — `BatchMode` makes a
missing key fail immediately instead of hanging on a password prompt, which matters because an
agent cannot answer one.

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

## "start termnorm" means a terminal on the box's own display

Not a background process — the operator wants to watch it. `tmux` holds the server so closing the
window cannot kill it; `ptyxis` is the only emulator installed, and it draws on the Wayland
session.

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u) WAYLAND_DISPLAY=wayland-0 DISPLAY=:0
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
tmux kill-session -t termnorm 2>/dev/null
tmux new -d -s termnorm "$HOME/potter/TermNorm-excel/start-server-py-LLMs.sh"
setsid ptyxis -- tmux attach -t termnorm >/dev/null 2>&1 </dev/null &
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
