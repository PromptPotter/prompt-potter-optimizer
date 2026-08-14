#!/usr/bin/env bash
# Installs uvicorn as a systemd service that auto-starts on boot.
# Idempotent: re-running just refreshes the unit and restarts.
set -euo pipefail

# --- config + knobs -----------------------------------------------------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/deploy.config" ]] && source "$HERE/deploy.config"
source "$HERE/health.sh"
APP_NAME="${APP_NAME:-myapp}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
APP_MODULE="${APP_MODULE:-myapp.main:app}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME/your-repo}"
RUN_USER="${RUN_USER:-$USER}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
BIND_PORT="${BIND_PORT:-8001}"
HEALTH_PATH="${HEALTH_PATH:-/api/v1/health}"
# Optional cgroup memory ceiling (e.g. "2G"). Empty = no limit. A bound turns the
# pp-self memory-starvation failure into a clean cgroup OOM instead of a silent OS kill.
MEMORY_MAX="${MEMORY_MAX:-}"
# Where systemd reads the environment from — the same knob bootstrap.sh seeded. Not necessarily
# $INSTALL_DIR: systemd loads EnvironmentFile as root, and SELinux denies root a file under $HOME.
# deploy.config.example carries the full symptom.
ENV_FILE="${ENV_FILE:-$INSTALL_DIR/.env}"
# ------------------------------------------------------------------------

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

[[ -d "$INSTALL_DIR/.venv" ]] || die "no .venv at $INSTALL_DIR — run bootstrap.sh first"
[[ -f "$ENV_FILE" ]] || die "no env file at $ENV_FILE — run bootstrap.sh first, or set ENV_FILE in deploy.config"

# --- SELinux: let systemd exec the venv (Fedora/RHEL enforcing) ----------
# A venv under $HOME is labeled user_home_t; a confined service domain can't
# execute it, so systemd fails EXEC with 203/"Permission denied" (silent
# crash-loop). Persist a bin_t label on .venv/bin and apply it. update.sh
# re-applies it after each dep rebuild. No-op where SELinux is off/absent.
if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
    say "SELinux enforcing — labeling $INSTALL_DIR/.venv/bin bin_t"
    command -v semanage >/dev/null 2>&1 || sudo dnf install -y policycoreutils-python-utils
    sudo semanage fcontext -a -t bin_t "$INSTALL_DIR/.venv/bin(/.*)?" 2>/dev/null \
        || sudo semanage fcontext -m -t bin_t "$INSTALL_DIR/.venv/bin(/.*)?"
    sudo restorecon -RF "$INSTALL_DIR/.venv/bin"
fi

UNIT_FILE=/etc/systemd/system/$SERVICE_NAME.service
say "writing $UNIT_FILE (user=$RUN_USER, bind=$BIND_HOST:$BIND_PORT)"

sudo tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=$APP_NAME (FastAPI + static webapp)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$INSTALL_DIR/.venv/bin/python -m uvicorn $APP_MODULE \\
    --host $BIND_HOST --port $BIND_PORT --workers 1 --proxy-headers \\
    --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=3s
# --- hardening (kernel-enforced blast-radius floor) ---------------------
# uvicorn needs no privileges: drop every capability, deny privilege gain,
# and make the whole filesystem read-only except the install dir it writes.
NoNewPrivileges=true
CapabilityBoundingSet=
AmbientCapabilities=
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$INSTALL_DIR
UMask=0077
# kernel + process surface the app never touches
RestrictSUIDSGID=true
RestrictRealtime=true
RestrictNamespaces=true
LockPersonality=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged ~@resources
${MEMORY_MAX:+MemoryMax=$MEMORY_MAX}
# If the service fails to start after tightening, the usual first suspects are
# ProtectSystem=strict (add the offending write path to ReadWritePaths) or the
# SystemCallFilter (check: journalctl -u $SERVICE_NAME -e | grep -i 'signal\|syscall').

[Install]
WantedBy=multi-user.target
EOF

say "reloading systemd, enabling + starting $SERVICE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME.service"
sudo systemctl restart "$SERVICE_NAME.service"

# A drop-in outranks the unit written above, so re-running this script cannot undo one — and the
# box that first hit the SELinux denial got exactly that, by hand. Ask systemd which file is
# actually in force rather than reporting the one we asked for: a service reading an env file
# nobody edits is invisible until a key is missing, and then it looks like the app's bug.
IN_FORCE="$(systemctl show "$SERVICE_NAME" -p EnvironmentFiles --value | sed 's/ (ignore_errors=.*//')"
[[ "$IN_FORCE" == "$ENV_FILE" ]] || warn "systemd loads '$IN_FORCE', NOT '$ENV_FILE' — a drop-in under /etc/systemd/system/$SERVICE_NAME.service.d/ is overriding this unit. Put the path in ENV_FILE (deploy.config) and delete the drop-in, or every key bootstrap/update writes goes to a file the service never reads."

if wait_healthy "http://$BIND_HOST:$BIND_PORT$HEALTH_PATH"; then
    say "service is up: http://$BIND_HOST:$BIND_PORT$HEALTH_PATH"
else
    die "service started but health check failed — see: journalctl -u $SERVICE_NAME -e"
fi

printf '\n\033[1;32muvicorn service installed.\033[0m\n\n'
cat <<EOF
useful commands:
  systemctl status $SERVICE_NAME        # is it running?
  journalctl -u $SERVICE_NAME -f        # live logs
  sudo systemctl restart $SERVICE_NAME  # after pulling new code

next step:
  ./install-tunnel.sh
EOF
