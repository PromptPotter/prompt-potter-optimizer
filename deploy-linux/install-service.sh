#!/usr/bin/env bash
# Installs uvicorn as a systemd service that auto-starts on boot.
# Idempotent: re-running just refreshes the unit and restarts.
set -euo pipefail

# --- knobs --------------------------------------------------------------
INSTALL_DIR="${INSTALL_DIR:-$HOME/potter/prompt-potter-optimizer}"
RUN_USER="${RUN_USER:-$USER}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
BIND_PORT="${BIND_PORT:-8001}"
# ------------------------------------------------------------------------

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

[[ -d "$INSTALL_DIR/.venv" ]] || die "no .venv at $INSTALL_DIR — run bootstrap.sh first"
[[ -f "$INSTALL_DIR/.env" ]]  || die "no .env at $INSTALL_DIR — run bootstrap.sh first"

UNIT_FILE=/etc/systemd/system/promptpotter.service
say "writing $UNIT_FILE (user=$RUN_USER, bind=$BIND_HOST:$BIND_PORT)"

sudo tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=PromptPotter Optimizer (FastAPI + static webapp)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python -m uvicorn promptpotter.main:app \\
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

say "reloading systemd, enabling + starting promptpotter.service"
sudo systemctl daemon-reload
sudo systemctl enable promptpotter.service
sudo systemctl restart promptpotter.service

sleep 2
if curl -fsS "http://$BIND_HOST:$BIND_PORT/api/v1/health" >/dev/null; then
    say "service is up: http://$BIND_HOST:$BIND_PORT/api/v1/health"
else
    die "service started but health check failed — see: journalctl -u promptpotter -e"
fi

printf '\n\033[1;32muvicorn service installed.\033[0m\n\n'
cat <<'EOF'
useful commands:
  systemctl status promptpotter        # is it running?
  journalctl -u promptpotter -f        # live logs
  sudo systemctl restart promptpotter  # after pulling new code

next step:
  ./install-tunnel.sh
EOF
