#!/usr/bin/env bash
# Open TermNorm in a window on the box's own display, starting it (pulled first) if it is not up.
#   ssh potter-box 'bash -s' < .claude/skills/potter-box/open-termnorm.sh
#   ssh potter-box 'bash -s -- restart' < .claude/skills/potter-box/open-termnorm.sh
# `restart` stops it first — update.sh never touches TermNorm, so a deploy ends with this.
set -u
T="$HOME/potter/TermNorm-excel"
export XDG_RUNTIME_DIR=/run/user/$(id -u) WAYLAND_DISPLAY=wayland-0 DISPLAY=:0
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus

if [ "${1:-}" = restart ]; then
  tmux kill-session -t termnorm 2>/dev/null
  # Parent first: the launcher respawns uvicorn.
  pkill -f start-server-py-LLMs.sh; sleep 1; pkill -f "uvicorn main:app"; sleep 1
fi
if ! tmux has-session -t termnorm 2>/dev/null; then
  if pgrep -f "uvicorn main:app" >/dev/null; then
    echo "TermNorm is running outside tmux — rerun with restart to get it a window"
    exit 1
  fi
  git -C "$T" pull --ff-only </dev/null
  tmux new -d -s termnorm "$T/start-server-py-LLMs.sh"
fi
if [ -z "$(tmux list-clients -t termnorm 2>/dev/null)" ]; then
  setsid ptyxis -- tmux attach -t termnorm >/dev/null 2>&1 </dev/null &
fi
echo "TermNorm at $(git -C "$T" log --oneline -1)"
