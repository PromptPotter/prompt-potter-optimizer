#!/usr/bin/env bash
# TermNorm in a window on the box's own display. Three modes, and only the first is free:
#   ssh potter-box 'bash -s' < .claude/skills/potter-box/open-termnorm.sh             # open
#   ssh potter-box 'bash -s -- start' < .claude/skills/potter-box/open-termnorm.sh    # ask first
#   ssh potter-box 'bash -s -- restart' < .claude/skills/potter-box/open-termnorm.sh  # ask first
# open     attaches a window to the running server and reports its commit. Changes nothing.
# start    pulls TermNorm and starts it. For a server a reboot took down.
# restart  stops it first — update.sh never touches TermNorm, so a deploy ends with this.
set -u
T="$HOME/potter/TermNorm-excel"
mode="${1:-open}"
export XDG_RUNTIME_DIR=/run/user/$(id -u) WAYLAND_DISPLAY=wayland-0 DISPLAY=:0
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus

case "$mode" in
  open | start | restart) ;;
  *)
    echo "unknown mode '$mode' — open, start or restart"
    exit 2
    ;;
esac

if [ "$mode" = restart ]; then
  tmux kill-session -t termnorm 2>/dev/null
  # Parent first: the launcher respawns uvicorn.
  pkill -f start-server-py-LLMs.sh; sleep 1; pkill -f "uvicorn main:app"; sleep 1
fi
if ! tmux has-session -t termnorm 2>/dev/null; then
  if pgrep -f "uvicorn main:app" >/dev/null; then
    echo "TermNorm is running outside tmux — restart gives it a window, and restart changes the box"
    exit 1
  fi
  if [ "$mode" = open ]; then
    echo "TermNorm is down at $(git -C "$T" log --oneline -1) — start pulls and starts it, which changes the box"
    exit 1
  fi
  git -C "$T" pull --ff-only </dev/null
  tmux new -d -s termnorm "$T/start-server-py-LLMs.sh"
fi
if [ -z "$(tmux list-clients -t termnorm 2>/dev/null)" ]; then
  setsid ptyxis -- tmux attach -t termnorm >/dev/null 2>&1 </dev/null &
fi
echo "TermNorm at $(git -C "$T" log --oneline -1)"
