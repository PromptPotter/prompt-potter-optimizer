#!/usr/bin/env bash
# Installs uvicorn as a systemd service that auto-starts on boot.
# Idempotent: re-running just refreshes the unit and restarts.
set -euo pipefail

# --- config + knobs -----------------------------------------------------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/deploy.config" ]] && source "$HERE/deploy.config"
APP_NAME="${APP_NAME:-myapp}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
APP_MODULE="${APP_MODULE:-myapp.main:app}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME/your-repo}"
RUN_USER="${RUN_USER:-$USER}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
BIND_PORT="${BIND_PORT:-8001}"
HEALTH_PATH="${HEALTH_PATH:-/api/v1/health}"
# ------------------------------------------------------------------------

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

[[ -d "$INSTALL_DIR/.venv" ]] || die "no .venv at $INSTALL_DIR — run bootstrap.sh first"
[[ -f "$INSTALL_DIR/.env" ]]  || die "no .env at $INSTALL_DIR — run bootstrap.sh first"

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
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python -m uvicorn $APP_MODULE \\
    --host $BIND_HOST --port $BIND_PORT --workers 1 --proxy-headers \\
    --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=3s
# Reasonable hardening — uvicorn doesn't need root or extra capabilities.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$INSTALL_DIR

[Install]
WantedBy=multi-user.target
EOF

say "reloading systemd, enabling + starting $SERVICE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME.service"
sudo systemctl restart "$SERVICE_NAME.service"

sleep 2
if curl -fsS "http://$BIND_HOST:$BIND_PORT$HEALTH_PATH" >/dev/null; then
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
